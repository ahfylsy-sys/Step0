import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import contextily as ctx
from pyproj import CRS, Transformer
import warnings

warnings.filterwarnings("ignore")

# ==============================
# 用户配置区（请根据实际情况修改）
# ==============================

# 1. CSV 文件夹路径
CSV_FOLDERS = [
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\Short_Dose\TIME_SERIES\15\CALPOST\effect",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\Short_Dose\TIME_SERIES\25\CALPOST\effect",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\Short_Dose\TIME_SERIES\35\CALPOST\effect",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\Short_Dose\TIME_SERIES\45\CALPOST\effect",
]

# 指定 Excel 保存路径（可自定义）
OUTPUT_EXCELS = [
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\cvar_risk_map_output15.xlsx",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\cvar_risk_map_output25.xlsx",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\cvar_risk_map_output35.xlsx",
    r"E:\LIUSHENGYU\WORK2-EVACUATION\Data\cvar_risk_map_output45.xlsx",
]

time_labels = ['15', '25', '35', '45']

times = [15, 25, 35, 45]

plt.rcParams['font.family'] = 'Times New Roman'

# 2. 核电厂 UTM 坐标
UTM_ZONE = "50N"
PLANT_X = 247413.0  # UTM Easting (m)
PLANT_Y = 2501099.0  # UTM Northing (m)

# 3. 网格参数
GRID_SIZE = 100  # 100x100
CELL_SIZE_M = 400.0  # 每个网格 400 米

# 4. CVaR 参数
CONFIDENCE_LEVEL = 0.99  # 95% 置信区间
RISK_THRESHOLDS = []  # 风险阈值 (Sv)，超过此值认为有风险，可根据实际需求调整
for time in times:
    risk_threshold = 0.05 / 7 / 16 / 24 * (time / 60)
    RISK_THRESHOLDS.append(risk_threshold)

# 打印每个时间点对应的阈值
print("各时间点的风险阈值:")
for t, th in zip(time_labels, RISK_THRESHOLDS):
    print(f"  时间 {t} 分钟: {th:.6e} Sv")

# 5. 输出图像保存路径前缀
OUTPUT_PNG_PREFIX = "cvar_risk_map"
OUTPUT_PNG_PATH = r"E:/LIUSHENGYU/WORK2-EVACUATION/figure/cvar_risk_map"


# ==============================
# 辅助函数
# ==============================

def calculate_cvar(doses, confidence_level=0.99):
    """
    计算条件风险价值 (CVaR / Expected Shortfall)

    参数:
        doses: 剂量数组（所有情景下该网格的剂量值）
        confidence_level: 置信水平，默认95%

    返回:
        var_value: VaR值（置信区间的分位数）
        cvar_value: CVaR值（超过VaR的尾部平均值）
    """
    if len(doses) == 0 or np.all(doses == 0):
        return 0.0, 0.0

    # 从小到大排序
    sorted_doses = np.sort(doses)

    # 计算 VaR（Value at Risk）：95%置信区间的上界（即第95百分位数）
    var_index = int(np.ceil(confidence_level * len(sorted_doses))) - 1
    var_index = min(var_index, len(sorted_doses) - 1)  # 防止越界
    var_value = sorted_doses[var_index]

    # 计算 CVaR（Conditional VaR）：超过VaR的尾部平均值
    # 即取超过第95百分位数的所有值的平均
    tail_values = sorted_doses[sorted_doses >= var_value]

    if len(tail_values) == 0:
        cvar_value = var_value
    else:
        cvar_value = np.mean(tail_values)

    return var_value, cvar_value


def calculate_cvar_alternative(doses, confidence_level=0.99):
    """
    CVaR 的另一种计算方式：使用连续近似

    对于离散样本，CVaR = E[X | X >= VaR]
    这里使用更精确的计算方式，考虑分位数的线性插值
    """
    if len(doses) == 0 or np.all(doses == 0):
        return 0.0, 0.0

    sorted_doses = np.sort(doses)
    n = len(sorted_doses)

    # 使用 numpy 的 percentile 计算 VaR
    var_value = np.percentile(sorted_doses, confidence_level * 100)

    # 计算 CVaR：尾部期望值
    # 取所有大于等于 VaR 的值的加权平均
    alpha = 1 - confidence_level  # 尾部概率
    tail_start_idx = int(np.floor(confidence_level * n))

    if tail_start_idx >= n:
        cvar_value = sorted_doses[-1]
    else:
        # 尾部值
        tail_values = sorted_doses[tail_start_idx:]
        cvar_value = np.mean(tail_values)

    return var_value, cvar_value


# ==============================
# 主程序
# ==============================

# ==============================
# 第一阶段：加载数据并计算所有 CVaR 风险图
# ==============================

all_risk_maps = []
all_var_maps = []
all_dose_3ds = []

print("=" * 60)
print("CVaR 风险分析程序")
print("=" * 60)
print(f"置信水平: {CONFIDENCE_LEVEL * 100}%")
print("=" * 60)

print("\n🔄 第一阶段：加载数据并计算所有 CVaR 风险图...")

for folder_idx, (CSV_FOLDER, RISK_THRESHOLD) in enumerate(zip(CSV_FOLDERS, RISK_THRESHOLDS)):
    print(f"\n📂 处理文件夹 [{folder_idx + 1}/{len(CSV_FOLDERS)}]: {CSV_FOLDER}")
    print(f"   时间点: {time_labels[folder_idx]} 分钟")
    print(f"   风险阈值: {RISK_THRESHOLD:.6e} Sv")

    # --- 加载 dose_3d ---
    csv_files = [f for f in os.listdir(CSV_FOLDER) if f.endswith('.csv')]
    if not csv_files:
        raise FileNotFoundError(f"未在 {CSV_FOLDER} 中找到 CSV 文件")

    print(f"   找到 {len(csv_files)} 个 CSV 文件")

    dose_3d = []
    for f in csv_files:
        rows = []
        with open(os.path.join(CSV_FOLDER, f), 'r') as file:
            lines = file.readlines()
        data_lines = lines[1:]  # 跳过标题行
        for line in data_lines[:GRID_SIZE]:
            fields = [x.strip() for x in line.split(',')]
            row = []
            for i in range(GRID_SIZE):
                if i < len(fields) and fields[i] not in ('', ' '):
                    try:
                        val = float(fields[i])
                    except (ValueError, OverflowError):
                        val = 0.0
                else:
                    val = 0.0
                row.append(val)
            rows.append(row)
        while len(rows) < GRID_SIZE:
            rows.append([0.0] * GRID_SIZE)
        arr = np.array(rows[:GRID_SIZE], dtype=np.float64)
        arr = arr[::-1]  # 行反转
        dose_3d.append(arr)

    dose_3d = np.array(dose_3d)
    all_dose_3ds.append(dose_3d)
    print(f"   数据维度: {dose_3d.shape} (情景数 × 行 × 列)")

    # --- 计算 CVaR 风险图 ---
    risk_map = np.zeros((GRID_SIZE, GRID_SIZE))  # CVaR 值（量化风险）
    var_map = np.zeros((GRID_SIZE, GRID_SIZE))  # VaR 值（用于判断是否有风险）

    risk_count = 0  # 统计有风险的网格数

    for i in range(GRID_SIZE):
        for j in range(GRID_SIZE):
            doses = dose_3d[:, i, j]

            # 计算 VaR 和 CVaR
            var_value, cvar_value = calculate_cvar(doses, CONFIDENCE_LEVEL)

            var_map[i, j] = var_value

            # 判断是否有风险：当 VaR（95%置信区间的最大值）超过阈值时
            if var_value >= RISK_THRESHOLD:
                risk_map[i, j] = cvar_value  # 量化风险为 CVaR
                risk_count += 1
            else:
                risk_map[i, j] = 0.0  # 无风险

    all_risk_maps.append(risk_map)
    all_var_maps.append(var_map)

    print(f"   有风险网格数: {risk_count} / {GRID_SIZE * GRID_SIZE}")
    print(f"   CVaR 最大值: {np.max(risk_map):.4e} Sv")
    print(f"   CVaR 非零平均值: {np.mean(risk_map[risk_map > 0]):.4e} Sv" if risk_count > 0 else "   无有效风险数据")

# --- 收集所有非零风险值，用于全局 colorbar ---
all_nonzero_risks = np.concatenate([rm[rm > 0].flatten() for rm in all_risk_maps if np.any(rm > 0)])

if len(all_nonzero_risks) == 0:
    print("\n⚠️ 警告：所有风险图均为零！可能需要降低风险阈值。")
    global_vmin = min(RISK_THRESHOLDS)  # 使用最小阈值
    global_vmax = max(RISK_THRESHOLDS) * 10
else:
    fixed_min = min(RISK_THRESHOLDS)  # 使用最小阈值作为下限
    global_vmin = max(np.percentile(all_nonzero_risks, 1), fixed_min)
    global_vmax = np.percentile(all_nonzero_risks, 99)

print(f"\n✅ 全局风险范围: vmin={global_vmin:.3e}, vmax={global_vmax:.3e}")

# ==============================
# 第二阶段：用统一 colorbar 绘图 + 保存 Excel
# ==============================

print("\n🔄 第二阶段：绘制风险图并保存数据...")

for idx, (CSV_FOLDER, OUTPUT_EXCEL, time_label, RISK_THRESHOLD) in enumerate(
        zip(CSV_FOLDERS, OUTPUT_EXCELS, time_labels, RISK_THRESHOLDS)):
    risk_map = all_risk_maps[idx]
    var_map = all_var_maps[idx]

    print(f"\n📊 处理时间点: {time_label} 分钟")
    print(f"   当前阈值: {RISK_THRESHOLD:.6e} Sv")

    # 地理配准
    half = GRID_SIZE // 2
    x_centers = PLANT_X + (np.arange(GRID_SIZE) - half) * CELL_SIZE_M
    y_centers = PLANT_Y + (np.arange(GRID_SIZE) - half) * CELL_SIZE_M
    x_min = x_centers[0] - CELL_SIZE_M / 2
    x_max = x_centers[-1] + CELL_SIZE_M / 2
    y_min = y_centers[0] - CELL_SIZE_M / 2
    y_max = y_centers[-1] + CELL_SIZE_M / 2

    crs_utm = CRS(f"+proj=utm +zone={UTM_ZONE.split('N')[0]} +north +ellps=WGS84 +datum=WGS84 +units=m +no_defs")
    crs_wgs84 = CRS("EPSG:4326")
    transformer = Transformer.from_crs(crs_utm, crs_wgs84, always_xy=True)
    lon_min, lat_min = transformer.transform(x_min, y_min)
    lon_max, lat_max = transformer.transform(x_max, y_max)

    # 绘图
    fig, ax = plt.subplots(figsize=(12, 10), dpi=300)
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)

    ctx.add_basemap(ax, crs=crs_wgs84, source=ctx.providers.CartoDB.Positron, attribution=False)

    # 对数色阶设置
    eps = 1e-20
    safe_vmin = max(global_vmin, eps)
    safe_vmax = max(global_vmax, safe_vmin * 1.1)

    norm = LogNorm(vmin=safe_vmin, vmax=safe_vmax)
    cmap = plt.get_cmap('rainbow')

    # 应用归一化
    rgba_img = cmap(norm(np.where(risk_map > 0, risk_map, np.nan)))

    # 设置透明度：仅有风险的区域显示
    rgba_img[:, :, 3] = np.where(risk_map > 0, 0.6, 0.0)

    # 绘图
    im = ax.imshow(
        rgba_img,
        extent=[lon_min, lon_max, lat_min, lat_max],
        origin='lower',
        interpolation='nearest',
        zorder=2
    )

    # Colorbar
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.6)
    cbar.set_label(f'CVaR Risk Value (Sv)\n[{int(CONFIDENCE_LEVEL * 100)}% Confidence Level]', rotation=90)

    # 标题 - 使用当前时间点对应的阈值
    ax.set_title(f'CVaR Risk Map - Time: {time_label} min\nThreshold: {RISK_THRESHOLD:.4e} Sv', fontsize=14)
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')

    # 保存图像
    output_png = f"{OUTPUT_PNG_PATH}/{OUTPUT_PNG_PREFIX}_{time_label}.png"
    plt.tight_layout()
    plt.savefig(output_png, dpi=300, bbox_inches='tight')
    plt.close(fig)
    print(f"   ✅ 风险地图已保存: {output_png}")

    # 保存 Excel - CVaR 风险矩阵
    df_risk = pd.DataFrame(risk_map).iloc[::-1]
    df_risk.to_excel(OUTPUT_EXCEL, index=False, header=False)
    print(f"   ✅ CVaR 风险矩阵已保存: {OUTPUT_EXCEL}")

    # 额外保存 VaR 矩阵（可选）
    var_excel = OUTPUT_EXCEL.replace('.xlsx', '_VAR.xlsx')
    df_var = pd.DataFrame(var_map).iloc[::-1]
    df_var.to_excel(var_excel, index=False, header=False)
    print(f"   ✅ VaR 矩阵已保存: {var_excel}")

# ==============================
# 第三阶段：统计摘要
# ==============================

print("\n" + "=" * 60)
print("统计摘要")
print("=" * 60)

for idx, (time_label, RISK_THRESHOLD) in enumerate(zip(time_labels, RISK_THRESHOLDS)):
    risk_map = all_risk_maps[idx]
    var_map = all_var_maps[idx]

    risk_cells = np.sum(risk_map > 0)
    total_cells = GRID_SIZE * GRID_SIZE

    print(f"\n时间点 {time_label} 分钟 (阈值: {RISK_THRESHOLD:.4e} Sv):")
    print(f"  - 有风险网格: {risk_cells} / {total_cells} ({100 * risk_cells / total_cells:.2f}%)")

    if risk_cells > 0:
        print(f"  - CVaR 范围: [{np.min(risk_map[risk_map > 0]):.4e}, {np.max(risk_map):.4e}] Sv")
        print(f"  - CVaR 平均值: {np.mean(risk_map[risk_map > 0]):.4e} Sv")
        print(f"  - VaR 最大值: {np.max(var_map):.4e} Sv")

print("\n" + "=" * 60)
print("✅ 所有处理完成！")
print("=" * 60)