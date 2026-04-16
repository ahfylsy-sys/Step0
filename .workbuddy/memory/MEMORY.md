# MEMORY.md - 项目长期记忆

_最后更新: 2026-04-16 v7.2_

---

## 项目概述

**核事故厂外居民应急撤离优化系统** — Q-NSGA-II量子非支配排序遗传算法，大亚湾核电站周边居民撤离分配优化。

- 双目标：最小化人口加权撤离时间 + 最小化累积辐射暴露风险
- 12个人口分组（2性别×6年龄），步行速度1.28–2.01 m/s
- PickupSinkModel v7.2：动态关闭+避难所+车队往返（巴士从避难所返回上车点继续接人）
- CVaR风险地图：4996场景×4阶段（15/25/35/45min）
- 道路网络：NetworkX Dijkstra，BPR拥堵模型
- 避难所：678个（Shelter_with_coords.xlsx），≥30km过滤后575个

---

## 模块职责

| 模块 | 行数 | 职责 |
|------|------|------|
| config.py | ~115 | 集中配置：路径、超参、可视化选项 |
| data_loader.py | ~450 | 数据加载+路网预处理+风险过滤 |
| pickup_sink.py | ~660 | 巴士调度仿真（v7.2 往返修正版） |
| optimizer.py | ~540 | Q-NSGA-II优化引擎 |
| main.py | ~540 | 端到端流程+CLI |
| visualization.py | ~575 | Pareto图+地图+动画（CartoDB底图） |
| export.py | ~148 | Excel/CSV导出 |
| optimizer_accel.py | ~434 | Numba JIT+CuPy GPU加速（不兼容sink） |
| risk_map.py | ~402 | CVaR风险地图生成管道 |

---

## 关键配置 (config.py)

```
CENTER_UTM = (247413, 2501099)
BUS_DEPOT = (240167, 2501014)  # 大鹏总站
GRID_RES = 400, WALK_SPEED = 2.04, MAX_WALK_TIME = 45*60
NSGA2: mu=200, lambda_=200, ngen=160, cxpb=0.7, mutpb=0.2
QNSGA2: n_observations=3, delta_theta_max=0.05π, catastrophe_interval=50
ROAD_CONGESTION: BPR alpha=0.15, beta=4.0, ped_flow_rate=75
SHELTER: n_shelters=0(自动), radius_m=30000, capacity=10000
RISK_CLOSURE_THRESHOLD = 0.0
PICKUP_RISK_FILTER: enabled=True, risk_threshold=0.0
ROAD_DISTANCE_FACTOR = 1.3
数据路径: E:\CITYU@WORK\WORK-2\Data
```

---

## PickupSinkModel v7.2 关键逻辑

### SINK_CONFIG
```
bus_capacity=50, fleet_size=200, bus_speed=30km/h
dispatch_delay=600s, boarding_time_per_pax=0
risk_closure_threshold=0.0, max_evac_duration=14400 (4h)
inevac_penalty=1e6, road_distance_factor=1.3
```

### 状态机 (process方法)
- **Case A**: 巴士在上车点有载客 → 继续装载(累计载客)或去避难所
- **Case B**: 巴士在depot/避难所/空站 → 寻找最佳开放上车点
- 往返循环: shelter(卸客) → 开放上车点(接人) → shelter → ...

### v7.2 修复的3个Bug
1. **载客量覆盖**: 旧代码 `load = min(bus_capacity, remaining[j])` 忽略已有载客 → 新代码 `space_left = bus_capacity - cur_load; new_load = min(space_left, remaining[j])`
2. **满载判断**: 旧代码 `is_full = (load >= bus_capacity)` 只看单轮 → 新代码 `is_full = (total_load >= bus_capacity)` 看累计
3. **巴士在站无处理**: 旧代码巴士在站清空后无出路 → 新代码 Case A2 直接去避难所

### 距离计算
- depot→上车点: Dijkstra (在路网裁剪10km内)
- 上车点↔避难所: Euclidean×1.3 (避难所≥30km超出路网范围)
- shelter→stop返程: stop_shelter_dist (对称, 同上)

### 关闭时间
- 遍历4阶段风险矩阵, 首次超阈值即关闭
- 未被覆盖的上车点: closure_time = max_evac_duration (14400s)

---

## 关键修改点速查

1. 新目标函数 → optimizer.py make_evaluate()
2. 巴士调度参数 → pickup_sink.py SINK_CONFIG
3. 算法超参 → config.py NSGA2_CONFIG/QNSGA2_CONFIG
4. 新人口分组 → data_loader.py build_group_configs()
5. 风险计算 → risk_map.py + data_loader.py
6. 加速引擎与sink不兼容 → optimizer_accel.py
7. 道路拥堵 → config.py ROAD_CONGESTION_CONFIG
8. 可视化 → visualization.py
9. 避难所 → config.py SHELTER_CONFIG + data_loader.py
10. 风险关闭阈值 → config.py RISK_CLOSURE_THRESHOLD

---

## 运行注意

- Windows GBK编码: 需 `$env:PYTHONIOENCODING="utf-8"` 再运行
- 单组测试约23分钟 (160代×200个体)
- 测试命令: `python main.py --test --serial`
- CLI: --test, --serial, --workers, --selection, --accel, --gpu, --no-sink

---

## 版本历史摘要

- **v5.5**: 多阶段滚动视界
- **v5.6**: 四阶段安全约束
- **v5.7**: BPR道路拥堵模型
- **v7.0**: 巴士转运+避难所+动画重构 (Shelter_with_coords.xlsx, CartoDB底图)
- **v7.1**: 风险过滤(filter_stops_by_risk) + Euclidean×1.3避难所距离修正
- **v7.2**: 往返逻辑修正 — 载客量累计、满载判断、巴士返程状态机
