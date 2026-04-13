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

# ======================== 数据路径 ========================
DATA_ROOT   = r"E:\LIUSHENGYU\WORK2-EVACUATION\Data"
OUTPUT_ROOT = r"E:\LIUSHENGYU\WORK2-EVACUATION\figure"

RISK_VALUE_FILES = [
    os.path.join(DATA_ROOT, f"cvar_risk_map_output{t}.xlsx") for t in [15, 25, 35, 45]
]
ROAD_NETWORK_SHP = os.path.join(DATA_ROOT, "road_data", "Shenzhen_Roads_Clip.shp")
BUS_FILE         = os.path.join(DATA_ROOT, "pickup_poi_all_aggregated_with_blacklist.xlsx")
ROAD_CLIP_RADIUS = 10_000  # 路网裁剪半径 (m)

# ======================== 人口分组 ========================
AGE_GROUPS   = ["20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
GENDERS      = ["male", "female"]
GENDER_SHORT = {"male": "m", "female": "f"}
# 12 组步行速度 (m/s)：male 20‑29 … 70+, female 20‑29 … 70+
WALK_SPEEDS  = [2.01, 1.94, 1.87, 1.81, 1.70, 1.55,
                1.84, 1.77, 1.72, 1.65, 1.59, 1.28]

# ======================== NSGA-II 基础参数 ========================
NSGA2_CONFIG = dict(
    mu       = 400,    # 种群大小
    lambda_  = 400,    # 子代数量
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

# ======================== 日志 ========================
LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")
