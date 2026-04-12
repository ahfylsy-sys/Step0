"""
Q-NSGA-II 疏散优化系统 — 主入口
一键运行：python main.py --test --serial

功能：
  1. 加载路网/居民/风险数据
  2. 预计算道路网络路径
  3. 构建可行域
  4. 运行 Q-NSGA-II 双目标优化
  5. 计算真实指标
  6. 导出 Pareto CSV + 分组 Excel + 日志
  7. 生成可视化（Pareto演化 / 分配地图 / 阶段图）
"""
import os
import sys
import traceback
import numpy as np
from datetime import datetime
from multiprocessing import Pool, cpu_count

from config import (
    CENTER_UTM, RISK_VALUE_FILES, ROAD_NETWORK_SHP, SHELTER_FILE,
    OUTPUT_ROOT, ROAD_CLIP_RADIUS, NSGA2_CONFIG, PARETO_VIZ,
    PICKUP_CLOSURE_CONFIG, ROAD_HIERARCHY_CONFIG,
    DEFAULT_N_WORKERS, DEFAULT_EVAL_THREADS,
    MULTISTAGE_CONFIG,
)
from data_loader import (
    load_resident_data, load_bus_stops, load_all_risk_data,
    load_road_network, precompute_paths, build_feasible,
    build_group_configs, precompute_shelter_paths,
    compute_stop_contamination_times,
    compute_4stage_safe_stops,
    rebuild_feasible_for_stage, compute_intermediate_positions,
)
from optimizer import (
    setup_deap, make_evaluate, run_qnsga2,
    select_solution, compute_metrics,
    hot_start_quantum_population, make_evaluate_stage,
)
from shelter_selector import (
    load_shelters, ShelterSelector, SHELTER_CONFIG,
)
from visualization import (
    ParetoVisualizer, plot_assignment_map, plot_evacuation_stages,
)
from export import (
    EvacLogger, export_pareto_csv, export_results_excel,
)

from resi_anime import animate_group

# ============================================================
#  多阶段滚动时域优化（v5.5 新增）
# ============================================================
def optimize_group_multistage(config, selection_method="min_risk",
                               accelerate=False, use_gpu=False,
                               n_eval_threads=None, use_sink=True,
                               use_dynamic_shelter=True,
                               use_dynamic_closure=True):
    """
    多阶段滚动时域 Q-NSGA-II 优化 (MRH-QNSGA2)。

    将45分钟疏散窗口分解为4个顺序决策阶段:
        Stage 0: t=0min,  用15min风险矩阵判断上车点可用性
        Stage 1: t=15min, 用15-25min风险矩阵重新评估
        Stage 2: t=25min, 用25-35min风险矩阵重新评估
        Stage 3: t=35min, 用35-45min风险矩阵重新评估

    每个阶段:
        1. 确定活跃居民 (尚未到达上车点的)
        2. 根据风险场确定可用上车点 (非零风险=禁用)
        3. 重建可行域
        4. 热启动量子种群 (70%继承 + 30%随机)
        5. 运行 Q-NSGA-II (代数递减: 100→60→40→20)
        6. 冻结已到达居民的分配

    文献依据:
        - Li et al. (2019) J. Environmental Radioactivity (rolling horizon)
        - Deb et al. (2007) EMO (热启动NSGA-II)
    """
    name  = config["group_name"]
    speed = config["speed"]
    out   = config["output_dir"]
    max_t = config.get("max_walk_time_minutes", 45) * 60
    os.makedirs(out, exist_ok=True)

    msc = MULTISTAGE_CONFIG
    stage_times = msc["stage_decision_times"]   # [0, 15, 25, 35]
    stage_deadlines = msc["stage_deadlines"]     # [15, 25, 35, 45]
    ngen_sched = msc["ngen_schedule"]            # [100, 60, 40, 20]
    hot_ratio = msc["hot_start_ratio"]           # 0.7

    print(f"\n{'='*60}")
    print(f"🔬 {name}  speed={speed} m/s  MULTISTAGE MODE (4 stages)")
    print(f"{'='*60}")

    # ---------- 日志 ----------
    logger = EvacLogger(name, out)
    logger.log(f"Group: {name}  Speed: {speed}  Mode: MULTISTAGE")
    logger.log(f"Stages: {stage_times} → {stage_deadlines}")
    logger.log(f"NGen schedule: {ngen_sched}  Hot-start: {hot_ratio}")
    logger.log(f"Config: sink={'ON' if use_sink else 'OFF'}  "
               f"dynamic_shelter={'ON' if use_dynamic_shelter else 'OFF'}")

    # ---------- Step 1: 加载数据 ----------
    G, nids, ncoords, kd = load_road_network(
        ROAD_NETWORK_SHP, center=CENTER_UTM, clip_radius=ROAD_CLIP_RADIUS)
    res_df   = load_resident_data(config["pop_file"])
    bus_xy, bus_gdf = load_bus_stops(config["bus_file"])
    risk_arrays, x_mins, y_maxs = load_all_risk_data(RISK_VALUE_FILES)

    active_idx = list(range(len(res_df)))
    bus_list   = list(range(len(bus_xy)))

    logger.log(f"Residents: {len(res_df)}  Bus stops: {len(bus_xy)}")

    # ---------- Step 2: 预计算路径 ----------
    road_paths, snapped_res, snapped_bus, validity = precompute_paths(
        res_df, bus_xy, G, active_idx, bus_list, kd, nids, ncoords)

    # ---------- Step 3: 初始可行域 ----------
    feasible_full, no_opt = build_feasible(
        road_paths, len(active_idx), bus_list, max_t, speed)

    # ---------- Step 3.5: 避难所预分配 (同单阶段) ----------
    shelter_mapping_multi = None
    shelter_capacities_global = None
    shelter_geometries = None
    shelter_xy_global = None
    if use_sink and use_dynamic_shelter:
        print(f"\n   🏥 Loading shelters and running multi-shelter allocator...")
        try:
            shelter_xy, shelter_cap, shelter_ids, shelter_gdf = load_shelters(SHELTER_FILE)
            shelter_xy_global = shelter_xy
            shelter_capacities_global = shelter_cap

            cache_path = os.path.join(out, "shelter_paths_cache.pkl")
            shelter_path_lengths, shelter_geometries = precompute_shelter_paths(
                snapped_bus, shelter_xy, G,
                kdtree=kd, nids=nids, ncoords=ncoords,
                top_k_for_geometry=50, cache_path=cache_path,
            )

            selector = ShelterSelector(
                shelter_xy=shelter_xy, capacities=shelter_cap,
                risk_arrays=risk_arrays, x_mins=x_mins, y_maxs=y_maxs,
                grid_res=400, config=SHELTER_CONFIG,
            )

            res_pop = res_df["pop"].values[active_idx]
            stop_pop_est = np.zeros(len(snapped_bus), dtype=np.float64)
            for il in range(len(active_idx)):
                f_list = feasible_full[il]
                if not f_list:
                    continue
                share = res_pop[il] / len(f_list)
                for j in f_list:
                    stop_pop_est[j] += share

            shelter_mapping_multi = selector.allocate_static_multi(
                bus_xy=snapped_bus, bus_demands=stop_pop_est,
                top_k=50, shelter_path_lengths=shelter_path_lengths,
            )
            print(f"   ✅ Multi-shelter alloc: "
                  f"{len(shelter_mapping_multi)} stops × Top 50 candidates")
            logger.log(f"Multi-shelter alloc: stops={len(shelter_mapping_multi)}")
        except Exception as e:
            print(f"   ⚠️  Multi-shelter allocator failed: {e}")
            traceback.print_exc()
            shelter_mapping_multi = None
            shelter_capacities_global = None

    # ---------- Step 4: 过滤无选项居民 ----------
    valid_ai, valid_f, valid_sr = [], [], []
    for il, i in enumerate(active_idx):
        if feasible_full[il]:
            valid_ai.append(i)
            valid_f.append(feasible_full[il])
            valid_sr.append(snapped_res[il])
    excl = len(active_idx) - len(valid_ai)
    if excl:
        print(f"   ❌ Excluded {excl} residents (no reachable stop)")
        logger.log(f"Excluded: {excl}")

    # 重映射路径索引
    o2n = {}
    for ni, i in enumerate(valid_ai):
        o2n[active_idx.index(i)] = ni
    new_paths = {(o2n[ol], j): pl
                 for (ol, j), pl in road_paths.items() if ol in o2n}

    active_idx   = valid_ai
    feasible_full = valid_f
    snapped_res  = np.array(valid_sr)
    road_paths   = new_paths

    if not active_idx:
        print(f"   ⚠️  No residents with feasible stops — skipping group (multistage)")
        logger.log("SKIPPED: no residents with feasible stops (multistage)")
        logger.close()
        return None

    res_pop = res_df["pop"].values[active_idx]
    logger.log(f"Active residents: {len(active_idx)}")

    # ────────────────────────────────────────────────────────────
    #  多阶段滚动时域主循环
    # ────────────────────────────────────────────────────────────
    n_res = len(active_idx)
    n_bus = len(snapped_bus)

    # 全局状态
    frozen_set = set()              # 已冻结的居民局部索引集合
    frozen_assign = {}              # {局部索引: 上车点j}
    frozen_sink_events = []         # [(上车点j, 到达时间sec, 人口)]
    current_assignments = [-1] * n_res  # 各居民当前分配
    prev_qpop = None
    prev_feasible = None
    stage_history = []

    # 可视化器
    pv = ParetoVisualizer(out, f"{name}_multistage")

    for s in range(4):
        t_decision = stage_times[s] * 60       # 决策时刻 (秒)
        t_deadline = stage_deadlines[s] * 60   # 截止时刻 (秒)
        ngen_s = ngen_sched[s]

        print(f"\n{'─'*50}")
        print(f"📍 Stage {s}: t={stage_times[s]}→{stage_deadlines[s]}min  "
              f"ngen={ngen_s}")
        logger.log(f"Stage {s}: decision={stage_times[s]}min "
                   f"deadline={stage_deadlines[s]}min ngen={ngen_s}")

        # ── 1. 确定活跃居民 ──
        active_local = [i for i in range(n_res) if i not in frozen_set]
        if not active_local:
            print(f"   ✅ All residents arrived, early termination")
            logger.log(f"Stage {s}: all arrived, early stop")
            break

        print(f"   Active: {len(active_local)}/{n_res} residents")
        logger.log(f"Stage {s}: active={len(active_local)}/{n_res}")

        # ── 2. 确定可用上车点 ──
        # 用下一阶段的风险矩阵判断 (t=0时用15min矩阵, t=15时用25min矩阵...)
        risk_check_stage = min(s + 1, 3)  # 检查的风险阶段索引
        if msc.get("closure_at_start") == "current_stage":
            risk_check_stage = s

        risk_check = risk_arrays[risk_check_stage]
        xm_check = x_mins[risk_check_stage]
        ym_check = y_maxs[risk_check_stage]

        available_stops = set()
        for j in range(n_bus):
            bx, by = snapped_bus[j]
            col = int((bx - xm_check) / 400)
            row = int((ym_check - by) / 400)
            if 0 <= row < risk_check.shape[0] and 0 <= col < risk_check.shape[1]:
                dose = float(risk_check[row, col])
                if dose <= 0:
                    available_stops.add(j)
                # 非零风险 → 禁用
            else:
                available_stops.add(j)  # 不在风险场范围内 → 可用

        # 补充: 即使在风险场外, 如果之前已被污染也排除
        if use_dynamic_closure and PICKUP_CLOSURE_CONFIG.get("enabled", False):
            cont_times, _ = compute_stop_contamination_times(
                snapped_bus, risk_arrays, x_mins, y_maxs,
                grid_res=400, threshold=PICKUP_CLOSURE_CONFIG["closure_threshold"])
            for j in range(n_bus):
                if np.isfinite(cont_times[j]) and cont_times[j] <= t_decision:
                    available_stops.discard(j)

        n_closed = n_bus - len(available_stops)
        print(f"   Available stops: {len(available_stops)}/{n_bus} "
              f"({n_closed} closed by risk)")
        logger.log(f"Stage {s}: avail_stops={len(available_stops)} "
                   f"closed={n_closed} risk_stage={risk_check_stage}")

        if not available_stops:
            print(f"   ❌ No available stops! Remaining residents cannot evacuate.")
            logger.log(f"Stage {s}: NO available stops!")
            break

        # ── 3. 重建可行域 ──
        # 剩余步行时间 = t_deadline - t_decision
        remaining_walk_time = t_deadline - t_decision
        # 但最大步行时间不能超过原始 max_t
        effective_max_walk = min(remaining_walk_time, max_t)

        feasible_s = rebuild_feasible_for_stage(
            active_local, available_stops, road_paths, speed, effective_max_walk)

        # 过滤无可行选项的活跃居民
        valid_active = []
        valid_feasible = []
        for idx, i in enumerate(active_local):
            if feasible_s[idx]:
                valid_active.append(i)
                valid_feasible.append(feasible_s[idx])
            else:
                # 该居民无可用的安全上车点 → 标记为不可撤离
                current_assignments[i] = -1
                frozen_set.add(i)
                print(f"   ⚠️  Resident {i} has no safe stop at stage {s}")

        if not valid_active:
            print(f"   ❌ No residents with feasible stops at stage {s}")
            logger.log(f"Stage {s}: no feasible residents")
            break

        active_local = valid_active
        feasible_s = valid_feasible

        # ── 4. 构建活跃居民的子数据 ──
        sub_res_x = snapped_res[active_local, 0] if len(snapped_res.shape) > 1 else np.array([snapped_res[i][0] for i in active_local])
        sub_res_y = snapped_res[active_local, 1] if len(snapped_res.shape) > 1 else np.array([snapped_res[i][1] for i in active_local])
        sub_pop = res_pop[active_local]

        # 子路径字典: 重映射活跃居民索引为 0..len(active_local)-1
        sub_road_paths = {}
        active_set = set(active_local)
        for new_i, orig_i in enumerate(active_local):
            for j in available_stops:
                pl = road_paths.get((orig_i, j))
                if pl is not None:
                    sub_road_paths[(new_i, j)] = pl

        # ── 5. Sink 模型 ──
        sink_model = None
        if use_sink:
            from pickup_sink import PickupSinkModel
            sink_model = PickupSinkModel(
                snapped_bus, risk_arrays, x_mins, y_maxs, None,
                shelter_mapping_multi=shelter_mapping_multi,
                shelter_capacities=shelter_capacities_global,
            )

        # ── 6. 阶段性评估函数 ──
        ev = make_evaluate_stage(
            sub_res_x, sub_res_y, sub_pop, snapped_bus, sub_road_paths,
            risk_arrays, x_mins, y_maxs,
            speed, max_t, stage_idx=s, t_offset_sec=t_decision,
            frozen_sink_events=frozen_sink_events if s > 0 else None,
            use_sink=use_sink, sink_model=sink_model,
            shelter_mapping_multi=shelter_mapping_multi,
            shelter_capacities=shelter_capacities_global,
        )

        # ── 7. 热启动 / 初始化量子种群 ──
        tb = setup_deap(feasible_s)

        if s > 0 and prev_qpop is not None and prev_feasible is not None:
            qpop = hot_start_quantum_population(
                prev_qpop, prev_feasible, feasible_s, hot_ratio)
            print(f"   🔥 Hot-start: {int(len(qpop)*hot_ratio)} inherited + "
                  f"{len(qpop)-int(len(qpop)*hot_ratio)} random")
        else:
            qpop = [QuantumIndividual(feasible_s) for _ in range(NSGA2_CONFIG["mu"])]

        # ── 8. 运行 Q-NSGA-II (代数 = ngen_s) ──
        from deap import tools as _tools

        mu = NSGA2_CONFIG["mu"]
        lamb = NSGA2_CONFIG["lambda_"]
        qc = QNSGA2_CONFIG
        tb.register("evaluate", ev)
        gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])

        # 初始观测
        pop = []
        for qi in qpop:
            for _ in range(qc["n_observations"]):
                ind = creator.Individual(qi.observe())
                ind.fitness.values = ev(ind)
                pop.append(ind)
        pop = _tools.selNSGA2(pop, mu)

        feas_count = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
        print(f"   Init: {feas_count}/{mu} feasible")
        if feas_count == 0:
            print(f"   ⚠️  No feasible solutions at stage {s}, using previous assignments")
            # 回退: 活跃居民保持当前分配
            for i in active_local:
                if current_assignments[i] >= 0:
                    frozen_assign[i] = current_assignments[i]
                    frozen_set.add(i)
            continue

        stage_logs = []
        for gen in range(ngen_s):
            fp = [x for x in pop
                  if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            pf = _tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

            dt = gate.delta(gen, ngen_s)
            if pf:
                for qi in qpop:
                    cs = qi.observe_greedy()
                    ci = creator.Individual(cs); ci.fitness.values = ev(ci)
                    gi = random.choice(pf)
                    gate.rotate(qi, cs, list(gi), ci.fitness.values, gi.fitness.values, dt, feasible_s)

            random.shuffle(qpop)
            nq = []
            for k in range(0, len(qpop) - 1, 2):
                if random.random() < NSGA2_CONFIG["cxpb"]:
                    c1, c2 = quantum_crossover(qpop[k], qpop[k+1], qc["q_crossover_rate"])
                    nq.extend([c1, c2])
                else:
                    nq.extend([qpop[k].copy(), qpop[k+1].copy()])
            if len(qpop) % 2 == 1:
                nq.append(qpop[-1].copy())
            for qi in nq:
                if random.random() < NSGA2_CONFIG["mutpb"]:
                    qi.theta = quantum_mutation(qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]).theta
            qpop = nq[:mu]

            ci_ = qc["catastrophe_interval"]
            if ci_ > 0 and (gen+1) % ci_ == 0:
                qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])

            q_off = []
            for qi in qpop:
                for _ in range(qc["n_observations"]):
                    ind = creator.Individual(qi.observe())
                    try: ind.fitness.values = ev(ind)
                    except: ind.fitness.values = (np.inf, np.inf)
                    q_off.append(ind)

            nc = int(lamb * qc["classical_ratio"])
            c_off = algorithms.varOr(pop, tb, lambda_=nc,
                                      cxpb=NSGA2_CONFIG["cxpb"],
                                      mutpb=NSGA2_CONFIG["mutpb"])
            for ind in c_off:
                try: ind.fitness.values = ev(ind)
                except: ind.fitness.values = (np.inf, np.inf)

            pop = tb.select(pop + q_off + c_off, mu)

            vf = [(x.fitness.values[0], x.fitness.values[1]) for x in pop
                  if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            if vf:
                mt, mr = min(f[0] for f in vf), min(f[1] for f in vf)
            else:
                mt, mr = float("inf"), float("inf")

            line = f"S{s} Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e}"
            stage_logs.append(line)
            if gen % 20 == 0 or gen == ngen_s - 1:
                print(f"   {line}")
            logger.log(line)

        # ── 9. 选择最优解 ──
        ff = [x for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        final_pf = _tools.sortNondominated(ff, len(ff), first_front_only=True)[0] if ff else []

        if final_pf:
            best, method = select_solution(final_pf, selection_method)
            sub_assign = list(best)
        else:
            print(f"   ⚠️  Empty Pareto at stage {s}")
            sub_assign = [feasible_s[i][0] if feasible_s[i] else -1
                          for i in range(len(active_local))]

        # ── 10. 冻结已到达居民 ──
        n_frozen_this_stage = 0
        for idx, i in enumerate(active_local):
            j = sub_assign[idx]
            current_assignments[i] = j
            pl = road_paths.get((i, j))
            if pl is None:
                continue
            walk_t = pl.length / speed
            # 判断: 在 t_deadline 前能否到达?
            if walk_t <= (t_deadline - t_decision):
                # 已到达 → 冻结
                frozen_set.add(i)
                frozen_assign[i] = j
                # 记录到达事件 (绝对时间)
                arrival_abs = t_decision + walk_t
                frozen_sink_events.append((j, arrival_abs, float(res_pop[i])))
                n_frozen_this_stage += 1

        print(f"   🧊 Frozen: {n_frozen_this_stage} arrived "
              f"(total frozen: {len(frozen_set)}/{n_res})")
        logger.log(f"Stage {s}: frozen={n_frozen_this_stage} "
                   f"total_frozen={len(frozen_set)}/{n_res}")

        # 记录阶段历史
        stage_history.append({
            "stage": s,
            "pareto_front": final_pf,
            "assignment": sub_assign,
            "active_local": active_local,
            "n_frozen": n_frozen_this_stage,
        })

        # 保存量子种群供下一阶段热启动
        prev_qpop = qpop
        prev_feasible = feasible_s

        # 可视化
        if final_pf:
            pv.record(s, final_pf)

    # ────────────────────────────────────────────────────────────
    #  合并最终结果
    # ────────────────────────────────────────────────────────────
    full_assign = [-1] * len(res_df)
    for idx, i in enumerate(active_idx):
        if current_assignments[idx] >= 0:
            full_assign[i] = current_assignments[idx]

    # 真实指标
    total_t, total_r, arrival = compute_metrics(
        full_assign, res_df, snapped_bus, active_idx, road_paths,
        risk_arrays, x_mins, y_maxs, res_df["pop"].values, snapped_res, speed, max_t)

    used = set(full_assign) - {-1}
    print(f"\n✅ {name} (MULTISTAGE): time={total_t/60:.1f}min  "
          f"risk={total_r:.2e}  stops={len(used)}")
    logger.log(f"RESULT (MULTISTAGE): time={total_t/60:.1f}min  "
               f"risk={total_r:.2e}  stops={len(used)}")

    # 导出
    export_pareto_csv(stage_history[-1]["pareto_front"] if stage_history else [],
                      os.path.join(out, "pareto_front_multistage.csv"))
    pv.plot_final(stage_history[-1]["pareto_front"] if stage_history else [],
                  None, "multistage")

    # 可视化
    plot_assignment_map(risk_arrays[-1], snapped_bus, full_assign, res_df,
                        active_idx, out, suffix="_multistage")

    logger.close()

    return dict(
        group_name=name, gender=config["gender"], age_group=config["age_group"],
        speed=speed, total_time=total_t, total_risk=total_r,
        assignment=full_assign, res_df=res_df, bus_xy=snapped_bus,
        active_indices=active_idx, used_stops=used, paths=road_paths,
        logs=[], pareto_front=stage_history[-1]["pareto_front"] if stage_history else [],
        shelter_mapping_multi=shelter_mapping_multi,
        shelter_load=None, bus_trips_per_stop=None,
        multistage=True, stage_history=stage_history,
    )


# ============================================================
#  单组优化（端到端）
# ============================================================
def optimize_group(config, selection_method="min_risk", accelerate=False,
                   use_gpu=False, n_eval_threads=None, use_sink=True,
                   use_dynamic_shelter=True, use_dynamic_closure=True,
                   use_4stage_safe=False):
    """
    对一个年龄-性别分组执行完整的 Q-NSGA-II 优化流程。

    参数:
        config              – 由 build_group_configs() 生成的配置字典
        selection_method    – Pareto 前沿解选择策略
        accelerate          – True 启用加速引擎 (Numba JIT + 多线程评估)
        use_gpu             – True 启用 CuPy GPU 加速风险计算
        n_eval_threads      – 评估并行线程数 (None = 自动)
        use_sink            – True 启用上车点 sink 边界条件 (巴士调度+排队)
        use_dynamic_shelter – True 启用动态避难所分配 (TOPSIS 多准则决策)
                              False 时回退到 SINK_CONFIG 默认的固定 30 km 往返

    返回:
        result_dict – 包含 assignment, metrics, paths 等全部结果
    """
    name  = config["group_name"]
    speed = config["speed"]
    out   = config["output_dir"]
    max_t = config.get("max_walk_time_minutes", 45) * 60
    os.makedirs(out, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🔬 {name}  speed={speed} m/s  selection={selection_method}")
    print(f"{'='*60}")

    # ---------- 日志 ----------
    logger = EvacLogger(name, out)
    logger.log(f"Group: {name}  Speed: {speed}  Selection: {selection_method}")
    logger.log(f"Config: sink={'ON' if use_sink else 'OFF'}  "
               f"dynamic_shelter={'ON' if use_dynamic_shelter else 'OFF'}  "
               f"dynamic_closure={'ON' if use_dynamic_closure else 'OFF'}  "
               f"4stage_safe={'ON' if use_4stage_safe else 'OFF'}")
    logger.log(f"NSGA2: mu={NSGA2_CONFIG['mu']} lambda={NSGA2_CONFIG['lambda_']} "
               f"ngen={NSGA2_CONFIG['ngen']} cxpb={NSGA2_CONFIG['cxpb']} "
               f"mutpb={NSGA2_CONFIG['mutpb']}")
    logger.log(f"Source: 2-level PSA (U.S. NRC Level 3 PRA Vol.3D)  "
               f"EPZ: dynamic (traditional={5000}m)  "
               f"RoadClip: {ROAD_CLIP_RADIUS}m")
    logger.log(f"RoadHierarchy: {'ON' if ROAD_HIERARCHY_CONFIG.get('enabled', False) else 'OFF'}  "
               f"PickupClosure: {'ON' if use_dynamic_closure else 'OFF'}")

    # ---------- Step 1: 加载数据 ----------
    G, nids, ncoords, kd = load_road_network(
        ROAD_NETWORK_SHP, center=CENTER_UTM, clip_radius=ROAD_CLIP_RADIUS)
    res_df   = load_resident_data(config["pop_file"])
    bus_xy, bus_gdf = load_bus_stops(config["bus_file"])
    risk_arrays, x_mins, y_maxs = load_all_risk_data(RISK_VALUE_FILES)

    active_idx = list(range(len(res_df)))
    bus_list   = list(range(len(bus_xy)))

    logger.log(f"Residents: {len(res_df)}  Bus stops: {len(bus_xy)}")

    # ---------- Step 2: 预计算路径 ----------
    road_paths, snapped_res, snapped_bus, validity = precompute_paths(
        res_df, bus_xy, G, active_idx, bus_list, kd, nids, ncoords)

    # ---------- Step 3: 可行域 ----------
    feasible, no_opt = build_feasible(
        road_paths, len(active_idx), bus_list, max_t, speed)

    # ---------- Step 3.1: 4阶段安全约束 (v5.6) ----------
    # 预计算4个时刻(15/25/35/45min)的安全上车点掩码
    # 安全约束在评估函数中检查: 分配到任一时刻不安全上车点的解→不可行
    # 不做可行域预过滤, 保留搜索空间完整性, 由优化器自然淘汰不安全解
    stage_safe_masks = None
    if use_4stage_safe:
        print(f"\n   🛡️  Computing 4-stage safety masks ...")
        _, stage_safe_masks = compute_4stage_safe_stops(
            snapped_bus, risk_arrays, x_mins, y_maxs, grid_res=400)
        n_safe_per_stage = [int(stage_safe_masks[:, si].sum()) for si in range(4)]
        n_always_safe = int(stage_safe_masks.all(axis=1).sum())
        print(f"   🛡️  4-stage safe: {n_always_safe}/{len(bus_list)} stops always-safe, "
              f"per-stage safe: {n_safe_per_stage}")
        logger.log(f"4-stage safe: {n_always_safe}/{len(bus_list)} always-safe, "
                   f"per-stage: {n_safe_per_stage}")

        # 4阶段安全约束启用时, 自动禁用动态关闭
        # 原因: 4阶段安全是硬约束(全时段检查), 动态关闭是软约束(重定向)
        # 两者叠加导致可行解空间过小, 几乎无解
        if use_dynamic_closure:
            print(f"   ℹ️  4-stage safe ON → dynamic closure auto-disabled (redundant+over-constraining)")
            use_dynamic_closure = False

    # ---------- Step 3.5: 上车点动态关闭 — 污染时间预计算 ----------
    contamination_times = None
    if use_dynamic_closure and PICKUP_CLOSURE_CONFIG.get("enabled", False):
        print(f"\n   🔴 Computing pickup contamination times ...")
        contamination_times, _ = compute_stop_contamination_times(
            snapped_bus, risk_arrays, x_mins, y_maxs,
            grid_res=400, threshold=PICKUP_CLOSURE_CONFIG["closure_threshold"])

    # ---------- Step 3.2: 4阶段安全约束 — 过滤可行域 + 排除无法安全疏散的居民 ----------
    # 对于4阶段安全约束:
    #   1. 将每个居民的可行上车点列表替换为仅含安全上车点的列表
    #      (确保Q-NSGA-II只从安全上车点中选择, 避免初始种群无可行解)
    #   2. 如果某居民的所有可行上车点在到达时刻均不安全, 则排除该居民
    if use_4stage_safe and stage_safe_masks is not None:
        n_excluded_by_safety = 0
        n_stops_filtered = 0
        for il in range(len(feasible)):
            safe_feasible = []
            for j in feasible[il]:
                pl = road_paths.get((il, j))
                if pl is None:
                    continue
                t_walk = pl.length / speed
                arrival_stage = 0
                if t_walk >= 35*60: arrival_stage = 3
                elif t_walk >= 25*60: arrival_stage = 2
                elif t_walk >= 15*60: arrival_stage = 1
                if stage_safe_masks[j, arrival_stage]:
                    safe_feasible.append(j)
            # 替换可行域为仅含安全上车点 (关键修复: 原代码仅排除空列表居民)
            n_removed = len(feasible[il]) - len(safe_feasible)
            if n_removed > 0:
                n_stops_filtered += n_removed
            if safe_feasible:
                feasible[il] = safe_feasible
            elif feasible[il]:
                n_excluded_by_safety += 1
                feasible[il] = []

        if n_stops_filtered > 0:
            print(f"   🛡️  4-stage safety: filtered {n_stops_filtered} unsafe stops from feasible lists")
            logger.log(f"4-stage safety filtered stops: {n_stops_filtered}")
        if n_excluded_by_safety > 0:
            print(f"   ⚠️  4-stage safety: {n_excluded_by_safety} residents excluded "
                  f"(no safe pickup point at arrival time)")
            logger.log(f"4-stage safety excluded: {n_excluded_by_safety}")

    # ---------- Step 4: 过滤无选项居民 ----------
    valid_ai, valid_f, valid_sr = [], [], []
    for il, i in enumerate(active_idx):
        if feasible[il]:
            valid_ai.append(i)
            valid_f.append(feasible[il])
            valid_sr.append(snapped_res[il])
    excl = len(active_idx) - len(valid_ai)
    if excl:
        print(f"   ❌ Excluded {excl} residents (no reachable/safe stop)")
        logger.log(f"Excluded: {excl}")

    # 重映射路径索引
    o2n = {}
    for ni, i in enumerate(valid_ai):
        o2n[active_idx.index(i)] = ni
    new_paths = {(o2n[ol], j): pl
                 for (ol, j), pl in road_paths.items() if ol in o2n}

    active_idx   = valid_ai
    feasible     = valid_f
    snapped_res  = np.array(valid_sr)
    road_paths   = new_paths

    if not active_idx:
        print(f"   ⚠️  No residents with feasible/safe stops — skipping group")
        logger.log("SKIPPED: no residents with feasible/safe stops")
        logger.close()
        return None

    res_pop = res_df["pop"].values[active_idx]
    logger.log(f"Active residents: {len(active_idx)}")

    # ---------- Step 4.5: 避难所多候选预分配 (v5.3 改进 1) ----------
    # 基于 4 准则评分生成 Top K 候选列表 + Dijkstra 真实路网距离
    # sink 阶段使用全局事件调度做多避难所级联
    # 详见 Shelter_Allocation_Manual.docx
    shelter_mapping_multi = None
    shelter_capacities_global = None
    shelter_geometries = None
    shelter_xy_global = None
    if use_sink and use_dynamic_shelter:
        print(f"\n   🏥 Loading shelters and running multi-shelter allocator...")
        try:
            shelter_xy, shelter_cap, shelter_ids, shelter_gdf = load_shelters(SHELTER_FILE)
            shelter_xy_global = shelter_xy
            shelter_capacities_global = shelter_cap

            # 预计算 Dijkstra 真实路网距离 (上车点 → 所有避难所)
            cache_path = os.path.join(out, "shelter_paths_cache.pkl")
            shelter_path_lengths, shelter_geometries = precompute_shelter_paths(
                snapped_bus, shelter_xy, G,
                kdtree=kd, nids=nids, ncoords=ncoords,
                top_k_for_geometry=50,
                cache_path=cache_path,
            )

            selector = ShelterSelector(
                shelter_xy=shelter_xy,
                capacities=shelter_cap,
                risk_arrays=risk_arrays,
                x_mins=x_mins,
                y_maxs=y_maxs,
                grid_res=400,
                config=SHELTER_CONFIG,
            )

            # 估算每个上车点的潜在人口 (按可行集均分)
            stop_pop_est = np.zeros(len(snapped_bus), dtype=np.float64)
            for il in range(len(active_idx)):
                f_list = feasible[il]
                if not f_list:
                    continue
                share = res_pop[il] / len(f_list)
                for j in f_list:
                    stop_pop_est[j] += share

            # v5.3: 多候选分配 (Top 50)
            shelter_mapping_multi = selector.allocate_static_multi(
                bus_xy=snapped_bus,
                bus_demands=stop_pop_est,
                top_k=50,
                shelter_path_lengths=shelter_path_lengths,
            )

            # 诊断: 首选距离分布
            top1_dists = np.array([
                m["candidates"][0]["distance_m"]
                for m in shelter_mapping_multi.values()
                if m.get("candidates")
            ])
            print(f"   ✅ Multi-shelter alloc: "
                  f"{len(shelter_mapping_multi)} stops × Top 50 candidates")
            if len(top1_dists) > 0:
                print(f"   📏 Top1 distances (Dijkstra): "
                      f"mean={top1_dists.mean()/1000:.1f} km, "
                      f"max={top1_dists.max()/1000:.1f} km, "
                      f"min={top1_dists.min()/1000:.1f} km")
            logger.log(
                f"Multi-shelter alloc: stops={len(shelter_mapping_multi)} "
                f"top_k=50 dist_mean={top1_dists.mean():.0f} "
                f"dist_max={top1_dists.max():.0f}"
            )
        except Exception as e:
            print(f"   ⚠️  Multi-shelter allocator failed, falling back to fixed 30km: {e}")
            traceback.print_exc()
            shelter_mapping_multi = None
            shelter_capacities_global = None
    elif use_sink and not use_dynamic_shelter:
        print(f"   🏥 Dynamic shelter OFF — using fixed shelter_distance_m (baseline)")

    # ---------- Step 5: 创建 Pareto 可视化器 ----------
    pv = ParetoVisualizer(out, name)

    # ---------- Step 6: 运行 Q-NSGA-II ----------
    tb = setup_deap(feasible)

    if accelerate:
        from optimizer_accel import (
            make_evaluate_accel, make_evaluate_accel_v4, run_qnsga2_accel,
        )
        print(f"\n   🚀 ACCELERATED MODE (GPU={use_gpu}, threads={n_eval_threads or 'auto'})")
        if use_sink:
            # v4: Numba Phase 2 + Python Phase 3 sink (multi-shelter cascade)
            print(f"   ⚡ Using accel v4 (Numba + sink + multi-shelter cascade)")
            ev = make_evaluate_accel_v4(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_sink=True,
                shelter_mapping_multi=shelter_mapping_multi,
                shelter_capacities=shelter_capacities_global)
        else:
            # v3: 纯加速, 无 sink
            ev = make_evaluate_accel(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_gpu=use_gpu)
    else:
        print(f"   🚏 Sink boundary: {'ON (bus dispatch + queueing)' if use_sink else 'OFF (baseline)'}")
        ev = make_evaluate(
            snapped_res[:, 0], snapped_res[:, 1], res_pop,
            snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
            use_sink=use_sink,
            shelter_mapping_multi=shelter_mapping_multi,
            shelter_capacities=shelter_capacities_global,
            contamination_times=contamination_times,
            pickup_closure_config=PICKUP_CLOSURE_CONFIG if use_dynamic_closure else None,
            feasible_ref=feasible,
            stage_safe_masks=stage_safe_masks)

    # 优化循环：加速模式使用 run_qnsga2_accel，标准模式内联循环（含可视化记录）
    if accelerate:
        _, final_pf, logs = run_qnsga2_accel(
            tb, ev, feasible, logger=logger, n_eval_threads=n_eval_threads)

        # 为 Pareto 可视化补充最终记录
        if final_pf:
            pv.record(NSGA2_CONFIG["ngen"], final_pf)
            pv.plot_current(NSGA2_CONFIG["ngen"], final_pf)
    else:
        # 标准模式：带逐代可视化记录的内联循环
        from deap import tools, creator
        from optimizer import (
            QuantumIndividual, QuantumRotationGate,
            quantum_crossover, quantum_mutation, quantum_catastrophe,
            QNSGA2_CONFIG,
        )
        import random, math
        from deap import algorithms as alg

        mu   = NSGA2_CONFIG["mu"]
        lamb = NSGA2_CONFIG["lambda_"]
        ngen = NSGA2_CONFIG["ngen"]
        qc   = QNSGA2_CONFIG
        tb.register("evaluate", ev)
        gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])

        qpop = [QuantumIndividual(feasible) for _ in range(mu)]
        pop = []
        for qi in qpop:
            for _ in range(qc["n_observations"]):
                ind = creator.Individual(qi.observe())
                ind.fitness.values = ev(ind)
                pop.append(ind)
        pop = tools.selNSGA2(pop, mu)

        feas = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
        print(f"   Init: {feas}/{mu} feasible")
        if feas == 0:
            print(f"   ⚠️  No feasible solutions in initial population — skipping group")
            logger.log("SKIPPED: no feasible solutions in initial population")
            logger.close()
            return None

        logs = []
        for gen in range(ngen):
            fp = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            pf = tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

            dt = gate.delta(gen, ngen)
            if pf:
                for qi in qpop:
                    cs = qi.observe_greedy()
                    ci = creator.Individual(cs); ci.fitness.values = ev(ci)
                    gi = random.choice(pf)
                    gate.rotate(qi, cs, list(gi), ci.fitness.values, gi.fitness.values, dt, feasible)

            random.shuffle(qpop)
            nq = []
            for k in range(0, len(qpop) - 1, 2):
                if random.random() < NSGA2_CONFIG["cxpb"]:
                    c1, c2 = quantum_crossover(qpop[k], qpop[k+1], qc["q_crossover_rate"])
                    nq.extend([c1, c2])
                else:
                    nq.extend([qpop[k].copy(), qpop[k+1].copy()])
            if len(qpop) % 2 == 1:
                nq.append(qpop[-1].copy())
            for qi in nq:
                if random.random() < NSGA2_CONFIG["mutpb"]:
                    qi.theta = quantum_mutation(qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]).theta
            qpop = nq[:mu]

            ci_ = qc["catastrophe_interval"]
            if ci_ > 0 and (gen+1) % ci_ == 0:
                qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])

            q_off = []
            for qi in qpop:
                for _ in range(qc["n_observations"]):
                    ind = creator.Individual(qi.observe())
                    try: ind.fitness.values = ev(ind)
                    except: ind.fitness.values = (np.inf, np.inf)
                    q_off.append(ind)

            nc = int(lamb * qc["classical_ratio"])
            c_off = alg.varOr(pop, tb, lambda_=nc, cxpb=NSGA2_CONFIG["cxpb"], mutpb=NSGA2_CONFIG["mutpb"])
            for ind in c_off:
                try: ind.fitness.values = ev(ind)
                except: ind.fitness.values = (np.inf, np.inf)

            pop = tb.select(pop + q_off + c_off, mu)

            vf = [(x.fitness.values[0], x.fitness.values[1]) for x in pop
                  if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            inf_c = mu - len(vf)
            if vf:
                mt, mr = min(f[0] for f in vf), min(f[1] for f in vf)
                fp2 = [x for x in pop if np.isfinite(x.fitness.values[0])]
                ps = len(tools.sortNondominated(fp2, len(fp2), first_front_only=True)[0]) if fp2 else 0
            else:
                mt, mr, ps = float("inf"), float("inf"), 0

            line = f"Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e} | PF {ps} | Inf {inf_c}"
            logs.append(line)
            logger.log(line)
            logger.log_gen(gen+1, mt, mr, ps, inf_c, dt)

            if gen % 20 == 0 or gen == ngen - 1:
                print(f"   {line}")

            feasible_combined = [x for x in pop + q_off + c_off
                                 if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            if feasible_combined:
                cur_pf = tools.sortNondominated(feasible_combined, len(feasible_combined), first_front_only=True)[0]
                pv.record(gen+1, cur_pf, feasible_combined)
                if (gen+1) % PARETO_VIZ["save_interval"] == 0 or gen == ngen - 1:
                    pv.plot_current(gen+1, cur_pf, feasible_combined)

        ff = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        final_pf = tools.sortNondominated(ff, len(ff), first_front_only=True)[0] if ff else []
        print(f"✅ Final Pareto: {len(final_pf)} solutions")

    # ---------- Step 7: 选择最优解 ----------
    best, method = select_solution(final_pf, selection_method)
    sub_assign = list(best)

    full_assign = [-1] * len(res_df)
    for idx, i in enumerate(active_idx):
        full_assign[i] = sub_assign[idx]

    # ---------- Step 8: 真实指标 ----------
    total_t, total_r, arrival = compute_metrics(
        full_assign, res_df, snapped_bus, active_idx, road_paths,
        risk_arrays, x_mins, y_maxs, res_df["pop"].values, snapped_res, speed, max_t)

    used = set(full_assign) - {-1}
    print(f"✅ {name}: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")
    logger.log(f"RESULT: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")

    # ---------- Step 8.5: 重放 sink 模型获取最终负载分布 (供可视化) ----------
    # 在已选定的最优解上跑一次 sink 仿真, 得到真实的 shelter_load
    # 这只针对单个选定方案, 不影响 Q-NSGA-II 优化过程
    final_shelter_load = None
    final_bus_trips = None
    if use_sink and use_dynamic_shelter and shelter_mapping_multi is not None:
        try:
            from pickup_sink import PickupSinkModel
            sink_replay = PickupSinkModel(
                snapped_bus, risk_arrays, x_mins, y_maxs, None,
                shelter_mapping_multi=shelter_mapping_multi,
                shelter_capacities=shelter_capacities_global,
            )
            # 用 sub_assign (active_idx 子集对应的分配)
            arrival_arr = np.array([arrival.get(i, 0.0) for i in active_idx],
                                   dtype=np.float64)
            pop_active = res_df["pop"].values[active_idx].astype(np.float64)
            _, _, info_replay = sink_replay.process(
                assignment=sub_assign,
                arrival_times=arrival_arr,
                pop_arr=pop_active,
                walk_risk=0.0,
            )
            final_shelter_load = info_replay.get("shelter_load", {})
            final_bus_trips = info_replay.get("bus_trips_per_stop", {})
            print(f"   📊 Final shelter load: "
                  f"{len(final_shelter_load)} shelters, "
                  f"{info_replay['bus_trips_total']} bus trips, "
                  f"{info_replay['n_overflow_passengers']} overflow pax")
            logger.log(
                f"Final sink replay: n_shelters={len(final_shelter_load)} "
                f"trips={info_replay['bus_trips_total']} "
                f"overflow={info_replay['n_overflow_passengers']}"
            )
        except Exception as e:
            print(f"   ⚠️  Final sink replay failed: {e}")
            traceback.print_exc()

    # ---------- Step 9: 导出 ----------
    export_pareto_csv(final_pf, os.path.join(out, "pareto_front.csv"))
    pv.plot_final(final_pf, best, method)
    pv.plot_summary()

    # ---------- Step 10: 可视化 ----------
    plot_assignment_map(risk_arrays[-1], snapped_bus, full_assign, res_df, active_idx, out)
    plot_evacuation_stages(risk_arrays, snapped_bus, full_assign, res_df,
                           active_idx, road_paths, snapped_res, snapped_bus, out, speed)

    # 第二阶段路径可视化 (改进 3)
    if shelter_mapping_multi is not None and shelter_xy_global is not None:
        try:
            from visualization import plot_phase2_routing
            plot_phase2_routing(
                risk_array=risk_arrays[-1],
                bus_xy=snapped_bus,
                shelter_xy=shelter_xy_global,
                shelter_capacities=shelter_capacities_global,
                shelter_mapping_multi=shelter_mapping_multi,
                shelter_geometries=shelter_geometries,
                shelter_load=final_shelter_load or {},
                used_stops=used,
                output_dir=out,
            )
        except Exception as e:
            print(f"   ⚠️  Phase2 routing visualization failed: {e}")
            traceback.print_exc()

    logger.close()

    return dict(
        group_name=name, gender=config["gender"], age_group=config["age_group"],
        speed=speed, total_time=total_t, total_risk=total_r,
        assignment=full_assign, res_df=res_df, bus_xy=snapped_bus,
        active_indices=active_idx, used_stops=used, paths=road_paths,
        logs=logs, pareto_front=final_pf,
        shelter_mapping_multi=shelter_mapping_multi,
        shelter_load=final_shelter_load,
        bus_trips_per_stop=final_bus_trips,
    )


# ============================================================
#  批量运行
# ============================================================
def _worker(args):
    config, sel, accel, gpu, threads, sink, dyn_shelter, dyn_closure, multistage, four_stage_safe = args
    try:
        if multistage:
            return optimize_group_multistage(
                config, sel, accelerate=accel,
                use_gpu=gpu, n_eval_threads=threads,
                use_sink=sink,
                use_dynamic_shelter=dyn_shelter,
                use_dynamic_closure=dyn_closure)
        else:
            return optimize_group(config, sel, accelerate=accel,
                                  use_gpu=gpu, n_eval_threads=threads,
                                  use_sink=sink,
                                  use_dynamic_shelter=dyn_shelter,
                                  use_dynamic_closure=dyn_closure,
                                  use_4stage_safe=four_stage_safe)
    except Exception as e:
        print(f"❌ {config['group_name']}: {e}")
        traceback.print_exc()
        return None


def main(selected_groups=None, parallel=True, n_workers=DEFAULT_N_WORKERS,
         selection_method="min_risk", accelerate=False,
         use_gpu=False, n_eval_threads=DEFAULT_EVAL_THREADS, use_sink=True,
         use_dynamic_shelter=True, use_dynamic_closure=True,
         use_multistage=False, use_4stage_safe=False):
    """
    批量运行全部（或指定）分组的 Q-NSGA-II 优化。

    参数:
        selected_groups  – 指定分组名列表，None 表示全部
        parallel         – 是否多进程并行（分组级）
        n_workers        – 并行进程数（None 自动选择）
        selection_method – 解选择策略
        accelerate       – True 启用加速引擎 (Numba + 多线程评估)
        use_gpu          – True 启用 CuPy GPU 风险计算
        n_eval_threads   – 评估线程数 (None = 自动)
        use_sink         – True 启用上车点 sink 边界条件
    """
    t0 = datetime.now()
    print(f"\n{'='*60}")
    mode_str = "MULTISTAGE (Rolling Horizon)" if use_multistage else "SINGLE-STAGE"
    print(f"🔬 Q-NSGA-II Evacuation Optimizer [{mode_str}]")
    print(f"   Time     : {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Selection: {selection_method}")
    print(f"   Sink BC  : {'ON' if use_sink else 'OFF (baseline)'}")
    print(f"   Multistage: {'ON' if use_multistage else 'OFF'}")
    print(f"   4-stage safe: {'ON' if use_4stage_safe else 'OFF'}")
    print(f"{'='*60}")

    configs = build_group_configs()
    if selected_groups:
        configs = [c for c in configs if c["group_name"] in selected_groups]
    if not configs:
        print("❌ No groups found!")
        return None

    print(f"   Groups: {len(configs)}")
    for c in configs:
        print(f"     - {c['group_name']} ({c['speed']} m/s)")

    if parallel and len(configs) > 1:
        nw = n_workers or min(max(1, cpu_count() - 1), len(configs))
        print(f"\n⚙️  Parallel: {nw} workers")
        with Pool(nw) as pool:
            raw = pool.map(_worker,
                           [(c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink, use_dynamic_shelter, use_dynamic_closure, use_multistage, use_4stage_safe)
                            for c in configs])
        results = [r for r in raw if r is not None]
    else:
        results = []
        for c in configs:
            r = _worker((c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink, use_dynamic_shelter, use_dynamic_closure, use_multistage, use_4stage_safe))
            if r:
                results.append(r)

    if results:
        out_xlsx = os.path.join(
            OUTPUT_ROOT, f"qnsga2_results_{selection_method}.xlsx")
        export_results_excel(results, results[0]["bus_xy"], out_xlsx)

        # ─────── 生成 12 组疏散动图 (GIF) ───────
        try:
            from resi_anime import animate_all_groups
            risk_arrays, _, _ = load_all_risk_data(RISK_VALUE_FILES)
            animate_all_groups(results, risk_arrays, OUTPUT_ROOT)
        except Exception as e:
            print(f"⚠️  Animation generation failed: {e}")
            import traceback
            traceback.print_exc()

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n{'='*60}")
    print(f"🎉 Done: {len(results)}/{len(configs)} groups  "
          f"({elapsed:.0f}s / {elapsed/60:.1f}min)")
    print(f"{'='*60}")
    return results


# ============================================================
#  命令行入口
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Q-NSGA-II Evacuation Optimization System")
    parser.add_argument("--groups", nargs="+",
                        help="Groups to process (e.g. m_20-29 f_30-39)")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: only m_20-29")
    parser.add_argument("--serial", action="store_true",
                        help="Serial mode (no multiprocessing)")
    parser.add_argument("--workers", type=int, default=DEFAULT_N_WORKERS,
                        help=f"Number of parallel workers (default: {DEFAULT_N_WORKERS})")
    parser.add_argument("--selection", default="min_risk",
                        choices=["min_risk", "min_time", "knee"],
                        help="Pareto solution selection method")
    parser.add_argument("--accel", action="store_true",
                        help="Enable accelerated engine (Numba JIT + thread pool)")
    parser.add_argument("--gpu", action="store_true",
                        help="Enable CuPy GPU risk computation (requires cupy)")
    parser.add_argument("--eval-threads", type=int, default=DEFAULT_EVAL_THREADS,
                        help=f"Number of evaluate threads (default: {DEFAULT_EVAL_THREADS})")
    parser.add_argument("--no-sink", action="store_true",
                        help="Disable pickup point sink boundary (baseline mode)")
    parser.add_argument("--no-dynamic-shelter", action="store_true",
                        help="Disable 4-criterion shelter allocation (use fixed 30km baseline)")
    parser.add_argument("--no-dynamic-closure", action="store_true",
                        help="Disable dynamic pickup point closure (risk-based stop shutdown)")
    parser.add_argument("--4stage-safe", dest="four_stage_safe", action="store_true",
                        help="Enable 4-stage safety constraint (v5.6): "
                             "solutions assigning to stops with non-zero risk "
                             "at any of 15/25/35/45min are infeasible")
    parser.add_argument("--multistage", action="store_true",
                        help="Enable multi-stage rolling horizon optimization (v5.5)")
    parser.add_argument("--no-multistage", action="store_true",
                        help="Force single-stage mode even if MULTISTAGE_CONFIG.enabled=True")

    args = parser.parse_args()
    if args.test:
        args.groups = ["m_20-29"]

    main(
        selected_groups=args.groups,
        parallel=not args.serial,
        n_workers=args.workers,
        selection_method=args.selection,
        accelerate=args.accel,
        use_gpu=args.gpu,
        n_eval_threads=args.eval_threads,
        use_sink=not args.no_sink,
        use_dynamic_shelter=not args.no_dynamic_shelter,
        use_dynamic_closure=not args.no_dynamic_closure,
        use_multistage=args.multistage and not args.no_multistage,
        use_4stage_safe=args.four_stage_safe,
    )
