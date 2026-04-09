"""
日志记录与结果导出模块
- EvacLogger：运行时日志 → txt + csv
- Pareto 前沿导出 → CSV
- 分组汇总 → Excel (多 Sheet)
"""
import os
import csv
import numpy as np
import pandas as pd
from datetime import datetime
from config import LOG_DIR


# ============================================================
#  日志记录器
# ============================================================
class EvacLogger:
    """
    将优化过程写入 .txt 文本日志 + .csv 结构化日志。
    每代的 MinTime / MinRisk / ParetoSize / Infeasible 等指标
    可用于后续收敛分析。
    """

    def __init__(self, group_name: str, output_dir: str = None):
        self.group_name = group_name
        base = output_dir or LOG_DIR
        os.makedirs(base, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.txt_path = os.path.join(base, f"{group_name}_{ts}.log")
        self.csv_path = os.path.join(base, f"{group_name}_{ts}.csv")

        self._txt = open(self.txt_path, "w", encoding="utf-8")
        self._csv = open(self.csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._csv)
        self._writer.writerow(["gen", "min_time", "min_risk",
                                "pareto_size", "infeasible", "delta_theta"])
        self.log(f"=== Q-NSGA-II Log: {group_name} ===")
        self.log(f"Started: {ts}")

    def log(self, msg: str):
        self._txt.write(msg + "\n")
        self._txt.flush()

    def log_gen(self, gen, min_t, min_r, pf_size, inf_count, delta_theta):
        self._writer.writerow([gen, min_t, min_r, pf_size, inf_count, delta_theta])
        self._csv.flush()

    def close(self):
        self._txt.close()
        self._csv.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
#  Pareto 前沿导出
# ============================================================
def export_pareto_csv(pareto_front, output_path):
    """将 Pareto 前沿的适应度值导出为 CSV"""
    rows = []
    for i, ind in enumerate(pareto_front):
        if np.isfinite(ind.fitness.values[0]):
            rows.append(dict(
                solution_id=i,
                time_objective=ind.fitness.values[0],
                risk_objective=ind.fitness.values[1],
                time_minutes=ind.fitness.values[0] / 60,
            ))
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"💾 Pareto CSV: {output_path} ({len(df)} solutions)")
    return df


# ============================================================
#  Excel 结果汇总
# ============================================================
def export_results_excel(all_results, bus_xy, output_path):
    """
    将多个分组的优化结果汇总到 Excel:
      Sheet 1 – Assignment: 逐条居民-上车点分配
      Sheet 2 – Group_Stats: 分组级统计
      Sheet 3 – Pop_by_Stop: 按上车点×分组的人口透视表
    """
    print(f"\n📊 Exporting results to {output_path} ...")
    records = []
    for res in all_results:
        assign = res["assignment"]
        rdf = res["res_df"]
        spd = res["speed"]
        for i in range(len(rdf)):
            j = assign[i]
            if j == -1:
                continue
            rx, ry = rdf["x"].values[i], rdf["y"].values[i]
            bx, by = bus_xy[j]
            d = np.hypot(rx - bx, ry - by)
            records.append(dict(
                bus_stop_id=j,
                group_name=res["group_name"],
                gender=res["gender"],
                age_group=res["age_group"],
                resident_id=rdf["id"].values[i],
                population=rdf["pop"].values[i],
                walk_distance_m=d,
                walk_time_min=d / spd / 60,
            ))
    df = pd.DataFrame(records)
    if df.empty:
        print("⚠️  No records to export")
        return

    stats = []
    for res in all_results:
        gd = df[df["group_name"] == res["group_name"]]
        stats.append(dict(
            group_name=res["group_name"],
            gender=res["gender"],
            age_group=res["age_group"],
            speed=res["speed"],
            total_pop=int(gd["population"].sum()),
            n_stops_used=int(gd["bus_stop_id"].nunique()),
            avg_walk_min=round(gd["walk_time_min"].mean(), 2),
            max_walk_min=round(gd["walk_time_min"].max(), 2),
            total_time_min=round(res["total_time"] / 60, 2),
            total_risk=res["total_risk"],
        ))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as w:
        df.sort_values(["bus_stop_id", "group_name"]).to_excel(
            w, "Assignment", index=False)
        pd.DataFrame(stats).to_excel(w, "Group_Stats", index=False)
        pivot = df.pivot_table(
            index="bus_stop_id", columns="group_name",
            values="population", aggfunc="sum", fill_value=0)
        pivot["total"] = pivot.sum(axis=1)
        pivot.sort_values("total", ascending=False).to_excel(w, "Pop_by_Stop")

    print(f"✅ Excel saved: {output_path}  "
          f"({len(df)} records, {len(stats)} groups)")
