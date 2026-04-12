"""
可视化模块
- Pareto 前沿演化图（含4子图拆分版本）
- 最终 Pareto 前沿 + 选定解标注
- 风险地图 + 分配结果叠加
- 疏散阶段居民位置图（4 个时间断面）
- v5.3 第二阶段疏散路径可视化
"""
import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import pyproj
try:
    import contextily as ctx
    HAS_CTX = True
except ImportError:
    HAS_CTX = False

from config import (
    CENTER_UTM, GRID_RES, STAGE_TIMES, WALK_SPEED,
    VIZ_CONFIG, PARETO_VIZ, CRS_UTM, CRS_WGS84,
)


# ============================================================
#  坐标转换工具
# ============================================================
_transformer = pyproj.Transformer.from_crs(CRS_UTM, CRS_WGS84, always_xy=True)

def _to_wgs(x, y):
    return _transformer.transform(x, y)


# ============================================================
#  Pareto 前沿可视化类
# ============================================================
class ParetoVisualizer:
    """记录每代 Pareto 前沿并生成演化图"""

    def __init__(self, output_dir, group_name="default"):
        self.output_dir = output_dir
        self.group_name = group_name
        self.history = []
        self.pareto_dir = os.path.join(output_dir, "pareto_evolution")
        os.makedirs(self.pareto_dir, exist_ok=True)

    # ---------- 记录 ----------
    def record(self, gen, pareto_front, population=None):
        pf = [(ind.fitness.values[0], ind.fitness.values[1])
              for ind in pareto_front
              if np.isfinite(ind.fitness.values[0])]
        pp = None
        if population:
            pp = [(ind.fitness.values[0], ind.fitness.values[1])
                  for ind in population
                  if np.isfinite(ind.fitness.values[0])]
        self.history.append(dict(gen=gen, pareto=pf, population=pp))

    # ---------- 当前代快照 ----------
    def plot_current(self, gen, pf, population=None, save=True):
        fig, ax = plt.subplots(figsize=PARETO_VIZ["figsize"], dpi=PARETO_VIZ["dpi"])
        pt = [ind.fitness.values[0] / 60 for ind in pf if np.isfinite(ind.fitness.values[0])]
        pr = [ind.fitness.values[1] for ind in pf if np.isfinite(ind.fitness.values[1])]

        if population:
            popt = [ind.fitness.values[0] / 60 for ind in population
                    if np.isfinite(ind.fitness.values[0]) and ind not in pf]
            popr = [ind.fitness.values[1] for ind in population
                    if np.isfinite(ind.fitness.values[1]) and ind not in pf]
            if popt:
                ax.scatter(popt, popr, c="lightgray", s=30, alpha=0.5, label="Dominated")

        if pt:
            si = np.argsort(pt)
            ax.plot([pt[i] for i in si], [pr[i] for i in si], "b-", lw=1.5, alpha=0.7)
            ax.scatter(pt, pr, c="blue", s=PARETO_VIZ["marker_size"],
                       edgecolor="darkblue", lw=1,
                       label=f"Pareto ({len(pt)})", zorder=3)
            if len(pt) >= 2:
                mi_t = int(np.argmin(pt))
                mi_r = int(np.argmin(pr))
                ax.scatter(pt[mi_t], pr[mi_t], c="green", s=PARETO_VIZ["highlight_size"],
                           marker="s", edgecolor="darkgreen", lw=2,
                           label=f"MinT {pt[mi_t]:.1f}min", zorder=4)
                ax.scatter(pt[mi_r], pr[mi_r], c="red", s=PARETO_VIZ["highlight_size"],
                           marker="^", edgecolor="darkred", lw=2,
                           label=f"MinR {pr[mi_r]:.2e}", zorder=4)

        ax.set_xlabel("Walking Time (min)")
        ax.set_ylabel("Cumulative Risk")
        ax.set_title(f"Pareto Front – {self.group_name}  Gen {gen}", fontweight="bold")
        ax.legend(fontsize=9); ax.grid(alpha=0.3)
        ax.ticklabel_format(style="scientific", axis="y", scilimits=(0, 0))
        plt.tight_layout()
        if save:
            p = os.path.join(self.pareto_dir, f"pareto_gen_{gen:04d}.png")
            fig.savefig(p, dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig)
        return fig

    # ---------- 最终选定解 ----------
    def plot_final(self, pf, selected, method, save=True):
        fig, ax = plt.subplots(figsize=PARETO_VIZ["figsize"], dpi=PARETO_VIZ["dpi"])
        pt = [ind.fitness.values[0] / 60 for ind in pf]
        pr = [ind.fitness.values[1] for ind in pf]
        si = np.argsort(pt)
        ax.plot([pt[i] for i in si], [pr[i] for i in si], "b-", lw=2, alpha=0.7)
        ax.scatter(pt, pr, c="blue", s=80, edgecolor="darkblue",
                   label=f"Pareto ({len(pf)})", zorder=3)
        st = selected.fitness.values[0] / 60
        sr = selected.fitness.values[1]
        ax.scatter(st, sr, c="gold", s=300, marker="*", edgecolor="orange", lw=2,
                   label=f"Selected ({method})", zorder=5)
        ax.annotate(f"T={st:.1f}min\nR={sr:.2e}", (st, sr),
                    xytext=(st + 2, sr * 1.1), fontsize=9, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color="orange"),
                    bbox=dict(boxstyle="round", fc="yellow", alpha=0.7))
        ax.set_xlabel("Walking Time (min)")
        ax.set_ylabel("Cumulative Risk")
        ax.set_title(f"Final Pareto – {self.group_name} ({method})", fontweight="bold")
        ax.legend(fontsize=9); ax.grid(alpha=0.3)
        ax.ticklabel_format(style="scientific", axis="y", scilimits=(0, 0))
        plt.tight_layout()
        if save:
            fig.savefig(os.path.join(self.pareto_dir, "final_pareto.png"),
                        dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig)
        return fig

    # ---------- 4 张演化总结图 ----------
    def plot_summary(self, save=True):
        if not self.history:
            return
        gens = [r["gen"] for r in self.history]

        # 图 1: Pareto 前沿演化
        fig1, ax1 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        n = len(self.history)
        show = [0, n // 4, n // 2, 3 * n // 4, n - 1] if n > 5 else list(range(n))
        colors = plt.cm.viridis(np.linspace(0, 1, len(show)))
        for idx, gi in enumerate(show):
            r = self.history[gi]
            if r["pareto"]:
                ts = sorted(r["pareto"], key=lambda p: p[0])
                ax1.plot([p[0] / 60 for p in ts], [p[1] for p in ts], "o-",
                         color=colors[idx], ms=4, lw=1.5, label=f"Gen {r['gen']}")
        ax1.set_xlabel("Time (min)"); ax1.set_ylabel("Risk")
        ax1.set_title(f"Pareto Evolution – {self.group_name}", fontweight="bold")
        ax1.legend(); ax1.grid(alpha=0.3)
        ax1.ticklabel_format(style="scientific", axis="y", scilimits=(0, 0))
        plt.tight_layout()
        if save:
            fig1.savefig(os.path.join(self.pareto_dir, "1_pareto_evolution.png"),
                         dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig1)

        # 图 2: 目标收敛
        mt_list, mr_list = [], []
        for r in self.history:
            if r["pareto"]:
                mt_list.append(min(p[0] for p in r["pareto"]) / 60)
                mr_list.append(min(p[1] for p in r["pareto"]))
            else:
                mt_list.append(np.nan); mr_list.append(np.nan)
        fig2, ax2 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax2b = ax2.twinx()
        ax2.plot(gens, mt_list, "b-", lw=2, label="Min Time")
        ax2b.plot(gens, mr_list, "r-", lw=2, label="Min Risk")
        ax2.set_xlabel("Generation"); ax2.set_ylabel("Time (min)", color="b")
        ax2b.set_ylabel("Risk", color="r")
        ax2.set_title(f"Convergence – {self.group_name}", fontweight="bold")
        ax2.grid(alpha=0.3)
        lines = ax2.get_lines() + ax2b.get_lines()
        ax2.legend(lines, [l.get_label() for l in lines])
        plt.tight_layout()
        if save:
            fig2.savefig(os.path.join(self.pareto_dir, "2_convergence.png"),
                         dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig2)

        # 图 3: Pareto 大小
        sizes = [len(r["pareto"]) for r in self.history]
        fig3, ax3 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax3.plot(gens, sizes, "g-", lw=2, marker="o", ms=3)
        ax3.fill_between(gens, sizes, alpha=0.3, color="green")
        ax3.set_xlabel("Generation"); ax3.set_ylabel("Pareto Size")
        ax3.set_title(f"Pareto Size – {self.group_name}", fontweight="bold")
        ax3.grid(alpha=0.3); plt.tight_layout()
        if save:
            fig3.savefig(os.path.join(self.pareto_dir, "3_pareto_size.png"),
                         dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig3)

        # 图 4: 前沿范围
        st, sr2 = [], []
        for r in self.history:
            if len(r["pareto"]) >= 2:
                ts_ = [p[0] for p in r["pareto"]]
                rs_ = [p[1] for p in r["pareto"]]
                st.append((max(ts_) - min(ts_)) / 60)
                sr2.append(max(rs_) - min(rs_))
            else:
                st.append(0); sr2.append(0)
        fig4, ax4 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax4b = ax4.twinx()
        ax4.plot(gens, st, "b-", lw=2, label="Time Spread")
        ax4b.plot(gens, sr2, "r-", lw=2, label="Risk Spread")
        ax4.set_xlabel("Generation"); ax4.set_ylabel("Time Spread (min)", color="b")
        ax4b.set_ylabel("Risk Spread", color="r")
        ax4.set_title(f"Pareto Spread – {self.group_name}", fontweight="bold")
        ax4.grid(alpha=0.3)
        lines = ax4.get_lines() + ax4b.get_lines()
        ax4.legend(lines, [l.get_label() for l in lines])
        plt.tight_layout()
        if save:
            fig4.savefig(os.path.join(self.pareto_dir, "4_pareto_spread.png"),
                         dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig4)


# ============================================================
#  风险地图 + 分配结果可视化
# ============================================================
def plot_assignment_map(risk_array, bus_xy, assignment, res_df,
                        active_indices, output_dir,
                        filename="final_assignment_map.png",
                        suffix=""):
    """在风险底图上标注使用的上车点及居民移动方向"""
    ny, nx_ = risk_array.shape
    x_min = CENTER_UTM[0] - (nx_ / 2) * GRID_RES
    x_max = CENTER_UTM[0] + (nx_ / 2) * GRID_RES
    y_max = CENTER_UTM[1] + (ny / 2) * GRID_RES
    y_min = CENTER_UTM[1] - (ny / 2) * GRID_RES

    lon_min, lat_min = _to_wgs(x_min, y_min)
    lon_max, lat_max = _to_wgs(x_max, y_max)
    lon_c, lat_c = _to_wgs(*CENTER_UTM)

    fig, ax = plt.subplots(figsize=VIZ_CONFIG["figsize"], dpi=VIZ_CONFIG["dpi"])
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)

    if HAS_CTX:
        try:
            ctx.add_basemap(ax, crs="EPSG:4326",
                            source=ctx.providers.CartoDB.Positron,
                            alpha=VIZ_CONFIG["basemap_alpha"])
        except Exception:
            pass

    risk_m = np.ma.masked_where(risk_array == 0, risk_array)
    cmap = mcolors.LinearSegmentedColormap.from_list("risk", VIZ_CONFIG["risk_colors"], N=100)
    cmap.set_bad(alpha=0)
    vmax = np.percentile(risk_array[risk_array > 0], 95) if np.any(risk_array > 0) else 1
    ax.imshow(risk_m, extent=[lon_min, lon_max, lat_min, lat_max],
              origin="upper", cmap=cmap, alpha=VIZ_CONFIG["risk_alpha"],
              vmin=0, vmax=vmax)

    used = set(assignment) - {-1}
    u_lon, u_lat, uu_lon, uu_lat = [], [], [], []
    for j, (bx, by) in enumerate(bus_xy):
        lo, la = _to_wgs(bx, by)
        if j in used:
            u_lon.append(lo); u_lat.append(la)
        else:
            uu_lon.append(lo); uu_lat.append(la)
    if uu_lon:
        ax.scatter(uu_lon, uu_lat, c="gray", s=20, alpha=0.3, label=f"Unused ({len(uu_lon)})")
    if u_lon:
        ax.scatter(u_lon, u_lat, c="lime", s=80, marker="^", edgecolor="darkgreen",
                   lw=1.5, label=f"Used ({len(u_lon)})", zorder=8)

    tw_lon, tw_lat, aw_lon, aw_lat = [], [], [], []
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1: continue
        rx, ry = res_df["x"].values[i], res_df["y"].values[i]
        rd = np.hypot(rx - CENTER_UTM[0], ry - CENTER_UTM[1])
        bd = np.hypot(bus_xy[j][0] - CENTER_UTM[0], bus_xy[j][1] - CENTER_UTM[1])
        lo, la = _to_wgs(rx, ry)
        if bd < rd:
            tw_lon.append(lo); tw_lat.append(la)
        else:
            aw_lon.append(lo); aw_lat.append(la)
    if aw_lon:
        ax.scatter(aw_lon, aw_lat, c="blue", s=15, alpha=0.6,
                   label=f"Away ({len(aw_lon)})", zorder=7)
    if tw_lon:
        ax.scatter(tw_lon, tw_lat, c="red", s=20, edgecolor="darkred", lw=0.5,
                   alpha=0.8, label=f"Toward ({len(tw_lon)})", zorder=7)

    ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*", edgecolor="red",
               lw=3, label="NPP", zorder=10)
    ax.legend(loc="lower left", fontsize=9, ncol=2)
    ax.set_title("Final Assignment Map", fontweight="bold")
    ax.grid(alpha=0.3, ls="--")
    plt.tight_layout()
    p = os.path.join(output_dir, filename.replace(".png", f"{suffix}.png") if suffix else filename)
    fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"📊 Saved: {p}")


# ============================================================
#  疏散阶段可视化
# ============================================================
def plot_evacuation_stages(risk_arrays, bus_xy, assignment, res_df,
                           active_indices, paths, snapped_res, snapped_bus,
                           output_dir, speed=WALK_SPEED):
    """生成 4 个时间断面的居民移动快照"""
    from optimizer import _risk as _r  # 风险查询复用

    g2l = {i: il for il, i in enumerate(active_indices)}

    # 预计算到达时间
    arrival = {}
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1: arrival[i] = np.inf; continue
        if i in g2l:
            pl = paths.get((g2l[i], j))
            arrival[i] = pl.length / speed if pl else np.inf
        else:
            arrival[i] = 0

    for stage_idx in range(4):
        stage_min = STAGE_TIMES[stage_idx]
        ts = stage_min * 60

        ny, nx_ = risk_arrays[stage_idx].shape
        x_min = CENTER_UTM[0] - (nx_ / 2) * GRID_RES
        x_max = CENTER_UTM[0] + (nx_ / 2) * GRID_RES
        y_max_val = CENTER_UTM[1] + (ny / 2) * GRID_RES
        y_min_val = CENTER_UTM[1] - (ny / 2) * GRID_RES

        lon_min, lat_min = _to_wgs(x_min, y_min_val)
        lon_max, lat_max = _to_wgs(x_max, y_max_val)
        lon_c, lat_c = _to_wgs(*CENTER_UTM)

        fig, ax = plt.subplots(figsize=VIZ_CONFIG["figsize"], dpi=VIZ_CONFIG["dpi"])
        ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)

        if HAS_CTX:
            try:
                ctx.add_basemap(ax, crs="EPSG:4326",
                                source=ctx.providers.CartoDB.Positron,
                                alpha=VIZ_CONFIG["basemap_alpha"])
            except Exception:
                pass

        ra = risk_arrays[stage_idx]
        rm = np.ma.masked_where(ra == 0, ra)
        cmap = mcolors.LinearSegmentedColormap.from_list("r", VIZ_CONFIG["risk_colors"], N=100)
        cmap.set_bad(alpha=0)
        vmax = np.percentile(ra[ra > 0], 95) if np.any(ra > 0) else 1
        ax.imshow(rm, extent=[lon_min, lon_max, lat_min, lat_max],
                  origin="upper", cmap=cmap, alpha=VIZ_CONFIG["risk_alpha"],
                  vmin=0, vmax=vmax)

        arr_lon, arr_lat = [], []
        mov_lon, mov_lat = [], []
        for i in range(len(res_df)):
            j = assignment[i]
            if j == -1: continue
            a = arrival.get(i, np.inf)
            if not np.isfinite(a): continue

            if ts >= a:
                x, y = snapped_bus[j]
                lo, la = _to_wgs(x, y)
                arr_lon.append(lo); arr_lat.append(la)
            elif i in g2l:
                pl = paths.get((g2l[i], j))
                if pl and pl.length > 0:
                    frac = min(ts * speed / pl.length, 1.0)
                    pt = pl.interpolate(frac, normalized=True)
                    lo, la = _to_wgs(pt.x, pt.y)
                else:
                    lo, la = _to_wgs(*snapped_bus[j])
                mov_lon.append(lo); mov_lat.append(la)

        if mov_lon:
            ax.scatter(mov_lon, mov_lat, c="blue", s=30, alpha=0.7,
                       label=f"Moving ({len(mov_lon)})", zorder=9)
        if arr_lon:
            ax.scatter(arr_lon, arr_lat, c="green", s=40, marker="o",
                       edgecolor="darkgreen", alpha=0.8,
                       label=f"Arrived ({len(arr_lon)})", zorder=9)

        ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*",
                   edgecolor="red", lw=3, label="NPP", zorder=11)
        ax.legend(loc="lower left", fontsize=9)
        ax.set_title(f"Evacuation at t = {stage_min} min", fontweight="bold")
        ax.grid(alpha=0.3, ls="--")
        plt.tight_layout()
        p = os.path.join(output_dir,
                         f"evacuation_stage_{stage_idx+1}_t{stage_min}min.png")
        fig.savefig(p, dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"📊 Saved: {p}")


# ============================================================
#  v5.3 第二阶段疏散路径可视化 (改进 3)
# ============================================================
def plot_phase2_routing(risk_array, bus_xy, shelter_xy, shelter_capacities,
                        shelter_mapping_multi, shelter_geometries,
                        shelter_load, output_dir,
                        used_stops=None,
                        filename="phase2_bus_routing.png"):
    """
    在底图上绘制第二阶段（车辆撤离）的路径:
      - 底图: 风险场 + 核电厂位置
      - 上车点: 三角形, 已使用的高亮
      - 避难所: 方块, 颜色按利用率渐变 (绿→黄→红)
      - 路径: 上车点→避难所的真实路网路径 (Dijkstra), 按上车点着色
      - 标注: 上车点 ID + 避难所 ID

    参数:
        risk_array            – 用于底图的风险矩阵 (一般用最后阶段)
        bus_xy                – (n_bus, 2) UTM 上车点坐标
        shelter_xy            – (n_shelters, 2) UTM 避难所坐标
        shelter_capacities    – (n_shelters,) 名义容量
        shelter_mapping_multi – dict {stop_idx: {'candidates': [...]}}
                                只画 candidates[0] (Top1) 的路径作为可视化代表
        shelter_geometries    – dict {(stop_idx, shelter_idx): LineString}
                                Dijkstra 真实路网路径几何
        shelter_load          – dict {shelter_idx: 实际承载人口}, 用于着色
        output_dir            – 输出目录
        used_stops            – set of stop indices that were actually used
                                (None 时绘制全部 mapping 中的上车点)
        filename              – 输出文件名
    """
    if shelter_mapping_multi is None or not shelter_mapping_multi:
        print("⚠️  plot_phase2_routing: no shelter_mapping_multi, skip")
        return

    ny, nx_ = risk_array.shape
    x_min = CENTER_UTM[0] - (nx_ / 2) * GRID_RES
    x_max = CENTER_UTM[0] + (nx_ / 2) * GRID_RES
    y_max_ = CENTER_UTM[1] + (ny / 2) * GRID_RES
    y_min = CENTER_UTM[1] - (ny / 2) * GRID_RES
    lon_min, lat_min = _to_wgs(x_min, y_min)
    lon_max, lat_max = _to_wgs(x_max, y_max_)
    lon_c, lat_c = _to_wgs(*CENTER_UTM)

    fig, ax = plt.subplots(figsize=(16, 12), dpi=VIZ_CONFIG["dpi"])
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)

    if HAS_CTX:
        try:
            ctx.add_basemap(ax, crs="EPSG:4326",
                            source=ctx.providers.CartoDB.Positron,
                            alpha=VIZ_CONFIG["basemap_alpha"])
        except Exception:
            pass

    # 风险底图
    risk_m = np.ma.masked_where(risk_array == 0, risk_array)
    cmap_risk = mcolors.LinearSegmentedColormap.from_list(
        "risk", VIZ_CONFIG["risk_colors"], N=100)
    cmap_risk.set_bad(alpha=0)
    vmax = np.percentile(risk_array[risk_array > 0], 95) \
           if np.any(risk_array > 0) else 1
    ax.imshow(risk_m, extent=[lon_min, lon_max, lat_min, lat_max],
              origin="upper", cmap=cmap_risk, alpha=VIZ_CONFIG["risk_alpha"],
              vmin=0, vmax=vmax)

    # ── 1. 绘制路径 (上车点 → Top1 避难所) ──
    # 每个上车点用不同颜色; 路径用真实 Dijkstra 几何 (若存在)
    n_stops = len(shelter_mapping_multi)
    if used_stops is None:
        stop_iter = list(shelter_mapping_multi.keys())
    else:
        stop_iter = [j for j in shelter_mapping_multi if j in used_stops]
    cmap_stops = plt.cm.get_cmap("tab20", max(len(stop_iter), 2))

    drawn_shelter_idx = set()
    for k, stop_j in enumerate(stop_iter):
        info = shelter_mapping_multi[stop_j]
        if not info.get("candidates"):
            continue
        top1 = info["candidates"][0]
        sh_idx = top1["shelter_idx"]
        drawn_shelter_idx.add(sh_idx)

        # 路径几何 (优先用真实 Dijkstra, 回退欧氏直线)
        geom = shelter_geometries.get((stop_j, sh_idx)) if shelter_geometries else None

        color = cmap_stops(k % cmap_stops.N)
        if geom is not None:
            try:
                xs, ys = geom.xy
                lons, lats = [], []
                for x, y in zip(xs, ys):
                    lo, la = _to_wgs(x, y)
                    lons.append(lo); lats.append(la)
                ax.plot(lons, lats, "-", color=color, lw=2.0, alpha=0.7,
                        zorder=5)
            except Exception:
                geom = None  # fall through to straight line

        if geom is None:
            # 直线回退
            bx, by = bus_xy[stop_j]
            sx, sy = shelter_xy[sh_idx]
            blo, bla = _to_wgs(bx, by)
            slo, sla = _to_wgs(sx, sy)
            ax.plot([blo, slo], [bla, sla], "--", color=color, lw=1.5,
                    alpha=0.6, zorder=5)

    # ── 2. 绘制上车点 ──
    for k, stop_j in enumerate(stop_iter):
        bx, by = bus_xy[stop_j]
        lo, la = _to_wgs(bx, by)
        color = cmap_stops(k % cmap_stops.N)
        ax.scatter(lo, la, c=[color], s=120, marker="^",
                   edgecolor="black", lw=1.2, zorder=8)
        ax.annotate(f"S{stop_j}", (lo, la), xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=8, fontweight="bold")

    # ── 3. 绘制使用的避难所 (按利用率着色) ──
    util_cmap = mcolors.LinearSegmentedColormap.from_list(
        "util", ["#00cc00", "#cccc00", "#ff8800", "#cc0000"], N=100)

    for sh_idx in drawn_shelter_idx:
        sx, sy = shelter_xy[sh_idx]
        lo, la = _to_wgs(sx, sy)
        load = shelter_load.get(sh_idx, 0.0) if shelter_load else 0.0
        cap = float(shelter_capacities[sh_idx])
        util = min(load / max(cap, 1.0), 1.5)
        color = util_cmap(min(util / 1.0, 1.0))

        ax.scatter(lo, la, c=[color], s=180, marker="s",
                   edgecolor="darkblue", lw=1.5, zorder=9)
        ax.annotate(f"#{sh_idx}\n{load:.0f}/{cap:.0f}",
                    (lo, la), xytext=(7, -10),
                    textcoords="offset points",
                    fontsize=7, color="darkblue",
                    bbox=dict(boxstyle="round,pad=0.2",
                              facecolor="white", edgecolor="none", alpha=0.7))

    # ── 4. 核电厂 ──
    ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*",
               edgecolor="red", lw=3, zorder=10, label="NPP")

    # ── 5. Legend (utilization colorbar 单独绘制) ──
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    legend_items = [
        Line2D([0], [0], marker="^", color="w", markerfacecolor="lightblue",
               markeredgecolor="black", markersize=12, label="Pickup stop"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="lightgreen",
               markeredgecolor="darkblue", markersize=12, label="Shelter (load/cap)"),
        Line2D([0], [0], color="black", lw=2, label="Bus route (Dijkstra)"),
        Line2D([0], [0], color="black", lw=1.5, ls="--", label="Bus route (straight fallback)"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="yellow",
               markeredgecolor="red", markersize=18, label="NPP"),
    ]
    ax.legend(handles=legend_items, loc="lower left", fontsize=9, ncol=2)

    ax.set_title("Phase 2: Bus Routing — Pickup Stops to Long-term Shelters\n"
                 f"({len(stop_iter)} stops → {len(drawn_shelter_idx)} shelters used)",
                 fontweight="bold", fontsize=13)
    ax.grid(alpha=0.3, ls="--")
    plt.tight_layout()

    p = os.path.join(output_dir, filename)
    fig.savefig(p, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"📊 Saved: {p}")
