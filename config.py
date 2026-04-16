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
STAGE_TIMES     = [0, 15, 30, 45] # 疏散阶段时间节点 (min), len=4 (含 t=0)
# 风险矩阵对应的时间节点 (min), 与 RISK_VALUE_FILES 一一对应
RISK_STAGE_TIMES = [15, 30, 45]
CRS_UTM         = "EPSG:32650"
CRS_WGS84       = "EPSG:4326"

# ======================== 数据路径 ========================
DATA_ROOT   = r"e:\Claude code\WORK-2\Data"
OUTPUT_ROOT = r"E:\Q-NSGA2-Results"

RISK_VALUE_FILES = [
    os.path.join(DATA_ROOT, "cvar_risk", f"cvar_risk_map_output_{t}.xlsx") for t in [15, 30, 45]
]
ROAD_NETWORK_SHP = os.path.join(DATA_ROOT, "road_data", "Shenzhen_Roads_Clip.shp")
BUS_FILE         = os.path.join(DATA_ROOT, "pickup_poi_all_aggregated_with_blacklist.xlsx")
ROAD_CLIP_RADIUS = 50_000  # 路网裁剪半径 (m) = 50 km

# ======================== 人口分组 ========================
AGE_GROUPS   = ["20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
GENDERS      = ["male", "female"]
GENDER_SHORT = {"male": "m", "female": "f"}
# 12 组步行速度 (m/s)：male 20‑29 … 70+, female 20‑29 … 70+
WALK_SPEEDS  = [2.01, 1.94, 1.87, 1.81, 1.70, 1.55,
                1.84, 1.77, 1.72, 1.65, 1.59, 1.28]

# ======================== NSGA-II 基础参数 ========================
NSGA2_CONFIG = dict(
    mu       = 200,    # 种群大小
    lambda_  = 200,    # 子代数量
    ngen     = 160,    # 迭代代数
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

# ======================== 道路拥挤度建模配置 (v5.7) ========================
# 基于道路宽度计算行人通行能力，当人流密度超过容量时施加 BPR 延迟惩罚
ROAD_CONGESTION_CONFIG = dict(
    enabled              = True,
    width_fields         = ["width", "WIDTH", "road_width", "ROADWIDTH",
                            "lane_width", "carriageway_width", "LANEWIDTH"],
    default_widths_m     = dict(
        motorway=22.0, trunk=15.0, primary=12.0, secondary=10.0,
        tertiary=7.0, residential=6.0, service=5.0, path=2.0,
        track=3.0, footway=2.0, pedestrian=3.0, unclassified=6.0,
    ),
    default_width_m      = 7.0,
    effective_width_ratio = 0.75,
    ped_flow_rate_ppm     = 75.0,
    bpr_alpha            = 0.15,
    bpr_beta             = 4.0,
    congestion_weight    = 1.0,
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

# ======================== 巴士总站 ========================
BUS_DEPOT = (240167, 2501014)   # 大鹏总站 UTM-50N 坐标

# ======================== 避难所配置 ========================
SHELTER_FILE = os.path.join(DATA_ROOT, "Shelter_with_coords.xlsx")
SHELTER_CONFIG = dict(
    radius_m             = 30_000,   # 避难所距核电厂半径 (m) = 30 km
    capacity_per_shelter = 10000,    # 每个避难所容量 (人)
    shelter_file         = SHELTER_FILE,  # 避难所 Excel (列: Number, lon, lat, Capacity)
    # 避难所数量由容量需求自动确定, 不再固定
)

# ======================== 风险场关闭阈值 ========================
RISK_CLOSURE_THRESHOLD = 0.0   # 上车点风险值超过此阈值即视为"被风险场覆盖"而关闭

# ======================== 上车点风险过滤 ========================
PICKUP_RISK_FILTER = dict(
    enabled        = True,
    risk_threshold = 0.0,     # t=15min 时风险值超过此阈值的上车点被排除出可行域
)

# ======================== 道路距离修正因子 ========================
ROAD_DISTANCE_FACTOR = 1.3    # Euclidean → 实际道路距离修正系数 (用于避难所等长距离路线)

# ======================== 日志 ========================
LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")
