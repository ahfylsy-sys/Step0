"""
数据加载与路网预处理模块
- 居民/上车点/风险场数据读取
- 路网图构建与 Dijkstra 路径预计算
- 可行域构建（严格路网模式：无直线备选）
"""
import os
import math
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
    SHELTER_CONFIG, PICKUP_RISK_FILTER, RISK_STAGE_TIMES,
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
            import re as _re
            val = row.get(width_field)
            if _pd.notna(val):
                # 处理 "1 m", "1.5m" 等字符串格式
                if isinstance(val, str):
                    m = _re.search(r'([\d.]+)', val)
                    if m:
                        try:
                            w = float(m.group(1))
                            if w > 0:
                                width = w
                        except ValueError:
                            pass
                elif float(val) > 0:
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
def build_feasible(road_paths, n_active, bus_list, max_time_sec, speed,
                   bus_xy=None, risk_arrays=None, x_mins=None, y_maxs=None,
                   depot_xy=None, bus_speed_ms=None, dispatch_delay_sec=None):
    """
    基于已有道路路径和时间约束构建每个居民的可行上车点列表。

    约束条件:
        1. 步行时间 ≤ max_time_sec
        2. 巴士能在上车点关闭前到达 (风险硬约束)
           巴士到达时间 = dispatch_delay + depot→stop行驶时间
           上车点关闭时间 = 首次被风险覆盖的阶段时间

    参数:
        road_paths         – {(i_local, j): LineString} 预计算路径
        n_active           – int, 活跃居民数
        bus_list           – list[int], 可用上车点索引
        max_time_sec       – float, 最大步行时间 (秒)
        speed              – float, 步行速度 (m/s)
        bus_xy             – (n_bus, 2) 上车点坐标 (UTM), 用于计算巴士可达性
        risk_arrays        – list of ndarray, 风险矩阵 (用于计算关闭时间)
        x_mins, y_maxs     – 风险矩阵空间参照
        depot_xy           – (2,) 巴士总站坐标 (UTM)
        bus_speed_ms       – float, 巴士速度 (m/s)
        dispatch_delay_sec – float, 调度延迟 (秒)
    """
    # ── 计算各上车点的关闭时间 ──
    closure_times = None
    if (bus_xy is not None and risk_arrays is not None
            and x_mins is not None and y_maxs is not None):
        closure_times = _compute_stop_closure_times(
            bus_xy, risk_arrays, x_mins, y_maxs)

    # ── 计算巴士从 depot 到各上车点的到达时间 ──
    bus_arrival_times = None
    if (depot_xy is not None and bus_xy is not None
            and bus_speed_ms is not None and dispatch_delay_sec is not None):
        factor = 1.3  # Euclidean → 道路距离修正
        bus_arrival_times = {}
        for j in bus_list:
            dx = bus_xy[j, 0] - depot_xy[0]
            dy = bus_xy[j, 1] - depot_xy[1]
            dist = math.hypot(dx, dy) * factor
            bus_arrival_times[j] = dispatch_delay_sec + dist / bus_speed_ms

    feasible = [[] for _ in range(n_active)]
    for il in range(n_active):
        for j in bus_list:
            pl = road_paths.get((il, j))
            if pl is None:
                continue
            walk_t = pl.length / speed
            if walk_t > max_time_sec:
                continue

            # 硬约束: 巴士必须在上车点关闭前到达
            if closure_times is not None and bus_arrival_times is not None:
                t_close = closure_times.get(j, float('inf'))
                t_bus_arrive = bus_arrival_times.get(j, float('inf'))
                if t_bus_arrive >= t_close:
                    continue  # 巴士到达时上车点已关闭 → 排除

            feasible[il].append(j)

    no_opt = sum(1 for f in feasible if len(f) == 0)
    n_excluded = sum(1 for il in range(n_active)
                     if len(feasible[il]) < len(bus_list))
    print(f"   Feasible: {n_active - no_opt}/{n_active} residents have reachable stops")
    if n_excluded > 0:
        print(f"   🛡️  Bus-risk constraint: {n_excluded} residents had stops excluded "
              f"(bus arrives after risk closure)")
    return feasible, no_opt


def _compute_stop_closure_times(bus_xy, risk_arrays, x_mins, y_maxs,
                                grid_res=GRID_RES):
    """
    计算每个上车点被风险场覆盖的关闭时间。

    遍历各阶段风险矩阵 (RISK_STAGE_TIMES), 首次超过阈值即关闭。

    返回:
        closure_times – {j: closure_time_sec}, 未被覆盖的上车点不包含在返回中
    """
    threshold = PICKUP_RISK_FILTER.get("risk_threshold", 0.0)
    stage_times_sec = [t * 60 for t in RISK_STAGE_TIMES]

    closure_times = {}
    for j in range(len(bus_xy)):
        bx, by = bus_xy[j]
        for si, t_stage in enumerate(stage_times_sec):
            ra = risk_arrays[si]
            xm = x_mins[si]
            ym = y_maxs[si]
            col = int((bx - xm) / grid_res)
            row = int((ym - by) / grid_res)
            if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
                risk_val = float(ra[row, col])
                if risk_val > threshold:
                    closure_times[j] = float(t_stage)
                    break
    return closure_times


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


# ============================================================
#  避难所数据
# ============================================================
def generate_shelters(center=CENTER_UTM, total_pop=None,
                      radius_m=None, capacity_per_shelter=None):
    """
    在核电厂中心 30 km 外围均匀生成避难所。

    避难所数量由总人口和单所容量自动确定:
        n_shelters = ceil(total_pop / capacity_per_shelter)
    若 total_pop 未提供则默认 8 个。

    参数:
        center               – (2,) 核电厂中心 UTM 坐标
        total_pop            – 需要安置的总人口数 (决定避难所数量)
        radius_m             – 避难所距中心半径 (m, 默认 30 km)
        capacity_per_shelter – 每个避难所容量 (人)

    返回:
        shelter_xy         – (n_shelters, 2) ndarray, UTM 坐标
        shelter_capacities – (n_shelters,) ndarray, 容量
    """
    cfg = SHELTER_CONFIG
    radius_m = radius_m or cfg["radius_m"]
    capacity_per_shelter = capacity_per_shelter or cfg["capacity_per_shelter"]

    # 按容量需求自动确定避难所数量
    if total_pop is not None and total_pop > 0:
        n_shelters = max(1, math.ceil(total_pop / capacity_per_shelter))
    else:
        n_shelters = 8  # 默认值

    angles = np.linspace(0, 2 * np.pi, n_shelters, endpoint=False)
    cx, cy = center[0], center[1]
    shelter_xy = np.array([
        [cx + radius_m * np.cos(a), cy + radius_m * np.sin(a)]
        for a in angles
    ], dtype=np.float64)
    shelter_capacities = np.full(n_shelters, capacity_per_shelter,
                                 dtype=np.float64)

    print(f"   🏠 Generated {n_shelters} shelters at {radius_m/1000:.0f} km "
          f"radius, capacity={capacity_per_shelter}/each "
          f"(total_pop={total_pop or 'N/A'})")
    return shelter_xy, shelter_capacities


def load_shelters(shelter_file=None, center=CENTER_UTM, total_pop=None,
                  target_crs=CRS_UTM):
    """
    加载避难所数据。若提供 Excel 文件则从中读取, 否则自动生成。

    Excel 文件格式 (Shelter_with_coords.xlsx):
        列 [Number, lon, lat, Capacity], lon/lat 为 WGS84 经纬度。
    旧格式兼容: 列 [name, x, y, capacity], x/y 为 UTM 坐标。

    **重要**: 只保留距核电厂 ≥ 30 km 的避难所 (长期避难所)。

    参数:
        shelter_file – 可选避难所 Excel 路径
        center       – 核电厂中心 UTM 坐标
        total_pop    – 需要安置的总人口数 (自动生成时用于确定避难所数量)
        target_crs   – 目标坐标系 (默认 UTM-50N)

    返回:
        shelter_xy         – (n_shelters, 2) ndarray, UTM 坐标
        shelter_capacities – (n_shelters,) ndarray
    """
    cfg = SHELTER_CONFIG
    shelter_file = shelter_file or cfg.get("shelter_file")
    min_dist = cfg.get("radius_m", 30_000)  # 最小距离 30 km

    if shelter_file and os.path.exists(shelter_file):
        df = pd.read_excel(shelter_file)

        # 检测格式: 有 lon/lat → WGS84 需要投影; 有 x/y → 已经是 UTM
        if "lon" in df.columns and "lat" in df.columns:
            # WGS84 → UTM 投影
            gdf = gpd.GeoDataFrame(
                df, geometry=gpd.points_from_xy(df["lon"], df["lat"]),
                crs="EPSG:4326"
            ).to_crs(target_crs)
            xy = np.array([(p.x, p.y) for p in gdf.geometry], dtype=np.float64)
            cap_col = "Capacity" if "Capacity" in df.columns else "capacity"
            caps = (df[cap_col].values.astype(np.float64)
                    if cap_col in df.columns
                    else np.full(len(df), cfg["capacity_per_shelter"],
                                 dtype=np.float64))
        elif "x" in df.columns and "y" in df.columns:
            # 已经是 UTM 坐标
            xy = df[["x", "y"]].values.astype(np.float64)
            caps = (df["capacity"].values.astype(np.float64)
                    if "capacity" in df.columns
                    else np.full(len(df), cfg["capacity_per_shelter"],
                                 dtype=np.float64))
        else:
            print(f"   ⚠️  Shelter file {shelter_file} has unrecognized format, "
                  f"falling back to auto-generation")
            return generate_shelters(center, total_pop=total_pop)

        # ── 过滤: 只保留距核电厂 ≥ 30 km 的避难所 ──
        cx, cy = center
        dists = np.sqrt((xy[:, 0] - cx) ** 2 + (xy[:, 1] - cy) ** 2)
        mask = dists >= min_dist
        n_before = len(xy)
        xy = xy[mask]
        caps = caps[mask]
        n_after = len(xy)
        print(f"   🏠 Loaded {n_after}/{n_before} shelters from {shelter_file} "
              f"(filtered: only ≥ {min_dist/1000:.0f} km from NPP)")

        if n_after == 0:
            print(f"   ⚠️  No shelters ≥ {min_dist/1000:.0f} km, "
                  f"falling back to auto-generation")
            return generate_shelters(center, total_pop=total_pop)

        return xy, caps

    return generate_shelters(center, total_pop=total_pop)


# ============================================================
#  上车点风险过滤
# ============================================================
def filter_stops_by_risk(bus_xy, risk_arrays, x_mins, y_maxs,
                         risk_threshold=None, grid_res=GRID_RES):
    """
    过滤在最早风险阶段 (t=15min) 已被覆盖的上车点。
    这些上车点距核电厂过近，不适合作为疏散集合点。

    参数:
        bus_xy        – (n_stops, 2) 上车点坐标 (UTM)
        risk_arrays   – list of 4 ndarray, 4 阶段风险矩阵
        x_mins        – 各阶段 x_min
        y_maxs        – 各阶段 y_max
        risk_threshold – 风险阈值, 超过则排除 (默认从 PICKUP_RISK_FILTER 读取)
        grid_res      – 风险场网格分辨率 (m)

    返回:
        safe_indices – list[int], 安全上车点的索引列表
    """
    cfg = PICKUP_RISK_FILTER
    if not cfg.get("enabled", True):
        return list(range(len(bus_xy)))

    threshold = risk_threshold if risk_threshold is not None else cfg.get("risk_threshold", 0.0)

    # 只检查第一阶段 (t=15min) 的风险
    ra = risk_arrays[0]
    xm = x_mins[0]
    ym = y_maxs[0]

    safe_indices = []
    for j in range(len(bus_xy)):
        bx, by = bus_xy[j]
        col = int((bx - xm) / grid_res)
        row = int((ym - by) / grid_res)
        if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
            risk_val = float(ra[row, col])
            if risk_val > threshold:
                continue  # 排除: 风险值超过阈值
        safe_indices.append(j)

    n_excluded = len(bus_xy) - len(safe_indices)
    print(f"   🛡️  Risk filter: {len(safe_indices)}/{len(bus_xy)} stops safe "
          f"({n_excluded} excluded at t≤15min, threshold={threshold})")
    return safe_indices
