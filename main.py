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
    CENTER_UTM, RISK_VALUE_FILES, ROAD_NETWORK_SHP,
    OUTPUT_ROOT, ROAD_CLIP_RADIUS, NSGA2_CONFIG, PARETO_VIZ,
    BUS_DEPOT,
)
from data_loader import (
    load_resident_data, load_bus_stops, load_all_risk_data,
    load_road_network, precompute_paths, build_feasible,
    build_group_configs, build_congestion_data,
    load_shelters, filter_stops_by_risk,
)
from config import ROAD_CONGESTION_CONFIG
from pickup_sink import SINK_CONFIG
from optimizer import (
    setup_deap, make_evaluate, run_qnsga2,
    select_solution, compute_metrics,
)
from visualization import (
    ParetoVisualizer, plot_assignment_map, plot_evacuation_stages,
    plot_bus_animation,
)
from export import (
    EvacLogger, export_pareto_csv, export_results_excel,
)


# ============================================================
#  单组优化（端到端）
# ============================================================
def optimize_group(config, selection_method="min_risk", accelerate=False,
                   use_gpu=False, n_eval_threads=None, use_sink=True):
    """
    对一个年龄-性别分组执行完整的 Q-NSGA-II 优化流程。

    参数:
        config           – 由 build_group_configs() 生成的配置字典
        selection_method – Pareto 前沿解选择策略
        accelerate       – True 启用加速引擎 (Numba JIT + 多线程评估)
        use_gpu          – True 启用 CuPy GPU 加速风险计算
        n_eval_threads   – 评估并行线程数 (None = 自动)
        use_sink         – True 启用上车点 sink 边界条件 (巴士调度+排队)

    返回:
        result_dict – 包含 assignment, metrics, paths 等全部结果
    """
    name  = config["group_name"]
    speed = config["speed"]
    out   = config["output_dir"]
    max_t = config.get("max_walk_time_minutes", 45) * 60
    os.makedirs(out, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Group {name}  speed={speed} m/s  selection={selection_method}")
    print(f"{'='*60}")

    # ---------- 日志 ----------
    logger = EvacLogger(name, out)
    logger.log(f"Group: {name}  Speed: {speed}  Selection: {selection_method}")

    # ---------- Step 1: 加载数据 ----------
    G, nids, ncoords, kd = load_road_network(
        ROAD_NETWORK_SHP, center=CENTER_UTM, clip_radius=ROAD_CLIP_RADIUS)
    res_df   = load_resident_data(config["pop_file"])
    bus_xy, bus_gdf = load_bus_stops(config["bus_file"])
    risk_arrays, x_mins, y_maxs = load_all_risk_data(RISK_VALUE_FILES)

    active_idx = list(range(len(res_df)))
    bus_list   = list(range(len(bus_xy)))

    # ---------- Step 1.5: 上车点风险过滤 ----------
    # 排除在 t≤15min 已被风险场覆盖的上车点 (距核电厂过近)
    safe_stop_indices = filter_stops_by_risk(
        bus_xy, risk_arrays, x_mins, y_maxs)
    bus_list = [j for j in bus_list if j in safe_stop_indices]
    if not bus_list:
        raise ValueError("No safe bus stops after risk filtering!")
    print(f"   Safe bus stops: {len(bus_list)}/{len(bus_xy)}")

    # 预估总人口 (用于确定避难所数量)
    total_pop_est = float(res_df["pop"].sum())
    shelter_xy, shelter_capacities = load_shelters(
        center=CENTER_UTM, total_pop=total_pop_est)

    logger.log(f"Residents: {len(res_df)}  Bus stops: {len(bus_xy)}")

    # ---------- Step 2: 预计算路径 ----------
    road_paths, snapped_res, snapped_bus, validity, path_node_seqs = precompute_paths(
        res_df, bus_xy, G, active_idx, bus_list, kd, nids, ncoords)

    # ---------- Step 3: 可行域 ----------
    # 传入巴士可达性参数: 巴士必须在上车点关闭前到达
    feasible, no_opt = build_feasible(
        road_paths, len(active_idx), bus_list, max_t, speed,
        bus_xy=bus_xy, risk_arrays=risk_arrays, x_mins=x_mins, y_maxs=y_maxs,
        depot_xy=np.array(BUS_DEPOT),
        bus_speed_ms=SINK_CONFIG["bus_speed_ms"],
        dispatch_delay_sec=SINK_CONFIG["dispatch_delay_sec"],
    )

    # ---------- Step 4: 过滤无选项居民 ----------
    valid_ai, valid_f, valid_sr = [], [], []
    for il, i in enumerate(active_idx):
        if feasible[il]:
            valid_ai.append(i)
            valid_f.append(feasible[il])
            valid_sr.append(snapped_res[il])
    excl = len(active_idx) - len(valid_ai)
    if excl:
        print(f"   Excluded {excl} residents (no reachable stop)")
        logger.log(f"Excluded: {excl}")

    # 重映射路径索引
    o2n = {}
    for ni, i in enumerate(valid_ai):
        o2n[active_idx.index(i)] = ni
    new_paths = {(o2n[ol], j): pl
                 for (ol, j), pl in road_paths.items() if ol in o2n}
    new_node_seqs = {(o2n[ol], j): ns
                     for (ol, j), ns in path_node_seqs.items() if ol in o2n}

    active_idx   = valid_ai
    feasible     = valid_f
    snapped_res  = np.array(valid_sr)
    road_paths   = new_paths
    path_node_seqs = new_node_seqs

    if not active_idx:
        raise ValueError("No residents with feasible stops!")

    res_pop = res_df["pop"].values[active_idx]
    logger.log(f"Active residents: {len(active_idx)}")

    # ---------- Step 4.3: 道路拥挤度数据构建 (v5.7) ----------
    congestion_data = None
    if ROAD_CONGESTION_CONFIG.get("enabled", False):
        congestion_data = build_congestion_data(path_node_seqs, G, speed, max_t)

    # ---------- Step 5: 创建 Pareto 可视化器 ----------
    pv = ParetoVisualizer(out, name)

    # ---------- Step 6: 运行 Q-NSGA-II ----------
    tb = setup_deap(feasible)

    if accelerate:
        from optimizer_accel import make_evaluate_accel, run_qnsga2_accel
        print(f"\n   ACCELERATED MODE (GPU={use_gpu}, threads={n_eval_threads or 'auto'})")
        if use_sink:
            print(f"   Sink boundary not supported in accel mode, falling back to standard evaluate")
            # 计算上车点和避难所对应的路网节点
            stop_nodes_eval = []
            for j in range(len(snapped_bus)):
                _, ni = kd.query(snapped_bus[j])
                stop_nodes_eval.append(int(nids[ni]))
            shelter_nodes_eval = []
            for si in range(len(shelter_xy)):
                _, ni = kd.query(shelter_xy[si])
                shelter_nodes_eval.append(int(nids[ni]))
            _, depot_ni_eval = kd.query(np.array(BUS_DEPOT))
            depot_node_eval = int(nids[depot_ni_eval])

            ev = make_evaluate(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_sink=True, congestion_data=congestion_data,
                shelter_xy=shelter_xy, shelter_capacities=shelter_capacities,
                depot_xy=np.array(BUS_DEPOT),
                road_graph=G,
                stop_nodes=stop_nodes_eval,
                shelter_nodes=shelter_nodes_eval,
                depot_node=depot_node_eval)
        else:
            ev = make_evaluate_accel(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_gpu=use_gpu)
    else:
        print(f"   Sink boundary: {'ON (bus dispatch + queueing)' if use_sink else 'OFF (baseline)'}")
        # 计算上车点和避难所对应的路网节点 (供evaluate闭包使用)
        stop_nodes_eval = []
        for j in range(len(snapped_bus)):
            _, ni = kd.query(snapped_bus[j])
            stop_nodes_eval.append(int(nids[ni]))
        shelter_nodes_eval = []
        for si in range(len(shelter_xy)):
            _, ni = kd.query(shelter_xy[si])
            shelter_nodes_eval.append(int(nids[ni]))
        _, depot_ni_eval = kd.query(np.array(BUS_DEPOT))
        depot_node_eval = int(nids[depot_ni_eval])

        ev = make_evaluate(
            snapped_res[:, 0], snapped_res[:, 1], res_pop,
            snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
            use_sink=use_sink, congestion_data=congestion_data,
            shelter_xy=shelter_xy, shelter_capacities=shelter_capacities,
            depot_xy=np.array(BUS_DEPOT),
            road_graph=G,
            stop_nodes=stop_nodes_eval,
            shelter_nodes=shelter_nodes_eval,
            depot_node=depot_node_eval)

    # 优化循环
    if accelerate:
        _, final_pf, logs = run_qnsga2_accel(
            tb, ev, feasible, logger=logger, n_eval_threads=n_eval_threads)
        if final_pf:
            pv.record(NSGA2_CONFIG["ngen"], final_pf)
            pv.plot_current(NSGA2_CONFIG["ngen"], final_pf)
    else:
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
            raise ValueError("No feasible solutions!")

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
        print(f"Final Pareto: {len(final_pf)} solutions")

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

    # ---------- Step 8.5: 获取巴士轨迹数据 (v7.0: 含路网路径) ----------
    bus_trajectory = []
    if use_sink:
        try:
            from pickup_sink import PickupSinkModel
            # 计算上车点和避难所对应的路网节点
            stop_nodes = []
            for j in range(len(snapped_bus)):
                _, ni = kd.query(snapped_bus[j])
                stop_nodes.append(int(nids[ni]))
            shelter_nodes = []
            for si in range(len(shelter_xy)):
                _, ni = kd.query(shelter_xy[si])
                shelter_nodes.append(int(nids[ni]))
            _, depot_ni = kd.query(np.array(BUS_DEPOT))
            depot_node = int(nids[depot_ni])

            sink_model = PickupSinkModel(
                snapped_bus, risk_arrays, x_mins, y_maxs,
                shelter_xy, shelter_capacities, SINK_CONFIG,
                depot_xy=np.array(BUS_DEPOT),
                road_graph=G,
                stop_nodes=stop_nodes,
                shelter_nodes=shelter_nodes,
                depot_node=depot_node)
            # 计算到达时间
            sub_arrival = []
            for idx, i in enumerate(active_idx):
                j = full_assign[i]
                pl = road_paths.get((idx, j))
                sub_arrival.append(pl.length / speed if pl else np.inf)
            _, _, sink_info = sink_model.process(
                assignment=[full_assign[i] for i in active_idx],
                arrival_times=np.array(sub_arrival, dtype=np.float64),
                pop_arr=res_df["pop"].values[active_idx],
            )
            bus_trajectory = sink_info.get("bus_trajectory", [])
        except Exception as e:
            print(f"   Failed to get bus trajectory: {e}")
            import traceback
            traceback.print_exc()

    used = set(full_assign) - {-1}
    print(f"Group {name}: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")
    logger.log(f"RESULT: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")

    # ---------- Step 9: 导出 ----------
    export_pareto_csv(final_pf, os.path.join(out, "pareto_front.csv"))
    pv.plot_final(final_pf, best, method)
    pv.plot_summary()

    # ---------- Step 10: 可视化 ----------
    plot_assignment_map(risk_arrays[-1], snapped_bus, full_assign, res_df, active_idx, out)
    plot_evacuation_stages(risk_arrays, snapped_bus, full_assign, res_df,
                           active_idx, road_paths, snapped_res, snapped_bus, out, speed)

    # ---------- Step 10.5: 完整疏散动画 (v7.0) ----------
    if bus_trajectory:
        plot_bus_animation(
            risk_arrays, snapped_bus, shelter_xy, full_assign,
            res_df, active_idx, bus_trajectory, out, speed,
            depot_xy=np.array(BUS_DEPOT),
            road_graph=G,
            road_paths=road_paths,
            snapped_res=snapped_res,
            snapped_bus=snapped_bus)

    logger.close()

    return dict(
        group_name=name, gender=config["gender"], age_group=config["age_group"],
        speed=speed, total_time=total_t, total_risk=total_r,
        assignment=full_assign, res_df=res_df, bus_xy=snapped_bus,
        active_indices=active_idx, used_stops=used, paths=road_paths,
        logs=logs, pareto_front=final_pf,
    )


# ============================================================
#  批量运行
# ============================================================
def _worker(args):
    config, sel, accel, gpu, threads, sink = args
    try:
        return optimize_group(config, sel, accelerate=accel,
                              use_gpu=gpu, n_eval_threads=threads,
                              use_sink=sink)
    except Exception as e:
        print(f"❌ {config['group_name']}: {e}")
        traceback.print_exc()
        return None


def main(selected_groups=None, parallel=True, n_workers=None,
         selection_method="min_risk", accelerate=False,
         use_gpu=False, n_eval_threads=None, use_sink=True):
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
    print(f"🔬 Q-NSGA-II Evacuation Optimizer")
    print(f"   Time     : {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Selection: {selection_method}")
    print(f"   Sink BC  : {'ON' if use_sink else 'OFF (baseline)'}")
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
                           [(c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink)
                            for c in configs])
        results = [r for r in raw if r is not None]
    else:
        results = []
        for c in configs:
            r = _worker((c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink))
            if r:
                results.append(r)

    if results:
        out_xlsx = os.path.join(
            OUTPUT_ROOT, f"qnsga2_results_{selection_method}.xlsx")
        export_results_excel(results, results[0]["bus_xy"], out_xlsx)

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
    parser.add_argument("--workers", type=int, default=None,
                        help="Number of parallel workers")
    parser.add_argument("--selection", default="min_risk",
                        choices=["min_risk", "min_time", "knee"],
                        help="Pareto solution selection method")
    parser.add_argument("--accel", action="store_true",
                        help="Enable accelerated engine (Numba JIT + thread pool)")
    parser.add_argument("--gpu", action="store_true",
                        help="Enable CuPy GPU risk computation (requires cupy)")
    parser.add_argument("--eval-threads", type=int, default=None,
                        help="Number of evaluate threads (default: auto)")
    parser.add_argument("--no-sink", action="store_true",
                        help="Disable pickup point sink boundary (baseline mode)")

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
    )
