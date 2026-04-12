# 模型配置修改日志

> 本文件记录 MODEL_CONFIG_NARRATIVE.md 的所有修改历史，包括确认过程和代码同步情况。
> 每次修改后，确认语句从主文档移除，转记于此。

---

## 2026-04-12 v5.6 四阶段安全约束

### 新增功能

**四阶段安全约束 (4-Stage Safety Constraint)**：预计算4个时刻(15/25/35/45min)各上车点的安全掩码，在优化过程中确保居民仅被分配到在到达时刻安全的上车点。

- **安全掩码**: `(n_bus, 4)` bool矩阵，`[j, si]` True=该上车点在阶段si风险值为0
- **到达阶段判定**: 步行时间→阶段映射 (<15min→0, [15,25)→1, [25,35)→2, ≥35min→3)
- **可行域过滤**: 移除在到达阶段不安全的上车点，排除无法安全疏散的居民
- **评估硬约束**: 分配到不安全上车点的解返回 (inf, inf)
- **自动禁用动态关闭**: 启用4阶段安全时，动态关闭自动禁用（冗余+过度约束）

### 运行结果

- **命令**: `python main.py --workers 4 --eval-threads 3 --4stage-safe`
- **耗时**: 1320s (22.0 min)
- **状态**: 12/12 分组全部成功
- **疏散人口**: 17,756
- **使用上车点**: 12/26 (14个被安全约束排除)
- **步行时间范围**: 1.0 ~ 37.9 min
- **步行距离范围**: 115 ~ 3,751 m

### 代码改动

| 文件 | 改动 |
|------|------|
| data_loader.py | 新增 `compute_4stage_safe_stops()` 函数 |
| optimizer.py | `make_evaluate()` 新增 `stage_safe_masks` 参数及4阶段安全检查逻辑 |
| main.py | 新增 `use_4stage_safe` 参数、`--4stage-safe` CLI参数、Step 3.1安全掩码计算、Step 3.2可行域安全过滤 |
| MODEL_CONFIG_NARRATIVE.md | 新增 items 77-82 (四阶段安全约束配置) |
| CHANGELOG.md | 新增 v5.6 变更记录 |
| V56_FOUR_STAGE_SAFETY_REPORT.md | **新建** — 完整分析报告 |

### CLI 用法

```bash
# 启用4阶段安全约束
python main.py --workers 4 --eval-threads 3 --4stage-safe

# 4阶段安全 + 多阶段滚动时域 (未来融合)
python main.py --workers 4 --eval-threads 3 --4stage-safe --multistage
```

### 版本号

v5.5 → **v5.6**

---

## 2026-04-12 v5.5 多阶段滚动时域优化

### 新增功能

**多阶段滚动时域 Q-NSGA-II (MRH-QNSGA2)**：将45分钟疏散窗口分解为4个顺序决策阶段，每个阶段根据最新风险态势对未到达居民重新优化。

- **决策时刻**: t=0, 15, 25, 35 min
- **各阶段迭代代数**: 100, 60, 40, 20 (递减)
- **热启动**: 70%从上一阶段量子种群继承 + 30%随机注入
- **上车点关闭**: t=0时用15min风险矩阵判断（非零风险=禁用），后续阶段递推
- **冻结机制**: 已到达上车点的居民分配冻结，不再参与后续优化

### 文献依据

- Li et al. (2019) J. Environmental Radioactivity — 滚动时域核应急决策框架
- Deb et al. (2007) EMO — 环境变化时热启动NSGA-II
- Rogers et al. (2015) Risk Analysis — NRC分阶段PAR更新机制

### 代码改动

| 文件 | 改动 |
|------|------|
| config.py | 新增 MULTISTAGE_CONFIG 配置块 |
| data_loader.py | 新增 rebuild_feasible_for_stage()、compute_intermediate_positions() |
| optimizer.py | 新增 hot_start_quantum_population()、make_evaluate_stage() |
| main.py | 新增 optimize_group_multistage()、--multistage CLI参数 |
| visualization.py | plot_assignment_map() 新增 suffix 参数 |
| MULTISTAGE_DESIGN.md | **新建** — 完整技术分析报告（含3方案对比） |

### CLI 用法

```bash
# 单阶段模式 (默认, 与v5.4一致)
python main.py --workers 4 --eval-threads 3

# 多阶段滚动时域模式
python main.py --workers 4 --eval-threads 3 --multistage

# 测试模式 + 多阶段
python main.py --test --multistage
```

### 版本号

v5.4 → **v5.5**

---

## 2026-04-12 刘兄修改 + 小佑审核同步

### 修改范围
起始总述、第1节（研究区域与事故场景）、第5节（道路网络）、第6节（可行域）、第11节（NSGA-II算法参数）

### 详细变更

#### 起始总述
- **新增**：文档修改工作流指令——修改后告知小佑修改了哪几大块，小佑对比原文档同步更新代码
- **新增**：确认项处理规则——确认后转为配置，确认语句移至本日志，主文档移除确认语句
- **新增**：本MD视为模型的skill文件

#### 第1节 · 研究区域与事故场景
- **Item 2 变更**：事故源项从 LOCA 改为 2级PSA分析所得源项类（Source term categories），数据来源为 U.S. NRC Level 3 PRA Project, Volume 3D
- **Item 3 变更**：紧急计划区从固定 5000m EPZ 改为量化时变风险场动态计划区，传统EPZ仅作参考基线
- **代码同步**：`config.py` lines 17-23 已更新（源项注释 + EPZ_TRADITIONAL_RADIUS + 动态EPZ说明）

#### 第5节 · 道路网络
- **Item 17 新增**：道路等级优先——主干道优先选择，按高级别→低级别排序优化
- **实现方式**：边权重 = length × cost_multiplier，级别越高乘数越小（主干道0.70-0.80，支路1.00，小路1.40-1.60）
- **代码同步**：`config.py` ROAD_HIERARCHY_CONFIG（lines 122-147）+ `data_loader.py` load_road_network 道路等级识别与加权（lines 146-203）+ Dijkstra使用weight权重规划路径（line 249）

#### 第6节 · 可行域
- **Item 20 新增**：动态上车点关闭机制
- **刘兄原始描述**：到达前关闭→重定向；到达后留下等巴士（但不确定是否合理）
- **小佑审核结论**：方案合理，确认采纳。理由：
  1. 到达前重定向：避免走进污染区，逻辑正确
  2. 到达后留等：重定向反而增加暴露时间，巴士提供快速撤离通道，排队辐射已由queue_risk模块计算
  3. 当前二元阈值制在scope内足够，未来可扩展"严重污染强制撤离"级别
- **代码同步**：`config.py` PICKUP_CLOSURE_CONFIG（lines 149-164）+ `data_loader.py` compute_stop_contamination_times（lines 433-485）+ `optimizer.py` make_evaluate 动态关闭与重定向逻辑（lines 216-283）+ `main.py` --no-dynamic-closure CLI参数

#### 第11节 · NSGA-II算法参数
- **Item 44 确认**：种群大小 μ = 100，子代数量 λ = 100，迭代代数 G_max = 100
- **确认过程**：论文 Table 2 为 μ=400, λ=400, G=160，刘兄确认以代码值 100/100/100 为准
- **代码同步**：`config.py` NSGA2_CONFIG 已是此值，无需修改

#### 待确认项清理
- **Item 69**（NSGA2参数确认）：已确认，移除待确认标记
- **Item 70**（main.py Bug）：export_results_excel重复调用 + elapsed重复计算——已在当前代码中修复，移除待确认标记

### 涉及文件
| 文件 | 修改类型 |
|------|----------|
| `config.py` | 新增源项注释、动态EPZ说明、ROAD_HIERARCHY_CONFIG、PICKUP_CLOSURE_CONFIG |
| `data_loader.py` | 道路等级识别与加权Dijkstra、compute_stop_contamination_times |
| `optimizer.py` | make_evaluate 新增 contamination_times / pickup_closure_config 参数及重定向逻辑 |
| `main.py` | 新增 use_dynamic_closure 参数、--no-dynamic-closure CLI、污染时间预计算调用 |
| `MODEL_CONFIG_NARRATIVE.md` | 更新 item 20/44/69/70，新增修改记录 |
| `CHANGELOG.md` | 新建本文件 |
