"""
上车点 Sink 边界条件模块 (Pickup Point Sink Model)
=================================================

本模块为疏散模型补充以下科学边界条件，以解决"居民走到上车点即视为疏散完成"
的失真问题：

  1. 上车点 NOT 是无限容量 sink：居民到达后需排队等待巴士
  2. 巴士从调度中心派出，有派车延迟和往返时间
  3. 巴士有座位容量上限，超载者继续排队
  4. 排队期间居民持续暴露于辐射场
  5. 总疏散时间 = 步行时间 + 排队时间 + 车载时间

文献依据 (SCI, 2020-2024):
─────────────────────────────────────────────────────────────────────
[1] Zhao X. et al. "A round-trip bus evacuation model with scheduling
    and routing planning." Transportation Research Part A: Policy
    and Practice, Vol. 137, 2020, pp. 285-300.
    → 明确将 in-bus travel time + waiting time at pickup points
      纳入目标函数, 指出"acceptable waiting time of evacuees at
      pick-up points is usually ignored in existing models, which
      reduces the practical value of the models."

[2] Sun Y., Chai X., Chen C. "Bus based emergency evacuation
    organization strategy of nuclear power plant planning restricted
    area." Progress in Nuclear Energy, Vol. 169, 2024, 105069.
    → 针对核电厂规划限制区, 双层优化 hub 选址 + 路径规划,
      约束含巴士容量、车队规模、往返次数。

[3] Bish D.R., Sherali H.D., Hobeika A.G. "Optimal evacuation
    planning using staging and routing." Journal of the Operational
    Research Society, Vol. 65(1), 2014, pp. 124-140.
    → 引入 constant evacuee arrival rate at pickup locations,
      minimize total exposure (= total waiting time).

[4] Zhao H., Feng S., Ci Y. "Scheduling a bus fleet for evacuation
    planning using stop-skipping method." Transportation Research
    Record, Vol. 2675(11), 2021, pp. 559-571.
    → 明确指出 "sudden passenger demand at a bus stop can lead
      to numerous passengers gathering at the stop", 排队时间是
      疏散效率的关键瓶颈。

[5] Goerigk M., Grün B. "A robust bus evacuation model with delayed
    scenario information." OR Spectrum, Vol. 36, 2014, pp. 923-948.
    → 给出巴士派遣延迟与不确定性的标准化建模框架。

边界条件参数取值依据:
─────────────────────────────────────────────────────────────────────
- 标准公交载客 50 人 (中国 GB/T 19260-2018 城市公共汽车标准)
- 校车/疏散巴士可达 60-80 人 (NRC NUREG/CR-7269, 2020)
- 公交平均行驶速度 30-40 km/h (城市道路, Sun et al. 2024)
- 调度响应延迟 5-15 分钟 (REP Manual 2023, FEMA)
- 单次乘客上车时间 ≈ 2-3 秒/人 (HCM 6th Edition, TRB 2016)
"""

import numpy as np
import math
from collections import defaultdict

# ============================================================
#  配置参数 (建议加入 config.py)
# ============================================================
SINK_CONFIG = dict(
    # ─── 巴士车队 ───
    bus_capacity         = 50,      # 单辆巴士容量 (人)
    fleet_size           = 30,      # 调度中心可用巴士总数
    bus_speed_kmh        = 30.0,    # 巴士平均行驶速度 (km/h)
    bus_speed_ms         = 30 * 1000 / 3600,   # 转 m/s ≈ 8.33

    # ─── 调度参数 ───
    dispatch_delay_sec   = 600,     # 调度响应延迟 (秒) ≈ 10 min
    boarding_time_per_pax = 2.5,    # 单人上车时间 (秒)

    # ─── 转运目的地 ───
    shelter_distance_m   = 30_000,  # 上车点到长期避难所距离 (m, 默认30km)
                                    # 实际可按 bus_stop → nearest shelter 计算
    # ─── 排队期间风险计算 ───
    queue_risk_enabled   = True,    # 是否计算排队期间的辐射暴露
    queue_dt_sec         = 60,      # 排队仿真时间步 (秒)

    # ─── 总疏散时长上限 ───
    max_evac_duration    = 7200,    # 7200s = 2小时 (从事故起至全部撤离)
)


# ============================================================
#  PickupSinkModel — 上车点 sink 边界条件主类
# ============================================================
class PickupSinkModel:
    """
    上车点 sink 边界条件模型。

    给定一个分配方案 ind = [j_0, ..., j_{N-1}] (居民→上车点),
    本模型计算考虑巴士容量、调度延迟、排队等待后的:
        - total_evacuation_time: 全员撤离的实际总时间
        - total_queue_risk: 排队期间累积的辐射暴露
        - bus_trips: 每个上车点需要的巴士往返次数
        - max_queue_length: 各上车点的最大排队人数

    适配方式: 在 evaluate(ind) 内部调用本类的 process(ind) 方法,
              将其结果叠加到原有的 (walk_time, walk_risk) 上。

    使用示例:
        sink = PickupSinkModel(bus_xy, risk_arrays, x_mins, y_maxs, SINK_CONFIG)
        T_total, R_total, info = sink.process(ind, arrival_times, pop_arr)
    """

    def __init__(self, bus_xy, risk_arrays, x_mins, y_maxs, config=None,
                 shelter_mapping=None, shelter_mapping_multi=None,
                 shelter_capacities=None):
        """
        参数:
            bus_xy                – (n_bus, 2) 上车点坐标 (UTM)
            risk_arrays           – list of 4 ndarray, 4 个时间阶段的风险矩阵
            x_mins                – 各阶段风险矩阵 x_min
            y_maxs                – 各阶段风险矩阵 y_max
            config                – SINK_CONFIG 字典
            shelter_mapping       – (legacy v5.1) dict {stop_idx: {'distance_m': ...}}
                                    1-to-1 静态分配, 每个上车点固定一个避难所。
            shelter_mapping_multi – (新 v5.3) dict {stop_idx: {'candidates': [
                                        {'shelter_idx', 'distance_m', 'capacity', ...},
                                        ...
                                    ]}}, 多候选级联调度。
                                    与 shelter_mapping 互斥, 不可同时提供。
            shelter_capacities    – (n_shelters,) ndarray, 全部避难所的名义容量。
                                    多候选模式下用于初始化全局共享的剩余容量计数器。
                                    None 时多候选模式失效, 退化为 1-to-1。
        """
        self.bus_xy = np.asarray(bus_xy, dtype=np.float64)
        self.risk_arrays = risk_arrays
        self.x_mins = x_mins
        self.y_maxs = y_maxs
        self.config = config or SINK_CONFIG
        self.n_bus = len(bus_xy)

        # ── 模式判定: 多候选 vs 1-to-1 ──
        if shelter_mapping_multi is not None and shelter_capacities is not None:
            self.mode = "multi"
            self.shelter_mapping_multi = shelter_mapping_multi
            self.shelter_capacities_nominal = np.asarray(
                shelter_capacities, dtype=np.float64)
            self.shelter_distances = None  # not used in multi mode
        elif shelter_mapping is not None:
            self.mode = "single"
            self.shelter_distances = {
                int(j): float(info["distance_m"])
                for j, info in shelter_mapping.items()
            }
            self.shelter_mapping = shelter_mapping
        else:
            self.mode = "fixed"
            self.shelter_distances = None

    # ─────────────────────────────────────────────────────
    #  主入口
    # ─────────────────────────────────────────────────────
    def process(self, assignment, arrival_times, pop_arr,
                walk_risk=0.0, grid_res=400):
        """
        计算考虑 sink 边界条件后的总时间与总风险。

        参数:
            assignment    – list[int], assignment[i] = 居民i的上车点编号
            arrival_times – ndarray (N,), 居民i到达上车点的时刻 (秒)
                            = walk_time_i (从家步行至上车点的时间)
            pop_arr       – ndarray (N,), 居民i所代表的人口数
            walk_risk     – float, 第一阶段(步行)的累积风险
            grid_res      – 风险场网格分辨率 (m)

        返回:
            total_time    – float, 总疏散时间 (秒)
                            = max(arrival) + queue_wait + bus_transit
            total_risk    – float, 总风险 = walk_risk + queue_risk
            info          – dict, 详细统计信息
        """
        cfg = self.config
        n = len(assignment)

        # ── Step 1: 按上车点分组居民到达事件 ──
        bus_arrivals = defaultdict(list)
        for i in range(n):
            j = assignment[i]
            if j == -1 or j is None:
                continue
            t = arrival_times[i]
            if not np.isfinite(t):
                continue
            bus_arrivals[j].append((float(t), float(pop_arr[i])))

        # ── Step 2: 根据模式分派 ──
        if self.mode == "multi":
            (max_completion_time, total_queue_risk, info_extra
             ) = self._simulate_global_scheduler(bus_arrivals, grid_res)
            bus_trips_per_stop = info_extra["bus_trips_per_stop"]
            max_queue_per_stop = info_extra["max_queue_per_stop"]
            shelter_load = info_extra["shelter_load"]
            n_overflow_passengers = info_extra["n_overflow_passengers"]
        else:
            # Legacy: 每个上车点独立仿真 (single 或 fixed 模式)
            max_completion_time = 0.0
            total_queue_risk = 0.0
            bus_trips_per_stop = {}
            max_queue_per_stop = {}
            for j, arr_list in bus_arrivals.items():
                arr_list.sort(key=lambda x: x[0])
                completion, q_risk, n_trips, max_q = self._simulate_queue_at_stop(
                    stop_idx=j, arrivals=arr_list, grid_res=grid_res)
                if completion > max_completion_time:
                    max_completion_time = completion
                total_queue_risk += q_risk
                bus_trips_per_stop[j] = n_trips
                max_queue_per_stop[j] = max_q
            shelter_load = None
            n_overflow_passengers = 0

        # ── Step 3: 总时长软惩罚 ──
        if max_completion_time > cfg["max_evac_duration"]:
            penalty = (max_completion_time - cfg["max_evac_duration"]) * pop_arr.sum()
            total_queue_risk += penalty * 0.01

        info = dict(
            n_stops_used      = len(bus_arrivals),
            bus_trips_total   = sum(bus_trips_per_stop.values()),
            max_completion_s  = max_completion_time,
            max_queue_length  = max(max_queue_per_stop.values()) if max_queue_per_stop else 0,
            queue_risk        = total_queue_risk,
            bus_trips_per_stop = bus_trips_per_stop,
            mode              = self.mode,
            shelter_load      = shelter_load,
            n_overflow_passengers = n_overflow_passengers,
        )

        total_time = max_completion_time
        total_risk = walk_risk + total_queue_risk
        return total_time, total_risk, info

    # ─────────────────────────────────────────────────────
    #  v5.3 全局事件调度 (改进 1: 多避难所级联)
    # ─────────────────────────────────────────────────────
    def _simulate_global_scheduler(self, bus_arrivals, grid_res):
        """
        全局事件驱动调度: 所有上车点共享避难所容量, 按时间顺序竞争。

        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        关键设计 (按用户 2026-04-09 决策确认):

        1. **共享容量**: shelter_remaining[k] 全局共享, 多个上车点的车辆
           竞争同一避难所容量。先到先得 (按出发时刻排序)。

        2. **预订模式**: 巴士在【出发瞬间】扣减目标避难所容量 (不是到达时
           扣减), 避免多车在路上同时假设有空位的冲突。
           依据: Pereira & Bish 2015 standard practice.

        3. **级联降级**: 巴士派出前遍历该上车点的 candidates 列表, 选择
           第一个 remaining_cap >= load 的避难所。

        4. **退化策略 (A)**: 全部 Top K 候选都满载时, 强制送往首选避难所,
           overflow_count += load (软惩罚, 与现有 overflow_penalty 一致)。

        5. **部分装载**: 队列剩余 < cap 时部分装载, 不等满。
           (本来已经实现, 此处保持)

        6. **每车独立 round_trip**: 因为同一上车点不同班次可能去不同避难所,
           round_trip_time 必须每辆车独立计算。
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        参数:
            bus_arrivals – dict {stop_idx: [(arrival_time, pop_count), ...]}
                           每个居民到达上车点的事件列表
            grid_res     – 风险场网格分辨率

        返回:
            max_completion_time – 全局最晚完成时刻 (秒)
            total_queue_risk    – 全局排队风险累积
            info_extra          – dict 包含 per-stop 统计 + shelter 负载分布
        """
        import heapq

        cfg = self.config
        bus_speed = cfg["bus_speed_ms"]
        cap_bus = cfg["bus_capacity"]
        boarding_per_pax = cfg["boarding_time_per_pax"]
        first_bus_delay = cfg["dispatch_delay_sec"]
        risk_enabled = cfg["queue_risk_enabled"]

        # ── 全局共享: 剩余避难所容量 ──
        # 每次调用 process 时重置 (关键! evaluate 是无状态的)
        shelter_remaining = self.shelter_capacities_nominal.copy() * \
                            cfg.get("shelter_safety_margin", 0.95)
        shelter_load = defaultdict(float)  # 累计装载, 供后续 Gini 计算

        # ── 每个上车点的状态 ──
        # state[j] = dict(queue=[(arr_time, remaining_pop)], idx_next=0,
        #                 last_event=0, max_queue=0, queue_risk=0, n_trips=0,
        #                 completion=0, arrivals=[...sorted...])
        state = {}
        for j, arr_list in bus_arrivals.items():
            arr_list = sorted(arr_list, key=lambda x: x[0])
            state[j] = dict(
                queue=[],
                arrivals=arr_list,
                idx_next=0,
                last_event=0.0,
                max_queue=0.0,
                queue_risk=0.0,
                n_trips=0,
                completion=0.0,
            )

        # ── 全局事件堆 ──
        # event = (time, event_type, stop_j)
        # event_type: 'arrival' (居民到达上车点) | 'bus' (巴士到达上车点)
        # 注: 居民事件不入堆, 因为可以从 state[j].arrivals 顺序获取;
        #     入堆的只有 bus 事件
        event_heap = []
        # 初始化: 每个有居民的上车点派一辆首班车
        for j in state:
            heapq.heappush(event_heap, (first_bus_delay, j))

        n_overflow_passengers = 0
        max_completion_time = 0.0
        total_queue_risk = 0.0

        # 主循环: 处理 bus 事件, 内部消化中间所有居民到达事件
        while event_heap:
            bus_time, stop_j = heapq.heappop(event_heap)
            st = state[stop_j]

            # ── 推进该上车点的居民到达, 直到 bus_time ──
            # 在 [last_event, bus_time] 区间内累积排队风险
            cur_time = st["last_event"]
            arr = st["arrivals"]
            bx, by = self.bus_xy[stop_j]

            while st["idx_next"] < len(arr) and arr[st["idx_next"]][0] <= bus_time:
                a_time, a_pop = arr[st["idx_next"]]

                # 区间内累积风险
                dt = a_time - cur_time
                if dt > 0 and st["queue"] and risk_enabled:
                    qpop = sum(q[1] for q in st["queue"])
                    if qpop > 0:
                        rv = self._risk_at(bx, by, cur_time, grid_res)
                        st["queue_risk"] += rv * qpop * dt
                cur_time = a_time

                # 居民加入队列
                st["queue"].append([a_time, a_pop])
                cur_q = sum(q[1] for q in st["queue"])
                if cur_q > st["max_queue"]:
                    st["max_queue"] = cur_q
                st["idx_next"] += 1

            # 推进到 bus_time
            dt = bus_time - cur_time
            if dt > 0 and st["queue"] and risk_enabled:
                qpop = sum(q[1] for q in st["queue"])
                if qpop > 0:
                    rv = self._risk_at(bx, by, cur_time, grid_res)
                    st["queue_risk"] += rv * qpop * dt
            cur_time = bus_time

            # ── 装载: 部分装载, 不等满 ──
            load = 0.0
            while st["queue"] and load < cap_bus:
                take = min(cap_bus - load, st["queue"][0][1])
                st["queue"][0][1] -= take
                load += take
                if st["queue"][0][1] <= 1e-6:
                    st["queue"].pop(0)

            if load <= 0:
                # 队列空, 检查是否还有未到达的居民
                if st["idx_next"] < len(arr):
                    # 还有居民未到达, 等到下一个居民到达再派车
                    next_arr = arr[st["idx_next"]][0]
                    heapq.heappush(event_heap, (next_arr, stop_j))
                    st["last_event"] = cur_time
                    continue
                else:
                    # 该上车点彻底完成
                    st["completion"] = max(st["completion"], cur_time)
                    if cur_time > max_completion_time:
                        max_completion_time = cur_time
                    st["last_event"] = cur_time
                    continue

            # ── 选目标避难所 (Top K 级联) ──
            cand_list = self.shelter_mapping_multi.get(stop_j, {}).get("candidates", [])
            chosen = None
            for cand in cand_list:
                shel_idx = cand["shelter_idx"]
                if shelter_remaining[shel_idx] >= load:
                    chosen = cand
                    shelter_remaining[shel_idx] -= load  # 预订模式
                    break
            if chosen is None:
                # 退化策略 (A): 强制送往首选, overflow 软惩罚
                if cand_list:
                    chosen = cand_list[0]
                    n_overflow_passengers += int(round(load))
                    # 不扣容量 (已为负或无意义), 只记录 overflow
                else:
                    # 罕见: 该上车点无候选避难所, 跳过
                    st["last_event"] = cur_time
                    continue

            # ── 装载耗时 + 上车期间风险 ──
            board_dur = load * boarding_per_pax
            if risk_enabled and board_dur > 0:
                residual_q = sum(q[1] for q in st["queue"]) + load
                rv = self._risk_at(bx, by, cur_time, grid_res)
                st["queue_risk"] += rv * residual_q * board_dur

            depart_time = cur_time + board_dur
            travel_one_way = chosen["distance_m"] / bus_speed
            arrival_at_shelter = depart_time + travel_one_way
            return_to_stop = arrival_at_shelter + travel_one_way

            # 累计 shelter 负载 (供 Gini 统计)
            shelter_load[chosen["shelter_idx"]] += load
            st["n_trips"] += 1
            st["last_event"] = depart_time
            st["completion"] = max(st["completion"], arrival_at_shelter)
            if arrival_at_shelter > max_completion_time:
                max_completion_time = arrival_at_shelter

            # ── 派下一班车 (该上车点的下一班) ──
            # 条件: 队列还有人 OR 还有居民未到达
            still_has_work = bool(st["queue"]) or st["idx_next"] < len(arr)
            if still_has_work:
                heapq.heappush(event_heap, (return_to_stop, stop_j))

        # ── 汇总 ──
        bus_trips_per_stop = {j: state[j]["n_trips"] for j in state}
        max_queue_per_stop = {j: state[j]["max_queue"] for j in state}
        for j in state:
            total_queue_risk += state[j]["queue_risk"]

        info_extra = dict(
            bus_trips_per_stop = bus_trips_per_stop,
            max_queue_per_stop = max_queue_per_stop,
            shelter_load       = dict(shelter_load),
            n_overflow_passengers = n_overflow_passengers,
        )
        return max_completion_time, total_queue_risk, info_extra

    # ─────────────────────────────────────────────────────
    #  单个上车点的离散事件排队仿真
    # ─────────────────────────────────────────────────────
    def _simulate_queue_at_stop(self, stop_idx, arrivals, grid_res):
        """
        对单个上车点 j 运行离散事件仿真:
          - 居民按 arrivals 列表的顺序陆续到达
          - 巴士按 dispatch_delay 后开始往返
          - 每辆巴士装满 bus_capacity 后离开
          - 排队期间居民暴露于辐射场

        返回:
            completion_time – 最后一名乘客上车的时刻
            queue_risk      – 排队期间总风险暴露
            n_trips         – 巴士往返次数
            max_queue       – 最大同时排队人数
        """
        cfg = self.config
        bx, by = self.bus_xy[stop_idx]

        # 单次往返时间 = 2 × (上车点 → 该点专属避难所) / 巴士速度
        # 依据: Miao 2023 / Yin 2023 — 每个上车点应根据 4 指标 (距离/容量/
        # 均衡/风险) 选择独立避难所, 而非假设统一 30 km.
        # 优先使用每个上车点独立的避难所距离 (由 ShelterSelector 预分配);
        # 若未设置则回退到全局默认 SINK_CONFIG["shelter_distance_m"]
        if self.shelter_distances is not None:
            shelter_d = self.shelter_distances.get(
                int(stop_idx), cfg["shelter_distance_m"])
        else:
            shelter_d = cfg["shelter_distance_m"]

        round_trip_time = 2.0 * shelter_d / cfg["bus_speed_ms"]

        first_bus_arrival = cfg["dispatch_delay_sec"]
        cap = cfg["bus_capacity"]
        boarding_per_pax = cfg["boarding_time_per_pax"]

        # 队列状态: [(arrival_time, remaining_pop), ...]
        queue = []
        idx_next_arrival = 0
        n_arrivals = len(arrivals)

        cur_time = 0.0
        next_bus_time = first_bus_arrival
        n_trips = 0
        max_queue = 0
        queue_risk = 0.0
        last_event_time = 0.0

        # 仿真循环: 每次推进到下一个事件 (居民到达 or 巴士到达)
        # 在两事件之间累积排队风险
        while idx_next_arrival < n_arrivals or queue:
            # 下一个事件的时间
            next_arr_time = (arrivals[idx_next_arrival][0]
                             if idx_next_arrival < n_arrivals else float("inf"))
            next_event_time = min(next_arr_time, next_bus_time)

            if next_event_time == float("inf"):
                break

            # ── 累积该时间段内的排队风险 ──
            dt = next_event_time - last_event_time
            if dt > 0 and queue and cfg["queue_risk_enabled"]:
                queue_pop = sum(q[1] for q in queue)
                if queue_pop > 0:
                    risk_val = self._risk_at(bx, by, last_event_time, grid_res)
                    queue_risk += risk_val * queue_pop * dt

            cur_time = next_event_time
            last_event_time = cur_time

            # ── 事件类型判定 ──
            if next_event_time == next_arr_time:
                # 事件: 居民到达
                queue.append([arrivals[idx_next_arrival][0],
                              arrivals[idx_next_arrival][1]])
                idx_next_arrival += 1
                # 更新最大队列
                cur_q = sum(q[1] for q in queue)
                if cur_q > max_queue:
                    max_queue = cur_q
            else:
                # 事件: 巴士到达
                # 巴士装载: 从队头取出最多 cap 人
                load = 0
                while queue and load < cap:
                    take = min(cap - load, queue[0][1])
                    queue[0][1] -= take
                    load += take
                    if queue[0][1] <= 1e-6:
                        queue.pop(0)
                # 上车耗时 (反映在下一辆车的派出时刻)
                board_dur = load * boarding_per_pax
                if load > 0:
                    n_trips += 1
                    # 累积上车期间的辐射 (装载中的人 + 仍在排队的人)
                    if cfg["queue_risk_enabled"] and board_dur > 0:
                        residual_q = sum(q[1] for q in queue) + load
                        risk_val = self._risk_at(bx, by, cur_time, grid_res)
                        queue_risk += risk_val * residual_q * board_dur
                    cur_time += board_dur
                # 下一辆巴士的到达时间
                next_bus_time = cur_time + round_trip_time

        completion_time = cur_time
        return completion_time, queue_risk, n_trips, max_queue

    # ─────────────────────────────────────────────────────
    #  风险场查询 (按当前时刻选择阶段)
    # ─────────────────────────────────────────────────────
    def _risk_at(self, x, y, t_sec, grid_res):
        """根据时刻 t (秒) 选择对应的风险矩阵, 查询 (x,y) 处的风险值"""
        t_min = t_sec / 60.0
        if t_min < 15:
            si = 0
        elif t_min < 25:
            si = 1
        elif t_min < 35:
            si = 2
        else:
            si = 3
        ra = self.risk_arrays[si]
        xm = self.x_mins[si]
        ym = self.y_maxs[si]
        col = int((x - xm) / grid_res)
        row = int((ym - y) / grid_res)
        if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
            return float(ra[row, col])
        return 0.0


# ============================================================
#  辅助函数: 检查方案的可行性
# ============================================================
def check_fleet_capacity(assignment, pop_arr, bus_xy, sink_config=None):
    """
    粗略检查: 总人口 / (车队规模 × 容量 × 最大往返次数) 是否在合理范围

    返回:
        feasible – bool, 是否在车队容量上限内
        info     – dict, 详细统计
    """
    cfg = sink_config or SINK_CONFIG
    total_pop = sum(pop_arr[i] for i in range(len(assignment)) if assignment[i] != -1)

    # 估算最大往返次数 = (总时长 - 调度延迟) / 单次往返时长
    round_trip = 2 * cfg["shelter_distance_m"] / cfg["bus_speed_ms"]
    max_trips = max(1, int((cfg["max_evac_duration"] - cfg["dispatch_delay_sec"]) / round_trip))
    fleet_capacity = cfg["fleet_size"] * cfg["bus_capacity"] * max_trips

    return total_pop <= fleet_capacity, dict(
        total_population=total_pop,
        fleet_max_capacity=fleet_capacity,
        max_round_trips=max_trips,
        utilization=total_pop / fleet_capacity if fleet_capacity > 0 else float("inf"),
    )
