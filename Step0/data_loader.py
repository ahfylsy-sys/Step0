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
    WALK_SPEEDS, OUTPUT_ROOT, ROAD_HIERARCHY_CONFIG,
    ROAD_CONGESTION_CONFIG,
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


def load_shelters(shelter_file: str, target_crs: str = CRS_UTM):
    """
    读取避难所 Excel → (xy_array, capacity_array, GeoDataFrame)

    Excel 要求列: Number, lon, lat, Capacity

    参数:
        shelter_file – 避难所 Excel 路径
        target_crs   – 目标投影坐标系 (默认 UTM-50N)

    返回:
        xy       – (M, 2) 避难所 UTM 坐标
        capacity – (M,)   避难所容量 (人)
        gdf      – GeoDataFrame (用于可视化)
    """
    df = pd.read_excel(shelter_file)
    # 列名检查
    required = {"lon", "lat", "Capacity"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Shelter file missing columns: {missing}. "
                         f"Got: {list(df.columns)}")
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326"
    ).to_crs(target_crs)
    xy = np.array([(p.x, p.y) for p in gdf.geometry], dtype=np.float64)
    cap = df["Capacity"].values.astype(np.float64)
    print(f"✅ Shelters loaded: {len(xy)} sites, "
          f"total capacity={int(cap.sum()):,} persons, "
          f"max={int(cap.max())}, min={int(cap.min())}")
    return xy, cap, gdf


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
                    selection_method="min_risk",
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

    支持道路等级优先: 读取道路分类字段, 高级别道路(主干道)获得更低的
    成本乘数, 使 Dijkstra 优先选择主干道进行疏散。

    边权重:
        - length: 欧氏长度 (m), 用于距离计算和可视化
        - weight: length × cost_multiplier, 用于 Dijkstra 路径规划
                  主干道乘数小 → 更容易被选中

    返回:
        G            – NetworkX 图 (边属性含 length 和 weight)
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

    # ── 道路等级识别 ──
    hcfg = ROAD_HIERARCHY_CONFIG
    road_class_field = None
    if hcfg["enabled"]:
        for field in hcfg["class_fields"]:
            if field in roads.columns:
                road_class_field = field
                break
        if road_class_field:
            print(f"   🛣️  Road hierarchy: using field '{road_class_field}'")
        else:
            print(f"   ⚠️  Road hierarchy: no class field found "
                  f"(tried {hcfg['class_fields']}), using uniform cost")

    def _get_multiplier(class_value):
        """根据道路等级值获取成本乘数"""
        if not hcfg["enabled"] or road_class_field is None:
            return 1.0
        cv = str(class_value).lower().strip()
        for key, mult in hcfg["cost_multipliers"].items():
            if cv.startswith(key):
                return mult
        return hcfg["default_multiplier"]

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
            print(f"   📏 Road width: no field found, using defaults by road class")

    def _get_width(row):
        """获取道路宽度 (m)：优先用 Shapefile 字段，否则按等级赋默认值"""
        if width_field is not None:
            import pandas as _pd
            val = row.get(width_field)
            if _pd.notna(val):
                w = float(val)
                if w > 0:
                    return w
        # 按道路等级赋默认宽度
        if road_class_field is not None:
            cv = str(row.get(road_class_field, "")).lower().strip()
            for key, width in ccfg["default_widths_m"].items():
                if cv.startswith(key):
                    return width
        return ccfg["default_width_m"]

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

    multiplier_stats = {"count": 0, "weighted_total": 0.0}
    width_stats = {"total": 0.0, "count": 0}
    for _, row in roads.iterrows():
        cs = list(row.geometry.coords)
        # 获取该路段的等级乘数
        mult = _get_multiplier(row.get(road_class_field, "")) if road_class_field else 1.0
        # 获取该路段的宽度和通行能力
        width = _get_width(row) if ccfg["enabled"] else ccfg["default_width_m"]
        eff_width = width * ccfg["effective_width_ratio"]
        capacity_ppm = eff_width * ccfg["ped_flow_rate_ppm"]  # 人/分钟
        for i in range(len(cs) - 1):
            u = _node(*cs[i])
            v = _node(*cs[i + 1])
            d = np.hypot(cs[i + 1][0] - cs[i][0], cs[i + 1][1] - cs[i][1])
            if not G.has_edge(u, v):
                G.add_edge(u, v, length=d, weight=d * mult,
                           width=width, capacity_ppm=capacity_ppm)
                multiplier_stats["count"] += 1
                multiplier_stats["weighted_total"] += mult
                width_stats["total"] += width
                width_stats["count"] += 1

    coords = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in G.nodes()])
    nids = np.array(list(G.nodes()))

    # 诊断: 道路等级分布
    if road_class_field and multiplier_stats["count"] > 0:
        avg_mult = multiplier_stats["weighted_total"] / multiplier_stats["count"]
        print(f"   🛣️  Road hierarchy: avg multiplier={avg_mult:.3f} "
              f"(1.0=tertiary baseline, <1.0=priority roads)")

    # 诊断: 道路宽度分布
    if ccfg["enabled"] and width_stats["count"] > 0:
        avg_w = width_stats["total"] / width_stats["count"]
        print(f"   📏 Road width: avg={avg_w:.1f}m across {width_stats['count']} edges"
              f" (capacity={avg_w * ccfg['effective_width_ratio'] * ccfg['ped_flow_rate_ppm']:.0f} p/min)")

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
    # 使用 "weight" (含道路等级乘数) 作为路径规划权重
    # 使用 "length" (纯欧氏距离) 用于步行时间计算
    cache = {}
    for il in range(len(active_idx)):
        rn = int(rnids[il])
        if rn not in cache:
            try:
                cache[rn] = nx.single_source_dijkstra_path(G, rn, weight="weight")
            except nx.NetworkXError:
                cache[rn] = {}

    # --- 构建路径 ---
    paths, validity = {}, {}
    path_node_seqs = {}   # {(i_local, j): [node_id, ...]} 用于拥挤度建模
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
    """
    从预计算的路径节点序列构建道路拥挤度评估所需的数据结构。

    核心思想:
        1. 每条路径经过若干道路段 (边)
        2. 每条边有通行能力 (由宽度决定)
        3. 当多条路径共享同一条边时，人流量叠加
        4. 超过通行能力时，BPR 函数施加非线性延迟惩罚

    参数:
        path_node_seqs – {(i_local, j): [node_id, ...]} 路径节点序列
        G              – NetworkX 路网图 (边属性含 length, width, capacity_ppm)
        speed          – 步行速度 (m/s)
        max_time_sec   – 最大步行时间 (秒)

    返回:
        congestion_data – dict:
            'edge_paths':      {(i,j): [(u,v), ...]}   每条路径经过的边列表
            'edge_capacities': {(u,v): float}           每条边的总吞吐能力 (人)
            'edge_lengths':    {(u,v): float}            每条边的长度 (m)
            'edge_widths':     {(u,v): float}            每条边的宽度 (m)
    """
    ccfg = ROAD_CONGESTION_CONFIG
    evac_duration_min = max_time_sec / 60.0

    edge_paths = {}
    edge_capacities = {}
    edge_lengths = {}
    edge_widths = {}

    for (il, j), node_seq in path_node_seqs.items():
        edges = []
        for k in range(len(node_seq) - 1):
            u, v = node_seq[k], node_seq[k + 1]
            edge_key = (min(u, v), max(u, v))  # 归一化无向边
            edges.append(edge_key)
            if edge_key not in edge_capacities:
                if G.has_edge(u, v):
                    edata = G.edges[u, v]
                elif G.has_edge(v, u):
                    edata = G.edges[v, u]
                else:
                    edata = {}
                length = edata.get('length', 0.0)
                width = edata.get('width', ccfg['default_width_m'])
                cap_ppm = edata.get('capacity_ppm',
                                    width * ccfg['effective_width_ratio']
                                    * ccfg['ped_flow_rate_ppm'])
                # 总吞吐能力 = 流率(人/分钟) × 疏散时长(分钟)
                edge_capacities[edge_key] = cap_ppm * evac_duration_min
                edge_lengths[edge_key] = length
                edge_widths[edge_key] = width
        edge_paths[(il, j)] = edges

    n_edges = len(edge_capacities)
    print(f"   🚦 Congestion data: {n_edges} unique edges, "
          f"{len(edge_paths)} path-edge mappings")
    if edge_widths:
        ws = np.array(list(edge_widths.values()))
        print(f"   📏 Edge widths: mean={ws.mean():.1f}m, "
              f"min={ws.min():.1f}m, max={ws.max():.1f}m")

    return dict(
        edge_paths=edge_paths,
        edge_capacities=edge_capacities,
        edge_lengths=edge_lengths,
        edge_widths=edge_widths,
    )


# ============================================================
#  上车点 → 避难所路径预计算 (改进 3 基础设施)
# ============================================================
def precompute_shelter_paths(bus_xy, shelter_xy, G, kdtree, nids, ncoords,
                             top_k_for_geometry=50, cache_path=None):
    """
    用 Dijkstra 预计算每个 (上车点, 避难所) 对的最短路径。

    优化策略:
        1. 对每个上车点只跑一次 single_source_dijkstra (而非 stops × shelters 次)
        2. 仅保留 Top K 个最近避难所的完整 LineString 几何 (供可视化)
        3. 全部避难所都返回路径长度 (供 sink 模型 round_trip 计算)
        4. 可选 pickle 缓存 (相同输入下次秒级加载)

    参数:
        bus_xy             – (n_stops, 2) 上车点 UTM 坐标
        shelter_xy         – (n_shelters, 2) 避难所 UTM 坐标
        G                  – networkx 路网图
        kdtree             – 路网节点 KDTree
        nids               – 路网节点 ID 数组
        ncoords            – 路网节点坐标数组
        top_k_for_geometry – 每个上车点保留几何 LineString 的避难所数 (默认 50)
        cache_path         – pkl 缓存路径; None 时不使用缓存

    返回:
        path_lengths   – ndarray (n_stops, n_shelters), 单位米
                         无连通时为 inf
        path_geometry  – dict {(stop_idx, shelter_idx): LineString}
                         仅 Top K 几何, 用于可视化
    """
    import pickle
    import time

    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                cached = pickle.load(f)
            if (cached["n_stops"] == len(bus_xy)
                    and cached["n_shelters"] == len(shelter_xy)
                    and cached["top_k"] == top_k_for_geometry):
                print(f"   📦 Loaded shelter paths from cache: {cache_path}")
                return cached["lengths"], cached["geometry"]
        except Exception as e:
            print(f"   ⚠️  Cache load failed ({e}), recomputing")

    n_stops = len(bus_xy)
    n_shelters = len(shelter_xy)
    print(f"   🔄 Computing Dijkstra: {n_stops} stops × {n_shelters} shelters ...")
    t0 = time.time()

    # ── 1. 把上车点和避难所吸附到路网节点 ──
    stop_dists, stop_kn = kdtree.query(bus_xy)
    stop_nodes = nids[stop_kn]   # (n_stops,) 路网节点 ID
    stop_snapped_coords = ncoords[stop_kn]

    shel_dists, shel_kn = kdtree.query(shelter_xy)
    shel_nodes = nids[shel_kn]   # (n_shelters,) 路网节点 ID
    shel_snapped_coords = ncoords[shel_kn]

    # 节点 ID → 节点在 nids 数组中的索引 (后续查路径用)
    nid_to_local = {int(n): i for i, n in enumerate(nids)}

    # ── 2. 对每个上车点跑一次 single-source Dijkstra ──
    path_lengths = np.full((n_stops, n_shelters), np.inf, dtype=np.float64)
    path_geometry = {}

    for s_idx in range(n_stops):
        src = int(stop_nodes[s_idx])
        try:
            dist_dict, path_dict = nx.single_source_dijkstra(
                G, src, weight="weight")
        except nx.NetworkXError:
            continue

        # 填充该上车点到所有避难所的距离
        for sh_idx in range(n_shelters):
            tgt = int(shel_nodes[sh_idx])
            if tgt in dist_dict:
                path_lengths[s_idx, sh_idx] = dist_dict[tgt]

        # 找出 Top K 最近的避难所, 仅为它们重构 LineString 几何
        valid_shelters = [sh for sh in range(n_shelters)
                          if np.isfinite(path_lengths[s_idx, sh])]
        if not valid_shelters:
            continue
        valid_shelters.sort(key=lambda sh: path_lengths[s_idx, sh])
        top_k_shelters = valid_shelters[:top_k_for_geometry]

        for sh_idx in top_k_shelters:
            tgt = int(shel_nodes[sh_idx])
            if tgt not in path_dict:
                continue
            try:
                node_seq = path_dict[tgt]  # list of node IDs along shortest path
                cs = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_seq]
                if len(cs) >= 2:
                    path_geometry[(s_idx, sh_idx)] = LineString(cs)
            except Exception:
                pass

        if (s_idx + 1) % max(1, n_stops // 10) == 0:
            print(f"      stop {s_idx+1}/{n_stops} done "
                  f"(elapsed {time.time()-t0:.0f}s)")

    elapsed = time.time() - t0
    n_valid = int(np.isfinite(path_lengths).sum())
    print(f"   ✅ Dijkstra done in {elapsed:.1f}s: "
          f"{n_valid}/{n_stops*n_shelters} valid pairs, "
          f"{len(path_geometry)} geometries kept")

    # ── 3. 缓存 ──
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "wb") as f:
                pickle.dump(dict(
                    n_stops=n_stops,
                    n_shelters=n_shelters,
                    top_k=top_k_for_geometry,
                    lengths=path_lengths,
                    geometry=path_geometry,
                ), f)
            print(f"   💾 Cached to {cache_path}")
        except Exception as e:
            print(f"   ⚠️  Cache save failed: {e}")

    return path_lengths, path_geometry


# ============================================================
#  上车点动态关闭: 污染时间预计算
# ============================================================
def compute_stop_contamination_times(bus_xy, risk_arrays, x_mins, y_maxs,
                                     grid_res=GRID_RES, threshold=0.001):
    """
    预计算每个上车点被风险场覆盖的时间 (动态关闭依据)。

    对每个上车点, 遍历 4 个风险阶段, 找到第一个剂量率超过阈值的阶段,
    记录该阶段的起始时间作为"污染时间"。若始终未超过阈值, 则为 inf (永不关闭)。

    参数:
        bus_xy      – (n_bus, 2) 上车点 UTM 坐标
        risk_arrays – list of 4 ndarray, 4 个阶段的风险矩阵
        x_mins      – 各阶段 x_min
        y_maxs      – 各阶段 y_max
        grid_res    – 网格分辨率 (m)
        threshold   – 关闭阈值 (与风险矩阵值同量级)

    返回:
        contamination_times – (n_bus,) ndarray, 各上车点的污染时间 (秒)
                              inf 表示该点在 45 分钟内不会被污染
        contaminated_mask   – (n_bus, 4) bool ndarray, [j, si] 表示上车点 j
                              在阶段 si 是否被污染
    """
    STAGE_TIMES_SEC = [0, 15 * 60, 25 * 60, 35 * 60]
    n_bus = len(bus_xy)
    contamination_times = np.full(n_bus, np.inf, dtype=np.float64)
    contaminated_mask = np.zeros((n_bus, 4), dtype=bool)

    for j in range(n_bus):
        bx, by = bus_xy[j]
        for si in range(4):
            ra = risk_arrays[si]
            xm = x_mins[si]
            ym = y_maxs[si]
            col = int((bx - xm) / grid_res)
            row = int((ym - by) / grid_res)
            if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
                dose = float(ra[row, col])
                if dose > threshold:
                    contamination_times[j] = STAGE_TIMES_SEC[si]
                    contaminated_mask[j, si] = True
                    break  # 取最早污染时间
            # 即使不在网格内也继续检查下一阶段

    n_contaminated = int(np.isfinite(contamination_times).sum())
    if n_contaminated > 0:
        cont_times_finite = contamination_times[np.isfinite(contamination_times)]
        print(f"   🔴 Pickup closure: {n_contaminated}/{n_bus} stops contaminated "
              f"(earliest at {cont_times_finite.min()/60:.0f} min)")
    else:
        print(f"   🟢 Pickup closure: 0/{n_bus} stops contaminated "
               f"(threshold={threshold})")

    return contamination_times, contaminated_mask


# ============================================================
#  4阶段安全约束: 预计算全时段安全上车点 (v5.6)
# ============================================================
def compute_4stage_safe_stops(bus_xy, risk_arrays, x_mins, y_maxs, grid_res=GRID_RES):
    """
    预计算在所有4个风险阶段均未被覆盖（风险值严格为0）的上车点。

    逻辑:
        - 对每个上车点, 检查其在 15/25/35/45 min 四个时刻的风险值
        - 只要在任一时刻风险值 > 0, 该上车点即被标记为"不安全"
        - 仅保留在全部4个时刻均为0风险的上车点作为"4阶段安全"

    参数:
        bus_xy      – (n_bus, 2) 上车点 UTM 坐标
        risk_arrays – list of 4 ndarray, 4 个阶段的风险矩阵
        x_mins      – 各阶段 x_min
        y_maxs      – 各阶段 y_max
        grid_res    – 网格分辨率 (m)

    返回:
        safe_stops       – set, 4阶段安全上车点索引集合
        stage_safe_masks – (n_bus, 4) bool ndarray, [j, si] True=安全
    """
    n_bus = len(bus_xy)
    stage_safe_masks = np.ones((n_bus, 4), dtype=bool)  # 默认安全

    for si in range(4):
        ra = risk_arrays[si]
        xm = x_mins[si]
        ym = y_maxs[si]
        for j in range(n_bus):
            bx, by = bus_xy[j]
            col = int((bx - xm) / grid_res)
            row = int((ym - by) / grid_res)
            if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
                if ra[row, col] > 0:
                    stage_safe_masks[j, si] = False
            # 不在风险场范围内 → 保持安全 (默认True)

    # 4阶段安全 = 所有阶段均安全
    safe_stops = set(np.where(stage_safe_masks.all(axis=1))[0])

    n_unsafe_per_stage = (~stage_safe_masks).sum(axis=0)
    stage_labels = ["15min", "25min", "35min", "45min"]
    print(f"   🛡️  4-stage safe stops: {len(safe_stops)}/{n_bus}")
    for si in range(4):
        n_contaminated = int(n_unsafe_per_stage[si])
        print(f"      Stage {si} ({stage_labels[si]}): "
              f"{n_bus - n_contaminated} safe, {n_contaminated} contaminated")

    # 诊断: 哪些上车点在哪些阶段不安全
    n_never_safe = int((~stage_safe_masks).all(axis=1).sum())
    n_partial = n_bus - len(safe_stops) - n_never_safe
    print(f"      Summary: {len(safe_stops)} always-safe, "
          f"{n_partial} partially-contaminated, "
          f"{n_never_safe} always-contaminated")

    return safe_stops, stage_safe_masks


# ============================================================
#  多阶段滚动时域: 可行域重建 (v5.5)
# ============================================================
def rebuild_feasible_for_stage(active_indices, available_stops,
                                road_paths, speed, max_walk_time_sec):
    """
    为阶段 s 重建可行域 — 仅包含活跃居民和可用上车点。

    参数:
        active_indices      – 当前阶段的活跃居民索引列表 (在road_paths中的局部索引)
        available_stops     – 当前阶段可用的上车点索引集合
        road_paths          – 预计算的路径字典 {(居民局部索引, 上车点索引): LineString}
        speed               – 步行速度 (m/s)
        max_walk_time_sec   – 最大步行时间 (秒)

    返回:
        feasible – list of list, feasible[i] 为第 i 个活跃居民的可用上车点列表
    """
    feasible = []
    for il in active_indices:
        f_list = []
        for j in available_stops:
            pl = road_paths.get((il, j))
            if pl is not None and pl.length / speed <= max_walk_time_sec:
                f_list.append(j)
        feasible.append(f_list)
    n_no_opt = sum(1 for f in feasible if len(f) == 0)
    print(f"   🔄 Stage feasible: {len(active_indices) - n_no_opt}/{len(active_indices)} "
          f"residents have reachable stops (avail_stops={len(available_stops)})")
    return feasible


# ============================================================
#  多阶段滚动时域: 中间位置计算 (v5.5)
# ============================================================
def compute_intermediate_positions(snapped_res, road_paths, assignments,
                                   speed, elapsed_sec):
    """
    计算居民在 elapsed_sec 时刻的当前位置。

    对于已到达上车点的居民, 返回上车点坐标;
    对于仍在路上的居民, 沿路径插值返回当前位置。

    参数:
        snapped_res   – (n_residents, 2) 居民吸附坐标
        road_paths    – 预计算路径字典
        assignments   – list, 各居民的当前分配上车点
        speed         – 步行速度 (m/s)
        elapsed_sec   – 从 t=0 开始经过的时间 (秒)

    返回:
        positions – (n_residents, 2) ndarray, 各居民当前位置
        arrived   – (n_residents,) bool ndarray, 各居民是否已到达
    """
    n = len(assignments)
    positions = np.array(snapped_res, dtype=np.float64).copy()
    arrived = np.zeros(n, dtype=bool)

    for i in range(n):
        j = assignments[i]
        if j < 0:
            continue
        pl = road_paths.get((i, j))
        if pl is None:
            continue
        d = pl.length
        t_walk = d / speed
        if elapsed_sec >= t_walk:
            # 已到达
            positions[i] = snapped_res[j] if j < len(snapped_res) else positions[i]
            arrived[i] = True
        elif d > 0:
            # 仍在路上 — 沿路径插值
            frac = min((elapsed_sec * speed) / d, 1.0)
            pt = pl.interpolate(frac, normalized=True)
            positions[i] = [pt.x, pt.y]

    return positions, arrived
