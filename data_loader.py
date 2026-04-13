"""
数据加载与路网预处理模块
- 居民/上车点/风险场数据读取
- 路网图构建与 Dijkstra 路径预计算
- 可行域构建（严格路网模式：无直线备选）
"""
import os
import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from scipy.spatial import KDTree
from shapely.geometry import LineString

from config import (
    CENTER_UTM, GRID_RES, CRS_UTM,
    DATA_ROOT, BUS_FILE, AGE_GROUPS, GENDERS, GENDER_SHORT,
    WALK_SPEEDS, OUTPUT_ROOT, ROAD_CONGESTION_CONFIG,
)


# ============================================================
#  数据读取
# ============================================================
def load_resident_data(pop_file: str) -> pd.DataFrame:
    """读取居民 CSV → DataFrame(id, x, y, pop)"""
    df = pd.read_csv(pop_file)
    df.columns = ["id", "x", "y", "pop"]
    return df


def load_bus_stops(bus_file: str, target_crs: str = CRS_UTM):
    """读取上车点 Excel → (xy_array, GeoDataFrame)"""
    bus = pd.read_excel(bus_file)
    gdf = gpd.GeoDataFrame(
        bus, geometry=gpd.points_from_xy(bus["lon"], bus["lat"]), crs="EPSG:4326"
    ).to_crs(target_crs)
    xy = np.array([(p.x, p.y) for p in gdf.geometry])
    return xy, gdf


def load_all_risk_data(risk_files, center=CENTER_UTM, res=GRID_RES):
    """加载 4 个阶段的风险矩阵及其空间参照"""
    arrays, x_mins, y_maxs = [], [], []
    for f in risk_files:
        arr = pd.read_excel(f, header=None).values.astype(np.float64)
        ny, nx = arr.shape
        arrays.append(arr)
        x_mins.append(center[0] - (nx / 2) * res)
        y_maxs.append(center[1] + (ny / 2) * res)
    return arrays, x_mins, y_maxs


def build_group_configs():
    """构建 12 个年龄×性别分组的配置字典列表"""
    configs, idx = [], 0
    for gender in GENDERS:
        g = GENDER_SHORT[gender]
        for age in AGE_GROUPS:
            name = f"{g}_{age}"
            pop_f = os.path.join(DATA_ROOT, "pop_data", "clipped_pop_gender_age_csv",
                                 gender, "all_age_clustering", f"{name}_cluster.csv")
            feas_f = os.path.join(DATA_ROOT, "feasible_domain", f"feasible_{name}.pkl")
            if os.path.exists(pop_f) and os.path.exists(feas_f):
                configs.append(dict(
                    group_name=name, gender=gender, age_group=age,
                    pop_file=pop_f, output_dir=os.path.join(OUTPUT_ROOT, g, age),
                    speed=WALK_SPEEDS[idx], bus_file=BUS_FILE,
                    selection_method="min_time",
                ))
            else:
                print(f"⚠️  Skipping {name}: data files not found")
            idx += 1
    return configs


# ============================================================
#  路网构建
# ============================================================
def load_road_network(shp_path, crs=CRS_UTM, center=None, clip_radius=None):
    """
    加载 Shapefile 路网 → NetworkX 无向图 + KDTree 空间索引

    返回:
        G            – NetworkX 图 (边权 = length)
        node_ids     – 节点 ID 数组
        node_coords  – 节点坐标 (N,2)
        kdtree       – 节点 KDTree
    """
    print("Loading road network ...")
    roads = gpd.read_file(shp_path).to_crs(crs)

    if center is not None and clip_radius is not None:
        from shapely.geometry import Point as ShapelyPoint
        buf = ShapelyPoint(*center).buffer(clip_radius)
        roads = roads[roads.intersects(buf)].copy()
        roads["geometry"] = roads.geometry.intersection(buf)
        print(f"   Clipped to {clip_radius/1000:.0f} km: {len(roads)} segments")

    roads = roads.explode(ignore_index=True)
    roads = roads[roads.geometry.type == "LineString"]
    if roads.empty:
        raise ValueError("Road network contains no valid LineStrings after clipping.")

    # ── 道路宽度识别 (拥挤度建模) ──
    ccfg = ROAD_CONGESTION_CONFIG
    width_field = None
    if ccfg["enabled"]:
        for field in ccfg["width_fields"]:
            if field in roads.columns:
                width_field = field
                break
        if width_field:
            print(f"   📏 Road width: using field '{width_field}'")
        else:
            print(f"   📏 Road width: no field found, using default {ccfg['default_width_m']}m")

    G = nx.Graph()
    c2n, nc = {}, 0

    def _node(x, y, tol=1e-3):
        nonlocal nc
        k = (round(x / tol) * tol, round(y / tol) * tol)
        if k not in c2n:
            c2n[k] = nc
            G.add_node(nc, x=k[0], y=k[1])
            nc += 1
        return c2n[k]

    for _, row in roads.iterrows():
        cs = list(row.geometry.coords)
        # 获取宽度
        width = ccfg["default_width_m"]
        if width_field is not None:
            import pandas as _pd
            val = row.get(width_field)
            if _pd.notna(val) and float(val) > 0:
                width = float(val)
        eff_width = width * ccfg["effective_width_ratio"]
        capacity_ppm = eff_width * ccfg["ped_flow_rate_ppm"]
        for i in range(len(cs) - 1):
            u = _node(*cs[i])
            v = _node(*cs[i + 1])
            d = np.hypot(cs[i + 1][0] - cs[i][0], cs[i + 1][1] - cs[i][1])
            if not G.has_edge(u, v):
                G.add_edge(u, v, length=d, width=width, capacity_ppm=capacity_ppm)

    coords = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in G.nodes()])
    nids = np.array(list(G.nodes()))
    print(f"✅ Road graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G, nids, coords, KDTree(coords)


# ============================================================
#  路径预计算（严格模式）
# ============================================================
def precompute_paths(res_df, bus_xy, G, active_idx, bus_list,
                     kdtree, nids, ncoords):
    """
    Dijkstra 预计算所有 (居民, 上车点) 的道路网络路径。
    无道路连通的对标记为无效，不使用直线备选。

    返回:
        paths       – {(i_local, j): LineString}
        snapped_res – 吸附后居民坐标 (N_active, 2)
        snapped_bus – 吸附后上车点坐标 (N_bus, 2)
        validity    – {(i_local, j): bool}
    """
    # --- 吸附上车点 ---
    bus_c = bus_xy[bus_list]
    bd, bi = kdtree.query(bus_c)          # KDTree.query → (distances, indices)
    snapped_bus = bus_xy.copy().astype(float)
    for i, j in enumerate(bus_list):
        snapped_bus[j] = ncoords[bi[i]]
    b2n = {j: int(nids[bi[i]]) for i, j in enumerate(bus_list)}

    # --- 吸附居民 ---
    rc = res_df[["x", "y"]].values[active_idx]
    rd, ri = kdtree.query(rc)             # KDTree.query → (distances, indices)
    snapped_res = ncoords[ri]
    rnids = nids[ri]

    print(f"🔗 Snap  res: avg={np.mean(rd):.1f}m  max={np.max(rd):.1f}m")
    print(f"🔗 Snap  bus: avg={np.mean(bd):.1f}m  max={np.max(bd):.1f}m")

    # --- Dijkstra 单源最短路缓存 ---
    cache = {}
    for il in range(len(active_idx)):
        rn = int(rnids[il])
        if rn not in cache:
            try:
                cache[rn] = nx.single_source_dijkstra_path(G, rn, weight="length")
            except nx.NetworkXError:
                cache[rn] = {}

    # --- 构建路径 ---
    paths, validity = {}, {}
    path_node_seqs = {}
    ok, fail = 0, 0
    for il in range(len(active_idx)):
        rn = int(rnids[il])
        pd_ = cache.get(rn, {})
        for j in bus_list:
            bn = b2n[j]
            if rn == bn:
                x, y = snapped_res[il]
                paths[(il, j)] = LineString([(x, y), (x + 0.001, y + 0.001)])
                path_node_seqs[(il, j)] = [rn]
                validity[(il, j)] = True
                ok += 1
                continue
            if bn in pd_:
                node_seq = pd_[bn]
                cs = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_seq]
                if len(cs) >= 2:
                    paths[(il, j)] = LineString(cs)
                    path_node_seqs[(il, j)] = list(node_seq)
                    validity[(il, j)] = True
                    ok += 1
                    continue
            validity[(il, j)] = False
            fail += 1

    total = len(active_idx) * len(bus_list)
    print(f"   Paths: {ok}/{total} valid ({ok/total*100:.1f}%), {fail} no path")
    return paths, snapped_res, snapped_bus, validity, path_node_seqs


# ============================================================
#  可行域构建
# ============================================================
def build_feasible(road_paths, n_active, bus_list, max_time_sec, speed):
    """
    基于已有道路路径和时间约束构建每个居民的可行上车点列表。
    不使用任何硬编码过滤规则，超时由目标函数自然惩罚。
    """
    feasible = [[] for _ in range(n_active)]
    for il in range(n_active):
        for j in bus_list:
            pl = road_paths.get((il, j))
            if pl is not None and pl.length / speed <= max_time_sec:
                feasible[il].append(j)
    no_opt = sum(1 for f in feasible if len(f) == 0)
    print(f"   Feasible: {n_active - no_opt}/{n_active} residents have reachable stops")
    return feasible, no_opt


# ============================================================
#  道路拥挤度数据构建 (v5.7)
# ============================================================
def build_congestion_data(path_node_seqs, G, speed, max_time_sec):
    """从预计算路径节点序列构建拥挤度评估数据。"""
    ccfg = ROAD_CONGESTION_CONFIG
    evac_duration_min = max_time_sec / 60.0
    edge_paths, edge_capacities, edge_lengths, edge_widths = {}, {}, {}, {}

    for (il, j), node_seq in path_node_seqs.items():
        edges = []
        for k in range(len(node_seq) - 1):
            u, v = node_seq[k], node_seq[k + 1]
            edge_key = (min(u, v), max(u, v))
            edges.append(edge_key)
            if edge_key not in edge_capacities:
                edata = G.edges[u, v] if G.has_edge(u, v) else (
                    G.edges[v, u] if G.has_edge(v, u) else {})
                length = edata.get('length', 0.0)
                width = edata.get('width', ccfg['default_width_m'])
                cap_ppm = edata.get('capacity_ppm',
                                    width * ccfg['effective_width_ratio']
                                    * ccfg['ped_flow_rate_ppm'])
                edge_capacities[edge_key] = cap_ppm * evac_duration_min
                edge_lengths[edge_key] = length
                edge_widths[edge_key] = width
        edge_paths[(il, j)] = edges

    print(f"   🚦 Congestion data: {len(edge_capacities)} unique edges")
    return dict(edge_paths=edge_paths, edge_capacities=edge_capacities,
                edge_lengths=edge_lengths, edge_widths=edge_widths)
