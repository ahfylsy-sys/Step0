"""
Q-NSGA-II 加速引擎 v3

v2 GPU 变慢的三个原因及修复:
  ① _interp_cpu 是纯 Python → 改为 Numba @njit
  ② 每分钟单独传 CPU→GPU (45次传输) → 改为一次性传 45×N 个位置
  ③ 每分钟单独 kernel launch (45次) → 改为一次 CuPy 向量化处理全部

v3 策略:
  CPU (Numba): 一次性算完 45 个时间步 × N 居民的所有坐标 → (45, N, 2)
  GPU (CuPy):  一次性接收 45×N 个位置，批量索引风险矩阵，批量求和
  结果: CPU→GPU 仅 1 次大块传输，GPU 仅 1 次批量计算
"""
import math, random, time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from deap import base, creator, tools, algorithms

from config import WALK_SPEED, GRID_RES, NSGA2_CONFIG, QNSGA2_CONFIG
DEFAULT_MAX_TIME = 45 * 60

# ── 后端检测 ──
try:
    from numba import njit; HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
try:
    import cupy as cp; HAS_CUPY = True
except ImportError:
    HAS_CUPY = False

def print_accel_status():
    print(f"   Numba : {'✅' if HAS_NUMBA else '❌ pip install numba'}")
    print(f"   CuPy  : {'✅' if HAS_CUPY else '❌ pip install cupy-cuda12x'}")

# ── 路径展平 ──
def flatten_paths(road_paths, n, bus_xy):
    cl, dl = [], []
    po, pl = {}, {}
    pos = 0
    for (i, j), ls in road_paths.items():
        c = np.array(ls.coords, dtype=np.float64)
        diffs = np.diff(c, axis=0)
        cum = np.zeros(len(c), np.float64)
        cum[1:] = np.cumsum(np.sqrt(diffs[:,0]**2 + diffs[:,1]**2))
        cl.append(c); dl.append(cum)
        po[(i,j)] = (pos, len(c))
        pl[(i,j)] = cum[-1]
        pos += len(c)
    ac = np.vstack(cl) if cl else np.empty((0,2), np.float64)
    ad = np.concatenate(dl) if dl else np.empty(0, np.float64)
    return ac, ad, po, pl


# ============================================================
#  Numba 全量内核 (CPU-only 路径, 最快)
# ============================================================
if HAS_NUMBA:
    @njit(cache=True)
    def _eval_kernel(ind, sts, nps, lens, ac, ad, bxy, pop,
                     r0,r1,r2,r3, xm0,xm1,xm2,xm3, ym0,ym1,ym2,ym3,
                     gres, spd, maxt, tlim):
        n = len(ind)
        tt = 0.0; mw = 0.0
        for i in range(n):
            t = lens[i] / spd
            tt += t * pop[i]
            if t > mw: mw = t
        if mw > maxt: return 1e18, 1e18

        tr = 0.0
        for m in range(tlim):
            ts = m * 60; dc = ts * spd
            if m < 15:   ra=r0; xm=xm0; ym=ym0
            elif m < 25: ra=r1; xm=xm1; ym=ym1
            elif m < 35: ra=r2; xm=xm2; ym=ym2
            else:        ra=r3; xm=xm3; ym=ym3
            rt = 0.0
            for i in range(n):
                d = lens[i]; j = ind[i]
                if dc >= d:
                    x = bxy[j,0]; y = bxy[j,1]
                else:
                    s = sts[i]; np_ = nps[i]
                    lo = 0; hi = np_ - 1
                    while lo < hi - 1:
                        mid = (lo+hi)//2
                        if ad[s+mid] <= dc: lo = mid
                        else: hi = mid
                    ss = ad[s+lo]; se = ad[s+lo+1]; sl = se - ss
                    if sl < 1e-10:
                        x = ac[s+lo,0]; y = ac[s+lo,1]
                    else:
                        f = (dc - ss) / sl
                        if f < 0.: f = 0.
                        if f > 1.: f = 1.
                        x = ac[s+lo,0]*(1.-f) + ac[s+lo+1,0]*f
                        y = ac[s+lo,1]*(1.-f) + ac[s+lo+1,1]*f
                c = int((x-xm)/gres); r = int((ym-y)/gres)
                rv = 0.0
                if 0<=r<ra.shape[0] and 0<=c<ra.shape[1]: rv = ra[r,c]
                rt += rv * pop[i]
            tr += rt * 60.
        return tt, tr

    @njit(cache=True)
    def _compute_all_positions(n, ind, lens, sts, nps, ac, ad, bxy, spd, tlim):
        """一次性计算 45×N 个位置坐标 → (tlim, N, 2)"""
        out = np.empty((tlim, n, 2), np.float64)
        for m in range(tlim):
            dc = m * 60 * spd
            for i in range(n):
                d = lens[i]; j = ind[i]
                if dc >= d:
                    out[m,i,0] = bxy[j,0]; out[m,i,1] = bxy[j,1]
                else:
                    s = sts[i]; np_ = nps[i]
                    lo = 0; hi = np_ - 1
                    while lo < hi - 1:
                        mid = (lo+hi)//2
                        if ad[s+mid] <= dc: lo = mid
                        else: hi = mid
                    ss = ad[s+lo]; se = ad[s+lo+1]; sl = se - ss
                    if sl < 1e-10:
                        out[m,i,0] = ac[s+lo,0]; out[m,i,1] = ac[s+lo,1]
                    else:
                        f = (dc - ss) / sl
                        if f < 0.: f = 0.
                        if f > 1.: f = 1.
                        out[m,i,0] = ac[s+lo,0]*(1.-f) + ac[s+lo+1,0]*f
                        out[m,i,1] = ac[s+lo,1]*(1.-f) + ac[s+lo+1,1]*f
        return out

else:
    _eval_kernel = None
    _compute_all_positions = None


# ============================================================
#  GPU 批量风险计算 (一次传输, 一次计算)
# ============================================================
def _eval_gpu_v3(ind, sts, nps, lens, ac, ad, bxy_cpu, pop_cpu,
                 ra_gpu, xms, yms, gres, spd, maxt, tlim, n,
                 pop_gpu):
    """
    v3 GPU 路径:
      CPU(Numba): _compute_all_positions → (45, N, 2)  一次性算完
      GPU(CuPy):  一次性传入 45×N 个坐标, 批量索引, 批量求和
      CPU↔GPU 传输: 仅 1 次 (而非 v2 的 45 次)
    """
    wt = lens / spd
    if wt.max() > maxt:
        return (np.inf, np.inf)
    total_t = float(np.dot(wt, pop_cpu))

    # CPU: Numba 一次性算 45×N 个位置
    if _compute_all_positions is not None:
        all_pos = _compute_all_positions(n, ind, lens, sts, nps, ac, ad, bxy_cpu, spd, tlim)
    else:
        # 无 Numba 降级
        all_pos = np.empty((tlim, n, 2), np.float64)
        for m in range(tlim):
            dc = m * 60 * spd
            for i in range(n):
                d = lens[i]
                if dc >= d:
                    all_pos[m,i,0] = bxy_cpu[ind[i],0]
                    all_pos[m,i,1] = bxy_cpu[ind[i],1]
                elif d > 0:
                    s, np_ = int(sts[i]), int(nps[i])
                    idx = np.searchsorted(ad[s:s+np_], dc, side='right') - 1
                    idx = max(0, min(idx, np_-2))
                    ss = ad[s+idx]; sl = ad[s+idx+1] - ss
                    if sl < 1e-10:
                        all_pos[m,i] = ac[s+idx]
                    else:
                        f = max(0., min(1., (dc-ss)/sl))
                        all_pos[m,i,0] = ac[s+idx,0]*(1-f) + ac[s+idx+1,0]*f
                        all_pos[m,i,1] = ac[s+idx,1]*(1-f) + ac[s+idx+1,1]*f
                else:
                    all_pos[m,i,0] = bxy_cpu[ind[i],0]
                    all_pos[m,i,1] = bxy_cpu[ind[i],1]

    # GPU: 一次传输 + 一次批量计算
    g_pos = cp.asarray(all_pos)                       # (45, N, 2) → GPU, 仅 1 次传输
    g_px = g_pos[:, :, 0]                             # (45, N)
    g_py = g_pos[:, :, 1]                             # (45, N)

    # 阶段索引
    stage_idx = np.zeros(tlim, dtype=np.int32)
    for m in range(tlim):
        stage_idx[m] = 0 if m < 15 else (1 if m < 30 else 2)

    total_risk = 0.0
    # 按阶段分组处理 (最多 3 组, 不再逐分钟)
    for si in range(3):
        mask = (stage_idx == si)
        minutes_in_stage = np.where(mask)[0]
        if len(minutes_in_stage) == 0:
            continue

        ra = ra_gpu[si]
        xm, ym = xms[si], yms[si]
        ny, nx = ra.shape

        # 取该阶段所有时间步的位置 → (n_minutes, N)
        px_stage = g_px[minutes_in_stage]              # (n_min, N) — 已在 GPU
        py_stage = g_py[minutes_in_stage]              # (n_min, N)

        # 批量风险索引 (完全向量化, 无循环)
        cols = ((px_stage - xm) / gres).astype(cp.int64)
        rows = ((ym - py_stage) / gres).astype(cp.int64)
        valid = (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
        rv = cp.zeros_like(px_stage)
        rv[valid] = ra[rows[valid], cols[valid]]       # GPU 批量索引

        # (n_min, N) × (N,) → (n_min,) → sum
        risk_per_min = cp.sum(rv * pop_gpu[None, :], axis=1)  # 广播乘
        total_risk += float(cp.sum(risk_per_min)) * 60.0

    return total_t, total_risk


# ============================================================
#  NumPy 降级版
# ============================================================
def _eval_fallback(ind, sts, nps, lens, ac, ad, bxy, pop,
                   ras, xms, yms, gres, spd, maxt, tlim, n):
    wt = lens / spd
    if wt.max() > maxt: return (np.inf, np.inf)
    tt = float(np.dot(wt, pop))
    tr = 0.0
    px = np.empty(n); py = np.empty(n)
    for m in range(tlim):
        dc = m * 60 * spd
        si = 0 if m<15 else (1 if m<30 else 2)
        for i in range(n):
            d = lens[i]
            if dc >= d:
                px[i] = bxy[ind[i],0]; py[i] = bxy[ind[i],1]
            elif d > 0:
                s, np_ = int(sts[i]), int(nps[i])
                idx = np.searchsorted(ad[s:s+np_], dc, side='right') - 1
                idx = max(0, min(idx, np_-2))
                ss = ad[s+idx]; sl = ad[s+idx+1] - ss
                if sl < 1e-10: px[i], py[i] = ac[s+idx]
                else:
                    f = max(0., min(1., (dc-ss)/sl))
                    px[i] = ac[s+idx,0]*(1-f) + ac[s+idx+1,0]*f
                    py[i] = ac[s+idx,1]*(1-f) + ac[s+idx+1,1]*f
            else: px[i] = bxy[ind[i],0]; py[i] = bxy[ind[i],1]
        ra = ras[si]; xm = xms[si]; ym = yms[si]
        cs = ((px-xm)/gres).astype(np.int64); rs = ((ym-py)/gres).astype(np.int64)
        ny, nx = ra.shape; v = (rs>=0)&(rs<ny)&(cs>=0)&(cs<nx)
        rv = np.zeros(n); rv[v] = ra[rs[v], cs[v]]
        tr += float(np.sum(rv * pop)) * 60.
    return tt, tr


# ============================================================
#  评估函数工厂
# ============================================================
def make_evaluate_accel(res_x, res_y, pop_arr, bus_xy, road_paths,
                        risk_arrays, x_mins, y_maxs,
                        speed=WALK_SPEED, max_time=DEFAULT_MAX_TIME,
                        use_gpu=False):
    n = len(res_x); tlim = int(max_time/60)
    pop_np = np.asarray(pop_arr, np.float64)
    bus_np = np.asarray(bus_xy, np.float64)
    ac, ad, po, pl = flatten_paths(road_paths, n, bus_xy)
    ra = [np.ascontiguousarray(r) for r in risk_arrays]

    # GPU 预传输
    ra_gpu = pop_gpu = None
    if use_gpu and HAS_CUPY:
        ra_gpu = [cp.asarray(r) for r in ra]
        pop_gpu = cp.asarray(pop_np)
        print("   🚀 GPU: risk arrays + pop uploaded to VRAM")
    elif use_gpu:
        print("   ⚠️  --gpu but CuPy not installed, CPU fallback")

    # Numba 预热
    if HAS_NUMBA and _eval_kernel is not None:
        print("   🔥 Numba: compiling kernels ...")
        t0 = time.time()
        _d1 = np.zeros(1, np.int64); _d2 = np.array([2], np.int64)
        _dl = np.array([1.]); _dc = np.array([[0.,0.],[1.,1.]])
        _dd = np.array([0.,1.414]); _db = np.array([[0.,0.]]); _dp = np.array([1.])
        _dr = np.zeros((10,10))
        _eval_kernel(_d1,_d1,_d2,_dl,_dc,_dd,_db,_dp,
                     _dr,_dr,_dr,_dr,0.,0.,0.,0.,1.,1.,1.,1.,400.,2.,2700.,1)
        if _compute_all_positions is not None:
            _compute_all_positions(1,_d1,_dl,_d1,_d2,_dc,_dd,_db,2.,1)
        print(f"   ✅ Numba: done ({time.time()-t0:.1f}s, cached)")

    def evaluate(ind):
        sts = np.empty(n, np.int64); nps = np.empty(n, np.int64)
        lens = np.empty(n, np.float64); ia = np.empty(n, np.int64)
        for i in range(n):
            j = ind[i]; key = (i, j)
            if key not in po: return (np.inf, np.inf)
            s, np_ = po[key]
            sts[i] = s; nps[i] = np_; lens[i] = pl[key]; ia[i] = j

        # 优先级: GPU > Numba > NumPy
        if use_gpu and ra_gpu is not None:
            return _eval_gpu_v3(ia, sts, nps, lens, ac, ad, bus_np, pop_np,
                                ra_gpu, x_mins, y_maxs, GRID_RES, speed,
                                max_time, tlim, n, pop_gpu)
        elif HAS_NUMBA and _eval_kernel is not None:
            t, r = _eval_kernel(ia, sts, nps, lens, ac, ad, bus_np, pop_np,
                                ra[0],ra[1],ra[2],ra[3],
                                x_mins[0],x_mins[1],x_mins[2],x_mins[3],
                                y_maxs[0],y_maxs[1],y_maxs[2],y_maxs[3],
                                float(GRID_RES),float(speed),float(max_time),tlim)
            return (np.inf,np.inf) if t >= 1e17 else (t, r)
        else:
            return _eval_fallback(ia, sts, nps, lens, ac, ad, bus_np, pop_np,
                                  ra, x_mins, y_maxs, GRID_RES, speed, max_time, tlim, n)
    return evaluate


# ── 多线程批量评估 ──
def batch_evaluate(inds, ev, nt=None):
    import os
    if nt is None: nt = min(os.cpu_count() or 4, len(inds), 8)
    if nt <= 1 or len(inds) < 4:
        for ind in inds:
            try: ind.fitness.values = ev(ind)
            except: ind.fitness.values = (np.inf, np.inf)
        return
    def _e(ind):
        try: ind.fitness.values = ev(ind)
        except: ind.fitness.values = (np.inf, np.inf)
        return ind
    with ThreadPoolExecutor(max_workers=nt) as pool:
        list(pool.map(_e, inds))


# ── 复用量子组件 ──
from optimizer import (
    QuantumIndividual, QuantumRotationGate,
    quantum_crossover, quantum_mutation, quantum_catastrophe,
    setup_deap, select_solution, compute_metrics,
)

# ============================================================
#  加速版 Q-NSGA-II 主循环
# ============================================================
def run_qnsga2_accel(toolbox, evaluate, feasible,
                     mu=None, ngen=None, lamb=None,
                     logger=None, n_eval_threads=None):
    mu   = mu   or NSGA2_CONFIG["mu"]
    lamb = lamb or NSGA2_CONFIG["lambda_"]
    ngen = ngen or NSGA2_CONFIG["ngen"]
    qc   = QNSGA2_CONFIG

    toolbox.register("evaluate", evaluate)
    gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])

    print(f"\n🚀 Q-NSGA-II ACCEL v3: mu={mu} ngen={ngen}")
    print_accel_status()
    print(f"   Eval threads: {n_eval_threads or 'auto'}")

    qpop = [QuantumIndividual(feasible) for _ in range(mu)]
    t0 = time.time()
    init = [creator.Individual(qi.observe())
            for qi in qpop for _ in range(qc["n_observations"])]
    batch_evaluate(init, evaluate, n_eval_threads)
    pop = tools.selNSGA2(init, mu)
    feas = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
    print(f"   Init: {feas}/{mu} feasible ({time.time()-t0:.1f}s)")
    if feas == 0: raise ValueError("No feasible solutions!")

    logs = []; ta = time.time()
    for gen in range(ngen):
        tg = time.time()
        fp = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        pf = tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

        dt = gate.delta(gen, ngen)
        if pf:
            gi_list = [creator.Individual(qi.observe_greedy()) for qi in qpop]
            batch_evaluate(gi_list, evaluate, n_eval_threads)
            for qi_i, qi in enumerate(qpop):
                gi = random.choice(pf)
                gate.rotate(qi, list(gi_list[qi_i]), list(gi),
                            gi_list[qi_i].fitness.values, gi.fitness.values, dt, feasible)

        random.shuffle(qpop); nq = []
        for k in range(0, len(qpop)-1, 2):
            if random.random() < NSGA2_CONFIG["cxpb"]:
                c1, c2 = quantum_crossover(qpop[k], qpop[k+1], qc["q_crossover_rate"])
                nq.extend([c1, c2])
            else: nq.extend([qpop[k].copy(), qpop[k+1].copy()])
        if len(qpop) % 2 == 1: nq.append(qpop[-1].copy())
        for qi in nq:
            if random.random() < NSGA2_CONFIG["mutpb"]:
                qi.theta = quantum_mutation(qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]).theta
        qpop = nq[:mu]
        ci_ = qc["catastrophe_interval"]
        if ci_ > 0 and (gen+1) % ci_ == 0:
            qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])

        qo = [creator.Individual(qi.observe())
              for qi in qpop for _ in range(qc["n_observations"])]
        batch_evaluate(qo, evaluate, n_eval_threads)

        nc = int(lamb * qc["classical_ratio"])
        co = algorithms.varOr(pop, toolbox, lambda_=nc,
                              cxpb=NSGA2_CONFIG["cxpb"], mutpb=NSGA2_CONFIG["mutpb"])
        batch_evaluate(co, evaluate, n_eval_threads)

        pop = toolbox.select(pop + qo + co, mu)

        vf = [(x.fitness.values[0], x.fitness.values[1]) for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        ic = mu - len(vf); el = time.time() - tg
        if vf:
            mt = min(f[0] for f in vf); mr = min(f[1] for f in vf)
            fp2 = [x for x in pop if np.isfinite(x.fitness.values[0])]
            ps = len(tools.sortNondominated(fp2, len(fp2), first_front_only=True)[0]) if fp2 else 0
        else: mt = mr = float("inf"); ps = 0
        line = f"Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e} | PF {ps} | Inf {ic} | {el:.1f}s"
        logs.append(line)
        if gen % 20 == 0 or gen == ngen - 1: print(f"   {line}")
        if logger: logger.log(line); logger.log_gen(gen+1, mt, mr, ps, ic, dt)

    ff = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
    fpf = tools.sortNondominated(ff, len(ff), first_front_only=True)[0] if ff else []
    tot = time.time() - ta
    print(f"✅ Pareto: {len(fpf)} solutions ({tot:.0f}s / {tot/60:.1f}min)")
    return pop, fpf, logs
