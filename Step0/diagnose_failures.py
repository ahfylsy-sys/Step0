"""
诊断3个失败组 (m_30-39, f_30-39, f_50-59) 无可行解的原因
分析4阶段安全约束对可行域的影响
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
    WALK_SPEEDS, OUTPUT_ROOT, ROAD_NETWORK_SHP, ROAD_CLIP_RADIUS,
    RISK_VALUE_FILES,
)
from data_loader import (
    load_resident_data, load_bus_stops, load_all_risk_data,
    load_road_network, precompute_paths, build_feasible,
    build_group_configs, compute_4stage_safe_stops,
)

# 加载共享数据
print("Loading shared data...")
G, nids, ncoords, kd = load_road_network(
    ROAD_NETWORK_SHP, center=CENTER_UTM, clip_radius=ROAD_CLIP_RADIUS)
bus_xy, bus_gdf = load_bus_stops(BUS_FILE)
risk_arrays, x_mins, y_maxs = load_all_risk_data(RISK_VALUE_FILES)

# 计算4阶段安全掩码
print("\nComputing 4-stage safe masks...")
safe_stops, stage_safe_masks = compute_4stage_safe_stops(
    bus_xy, risk_arrays, x_mins, y_maxs, grid_res=400)

print(f"\n{'='*70}")
print(f"4-stage safety summary:")
n_bus = len(bus_xy)
for si in range(4):
    n_safe = int(stage_safe_masks[:, si].sum())
    n_unsafe = n_bus - n_safe
    print(f"  Stage {si} ({[15,25,35,45][si]}min): {n_safe} safe, {n_unsafe} unsafe stops")

n_always_safe = int(stage_safe_masks.all(axis=1).sum())
n_never_safe = int((~stage_safe_masks).all(axis=1).sum())
n_partial = n_bus - n_always_safe - n_never_safe
print(f"  Always-safe: {n_always_safe}, Partially-contaminated: {n_partial}, Always-contaminated: {n_never_safe}")

# 列出每个不安全上车点的详情
print(f"\nUnsafe stop details:")
for j in range(n_bus):
    if not stage_safe_masks[j, :].all():
        stages_unsafe = [si for si in range(4) if not stage_safe_masks[j, si]]
        print(f"  Stop {j}: unsafe at stages {stages_unsafe}")

# 分析每个分组
configs = build_group_configs()
failing_groups = ["m_30-39", "f_30-39", "f_50-59"]

for config in configs:
    name = config["group_name"]
    speed = config["speed"]
    max_t = 45 * 60
    
    print(f"\n{'='*70}")
    print(f"Group: {name}  Speed: {speed} m/s")
    
    res_df = load_resident_data(config["pop_file"])
    active_idx = list(range(len(res_df)))
    bus_list = list(range(len(bus_xy)))
    
    # 预计算路径
    road_paths, snapped_res, snapped_bus, validity = precompute_paths(
        res_df, bus_xy, G, active_idx, bus_list, kd, nids, ncoords)
    
    # 构建可行域
    feasible, no_opt = build_feasible(
        road_paths, len(active_idx), bus_list, max_t, speed)
    
    n_total = len(active_idx)
    n_with_feasible = sum(1 for f in feasible if f)
    n_no_feasible = n_total - n_with_feasible
    print(f"  Before 4-stage filter: {n_with_feasible}/{n_total} residents have feasible stops, {n_no_feasible} excluded")
    
    if name not in failing_groups:
        # 对成功组也做简要分析
        n_excluded_by_safety = 0
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
            if not safe_feasible and feasible[il]:
                n_excluded_by_safety += 1
        
        n_remaining = n_with_feasible - n_excluded_by_safety
        print(f"  After 4-stage filter: {n_remaining} remaining, {n_excluded_by_safety} excluded by safety")
        continue
    
    # 对失败组做详细分析
    print(f"\n  --- Detailed analysis for FAILING group {name} ---")
    
    # 分析每个居民的可行上车点和安全状态
    resident_details = []
    for il in range(len(feasible)):
        if not feasible[il]:
            resident_details.append({
                'il': il, 'orig_idx': active_idx[il],
                'n_feasible': 0, 'n_safe': 0,
                'feasible_stops': [], 'safe_stops': [],
                'unsafe_reason': 'no reachable stop'
            })
            continue
        
        safe_feasible = []
        unsafe_details = []
        for j in feasible[il]:
            pl = road_paths.get((il, j))
            if pl is None:
                continue
            t_walk = pl.length / speed
            arrival_stage = 0
            if t_walk >= 35*60: arrival_stage = 3
            elif t_walk >= 25*60: arrival_stage = 2
            elif t_walk >= 15*60: arrival_stage = 1
            
            is_safe = stage_safe_masks[j, arrival_stage]
            if is_safe:
                safe_feasible.append(j)
            else:
                # 记录为什么不安全
                unsafe_at = [si for si in range(4) if not stage_safe_masks[j, si]]
                unsafe_details.append({
                    'stop': j, 't_walk_min': t_walk/60,
                    'arrival_stage': arrival_stage,
                    'unsafe_stages': unsafe_at
                })
        
        resident_details.append({
            'il': il, 'orig_idx': active_idx[il],
            'n_feasible': len(feasible[il]),
            'n_safe': len(safe_feasible),
            'feasible_stops': feasible[il],
            'safe_stops': safe_feasible,
            'unsafe_details': unsafe_details,
            'unsafe_reason': 'all stops unsafe at arrival' if (feasible[il] and not safe_feasible) else None
        })
    
    # 统计
    n_no_feasible = sum(1 for r in resident_details if r['n_feasible'] == 0)
    n_no_safe = sum(1 for r in resident_details if r['n_feasible'] > 0 and r['n_safe'] == 0)
    n_has_safe = sum(1 for r in resident_details if r['n_safe'] > 0)
    
    print(f"  Residents with no reachable stops: {n_no_feasible}")
    print(f"  Residents with reachable but no SAFE stops: {n_no_safe}")
    print(f"  Residents with at least 1 safe stop: {n_has_safe}")
    
    if n_no_safe > 0:
        print(f"\n  Residents excluded by 4-stage safety (all stops unsafe at arrival):")
        for r in resident_details:
            if r['n_feasible'] > 0 and r['n_safe'] == 0:
                rx, ry = snapped_res[r['il']]
                dist_to_plant = np.hypot(rx - CENTER_UTM[0], ry - CENTER_UTM[1])
                print(f"    Resident {r['il']} (orig {r['orig_idx']}): "
                      f"dist_to_plant={dist_to_plant:.0f}m, "
                      f"feasible_stops={r['feasible_stops']}")
                for ud in r['unsafe_details']:
                    print(f"      Stop {ud['stop']}: walk={ud['t_walk_min']:.1f}min, "
                          f"arrival_stage={ud['arrival_stage']}, "
                          f"unsafe_at_stages={ud['unsafe_stages']}")
    
    if n_has_safe > 0:
        print(f"\n  Residents with safe stops (sample):")
        count = 0
        for r in resident_details:
            if r['n_safe'] > 0:
                rx, ry = snapped_res[r['il']]
                dist_to_plant = np.hypot(rx - CENTER_UTM[0], ry - CENTER_UTM[1])
                print(f"    Resident {r['il']}: dist={dist_to_plant:.0f}m, "
                      f"feasible={r['n_feasible']}, safe={r['n_safe']}, "
                      f"safe_stops={r['safe_stops']}")
                count += 1
                if count >= 5:
                    break
    
    # 关键问题: 4阶段过滤后是否有居民剩余?
    n_remaining = n_has_safe
    print(f"\n  >>> After 4-stage safety filter: {n_remaining} residents remaining")
    if n_remaining == 0:
        print(f"  >>> THIS GROUP WILL FAIL: no residents with safe stops!")
    elif n_remaining > 0:
        print(f"  >>> This group should have {n_remaining} residents for optimization")
        # 检查是否有其他原因导致无可行解
        # 例如: 初始种群中所有解都被evaluate函数拒绝
        print(f"  >>> Checking if evaluate function might reject all solutions...")
        
        # 模拟构建过滤后的可行域
        valid_f = []
        for r in resident_details:
            if r['n_safe'] > 0:
                valid_f.append(r['safe_stops'])
        
        if valid_f:
            print(f"  >>> Filtered feasible domain: {len(valid_f)} residents")
            # 检查每个居民的safe_stops是否在snapped_bus范围内
            for idx, f_list in enumerate(valid_f):
                for j in f_list:
                    if j >= len(snapped_bus):
                        print(f"    WARNING: Resident {idx} has stop {j} >= {len(snapped_bus)}")
            
            # 尝试构造一个随机解并检查evaluate是否接受
            import random
            test_sol = [random.choice(f) for f in valid_f]
            print(f"  >>> Test solution: {test_sol[:5]}...")
            
            # 手动检查4阶段安全
            all_safe = True
            for idx, j in enumerate(test_sol):
                # 找到对应的原始居民索引
                safe_residents = [r for r in resident_details if r['n_safe'] > 0]
                r = safe_residents[idx]
                pl = road_paths.get((r['il'], j))
                if pl is None:
                    print(f"    WARNING: No path for resident {r['il']} -> stop {j}")
                    all_safe = False
                    continue
                t_walk = pl.length / speed
                arrival_stage = 0
                if t_walk >= 35*60: arrival_stage = 3
                elif t_walk >= 25*60: arrival_stage = 2
                elif t_walk >= 15*60: arrival_stage = 1
                if not stage_safe_masks[j, arrival_stage]:
                    print(f"    WARNING: Stop {j} unsafe at arrival_stage {arrival_stage} for resident {r['il']}")
                    all_safe = False
            
            if all_safe:
                print(f"  >>> Test solution passes 4-stage safety check")
            else:
                print(f"  >>> Test solution FAILS 4-stage safety check - BUG!")

print(f"\n{'='*70}")
print("Diagnosis complete.")
