"""
配置模块 — 集中管理所有可配置参数
所有路径、算法超参、可视化选项均在此定义，运行时只需修改本文件。
"""
import os
import math

# ======================== 基础物理参数 ========================
WALK_SPEED      = 2.04          # 默认步行速度 (m/s)
GRID_RES        = 400           # 风险场网格分辨率 (m)
MAX_WALK_TIME   = 45 * 60      # 最大允许步行时间 (s)
CENTER_UTM      = (247413, 2501099)   # 核电厂中心 (UTM-50N)
STAGE_TIMES     = [0, 15, 25, 35, 45] # 疏散阶段时间节点 (min)
CRS_UTM         = "EPSG:32650"
CRS_WGS84       = "EPSG:4326"

# ─── 事故场景参数 ───
# 源项: 采用2级PSA分析所得源项类 (Source term categories)
# 数据来源: U.S. NRC Level 3 PRA Project, Volume 3D
# https://www.nrc.gov/docs/ML2206/ML22067A215.pdf
EPZ_TRADITIONAL_RADIUS = 5_000   # 传统紧急计划区半径 (m)
                                  # 本研究采用量化时变风险场作为动态计划区,
                                  # 传统EPZ仅作参考基线
ROAD_CLIP_RADIUS = 10_000        # 路网裁剪半径 (m), 保留足够路网供疏散

# ─── CVaR 风险场参数 ───
CVAR_ALPHA      = 0.95          # CVaR 条件风险值分位数 (95%)

# ======================== 数据路径 ========================
# 原始数据路径 — 请根据实际存放位置修改
DATA_ROOT   = r"E:\CITYU@WORK\WORK-2\Data"
OUTPUT_ROOT = r"E:\Q-NSGA2-Results"

RISK_VALUE_FILES = [
    os.path.join(DATA_ROOT, f"cvar_risk_map_output{t}.xlsx") for t in [15, 25, 35, 45]
]
ROAD_NETWORK_SHP = os.path.join(DATA_ROOT, "road_data", "Shenzhen_Roads_Clip.shp")
BUS_FILE         = os.path.join(DATA_ROOT, "pickup_poi_all_aggregated_with_blacklist.xlsx")
SHELTER_FILE     = os.path.join(DATA_ROOT, "Shelter_with_coords.xlsx")  # v5.1 新增

# ======================== 人口分组 ========================
AGE_GROUPS   = ["20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
GENDERS      = ["male", "female"]
GENDER_SHORT = {"male": "m", "female": "f"}
# 12 组步行速度 (m/s)：male 20‑29 … 70+, female 20‑29 … 70+
WALK_SPEEDS  = [2.01, 1.94, 1.87, 1.81, 1.70, 1.55,
                1.84, 1.77, 1.72, 1.65, 1.59, 1.28]

# ======================== NSGA-II 基础参数 ========================
NSGA2_CONFIG = dict(
    mu       = 100,    # 种群大小
    lambda_  = 100,    # 子代数量
    ngen     = 100,    # 迭代代数
    cxpb     = 0.7,    # 交叉概率
    mutpb    = 0.2,    # 变异概率
    indpb    = 0.1,    # 单基因变异概率
)

# ======================== Q-NSGA-II 量子参数 ========================
QNSGA2_CONFIG = dict(
    n_observations        = 3,
    delta_theta_max       = 0.05  * math.pi,
    delta_theta_min       = 0.001 * math.pi,
    q_crossover_rate      = 0.5,
    q_mutation_rate        = 0.15,
    q_mutation_perturbation = 0.1 * math.pi,
    catastrophe_interval  = 50,
    catastrophe_rate      = 0.1,
    classical_ratio       = 0.3,
)

# ======================== 可视化参数 ========================
VIZ_CONFIG = dict(
    figsize       = (14, 12),
    dpi           = 150,
    risk_colors   = ['#00ff00', '#ffff00', '#ff8000', '#ff0000', '#8b0000'],
    risk_alpha    = 0.5,
    basemap_alpha = 0.8,
)
PARETO_VIZ = dict(
    figsize        = (12, 10),
    dpi            = 150,
    marker_size    = 60,
    highlight_size = 150,
    save_interval  = 20,
)

# ======================== 并行计算参数 ========================
# 非测试运行时的默认并行配置
DEFAULT_N_WORKERS   = 4     # 分组级并行进程数 (4核)
DEFAULT_EVAL_THREADS = 3    # 每组评估线程数 (3线程)

# ======================== 日志 ========================
LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")

# ======================== 避难所 (Shelter) 配置 ========================
# 基于 Yin 2023 / Miao 2023 / Song 2024 / Choi 2023 的 4 指标加权评分模型
# 详见 Shelter_Allocation_Manual.docx
SHELTER_CONFIG = dict(
    # ─── 4 指标权重 (Yin 2023 F1 + Song 2024 F_inequity + Miao 2023 dose) ───
    # 核事故场景偏重辐射风险
    weight_distance = 0.20,   # 距离/时间效率 (P-median, Yin 2023 F1)
    weight_capacity = 0.20,   # 容量充裕度 (Yin 2023 capacity constraint)
    weight_balance  = 0.20,   # 负载均衡/公平性 (Song 2024 Gini, Sharbaf 2025)
    weight_risk     = 0.40,   # 动态时空剂量 (Miao 2023 DDEEM, Ren 2024)

    # ─── 候选避难所筛选 ───
    max_search_distance_m = 30_000,   # 候选避难所最大搜索距离 (m)
                                       # 与EPZ半径量级一致, 提供安全裕度
    min_capacity          = 50,       # 最小可用容量 (剔除过小避难所)

    # ─── 容量约束参数 ───
    capacity_safety_margin = 0.90,    # 可用容量 = 名义容量 × 此系数
                                      # 保留 10% 余量应对随机波动
    overflow_penalty       = 10.0,    # 容量超载惩罚倍数

    # ─── 风险评估参数 ───
    risk_sample_points    = 5,        # 路径风险采样点数 (起点/中间/终点)
    risk_arrival_stage    = 3,        # 假定到达避难所的风险场阶段 (0-3)
                                      # 3 = 最后阶段 (35-45 min 后)

    # ─── 静态预分配策略 ───
    allow_reassignment    = True,     # 预分配后若容量超限, 允许二次重分配
    deterministic_seed    = 42,       # 打破评分相同时的 tie-break 随机种子
)

# ======================== 道路等级优先配置 ========================
# 优化时优先选择高级别道路（主干道 > 小道）
# 实现: 边权重 = length × cost_multiplier, 级别越高乘数越小
ROAD_HIERARCHY_CONFIG = dict(
    enabled              = True,      # 是否启用道路等级优先
    # ─── 道路等级字段名（按优先级尝试, 命中第一个即用）───
    class_fields         = ["fclass", "roadclass", "highway", "FUNCTIONAL",
                            "ROADCLASS", "road_type", "type"],
    # ─── 等级→成本乘数映射 (乘数越小越优先) ───
    # 键为字段值的前缀匹配 (不区分大小写)
    cost_multipliers     = dict(
        motorway     = 0.70,   # 高速公路
        trunk        = 0.75,   # 干道/快速路
        primary      = 0.80,   # 主干道
        secondary    = 0.90,   # 次干道
        tertiary     = 1.00,   # 支路
        residential  = 1.15,   # 居民区道路
        service      = 1.25,   # 服务道路
        path         = 1.40,   # 小路
        track        = 1.50,   # 土路
        footway      = 1.60,   # 人行道
        pedestrian   = 1.60,   # 步行街
        unclassified = 1.20,   # 未分类
    ),
    default_multiplier   = 1.10,     # 未匹配到等级时的默认乘数
)

# ======================== 上车点动态关闭配置 ========================
# 当风险场覆盖上车点时, 动态关闭该上车点并重定向居民
PICKUP_CLOSURE_CONFIG = dict(
    enabled              = True,      # 是否启用动态上车点关闭
    # ─── 关闭阈值: 当上车点处剂量率超过此值时关闭 ───
    # 单位与风险场矩阵一致 (CVaR值)
    closure_threshold    = 0.5,      # 默认关闭阈值 (CVaR值, 需根据实际量级调整)
    # ─── 重定向策略 ───
    # "next_feasible": 从可行集中选下一个未关闭的上车点
    # "nearest_safe":  选最近的未关闭上车点
    redirect_strategy    = "next_feasible",
    # ─── 已到达居民处理 ───
    # True: 已到达的居民留在原上车点等巴士 (巴士提供辐射屏蔽)
    # False: 已到达的居民也需重定向 (不推荐)
    keep_arrived         = True,
)

# ======================== 多阶段滚动时域配置 (v5.5) ========================
# 将45分钟疏散窗口分解为4个顺序决策阶段
# 每个阶段根据最新风险态势对未到达居民重新运行Q-NSGA-II优化
# 文献依据: Li et al. (2019) J. Environmental Radioactivity (rolling horizon)
#           Deb et al. (2007) EMO (热启动NSGA-II)
MULTISTAGE_CONFIG = dict(
    enabled              = True,
    # ─── 阶段时间划分 (分钟) ───
    stage_decision_times = [0, 15, 25, 35],    # 各阶段决策时刻
    stage_deadlines      = [15, 25, 35, 45],    # 各阶段截止时刻
    # ─── 各阶段迭代代数 (递减: 后期居民少, 搜索空间小) ───
    ngen_schedule        = [100, 60, 40, 20],
    # ─── 热启动参数 ───
    hot_start_ratio      = 0.7,     # 从上一阶段继承的种群比例 (0.7=70%)
                                     # 剩余30%随机初始化注入多样性
    # ─── 上车点关闭阈值 ───
    # t=0时用15min风险矩阵判断, 非零风险覆盖的上车点禁用
    # t=15/25/35时用对应阶段风险矩阵判断
    closure_at_start     = "next_stage",  # "next_stage": 用下一阶段风险矩阵判断
                                          # "current_stage": 用当前阶段风险矩阵判断
)
