"""
animate_groups.py — 12 个性别×年龄段亚组疏散过程动图生成器

为每个性别×年龄段亚组生成一个 GIF 动图, 展示该亚组居民在 0–45 分钟
疏散过程中沿其分配路径的移动轨迹。背景叠加四阶段 (15/25/35/45 min)
时变 CVaR 风险场, 在对应时间节点自动切换。

依赖:
    matplotlib.animation.PillowWriter (内置, 无需 imagemagick)
    pyproj, numpy, shapely (项目已有)
    contextily (可选, 提供底图)

使用方式:
─────────────────────────────────────────────────────────────────
方式 1: 在 main.py 中调用 (推荐, 共享 result_dict, 无需重新优化)
─────────────────────────────────────────────────────────────────
    from resi_anime import animate_group, animate_all_groups

    # 单组
    animate_group(result_dict, risk_arrays, output_dir, fps=4)

    # 批量 (在 main 末尾)
    animate_all_groups(results, risk_arrays, OUTPUT_ROOT)

─────────────────────────────────────────────────────────────────
方式 2: 命令行独立调用 (从 pickle 文件加载已优化结果)
─────────────────────────────────────────────────────────────────
    python resi_anime.py --pickle results.pkl --output figure/animations

─────────────────────────────────────────────────────────────────
方式 3: 一键运行 (重新优化 + 生成动图, 适合首次跑)
─────────────────────────────────────────────────────────────────
    python resi_anime.py --run-and-animate --test --serial
"""
import os
import sys
import pickle
import argparse
import warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")   # 无 GUI 后端, 适合服务器/批量
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.animation as animation
from matplotlib.patches import Patch
import pyproj

from config import (
    CENTER_UTM, CRS_UTM, CRS_WGS84, GRID_RES,
    STAGE_TIMES, VIZ_CONFIG, RISK_VALUE_FILES, OUTPUT_ROOT,
)

try:
    import contextily as ctx
    HAS_CTX = True
except ImportError:
    HAS_CTX = False
    warnings.warn("contextily not installed; animations will have no basemap")


# ============================================================
#  坐标变换工具
# ============================================================
_transformer = pyproj.Transformer.from_crs(CRS_UTM, CRS_WGS84, always_xy=True)


def _to_wgs(x, y):
    """UTM-50N → WGS84 经纬度"""
    return _transformer.transform(x, y)


# ============================================================
#  动图配置
# ============================================================
ANIM_CONFIG = dict(
    # ─── 时间轴 ───
    duration_min      = 45,        # 总动图时长 (min, 与 STAGE_TIMES 对应)
    frame_interval_sec = 30,       # 每帧时间步长 (s) → 45min/30s = 90 帧
    fps               = 6,         # 输出 GIF 帧率 (帧/秒)

    # ─── 显示性能 ───
    max_residents     = 3000,      # 单帧最大居民点数 (超出则随机抽样)
    figsize           = (12, 10),
    dpi               = 100,       # GIF 用 100 dpi 控制文件大小

    # ─── 配色 ───
    color_moving      = "#1f77b4", # 蓝色 - 行进中
    color_arrived     = "#2ca02c", # 绿色 - 已到达
    color_npp         = "#ffd700", # 金色 - 核电厂
    color_bus_stop    = "#ff7f0e", # 橙色 - 上车点
    point_size_moving = 18,
    point_size_arrived = 22,
)


# ============================================================
#  风险场阶段映射
# ============================================================
def _stage_for_time(t_sec, stage_times_min=STAGE_TIMES):
    """
    根据当前时间 (秒) 返回对应的风险场阶段索引 (0-3)。
    STAGE_TIMES = [0, 15, 25, 35, 45] (min)
    映射规则:
        t ∈ [ 0,15) → stage 0  (0-15 min 风险场)
        t ∈ [15,25) → stage 1
        t ∈ [25,35) → stage 2
        t ∈ [35,45] → stage 3
    """
    t_min = t_sec / 60.0
    for k in range(len(stage_times_min) - 1):
        if stage_times_min[k] <= t_min < stage_times_min[k + 1]:
            return k
    return len(stage_times_min) - 2  # 最后阶段


# ============================================================
#  预计算: 每位居民的到达时间 & 路径长度缓存
# ============================================================
def _precompute_arrivals(result_dict):
    """
    返回:
        arrival_t  - ndarray (N_res,) 每位居民步行到达上车点的时间 (s)
        path_len   - ndarray (N_res,) 每位居民的路径总长度 (m)
        valid_mask - ndarray (N_res,) bool, True 表示该居民有有效路径
    """
    res_df       = result_dict["res_df"]
    assignment   = result_dict["assignment"]
    paths        = result_dict["paths"]
    active_idx   = result_dict["active_indices"]
    speed        = result_dict["speed"]

    g2l = {i: il for il, i in enumerate(active_idx)}
    N = len(res_df)

    arrival_t  = np.full(N, np.inf, dtype=np.float64)
    path_len   = np.zeros(N, dtype=np.float64)
    valid_mask = np.zeros(N, dtype=bool)

    for i in range(N):
        j = assignment[i]
        if j == -1 or i not in g2l:
            continue
        pl = paths.get((g2l[i], j))
        if pl is None or pl.length <= 0:
            continue
        path_len[i]  = pl.length
        arrival_t[i] = pl.length / speed
        valid_mask[i] = True

    return arrival_t, path_len, valid_mask


def _interpolate_position(result_dict, i, t_sec, path_len_i, arrival_t_i):
    """
    返回居民 i 在时间 t_sec 时的 (x, y) UTM 坐标。
    若已到达, 返回上车点坐标; 若无效, 返回 None。
    """
    paths       = result_dict["paths"]
    assignment  = result_dict["assignment"]
    snapped_bus = result_dict["bus_xy"]
    active_idx  = result_dict["active_indices"]
    speed       = result_dict["speed"]

    j = assignment[i]
    if j == -1:
        return None

    if t_sec >= arrival_t_i:
        return snapped_bus[j]

    g2l = {i_: il for il, i_ in enumerate(active_idx)}
    if i not in g2l:
        return None
    pl = paths.get((g2l[i], j))
    if pl is None:
        return None

    frac = min(t_sec * speed / pl.length, 1.0)
    pt = pl.interpolate(frac, normalized=True)
    return (pt.x, pt.y)


# ============================================================
#  核心: 单组动图生成
# ============================================================
def animate_group(result_dict, risk_arrays, output_dir,
                  fps=None, max_residents=None, verbose=True):
    """
    为单个性别×年龄段亚组生成 GIF 疏散动图。

    参数:
        result_dict   – optimize_group() 返回的结果字典
                        必须包含: group_name, gender, age_group, speed,
                                  res_df, assignment, paths, bus_xy,
                                  active_indices
        risk_arrays   – list of 4 个风险场矩阵 (来自 load_all_risk_data)
        output_dir    – GIF 输出目录
        fps           – 帧率 (默认 ANIM_CONFIG['fps'])
        max_residents – 单帧最大点数 (默认 ANIM_CONFIG['max_residents'])
        verbose       – 是否打印进度

    返回:
        gif_path – 生成的 GIF 文件路径
    """
    fps = fps or ANIM_CONFIG["fps"]
    max_res = max_residents or ANIM_CONFIG["max_residents"]

    name = result_dict["group_name"]
    gender = result_dict["gender"]
    age = result_dict["age_group"]
    speed = result_dict["speed"]

    if verbose:
        print(f"\n🎬 Animating {name}  speed={speed:.2f} m/s ...")

    # ── 1. 预计算到达时间 ──
    arrival_t, path_len, valid_mask = _precompute_arrivals(result_dict)
    n_valid = int(valid_mask.sum())
    if n_valid == 0:
        print(f"   ⚠️  {name}: no valid residents, skip")
        return None
    if verbose:
        print(f"   {n_valid} residents with valid paths")

    # ── 2. 居民抽样 (性能) ──
    valid_idx = np.where(valid_mask)[0]
    if len(valid_idx) > max_res:
        rng = np.random.default_rng(42)
        sample_idx = rng.choice(valid_idx, size=max_res, replace=False)
        if verbose:
            print(f"   subsampled {max_res}/{len(valid_idx)} for animation")
    else:
        sample_idx = valid_idx
    n_sample = len(sample_idx)

    # ── 3. 预计算每个采样居民在每帧的位置 (向量化以提速) ──
    # 帧时间序列
    n_frames = int(ANIM_CONFIG["duration_min"] * 60 / ANIM_CONFIG["frame_interval_sec"]) + 1
    frame_times = np.linspace(0, ANIM_CONFIG["duration_min"] * 60, n_frames)

    if verbose:
        print(f"   precomputing {n_sample} × {n_frames} positions ...")

    # 提前缓存 LineString 引用
    paths       = result_dict["paths"]
    assignment  = result_dict["assignment"]
    snapped_bus = result_dict["bus_xy"]
    active_idx  = result_dict["active_indices"]
    g2l = {i_: il for il, i_ in enumerate(active_idx)}

    # 位置数组: shape = (n_frames, n_sample, 2) for (x_utm, y_utm)
    positions = np.full((n_frames, n_sample, 2), np.nan, dtype=np.float64)
    arrived_mask_per_frame = np.zeros((n_frames, n_sample), dtype=bool)

    # 提速: 对每个采样居民, 一次性获取 LineString, 然后批量插值
    for k, i in enumerate(sample_idx):
        j = assignment[i]
        bx, by = snapped_bus[j]
        pl = paths.get((g2l[i], j))
        if pl is None:
            continue
        L = pl.length
        a_t = arrival_t[i]

        for f, t in enumerate(frame_times):
            if t >= a_t:
                positions[f, k] = (bx, by)
                arrived_mask_per_frame[f, k] = True
            else:
                frac = (t * speed) / L
                # shapely interpolate 是单点调用; 这里只能逐点
                pt = pl.interpolate(frac, normalized=True)
                positions[f, k] = (pt.x, pt.y)

    # ── 4. 批量 UTM → WGS84 (一次性) ──
    flat_x = positions[..., 0].ravel()
    flat_y = positions[..., 1].ravel()
    valid_pts = ~np.isnan(flat_x)
    flat_lon = np.full_like(flat_x, np.nan)
    flat_lat = np.full_like(flat_y, np.nan)
    if valid_pts.any():
        lo, la = _to_wgs(flat_x[valid_pts], flat_y[valid_pts])
        flat_lon[valid_pts] = lo
        flat_lat[valid_pts] = la
    lon_arr = flat_lon.reshape(positions[..., 0].shape)
    lat_arr = flat_lat.reshape(positions[..., 1].shape)

    # ── 5. 计算地图范围 (基于第一阶段风险场) ──
    ra0 = risk_arrays[0]
    ny, nx_ = ra0.shape
    x_min = CENTER_UTM[0] - (nx_ / 2) * GRID_RES
    x_max = CENTER_UTM[0] + (nx_ / 2) * GRID_RES
    y_min_v = CENTER_UTM[1] - (ny / 2) * GRID_RES
    y_max_v = CENTER_UTM[1] + (ny / 2) * GRID_RES
    lon_min, lat_min = _to_wgs(x_min, y_min_v)
    lon_max, lat_max = _to_wgs(x_max, y_max_v)
    lon_c, lat_c = _to_wgs(*CENTER_UTM)

    # 上车点经纬度 (静态)
    bus_lon, bus_lat = _to_wgs(snapped_bus[:, 0], snapped_bus[:, 1])

    # ── 6. 创建 figure ──
    fig, ax = plt.subplots(figsize=ANIM_CONFIG["figsize"],
                           dpi=ANIM_CONFIG["dpi"])
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)

    if HAS_CTX:
        try:
            ctx.add_basemap(ax, crs="EPSG:4326",
                            source=ctx.providers.CartoDB.Positron,
                            alpha=VIZ_CONFIG["basemap_alpha"])
        except Exception:
            pass

    # 风险场色表
    cmap_risk = mcolors.LinearSegmentedColormap.from_list(
        "risk", VIZ_CONFIG["risk_colors"], N=100)
    cmap_risk.set_bad(alpha=0)

    # 预计算每个 stage 的 vmax (统一色阶, 避免帧间跳变)
    risk_vmaxs = []
    for ra in risk_arrays:
        if np.any(ra > 0):
            risk_vmaxs.append(np.percentile(ra[ra > 0], 95))
        else:
            risk_vmaxs.append(1.0)
    global_vmax = max(risk_vmaxs)

    # 风险场图层 (动态更新)
    risk_im = ax.imshow(
        np.ma.masked_where(ra0 == 0, ra0),
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin="upper", cmap=cmap_risk,
        alpha=VIZ_CONFIG["risk_alpha"],
        vmin=0, vmax=global_vmax, zorder=2,
    )

    # 上车点 (静态)
    ax.scatter(bus_lon, bus_lat, c=ANIM_CONFIG["color_bus_stop"],
               s=35, marker="s", edgecolor="black", lw=0.5,
               alpha=0.8, zorder=5, label="Pickup stops")

    # 核电厂 (静态)
    ax.scatter(lon_c, lat_c, c=ANIM_CONFIG["color_npp"], s=400,
               marker="*", edgecolor="red", lw=2.5,
               zorder=12, label="NPP")

    # 居民散点 (动态)
    scat_moving = ax.scatter(
        [], [], c=ANIM_CONFIG["color_moving"],
        s=ANIM_CONFIG["point_size_moving"],
        alpha=0.75, zorder=9, label="Moving",
        edgecolor="white", lw=0.3,
    )
    scat_arrived = ax.scatter(
        [], [], c=ANIM_CONFIG["color_arrived"],
        s=ANIM_CONFIG["point_size_arrived"],
        alpha=0.85, zorder=10, label="Arrived",
        edgecolor="darkgreen", lw=0.5,
    )

    # 标题与图例
    title = ax.set_title("", fontsize=14, fontweight="bold")
    ax.legend(loc="lower left", fontsize=9, framealpha=0.85)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.25, ls="--")

    # 信息文本框
    info_box = ax.text(
        0.02, 0.98, "", transform=ax.transAxes,
        fontsize=10, verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.4",
                  facecolor="white", alpha=0.85, edgecolor="gray"),
        zorder=15,
    )

    # ── 7. 更新函数 ──
    last_stage = [-1]   # 闭包记忆, 避免每帧都重画 imshow

    def _update(f):
        t_sec = frame_times[f]
        t_min = t_sec / 60.0

        # 风险场切换 (仅当 stage 变化时)
        stage = _stage_for_time(t_sec)
        if stage != last_stage[0]:
            last_stage[0] = stage
            ra = risk_arrays[stage]
            risk_im.set_data(np.ma.masked_where(ra == 0, ra))

        # 居民位置
        arr_msk = arrived_mask_per_frame[f]
        mov_msk = (~arr_msk) & ~np.isnan(lon_arr[f])

        scat_moving.set_offsets(
            np.column_stack([lon_arr[f, mov_msk], lat_arr[f, mov_msk]])
            if mov_msk.any() else np.empty((0, 2))
        )
        scat_arrived.set_offsets(
            np.column_stack([lon_arr[f, arr_msk], lat_arr[f, arr_msk]])
            if arr_msk.any() else np.empty((0, 2))
        )

        # 标题与信息
        n_mov = int(mov_msk.sum())
        n_arr = int(arr_msk.sum())
        title.set_text(
            f"Daya Bay Evacuation — {gender.capitalize()} {age}  "
            f"(t = {t_min:5.1f} min)"
        )
        info_box.set_text(
            f"Group: {name}\n"
            f"Walk speed: {speed:.2f} m/s\n"
            f"Risk stage: {stage + 1}/4 "
            f"({STAGE_TIMES[stage]}–{STAGE_TIMES[stage+1]} min)\n"
            f"Moving: {n_mov}    Arrived: {n_arr}\n"
            f"Sample: {n_sample}/{n_valid}"
        )

        return risk_im, scat_moving, scat_arrived, title, info_box

    # ── 8. 创建动画并保存 GIF ──
    if verbose:
        print(f"   rendering {n_frames} frames @ {fps} fps ...")

    anim = animation.FuncAnimation(
        fig, _update, frames=n_frames,
        interval=1000 / fps, blit=False, repeat=False,
    )

    os.makedirs(output_dir, exist_ok=True)
    gif_path = os.path.join(output_dir, f"evacuation_{name}.gif")

    writer = animation.PillowWriter(fps=fps)
    anim.save(gif_path, writer=writer, dpi=ANIM_CONFIG["dpi"])
    plt.close(fig)

    file_kb = os.path.getsize(gif_path) / 1024
    if verbose:
        print(f"   ✅ Saved: {gif_path} ({file_kb:.0f} KB)")
    return gif_path


# ============================================================
#  批量: 12 组动图
# ============================================================
def animate_all_groups(results, risk_arrays, output_root,
                       fps=None, max_residents=None):
    """
    为 results 列表中的所有亚组生成动图。

    参数:
        results       – optimize_group() 返回的字典列表
        risk_arrays   – 4 个风险场矩阵
        output_root   – 输出根目录, 实际保存到 {output_root}/animations/
        fps, max_residents – 透传至 animate_group

    返回:
        gif_paths – {group_name: gif_path}
    """
    out_dir = os.path.join(output_root, "animations")
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🎬 Generating {len(results)} group animations → {out_dir}")
    print(f"{'='*60}")

    gif_paths = {}
    for i, res in enumerate(results, 1):
        if res is None:
            continue
        print(f"\n[{i}/{len(results)}] {res['group_name']}")
        try:
            p = animate_group(res, risk_arrays, out_dir,
                              fps=fps, max_residents=max_residents)
            if p:
                gif_paths[res["group_name"]] = p
        except Exception as e:
            print(f"   ❌ Failed: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n🎉 Done: {len(gif_paths)}/{len(results)} animations generated")
    return gif_paths


# ============================================================
#  CLI 入口
# ============================================================
def _cli():
    parser = argparse.ArgumentParser(
        description="Generate per-group evacuation animations (GIFs)"
    )
    parser.add_argument("--pickle", type=str, default=None,
                        help="Path to pickled results list (from main.py)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory (default: OUTPUT_ROOT/animations)")
    parser.add_argument("--fps", type=int, default=None,
                        help=f"GIF frame rate (default: {ANIM_CONFIG['fps']})")
    parser.add_argument("--max-residents", type=int, default=None,
                        help="Max residents shown per frame (subsampling)")
    parser.add_argument("--run-and-animate", action="store_true",
                        help="Run optimization first, then animate")
    parser.add_argument("--test", action="store_true",
                        help="(With --run-and-animate) Use test mode")
    parser.add_argument("--serial", action="store_true",
                        help="(With --run-and-animate) Serial execution")
    args = parser.parse_args()

    # ── 加载风险场 ──
    from data_loader import load_all_risk_data
    print("Loading risk fields ...")
    risk_arrays, _, _ = load_all_risk_data(RISK_VALUE_FILES)

    # ── 获取 results ──
    if args.run_and_animate:
        from main import main as run_main
        print("Running optimization ...")
        results = run_main(
            selected_groups=["m_20-29"] if args.test else None,
            parallel=not args.serial,
            selection_method="knee",
        )
    elif args.pickle:
        if not os.path.exists(args.pickle):
            print(f"❌ Pickle file not found: {args.pickle}")
            sys.exit(1)
        print(f"Loading results from {args.pickle} ...")
        with open(args.pickle, "rb") as f:
            results = pickle.load(f)
    else:
        print("❌ Must specify either --pickle FILE or --run-and-animate")
        parser.print_help()
        sys.exit(1)

    if not results:
        print("❌ No results to animate")
        sys.exit(1)

    # ── 生成动图 ──
    out_dir = args.output or OUTPUT_ROOT
    animate_all_groups(results, risk_arrays, out_dir,
                       fps=args.fps, max_residents=args.max_residents)


if __name__ == "__main__":
    _cli()
