"""
上车点 Sink 边界条件模块 (Pickup Point Sink Model) — v7.2
=========================================================

v7.2 核心改进 (往返逻辑修正):
  1. 修复巴士载客量覆盖 bug — 累计载客正确累加
  2. 修复满载判断 — 基于总载客量而非单轮装载量
  3. 明确状态机: depot→stop→(装载循环)→shelter→stop→... 往复
  4. 巴士在避难所卸客后自动返回开放上车点继续接人
  5. 增大 max_iter 和 max_evac_duration 允许多轮往返

v7.0~v7.1 改进:
  - 巴士沿真实路网行驶 (Dijkstra 路径)
  - 满载或该站清空后才前往避难所
  - 动态上车点风险过滤 + Euclidean×1.3 避难所距离修正

文献依据 (SCI, 2020-2024):
─────────────────────────────────────────────────────────────────────
[1] Zhao X. et al. "A round-trip bus evacuation model with scheduling
    and routing planning." Transportation Research Part A, 2020.

[2] Sun Y., Chai X., Chen C. "Bus based emergency evacuation
    organization strategy of nuclear power plant planning restricted
    area." Progress in Nuclear Energy, 2024.

[3] IAEA GSG-11: "Criteria for Use in Preparedness and Response
    for a Nuclear or Radiological Emergency", 2018.

[4] NRC NUREG/CR-7269: "Evacuation Time Estimates", 2020.

[5] Goerigk M., Grün B. "A robust bus evacuation model with delayed
    scenario information." OR Spectrum, 2014.
"""

import numpy as np
import math
import networkx as nx
from collections import defaultdict

from config import RISK_STAGE_TIMES


# ============================================================
#  配置参数
# ============================================================
SINK_CONFIG = dict(
    # ─── 巴士车队 ───
    bus_capacity         = 50,      # 单辆巴士容量 (人)
    fleet_size           = 200,     # 车队可用巴士总数
    bus_speed_kmh        = 30.0,    # 巴士平均行驶速度 (km/h)
    bus_speed_ms         = 30 * 1000 / 3600,   # 转 m/s ≈ 8.33

    # ─── 调度参数 ───
    dispatch_delay_sec   = 600,     # 调度响应延迟 (秒) ≈ 10 min
    boarding_time_per_pax = 0,      # 单人上车时间 (秒) — 当前设为 0

    # ─── 风险场关闭参数 ───
    risk_closure_threshold = 0.0,   # 风险值超过此阈值视为"被风险场覆盖"

    # ─── 避难所参数 ───
    shelter_distance_min_m = 30_000,  # 避难所距核电厂最小距离 (m)

    # ─── 排队期间风险计算 ───
    queue_risk_enabled   = True,    # 是否计算排队期间的辐射暴露

    # ─── 总疏散时长上限 ───
    max_evac_duration    = 14400,   # 14400 s = 4 小时 (允许巴士往返)

    # ─── 不可疏散惩罚 ───
    inevac_penalty       = 1e6,     # 每个未疏散人口的惩罚系数

    # ─── 道路距离修正 ───
    road_distance_factor = 1.3,     # Euclidean → 实际道路距离修正系数
)


# ============================================================
#  路径距离预计算工具函数
# ============================================================
def _precompute_path_distances(G, node_pairs):
    """
    批量计算节点对之间的最短路径距离和节点序列。

    参数:
        G          – NetworkX 图
        node_pairs – list of (src_node, dst_node)

    返回:
        dist_map   – {(src, dst): (distance, node_sequence)}
    """
    dist_map = {}
    # 按源节点分组, 每个源节点只需一次 single_source_dijkstra
    sources = set(p[0] for p in node_pairs)
    for src in sources:
        try:
            lengths, paths = nx.single_source_dijkstra(G, src, weight="length")
        except nx.NetworkXNoPath:
            lengths, paths = {}, {}
        for dst in [p[1] for p in node_pairs if p[0] == src]:
            if dst in lengths:
                dist_map[(src, dst)] = (lengths[dst], paths[dst])
            else:
                # 回退: Euclidean 距离 × 1.5 作为估算
                sx, sy = G.nodes[src]["x"], G.nodes[src]["y"]
                dx, dy = G.nodes[dst]["x"], G.nodes[dst]["y"]
                est_dist = math.hypot(dx - sx, dy - sy) * 1.5
                dist_map[(src, dst)] = (est_dist, [src, dst])
    return dist_map


def _path_length_from_nodes(G, node_seq):
    """从节点序列计算路径总长度"""
    if len(node_seq) < 2:
        return 0.0
    total = 0.0
    for k in range(len(node_seq) - 1):
        u, v = node_seq[k], node_seq[k + 1]
        if G.has_edge(u, v):
            total += G[u][v].get("length", 0.0)
        elif G.has_edge(v, u):
            total += G[v][u].get("length", 0.0)
        else:
            # 无边连通, 用 Euclidean 估算
            ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
            vx, vy = G.nodes[v]["x"], G.nodes[v]["y"]
            total += math.hypot(vx - ux, vy - uy) * 1.5
    return total


# ============================================================
#  PickupSinkModel — 动态上车点 Sink 边界条件主类 (v7.2)
# ============================================================
class PickupSinkModel:
    """
    动态上车点 Sink 边界条件模型 (v7.2 — 往返修正版)。

    给定一个分配方案 ind = [j_0, ..., j_{N-1}] (居民→上车点),
    本模型计算考虑以下边界条件后的总疏散时间与总风险:
        - 上车点随时变风险场动态关闭
        - 巴士沿真实路网行驶 (depot→stop: Dijkstra; stop↔shelter: Euclidean×1.3)
        - 初始所有巴士从 depot 出发前往上车点
        - 满载 (bus_capacity) 或该站清空后才前往避难所
        - **巴士到达避难所卸客后, 返回开放上车点继续接人 (往返循环)**
        - 返回时不可前往已关闭的上车点
        - 巴士初始位置在大鹏总站 (BUS_DEPOT)

    状态机:
        depot ──→ stop (装载) ──→ shelter (卸客) ──→ stop (装载) ──→ ...
                   ↑                                              │
                   └──────────── 未满且站有剩余 ←──────────────────┘
                                                               ↓
                                                          shelter → ...

    使用示例:
        sink = PickupSinkModel(bus_xy, risk_arrays, x_mins, y_maxs,
                               shelter_xy, shelter_capacities,
                               SINK_CONFIG, road_graph=G,
                               stop_nodes=..., shelter_nodes=...,
                               depot_node=...)
        T_total, R_total, info = sink.process(ind, arrival_times, pop_arr)
    """

    def __init__(self, bus_xy, risk_arrays, x_mins, y_maxs,
                 shelter_xy, shelter_capacities, config=None,
                 depot_xy=None, road_graph=None,
                 stop_nodes=None, shelter_nodes=None, depot_node=None):
        """
        参数:
            bus_xy             – (n_bus, 2) 上车点坐标 (UTM)
            risk_arrays        – list of 4 ndarray, 4 个时间阶段的风险矩阵
            x_mins             – 各阶段风险矩阵 x_min
            y_maxs             – 各阶段风险矩阵 y_max
            shelter_xy         – (n_shelters, 2) 避难所坐标 (UTM)
            shelter_capacities – (n_shelters,) 避难所容量
            config             – SINK_CONFIG 字典
            depot_xy           – (2,) 巴士总站坐标 (UTM)
            road_graph         – NetworkX 图 (用于路径计算)
            stop_nodes         – list[int], 各上车点对应的路网节点 ID
            shelter_nodes      – list[int], 各避难所对应的路网节点 ID
            depot_node         – int, 巴士总站对应的路网节点 ID
        """
        self.bus_xy = np.asarray(bus_xy, dtype=np.float64)
        self.risk_arrays = risk_arrays
        self.x_mins = x_mins
        self.y_maxs = y_maxs
        self.shelter_xy = np.asarray(shelter_xy, dtype=np.float64)
        self.shelter_capacities = np.asarray(shelter_capacities, dtype=np.float64)
        self.config = config or SINK_CONFIG
        self.n_bus = len(bus_xy)
        self.n_shelters = len(shelter_xy)

        # 巴士总站
        if depot_xy is not None:
            self.depot_xy = np.asarray(depot_xy, dtype=np.float64).reshape(2)
        else:
            self.depot_xy = None

        # 路网相关
        self.road_graph = road_graph
        self.stop_nodes = stop_nodes       # list[int], len=n_bus
        self.shelter_nodes = shelter_nodes  # list[int], len=n_shelters
        self.depot_node = depot_node        # int

        # 巴士专用路网子图 (仅含主干道，用于 stop→shelter 路径计算)
        self.bus_road_graph = None

        # 预计算路径距离
        self._precompute_road_distances()

        # 预计算: 各上车点的关闭时间
        self.closure_times = self._compute_closure_times()

    # ─────────────────────────────────────────────────────
    #  预计算: 路网距离矩阵
    # ─────────────────────────────────────────────────────
    def _build_bus_road_graph(self):
        """构建巴士专用路网子图 (仅含主干道，限制行驶道路级别)

        道路级别权重惩罚:
            motorway/trunk/primary: 1.0  (优先)
            secondary:              1.5  (可接受)
            tertiary/residential:   3.0  (尽量避免)
            service/track/footway:  100  (基本禁止)
        """
        G = self.road_graph
        if G is None:
            return

        # 道路级别权重映射
        highway_weights = {
            "motorway": 1.0, "motorway_link": 1.2,
            "trunk": 1.0, "trunk_link": 1.2,
            "primary": 1.0, "primary_link": 1.3,
            "secondary": 1.5, "secondary_link": 1.8,
            "tertiary": 3.0, "tertiary_link": 3.5,
            "residential": 3.0, "unclassified": 3.0,
            "service": 100.0, "track": 100.0,
            "footway": 100.0, "path": 100.0,
            "pedestrian": 100.0, "steps": 100.0,
        }

        bus_G = nx.Graph()

        # 复制所有节点
        for n, data in G.nodes(data=True):
            bus_G.add_node(n, **data)

        # 复制边，施加权重惩罚
        for u, v, data in G.edges(data=True):
            hw = data.get("highway", "residential")
            weight = highway_weights.get(hw, 3.0)
            length = data.get("length", 0.0)
            weighted_length = length * weight

            bus_G.add_edge(u, v, length=length, weighted_length=weighted_length,
                           highway=hw, **{k: v for k, v in data.items()
                                          if k not in ("length",)})

        self.bus_road_graph = bus_G
        n_edges = bus_G.number_of_edges()
        print(f"   🚌 Bus road graph: {n_edges} edges (with highway weighting)")

    def _snap_shelters_to_major_roads(self):
        """将每个避难所吸附至路网边缘最近的主干道节点

        策略: 避难所距核电厂 ≥30km, 超出路网裁剪范围 (10km)。
        因此找到路网中**距避难所方向最近的主干道节点**作为下车点，
        然后从该节点直线到避难所。

        主干道定义: highway in [motorway, trunk, primary, secondary]
        """
        G = self.bus_road_graph or self.road_graph
        if G is None:
            return

        # 收集所有主干道节点
        major_highways = {"motorway", "motorway_link", "trunk", "trunk_link",
                          "primary", "primary_link", "secondary", "secondary_link"}
        major_nodes = []
        major_coords = []
        for n, data in G.nodes(data=True):
            hw = data.get("highway", "")
            # 检查节点是否连接到主干道边
            is_major = False
            for neighbor in G.neighbors(n):
                edge_data = G[n][neighbor]
                if edge_data.get("highway", "") in major_highways:
                    is_major = True
                    break
            if is_major:
                major_nodes.append(n)
                major_coords.append((data["x"], data["y"]))

        if not major_nodes:
            print("   ⚠️  No major road nodes found, using Euclidean fallback")
            return

        major_coords = np.array(major_coords)
        from scipy.spatial import KDTree
        major_kdtree = KDTree(major_coords)

        # 为每个避难所找到路网中最近的主干道节点
        self.shelter_snap_nodes = []
        self.shelter_snap_coords = []
        for si in range(self.n_shelters):
            sx, sy = self.shelter_xy[si]
            _, ni = major_kdtree.query([sx, sy])
            snap_node = major_nodes[ni]
            snap_coord = major_coords[ni]
            self.shelter_snap_nodes.append(snap_node)
            self.shelter_snap_coords.append(snap_coord)

        n_snapped = len(self.shelter_snap_nodes)
        print(f"   🏠 Shelters snapped to nearest major road nodes: "
              f"{n_snapped}/{self.n_shelters}")

    def _precompute_road_distances(self):
        """预计算 depot→上车点、上车点→避难所的路网距离

        改进 (v7.3):
            1. 构建巴士专用路网子图 (道路级别加权)
            2. 避难所吸附至主干道节点
            3. stop→shelter 使用 Dijkstra 路径 (上车点→吸附的主干道节点)
        """
        if self.road_graph is None:
            print("   ⚠️  No road graph provided, using Euclidean distances")
            self._use_euclidean_distances()
            return

        # Step 1: 构建巴士专用路网
        self._build_bus_road_graph()

        # Step 2: 避难所吸附至主干道
        self._snap_shelters_to_major_roads()

        G = self.bus_road_graph or self.road_graph
        factor = self.config.get("road_distance_factor", 1.3)

        # ── depot → 上车点: Dijkstra ──
        pairs = []
        if self.depot_node is not None and self.stop_nodes:
            for j in range(self.n_bus):
                pairs.append((self.depot_node, self.stop_nodes[j]))

        dist_map = _precompute_path_distances(G, pairs) if pairs else {}

        self.depot_to_stop_dist = np.zeros(self.n_bus, dtype=np.float64)
        self.depot_to_stop_path = [None] * self.n_bus
        if self.depot_node is not None:
            for j in range(self.n_bus):
                key = (self.depot_node, self.stop_nodes[j])
                if key in dist_map:
                    self.depot_to_stop_dist[j] = dist_map[key][0]
                    self.depot_to_stop_path[j] = dist_map[key][1]

        # ── 上车点 → 避难所: Dijkstra 至吸附的主干道节点 + 直线到避难所 ──
        self.stop_shelter_dist = np.zeros((self.n_bus, self.n_shelters),
                                          dtype=np.float64)
        self.stop_shelter_path = [[None] * self.n_shelters
                                  for _ in range(self.n_bus)]

        if hasattr(self, 'shelter_snap_nodes') and self.shelter_snap_nodes:
            # 收集所有需要计算的 (stop_node, shelter_snap_node) 对
            shelter_pairs = []
            for j in range(self.n_bus):
                for si in range(self.n_shelters):
                    shelter_pairs.append((self.stop_nodes[j],
                                          self.shelter_snap_nodes[si]))

            if shelter_pairs:
                shelter_dist_map = _precompute_path_distances(G, shelter_pairs)

                for j in range(self.n_bus):
                    for si in range(self.n_shelters):
                        key = (self.stop_nodes[j], self.shelter_snap_nodes[si])
                        if key in shelter_dist_map:
                            dist_road, path_nodes = shelter_dist_map[key]
                            # 加上主干道节点到避难所的直线距离
                            snap_coord = self.shelter_snap_coords[si]
                            shelter_coord = (self.shelter_xy[si, 0],
                                             self.shelter_xy[si, 1])
                            dist_final = math.hypot(
                                shelter_coord[0] - snap_coord[0],
                                shelter_coord[1] - snap_coord[1])
                            total_dist = dist_road + dist_final

                            self.stop_shelter_dist[j, si] = total_dist
                            # 路径: 路网节点序列 + 吸附点坐标 + 避难所实际坐标
                            self.stop_shelter_path[j][si] = (
                                path_nodes + [snap_coord, shelter_coord])
                        else:
                            # 无路径连通，回退到 Euclidean×factor
                            dx = self.bus_xy[j, 0] - self.shelter_xy[si, 0]
                            dy = self.bus_xy[j, 1] - self.shelter_xy[si, 1]
                            self.stop_shelter_dist[j, si] = math.hypot(dx, dy) * factor
                            mx = (self.bus_xy[j, 0] + self.shelter_xy[si, 0]) / 2
                            my = (self.bus_xy[j, 1] + self.shelter_xy[si, 1]) / 2
                            self.stop_shelter_path[j][si] = [
                                (self.bus_xy[j, 0], self.bus_xy[j, 1]),
                                (mx, my),
                                (self.shelter_xy[si, 0], self.shelter_xy[si, 1]),
                            ]

        n_dijkstra = sum(1 for j in range(self.n_bus)
                         for si in range(self.n_shelters)
                         if self.stop_shelter_path[j][si] is not None
                         and not isinstance(self.stop_shelter_path[j][si][0], tuple))
        print(f"   🚌 Stop↔Shelter: {n_dijkstra}/{self.n_bus * self.n_shelters} "
              f"via Dijkstra (bus-weighted roads)")

    def _use_euclidean_distances(self):
        """回退: 使用 Euclidean 距离"""
        factor = self.config.get("road_distance_factor", 1.3)
        self.depot_to_stop_dist = np.zeros(self.n_bus, dtype=np.float64)
        self.depot_to_stop_path = [None] * self.n_bus
        if self.depot_xy is not None:
            diff = self.bus_xy - self.depot_xy[np.newaxis, :]
            self.depot_to_stop_dist = np.sqrt((diff ** 2).sum(axis=1))

        diff = self.bus_xy[:, np.newaxis, :] - self.shelter_xy[np.newaxis, :, :]
        self.stop_shelter_dist = np.sqrt((diff ** 2).sum(axis=2)) * factor
        self.stop_shelter_path = [[None] * self.n_shelters
                                  for _ in range(self.n_bus)]

    # ─────────────────────────────────────────────────────
    #  预计算: 上车点关闭时间
    # ─────────────────────────────────────────────────────
    def _compute_closure_times(self, grid_res=400):
        """
        计算每个上车点被风险场覆盖的时间。
        遍历各阶段的风险矩阵 (由 RISK_STAGE_TIMES 定义)，
        当某上车点位置的风险值首次超过阈值时，该点关闭。

        未被任何阶段风险覆盖的上车点, 关闭时间设为 never_close_time
        (默认 4 小时), 而非 max_evac_duration, 以允许巴士往返。
        """
        cfg = self.config
        threshold = cfg.get("risk_closure_threshold", 0.0)
        stage_times_sec = [t * 60 for t in RISK_STAGE_TIMES]
        never_close_time = float(cfg.get("max_evac_duration", 14400))

        closure = np.full(self.n_bus, never_close_time)

        for j in range(self.n_bus):
            bx, by = self.bus_xy[j]
            for si, t_stage in enumerate(stage_times_sec):
                ra = self.risk_arrays[si]
                xm = self.x_mins[si]
                ym = self.y_maxs[si]
                col = int((bx - xm) / grid_res)
                row = int((ym - by) / grid_res)
                if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
                    risk_val = float(ra[row, col])
                    if risk_val > threshold:
                        closure[j] = float(t_stage)
                        break

        return closure

    # ─────────────────────────────────────────────────────
    #  避难所分配: 最近可用
    # ─────────────────────────────────────────────────────
    def _find_shelter(self, stop_idx, load, shelter_remaining):
        """
        为从上车点 stop_idx 出发的 load 人分配最近可用避难所。
        优先选择距离最近且有足够容量的避难所。
        """
        dists = self.stop_shelter_dist[stop_idx]
        order = np.argsort(dists)

        for si in order:
            if shelter_remaining[si] >= load:
                return int(si)
        for si in order:
            if shelter_remaining[si] > 0:
                return int(si)
        return int(order[0])

    # ─────────────────────────────────────────────────────
    #  风险场查询
    # ─────────────────────────────────────────────────────
    def _risk_at(self, x, y, t_sec, grid_res):
        """根据时刻 t (秒) 选择对应的风险矩阵, 查询 (x,y) 处的风险值"""
        t_min = t_sec / 60.0
        n_stages = len(RISK_STAGE_TIMES)
        si = 0
        for k in range(n_stages):
            if t_min < RISK_STAGE_TIMES[k]:
                si = k
                break
        else:
            si = n_stages - 1
        ra = self.risk_arrays[si]
        xm = self.x_mins[si]
        ym = self.y_maxs[si]
        col = int((x - xm) / grid_res)
        row = int((ym - y) / grid_res)
        if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
            return float(ra[row, col])
        return 0.0

    # ─────────────────────────────────────────────────────
    #  主入口: 车队疏散仿真 (v7.2 — 往返修正版)
    # ─────────────────────────────────────────────────────
    def process(self, assignment, arrival_times, pop_arr,
                walk_risk=0.0, grid_res=400):
        """
        计算考虑动态 Sink 边界条件后的总时间与总风险。

        仿真逻辑 (v7.2 — 往返修正版):
          ┌──────────────────────────────────────────────────────┐
          │  巴士状态机:                                          │
          │  Case A: 巴士在上车点有载客 → 继续装载或去避难所       │
          │  Case B: 巴士在 depot/避难所/空站 → 派往最佳上车点     │
          │                                                      │
          │  往返循环:                                             │
          │  shelter(卸客) → 寻找开放上车点 → 前往接人 → 满载/清空  │
          │  → 去避难所 → 卸客 → 返回开放上车点 → ...              │
          └──────────────────────────────────────────────────────┘

        返回:
            total_time – float, 总疏散时间 (秒)
            total_risk – float, 总风险
            info       – dict, 详细统计 (含 bus_trajectory)
        """
        cfg = self.config
        n = len(assignment)
        max_evac = float(cfg["max_evac_duration"])

        # ── Step 1: 按上车点分组 ──
        stop_data = {}
        for i in range(n):
            j = assignment[i]
            if j < 0 or j is None:
                continue
            t = arrival_times[i]
            if not np.isfinite(t):
                continue
            if j not in stop_data:
                stop_data[j] = {"pop": 0.0, "latest_arrival": 0.0}
            stop_data[j]["pop"] += float(pop_arr[i])
            stop_data[j]["latest_arrival"] = max(
                stop_data[j]["latest_arrival"], float(t))

        if not stop_data:
            return 0.0, walk_risk, dict(
                n_stops_used=0, bus_trips_total=0,
                max_completion_s=0.0, unevacuated_pop=0.0,
                bus_trajectory=[])

        # ── Step 2: 初始化车队 ──
        n_buses = cfg["fleet_size"]
        bus_capacity = cfg["bus_capacity"]
        bus_speed = cfg["bus_speed_ms"]
        boarding_per_pax = cfg["boarding_time_per_pax"]

        # 巴士状态: 初始在 depot
        bus_avail = np.full(n_buses, float(cfg["dispatch_delay_sec"]),
                            dtype=np.float64)
        # bus_location[bi]: ("depot", -1) | ("stop", j) | ("shelter", si)
        bus_location = [("depot", -1)] * n_buses
        bus_current_load = np.zeros(n_buses, dtype=np.float64)

        # 各上车点剩余人口
        remaining = {j: d["pop"] for j, d in stop_data.items()}
        latest_arrival = {j: d["latest_arrival"] for j, d in stop_data.items()}

        # 避难所剩余容量
        shelter_remaining = self.shelter_capacities.copy()

        # 统计
        total_queue_risk = 0.0
        max_completion_time = 0.0
        bus_trips = 0
        unevacuated_pop = 0.0

        # 巴士轨迹记录
        bus_trajectory = []

        # ── Step 3: 事件驱动仿真 (v7.2) ──
        # 循环直到所有上车点清空或达到最大时长
        max_iter = n_buses * 50  # 增大上限, 允许多轮往返
        iteration = 0

        while iteration < max_iter:
            iteration += 1

            # 检查是否还有剩余人口
            total_remaining = sum(remaining.values())
            if total_remaining <= 1e-6:
                break

            # 找出最早可用的巴士
            bi = int(np.argmin(bus_avail))
            if bus_avail[bi] > max_evac:
                break  # 所有巴士都超时了

            loc_type, loc_idx = bus_location[bi]
            cur_load = bus_current_load[bi]

            # ════════════════════════════════════════════════════
            #  Case A: 巴士在上车点且有载客
            #         → 继续装载更多居民, 或去避难所
            # ════════════════════════════════════════════════════
            if loc_type == "stop" and cur_load > 1e-6:
                j = loc_idx
                t_close = self.closure_times[j]
                now = bus_avail[bi]

                # A1: 该站还有剩余人口且未关闭 → 尝试继续装载
                if remaining[j] > 1e-6 and now < t_close:
                    space_left = bus_capacity - cur_load

                    # 巴士已满 → 直接去避难所
                    if space_left <= 1e-6:
                        si = self._find_shelter(j, cur_load,
                                                shelter_remaining)
                        travel = self.stop_shelter_dist[j, si] / bus_speed
                        shelter_arrival = now + travel

                        bus_trajectory.append((
                            bi, "stop", j, "shelter", si,
                            now, shelter_arrival, cur_load,
                            self.stop_shelter_path[j][si]))

                        shelter_remaining[si] -= min(
                            cur_load, shelter_remaining[si])
                        bus_avail[bi] = shelter_arrival
                        bus_location[bi] = ("shelter", si)
                        bus_current_load[bi] = 0
                        max_completion_time = max(
                            max_completion_time, shelter_arrival)
                        bus_trips += 1
                        continue

                    # 装载更多居民
                    new_load = min(space_left, remaining[j])
                    load_start = max(now, latest_arrival[j])

                    if load_start >= t_close:
                        # 即将关闭, 直接去避难所
                        si = self._find_shelter(j, cur_load,
                                                shelter_remaining)
                        travel = self.stop_shelter_dist[j, si] / bus_speed
                        shelter_arrival = now + travel

                        bus_trajectory.append((
                            bi, "stop", j, "shelter", si,
                            now, shelter_arrival, cur_load,
                            self.stop_shelter_path[j][si]))

                        shelter_remaining[si] -= min(
                            cur_load, shelter_remaining[si])
                        bus_avail[bi] = shelter_arrival
                        bus_location[bi] = ("shelter", si)
                        bus_current_load[bi] = 0
                        max_completion_time = max(
                            max_completion_time, shelter_arrival)
                        bus_trips += 1
                        continue

                    boarding = new_load * boarding_per_pax
                    depart_time = load_start + boarding

                    # 排队风险
                    if cfg["queue_risk_enabled"] and new_load > 0:
                        risk_val = self._risk_at(
                            self.bus_xy[j, 0], self.bus_xy[j, 1],
                            load_start, grid_res)
                        total_queue_risk += risk_val * new_load * boarding

                    # 更新剩余人口 & 累计载客
                    remaining[j] -= new_load
                    total_load = cur_load + new_load
                    bus_current_load[bi] = total_load

                    is_full = (total_load >= bus_capacity - 1e-6)
                    is_cleared = (remaining[j] <= 1e-6)

                    if is_full or is_cleared:
                        # 前往避难所
                        si = self._find_shelter(j, total_load,
                                                shelter_remaining)
                        travel = self.stop_shelter_dist[j, si] / bus_speed
                        shelter_arrival = depart_time + travel

                        bus_trajectory.append((
                            bi, "stop", j, "shelter", si,
                            depart_time, shelter_arrival, total_load,
                            self.stop_shelter_path[j][si]))

                        shelter_remaining[si] -= min(
                            total_load, shelter_remaining[si])
                        bus_avail[bi] = shelter_arrival
                        bus_location[bi] = ("shelter", si)
                        bus_current_load[bi] = 0
                        max_completion_time = max(
                            max_completion_time, shelter_arrival)
                        bus_trips += 1
                    else:
                        # 继续等待下一批居民
                        bus_avail[bi] = depart_time
                    continue

                # A2: 该站已清空或已关闭 → 带着现有载客去避难所
                else:
                    si = self._find_shelter(j, cur_load,
                                            shelter_remaining)
                    travel = self.stop_shelter_dist[j, si] / bus_speed
                    shelter_arrival = now + travel

                    bus_trajectory.append((
                        bi, "stop", j, "shelter", si,
                        now, shelter_arrival, cur_load,
                        self.stop_shelter_path[j][si]))

                    shelter_remaining[si] -= min(
                        cur_load, shelter_remaining[si])
                    bus_avail[bi] = shelter_arrival
                    bus_location[bi] = ("shelter", si)
                    bus_current_load[bi] = 0
                    max_completion_time = max(
                        max_completion_time, shelter_arrival)
                    bus_trips += 1
                    continue

            # ════════════════════════════════════════════════════
            #  Case B: 巴士在 depot / 避难所 / 空车上车点
            #         → 寻找最佳开放上车点前往接人
            # ════════════════════════════════════════════════════
            best_j = None
            best_score = float("inf")

            for j in stop_data:
                if remaining[j] <= 1e-6:
                    continue
                t_close = self.closure_times[j]
                travel = self._travel_time_to_stop(bi, j, bus_location)
                arrival = bus_avail[bi] + travel
                if arrival >= t_close:
                    continue
                # 评分: 关闭时间越早越优先 (紧迫性), 距离越近越好
                score = t_close - arrival + travel * 0.1
                if score < best_score:
                    best_score = score
                    best_j = j

            if best_j is None:
                # 这辆巴士无法到达任何有剩余人口的上车点
                # 检查是否还有任何巴士能到达任何上车点
                any_reachable = False
                for bi2 in range(n_buses):
                    if bus_avail[bi2] > max_evac:
                        continue
                    for j in stop_data:
                        if remaining[j] <= 1e-6:
                            continue
                        t_close = self.closure_times[j]
                        travel = self._travel_time_to_stop(
                            bi2, j, bus_location)
                        if bus_avail[bi2] + travel < t_close:
                            any_reachable = True
                            break
                    if any_reachable:
                        break

                if not any_reachable:
                    # 没有任何巴士能到达任何有剩余人口的上车点
                    unevacuated_pop = total_remaining
                    break

                # 标记此巴士为超时, 继续尝试其他巴士
                bus_avail[bi] = max_evac + 1
                continue

            j = best_j
            t_close = self.closure_times[j]

            # ── 巴士前往上车点 ──
            travel_to_stop = self._travel_time_to_stop(bi, j, bus_location)
            arrival_at_stop = bus_avail[bi] + travel_to_stop
            path_nodes = self._path_nodes_to_stop(bi, j, bus_location)

            # 记录轨迹: 前往上车点
            loc_type_cur, loc_idx_cur = bus_location[bi]
            bus_trajectory.append((
                bi, loc_type_cur, loc_idx_cur, "stop", j,
                bus_avail[bi], arrival_at_stop, 0, path_nodes
            ))

            # ── 等待居民到达 + 装载 ──
            load_start = max(arrival_at_stop, latest_arrival[j])
            if load_start >= t_close:
                # 到达时上车点已关闭, 巴士空车停留
                bus_avail[bi] = arrival_at_stop
                bus_location[bi] = ("stop", j)
                continue

            load = min(bus_capacity, remaining[j])
            boarding = load * boarding_per_pax
            depart_time = load_start + boarding

            # ── 排队风险 ──
            if cfg["queue_risk_enabled"] and load > 0:
                wait_time = max(0.0, load_start - latest_arrival[j])
                if wait_time > 0:
                    risk_val = self._risk_at(
                        self.bus_xy[j, 0], self.bus_xy[j, 1],
                        load_start, grid_res)
                    total_queue_risk += risk_val * load * wait_time

            # ── 更新上车点剩余人口 ──
            remaining[j] -= load
            bus_current_load[bi] = load

            # ── 判断: 满载或该站清空 → 前往避难所 ──
            is_full = (load >= bus_capacity - 1e-6)
            is_cleared = (remaining[j] <= 1e-6)

            if is_full or is_cleared:
                # 前往避难所
                si = self._find_shelter(j, load, shelter_remaining)
                travel_to_shelter = self.stop_shelter_dist[j, si] / bus_speed
                shelter_arrival = depart_time + travel_to_shelter
                path_to_shelter = self.stop_shelter_path[j][si]

                bus_trajectory.append((
                    bi, "stop", j, "shelter", si,
                    depart_time, shelter_arrival, load, path_to_shelter
                ))

                # 更新避难所容量
                actual_load = min(load, shelter_remaining[si])
                shelter_remaining[si] -= actual_load

                # 更新巴士状态 → 在避难所, 下轮可返回上车点
                bus_avail[bi] = shelter_arrival
                bus_location[bi] = ("shelter", si)
                bus_current_load[bi] = 0
                max_completion_time = max(max_completion_time,
                                          shelter_arrival)
                bus_trips += 1
            else:
                # 未满且该站还有剩余 → 巴士停留在上车点等待下一批
                bus_avail[bi] = depart_time
                bus_location[bi] = ("stop", j)

        # ── Step 4: 处理停留在上车点的巴士 (强制送往避难所) ──
        for bi in range(n_buses):
            loc_type, loc_idx = bus_location[bi]
            if loc_type == "stop" and bus_current_load[bi] > 1e-6:
                j = loc_idx
                load = bus_current_load[bi]
                si = self._find_shelter(j, load, shelter_remaining)
                travel = self.stop_shelter_dist[j, si] / bus_speed
                depart = bus_avail[bi]
                arrival = depart + travel

                bus_trajectory.append((
                    bi, "stop", j, "shelter", si,
                    depart, arrival, load,
                    self.stop_shelter_path[j][si]
                ))

                shelter_remaining[si] -= min(load, shelter_remaining[si])
                bus_avail[bi] = arrival
                bus_location[bi] = ("shelter", si)
                max_completion_time = max(max_completion_time, arrival)

        # ── Step 5: 汇总 ──
        total_risk = walk_risk + total_queue_risk

        if unevacuated_pop > 0:
            total_risk += unevacuated_pop * cfg.get("inevac_penalty", 1e6)

        if max_completion_time > max_evac:
            overtime = max_completion_time - max_evac
            total_risk += overtime * float(np.sum(pop_arr)) * 0.01

        info = dict(
            n_stops_used=len(stop_data),
            bus_trips_total=bus_trips,
            max_completion_s=max_completion_time,
            unevacuated_pop=unevacuated_pop,
            closure_times={j: float(self.closure_times[j])
                           for j in stop_data},
            queue_risk=total_queue_risk,
            bus_trajectory=bus_trajectory,
        )

        return max_completion_time, total_risk, info

    def _travel_time_to_stop(self, bus_idx, stop_idx, bus_location):
        """计算巴士到上车点的行驶时间 (秒)"""
        loc_type, loc_idx = bus_location[bus_idx]
        if loc_type == "depot":
            dist = self.depot_to_stop_dist[stop_idx]
        elif loc_type == "shelter":
            dist = self.stop_shelter_dist[stop_idx, loc_idx]
        elif loc_type == "stop":
            # 从当前上车点到目标上车点: 经避难所绕行
            # 简化: 直接 Euclidean × 1.5
            sx, sy = self.bus_xy[loc_idx]
            dx, dy = self.bus_xy[stop_idx]
            dist = math.hypot(dx - sx, dy - sy) * 1.5
        else:
            dist = 0.0
        return dist / self.config["bus_speed_ms"]

    def _path_nodes_to_stop(self, bus_idx, stop_idx, bus_location):
        """获取巴士到上车点的路径节点序列"""
        loc_type, loc_idx = bus_location[bus_idx]
        if loc_type == "depot":
            return self.depot_to_stop_path[stop_idx]
        elif loc_type == "shelter":
            return self.stop_shelter_path[stop_idx][loc_idx]
        else:
            return None


# ============================================================
#  辅助函数
# ============================================================
def check_fleet_capacity(assignment, pop_arr, bus_xy, shelter_xy,
                         shelter_capacities,
                         closure_times=None, sink_config=None):
    """粗略检查: 总人口与车队容量是否匹配"""
    cfg = sink_config or SINK_CONFIG
    total_pop = sum(pop_arr[i] for i in range(len(assignment))
                    if assignment[i] != -1)

    if len(bus_xy) > 0 and len(shelter_xy) > 0:
        diff = bus_xy[:, np.newaxis, :] - shelter_xy[np.newaxis, :, :]
        avg_dist = np.sqrt((diff ** 2).sum(axis=2)).mean()
    else:
        avg_dist = cfg.get("shelter_distance_min_m", 30_000)
    round_trip = 2 * avg_dist / cfg["bus_speed_ms"]

    max_trips = max(1, int(
        (cfg["max_evac_duration"] - cfg["dispatch_delay_sec"]) / round_trip) + 1)
    fleet_capacity = cfg["fleet_size"] * cfg["bus_capacity"] * max_trips

    closed_early = 0
    if closure_times is not None:
        for j in range(len(bus_xy)):
            if closure_times[j] <= cfg["dispatch_delay_sec"]:
                closed_early += 1

    return total_pop <= fleet_capacity, dict(
        total_population=total_pop,
        fleet_max_capacity=fleet_capacity,
        max_round_trips=max_trips,
        utilization=total_pop / fleet_capacity if fleet_capacity > 0
        else float("inf"),
        avg_round_trip_s=round_trip,
        stops_closed_before_dispatch=closed_early,
    )
