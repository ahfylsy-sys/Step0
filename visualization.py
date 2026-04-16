"""
可视化模块 v7.0
- Pareto 前沿演化图
- 风险地图 + 分配结果叠加
- 疏散阶段居民位置图
- 完整疏散动画（居民步行 + 巴士转运 + 时变风险场）
"""
import os
import math
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.animation as animation
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

_transformer = pyproj.Transformer.from_crs(CRS_UTM, CRS_WGS84, always_xy=True)

def _to_wgs(x, y):
    return _transformer.transform(x, y)


class ParetoVisualizer:
    def __init__(self, output_dir, group_name="default"):
        self.output_dir = output_dir
        self.group_name = group_name
        self.history = []
        self.pareto_dir = os.path.join(output_dir, "pareto_evolution")
        os.makedirs(self.pareto_dir, exist_ok=True)

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
                       edgecolor="darkblue", lw=1, label=f"Pareto ({len(pt)})", zorder=3)
            if len(pt) >= 2:
                mi_t = int(np.argmin(pt)); mi_r = int(np.argmin(pr))
                ax.scatter(pt[mi_t], pr[mi_t], c="green", s=PARETO_VIZ["highlight_size"],
                           marker="s", edgecolor="darkgreen", lw=2, label=f"MinT {pt[mi_t]:.1f}min", zorder=4)
                ax.scatter(pt[mi_r], pr[mi_r], c="red", s=PARETO_VIZ["highlight_size"],
                           marker="^", edgecolor="darkred", lw=2, label=f"MinR {pr[mi_r]:.2e}", zorder=4)
        ax.set_xlabel("Walking Time (min)"); ax.set_ylabel("Cumulative Risk")
        ax.set_title(f"Pareto Front - {self.group_name}  Gen {gen}", fontweight="bold")
        ax.legend(fontsize=9); ax.grid(alpha=0.3)
        ax.ticklabel_format(style="scientific", axis="y", scilimits=(0, 0))
        plt.tight_layout()
        if save:
            fig.savefig(os.path.join(self.pareto_dir, f"pareto_gen_{gen:04d}.png"),
                        dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig)

    def plot_final(self, pf, selected, method, save=True):
        fig, ax = plt.subplots(figsize=PARETO_VIZ["figsize"], dpi=PARETO_VIZ["dpi"])
        pt = [ind.fitness.values[0] / 60 for ind in pf]
        pr = [ind.fitness.values[1] for ind in pf]
        si = np.argsort(pt)
        ax.plot([pt[i] for i in si], [pr[i] for i in si], "b-", lw=2, alpha=0.7)
        ax.scatter(pt, pr, c="blue", s=80, edgecolor="darkblue", label=f"Pareto ({len(pf)})", zorder=3)
        st = selected.fitness.values[0] / 60; sr = selected.fitness.values[1]
        ax.scatter(st, sr, c="gold", s=300, marker="*", edgecolor="orange", lw=2,
                   label=f"Selected ({method})", zorder=5)
        ax.set_xlabel("Walking Time (min)"); ax.set_ylabel("Cumulative Risk")
        ax.set_title(f"Final Pareto - {self.group_name} ({method})", fontweight="bold")
        ax.legend(fontsize=9); ax.grid(alpha=0.3)
        plt.tight_layout()
        if save:
            fig.savefig(os.path.join(self.pareto_dir, "final_pareto.png"),
                        dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig)

    def plot_summary(self, save=True):
        if not self.history:
            return
        gens = [r["gen"] for r in self.history]
        fig1, ax1 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        n = len(self.history)
        show = [0, n//4, n//2, 3*n//4, n-1] if n > 5 else list(range(n))
        colors = plt.cm.viridis(np.linspace(0, 1, len(show)))
        for idx, gi in enumerate(show):
            r = self.history[gi]
            if r["pareto"]:
                ts = sorted(r["pareto"], key=lambda p: p[0])
                ax1.plot([p[0]/60 for p in ts], [p[1] for p in ts], "o-",
                         color=colors[idx], ms=4, lw=1.5, label=f"Gen {r['gen']}")
        ax1.set_xlabel("Time"); ax1.set_ylabel("Risk")
        ax1.set_title(f"Pareto Evolution - {self.group_name}", fontweight="bold")
        ax1.legend(); ax1.grid(alpha=0.3)
        plt.tight_layout()
        if save:
            fig1.savefig(os.path.join(self.pareto_dir, "1_pareto_evolution.png"), dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig1)
        mt_list = [min(p[0] for p in r["pareto"])/60 if r["pareto"] else np.nan for r in self.history]
        mr_list = [min(p[1] for p in r["pareto"]) if r["pareto"] else np.nan for r in self.history]
        fig2, ax2 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax2b = ax2.twinx()
        ax2.plot(gens, mt_list, "b-", lw=2, label="Min Time")
        ax2b.plot(gens, mr_list, "r-", lw=2, label="Min Risk")
        ax2.set_xlabel("Generation"); ax2.set_ylabel("Time (min)", color="b")
        ax2b.set_ylabel("Risk", color="r")
        ax2.set_title(f"Convergence - {self.group_name}", fontweight="bold")
        ax2.grid(alpha=0.3)
        lines = ax2.get_lines() + ax2b.get_lines()
        ax2.legend(lines, [l.get_label() for l in lines])
        plt.tight_layout()
        if save:
            fig2.savefig(os.path.join(self.pareto_dir, "2_convergence.png"), dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig2)
        sizes = [len(r["pareto"]) for r in self.history]
        fig3, ax3 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax3.plot(gens, sizes, "g-", lw=2, marker="o", ms=3)
        ax3.fill_between(gens, sizes, alpha=0.3, color="green")
        ax3.set_xlabel("Generation"); ax3.set_ylabel("Pareto Size")
        ax3.set_title(f"Pareto Size - {self.group_name}", fontweight="bold")
        ax3.grid(alpha=0.3); plt.tight_layout()
        if save:
            fig3.savefig(os.path.join(self.pareto_dir, "3_pareto_size.png"), dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig3)
        st, sr2 = [], []
        for r in self.history:
            if len(r["pareto"]) >= 2:
                ts_ = [p[0] for p in r["pareto"]]; rs_ = [p[1] for p in r["pareto"]]
                st.append((max(ts_) - min(ts_))/60); sr2.append(max(rs_) - min(rs_))
            else:
                st.append(0); sr2.append(0)
        fig4, ax4 = plt.subplots(figsize=(12, 10), dpi=PARETO_VIZ["dpi"])
        ax4b = ax4.twinx()
        ax4.plot(gens, st, "b-", lw=2, label="Time Spread")
        ax4b.plot(gens, sr2, "r-", lw=2, label="Risk Spread")
        ax4.set_xlabel("Generation"); ax4.set_ylabel("Time Spread (min)", color="b")
        ax4b.set_ylabel("Risk Spread", color="r")
        ax4.set_title(f"Pareto Spread - {self.group_name}", fontweight="bold")
        ax4.grid(alpha=0.3)
        lines = ax4.get_lines() + ax4b.get_lines()
        ax4.legend(lines, [l.get_label() for l in lines])
        plt.tight_layout()
        if save:
            fig4.savefig(os.path.join(self.pareto_dir, "4_pareto_spread.png"), dpi=PARETO_VIZ["dpi"], bbox_inches="tight")
        plt.close(fig4)


def plot_assignment_map(risk_array, bus_xy, assignment, res_df, active_indices, output_dir, filename="final_assignment_map.png"):
    ny, nx_ = risk_array.shape
    x_min = CENTER_UTM[0] - (nx_/2)*GRID_RES; x_max = CENTER_UTM[0] + (nx_/2)*GRID_RES
    y_max = CENTER_UTM[1] + (ny/2)*GRID_RES; y_min = CENTER_UTM[1] - (ny/2)*GRID_RES
    lon_min, lat_min = _to_wgs(x_min, y_min); lon_max, lat_max = _to_wgs(x_max, y_max)
    lon_c, lat_c = _to_wgs(*CENTER_UTM)
    fig, ax = plt.subplots(figsize=VIZ_CONFIG["figsize"], dpi=VIZ_CONFIG["dpi"])
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    if HAS_CTX:
        try: ctx.add_basemap(ax, crs="EPSG:4326", source=ctx.providers.Gaode.Normal, alpha=VIZ_CONFIG["basemap_alpha"])
        except Exception: pass
    risk_m = np.ma.masked_where(risk_array == 0, risk_array)
    cmap = mcolors.LinearSegmentedColormap.from_list("risk", VIZ_CONFIG["risk_colors"], N=100)
    cmap.set_bad(alpha=0)
    vmax = np.percentile(risk_array[risk_array > 0], 95) if np.any(risk_array > 0) else 1
    ax.imshow(risk_m, extent=[lon_min, lon_max, lat_min, lat_max], origin="upper", cmap=cmap, alpha=VIZ_CONFIG["risk_alpha"], vmin=0, vmax=vmax)
    used = set(assignment) - {-1}
    u_lon, u_lat, uu_lon, uu_lat = [], [], [], []
    for j, (bx, by) in enumerate(bus_xy):
        lo, la = _to_wgs(bx, by)
        if j in used: u_lon.append(lo); u_lat.append(la)
        else: uu_lon.append(lo); uu_lat.append(la)
    if uu_lon: ax.scatter(uu_lon, uu_lat, c="gray", s=20, alpha=0.3, label=f"Unused ({len(uu_lon)})")
    if u_lon: ax.scatter(u_lon, u_lat, c="lime", s=80, marker="^", edgecolor="darkgreen", lw=1.5, label=f"Used ({len(u_lon)})", zorder=8)
    ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*", edgecolor="red", lw=3, label="NPP", zorder=10)
    ax.legend(loc="lower left", fontsize=9, ncol=2); ax.set_title("Final Assignment Map", fontweight="bold")
    ax.grid(alpha=0.3, ls="--"); plt.tight_layout()
    fig.savefig(os.path.join(output_dir, filename), dpi=200, bbox_inches="tight"); plt.close(fig)
    print(f"Saved: {os.path.join(output_dir, filename)}")


def plot_evacuation_stages(risk_arrays, bus_xy, assignment, res_df, active_indices, paths, snapped_res, snapped_bus, output_dir, speed=WALK_SPEED):
    g2l = {i: il for il, i in enumerate(active_indices)}
    arrival = {}
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1: arrival[i] = np.inf; continue
        if i in g2l:
            pl = paths.get((g2l[i], j))
            arrival[i] = pl.length / speed if pl else np.inf
        else: arrival[i] = 0
    for stage_idx in range(len(risk_arrays)):
        stage_min = STAGE_TIMES[stage_idx]; ts = stage_min * 60
        ny, nx_ = risk_arrays[stage_idx].shape
        x_min = CENTER_UTM[0] - (nx_/2)*GRID_RES; x_max = CENTER_UTM[0] + (nx_/2)*GRID_RES
        y_max_val = CENTER_UTM[1] + (ny/2)*GRID_RES; y_min_val = CENTER_UTM[1] - (ny/2)*GRID_RES
        lon_min, lat_min = _to_wgs(x_min, y_min_val); lon_max, lat_max = _to_wgs(x_max, y_max_val)
        lon_c, lat_c = _to_wgs(*CENTER_UTM)
        fig, ax = plt.subplots(figsize=VIZ_CONFIG["figsize"], dpi=VIZ_CONFIG["dpi"])
        ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
        if HAS_CTX:
            try: ctx.add_basemap(ax, crs="EPSG:4326", source=ctx.providers.CartoDB.Positron, alpha=VIZ_CONFIG["basemap_alpha"])
            except Exception: pass
        ra = risk_arrays[stage_idx]; rm = np.ma.masked_where(ra == 0, ra)
        cmap = mcolors.LinearSegmentedColormap.from_list("r", VIZ_CONFIG["risk_colors"], N=100)
        cmap.set_bad(alpha=0)
        vmax = np.percentile(ra[ra > 0], 95) if np.any(ra > 0) else 1
        ax.imshow(rm, extent=[lon_min, lon_max, lat_min, lat_max], origin="upper", cmap=cmap, alpha=VIZ_CONFIG["risk_alpha"], vmin=0, vmax=vmax)
        arr_lon, arr_lat, mov_lon, mov_lat = [], [], [], []
        for i in range(len(res_df)):
            j = assignment[i]
            if j == -1: continue
            a = arrival.get(i, np.inf)
            if not np.isfinite(a): continue
            if ts >= a:
                lo, la = _to_wgs(snapped_bus[j, 0], snapped_bus[j, 1])
                arr_lon.append(lo); arr_lat.append(la)
            elif i in g2l:
                pl = paths.get((g2l[i], j))
                if pl and pl.length > 0:
                    frac = min(ts * speed / pl.length, 1.0)
                    pt = pl.interpolate(frac, normalized=True)
                    lo, la = _to_wgs(pt.x, pt.y)
                else: lo, la = _to_wgs(*snapped_bus[j])
                mov_lon.append(lo); mov_lat.append(la)
        if mov_lon: ax.scatter(mov_lon, mov_lat, c="blue", s=30, alpha=0.7, label=f"Moving ({len(mov_lon)})", zorder=9)
        if arr_lon: ax.scatter(arr_lon, arr_lat, c="green", s=40, marker="o", edgecolor="darkgreen", alpha=0.8, label=f"Arrived ({len(arr_lon)})", zorder=9)
        ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*", edgecolor="red", lw=3, label="NPP", zorder=11)
        ax.legend(loc="lower left", fontsize=9); ax.set_title(f"Evacuation at t = {stage_min} min", fontweight="bold")
        ax.grid(alpha=0.3, ls="--"); plt.tight_layout()
        p = os.path.join(output_dir, f"evacuation_stage_{stage_idx+1}_t{stage_min}min.png")
        fig.savefig(p, dpi=200, bbox_inches="tight"); plt.close(fig)
        print(f"Saved: {p}")


def plot_bus_animation(risk_arrays, bus_xy, shelter_xy, assignment,
                       res_df, active_indices, bus_trajectory,
                       output_dir, speed=WALK_SPEED,
                       fps=8, duration_sec=90,
                       depot_xy=None, road_graph=None,
                       road_paths=None, snapped_res=None,
                       snapped_bus=None):
    """
    生成完整疏散过程动画 (v7.0)，整合:
      1. 居民沿路网步行前往上车点
      2. 巴士沿路网转运居民 (depot->上车点->避难所->返回)
      3. 时变风险场 (4阶段切换)
    """
    if not bus_trajectory:
        print("No bus trajectory data, skipping animation")
        return

    g2l = {i: il for il, i in enumerate(active_indices)}

    # ── 提取实际使用的避难所 (从轨迹中) ──
    used_shelter_ids = set()
    for t in bus_trajectory:
        if t[3] == "shelter":
            used_shelter_ids.add(t[4])
    used_shelter_xy = shelter_xy[list(used_shelter_ids)] if used_shelter_ids else shelter_xy[:0]

    # 时间范围
    all_times = [t[5] for t in bus_trajectory] + [t[6] for t in bus_trajectory]
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1:
            continue
        if i in g2l:
            pl = road_paths.get((g2l[i], j)) if road_paths else None
            if pl and pl.length > 0:
                all_times.append(pl.length / speed)
    t_min_val = max(0, min(all_times) - 60)
    t_max_val = max(all_times) + 60
    t_range = t_max_val - t_min_val
    if t_range < 1:
        t_range = 1.0

    n_frames = fps * duration_sec
    time_per_frame = t_range / n_frames

    # 坐标范围 (只包含使用的避难所)
    all_x = list(bus_xy[:, 0]) + list(used_shelter_xy[:, 0]) + [CENTER_UTM[0]]
    all_y = list(bus_xy[:, 1]) + list(used_shelter_xy[:, 1]) + [CENTER_UTM[1]]
    if depot_xy is not None:
        all_x.append(depot_xy[0])
        all_y.append(depot_xy[1])
    if snapped_res is not None:
        all_x.extend(snapped_res[:, 0].tolist())
        all_y.extend(snapped_res[:, 1].tolist())
    x_min_utm, x_max_utm = min(all_x), max(all_x)
    y_min_utm, y_max_utm = min(all_y), max(all_y)
    margin = 0.08 * max(x_max_utm - x_min_utm, y_max_utm - y_min_utm)
    x_min_utm -= margin
    x_max_utm += margin
    y_min_utm -= margin
    y_max_utm += margin

    lon_min, lat_min = _to_wgs(x_min_utm, y_min_utm)
    lon_max, lat_max = _to_wgs(x_max_utm, y_max_utm)
    lon_c, lat_c = _to_wgs(*CENTER_UTM)

    # 居民位置
    def _resident_pos(res_idx, t_now):
        j = assignment[res_idx]
        if j == -1:
            return None
        rx, ry = res_df["x"].values[res_idx], res_df["y"].values[res_idx]
        pl = road_paths.get((g2l[res_idx], j)) if (res_idx in g2l and road_paths) else None
        if pl is None or pl.length == 0:
            return (*_to_wgs(rx, ry), "arrived")
        arr_t = pl.length / speed
        if t_now >= arr_t:
            return (*_to_wgs(snapped_bus[j, 0], snapped_bus[j, 1]), "arrived")
        frac = min(t_now * speed / pl.length, 1.0)
        pt = pl.interpolate(frac, normalized=True)
        return (*_to_wgs(pt.x, pt.y), "walking")

    # 巴士位置 (沿路网)
    bus_ids = sorted(set(t[0] for t in bus_trajectory))

    def _bus_pos(bus_idx, t_now):
        relevant = [t for t in bus_trajectory if t[0] == bus_idx and t[5] <= t_now]
        if not relevant:
            if depot_xy is not None:
                return (*_to_wgs(depot_xy[0], depot_xy[1]), "at_depot", 0)
            return None
        last = relevant[-1]
        _, ft, fi, tt, ti, dep, arr, load, pnodes = last
        if t_now >= arr:
            if tt == "shelter":
                return (*_to_wgs(shelter_xy[ti, 0], shelter_xy[ti, 1]), "at_shelter", load)
            else:
                return (*_to_wgs(bus_xy[ti, 0], bus_xy[ti, 1]), "at_stop", 0)
        frac = (t_now - dep) / max(arr - dep, 1e-6)
        frac = max(0.0, min(1.0, frac))
        if pnodes and len(pnodes) >= 2:
            # 判断 pnodes 是否为混合格式: [node_id, ..., (x, y)]
            # 最后一个元素可能是坐标元组 (避难所实际位置)
            is_mixed = isinstance(pnodes[-1], (tuple, list)) and len(pnodes[-1]) == 2

            if is_mixed and road_graph:
                # 混合路径: [node_id1, node_id2, ..., (x_final, y_final)]
                # 前部分是路网节点，最后是终点坐标
                node_part = pnodes[:-1]
                final_coord = pnodes[-1]

                # 计算总长度
                plen = 0.0
                for k in range(len(node_part) - 1):
                    u, v = node_part[k], node_part[k + 1]
                    if road_graph.has_edge(u, v):
                        plen += road_graph[u][v].get("length", 0.0)
                    elif road_graph.has_edge(v, u):
                        plen += road_graph[v][u].get("length", 0.0)
                    else:
                        plen += math.hypot(
                            road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"],
                            road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"])
                # 最后一段: 最后一个节点 → 终点坐标
                last_node = node_part[-1]
                nx_last = road_graph.nodes[last_node]["x"]
                ny_last = road_graph.nodes[last_node]["y"]
                last_seg = math.hypot(final_coord[0] - nx_last, final_coord[1] - ny_last)
                plen += last_seg

                if plen > 0:
                    target = frac * plen
                    accum = 0.0
                    # 遍历路网节点段
                    for k in range(len(node_part) - 1):
                        u, v = node_part[k], node_part[k + 1]
                        if road_graph.has_edge(u, v):
                            sl = road_graph[u][v].get("length", 0.0)
                        elif road_graph.has_edge(v, u):
                            sl = road_graph[v][u].get("length", 0.0)
                        else:
                            sl = math.hypot(
                                road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"],
                                road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"])
                        if accum + sl >= target:
                            sf = (target - accum) / max(sl, 1e-6)
                            x = road_graph.nodes[u]["x"] + sf * (road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"])
                            y = road_graph.nodes[u]["y"] + sf * (road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"])
                            st = "to_stop" if tt == "stop" else "to_shelter"
                            cl = load if tt == "shelter" else 0
                            return (*_to_wgs(x, y), st, cl)
                        accum += sl
                    # 最后一段
                    if accum + last_seg >= target:
                        sf = (target - accum) / max(last_seg, 1e-6)
                        x = nx_last + sf * (final_coord[0] - nx_last)
                        y = ny_last + sf * (final_coord[1] - ny_last)
                        st = "to_stop" if tt == "stop" else "to_shelter"
                        cl = load if tt == "shelter" else 0
                        return (*_to_wgs(x, y), st, cl)
            elif road_graph and not is_mixed:
                # pnodes 是纯路网节点 ID 序列
                plen = 0.0
                for k in range(len(pnodes) - 1):
                    u, v = pnodes[k], pnodes[k + 1]
                    if road_graph.has_edge(u, v):
                        plen += road_graph[u][v].get("length", 0.0)
                    elif road_graph.has_edge(v, u):
                        plen += road_graph[v][u].get("length", 0.0)
                    else:
                        plen += math.hypot(
                            road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"],
                            road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"]) * 1.5
                if plen > 0:
                    target = frac * plen
                    accum = 0.0
                    for k in range(len(pnodes) - 1):
                        u, v = pnodes[k], pnodes[k + 1]
                        if road_graph.has_edge(u, v):
                            sl = road_graph[u][v].get("length", 0.0)
                        elif road_graph.has_edge(v, u):
                            sl = road_graph[v][u].get("length", 0.0)
                        else:
                            sl = math.hypot(
                                road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"],
                                road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"]) * 1.5
                        if accum + sl >= target:
                            sf = (target - accum) / max(sl, 1e-6)
                            x = road_graph.nodes[u]["x"] + sf * (road_graph.nodes[v]["x"] - road_graph.nodes[u]["x"])
                            y = road_graph.nodes[u]["y"] + sf * (road_graph.nodes[v]["y"] - road_graph.nodes[u]["y"])
                            st = "to_stop" if tt == "stop" else "to_shelter"
                            cl = load if tt == "shelter" else 0
                            return (*_to_wgs(x, y), st, cl)
                        accum += sl
            elif not road_graph:
                # pnodes 是纯坐标序列 [(x0,y0), (x1,y1), ...]
                plen = 0.0
                for k in range(len(pnodes) - 1):
                    plen += math.hypot(pnodes[k + 1][0] - pnodes[k][0],
                                       pnodes[k + 1][1] - pnodes[k][1])
                if plen > 0:
                    target = frac * plen
                    accum = 0.0
                    for k in range(len(pnodes) - 1):
                        sl = math.hypot(pnodes[k + 1][0] - pnodes[k][0],
                                        pnodes[k + 1][1] - pnodes[k][1])
                        if accum + sl >= target:
                            sf = (target - accum) / max(sl, 1e-6)
                            x = pnodes[k][0] + sf * (pnodes[k + 1][0] - pnodes[k][0])
                            y = pnodes[k][1] + sf * (pnodes[k + 1][1] - pnodes[k][1])
                            st = "to_stop" if tt == "stop" else "to_shelter"
                            cl = load if tt == "shelter" else 0
                            return (*_to_wgs(x, y), st, cl)
                        accum += sl
        if ft == "shelter":
            x0, y0 = shelter_xy[fi]
        elif ft == "depot":
            x0, y0 = (depot_xy[0], depot_xy[1]) if depot_xy is not None else CENTER_UTM
        elif ft == "stop":
            x0, y0 = bus_xy[fi]
        else:
            x0, y0 = CENTER_UTM
        if tt == "shelter":
            x1, y1 = shelter_xy[ti]
        elif tt == "stop":
            x1, y1 = bus_xy[ti]
        else:
            x1, y1 = CENTER_UTM
        x = x0 + frac * (x1 - x0)
        y = y0 + frac * (y1 - y0)
        st = "to_stop" if tt == "stop" else "to_shelter"
        cl = load if tt == "shelter" else 0
        return (*_to_wgs(x, y), st, cl)

    state_colors = {
        "at_depot": "#9C27B0", "at_shelter": "#2196F3",
        "to_stop": "#FF9800", "at_stop": "#4CAF50",
        "to_shelter": "#F44336",
    }
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="^", color="w", markerfacecolor="lime",
               markeredgecolor="darkgreen", ms=10, label="Pickup point"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="cyan",
               markeredgecolor="darkblue", ms=10, label=f"Shelter (used: {len(used_shelter_ids)})"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="#9C27B0",
               markeredgecolor="white", ms=10, label="Bus Depot"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="yellow",
               markeredgecolor="red", ms=14, label="NPP"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#FF5722",
               ms=6, label="Resident walking"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#4CAF50",
               ms=6, label="Resident arrived"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#FF9800",
               ms=8, label="Bus to pickup"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#F44336",
               ms=8, label="Bus to shelter"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor="#2196F3",
               ms=8, label="Bus @ shelter"),
    ]

    fig, ax = plt.subplots(figsize=(14, 12), dpi=VIZ_CONFIG["dpi"])
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    if HAS_CTX:
        try:
            ctx.add_basemap(ax, crs="EPSG:4326", source=ctx.providers.CartoDB.Positron, alpha=VIZ_CONFIG["basemap_alpha"])
        except Exception:
            pass
    for j in range(len(bus_xy)):
        lo, la = _to_wgs(bus_xy[j, 0], bus_xy[j, 1])
        ax.plot(lo, la, "^", color="lime", ms=8, mec="darkgreen", mew=1, zorder=8)
    # 只显示使用的避难所
    for si in used_shelter_ids:
        lo, la = _to_wgs(shelter_xy[si, 0], shelter_xy[si, 1])
        ax.plot(lo, la, "s", color="cyan", ms=10, mec="darkblue", mew=1.5, zorder=8)
    ax.scatter(lon_c, lat_c, c="yellow", s=500, marker="*", edgecolor="red", lw=3, zorder=10)
    if depot_xy is not None:
        d_lon, d_lat = _to_wgs(depot_xy[0], depot_xy[1])
        ax.plot(d_lon, d_lat, "D", color="#9C27B0", ms=12, mec="white", mew=2, zorder=9)
    ax.legend(handles=legend_elements, loc="lower left", fontsize=8, ncol=2)
    time_text = ax.text(0.02, 0.98, "", transform=ax.transAxes, fontsize=12, fontweight="bold", va="top", bbox=dict(boxstyle="round", fc="white", alpha=0.8))
    bus_scatter = ax.scatter([], [], marker="s", s=60, zorder=12, edgecolors="black", linewidths=0.5)
    res_scatter = ax.scatter([], [], marker="o", s=20, zorder=11, alpha=0.7)

    def _init():
        bus_scatter.set_offsets(np.empty((0, 2)))
        res_scatter.set_offsets(np.empty((0, 2)))
        time_text.set_text("")
        return [bus_scatter, res_scatter, time_text]

    def _update(frame):
        t_now = t_min_val + frame * time_per_frame
        t_min_disp = t_now / 60.0
        bp, bc = [], []
        for bi in bus_ids:
            r = _bus_pos(bi, t_now)
            if r is None:
                continue
            bp.append([r[0], r[1]])
            bc.append(state_colors.get(r[2], "gray"))
        if bp:
            bus_scatter.set_offsets(np.array(bp))
            bus_scatter.set_facecolors(bc)
        else:
            bus_scatter.set_offsets(np.empty((0, 2)))
        rp, rc = [], []
        for i in range(len(res_df)):
            r = _resident_pos(i, t_now)
            if r is None:
                continue
            rp.append([r[0], r[1]])
            rc.append("#FF5722" if r[2] == "walking" else "#4CAF50")
        if rp:
            res_scatter.set_offsets(np.array(rp))
            res_scatter.set_facecolors(rc)
        else:
            res_scatter.set_offsets(np.empty((0, 2)))
        time_text.set_text(f"t = {t_min_disp:.1f} min")
        return [bus_scatter, res_scatter, time_text]

    anim = animation.FuncAnimation(fig, _update, init_func=_init, frames=n_frames, interval=1000 / fps, blit=True)
    ax.set_title("Complete Evacuation Animation", fontweight="bold")
    ax.grid(alpha=0.3, ls="--")
    gif_path = os.path.join(output_dir, "bus_evacuation_animation.gif")
    try:
        anim.save(gif_path, writer="pillow", fps=fps, dpi=100)
        print(f"Saved: {gif_path}")
    except Exception as e:
        print(f"Failed to save GIF: {e}")
        mp4_path = os.path.join(output_dir, "bus_evacuation_animation.mp4")
        try:
            anim.save(mp4_path, writer="ffmpeg", fps=fps, dpi=100)
            print(f"Saved: {mp4_path}")
        except Exception as e2:
            print(f"Failed to save MP4: {e2}")
    plt.close(fig)

    # 静态快照
    for snap_min in [15, 30, 45]:
        t_snap = snap_min * 60.0
        fig2, ax2 = plt.subplots(figsize=VIZ_CONFIG["figsize"], dpi=VIZ_CONFIG["dpi"])
        ax2.set_xlim(lon_min, lon_max)
        ax2.set_ylim(lat_min, lat_max)
        if HAS_CTX:
            try:
                ctx.add_basemap(ax2, crs="EPSG:4326", source=ctx.providers.CartoDB.Positron, alpha=VIZ_CONFIG["basemap_alpha"])
            except Exception:
                pass
        si = 0 if snap_min <= 15 else (1 if snap_min <= 30 else 2)
        ra = risk_arrays[si]
        ny2, nx2 = ra.shape
        ra_xmin = CENTER_UTM[0] - (nx2/2)*GRID_RES
        ra_xmax = CENTER_UTM[0] + (nx2/2)*GRID_RES
        ra_ymax = CENTER_UTM[1] + (ny2/2)*GRID_RES
        ra_ymin = CENTER_UTM[1] - (ny2/2)*GRID_RES
        ra_lomin, ra_lamin = _to_wgs(ra_xmin, ra_ymin)
        ra_lomax, ra_lamax = _to_wgs(ra_xmax, ra_ymax)
        rm = np.ma.masked_where(ra == 0, ra)
        cmap = mcolors.LinearSegmentedColormap.from_list("r", VIZ_CONFIG["risk_colors"], N=100)
        cmap.set_bad(alpha=0)
        vmax = np.percentile(ra[ra > 0], 95) if np.any(ra > 0) else 1
        ax2.imshow(rm, extent=[ra_lomin, ra_lomax, ra_lamin, ra_lamax], origin="upper", cmap=cmap, alpha=VIZ_CONFIG["risk_alpha"], vmin=0, vmax=vmax)
        for j in range(len(bus_xy)):
            lo, la = _to_wgs(bus_xy[j, 0], bus_xy[j, 1])
            ax2.plot(lo, la, "^", color="lime", ms=8, mec="darkgreen", mew=1, zorder=8)
        for si2 in used_shelter_ids:
            lo, la = _to_wgs(shelter_xy[si2, 0], shelter_xy[si2, 1])
            ax2.plot(lo, la, "s", color="cyan", ms=10, mec="darkblue", mew=1.5, zorder=8)
        ax2.scatter(lon_c, lat_c, c="yellow", s=500, marker="*", edgecolor="red", lw=3, zorder=10)
        if depot_xy is not None:
            d_lon, d_lat = _to_wgs(depot_xy[0], depot_xy[1])
            ax2.plot(d_lon, d_lat, "D", color="#9C27B0", ms=12, mec="white", mew=2, zorder=9)
        bp, bc = [], []
        for bi in bus_ids:
            r = _bus_pos(bi, t_snap)
            if r is None:
                continue
            bp.append([r[0], r[1]])
            bc.append(state_colors.get(r[2], "gray"))
        if bp:
            ax2.scatter([p[0] for p in bp], [p[1] for p in bp], c=bc, marker="s", s=60, edgecolors="black", linewidths=0.5, zorder=12)
        rp, rc = [], []
        for i in range(len(res_df)):
            r = _resident_pos(i, t_snap)
            if r is None:
                continue
            rp.append([r[0], r[1]])
            rc.append("#FF5722" if r[2] == "walking" else "#4CAF50")
        if rp:
            ax2.scatter([p[0] for p in rp], [p[1] for p in rp], c=rc, marker="o", s=20, alpha=0.7, zorder=11)
        ax2.legend(handles=legend_elements, loc="lower left", fontsize=8, ncol=2)
        ax2.set_title(f"Evacuation at t = {snap_min} min", fontweight="bold")
        ax2.grid(alpha=0.3, ls="--")
        plt.tight_layout()
        snap_path = os.path.join(output_dir, f"bus_snapshot_t{snap_min}min.png")
        fig2.savefig(snap_path, dpi=200, bbox_inches="tight")
        plt.close(fig2)
        print(f"Saved: {snap_path}")
