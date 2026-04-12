# 动态多阶段撤离决策机制 — 技术分析报告

> 由 Claude Code (Max) 分析生成 | 2026-04-12

---

## 一、当前系统架构

当前 v5.4 系统的核心流程是**单阶段优化**：
- t=0 时刻，Q-NSGA-II 为每位居民分配一个上车点（基于全时段风险）
- 风险场分4个离散阶段 [0,15), [15,25), [25,35), [35,45) 但优化时是**一次性决策**
- 动态上车点关闭（v5.4新增）已实现了**被动响应**：evaluate 内检查到达时刻 vs 污染时刻，若到达时已污染则重定向
- 但重定向是在 evaluate 内部的**贪心修补**，不是真正的多阶段重新优化

需求：**主动式多阶段优化**——每个时间窗口根据最新风险态势重新决策。

---

## 二、相关文献综述

### 2.1 动态多目标优化 (DMOO)

| 文献 | 核心方法 | 与本问题的关联 |
|------|----------|----------------|
| **Farina et al. (2004)** "Dynamic Multiobjective Optimization Problems", IEEE Trans. Evol. Comput. | 提出 DMOO 基准问题族 (dMOP), 证明时变环境下 Pareto 前沿形状会漂移 | 理论基础：风险场变化导致 Pareto 前沿漂移 |
| **Deb et al. (2007)** "Dynamic Multi-Objective Optimization and Decision-Making Using Modified NSGA-II", EMO | 环境变化时保留部分精英 + 重新注入随机个体 | 直接适用：阶段切换时可热启动 NSGA-II |
| **Helbig & Engelbrecht (2014)** "Benchmarks for Dynamic Multi-Objective Optimisation", ACM Computing Surveys | 系统综述 DMOO 算法性能，推荐**记忆+预测**策略 | 方法论框架 |
| **Jiang & Yang (2017)** "A Steady-State and Generational Evolutionary Algorithm for Dynamic Multiobjective Optimization", IEEE Trans. Evol. Comput. | SGEA 算法：环境变化检测 + 引导种群向新环境迁移 | 可用于阶段切换时的种群迁移策略 |

### 2.2 时变环境进化算法

| 文献 | 核心思路 | 适用性 |
|------|----------|--------|
| **Nguyen et al. (2012)** "Evolutionary Dynamic Optimization: A Survey", Swarm Evol. Comput. | 综述4大类策略：多样性维护、记忆机制、预测策略、多种群方法 | 框架性参考 |
| **Yazdani et al. (2021)** "A Survey of Evolutionary Continuous Dynamic Optimization Over Two Decades", IEEE Trans. Evol. Comput. | 最新综述：强调**change detection + response**范式 | 适用于阶段切换检测 |
| **Mavrovouniotis et al. (2017)** "A Survey of Swarm Intelligence for Dynamic Optimization", Swarm Evol. Comput. | 蚁群/粒子群在动态环境下的热启动策略 | 量子种群的热启动参考 |

### 2.3 核应急动态决策

| 文献 | 核心内容 | 直接参考价值 |
|------|----------|-------------|
| **Miao et al. (2023)** "Dynamic Dose-Based Emergency Evacuation Model", Energies | DDEEM: 沿路径的移动预期剂量，动态更新撤离路径 | 已被系统引用，路径风险计算范式 |
| **Sun et al. (2024)** "Bus based emergency evacuation...", Progress in Nuclear Energy | 核电厂限制区双层优化，hub选址+路径规划 | 多阶段调度参考 |
| **Li et al. (2019)** "Dynamic decision support for nuclear emergency evacuation", J. Environmental Radioactivity | **滚动时域(rolling horizon)**核应急决策框架 | **核心方法论参考** |
| **Rogers et al. (2015)** "Protective Action Decision-Making for Nuclear Power Plant Emergencies", Risk Analysis | NRC 决策逻辑：分阶段PAR (Protective Action Recommendation) | 政策依据 |

---

## 三、三种可行方案

### 方案 A：滚动时域分解 (Rolling Horizon Decomposition) — ★推荐

**核心思想**：将 45 分钟疏散窗口的多阶段决策问题分解为 4 个顺序执行的单阶段子问题。每个阶段根据当前风险态势、居民到达状态、上车点可用性，对**尚未到达上车点的居民**重新运行 Q-NSGA-II 优化。已到达上车点的居民分配被**冻结**，不再参与后续优化。

类比真实应急指挥：应急指挥中心每 15 分钟接收最新的气象扩散预报，更新一次疏散指令（PAR），对尚在路上的居民发布新的疏散路径指引。

**完整算法流程**：

```
算法: Multi-Stage Rolling Horizon Q-NSGA-II (MRH-QNSGA2)

输入:
    residents[]        — 全部居民列表
    bus_stops[]        — 全部上车点列表
    road_paths{}       — 预计算的路径字典
    risk_arrays[0..3]  — 4 阶段风险矩阵
    speed_g            — 该分组步行速度
    STAGE_DECISION_TIMES = [0, 15, 25, 35]  (分钟)
    STAGE_DEADLINES      = [15, 25, 35, 45]  (分钟)
    NGEN_SCHEDULE        = [100, 60, 40, 20] (各阶段迭代代数)
    HOT_START_RATIO      = 0.7               (热启动种群比例)

输出:
    final_assignment[]  — 每位居民的最终上车点分配
    stage_history[]     — 各阶段的 Pareto 前沿和分配记录

步骤 0: 预处理
    walk_time[i][j] ← road_paths[(i,j)].length / speed_g
    contam_time[j] ← compute_stop_contamination_times(...)
    frozen_set ← ∅, frozen_assign ← {}
    current_pos[i] ← residents[i].home_xy
    prev_PF ← None, prev_qpop ← None

步骤 1-4: 逐阶段滚动优化
    FOR s = 0, 1, 2, 3:
        t_decision ← STAGE_DECISION_TIMES[s] × 60
        t_deadline ← STAGE_DEADLINES[s] × 60
        ngen_s ← NGEN_SCHEDULE[s]

        // 1. 确定活跃居民
        active_s ← {i | i ∉ frozen_set}
        IF active_s = ∅: BREAK

        // 2. 确定可用上车点
        contaminated_stops ← {j | risk_at(bus_stops[j], risk_arrays[s]) > 0}
        available_stops ← all_stops - contaminated_stops

        // 3. 重建可行域
        feasible_s ← rebuild_feasible_for_stage(active_s, available_stops, current_pos)

        // 4. 构建阶段评估函数
        eval_s ← make_evaluate_stage(active_s, feasible_s, s, ...)

        // 5. 热启动种群
        IF s > 0 AND prev_qpop IS NOT None:
            qpop_s ← hot_start_quantum_population(prev_qpop, active_s, feasible_s, HOT_START_RATIO)
        ELSE:
            qpop_s ← QuantumPopulation(feasible_s)

        // 6. 运行 Q-NSGA-II (代数 = ngen_s)
        PF_s, best_s, qpop_new ← run_qnsga2(eval_s, qpop_s, ngen=ngen_s)

        // 7. 冻结已到达居民
        FOR i IN active_s:
            j ← best_s[i]
            walk_t ← walk_time[i][j]
            IF walk_t ≤ t_deadline:
                frozen_set ← frozen_set ∪ {i}
                frozen_assign[i] ← j
            ELSE:
                current_pos[i] ← 沿路径 t_deadline 时刻的位置

        // 8. 记录
        prev_PF ← PF_s, prev_qpop ← qpop_new
        stage_history.append(PF_s, best_s)

    RETURN frozen_assign, stage_history
```

**热启动策略**：
- 70% 种群从上一阶段最优解的量子角度继承（已冻结居民的基因位固定，活跃居民的基因位保留）
- 30% 种群随机初始化（注入多样性，防止陷入局部最优）
- 量子角度继承时，已冻结居民的角度设为 π/4（均匀分布，不影响采样），活跃居民的角度从上一阶段继承

---

### 方案 B：全局多阶段编码 + 降维策略

**核心思想**：将整个 45 分钟窗口视为一个统一的优化问题。个体编码同时包含所有 4 个阶段的变量，Q-NSGA-II 一次运行搜索全局最优策略。

**编码设计**（固定长度 = 4N）：
```
基因位 [0, N):       Stage 0 分配 — j_i^(0)
基因位 [N, 2N):      Stage 1 分配 — j_i^(1) (重定向目标)
基因位 [2N, 3N):     Stage 2 分配 — j_i^(2)
基因位 [3N, 4N):     Stage 3 分配 — j_i^(3)
```

**优点**：理论上能发现"故意在 Stage 0 选择非最优上车点、因为预见到 Stage 1 风险变化后该选择反而更优"的全局最优策略。

**缺点**：
- 搜索空间 4N 维，100代×100种群几乎不可能收敛
- 量子编码需要重写（维度变化）
- DEAP 框架兼容性差
- 工程实现风险极高（核心引擎重写）

---

### 方案 C：单次运行 + 阶段性重评估（轻量方案）

**核心思想**：保持当前单次 Q-NSGA-II 运行框架不变，但在评估函数内增加多阶段模拟逻辑。每个个体评估时，按4阶段模拟居民运动，阶段切换时根据当前风险场动态判断是否需要重定向。

**优点**：代码改动最小（~200行），计算量几乎不增加。

**缺点**：重定向策略是评估函数内部的贪心修补，不是真正的重新优化；20代可能不够收敛；学术创新性弱。

---

## 四、三种方案对比

### 表 1：解质量

| 维度 | 方案 A | 方案 B | 方案 C |
|------|:---:|:---:|:---:|
| 理论全局最优性 | 次优（贪心分解） | 最优（全局搜索） | 次优（贪心修补） |
| 实际收敛质量 | **高**（每阶段充分搜索） | 低（4N维难收敛） | 中（20代不够） |
| 阶段间协调性 | 中（顺序贪心） | 理论最优 | 低 |
| 对风险突变的响应 | **强**（每阶段重新优化） | 强（已编码） | 弱（修补式） |

### 表 2：计算效率

| 维度 | 方案 A | 方案 B | 方案 C |
|------|:---:|:---:|:---:|
| 编码维度 | N (每阶段独立) | 4N | N |
| 评估复杂度 | O(N) × 4阶段 | O(4N) 串行模拟 | O(N) + 修补 |
| 12组×4并行 (Python) | **~12.6 min** | ~33 min | ~7.5 min |
| 12组×4并行 (Numba) | **~37.5 s** | ~100 s | ~24 s |
| 计算量倍率 (vs 单阶段) | 1.5× | 4× | 0.9× |

### 表 3：工程实现

| 维度 | 方案 A | 方案 B | 方案 C |
|------|:---:|:---:|:---:|
| 代码新增行数 | ~640 行 | ~800+ 行 | ~200 行 |
| 修改 QuantumIndividual | 新增热启动函数 | **重写** | 不修改 |
| DEAP框架兼容性 | **高** | **低** | **高** |
| 是否影响现有功能 | 否（新增入口） | 是（核心重写） | 否（新增入口） |

### 表 4：综合评分 (1-5分，5分最优)

| 维度 | 方案 A | 方案 B | 方案 C |
|------|:---:|:---:|:---:|
| 解质量 | 4 | 5(理论)/2(实际) | 3 |
| 计算效率 | 4 | 2 | **5** |
| 实现难度 (低=好) | 3 | 1 | **5** |
| 鲁棒性 | **5** | 3 | 2 |
| 学术价值 | **5** | 4 | 2 |
| 工程风险 (低=好) | 4 | 1 | **5** |
| **综合推荐** | **25/30** | 16/30 | 22/30 |

---

## 五、推荐方案 A 的具体实现

### 5.1 config.py 新增配置

```python
MULTISTAGE_CONFIG = dict(
    enabled              = True,
    stage_decision_times = [0, 15, 25, 35],     # 决策时刻 (分钟)
    stage_deadlines      = [15, 25, 35, 45],     # 各阶段截止时刻 (分钟)
    ngen_schedule        = [100, 60, 40, 20],    # 各阶段迭代代数
    hot_start_ratio      = 0.7,                  # 热启动种群比例
    closure_threshold    = 0.5,                   # 上车点关闭阈值 (CVaR)
)
```

### 5.2 data_loader.py 新增函数

```python
def rebuild_feasible_for_stage(active_indices, available_stops, 
                                current_positions, road_paths, 
                                speed, max_walk_time):
    """为阶段 s 重建可行域"""
    feasible = {}
    for i in active_indices:
        x, y = current_positions[i]
        feasible[i] = []
        for j in available_stops:
            pl = road_paths.get((i, j))
            if pl is None:
                continue
            t = pl.length / speed
            if t <= max_walk_time:
                feasible[i].append(j)
    return feasible
```

### 5.3 optimizer.py 新增函数

```python
def hot_start_quantum_population(prev_qpop, active_indices, feasible_new, ratio=0.7):
    """从上一阶段量子种群热启动"""
    n_total = len(feasible_new)
    n_inherit = int(n_total * ratio)
    qpop = []
    
    # 继承部分：从 prev_qpop 的最优解继承角度
    for k in range(n_inherit):
        qi = QuantumIndividual(feasible_new)
        # 活跃居民的角度从 prev 继承
        for i in active_indices:
            if i in prev_qpop[0].feasible and i in qi.feasible:
                # 找到公共上车点的角度映射
                ...
        qpop.append(qi)
    
    # 随机部分：注入多样性
    for k in range(n_total - n_inherit):
        qpop.append(QuantumIndividual(feasible_new))
    
    return qpop
```

### 5.4 main.py 新增入口

```python
def optimize_group_multistage(config, selection_method, ...):
    """多阶段滚动时域优化入口"""
    # 加载数据
    # 逐阶段调用 run_qnsga2
    # 合并冻结分配
    # 返回最终结果
```

### 5.5 CLI 参数

```python
parser.add_argument("--multistage", action="store_true",
                    help="Enable multi-stage rolling horizon optimization (v5.5)")
parser.add_argument("--no-multistage", action="store_true",
                    help="Force single-stage mode")
```

### 5.6 Sink 模型兼容

冻结居民的到达事件合并到活跃居民的到达事件列表中，sink 模型的全局事件调度器自然考虑冻结居民对巴士和避难所容量的占用。

---

## 六、计算复杂度评估

| 模式 | 单阶段 | 4阶段 (方案A) | 倍率 |
|------|--------|--------------|------|
| Python, 12组 (4并行) | 8.4 min | **12.6 min** | 1.5× |
| Numba, 12组 (4并行) | 25 s | **37.5 s** | 1.5× |

关键加速因素：
1. 后期阶段活跃居民大幅减少 → 评估向量变短
2. 迭代代数递减 [100, 60, 40, 20] → 总代数 220 vs 单阶段 400
3. 热启动收敛更快 → 实际可能不需要跑满预设代数

**不会导致解空间爆炸**：每阶段独立优化，解空间大小 = max(各阶段) 而非乘积。

---

## 七、实施路线

| 步骤 | 内容 | 预计改动 |
|------|------|----------|
| Step 1 | config.py 新增 MULTISTAGE_CONFIG | +15行 |
| Step 2 | data_loader.py 新增 rebuild_feasible_for_stage() | +30行 |
| Step 3 | optimizer.py 新增 hot_start 函数 + staged evaluate | +200行 |
| Step 4 | main.py 新增 optimize_group_multistage() | +250行 |
| Step 5 | pickup_sink.py 兼容冻结居民 | +30行 |
| Step 6 | CLI 参数 + 日志 | +20行 |
| Step 7 | 可视化/导出扩展 | +100行 |
| **合计** | | **~640行新增 + ~70行修改** |

版本号建议：**v5.5**
