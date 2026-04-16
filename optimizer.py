"""
Q-NSGA-II 优化引擎
- QuantumIndividual：量子比特编码
- QuantumRotationGate：自适应旋转门
- 量子交叉 / 变异 / 灾变算子
- 双目标评估函数（人口加权时间 + 沿路网插值风险）
- 主进化循环 run_qnsga2()
- 解选择与真实指标计算
"""
import math
import random
import numpy as np
from deap import base, creator, tools, algorithms

from config import (
    WALK_SPEED, GRID_RES, MAX_WALK_TIME,
    NSGA2_CONFIG, QNSGA2_CONFIG,
    RISK_STAGE_TIMES,
)

DEFAULT_MAX_TIME = 45 * 60


# ============================================================
#  风险查询（内联，避免跨模块高频调用开销）
# ============================================================
def _risk(x, y, arr, xm, ym, res=GRID_RES):
    c = int((x - xm) / res)
    r = int((ym - y) / res)
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]:
        return arr[r, c]
    return 0.0


# ============================================================
#  量子个体
# ============================================================
class QuantumIndividual:
    """
    Q-bit 编码个体。每个居民 i 维护角度向量 θ[i]，
    选择第 k 个可行上车点的概率 ∝ cos²(θ[i][k])。
    """
    __slots__ = ("n", "feasible", "theta")

    def __init__(self, feasible, init="uniform"):
        self.n = len(feasible)
        self.feasible = feasible
        if init == "uniform":
            self.theta = [np.full(len(feasible[i]), math.pi / 4)
                          for i in range(self.n)]
        else:
            self.theta = [np.random.uniform(0, math.pi / 2, len(feasible[i]))
                          for i in range(self.n)]

    def observe(self):
        """按 cos²(θ) 概率抽样 → 经典解"""
        sol = []
        for i in range(self.n):
            pr = np.cos(self.theta[i]) ** 2
            s = pr.sum()
            k = (np.random.choice(len(self.feasible[i]), p=pr / s)
                 if s > 1e-12
                 else random.randint(0, len(self.feasible[i]) - 1))
            sol.append(self.feasible[i][k])
        return sol

    def observe_greedy(self):
        """确定性：取概率最大的选项"""
        return [self.feasible[i][np.argmax(np.cos(self.theta[i]) ** 2)]
                for i in range(self.n)]

    def copy(self):
        new = QuantumIndividual.__new__(QuantumIndividual)
        new.n, new.feasible = self.n, self.feasible
        new.theta = [a.copy() for a in self.theta]
        return new


# ============================================================
#  量子旋转门
# ============================================================
class QuantumRotationGate:
    """
    自适应旋转门：早期 Δθ 大（探索），后期 Δθ 小（开发）。
    引导解支配当前解时全幅旋转，否则缩小为 0.3 倍。
    """
    def __init__(self, d_max, d_min):
        self.d_max, self.d_min = d_max, d_min

    def delta(self, gen, ngen):
        return self.d_max - (self.d_max - self.d_min) * gen / max(ngen, 1)

    def rotate(self, qi, cur, guide, cur_f, guide_f, dt, feasible):
        dom = self._dom(guide_f, cur_f)
        dt2 = dt if dom else dt * 0.3
        for i in range(qi.n):
            if cur[i] == guide[i]:
                continue
            try:
                gi = feasible[i].index(guide[i])
                ci = feasible[i].index(cur[i])
            except ValueError:
                continue
            qi.theta[i][gi] = max(0.01, qi.theta[i][gi] - dt2)
            qi.theta[i][ci] = min(math.pi / 2 - 0.01, qi.theta[i][ci] + dt2)

    @staticmethod
    def _dom(a, b):
        """a 是否 Pareto-支配 b（双目标最小化）"""
        if np.isinf(a[0]) or np.isinf(a[1]):
            return False
        if np.isinf(b[0]) or np.isinf(b[1]):
            return True
        return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


# ============================================================
#  量子算子
# ============================================================
def quantum_crossover(q1, q2, rate=0.5):
    """角度空间逐维交换"""
    c1, c2 = q1.copy(), q2.copy()
    for i in range(q1.n):
        if random.random() < rate:
            c1.theta[i], c2.theta[i] = c2.theta[i].copy(), c1.theta[i].copy()
    return c1, c2


def quantum_mutation(q, rate=0.15, pert=0.1 * math.pi):
    """角度随机扰动"""
    m = q.copy()
    for i in range(m.n):
        if random.random() < rate:
            for k in range(len(m.theta[i])):
                if random.random() < 0.5:
                    m.theta[i][k] += random.uniform(-pert, pert)
                    m.theta[i][k] = max(0.01, min(math.pi / 2 - 0.01, m.theta[i][k]))
    return m


def quantum_catastrophe(qpop, rate=0.1):
    """重新初始化部分种群，防止早熟"""
    for idx in random.sample(range(len(qpop)), int(len(qpop) * rate)):
        qi = qpop[idx]
        for i in range(qi.n):
            qi.theta[i] = np.random.uniform(0, math.pi / 2, len(qi.theta[i]))
    return qpop


# ============================================================
#  DEAP 框架设置
# ============================================================
def setup_deap(feasible):
    """创建 DEAP 双目标最小化 Individual 与 Toolbox"""
    if hasattr(creator, "FitnessMulti"): del creator.FitnessMulti
    if hasattr(creator, "Individual"):   del creator.Individual
    creator.create("FitnessMulti", base.Fitness, weights=(-1.0, -1.0))
    creator.create("Individual", list, fitness=creator.FitnessMulti)

    tb = base.Toolbox()
    n = len(feasible)
    tb.register("individual",
                lambda: creator.Individual([random.choice(feasible[i]) for i in range(n)]))
    tb.register("population", tools.initRepeat, list, tb.individual)
    tb.register("mate", tools.cxTwoPoint)

    def _mut(ind, indpb=NSGA2_CONFIG["indpb"]):
        for i in range(len(ind)):
            if random.random() < indpb:
                ind[i] = random.choice(feasible[i])
        return (ind,)

    tb.register("mutate", _mut)
    tb.register("select", tools.selNSGA2)
    return tb


# ============================================================
#  双目标评估函数 (含上车点 sink 边界条件)
# ============================================================
def make_evaluate(res_x, res_y, pop_arr, bus_xy, road_paths,
                  risk_arrays, x_mins, y_maxs,
                  speed=WALK_SPEED, max_time=DEFAULT_MAX_TIME,
                  use_sink=True, sink_config=None,
                  congestion_data=None,
                  shelter_xy=None, shelter_capacities=None,
                  depot_xy=None,
                  road_graph=None, stop_nodes=None,
                  shelter_nodes=None, depot_node=None):
    """
    返回 evaluate(ind) → (time_obj, risk_obj)

    时间目标 (含 sink 边界):
        T_total = max(walk_time + queue_wait + bus_transit)
        其中 queue_wait 由巴士调度+容量约束决定

    风险目标:
        R_total = walk_risk + queue_risk
        walk_risk:  沿路径步行期间累积
        queue_risk: 在上车点排队等待巴士期间累积

    参数:
        use_sink           – 是否启用 sink 边界条件 (默认启用)
        sink_config        – PickupSinkModel 配置, None 时使用默认值
        shelter_xy         – (n_shelters, 2) 避难所 UTM 坐标
        shelter_capacities – (n_shelters,) 避难所容量
        depot_xy           – (2,) 巴士总站 UTM 坐标 (大鹏总站)
    """
    n = len(res_x)
    t_lim_min = int(max_time / 60)
    pop_np = np.asarray(pop_arr, dtype=np.float64)

    # 初始化 sink 模型 (一次性, 复用)
    sink_model = None
    if use_sink:
        from pickup_sink import PickupSinkModel
        sink_model = PickupSinkModel(
            bus_xy, risk_arrays, x_mins, y_maxs,
            shelter_xy, shelter_capacities, sink_config,
            depot_xy=depot_xy,
            road_graph=road_graph,
            stop_nodes=stop_nodes,
            shelter_nodes=shelter_nodes,
            depot_node=depot_node)

    # 道路拥挤度参数 (v5.7)
    use_congestion = (congestion_data is not None)
    if use_congestion:
        from config import ROAD_CONGESTION_CONFIG as _ccfg
        _edge_paths_map = congestion_data['edge_paths']
        _edge_caps = congestion_data['edge_capacities']
        _edge_lens = congestion_data['edge_lengths']
        _bpr_alpha = _ccfg['bpr_alpha']
        _bpr_beta = _ccfg['bpr_beta']
        _cong_weight = _ccfg['congestion_weight']

    def evaluate(ind):
        # ── Phase 1: 步行阶段 ──
        infos, times = [], []
        max_t = 0.0
        for i in range(n):
            j = ind[i]
            pl = road_paths.get((i, j))
            if pl is None:
                return (np.inf, np.inf)
            d = pl.length
            t = d / speed
            times.append(t)
            infos.append((pl, d, j))
            if t > max_t:
                max_t = t
        if max_t > max_time:
            return (np.inf, np.inf)

        walk_time_weighted = sum(t * p for t, p in zip(times, pop_arr))

        # ── 道路拥挤度惩罚 (v5.7) ──
        if use_congestion:
            edge_flow = {}
            for i in range(n):
                j_stop = infos[i][2]
                for ek in _edge_paths_map.get((i, j_stop), ()):
                    edge_flow[ek] = edge_flow.get(ek, 0.0) + pop_arr[i]
            congestion_delay = 0.0
            for ek, flow in edge_flow.items():
                cap = _edge_caps.get(ek, 1e18)
                if cap > 0 and flow > cap:
                    vc = flow / cap
                    length = _edge_lens.get(ek, 0.0)
                    free_t = length / speed
                    delay = free_t * _bpr_alpha * (vc ** _bpr_beta)
                    congestion_delay += delay * flow
            walk_time_weighted += _cong_weight * congestion_delay

        # ── Phase 2: 步行风险 (沿路径累积) ──
        walk_risk = 0.0
        n_stages = len(RISK_STAGE_TIMES)
        for minute in range(t_lim_min):
            ts = minute * 60
            # 根据时刻选择对应的风险阶段
            si = 0
            for k in range(n_stages):
                if minute < RISK_STAGE_TIMES[k]:
                    si = k
                    break
            else:
                si = n_stages - 1
            ra, xm, ym = risk_arrays[si], x_mins[si], y_maxs[si]
            rt = 0.0
            for i in range(n):
                pl, d, dj = infos[i]
                dc = ts * speed
                if dc >= d:
                    x, y = bus_xy[dj]
                elif d > 0:
                    pt = pl.interpolate(dc / d, normalized=True)
                    x, y = pt.x, pt.y
                else:
                    x, y = bus_xy[dj]
                rt += _risk(x, y, ra, xm, ym) * pop_arr[i]
            walk_risk += rt * 60

        if not use_sink:
            # 退化为原始模型
            return walk_time_weighted, walk_risk

        # ── Phase 3: 上车点 sink 阶段 ──
        # arrival_times[i] = 居民 i 到达上车点的时刻 (= walk_time_i)
        arrival_times = np.array(times, dtype=np.float64)

        try:
            T_total, R_total, sink_info = sink_model.process(
                assignment=list(ind),
                arrival_times=arrival_times,
                pop_arr=pop_np,
                walk_risk=walk_risk,
            )
        except Exception:
            return (np.inf, np.inf)

        # ── 硬约束: 巴士到达时上车点已被风险覆盖 → 不可行解 ──
        # 如果有居民因上车点关闭而无法疏散, 该解直接判为不可行
        if sink_info.get("unevacuated_pop", 0) > 1e-6:
            return (np.inf, np.inf)

        # T_total 是全员撤离总时长 (秒); 转为人口加权形式与原口径一致
        # 这里取 max(walk_time_weighted, T_total * total_pop) 的合成值
        # 物理含义: 总加权疏散时间 = walk部分 + sink部分的加权差额
        sink_extra_time = max(0.0, T_total - max(times)) * float(pop_np.sum())
        total_time_obj = walk_time_weighted + sink_extra_time

        return total_time_obj, R_total

    return evaluate


# ============================================================
#  Q-NSGA-II 主循环
# ============================================================
def run_qnsga2(toolbox, evaluate, feasible,
               mu=None, ngen=None, lamb=None, logger=None):
    """
    Q-NSGA-II 进化主循环

    流程：
      1 初始化量子种群 → 2 观测 + 评估 →
      3 非支配排序识别 Pareto 前沿 →
      4 量子旋转门引导角度 →
      5 量子交叉 / 变异 → 6 周期灾变 →
      7 量子观测子代 → 8 经典 GA 子代 →
      9 NSGA-II 环境选择 → 重复 3-9

    返回:
        pop, pareto_front, log_lines
    """
    mu   = mu   or NSGA2_CONFIG["mu"]
    lamb = lamb or NSGA2_CONFIG["lambda_"]
    ngen = ngen or NSGA2_CONFIG["ngen"]
    qc   = QNSGA2_CONFIG

    toolbox.register("evaluate", evaluate)
    gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])

    header = (f"Q-NSGA-II: mu={mu} ngen={ngen} obs={qc['n_observations']} "
              f"classical={qc['classical_ratio']}")
    print(f"\n🔬 {header}")
    if logger:
        logger.log(header)

    # ---- Step 1: 量子种群初始化 ----
    qpop = [QuantumIndividual(feasible) for _ in range(mu)]

    # ---- Step 2: 初始观测 & 评估 ----
    pop = []
    for qi in qpop:
        for _ in range(qc["n_observations"]):
            sol = qi.observe()
            ind = creator.Individual(sol)
            ind.fitness.values = evaluate(ind)
            pop.append(ind)
    pop = tools.selNSGA2(pop, mu)

    feas = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
    print(f"   Init: {feas}/{mu} feasible")
    if feas == 0:
        raise ValueError("No feasible solutions in initial population!")

    logs = []

    for gen in range(ngen):
        # ---- Step 3: Pareto 前沿 ----
        fp = [x for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        pf = tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

        # ---- Step 4: 量子旋转门 ----
        dt = gate.delta(gen, ngen)
        if pf:
            for qi in qpop:
                cs = qi.observe_greedy()
                ci = creator.Individual(cs)
                ci.fitness.values = evaluate(ci)
                gi = random.choice(pf)
                gate.rotate(qi, cs, list(gi),
                            ci.fitness.values, gi.fitness.values, dt, feasible)

        # ---- Step 5: 量子交叉 + 变异 ----
        random.shuffle(qpop)
        nq = []
        for k in range(0, len(qpop) - 1, 2):
            if random.random() < NSGA2_CONFIG["cxpb"]:
                c1, c2 = quantum_crossover(qpop[k], qpop[k + 1], qc["q_crossover_rate"])
                nq.extend([c1, c2])
            else:
                nq.extend([qpop[k].copy(), qpop[k + 1].copy()])
        if len(qpop) % 2 == 1:
            nq.append(qpop[-1].copy())
        for qi in nq:
            if random.random() < NSGA2_CONFIG["mutpb"]:
                qi.theta = quantum_mutation(
                    qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]
                ).theta
        qpop = nq[:mu]

        # ---- Step 6: 量子灾变 ----
        ci_ = qc["catastrophe_interval"]
        if ci_ > 0 and (gen + 1) % ci_ == 0:
            qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])

        # ---- Step 7: 量子观测子代 ----
        q_off = []
        for qi in qpop:
            for _ in range(qc["n_observations"]):
                ind = creator.Individual(qi.observe())
                try:
                    ind.fitness.values = evaluate(ind)
                except Exception:
                    ind.fitness.values = (np.inf, np.inf)
                q_off.append(ind)

        # ---- Step 8: 经典 GA 子代 ----
        nc = int(lamb * qc["classical_ratio"])
        c_off = algorithms.varOr(
            pop, toolbox, lambda_=nc,
            cxpb=NSGA2_CONFIG["cxpb"], mutpb=NSGA2_CONFIG["mutpb"])
        for ind in c_off:
            try:
                ind.fitness.values = evaluate(ind)
            except Exception:
                ind.fitness.values = (np.inf, np.inf)

        # ---- Step 9: 环境选择 ----
        pop = toolbox.select(pop + q_off + c_off, mu)

        # ---- 日志 ----
        vf = [(x.fitness.values[0], x.fitness.values[1])
              for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        inf_c = mu - len(vf)
        if vf:
            mt, mr = min(f[0] for f in vf), min(f[1] for f in vf)
            fp2 = [x for x in pop if np.isfinite(x.fitness.values[0])]
            ps = len(tools.sortNondominated(fp2, len(fp2),
                                            first_front_only=True)[0]) if fp2 else 0
        else:
            mt, mr, ps = float("inf"), float("inf"), 0

        line = (f"Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e} "
                f"| PF {ps} | Inf {inf_c} | Δθ {dt/math.pi:.4f}π")
        logs.append(line)
        if gen % 20 == 0 or gen == ngen - 1:
            print(f"   {line}")
        if logger:
            logger.log(line)

    # ---- 最终 Pareto 前沿 ----
    ff = [x for x in pop
          if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
    final_pf = (tools.sortNondominated(ff, len(ff), first_front_only=True)[0]
                if ff else [])
    print(f"✅ Final Pareto front: {len(final_pf)} solutions")
    return pop, final_pf, logs


# ============================================================
#  解选择
# ============================================================
def select_solution(pf, method="min_risk"):
    feas = [x for x in pf
            if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
    if not feas:
        raise ValueError("Empty Pareto front!")

    if method == "min_risk":
        best = feas[int(np.argmin([x.fitness.values[1] for x in feas]))]
    elif method == "min_time":
        best = feas[int(np.argmin([x.fitness.values[0] for x in feas]))]
    elif method == "knee":
        if len(feas) < 3:
            return select_solution(feas, "min_risk")
        sf = sorted(feas, key=lambda x: x.fitness.values[0])
        ts = [x.fitness.values[0] for x in sf]
        rs = [x.fitness.values[1] for x in sf]
        tr = max(ts) - min(ts) or 1
        rr = max(rs) - min(rs) or 1
        nt = [(t - min(ts)) / tr for t in ts]
        nr = [(r - min(rs)) / rr for r in rs]
        best = sf[int(np.argmax(
            [abs(nt[i] + nr[i] - 1) / np.sqrt(2) for i in range(len(sf))]))]
    else:
        raise ValueError(f"Unknown method: {method}")

    print(f"🎯 Selected ({method}): T={best.fitness.values[0]:.2f}  "
          f"R={best.fitness.values[1]:.2e}")
    return best, method


# ============================================================
#  真实指标计算（全仿真）
# ============================================================
def compute_metrics(assignment, res_df, bus_xy, active_idx, paths,
                    risk_arrays, x_mins, y_maxs, res_pop, snapped_res,
                    speed=WALK_SPEED, max_time=DEFAULT_MAX_TIME):
    """沿道路网络路径逐分钟仿真，计算真实时间和风险"""
    g2l = {i: il for il, i in enumerate(active_idx)}
    arrival = {}
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1:
            arrival[i] = np.inf
        elif i in g2l:
            pl = paths.get((g2l[i], j))
            arrival[i] = pl.length / speed if pl else np.inf
        else:
            arrival[i] = 0.0

    total_time = sum(t for t in arrival.values() if np.isfinite(t))
    total_risk = 0.0
    reached = np.zeros(len(res_df), dtype=bool)

    for minute in range(int(max_time / 60) + 1):
        ts = minute * 60
        n_stages = len(RISK_STAGE_TIMES)
        si = 0
        for k in range(n_stages):
            if minute <= RISK_STAGE_TIMES[k]:
                si = k
                break
        else:
            si = n_stages - 1
        ra, xm, ym = risk_arrays[si], x_mins[si], y_maxs[si]
        for i in range(len(res_df)):
            j = assignment[i]
            if j == -1 or not np.isfinite(arrival.get(i, np.inf)):
                continue
            if reached[i]:
                x, y = bus_xy[j]
            elif ts >= arrival[i]:
                x, y = bus_xy[j]; reached[i] = True
            elif i in g2l:
                pl = paths.get((g2l[i], j))
                if pl and pl.length > 0:
                    pt = pl.interpolate(min(ts * speed / pl.length, 1.0), normalized=True)
                    x, y = pt.x, pt.y
                else:
                    x, y = bus_xy[j]
            else:
                x, y = bus_xy[j]
            total_risk += _risk(x, y, ra, xm, ym) * res_pop[i] * 60

    return total_time, total_risk, arrival
