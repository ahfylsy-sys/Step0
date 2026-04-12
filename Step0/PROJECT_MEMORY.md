# PROJECT_MEMORY.md

> **本文件用途**：为长对话压缩 (compaction) 后的上下文恢复提供关键信息。
> Claude 在新对话开始时应先读此文件以快速进入项目状态。
>
> **Last updated**: 2026-04-09 (第 3 版 - v5.3 多避难所级联调度)
> **Update policy**: 每 5-10 轮对话后由 Claude 主动重新生成，保留 "Last updated" 时间戳
> **File location**: `/mnt/user-data/outputs/evac_v2/PROJECT_MEMORY.md`

---

## 项目简介

**作者**: 刘胜禹

**项目目标**: 基于 Q-NSGA-II (量子+NSGA-II 混合进化算法) 的核事故疏散优化系统，应用场景为**深圳大亚湾核电站**周边居民疏散。核心任务是把每位居民分配到一个上车点，双目标最小化:
1. 人口加权步行时间 + sink 阶段延迟
2. 累积辐射暴露剂量 (步行 + 排队期间)

**研究区域**:
- 核电厂中心: CENTER_UTM = (247413, 2501099), UTM-50N
- 路网裁剪半径: 10 km
- 风险场: 4 个时间阶段 (15/25/35/45 min), 来自 CVaR 预计算
- 12 个分组: male/female × 6 年龄段 (20-29 ... 70+), 步行速度 1.28–2.01 m/s

---

## 系统演进历史 (从旧到新)

| 版本 | 主要变化 | 状态 |
|---|---|---|
| v1 | 13 个独立脚本 | 已归档 |
| v2 | 合并为 3 脚本 | 已归档 |
| v3 | 单文件 qnsga2_simple.py (831 行) | 已归档 |
| v4 `evac_system/` | 7 模块化 + Numba 加速 | 已归档 |
| v4.5 `agent_evac/` | 双层 Agent-QNSGA-II (出发延迟+速度波动) | 已归档 |
| v5 `evac_v2/` | + 上车点 sink 边界条件 (PickupSinkModel) | 基础 |
| v5.1 `evac_v2/` | + 4 指标动态避难所分配 (ShelterSelector, 1-to-1) | — |
| v5.2 `evac_v2/` | + accel v4 (Numba Phase 2 + sink, 17-20× 加速) | — |
| **v5.3 `evac_v2/`** | **+ 多避难所级联 + Dijkstra 真实路径 + 第二阶段可视化** | **最新** |

---

## 当前系统架构 (evac_v2/)

### 文件结构 (9 个 Python 模块 + 4 个文档)

```
evac_v2/
├── config.py              # 所有参数集中 (SHELTER_CONFIG, SINK_CONFIG, NSGA2_CONFIG ...)
├── data_loader.py         # 路网/居民/上车点/风险场读取 (不含避难所, 见设计决策)
├── optimizer.py           # Q-NSGA-II 主引擎 + make_evaluate
├── optimizer_accel.py     # Numba/GPU 加速版本 (与 sink 不兼容)
├── pickup_sink.py         # sink 边界条件 (v5 新增, v5.1 改动)
├── shelter_selector.py    # 【v5.1 新增】4 指标避难所分配
├── visualization.py       # Pareto 可视化 + 地图
├── export.py              # CSV/Excel 导出 + 日志器
├── main.py                # 端到端主入口
├── Pickup_Sink_Manual.docx
├── Sink_Parameter_Citations.docx    # sink 8 参数文献溯源
├── Shelter_Allocation_Manual.docx   # 【v5.1】shelter 8 篇文献溯源
├── Code_Annotations.md              # 4 核心模块逐块注释
└── PROJECT_MEMORY.md                # 本文件
```

### 关键数据流

```
main.optimize_group:
  Step 1 加载数据 (路网/居民/上车点/风险场)
  Step 2 precompute_paths (Dijkstra)
  Step 3 build_feasible (可行域)
  Step 4 过滤无可行解居民
  Step 4.5 【v5.1 新增】避难所 4 指标分配
          → load_shelters() + ShelterSelector.allocate_static()
          → 生成 shelter_mapping dict: {stop_idx: {distance_m, ...}}
  Step 5 创建 Pareto 可视化器
  Step 6 make_evaluate(..., shelter_mapping=...)
          → 内部创建 PickupSinkModel(shelter_mapping=...)
  Step 7-10 运行 Q-NSGA-II / 选解 / 真实指标 / 导出
```

### sink + shelter 的工作机制

**sink 阶段 (pickup_sink.py)**: 每个上车点是一个独立的离散事件队列。居民按 walk_time 到达 → 排队 → 巴士按 `dispatch_delay + k × round_trip_time` 到达 → 装载 50 人/次 → 往返。排队期间累积 queue_risk。

**shelter 集成点**: `round_trip_time = 2 × distance / bus_speed`。原固定 30 km → 改为 **每个上车点独立**: `sink.shelter_distances[stop_idx] = shelter_mapping[stop_idx]['distance_m']`，fallback 到 `SINK_CONFIG['shelter_distance_m'] = 30000`。

**集成测试结果** (10 个虚拟上车点 + 678 真实避难所):
- 固定 30km 基线: T_sink = 29,909 s (498 min)
- 4 指标动态分配: T_sink = 2,479 s (41 min)
- **12× 降低**，因为实际最近避难所平均距离只有 1.6 km

---

## 关键设计决策

### 决策 1: `load_shelters()` 放在 `shelter_selector.py` 而不是 `data_loader.py`
**理由**:
1. 职责内聚——shelter 子系统完整自包含
2. 耦合最小化——避免 data_loader.py 反向感知 SHELTER_CONFIG
3. 与其他 load_* 函数职责不同——shelter 的读取后立即被 ShelterSelector 消费

**用户决定**: 保持现状 (2026-04-09 确认)

### 决策 2: shelter 分配采用"混合模式" (静态预分配为主 + 动态可选)
**文献依据**: Yin 2023 / Miao 2023 / Song 2024 / Sharbaf 2025 / Pereira-Bish 2015
**实现**: `ShelterSelector.allocate_static()` 为主, `allocate_dynamic()` 可选
**用户选择**: 按推荐 (2026-04-09 确认)

### 决策 3: 4 指标权重 0.2 / 0.2 / 0.2 / 0.4 (偏重风险)
- 距离 (Yin 2023 F1): 0.20
- 容量 (Sun 2024): 0.20
- 负载均衡 (Sharbaf 2025 Gini): 0.20
- **动态风险 (Miao 2023 DDEEM): 0.40** ← 核事故场景优先
**依据**: Ren 2024 报告动态剂量感知路径比最短路径降低 60% 有效剂量
**用户选择**: 按推荐 (2026-04-09 确认)

### 决策 4: --accel 模式 v4 (2026-04-09 实现)
**v4 现在支持 sink + shelter_mapping**, 不再自动回退。
- Phase 1+2: `_eval_kernel_sink` (Numba JIT), 返回 `(walk_time_w, walk_risk, arrivals[])`
- Phase 3: 纯 Python 调 `PickupSinkModel.process`，因为 sink 本身只 O(events) ≈ 1-3 ms，不是瓶颈
- **测试结果**: 19.9× 加速 (5.0 ms/eval → 0.25 ms/eval)，bit-exact 等价
- 旧 `make_evaluate_accel` (v3) 保留作为 `--accel --no-sink` 的路径

### 决策 5: 风险采样沿 stop→shelter **直线**而非路网 Dijkstra
**理由**:
1. 避难所距离 km 级，路网细节对宏观剂量影响 < 20%
2. 评估频率极高 (每代 1700+ 次), 路网查询太慢
3. Miao 2023 DDEEM 也是粗粒度网格，与此近似等效

### 决策 6: v5.3 多避难所级联调度 (2026-04-09 实现)
**用户明确要求**:
1. 候选避难所按 4 指标评分排序, 全部上车点共享同一排序的首选
2. 当首选避难所满载时, "后到的"派遣换到次选 (基于到达时刻判断先后)
3. 一个避难所可同时接收多个上车点 (跨上车点容量竞争)

**Claude 补充的关键设计**:
- **Top K = 50**: 折中性能与稳健性 (用户选择)
- **预订模式**: 巴士发车瞬间扣减容量 (不是到达时), 避免多车冲突 (Pereira & Bish 2015 主流)
- **退化策略 A**: 全部 Top K 满载时, 强制送往首选 + softmax overflow_penalty (与现走机制一致)
- **每车独立 round_trip**: 同一上车点不同班次可能去不同避难所, round_trip_time 必须每辆车独立计算
- **全局事件堆**: 用 `heapq` 维护所有上车点的派车事件, 按时间顺序处理, 保证容量竞争的物理正确性
- **路径**: 改进 3 - Dijkstra 真实路网距离 (用户选择), 每个上车点 single-source Dijkstra 一次, 取 Top 50 几何

**实现位置**:
- `data_loader.py.precompute_shelter_paths()` - Dijkstra + pkl 缓存
- `shelter_selector.py.allocate_static_multi(top_k=50, shelter_path_lengths=...)` - 候选生成
- `pickup_sink.py._simulate_global_scheduler()` - 全局事件堆调度 (~150 行)
- `pickup_sink.py.PickupSinkModel.__init__` 新增 `mode` 字段 ("multi"/"single"/"fixed")
- `optimizer.py / optimizer_accel.py` 接受 `shelter_mapping_multi` + `shelter_capacities`
- `visualization.py.plot_phase2_routing()` - 第二阶段路径可视化 (改进 3)
- `main.py` Step 4.5 调用 multi-shelter, Step 8.5 重放 sink 获取 final shelter_load, Step 10 调用 plot_phase2_routing

---

## 文献引用库 (全部已核实)

### sink 边界条件 (8 个参数)
见 `Sink_Parameter_Citations.docx`, 12 篇完整参考文献。核心:
- **Pereira & Bish 2015** TS 49(4):853–867 — 排队-暴露耦合
- **Zhao 2020** TRA 137:285–300 — round-trip bus model
- **Sun 2024** Prog Nucl Energy 169:105069 — 核电厂限制区
- **Goerigk & Grün 2014** OR Spectrum 36(4):923–948 — dispatch delay
- **TCQSM 3rd Ed.** Ch.6 Exhibit 6-4 — boarding time 2.5s
- **FEMA REP 2023** p.19 Exhibit I-1 — EPZ 16 km

### shelter 分配 (4 指标)
见 `Shelter_Allocation_Manual.docx`, 8 篇核心:
- **Yin, Zhao, Lv 2023** Frontiers in Public Health 10:1098675 — 量子遗传算法 shelter 分配 (**方法学同源**, F1 距离目标 + F2 dispersion penalty)
- **Pereira & Bish 2015** Transportation Science 49(4):853–867 — 距离分配结构性论据
- **Sun, Yuan, Chai, Chen 2024** Prog Nucl Energy 169:105069 — 核电厂容量约束
- **Song et al. 2024** Geomat Nat Haz Risk — NSGA-II + 可达性不等性 (28.80% 削减)
- **Sharbaf et al. 2025** Omega 131:103188 — Gini's Mean Absolute Difference
- **Miao, Zhang, Yu, Shi, Zheng 2023** Energies 16(17):6338 — **DDEEM moving expected dose**
- **Ren, Zhang, Zheng, Miao 2024** Sustainability 16(6):2458 — Gaussian plume + Dijkstra, **60% dose reduction**
- **Choi, Kim, Joo, Moon 2023** Nucl Eng Tech 55(1):261–269 — 韩国核电站大数据 shelter suitability

---

## 命令行接口 (最新)

```bash
# 默认：sink + 4 指标动态避难所分配 (标准模式)
python main.py --test --serial

# 【推荐】启用 v4 加速 (Numba JIT, 约 20× 加速)
# sink + shelter 完全支持, Pareto 前沿数学等价
python main.py --test --serial --accel

# 关闭动态分配, 固定 30 km 基线 (对比用)
python main.py --test --serial --no-dynamic-shelter

# 关闭 sink (最简基线)
python main.py --test --serial --no-sink

# v3 加速路径 (纯 Phase 1+2 向量化, 无 sink)
python main.py --test --serial --accel --no-sink

# 全量并行
python main.py --workers 4 --selection knee

# 解选择: min_risk / min_time / knee
```

---

## 已完成的关键工作

- [x] v5 sink 边界条件 + 文献溯源文档
- [x] v5.1 shelter_selector.py (362 行, 独立测试通过)
- [x] config.py SHELTER_CONFIG 配置块
- [x] pickup_sink.py 接受 shelter_mapping dict
- [x] optimizer.py make_evaluate 接受 shelter_mapping 参数
- [x] main.py Step 4.5 集成 + --no-dynamic-shelter CLI
- [x] Shelter_Allocation_Manual.docx (306 段, 8 篇文献精确到章节)
- [x] Code_Annotations.md (4 核心模块逐块注释, 2474 行)
- [x] 端到端集成测试 (12× T_sink 降低验证)
- [x] v5.2 accel v4: `make_evaluate_accel_v4` + `_eval_kernel_sink` Numba 内核
- [x] main.py --accel 不再自动回退, 直接用 v4 支持 sink + shelter
- [x] v4 等价性测试通过 (bit-exact, 19.9× 加速)
- [x] **v5.3 多避难所级联 (改进 1):**
  - [x] data_loader.py `precompute_shelter_paths()` Dijkstra + pkl 缓存
  - [x] shelter_selector.py `allocate_static_multi()` Top 50 候选, 接受真实路径距离
  - [x] pickup_sink.py 重写 `process()` + `_simulate_global_scheduler()` 全局事件堆调度
  - [x] optimizer.py / optimizer_accel.py 接受 `shelter_mapping_multi` + `shelter_capacities`
  - [x] main.py Step 4.5 调用 multi-shelter, Step 8.5 重放 sink, Step 10 调用 plot_phase2_routing
  - [x] 集成测试: cascade 机制验证 (8 上车点抢小避难所, 实际使用 15 个避难所)
  - [x] v4 加速等价性测试: bit-exact + 17.1× 加速 (与 multi-shelter 兼容)
- [x] **v5.3 第二阶段可视化 (改进 3):**
  - [x] visualization.py `plot_phase2_routing()` 函数 (180 行)
  - [x] 真实 Dijkstra 路径几何 (回退欧氏直线)
  - [x] 避难所利用率渐变着色 (绿→黄→红)
  - [x] 上车点按颜色区分, 标注 ID
  - [x] 烟雾测试通过 (247 KB PNG 输出)
- [x] **v5.3 改进 2 (部分装载)** — 已存在于原 sink 代码, 无需修改

---

## 已知问题与待办

### 🐛 最近修过的 bug
1. **main.py 重复导入 load_shelters** (2026-04-09 修复): 从 `data_loader` 和 `shelter_selector` 都导入了同名函数，导致 `ImportError`。正确的只从 `shelter_selector` 导入。

### 📋 待办 / 可选改进
- [ ] `export.py`: 补充 shelter_id / shelter_distance_m 列，新增 Shelter_Summary sheet (优先级:高)
- [ ] `compute_metrics()` 集成 sink 重放 (当前 total_time 不含 sink 阶段, 与日志中的"final sink replay"对不上)
- [ ] 考虑道路损毁场景的鲁棒性测试 (后续工作)
- [ ] 权重敏感性分析 (w_dist, w_cap, w_load, w_risk 网格扫描)
- [ ] **真实 Daya Bay 数据上的端到端 Dijkstra 性能基准** — 678 shelters × ~15 stops 预计首次 1-2 分钟, 后续秒级缓存命中

---

## 关键约定与风格

- **中文交流**，学术化风格，注释清晰
- **Word 文档生成**用 Node.js + docx 库，中文全角双引号 `""` 会破坏 JS 解析器，用直角引号 `「」` 替代
- **Word 文档必须用 `validate.py` 验证** (`/mnt/skills/public/docx/scripts/office/validate.py`)
- **Pandas 3.x** 的 `to_excel()` 必须用 `sheet_name=` 关键字参数
- **文件输出**: 所有交付物放在 `/mnt/user-data/outputs/evac_v2/`
- **用户的本地路径**: `E:\LIUSHENGYU\WORK2-EVACUATION\Python_script\Workspace\Step0\` (注意是 `Step0`, 不是 `evac_v2`——用户本地可能不完全同步)
- **原数据路径**: `E:\LIUSHENGYU\WORK2-EVACUATION\Data`
- **输出路径**: `E:\LIUSHENGYU\WORK2-EVACUATION\figure`

---

## 参数速查表

### SHELTER_CONFIG (shelter_selector.py)
```python
weight_distance = 0.20   # Yin 2023 F1
weight_capacity = 0.20   # Sun 2024
weight_balance  = 0.20   # Sharbaf 2025
weight_risk     = 0.40   # Miao 2023 DDEEM
max_search_distance_m = 60_000   # 候选最大欧氏距离
min_capacity          = 50       # 剔除过小避难所
capacity_safety_margin = 0.90
overflow_penalty       = 10.0
risk_sample_points    = 5        # 路径采样点数
risk_arrival_stage    = 3        # 假定到达时风险阶段 (最后阶段)
```

### SINK_CONFIG (pickup_sink.py)
```python
bus_capacity         = 50        # 人/车 (GB/T 19260-2018)
fleet_size           = 30        # Pereira & Bish 2015
bus_speed_kmh        = 30.0      # TCQSM 3rd Ed.
dispatch_delay_sec   = 600       # Goerigk & Grün 2014 中位数
boarding_time_per_pax = 2.5      # TCQSM Exhibit 6-4
shelter_distance_m   = 30_000    # 仅作为 fallback
max_evac_duration    = 7200      # 2 h, FEMA REP
```

### 数据规模
- 678 个候选避难所, 总容量 176,948 人
- 567 个通过 min_capacity 筛选
- 容量范围 [10, 5000]

---

## 恢复协议 (Compaction 后使用)

如果你发现上下文被压缩了, 执行以下步骤:

1. 读本文件 `/mnt/user-data/outputs/evac_v2/PROJECT_MEMORY.md`
2. 如有必要, `ls /mnt/user-data/outputs/evac_v2/` 查看所有交付物
3. 如需查代码, 读 `/home/claude/evac_v2/*.py` (工作副本, 可能与 outputs/ 不完全同步)
4. 重要: 用户启用的是 `evac_v2/` 命名, 但本地工作目录是 `Step0/`, 同步时注意

---

*End of PROJECT_MEMORY.md*
