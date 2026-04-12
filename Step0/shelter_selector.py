"""
Shelter Selector — 多准则避难所选择与分配模块
================================================

本模块基于近 10 年 SCI 文献中的避难所分配最佳实践, 为每个被使用的上车点
从候选避难所集合中选出一个最优转运目的地, 替代原 pickup_sink.py 中固定的
30 km shelter_distance_m 假设。

核心方法: 4 指标加权评分 (类 TOPSIS / P-median 混合)

    score(stop_j → shelter_k)
      = w_dist * d̂(j,k)       # 归一化距离      [Yin 2023 F1, Pereira-Bish 2015]
      + w_cap  * ĉ(k)          # 归一化容量紧度  [Yin 2023 constraint, Sun 2024]
      + w_load * l̂(k)          # 归一化负载利用率 [Song 2024 Gini, Sharbaf 2025]
      + w_risk * r̂(j,k,t)      # 归一化动态剂量  [Miao 2023 DDEEM, Ren 2024]

所有指标均 min-max 归一化到 [0,1], 分数越小越好, 选取 argmin 作为分配目标。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
文献依据 (近 10 年 SCI):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] Yin Y., Zhao X., Lv W. (2023). Emergency shelter allocation
    planning technology for large-scale evacuation based on quantum
    genetic algorithm. Frontiers in Public Health, 10:1098675.
    → 同方法学: 量子遗传算法 + 大规模疏散分配。F1 目标函数
      = 总加权疏散距离; F2 discrete penalty = 分散度惩罚;
      Constraint set = 容量约束。本模块的距离 + 负载均衡指标
      直接对应于该文献的 F1 和 F2 设计。

[2] Miao H., Zhang G., Yu P., Shi C., Zheng J. (2023). Dynamic
    Dose-Based Emergency Evacuation Model for Enhancing Nuclear
    Power Plant Emergency Response Strategies. Energies, 16(17):6338.
    → 核心概念 "moving expected dose": 路径风险由居民到达时刻的
      动态剂量率决定, 而非静态距离。本模块的 risk 指标沿路径
      采样时变风险场, 即 DDEEM 的离散化形式。

[3] Ren Y., Zhang G., Zheng J., Miao H. (2024). An Integrated
    Solution for Nuclear Power Plant On-Site Optimal Evacuation
    Path Planning Based on Atmospheric Dispersion and Dose Model.
    Sustainability, 16(6):2458.
    → Gaussian plume + dose conversion factor + improved Dijkstra,
      报告比最短路径降低 60% 有效剂量。

[4] Song Y. et al. (2024/2025). Research on the optimization of
    urban emergency shelters considering flood disaster risks.
    Taylor & Francis.
    → 多目标优化 NSGA-II 框架, 目标: 加权疏散距离 + 可达性
      不等性 (Gini). 报告 28.80% 的 accessibility inequality 削减。

[5] Sharbaf M., Bélanger V., Cherkesly M., Rancourt M.-E.,
    Toglia G.M. (2025). Risk-based shelter network design in
    flood-prone areas: An application to Haiti. Omega, 131.
    → 明确使用 Gini 系数作为负载均衡目标。

[6] Choi J.S., Kim J.W., Joo H.Y., Moon J.H. (2023). Applying a
    big data analysis to evaluate the suitability of shelter
    locations for the evacuation of residents in case of
    radiological emergencies. Nuclear Engineering and Technology,
    55:261-269.
    → 韩国核电站周边避难所适宜性评估, 提供了核事故场景下多准则
      shelter suitability 评分方法的直接参照。

[7] Sun Y., Yuan T., Chai X., Chen C. (2024). Bus based emergency
    evacuation organization strategy of nuclear power plant planning
    restricted area. Progress in Nuclear Energy, 169:105069.
    → 核电厂规划限制区巴士枢纽选址 + 路径规划联合优化, 容量约束
      与往返时间的建模与本项目一致。

[8] Pereira V.C., Bish D.R. (2015). Scheduling and Routing for a
    Bus-Based Evacuation with a Constant Evacuee Arrival Rate.
    Transportation Science, 49(4):853-867.
    → 排队-暴露耦合 (total exposure = total waiting time) 的奠基性
      工作, 为动态风险沿路径采样提供理论基础。
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import KDTree

# 引用 config 中定义的 SHELTER_CONFIG (权重和阈值的单一来源)
try:
    from config import SHELTER_CONFIG
except ImportError:
    # 独立测试模式的回退默认值
    SHELTER_CONFIG = dict(
        weight_distance = 0.20,
        weight_capacity = 0.20,
        weight_balance  = 0.20,
        weight_risk     = 0.40,
        max_search_distance_m = 60_000,
        min_capacity          = 50,
        capacity_safety_margin = 0.90,
        overflow_penalty       = 10.0,
        risk_sample_points    = 5,
        risk_arrival_stage    = 3,
        allow_reassignment    = True,
        deterministic_seed    = 42,
    )

# 对外暴露别名, 供 main.py 导入
SHELTER_CRITERIA_WEIGHTS = dict(
    distance = SHELTER_CONFIG["weight_distance"],
    capacity = SHELTER_CONFIG["weight_capacity"],
    balance  = SHELTER_CONFIG["weight_balance"],
    risk     = SHELTER_CONFIG["weight_risk"],
)


# ============================================================
#  数据加载
# ============================================================
def load_shelters(shelter_file, target_crs="EPSG:32650"):
    """
    读取避难所 Excel (Number, lon, lat, Capacity) 并投影为 UTM。

    参数:
        shelter_file – Excel 文件路径
        target_crs   – 目标坐标系 (默认 UTM-50N)

    返回:
        shelter_xy    – (n, 2) ndarray, UTM 坐标
        capacities    – (n,) ndarray, 名义容量
        shelter_ids   – (n,) ndarray, 避难所编号
        gdf           – 完整 GeoDataFrame (含几何, 供可视化使用)
    """
    df = pd.read_excel(shelter_file)
    # 统一列名 (大小写容错)
    df.columns = [c.strip() for c in df.columns]
    col_map = {c.lower(): c for c in df.columns}
    need = ["number", "lon", "lat", "capacity"]
    for k in need:
        if k not in col_map:
            raise ValueError(f"Shelter file missing column '{k}', got {list(df.columns)}")

    ids  = df[col_map["number"]].values.astype(int)
    lons = df[col_map["lon"]].values.astype(float)
    lats = df[col_map["lat"]].values.astype(float)
    caps = df[col_map["capacity"]].values.astype(float)

    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(lons, lats), crs="EPSG:4326"
    ).to_crs(target_crs)

    shelter_xy = np.array([(p.x, p.y) for p in gdf.geometry], dtype=np.float64)

    print(f"✅ Shelters loaded: {len(ids)} sites, "
          f"total capacity={int(caps.sum())}, "
          f"range=[{int(caps.min())}, {int(caps.max())}]")
    return shelter_xy, caps, ids, gdf


# ============================================================
#  ShelterSelector 主类
# ============================================================
class ShelterSelector:
    """
    多准则避难所选择器。

    用法 1 — 静态预分配 (默认, 推荐):
        selector = ShelterSelector(shelter_xy, capacities, risk_arrays,
                                   x_mins, y_maxs, config=SHELTER_CONFIG)
        mapping = selector.allocate_static(bus_xy, bus_demands)
        # mapping: {stop_idx: {'shelter_idx', 'shelter_id', 'distance_m',
        #                      'capacity', 'score', 'components'}}
        sink = PickupSinkModel(..., shelter_mapping=mapping)

    用法 2 — 动态每次评估 (需要当前风险场时刻):
        mapping = selector.allocate_dynamic(bus_xy, bus_demands,
                                            arrival_time_sec=600)
        # 与 allocate_static 相同的返回格式,
        # 但 risk 指标使用当前时刻而不是固定阶段

    所有评分都归一化到 [0, 1], 分数越小越好。
    """

    def __init__(self, shelter_xy, capacities, risk_arrays, x_mins, y_maxs,
                 grid_res=400, config=None):
        """
        参数:
            shelter_xy   – (n_shelter, 2) UTM 坐标
            capacities   – (n_shelter,) 名义容量
            risk_arrays  – 4 个阶段的风险矩阵 list
            x_mins       – 各阶段 x_min 空间参照
            y_maxs       – 各阶段 y_max 空间参照
            grid_res     – 风险场网格分辨率 (m)
            config       – SHELTER_CONFIG 字典
        """
        self.shelter_xy  = np.asarray(shelter_xy, dtype=np.float64)
        self.capacities  = np.asarray(capacities, dtype=np.float64)
        self.risk_arrays = risk_arrays
        self.x_mins      = x_mins
        self.y_maxs      = y_maxs
        self.grid_res    = grid_res
        self.config      = config or SHELTER_CONFIG
        self.n_shelter   = len(shelter_xy)

        # 预筛掉容量过小的避难所 (Yin 2023 candidate screening)
        self.valid_mask = self.capacities >= self.config["min_capacity"]
        n_valid = int(self.valid_mask.sum())
        print(f"   ShelterSelector: {n_valid}/{self.n_shelter} shelters "
              f"above min_capacity={self.config['min_capacity']}")

        # KDTree 加速最近邻查询 (仅对有效避难所)
        self._valid_idx = np.where(self.valid_mask)[0]
        self._kdtree = KDTree(self.shelter_xy[self._valid_idx])

    # ─────────────────────────────────────────────────────────
    #  私有工具: 风险查询 (同 pickup_sink._risk_at)
    # ─────────────────────────────────────────────────────────
    def _risk_at(self, x, y, stage_idx):
        """查询指定阶段风险场在 (x,y) 处的值"""
        if stage_idx < 0 or stage_idx >= len(self.risk_arrays):
            return 0.0
        ra = self.risk_arrays[stage_idx]
        xm = self.x_mins[stage_idx]
        ym = self.y_maxs[stage_idx]
        col = int((x - xm) / self.grid_res)
        row = int((ym - y) / self.grid_res)
        if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
            return float(ra[row, col])
        return 0.0

    def _stage_from_time(self, t_sec):
        """按秒数映射到风险阶段索引 (0-3)"""
        t_min = t_sec / 60.0
        if t_min < 15:  return 0
        if t_min < 25:  return 1
        if t_min < 35:  return 2
        return 3

    def _path_risk(self, stop_xy, shelter_xy, stage_idx):
        """
        沿上车点→避难所的直线路径采样风险, 返回平均风险。
        参考 Miao 2023 DDEEM 的 "moving expected dose" 概念 (离散化版本)。

        注: 使用直线采样而不是路网 Dijkstra, 因为:
          1. 避难所距离较远 (km 级), 路网细节对宏观剂量影响不显著;
          2. 评估频率极高 (每代 1700+ 次), 路网查询太慢;
          3. Miao 2023 的 improved A* 也是在粗粒度网格上运行, 与本做法等效。
        """
        n_pts = self.config["risk_sample_points"]
        ts = np.linspace(0, 1, n_pts)
        total = 0.0
        for t in ts:
            x = stop_xy[0] + t * (shelter_xy[0] - stop_xy[0])
            y = stop_xy[1] + t * (shelter_xy[1] - stop_xy[1])
            total += self._risk_at(x, y, stage_idx)
        return total / n_pts

    def _score_one_stop(self, stop_xy, stop_demand, current_utilization,
                        stage_idx, candidate_idx_in_valid):
        """
        对单个上车点, 在候选避难所集合中计算 4 指标评分, 返回最优选择。

        参数:
            stop_xy              – (2,) 上车点 UTM 坐标
            stop_demand          – float, 该上车点需转运的总人口
            current_utilization  – (n_valid,) 各避难所已占用比例 [0,1]
            stage_idx            – 用于风险查询的阶段索引
            candidate_idx_in_valid – 允许评分的有效避难所子集 (距离预筛后)

        返回:
            best_local_idx – 在 candidate_idx_in_valid 中的位置
            components     – dict 包含 4 个原始分量和总分, 供调试
        """
        if len(candidate_idx_in_valid) == 0:
            return None, None

        cfg = self.config
        # ─── 原始 4 指标 (未归一化) ───
        n = len(candidate_idx_in_valid)
        dist_raw = np.empty(n)
        cap_raw  = np.empty(n)
        load_raw = np.empty(n)
        risk_raw = np.empty(n)

        for k, local_idx in enumerate(candidate_idx_in_valid):
            global_idx = self._valid_idx[local_idx]
            sx, sy = self.shelter_xy[global_idx]

            # 1. 距离 (Yin 2023 F1): 欧氏距离 (m)
            dist_raw[k] = np.hypot(sx - stop_xy[0], sy - stop_xy[1])

            # 2. 容量紧度 (Yin 2023 constraint):
            #    remaining = capacity*(1-utilization) - stop_demand
            #    若 remaining < 0, 超载; 指标用 stop_demand / available
            available = self.capacities[global_idx] * \
                        (1 - current_utilization[local_idx]) * \
                        cfg["capacity_safety_margin"]
            if available <= 0:
                cap_raw[k] = cfg["overflow_penalty"]  # 极大值, 实际上视为不可选
            else:
                cap_raw[k] = stop_demand / available  # [0, overflow_penalty]

            # 3. 负载均衡 (Song 2024 Gini, Sharbaf 2025):
            #    使用当前利用率本身; 分配到低利用率的避难所会降低 Gini
            load_raw[k] = current_utilization[local_idx]

            # 4. 动态风险 (Miao 2023 DDEEM):
            #    沿 stop→shelter 直线采样时变风险场
            risk_raw[k] = self._path_risk(
                stop_xy, self.shelter_xy[global_idx], stage_idx)

        # ─── min-max 归一化到 [0, 1] ───
        def _norm(a):
            lo, hi = a.min(), a.max()
            if hi - lo < 1e-12:
                return np.zeros_like(a)
            return (a - lo) / (hi - lo)

        dist_n = _norm(dist_raw)
        cap_n  = _norm(np.clip(cap_raw, 0, cfg["overflow_penalty"]))
        load_n = _norm(load_raw)
        risk_n = _norm(risk_raw)

        # ─── 加权总分 ───
        score = (cfg["weight_distance"] * dist_n +
                 cfg["weight_capacity"] * cap_n +
                 cfg["weight_balance"]  * load_n +
                 cfg["weight_risk"]     * risk_n)

        # 容量超载硬惩罚: cap_raw 达到 overflow_penalty 的候选直接置为 inf
        score = np.where(cap_raw >= cfg["overflow_penalty"], np.inf, score)

        if np.all(np.isinf(score)):
            # 所有候选都超载, 返回容量最大的 (退化策略)
            best = int(np.argmax(
                [self.capacities[self._valid_idx[k]] for k in candidate_idx_in_valid]))
        else:
            best = int(np.argmin(score))

        components = dict(
            distance_m = float(dist_raw[best]),
            capacity_ratio = float(cap_raw[best]),
            load_util = float(load_raw[best]),
            risk_avg = float(risk_raw[best]),
            total_score = float(score[best]) if np.isfinite(score[best]) else None,
            n_candidates = n,
        )
        return best, components

    # ─────────────────────────────────────────────────────────
    #  公开方法: 静态预分配 (推荐, 默认模式)
    # ─────────────────────────────────────────────────────────
    def allocate_static(self, bus_xy, bus_demands):
        """
        静态预分配: 对每个上车点分配一个最优避难所。

        该方法在 Q-NSGA-II 优化开始前调用一次, 生成 stop → shelter 映射
        供 PickupSinkModel 在整个进化过程中使用。风险指标使用
        config["risk_arrival_stage"] 对应的固定阶段, 因此 "静态"。

        参数:
            bus_xy       – (n_bus, 2) 上车点 UTM 坐标
            bus_demands  – (n_bus,) 每个上车点需要疏散的总人口
                           (可用所有居民人口总和的粗略估计)

        返回:
            mapping – dict[stop_idx → dict] 包含:
                'shelter_idx': int   (self.shelter_xy 全局索引)
                'shelter_id':  int   (原 Excel 中 Number 列)
                'distance_m':  float
                'capacity':    float
                'components':  dict  (4 个原始指标)
                'score':       float
        """
        cfg = self.config
        stage_idx = cfg["risk_arrival_stage"]
        bus_xy = np.asarray(bus_xy, dtype=np.float64)
        bus_demands = np.asarray(bus_demands, dtype=np.float64)
        n_bus = len(bus_xy)

        # 跟踪每个避难所的当前利用率 (随分配进行更新)
        current_util = np.zeros(len(self._valid_idx), dtype=np.float64)

        # 按需求降序分配 (需求大的上车点先选, 避免小需求占满大避难所)
        order = np.argsort(-bus_demands)

        mapping = {}
        unassigned = []
        rng = np.random.default_rng(cfg["deterministic_seed"])

        for stop_j in order:
            demand = bus_demands[stop_j]
            if demand <= 0:
                continue  # 该上车点无居民, 跳过

            # 距离预筛: 只考虑 max_search_distance_m 半径内的避难所
            dists, knn_idx = self._kdtree.query(
                bus_xy[stop_j], k=min(50, len(self._valid_idx)))
            candidate_local = knn_idx[dists <= cfg["max_search_distance_m"]]
            if len(candidate_local) == 0:
                # 扩展搜索: 取最近的 K 个 (无论距离)
                candidate_local = knn_idx[:10]

            best_local, comps = self._score_one_stop(
                bus_xy[stop_j], demand, current_util,
                stage_idx, candidate_local)

            if best_local is None:
                unassigned.append(stop_j)
                continue

            chosen_local = int(candidate_local[best_local])
            chosen_global = int(self._valid_idx[chosen_local])

            # 更新该避难所的利用率
            cap = self.capacities[chosen_global]
            current_util[chosen_local] += demand / max(cap, 1.0)
            current_util[chosen_local] = min(current_util[chosen_local], 1.0)

            mapping[int(stop_j)] = dict(
                shelter_idx = chosen_global,
                shelter_id  = int(chosen_global),  # 与 Excel 中 Number 一致
                distance_m  = comps["distance_m"],
                capacity    = float(cap),
                components  = comps,
                score       = comps["total_score"],
                stage_used  = stage_idx,
            )

        # 未分配的上车点: 使用最近可用避难所 (退化策略)
        for stop_j in unassigned:
            d, k = self._kdtree.query(bus_xy[stop_j], k=1)
            chosen_local = int(k)
            chosen_global = int(self._valid_idx[chosen_local])
            mapping[int(stop_j)] = dict(
                shelter_idx = chosen_global,
                shelter_id  = int(chosen_global),
                distance_m  = float(d),
                capacity    = float(self.capacities[chosen_global]),
                components  = dict(distance_m=float(d), capacity_ratio=0,
                                   load_util=1.0, risk_avg=0,
                                   total_score=None, n_candidates=1),
                score       = None,
                stage_used  = stage_idx,
                fallback    = True,
            )

        # 诊断统计
        all_dists = [m["distance_m"] for m in mapping.values()]
        if all_dists:
            print(f"   Static allocation: {len(mapping)}/{n_bus} stops assigned, "
                  f"dist mean={np.mean(all_dists)/1000:.1f}km "
                  f"max={np.max(all_dists)/1000:.1f}km")
            n_fallback = sum(1 for m in mapping.values() if m.get("fallback"))
            if n_fallback > 0:
                print(f"   ⚠️  {n_fallback} stops used fallback (nearest) allocation")
        return mapping

    # ─────────────────────────────────────────────────────────
    #  公开方法: 动态每次评估
    # ─────────────────────────────────────────────────────────
    def allocate_dynamic(self, bus_xy, bus_demands, arrival_time_sec):
        """
        动态分配: 根据指定的到达时刻选择风险阶段, 然后运行与 static 相同的流程。

        此方法可在 NSGA-II 的 evaluate 内部调用, 但会显著拖慢优化速度 (5-10x)。
        推荐仅用于:
          - 敏感性分析 (对比 static vs dynamic)
          - 最终最优解的精细化评估 (compute_metrics 阶段)

        参数:
            bus_xy           – (n_bus, 2) 上车点坐标
            bus_demands      – (n_bus,) 上车点需求
            arrival_time_sec – 预期到达避难所的时刻 (秒)
        """
        # 临时覆盖 risk_arrival_stage, 然后调用 static
        orig_stage = self.config["risk_arrival_stage"]
        try:
            self.config = dict(self.config)
            self.config["risk_arrival_stage"] = self._stage_from_time(
                arrival_time_sec)
            return self.allocate_static(bus_xy, bus_demands)
        finally:
            self.config["risk_arrival_stage"] = orig_stage

    # ─────────────────────────────────────────────────────────
    #  公开方法: 多候选静态预分配 (改进 1, v5.3)
    # ─────────────────────────────────────────────────────────
    def allocate_static_multi(self, bus_xy, bus_demands, top_k=50,
                              shelter_path_lengths=None):
        """
        为每个上车点生成 Top K 个候选避难所的有序列表 (按 4 指标评分升序)。
        替代 allocate_static() 的 1-to-1 映射，用于多避难所级联调度。

        与 allocate_static 的差异:
          1. 不再为每个上车点独立"占用"容量 (current_util 不更新)
             — 因为级联调度的容量竞争发生在 sink 仿真中, 这里只给排序
          2. 返回完整的 Top K 候选列表, 不只是首选
          3. 支持传入预计算的真实路网距离 (shelter_path_lengths)
             代替欧氏直线距离

        参数:
            bus_xy               – (n_bus, 2) 上车点 UTM 坐标
            bus_demands          – (n_bus,) 上车点需求 (用于 capacity score)
            top_k                – 每个上车点保留的候选数 (默认 50)
            shelter_path_lengths – 可选的 (n_bus, n_shelter_global) 真实路网距离矩阵
                                   None 时使用欧氏距离
                                   注意第二维是全局 shelter 索引 (0..n_shelter-1),
                                   不是 valid_idx 子集索引

        返回:
            mapping – dict[stop_idx → dict] 包含:
                'candidates': list of dict, 每个包含
                    'shelter_idx': int  全局避难所索引
                    'shelter_id':  int  Excel Number 列
                    'distance_m':  float (欧氏 或 真实路网)
                    'capacity':    float
                    'score':       float (4 指标加权总分)
                'shelter_idx': int  首选 (= candidates[0]['shelter_idx'])
                'distance_m':  float (= candidates[0]['distance_m'])
        """
        cfg = self.config
        stage_idx = cfg["risk_arrival_stage"]
        bus_xy = np.asarray(bus_xy, dtype=np.float64)
        bus_demands = np.asarray(bus_demands, dtype=np.float64)
        n_bus = len(bus_xy)

        if shelter_path_lengths is not None:
            shelter_path_lengths = np.asarray(shelter_path_lengths, dtype=np.float64)
            if shelter_path_lengths.shape != (n_bus, self.n_shelter):
                raise ValueError(
                    f"shelter_path_lengths shape mismatch: "
                    f"expected ({n_bus}, {self.n_shelter}), "
                    f"got {shelter_path_lengths.shape}")

        mapping = {}
        # 注: 不维护 current_util, 因为多上车点级联场景下"占用"由 sink 仿真处理

        for stop_j in range(n_bus):
            demand = bus_demands[stop_j]
            if demand <= 0:
                # 该上车点无居民, 仍给一个最近的避难所做退化
                d, k = self._kdtree.query(bus_xy[stop_j], k=1)
                global_idx = int(self._valid_idx[int(k)])
                mapping[int(stop_j)] = dict(
                    candidates=[dict(
                        shelter_idx=global_idx,
                        shelter_id=global_idx,
                        distance_m=float(d),
                        capacity=float(self.capacities[global_idx]),
                        score=0.0,
                    )],
                    shelter_idx=global_idx,
                    distance_m=float(d),
                )
                continue

            # ── 候选筛选 ──
            # 用 KDTree 取所有有效避难所中欧氏意义下最近的 K_search 个
            k_search = min(max(top_k * 2, 100), len(self._valid_idx))
            _, knn_idx = self._kdtree.query(bus_xy[stop_j], k=k_search)
            # knn_idx 是在 _valid_idx (567) 中的下标
            candidate_local = np.asarray(knn_idx, dtype=np.int64)

            n_cand = len(candidate_local)
            if n_cand == 0:
                continue

            # ── 4 指标原始值 ──
            dist_raw = np.empty(n_cand)
            cap_raw  = np.empty(n_cand)
            load_raw = np.zeros(n_cand)  # 多候选模式下不预扣容量, load = 0
            risk_raw = np.empty(n_cand)

            for k, local_idx in enumerate(candidate_local):
                global_idx = int(self._valid_idx[int(local_idx)])
                sx, sy = self.shelter_xy[global_idx]

                # 距离 — 优先用真实路网, 回退欧氏
                if shelter_path_lengths is not None:
                    d_real = shelter_path_lengths[stop_j, global_idx]
                    if np.isfinite(d_real):
                        dist_raw[k] = d_real
                    else:
                        dist_raw[k] = np.hypot(sx - bus_xy[stop_j, 0],
                                                sy - bus_xy[stop_j, 1])
                else:
                    dist_raw[k] = np.hypot(sx - bus_xy[stop_j, 0],
                                            sy - bus_xy[stop_j, 1])

                # 容量紧度 (基于名义容量, 因为不知道其他上车点会占多少)
                avail = self.capacities[global_idx] * cfg["capacity_safety_margin"]
                if avail <= 0:
                    cap_raw[k] = cfg["overflow_penalty"]
                else:
                    cap_raw[k] = demand / avail

                # 风险 (沿路径采样, 改进 3 后这里也可以用真实路径采样, 但保持简洁)
                risk_raw[k] = self._path_risk(
                    bus_xy[stop_j], self.shelter_xy[global_idx], stage_idx)

            # ── min-max 归一化 ──
            def _norm(a):
                lo, hi = a.min(), a.max()
                if hi - lo < 1e-12:
                    return np.zeros_like(a)
                return (a - lo) / (hi - lo)

            dist_n = _norm(dist_raw)
            cap_n  = _norm(np.clip(cap_raw, 0, cfg["overflow_penalty"]))
            load_n = load_raw  # 全 0
            risk_n = _norm(risk_raw)

            score = (cfg["weight_distance"] * dist_n +
                     cfg["weight_capacity"] * cap_n +
                     cfg["weight_balance"]  * load_n +
                     cfg["weight_risk"]     * risk_n)

            # ── 按评分升序排序, 取 Top K ──
            order = np.argsort(score)[:top_k]

            candidates = []
            for k in order:
                local_idx = int(candidate_local[k])
                global_idx = int(self._valid_idx[local_idx])
                candidates.append(dict(
                    shelter_idx=global_idx,
                    shelter_id=global_idx,
                    distance_m=float(dist_raw[k]),
                    capacity=float(self.capacities[global_idx]),
                    score=float(score[k]),
                ))

            mapping[int(stop_j)] = dict(
                candidates=candidates,
                shelter_idx=candidates[0]["shelter_idx"],
                distance_m=candidates[0]["distance_m"],
            )

        # 诊断
        all_first_dist = [m["distance_m"] for m in mapping.values()]
        if all_first_dist:
            print(f"   Multi-shelter alloc: {len(mapping)} stops, "
                  f"top1 dist mean={np.mean(all_first_dist)/1000:.1f}km "
                  f"max={np.max(all_first_dist)/1000:.1f}km, "
                  f"top_k={top_k}")
        return mapping


# ============================================================
#  独立测试入口
# ============================================================
if __name__ == "__main__":
    """独立测试: 加载 Excel 并跑一次 static 分配 (无 Q-NSGA-II)"""
    import sys
    xlsx = sys.argv[1] if len(sys.argv) > 1 else "Shelter_with_coords.xlsx"

    shelter_xy, caps, ids, gdf = load_shelters(xlsx)

    # 虚拟: 5 个上车点, 每个 500 人, 4 个空风险场
    bus_xy = shelter_xy[:5] + np.random.randn(5, 2) * 500
    bus_demands = np.full(5, 500.0)
    dummy_risk = [np.zeros((100, 100)) for _ in range(4)]
    dummy_xm = [shelter_xy[:, 0].min()] * 4
    dummy_ym = [shelter_xy[:, 1].max()] * 4

    sel = ShelterSelector(shelter_xy, caps, dummy_risk, dummy_xm, dummy_ym)
    mapping = sel.allocate_static(bus_xy, bus_demands)
    for j, info in mapping.items():
        print(f"  stop {j} → shelter {info['shelter_id']}  "
              f"dist={info['distance_m']/1000:.2f}km  "
              f"cap={info['capacity']:.0f}")
