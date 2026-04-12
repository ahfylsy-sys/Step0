# 模型配置与假设清单

> 本文件为 Q-NSGA-II 核事故疏散优化系统的**唯一配置真相源**。
> 修改本文件后，由小佑读取并同步更新对应代码文件（config.py / pickup_sink.py / shelter_selector.py / optimizer.py / data_loader.py / main.py 等）。
> 每项配置标注了**当前值**、**代码位置**和**文献依据**（如有）。

---

## 1. 研究区域与事故场景

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 1.1 | 核电厂中心坐标 (UTM-50N) | (247413, 2501099) | `config.py` → `CENTER_UTM` | 大亚湾核电站几何中心 |
| 1.2 | 坐标系 | EPSG:32650 (WGS 84 / UTM Zone 50N) | `config.py` → `CRS_UTM` | 全流程统一投影 |
| 1.3 | 传统EPZ半径 | 5,000 m | `config.py` → `EPZ_TRADITIONAL_RADIUS` | 传统紧急计划区，仅作参考基线；本研究采用动态计划区 |
| 1.4 | 路网裁剪半径 | 10,000 m | `config.py` → `ROAD_CLIP_RADIUS` | GB/T 17680.1-2008; NUREG-0654/FEMA-REP-1 Rev.2 |
| 1.5 | 紧急计划区 | 动态计划区（量化时变风险场） | `config.py` → 注释 | 传统5km EPZ仅作基线，实际边界由CVaR风险场动态确定 |
| 1.6 | 事故源项 | 2级PSA源项类 (Source term categories) | `config.py` → 注释 | U.S. NRC Level 3 PRA Project, Volume 3D |
| 1.7 | 风险场模型 | CVaR 条件风险值大气扩散 | Case_Study_Section.md §4.1 | 高斯烟羽 + α分位数条件尾部期望 |

---

## 2. 时变风险场

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 2.1 | 风险阶段时间节点 | [0, 15, 25, 35, 45] min | `config.py` → `STAGE_TIMES` | 4个离散阶段：[0,15), [15,25), [25,35), [35,45) |
| 2.2 | 风险场网格分辨率 | 400 m | `config.py` → `GRID_RES` | Δx = 400 m 栅格化剂量率矩阵 |
| 2.3 | 风险场数据文件 | `cvar_risk_map_output{15,25,35,45}.xlsx` | `config.py` → `RISK_VALUE_FILES` | 4个Excel文件，每个对应一个阶段 |
| 2.4 | 风险查询方式 | 最近邻（整数行列索引） | `optimizer.py` → `_risk()`; `pickup_sink.py` → `_risk_at()` | 非双线性插值，取最近网格点值 |
| 2.5 | Phase 2 风险累积方式 | 逐分钟沿路径插值位置 × 剂量率 × 人口 × 60s | `optimizer.py` → `make_evaluate()` | 每分钟子区间内剂量率恒定假设 |

---

## 3. 受疏散人群分层

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 3.1 | 性别分组 | male, female | `config.py` → `GENDERS` | 2类 |
| 3.2 | 年龄分组 | 20-29, 30-39, 40-49, 50-59, 60-69, 70+ | `config.py` → `AGE_GROUPS` | 6组 |
| 3.3 | 总分组数 | 12 (2×6) | — | 性别×年龄的笛卡尔积 |
| 3.4 | 步行速度 (m/s) | [2.01, 1.94, 1.87, 1.81, 1.70, 1.55, 1.84, 1.77, 1.72, 1.65, 1.59, 1.28] | `config.py` → `WALK_SPEEDS` | 顺序：m_20-29…m_70+, f_20-29…f_70+; Bohannon 1997; Gates 2006 |
| 3.5 | 人口数据来源 | 普查级网格化CSV | `data_loader.py` → `load_resident_data()` | 每行：id, x, y, pop |
| 3.6 | 人口文件路径 | `{DATA_ROOT}/pop_data/clipped_pop_gender_age_csv/{gender}/all_age_clustering/{name}_cluster.csv` | `data_loader.py` → `build_group_configs()` | 12个独立CSV文件 |

---

## 4. 上车点（巴士停靠站）

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 4.1 | 上车点数据源 | `pickup_poi_all_aggregated_with_blacklist.xlsx` | `config.py` → `BUS_FILE` | EPZ内公共交通POI聚合 |
| 4.2 | 上车点筛选机制 | 黑名单过滤 | Case_Study_Section.md §4.3.1 | 排除军事禁区/路网不连通/不可通行地形 |
| 4.3 | 上车点投影 | WGS84 → UTM-50N | `data_loader.py` → `load_bus_stops()` | Excel含lon/lat列 |
| 4.4 | 上车点路网吸附 | KDTree最近邻 | `data_loader.py` → `precompute_paths()` | 吸附至路网节点 |

---

## 5. 道路网络

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 5.1 | 路网数据源 | `Shenzhen_Roads_Clip.shp` | `config.py` → `ROAD_NETWORK_SHP` | 深圳市道路Shapefile |
| 5.2 | 路网裁剪 | 10 km缓冲区 | `data_loader.py` → `load_road_network()` | 以核电厂中心为圆心 |
| 5.3 | 图类型 | 无向平面图 G=(V,E) | `data_loader.py` → `load_road_network()` | NetworkX Graph |
| 5.4 | 节点合并容差 | 1 mm (0.001 m) | `data_loader.py` → `_node()` 中 `tol=1e-3` | 近邻坐标合并 |
| 5.5 | 边权重 | length (欧氏长度 m) + weight (length × cost_multiplier) | `data_loader.py` → `load_road_network()` | length用于距离计算，weight用于Dijkstra路径规划 |
| 5.6 | 路径算法 | Dijkstra单源最短路（weight权重） | `data_loader.py` → `precompute_paths()` | 严格路网路由，无欧氏回退；使用含等级乘数的weight |
| 5.7 | 不可达处理 | 标记为不可行，不回退直线 | `data_loader.py` → `precompute_paths()` | `validity[(il,j)] = False` |
| 5.8 | 道路等级优先 | 启用 | `config.py` → `ROAD_HIERARCHY_CONFIG["enabled"]` | 主干道优先，高级别道路乘数小 |
| 5.9 | 等级识别字段 | ["fclass", "roadclass", "highway", ...] | `config.py` → `ROAD_HIERARCHY_CONFIG["class_fields"]` | 按优先级尝试，命中第一个即用 |
| 5.10 | 等级成本乘数 | motorway=0.70, trunk=0.75, primary=0.80, secondary=0.90, tertiary=1.00, residential=1.15, service=1.25, path=1.40, track=1.50, footway=1.60 | `config.py` → `ROAD_HIERARCHY_CONFIG["cost_multipliers"]` | 乘数越小越优先选择 |
| 5.11 | 默认等级乘数 | 1.10 | `config.py` → `ROAD_HIERARCHY_CONFIG["default_multiplier"]` | 未匹配到等级时的回退值 |

---

## 6. 可行域

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 6.1 | 最大允许步行时间 | 2,700 s (45 min) | `config.py` → `MAX_WALK_TIME` | NUREG-0654/FEMA-REP-1 Rev.2; GB/T 17680.1-2008 |
| 6.2 | 可行域定义 | F_i = {j ∈ J \| 路径存在 ∧ d_ij/v_g ≤ T_max} | `data_loader.py` → `build_feasible()` | 严格路网连通 + 时间约束 |
| 6.3 | 无可行选项居民 | 从优化中排除 | `main.py` Step 4 | 标记为需替代干预（直升机/就地避难） |
| 6.4 | 可行域缓存 | `.pkl` 文件 | `config.py` → `build_group_configs()` | `feasible_{name}.pkl` |
| 6.5 | 动态上车点关闭 | 启用 | `config.py` → `PICKUP_CLOSURE_CONFIG["enabled"]` | 风险场覆盖时关闭上车点，`--no-dynamic-closure` 可关闭 |
| 6.6 | 关闭阈值 | 0.001 | `config.py` → `PICKUP_CLOSURE_CONFIG["closure_threshold"]` | 上车点剂量率超过此值时关闭（与CVaR矩阵同量级） |
| 6.7 | 重定向策略 | next_feasible | `config.py` → `PICKUP_CLOSURE_CONFIG["redirect_strategy"]` | 从可行集中选下一个未关闭的上车点 |
| 6.8 | 已到达居民处理 | 留在原站等巴士 | `config.py` → `PICKUP_CLOSURE_CONFIG["keep_arrived"]` = True | 重定向增加暴露，巴士提供快速撤离通道 |
| 6.9 | 污染时间预计算 | 4阶段遍历取最早 | `data_loader.py` → `compute_stop_contamination_times()` | 找到首个超过阈值的阶段，记录起始时间 |

---

## 7. 巴士配置（Sink 边界条件）

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 7.1 | 巴士容量 | 50 人/辆 | `pickup_sink.py` → `SINK_CONFIG["bus_capacity"]` | GB/T 19260-2018 |
| 7.2 | 名义车队规模 | 30 辆 | `pickup_sink.py` → `SINK_CONFIG["fleet_size"]` | Pereira & Bish 2015; 仅用于先验可行性检查 |
| 7.3 | 巴士行驶速度 | 30 km/h (8.33 m/s) | `pickup_sink.py` → `SINK_CONFIG["bus_speed_kmh"]` | TCQSM 3rd Ed. |
| 7.4 | 调度响应延迟 | 600 s (10 min) | `pickup_sink.py` → `SINK_CONFIG["dispatch_delay_sec"]` | Goerigk & Grün 2014 |
| 7.5 | 单人上车时间 | 2.5 s/人 | `pickup_sink.py` → `SINK_CONFIG["boarding_time_per_pax"]` | TCQSM Exhibit 6-4 |
| 7.6 | 默认避难所距离（回退值） | 30,000 m | `pickup_sink.py` → `SINK_CONFIG["shelter_distance_m"]` | FEMA REP 2023; 无动态分配时的默认值 |
| 7.7 | 排队风险计算 | 启用 | `pickup_sink.py` → `SINK_CONFIG["queue_risk_enabled"]` = True | 排队期间持续暴露于辐射场 |
| 7.8 | 排队仿真时间步 | 60 s | `pickup_sink.py` → `SINK_CONFIG["queue_dt_sec"]` | — |
| 7.9 | 最大疏散时长 | 7,200 s (2 h) | `pickup_sink.py` → `SINK_CONFIG["max_evac_duration"]` | FEMA REP 2023 |
| 7.10 | 调度模式 | "multi"（多避难所级联） | `pickup_sink.py` → `PickupSinkModel.mode` | v5.3 默认模式 |
| 7.11 | 逐站派遣假设 | 每个上车点独立派车 | Case_Study_Section.md §4.6 | 性能上界假设; Goerigk & Grün 2014 |
| 7.12 | 部分装载 | 允许（队列剩余 < 容量时） | `pickup_sink.py` → `_simulate_global_scheduler()` | 不等满即发车 |

---

## 8. 避难所配置

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 8.1 | 避难所数据源 | `Shelter_with_coords.xlsx` | `config.py` → `SHELTER_FILE` | 深圳市行政避难所数据库 |
| 8.2 | 候选避难所总数 | 678 | Case_Study_Section.md §4.7.1 | 含全部名义容量 |
| 8.3 | 最小可用容量过滤阈值 | 50 人 | `config.py` → `SHELTER_CONFIG["min_capacity"]` | 剔除运营上无意义的过小设施 |
| 8.4 | 过滤后有效候选数 | 567 | Case_Study_Section.md §4.7.1 | 容量 ≥ 50 的避难所 |
| 8.5 | 单个避难所容量范围 | 10 ~ 5,000 人 | Case_Study_Section.md §4.7.1 | 过滤前 |
| 8.6 | 总名义容量 | 176,948 人 | Case_Study_Section.md §4.7.1 | 678个避难所合计 |
| 8.7 | 候选搜索最大距离 | 60,000 m | `config.py` → `SHELTER_CONFIG["max_search_distance_m"]` | 超出EPZ的安全裕度 |
| 8.8 | KDTree候选预筛数 | max(2K, 100) = 100 | `shelter_selector.py` → `allocate_static_multi()` | 欧氏最近邻预筛 |

---

## 9. 四指标加权避难所评分

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 9.1 | 权重：距离 | 0.20 | `config.py` → `SHELTER_CONFIG["weight_distance"]` | Yin 2023 F1 (P-median) |
| 9.2 | 权重：容量 | 0.20 | `config.py` → `SHELTER_CONFIG["weight_capacity"]` | Sun 2024 容量约束 |
| 9.3 | 权重：负载均衡 | 0.20 | `config.py` → `SHELTER_CONFIG["weight_balance"]` | Song 2024 Gini; Sharbaf 2025 |
| 9.4 | 权重：动态风险 | 0.40 | `config.py` → `SHELTER_CONFIG["weight_risk"]` | Miao 2023 DDEEM; Ren 2024 |
| 9.5 | 评分阶段容量安全裕度 | 0.90 | `config.py` → `SHELTER_CONFIG["capacity_safety_margin"]` | 保留10%余量 |
| 9.6 | 溢出惩罚倍数 | 10.0 | `config.py` → `SHELTER_CONFIG["overflow_penalty"]` | 容量超载时硬性排除 |
| 9.7 | 风险采样点数 | 5 | `config.py` → `SHELTER_CONFIG["risk_sample_points"]` | 直线上等距5点 |
| 9.8 | 风险评估阶段 | 3 (35-45 min后) | `config.py` → `SHELTER_CONFIG["risk_arrival_stage"]` | 最晚可用阶段 |
| 9.9 | 风险采样方式 | 直线采样（非Dijkstra路径） | `shelter_selector.py` → `_path_risk()` | km级距离下路网绕行影响可忽略 |
| 9.10 | 归一化方法 | min-max → [0,1] | `shelter_selector.py` → `_score_one_stop()` | 各指标独立归一化 |
| 9.11 | 多候选模式负载均衡项 | 0（评分阶段置零） | `shelter_selector.py` → `allocate_static_multi()` | 容量竞争由sink调度器动态解决 |
| 9.12 | 随机种子（tie-break） | 42 | `config.py` → `SHELTER_CONFIG["deterministic_seed"]` | 评分相同时的确定性打破 |

---

## 10. 多避难所级联调度（v5.3）

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 10.1 | 每个上车点候选数 (Top K) | 50 | `main.py` Step 4.5 → `top_k=50` | 按评分升序的有序候选列表 |
| 10.2 | 调度阶段容量安全裕度 | 0.95 | `pickup_sink.py` → `shelter_remaining *= 0.95` | 略宽于评分阶段的0.90 |
| 10.3 | 容量扣减方式 | 预订模式（出发瞬间扣减） | `pickup_sink.py` → `_simulate_global_scheduler()` | Pereira & Bish 2015 |
| 10.4 | 级联降级策略 | 遍历Top K，首选满足容量的 | `pickup_sink.py` → `_simulate_global_scheduler()` | 第一个 remaining_cap ≥ load |
| 10.5 | 全部候选耗尽时 | 强制送往首选避难所 + overflow计数 | `pickup_sink.py` → `_simulate_global_scheduler()` | 软惩罚通过适应度函数传递 |
| 10.6 | 每车独立往返时间 | 是 | `pickup_sink.py` → `_simulate_global_scheduler()` | 同一上车点不同班次可能去不同避难所 |
| 10.7 | 全局事件调度器 | heapq优先队列 | `pickup_sink.py` → `_simulate_global_scheduler()` | 按巴士到达时间排序 |
| 10.8 | 上车点→避难所距离 | Dijkstra真实路网距离 | `main.py` → `precompute_shelter_paths()` | 有pkl缓存机制 |

---

## 11. Q-NSGA-II 算法参数

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 11.1 | 种群大小 μ | 100 | `config.py` → `NSGA2_CONFIG["mu"]` | 已确认（论文Table 2为400，以代码值为准） |
| 11.2 | 子代数量 λ | 100 | `config.py` → `NSGA2_CONFIG["lambda_"]` | 已确认（论文Table 2为400，以代码值为准） |
| 11.3 | 迭代代数 G_max | 100 | `config.py` → `NSGA2_CONFIG["ngen"]` | 已确认（论文Table 2为160，以代码值为准） |
| 11.4 | 交叉概率 p_c | 0.7 | `config.py` → `NSGA2_CONFIG["cxpb"]` | — |
| 11.5 | 变异概率 p_m | 0.2 | `config.py` → `NSGA2_CONFIG["mutpb"]` | — |
| 11.6 | 单基因变异概率 p_ind | 0.1 | `config.py` → `NSGA2_CONFIG["indpb"]` | — |
| 11.7 | 量子观测次数 n_obs | 3 | `config.py` → `QNSGA2_CONFIG["n_observations"]` | 每个量子个体每代观测3次 |
| 11.8 | 旋转门最大角度 Δθ_max | 0.05π | `config.py` → `QNSGA2_CONFIG["delta_theta_max"]` | 早期探索 |
| 11.9 | 旋转门最小角度 Δθ_min | 0.001π | `config.py` → `QNSGA2_CONFIG["delta_theta_min"]` | 后期开发 |
| 11.10 | 量子交叉率 | 0.5 | `config.py` → `QNSGA2_CONFIG["q_crossover_rate"]` | 角度空间逐维交换 |
| 11.11 | 量子变异率 | 0.15 | `config.py` → `QNSGA2_CONFIG["q_mutation_rate"]` | — |
| 11.12 | 量子变异扰动 | 0.1π | `config.py` → `QNSGA2_CONFIG["q_mutation_perturbation"]` | — |
| 11.13 | 灾变间隔 | 50 代 | `config.py` → `QNSGA2_CONFIG["catastrophe_interval"]` | 防止早熟 |
| 11.14 | 灾变率 | 0.1 | `config.py` → `QNSGA2_CONFIG["catastrophe_rate"]` | 重初始化10%量子种群 |
| 11.15 | 经典子代比例 | 0.3 | `config.py` → `QNSGA2_CONFIG["classical_ratio"]` | 30%子代来自经典GA算子 |
| 11.16 | 旋转门非支配缩放 | 0.3× | `optimizer.py` → `QuantumRotationGate.rotate()` | 引导解不支配时缩小旋转幅度 |

---

## 12. 双目标函数

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 12.1 | 目标1 (f₁) | 人口加权步行时间 + sink延迟 | `optimizer.py` → `make_evaluate()` | f₁ = Σp_i·d_ij/v_g + sink_extra_time |
| 12.2 | 目标2 (f₂) | 步行累积辐射 + 排队辐射 | `optimizer.py` → `make_evaluate()` | f₂ = walk_risk + queue_risk |
| 12.3 | 优化方向 | 双目标最小化 | `optimizer.py` → `setup_deap()` | weights=(-1.0, -1.0) |
| 12.4 | 不可行解惩罚 | (inf, inf) | `optimizer.py` → `make_evaluate()` | 超时/路径缺失返回无穷大 |
| 12.5 | sink额外时间计算 | max(0, T_sink - max_walk) × 总人口 | `optimizer.py` → `make_evaluate()` | 步行与sink的加权差额 |

---

## 13. 解选择策略

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 13.1 | 默认选择策略 | min_risk | `main.py` → `--selection` 默认值 | 最小化f₂ |
| 13.2 | 可选策略 | min_time, min_risk, knee | `optimizer.py` → `select_solution()` | knee = Pareto前沿曲率最大点 |
| 13.3 | knee计算方式 | 归一化后距对角线距离最大 | `optimizer.py` → `select_solution()` | |nt+nr-1|/√2 |

---

## 14. 加速引擎

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 / 文献依据 |
|------|--------|--------|----------|----------------|
| 14.1 | Numba JIT | 可选 | `optimizer_accel.py` | Phase 1+2 向量化内核 |
| 14.2 | CuPy GPU | 可选 | `optimizer_accel.py` | 批量风险矩阵索引 |
| 14.3 | 多线程评估 | 可选 | `optimizer_accel.py` → `batch_evaluate()` | ThreadPoolExecutor |
| 14.4 | 加速模式默认 | 关闭 | `main.py` → `--accel` | 需显式启用 |
| 14.5 | v4加速（含sink） | Numba Phase1+2 + Python Phase3 | `optimizer_accel.py` → `make_evaluate_accel_v4()` | sink部分保持纯Python |
| 14.6 | 单次评估延迟（纯Python） | ~5 ms | Case_Study_Section.md §4.8 | Shapely逐分钟插值 |
| 14.7 | 单次评估延迟（Numba） | ~0.25 ms | Case_Study_Section.md §4.8 | 约20倍加速 |

---

## 15. 数据路径

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 |
|------|--------|--------|----------|------|
| 15.1 | 数据根目录 | `E:\CITYU@WORK\WORK-2\Data` | `config.py` → `DATA_ROOT` | 原始输入数据 |
| 15.2 | 输出根目录 | `E:\Q-NSGA2-Results` | `config.py` → `OUTPUT_ROOT` | 全部结果输出 |
| 15.3 | 风险场文件 | `{DATA_ROOT}/cvar_risk_map_output{t}.xlsx` | `config.py` → `RISK_VALUE_FILES` | t=15,25,35,45 |
| 15.4 | 路网文件 | `{DATA_ROOT}/road_data/Shenzhen_Roads_Clip.shp` | `config.py` → `ROAD_NETWORK_SHP` | — |
| 15.5 | 上车点文件 | `{DATA_ROOT}/pickup_poi_all_aggregated_with_blacklist.xlsx` | `config.py` → `BUS_FILE` | — |
| 15.6 | 避难所文件 | `{DATA_ROOT}/Shelter_with_coords.xlsx` | `config.py` → `SHELTER_FILE` | — |
| 15.7 | 人口文件 | `{DATA_ROOT}/pop_data/clipped_pop_gender_age_csv/{gender}/all_age_clustering/{name}_cluster.csv` | `data_loader.py` → `build_group_configs()` | — |
| 15.8 | 可行域缓存 | `{DATA_ROOT}/feasible_domain/feasible_{name}.pkl` | `data_loader.py` → `build_group_configs()` | — |

---

## 16. 可视化配置

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 |
|------|--------|--------|----------|------|
| 16.1 | 图像尺寸 | (14, 12) | `config.py` → `VIZ_CONFIG["figsize"]` | 英寸 |
| 16.2 | DPI | 150 | `config.py` → `VIZ_CONFIG["dpi"]` | — |
| 16.3 | 风险色带 | 绿→黄→橙→红→暗红 | `config.py` → `VIZ_CONFIG["risk_colors"]` | 5级 |
| 16.4 | Pareto可视化保存间隔 | 每20代 | `config.py` → `PARETO_VIZ["save_interval"]` | — |
| 16.5 | 底图 | contextily瓦片 | `visualization.py` | 自动下载 |

---

## 17. 动画配置

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 |
|------|--------|--------|----------|------|
| 17.1 | 动画时长 | 45 min | `resi_anime.py` | 对应疏散时间窗 |
| 17.2 | 帧间隔 | 30 s | `resi_anime.py` | 每30秒一帧 |
| 17.3 | 总帧数 | ~90 | `resi_anime.py` | 45×60/30 |
| 17.4 | 帧率 | 6 fps | `resi_anime.py` | GIF播放速度 |
| 17.5 | 每帧最大居民数 | 3,000 | `resi_anime.py` | 超出则随机采样 |
| 17.6 | 输出格式 | GIF (PillowWriter) | `resi_anime.py` | — |

---

## 18. 运行配置

| 编号 | 配置项 | 当前值 | 代码位置 | 说明 |
|------|--------|--------|----------|------|
| 18.1 | 默认并行模式 | 多进程 (multiprocessing.Pool) | `main.py` → `main()` | 分组级并行 |
| 18.2 | 默认工作进程数 | cpu_count()-1 | `main.py` → `main()` | 自动选择 |
| 18.3 | Sink边界条件 | 默认启用 | `main.py` → `--no-sink` 关闭 | — |
| 18.4 | 动态避难所分配 | 默认启用 | `main.py` → `--no-dynamic-shelter` 关闭 | — |
| 18.5 | 动态上车点关闭 | 默认启用 | `main.py` → `--no-dynamic-closure` 关闭 | 风险场覆盖时关闭上车点 |
| 18.6 | 测试模式 | 仅运行 m_20-29 | `main.py` → `--test` | — |

---

## ⚠️ 已知不一致项

| 编号 | 问题描述 | 状态 | 说明 |
|------|----------|------|------|
| A1 | NSGA2种群大小 μ=100 vs 论文Table 2 μ=400 | ✅ 已确认 | 以代码值100为准（2026-04-12刘兄确认） |
| A2 | NSGA2子代数量 λ=100 vs 论文Table 2 λ=400 | ✅ 已确认 | 以代码值100为准（2026-04-12刘兄确认） |
| A3 | NSGA2迭代代数 G=100 vs 论文Table 2 G=160 | ✅ 已确认 | 以代码值100为准（2026-04-12刘兄确认） |
| A4 | main.py重复导出Excel | ✅ 已修复 | 当前代码仅调用一次 |
| A5 | main.py重复计算elapsed | ✅ 已修复 | 当前代码仅计算一次 |

---

## 修改记录

| 日期 | 修改内容 | 修改人 |
|------|----------|--------|
| 2026-04-12 | 初始版本生成 | 小佑 |
| 2026-04-12 | 同步刘兄修改：1.3-1.6源项/动态EPZ；5.8-5.11道路等级优先；6.5-6.9动态上车点关闭；11.1-11.3确认移除⚠️；18.5动态关闭CLI；A1-A5全部已确认/已修复 | 小佑 |
