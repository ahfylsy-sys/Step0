# evac_v2 核心模块逐块代码注释文档

> 适用版本：evac_v2 (含 sink 边界条件)
> 覆盖文件：`data_loader.py` (219 行) · `pickup_sink.py` (351 行) · `optimizer.py` (508 行) · `main.py` (429 行)
> 用途：照此文档逐块对照代码，理解每段逻辑的设计意图与可修改点

## 文档结构说明

本文档按模块组织，每个模块按 **逻辑块** 划分（一个逻辑块通常 5-30 行）。每个块包含：

1. **代码片段**（带原始行号）
2. **块功能**：这段代码做什么
3. **逐行/关键行解析**：每行（或关键行）的作用
4. **修改建议**：在哪里改、改什么会有什么影响
5. **常见陷阱**（如果有）

---

# 第一部分：data_loader.py

## 1.1 模块文档与导入 (Lines 1–19)

```python
"""
数据加载与路网预处理模块
- 居民/上车点/风险场数据读取
- 路网图构建与 Dijkstra 路径预计算
- 可行域构建（严格路网模式：无直线备选）
"""
import os
import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from scipy.spatial import KDTree
from shapely.geometry import LineString

from config import (
    CENTER_UTM, GRID_RES, CRS_UTM,
    DATA_ROOT, BUS_FILE, AGE_GROUPS, GENDERS, GENDER_SHORT,
    WALK_SPEEDS, OUTPUT_ROOT,
)
```

**块功能**：声明模块职责并导入所有依赖。

**关键行解析**：
- L7-13 标准科学计算与地理空间库的导入。`geopandas` 用于读取 Shapefile，`networkx` 用于构建路网图，`scipy.spatial.KDTree` 用于上车点-路网节点的最近邻查找。
- L15-19 从 `config.py` 导入全局常量。集中导入便于维护：当需要修改路径或核电厂坐标时只改 `config.py`。

**修改建议**：
- 若要添加新数据源（如人行道宽度），先在 `config.py` 加常量，再在此处加入导入列表。
- 注意 **`from shapely.geometry import LineString`** 仅用于构建路径几何对象。若改用其他几何类型（如 `MultiLineString`），需同步修改 `precompute_paths` 函数。

---

## 1.2 居民数据读取 (Lines 25–29)

```python
def load_resident_data(pop_file: str) -> pd.DataFrame:
    """读取居民 CSV → DataFrame(id, x, y, pop)"""
    df = pd.read_csv(pop_file)
    df.columns = ["id", "x", "y", "pop"]
    return df
```

**块功能**：读取居民聚类数据 CSV，统一列名。

**逐行解析**：
- L27 `pd.read_csv` 直接读取，假设文件有表头但表头名称不固定。
- L28 **强制重命名前 4 列**为 `[id, x, y, pop]`。这是一个隐式约定：CSV 必须前 4 列分别是「编号、UTM x 坐标、UTM y 坐标、人口数」，顺序错了就会出现严重 bug。
- L29 返回 DataFrame，后续模块通过列名访问。

**修改建议**：
- 若 CSV 列顺序变了，**唯一需要改的是 L28** 这行。
- 若需要保留 CSV 中的额外列（如年龄分布、性别比例），改为：
  ```python
  rename_map = {"原列名": "id", "原列名": "x", ...}
  df = df.rename(columns=rename_map)
  ```
  避免直接覆盖 `df.columns`。

**常见陷阱**：CSV 列数 < 4 时 L28 会抛 `ValueError: Length mismatch`；列数 > 4 时多余列被保留但列名变为整数索引。

---

## 1.3 上车点读取 (Lines 32–39)

```python
def load_bus_stops(bus_file: str, target_crs: str = CRS_UTM):
    """读取上车点 Excel → (xy_array, GeoDataFrame)"""
    bus = pd.read_excel(bus_file)
    gdf = gpd.GeoDataFrame(
        bus, geometry=gpd.points_from_xy(bus["lon"], bus["lat"]), crs="EPSG:4326"
    ).to_crs(target_crs)
    xy = np.array([(p.x, p.y) for p in gdf.geometry])
    return xy, gdf
```

**块功能**：读取上车点 Excel（含经纬度），投影为 UTM 坐标系，返回坐标数组与 GeoDataFrame。

**关键行解析**：
- L34 读取 Excel；该文件**必须包含 `lon` 和 `lat` 两列**（小写）。
- L35-37 三步：(1) 用 `lon`/`lat` 列构造 Point 几何 → (2) 包装为 GeoDataFrame，CRS 标记为 WGS84 → (3) 投影到 UTM-50N。这是地理坐标→平面坐标的标准流程，避免后续距离计算用经纬度（米≠度）。
- L38 从 GeoDataFrame 提取 `(x, y)` 元组列表，转为 NumPy 数组供 KDTree 和优化器使用。
- L39 返回**两份数据**：`xy` 数组用于数值计算，`gdf` 用于可视化和地理分析。

**修改建议**：
- 若 Excel 列名是 `经度`/`纬度`，将 L36 改为 `gpd.points_from_xy(bus["经度"], bus["纬度"])`。
- 若数据本身已是 UTM 坐标（不需要转换），跳过 `to_crs` 步骤：
  ```python
  gdf = gpd.GeoDataFrame(bus, geometry=gpd.points_from_xy(bus["x"], bus["y"]), crs=target_crs)
  ```

---

## 1.4 风险场加载 (Lines 42–51)

```python
def load_all_risk_data(risk_files, center=CENTER_UTM, res=GRID_RES):
    """加载 4 个阶段的风险矩阵及其空间参照"""
    arrays, x_mins, y_maxs = [], [], []
    for f in risk_files:
        arr = pd.read_excel(f, header=None).values.astype(np.float64)
        ny, nx = arr.shape
        arrays.append(arr)
        x_mins.append(center[0] - (nx / 2) * res)
        y_maxs.append(center[1] + (ny / 2) * res)
    return arrays, x_mins, y_maxs
```

**块功能**：加载 4 个时间阶段（15/25/35/45 min）的辐射风险矩阵，并计算每个矩阵的空间参照（左上角坐标）。

**逐行解析**：
- L44 三个累积列表：`arrays` 存矩阵本体，`x_mins`/`y_maxs` 存空间参照。
- L45 遍历 4 个文件路径（在 `config.py` 中定义）。
- L46 `pd.read_excel(..., header=None)` 表示**没有表头**，整个 Excel 当做矩阵；`.values` 取出 NumPy 数组；`astype(np.float64)` 强制转浮点。
- L47 `ny`（行数）= 网格 y 维度，`nx`（列数）= 网格 x 维度。注意：行优先存储下，矩阵的第一维对应 y。
- L48 矩阵存入列表。
- L49-50 **计算空间参照**：假设核电厂位于矩阵中心，则 `x_min = center_x - (nx/2)*res`（矩阵左边界 UTM 坐标），`y_max = center_y + (ny/2)*res`（矩阵上边界 UTM 坐标）。这两个值用于后续把 UTM 坐标 (x,y) 转成矩阵索引 (row, col)。
- L51 返回三个列表，长度均为 4。

**修改建议**：
- 如果风险矩阵的中心 **不在** 核电厂上，需要把 L49-50 改为读取每个文件附带的元数据（如 `.tfw` world file 或 GeoTIFF）。
- 如果分辨率不是固定的 400m，可在循环中读取每个文件的实际分辨率。

**常见陷阱**：L46 的 `.astype(np.float64)` 不可省略——Excel 中数字可能被读成 `int64`，与后续浮点运算混用会引发性能下降或类型不一致错误。

---

## 1.5 分组配置构建 (Lines 54–74)

```python
def build_group_configs():
    """构建 12 个年龄×性别分组的配置字典列表"""
    configs, idx = [], 0
    for gender in GENDERS:
        g = GENDER_SHORT[gender]
        for age in AGE_GROUPS:
            name = f"{g}_{age}"
            pop_f = os.path.join(DATA_ROOT, "pop_data", "clipped_pop_gender_age_csv",
                                 gender, "all_age_clustering", f"{name}_cluster.csv")
            feas_f = os.path.join(DATA_ROOT, "feasible_domain", f"feasible_{name}.pkl")
            if os.path.exists(pop_f) and os.path.exists(feas_f):
                configs.append(dict(
                    group_name=name, gender=gender, age_group=age,
                    pop_file=pop_f, output_dir=os.path.join(OUTPUT_ROOT, g, age),
                    speed=WALK_SPEEDS[idx], bus_file=BUS_FILE,
                    selection_method="min_time",
                ))
            else:
                print(f"⚠️  Skipping {name}: data files not found")
            idx += 1
    return configs
```

**块功能**：扫描数据目录，为每个性别×年龄组生成一个配置字典（共 2×6=12 个组），用于后续的批量优化。

**逐行解析**：
- L56 `idx` 是用于索引 `WALK_SPEEDS` 的全局序号（0-11），每组对应一个步行速度。
- L57-59 双重循环：外层性别（male/female），内层年龄段（20-29/.../70+）。
- L58 `g = GENDER_SHORT[gender]` 把 "male"→"m"，"female"→"f"，用于构造短文件名。
- L60 `name` 是组名，例如 `m_20-29` 或 `f_70+`。
- L61-63 拼接两个数据文件路径：人口数据 CSV 与可行域 pickle。**这两个路径与 `DATA_ROOT` 下的目录结构强耦合**，目录变动需在此处同步修改。
- L64 `os.path.exists` 双重检查：两个文件都存在才加入配置。
- L65-70 配置字典结构：包含组标识、文件路径、输出目录、该组的步行速度、上车点文件、默认选解策略。
- L71-72 文件缺失时打印警告并跳过该组。
- L73 **`idx += 1` 不在 if 内**：无论该组是否被加入，`idx` 都递增，保证 `WALK_SPEEDS` 与 `(gender, age)` 的对应关系。

**修改建议**：
- 改文件目录结构 → 改 L61-63 的 `os.path.join` 参数。
- 改默认选解策略 → 改 L69 的 `selection_method="min_time"`。
- 添加新配置项（如 max_walk_time）→ 在 L65-70 的 dict 中加键值对。

**常见陷阱**：如果你想跳过某些组（不是因为文件缺失），不要在 if 内 `continue`，否则 `idx` 也会递增，破坏速度对应关系。正确做法是在 L65-70 之前加 `if name in skip_list: idx += 1; continue`。

---

## 1.6 路网加载 — 函数签名与裁剪 (Lines 80–98)

```python
def load_road_network(shp_path, crs=CRS_UTM, center=None, clip_radius=None):
    """
    加载 Shapefile 路网 → NetworkX 无向图 + KDTree 空间索引

    返回:
        G            – NetworkX 图 (边权 = length)
        node_ids     – 节点 ID 数组
        node_coords  – 节点坐标 (N,2)
        kdtree       – 节点 KDTree
    """
    print("Loading road network ...")
    roads = gpd.read_file(shp_path).to_crs(crs)

    if center is not None and clip_radius is not None:
        from shapely.geometry import Point as ShapelyPoint
        buf = ShapelyPoint(*center).buffer(clip_radius)
        roads = roads[roads.intersects(buf)].copy()
        roads["geometry"] = roads.geometry.intersection(buf)
        print(f"   Clipped to {clip_radius/1000:.0f} km: {len(roads)} segments")
```

**块功能**：读取 Shapefile，可选地裁剪为以核电厂为中心的圆形区域。

**关键行解析**：
- L91 读 Shapefile 并立刻投影为 UTM。
- L93 仅当传入了 `center` 和 `clip_radius` 时才裁剪。原始路网可能很大（数十万条线段），裁剪后能将节点数从十万级降到千级，显著加速 Dijkstra。
- L95 构造 `ShapelyPoint` 后用 `.buffer(clip_radius)` 生成圆形几何。
- L96 用 `intersects(buf)` 筛出与圆相交的道路（**保留与圆有任何交集的整条线段**）。
- L97 `intersection(buf)` 把延伸到圆外的部分裁掉。这两步要分开做：先粗筛降数据量，再精细裁剪。

**修改建议**：
- 若区域不是圆形（如行政区边界），把 L95 的 `ShapelyPoint(*center).buffer(...)` 替换为 `gpd.read_file("boundary.shp").geometry[0]`。
- 若想加快裁剪，可在 L96 之前用 `roads.sindex`（空间索引）做预筛。

---

## 1.7 路网加载 — 几何清洗与节点去重 (Lines 100–115)

```python
    roads = roads.explode(ignore_index=True)
    roads = roads[roads.geometry.type == "LineString"]
    if roads.empty:
        raise ValueError("Road network contains no valid LineStrings after clipping.")

    G = nx.Graph()
    c2n, nc = {}, 0

    def _node(x, y, tol=1e-3):
        nonlocal nc
        k = (round(x / tol) * tol, round(y / tol) * tol)
        if k not in c2n:
            c2n[k] = nc
            G.add_node(nc, x=k[0], y=k[1])
            nc += 1
        return c2n[k]
```

**块功能**：把 MultiLineString 拆为 LineString，过滤无效几何，并定义节点去重函数。

**关键行解析**：
- L100 `explode()` 把 MultiLineString 拆成多条 LineString（一行一条），`ignore_index=True` 重建索引。
- L101 过滤掉非 LineString 类型的几何（如 Point、Polygon），避免后续构图出错。
- L103 裁剪后无有效几何 → 抛错。
- L105 创建无向图。**用无向图意味着所有道路都是双向通行**，若需要单向道路应改为 `nx.DiGraph`。
- L106 `c2n` 是「坐标 → 节点 ID」映射；`nc` 是节点计数器。
- L108-115 **节点去重函数**：
  - L110 `tol=1e-3` 表示 0.001 米（亚毫米级）容差。`round(x / tol) * tol` 把坐标量化到这个精度，避免浮点误差导致同一物理位置被识别为不同节点。
  - L111-114 如果该坐标键不在映射中，分配一个新 ID 并加入图（带 `x`, `y` 属性）。
  - L115 返回该坐标对应的节点 ID。

**修改建议**：
- 节点合并容差太宽会让相近的不同道路被错误连通；太严会让本该同一节点的坐标被分裂。**1mm 是个安全默认值**，但若数据精度低（如手工数字化）可放宽到 `1e-1`（0.1m）。
- 若需要单向道路，改 L105 为 `nx.DiGraph`，并在加边时考虑方向属性。

---

## 1.8 路网加载 — 边构建与 KDTree (Lines 117–129)

```python
    for _, row in roads.iterrows():
        cs = list(row.geometry.coords)
        for i in range(len(cs) - 1):
            u = _node(*cs[i])
            v = _node(*cs[i + 1])
            d = np.hypot(cs[i + 1][0] - cs[i][0], cs[i + 1][1] - cs[i][1])
            if not G.has_edge(u, v):
                G.add_edge(u, v, length=d)

    coords = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in G.nodes()])
    nids = np.array(list(G.nodes()))
    print(f"✅ Road graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G, nids, coords, KDTree(coords)
```

**块功能**：遍历每条 LineString，把相邻坐标点之间建立边；最后构造 KDTree 索引。

**逐行解析**：
- L117 `roads.iterrows()` 逐行遍历 GeoDataFrame。
- L118 取出该 LineString 的所有坐标点（一个列表，长度 ≥ 2）。
- L119-122 对每对相邻坐标 `(cs[i], cs[i+1])`：(1) 通过 `_node` 函数获取或创建节点 ID；(2) 用 `np.hypot` 算欧氏距离作为边权。
- L123-124 `if not G.has_edge`：避免重复添加边（同一对节点可能被多条道路连接）。这里只保留**第一条**遇到的边长，理论上同一对节点之间应该只有一条边，否则路网数据有重复。
- L126 把所有节点的 (x,y) 坐标提取为 (N,2) 数组，用于 KDTree。
- L127 节点 ID 数组（NetworkX 节点 ID 不一定是连续整数，所以单独保存）。
- L129 构造 KDTree 并随其他三个对象一起返回。**KDTree 用于后续的最近邻查询**，例如把居民坐标吸附到最近的路网节点。

**修改建议**：
- 想保留多条平行边时改用 `nx.MultiGraph`，然后去掉 L123 的 `has_edge` 检查。
- 想用真实道路长度而非欧氏距离（如曲折山路），先把 LineString 分段累加：`d = row.geometry.length` 替代 `np.hypot`，但要注意这是整条线段的总长，不是相邻点之间的。
- 添加道路属性（如等级、宽度），改 L124 为 `G.add_edge(u, v, length=d, highway=row.get('fclass'), width=row.get('width'))`。

---

## 1.9 路径预计算 — 吸附与缓存 (Lines 135–172)

```python
def precompute_paths(res_df, bus_xy, G, active_idx, bus_list,
                     kdtree, nids, ncoords):
    # --- 吸附上车点 ---
    bus_c = bus_xy[bus_list]
    bd, bi = kdtree.query(bus_c)
    snapped_bus = bus_xy.copy().astype(float)
    for i, j in enumerate(bus_list):
        snapped_bus[j] = ncoords[bi[i]]
    b2n = {j: int(nids[bi[i]]) for i, j in enumerate(bus_list)}

    # --- 吸附居民 ---
    rc = res_df[["x", "y"]].values[active_idx]
    rd, ri = kdtree.query(rc)
    snapped_res = ncoords[ri]
    rnids = nids[ri]

    print(f"🔗 Snap  res: avg={np.mean(rd):.1f}m  max={np.max(rd):.1f}m")
    print(f"🔗 Snap  bus: avg={np.mean(bd):.1f}m  max={np.max(bd):.1f}m")

    # --- Dijkstra 单源最短路缓存 ---
    cache = {}
    for il in range(len(active_idx)):
        rn = int(rnids[il])
        if rn not in cache:
            try:
                cache[rn] = nx.single_source_dijkstra_path(G, rn, weight="length")
            except nx.NetworkXError:
                cache[rn] = {}
```

**块功能**：(1) 把上车点和居民坐标吸附到最近的路网节点；(2) 对每个唯一居民节点运行一次 Dijkstra 并缓存结果。

**关键行解析**：
- L148 `bus_xy[bus_list]` 取出指定子集的上车点坐标（`bus_list` 是上车点全局索引列表）。
- L149 `kdtree.query(bus_c)` 返回 `(distances, indices)` —— 每个上车点最近的路网节点的距离和索引。**注意**：之前曾误写成 `_, bd, bi` 三值解包导致 bug，已修复为正确的两值解包。
- L150-152 用最近节点坐标覆盖原上车点坐标。`bus_xy.copy()` 避免修改原数组；只更新 `bus_list` 中的位置。
- L153 `b2n` 字典：上车点全局 ID → 路网节点 ID，供后续构建路径使用。
- L156-159 居民同样吸附；`rnids` 是每个居民对应的路网节点 ID。
- L161-162 打印吸附距离的均值与最大值，用于诊断（若 max 值很大说明居民/上车点远离路网，可能数据有问题）。
- L165-172 **关键优化**：用字典缓存每个居民起点的 Dijkstra 结果。`single_source_dijkstra_path(G, rn)` 返回从节点 `rn` 到所有其他节点的最短路径（节点序列）。**如果两个居民被吸附到同一路网节点，只算一次 Dijkstra**，复用缓存。
- L171-172 异常处理：如果该节点不在图的连通分量中，存空字典，后续构建路径时会自动失败。

**修改建议**：
- 如果想加权 Dijkstra（不是用纯距离），把 L170 的 `weight="length"` 换成自定义函数：`weight=lambda u,v,d: d['length'] / d.get('speed', 1)`。
- 如果路网很大、居民数也多，可改用 `nx.single_source_dijkstra_path_length` 只返回长度（不返回节点序列），最后再单独构造需要的路径，节省内存。

---

## 1.10 路径预计算 — 路径构建 (Lines 174–200)

```python
    paths, validity = {}, {}
    ok, fail = 0, 0
    for il in range(len(active_idx)):
        rn = int(rnids[il])
        pd_ = cache.get(rn, {})
        for j in bus_list:
            bn = b2n[j]
            if rn == bn:
                x, y = snapped_res[il]
                paths[(il, j)] = LineString([(x, y), (x + 0.001, y + 0.001)])
                validity[(il, j)] = True
                ok += 1
                continue
            if bn in pd_:
                cs = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in pd_[bn]]
                if len(cs) >= 2:
                    paths[(il, j)] = LineString(cs)
                    validity[(il, j)] = True
                    ok += 1
                    continue
            validity[(il, j)] = False
            fail += 1

    total = len(active_idx) * len(bus_list)
    print(f"   Paths: {ok}/{total} valid ({ok/total*100:.1f}%), {fail} no path")
    return paths, snapped_res, snapped_bus, validity
```

**块功能**：用缓存的 Dijkstra 结果构造每个 `(居民 il, 上车点 j)` 对的 LineString 路径。

**关键行解析**：
- L175 `paths` 字典：键是 `(居民局部索引, 上车点 ID)`，值是 LineString。
- L177-179 对每个居民取出对应的 Dijkstra 缓存。
- L180-181 对每个上车点 `j`，查 `b2n` 得到对应的路网节点 `bn`。
- L182-187 **特殊情况**：如果居民和上车点吸附到同一节点，构造一个长度极小的 LineString（避免零长度几何报错）。这种"自环"路径长度约 0.0014m，对优化目标几乎无影响。
- L188-194 **正常情况**：从缓存中取 Dijkstra 路径（节点序列），把每个节点 ID 转回坐标，构造 LineString。`len(cs) >= 2` 检查避免单点 LineString。
- L195-196 如果 Dijkstra 中没有路径（连通分量隔离），标记为不可达。
- L198-199 打印统计信息。
- L200 返回 4 个对象：路径字典、吸附后的居民/上车点坐标、可行性字典。

**修改建议**：
- 如果想保留多条备选路径（次短路径），把 L170 改为 `single_source_dijkstra_path` 之后再调用 `nx.shortest_simple_paths` 取前 K 条。
- 如果性能是瓶颈，可把这个嵌套循环改为**并行**（用 `joblib` 或 `multiprocessing`），但要注意 NetworkX 图不能直接 pickle 给子进程。

**常见陷阱**：L189 的列表推导 `[(G.nodes[n]["x"], G.nodes[n]["y"]) for n in pd_[bn]]` 在路径很长时是性能热点。如果路网很大可缓存所有节点坐标为字典：`node_xy = {n: (G.nodes[n]["x"], G.nodes[n]["y"]) for n in G.nodes()}`。

---

## 1.11 可行域构建 (Lines 206–219)

```python
def build_feasible(road_paths, n_active, bus_list, max_time_sec, speed):
    """
    基于已有道路路径和时间约束构建每个居民的可行上车点列表。
    不使用任何硬编码过滤规则，超时由目标函数自然惩罚。
    """
    feasible = [[] for _ in range(n_active)]
    for il in range(n_active):
        for j in bus_list:
            pl = road_paths.get((il, j))
            if pl is not None and pl.length / speed <= max_time_sec:
                feasible[il].append(j)
    no_opt = sum(1 for f in feasible if len(f) == 0)
    print(f"   Feasible: {n_active - no_opt}/{n_active} residents have reachable stops")
    return feasible, no_opt
```

**块功能**：为每个居民构建可行上车点列表（步行时间 ≤ 上限的所有上车点）。

**逐行解析**：
- L211 `feasible[il]` 是一个列表，存储居民 `il` 的所有可行上车点 ID。
- L212-216 双重循环遍历所有 `(居民, 上车点)` 对：
  - L214 取出预计算的路径（可能为 `None`）。
  - L215 **可达条件**：路径存在 **且** `路径长度 / 步行速度 ≤ 最大允许时间`。`pl.length / speed` 是预计步行耗时（秒）。
  - L216 通过则加入可行集。
- L217 统计无可行选项的居民数。

**修改建议**：
- 如果想放宽时间约束（让更多解被考虑），调用时增大 `max_time_sec`。
- 如果想加入额外的过滤条件（例如最小距离），改 L215 为复合条件。
- **强烈不建议**在此函数中加入风险阈值过滤——风险应该由目标函数自然权衡，硬过滤会丢失 Pareto 前沿的极端解。

---

# 第二部分：pickup_sink.py

## 2.1 模块文档与文献依据 (Lines 1–58)

```python
"""
上车点 Sink 边界条件模块 (Pickup Point Sink Model)
=================================================
... (省略文档字符串详细内容) ...
"""

import numpy as np
import math
from collections import defaultdict
```

**块功能**：模块文档、文献引用、参数依据；导入依赖。

**关键说明**：
- 文档字符串中的 5 篇核心文献是参数取值的学术依据，每篇都对应到特定的参数（参考 `Sink_Parameter_Citations.docx`）。
- L56-58 仅依赖 `numpy` 和 `defaultdict`——**没有 Numba 或 CuPy**。这是有意为之：sink 模块包含大量动态数据结构（变长队列），难以 Numba JIT。如果未来想加速，需要把队列改为定长 NumPy 数组。

**修改建议**：
- 如果你的研究需要新加文献依据，更新顶部的注释块即可，代码逻辑不变。
- 如果未来要 Numba 化，**新写一个 `_simulate_queue_at_stop_numba` 函数**，保持原函数作为 fallback。

---

## 2.2 SINK_CONFIG 配置字典 (Lines 63–83)

```python
SINK_CONFIG = dict(
    # ─── 巴士车队 ───
    bus_capacity         = 50,      # 单辆巴士容量 (人)
    fleet_size           = 30,      # 调度中心可用巴士总数
    bus_speed_kmh        = 30.0,    # 巴士平均行驶速度 (km/h)
    bus_speed_ms         = 30 * 1000 / 3600,   # 转 m/s ≈ 8.33

    # ─── 调度参数 ───
    dispatch_delay_sec   = 600,     # 调度响应延迟 (秒) ≈ 10 min
    boarding_time_per_pax = 2.5,    # 单人上车时间 (秒)

    # ─── 转运目的地 ───
    shelter_distance_m   = 30_000,  # 上车点到长期避难所距离 (m, 默认30km)

    # ─── 排队期间风险计算 ───
    queue_risk_enabled   = True,    # 是否计算排队期间的辐射暴露
    queue_dt_sec         = 60,      # 排队仿真时间步 (秒)

    # ─── 总疏散时长上限 ───
    max_evac_duration    = 7200,    # 7200s = 2小时
)
```

**块功能**：定义所有 sink 边界条件参数的默认值。

**关键参数说明**（详见 `Sink_Parameter_Citations.docx`）：

| 参数 | 取值 | 说明 |
|---|---|---|
| `bus_capacity` | 50 | 中国 12m 标准客车（GB/T 19260-2018） |
| `fleet_size` | 30 | Pereira & Bish 2015 / Sun 2024 中位数 |
| `bus_speed_ms` | 8.33 | 30 km/h 换算，城市道路应急工况 |
| `dispatch_delay_sec` | 600 | Goerigk & Grün 2014 给出的 [5,15] min 中位 |
| `boarding_time_per_pax` | 2.5 | TCQSM 3rd Ed. 低地板单门工况下界 |
| `shelter_distance_m` | 30000 | 超出 FEMA REP plume EPZ (16 km) |
| `max_evac_duration` | 7200 | 2 小时，与单次往返时间 ≈ 一致 |

**修改建议**：
- **敏感性分析**：敏感参数依次为 `dispatch_delay_sec`、`bus_capacity`、`fleet_size`。建议每个参数测试 3 档（如 dispatch=300/600/900）。
- **关闭排队风险**：将 `queue_risk_enabled` 设为 `False`，模型只计算总时间，不计算排队期间的辐射累积。这是与原模型的对比基线。
- **调整 shelter 距离**：如果你的研究区域避难所更近（如 15 km），改 `shelter_distance_m = 15_000`。这会让 `round_trip_time` 减半，sink 阶段时长大幅降低。

**常见陷阱**：`bus_speed_ms` 是从 `bus_speed_kmh` 推导的常量。如果改了 km/h 值，**必须同步改 m/s 值**，因为这里是字面常量而不是动态计算。建议改为：
```python
bus_speed_kmh = 30.0
bus_speed_ms  = bus_speed_kmh * 1000 / 3600
```

---

## 2.3 PickupSinkModel 初始化 (Lines 89–122)

```python
class PickupSinkModel:
    """上车点 sink 边界条件模型。"""

    def __init__(self, bus_xy, risk_arrays, x_mins, y_maxs, config=None):
        self.bus_xy = np.asarray(bus_xy, dtype=np.float64)
        self.risk_arrays = risk_arrays
        self.x_mins = x_mins
        self.y_maxs = y_maxs
        self.config = config or SINK_CONFIG
        self.n_bus = len(bus_xy)
```

**块功能**：构造函数，把所有外部数据存入实例属性。

**逐行解析**：
- L117 `np.asarray(..., dtype=np.float64)` 强制转换：保证后续向量化运算的类型一致性。**这一步即使 `bus_xy` 已经是 NumPy 数组也会快速通过**（不复制）。
- L118-120 直接引用列表（不复制），节省内存。这要求外部不能在 `process()` 调用期间修改这些列表。
- L121 `or` 短路：若 `config` 为 `None`，使用全局默认 `SINK_CONFIG`。
- L122 缓存上车点数量。

**修改建议**：
- 如果需要每个上车点有不同的车队规模或容量，把 `config` 从 dict 改为 dict 嵌套：`config = {0: {...}, 1: {...}, ...}`，并在 `_simulate_queue_at_stop` 中按 `stop_idx` 取对应配置。
- 如果想支持动态风险场更新（如每分钟更新），把 `risk_arrays` 改为 callable：`self.risk_func = risk_arrays`，并在 `_risk_at` 中调用 `self.risk_func(t)`。

---

## 2.4 process() 主入口 — 分组阶段 (Lines 127–159)

```python
def process(self, assignment, arrival_times, pop_arr,
            walk_risk=0.0, grid_res=400):
    cfg = self.config
    n = len(assignment)

    # ── Step 1: 按上车点分组 ──
    bus_arrivals = defaultdict(list)
    for i in range(n):
        j = assignment[i]
        if j == -1 or j is None:
            continue
        t = arrival_times[i]
        if not np.isfinite(t):
            continue
        bus_arrivals[j].append((float(t), float(pop_arr[i])))
```

**块功能**：把全局的「居民→上车点」分配方案重组为「上车点→[(到达时刻, 人口数)]」的事件列表。

**逐行解析**：
- L146 局部别名 `cfg`，避免反复 `self.config`。
- L147 `n` 是居民总数。
- L151 `defaultdict(list)`：访问不存在的键时自动创建空列表，比手动 `if j not in d: d[j] = []` 简洁。
- L152-159 遍历所有居民：
  - L153 取出该居民的目标上车点 `j`。
  - L154-155 跳过无分配的居民（`-1` 或 `None`）。
  - L156 取出步行到达时刻。
  - L157-158 跳过 `inf` 或 `nan`（这些是不可行解的标记）。
  - L159 把 `(到达时刻, 人口数)` 元组存入对应上车点的列表。**强制 `float` 转换**避免后续混合 numpy 类型。

**修改建议**：
- 如果想区分不同人口类型（老人、儿童），把元组扩展为 `(t, pop, age_group)` 并在仿真函数中对老人加权。
- 如果想支持「居民选择多个备选上车点」，把 `j` 改为列表，并在此处展开为多个事件。

---

## 2.5 process() 主入口 — 仿真与汇总 (Lines 161–201)

```python
    # ── Step 2: 对每个被使用的上车点运行排队仿真 ──
    max_completion_time = 0.0
    total_queue_risk = 0.0
    bus_trips_per_stop = {}
    max_queue_per_stop = {}

    for j, arr_list in bus_arrivals.items():
        # 按到达时间排序
        arr_list.sort(key=lambda x: x[0])

        completion, q_risk, n_trips, max_q = self._simulate_queue_at_stop(
            stop_idx=j,
            arrivals=arr_list,
            grid_res=grid_res,
        )

        if completion > max_completion_time:
            max_completion_time = completion
        total_queue_risk += q_risk
        bus_trips_per_stop[j] = n_trips
        max_queue_per_stop[j] = max_q

    # ── Step 3: 检查总时长是否超限 ──
    if max_completion_time > cfg["max_evac_duration"]:
        penalty = (max_completion_time - cfg["max_evac_duration"]) * pop_arr.sum()
        max_completion_time = max_completion_time
        total_queue_risk += penalty * 0.01

    info = dict(
        n_stops_used      = len(bus_arrivals),
        bus_trips_total   = sum(bus_trips_per_stop.values()),
        max_completion_s  = max_completion_time,
        max_queue_length  = max(max_queue_per_stop.values()) if max_queue_per_stop else 0,
        queue_risk        = total_queue_risk,
        bus_trips_per_stop = bus_trips_per_stop,
    )

    total_time = max_completion_time
    total_risk = walk_risk + total_queue_risk
    return total_time, total_risk, info
```

**块功能**：对每个被使用的上车点独立运行排队仿真，汇总全局结果并返回。

**关键行解析**：
- L162-165 4 个累积变量：最大完成时间、总排队风险、每个站的巴士次数、每个站的最大队列。
- L167-181 **主循环**：对每个被使用的上车点：
  - L169 **按到达时间排序**——这是 `_simulate_queue_at_stop` 的前置条件，里面的离散事件循环假设到达事件已排序。
  - L171-175 调用核心仿真函数。
  - L177-181 用 `max()` 取所有上车点中最晚完成的时间（这是全局疏散完成时间），用 `+=` 累加风险（每个站的排队风险都贡献给总风险）。
- L184-188 **超时软惩罚**：
  - L184 完成时间超过 max_evac_duration 时计算惩罚值。
  - L186 `penalty` = 超时秒数 × 总人口（一个数量级很大的值）。
  - L187 这一行 `max_completion_time = max_completion_time` 是无效赋值（注释说是为了保留真实值），可以删除。
  - L188 把惩罚的 1% 加到风险上。**为什么 1%**：纯粹是经验值，让超时方案显著劣化但不完全压垮其他目标。
- L190-197 构造统计字典 `info`，供调用方记录日志或可视化。
- L199-200 **关键设计**：`total_time` = sink 阶段完成时间（不是 sink + walk）；`total_risk` = walk 风险 + queue 风险。这种分离使得 evaluate 函数可以根据业务需要重新组合两个目标。

**修改建议**：
- **改硬约束**：把 L184-188 改为 `if max_completion_time > cfg["max_evac_duration"]: return (np.inf, np.inf, info)`，让超时方案直接被 NSGA-II 排除。这会让前沿更收敛但可能丢失边界情况下的可行解。
- **改惩罚系数**：L188 的 `0.01` 系数过小会让超时方案被选入前沿；过大会主导风险目标。建议先用默认值跑一次，看 Pareto 前沿是否合理，再调整。
- **删除无效行**：L187 可以删除。

---

## 2.6 _simulate_queue_at_stop() — 初始化 (Lines 206–243)

```python
def _simulate_queue_at_stop(self, stop_idx, arrivals, grid_res):
    cfg = self.config
    bx, by = self.bus_xy[stop_idx]

    round_trip_time = (
        2.0 * cfg["shelter_distance_m"] / cfg["bus_speed_ms"]
    )

    first_bus_arrival = cfg["dispatch_delay_sec"]
    cap = cfg["bus_capacity"]
    boarding_per_pax = cfg["boarding_time_per_pax"]

    queue = []
    idx_next_arrival = 0
    n_arrivals = len(arrivals)

    cur_time = 0.0
    next_bus_time = first_bus_arrival
    n_trips = 0
    max_queue = 0
    queue_risk = 0.0
    last_event_time = 0.0
```

**块功能**：单上车点离散事件仿真的初始化。

**逐行解析**：
- L221 取出该上车点坐标，供后续查询风险场使用。
- L225-227 **单次往返时间** = 2 × 避难所距离 / 巴士速度。这里假设：调度中心 → 上车点的时间 ≈ 0（即调度中心就在上车点附近），主要时间消耗在 上车点 ↔ 避难所 的往返。**如果调度中心实际上很远，需要加上 dispatch_to_stop 的时间**。
- L229 第一辆巴士到达时间 = 调度延迟（默认 600s）。**注意**：这里假设第一辆巴士在 t=0 接收到调度命令，t=600s 抵达上车点。如果实际场景是「事故发生 → 警报发出 → 调度命令」，需要再加一个 alarm_delay。
- L230-231 局部别名提速。
- L234 **队列结构**：列表 of `[到达时刻, 剩余人数]`。注意是 list 不是 tuple——因为部分装载时需要修改剩余人数。
- L235 `idx_next_arrival` 是下一个要处理的居民到达事件的索引（指向 `arrivals` 列表）。
- L238-243 仿真状态变量：
  - `cur_time`：当前仿真时钟（秒）。
  - `next_bus_time`：下一辆巴士到达时刻。
  - `n_trips`：累计已派出的巴士次数。
  - `max_queue`：历史最大队列长度（人数）。
  - `queue_risk`：累积排队风险。
  - `last_event_time`：上一个事件的时间，用于计算事件间隔。

**修改建议**：
- 加入 alarm_delay：把 L229 改为 `first_bus_arrival = cfg.get("alarm_delay_sec", 0) + cfg["dispatch_delay_sec"]`。
- 考虑车队规模约束：当前模型假设车队无限——只要时间允许就持续派车。若要建模车队上限，需要在 L240 加入 `bus_pool = cfg["fleet_size"]`，每次派车前检查 `bus_pool > 0`。

---

## 2.7 _simulate_queue_at_stop() — 主事件循环 (Lines 245–298)

```python
    while idx_next_arrival < n_arrivals or queue:
        # 下一个事件的时间
        next_arr_time = (arrivals[idx_next_arrival][0]
                         if idx_next_arrival < n_arrivals else float("inf"))
        next_event_time = min(next_arr_time, next_bus_time)

        if next_event_time == float("inf"):
            break

        # ── 累积该时间段内的排队风险 ──
        dt = next_event_time - last_event_time
        if dt > 0 and queue and cfg["queue_risk_enabled"]:
            queue_pop = sum(q[1] for q in queue)
            if queue_pop > 0:
                risk_val = self._risk_at(bx, by, last_event_time, grid_res)
                queue_risk += risk_val * queue_pop * dt

        cur_time = next_event_time
        last_event_time = cur_time

        # ── 事件类型判定 ──
        if next_event_time == next_arr_time:
            # 事件: 居民到达
            queue.append([arrivals[idx_next_arrival][0],
                          arrivals[idx_next_arrival][1]])
            idx_next_arrival += 1
            cur_q = sum(q[1] for q in queue)
            if cur_q > max_queue:
                max_queue = cur_q
        else:
            # 事件: 巴士到达
            load = 0
            while queue and load < cap:
                take = min(cap - load, queue[0][1])
                queue[0][1] -= take
                load += take
                if queue[0][1] <= 1e-6:
                    queue.pop(0)
            board_dur = load * boarding_per_pax
            if load > 0:
                n_trips += 1
                if cfg["queue_risk_enabled"] and board_dur > 0:
                    residual_q = sum(q[1] for q in queue) + load
                    risk_val = self._risk_at(bx, by, cur_time, grid_res)
                    queue_risk += risk_val * residual_q * board_dur
                cur_time += board_dur
            next_bus_time = cur_time + round_trip_time
```

**块功能**：离散事件仿真的核心循环。每次推进到下一个事件（居民到达或巴士到达），处理事件并累积风险。

**逐行详解**：

**循环条件 L247**：还有未处理的到达事件 OR 队列非空。两个条件用 OR：即使所有居民都到达了，只要队列还有人就继续仿真直到巴士装完。

**确定下一事件 L249-251**：
- L249-250 下一个居民到达时刻；如果所有居民都到达了，设为 `inf`。
- L251 下一个事件 = 居民到达 vs 巴士到达 中较早的那个。

**异常退出 L253-254**：如果两个事件都是 `inf`（说明没有居民到达 **且** 也没有巴士派出，理论上不会发生），跳出循环。

**累积排队风险 L257-262**：
- L257 时间间隔 `dt` = 下个事件 - 上个事件。
- L258 三个条件：(1) 间隔大于 0；(2) 队列非空；(3) 配置开启了风险计算。
- L259 当前队列总人数（队列里每个元素是 `[到达时刻, 剩余人数]`，所以 `q[1]`）。
- L261 查询该时刻、该上车点位置的风险值。**用 `last_event_time` 而非 `cur_time`**——因为风险是从上一个事件持续到当前事件的，使用区间起点的风险值。
- L262 风险公式：`risk × 队列人数 × 时长`。这是 Pereira & Bish 2015 中 `total exposure` 的离散化形式。

**推进时钟 L264-265**：把当前时刻和上一事件时刻都更新为新事件时刻。

**居民到达事件 L268-276**：
- L270-271 把新到达的居民 `[时刻, 人数]` 加入队列尾部。
- L272 索引前进。
- L274-276 更新历史最大队列长度（用于统计）。

**巴士到达事件 L277-298**：
- L280 `load` = 本辆巴士本次装载的人数。
- L281-286 **装载循环**：
  - L282 `take` = 该次能从队首取出的人数（受队首剩余人数和巴士剩余容量限制）。
  - L283 队首剩余人数减少。
  - L284 累计装载量增加。
  - L285-286 如果队首被装完（剩余 ≤ 1e-6 处理浮点误差），从队列中移除。
  - **关键设计**：这是 FIFO 队列，保证先到先上车的公平性。
- L288 `board_dur` = 上车耗时 = 装载人数 × 单人上车时间。
- L289-296 仅当装载了至少 1 人才记录这次往返：
  - L290 巴士次数 +1。
  - L292-295 **上车期间也累积风险**：包括正在上车的人 + 仍在排队等待的人。这一段时间是 `board_dur`。
  - L296 时钟前进 `board_dur`（巴士滞留时间）。
- L298 下一辆巴士到达时刻 = 当前时刻 + 往返时间。**注意**：这里假设车队连续运转——上一辆装载完毕后才派下一辆。如果想建模并行车队（多辆同时到达），需要重写为多事件队列。

**修改建议**：
- **车队上限约束**：在 L298 之前加 `if n_trips >= cfg["fleet_size"]: next_bus_time = float("inf")`，超过车队规模后停止派车。
- **快速装载（无 FIFO）**：把 L281-286 改为 `take = min(cap, sum_queue)`，但要单独维护总队列人数变量。
- **巴士并行**：用 heap 维护多个 `next_bus_time`，每次取最早的事件。
- **死锁保护**：当前循环可能在某些边界条件下无限循环（例如所有居民到达后队列非空但 `next_bus_time` 不再前进）。建议加 `if cur_time > 10 * cfg["max_evac_duration"]: break` 作为保险。

**常见陷阱**：
1. L262 的 `risk_val` 用 `last_event_time` 是关键——之前的版本曾用 `cur_time` 导致风险被高估。
2. L285 的 `<= 1e-6` 浮点容差不能省略，否则浮点误差导致队首永远不被移除，陷入死循环。
3. 装载循环 L281 可能在容量很大、队列很短的情况下成为热点——可以预先 `if not queue: continue` 跳过。

---

## 2.8 _simulate_queue_at_stop() — 收尾 (Lines 300–301)

```python
    completion_time = cur_time
    return completion_time, queue_risk, n_trips, max_queue
```

**块功能**：返回该上车点的 4 个统计指标。

**说明**：`completion_time` 是该上车点最后一次有装载的巴士的离开时刻，即「最后一名乘客上车的时刻」。这个值会在 `process()` 中用 `max()` 聚合为全局完成时间。

---

## 2.9 _risk_at() 风险查询 (Lines 306–324)

```python
def _risk_at(self, x, y, t_sec, grid_res):
    """根据时刻 t (秒) 选择对应的风险矩阵, 查询 (x,y) 处的风险值"""
    t_min = t_sec / 60.0
    if t_min < 15:
        si = 0
    elif t_min < 25:
        si = 1
    elif t_min < 35:
        si = 2
    else:
        si = 3
    ra = self.risk_arrays[si]
    xm = self.x_mins[si]
    ym = self.y_maxs[si]
    col = int((x - xm) / grid_res)
    row = int((ym - y) / grid_res)
    if 0 <= row < ra.shape[0] and 0 <= col < ra.shape[1]:
        return float(ra[row, col])
    return 0.0
```

**块功能**：查询时刻 t 在位置 (x,y) 的辐射风险值。

**关键行解析**：
- L308 把秒换算为分钟（风险阶段以分钟为单位）。
- L309-316 **时间阶段映射**：[0,15) → 阶段 0；[15,25) → 阶段 1；[25,35) → 阶段 2；[35,∞) → 阶段 3。这个分段对应 4 个 Excel 风险矩阵（在 `config.py` 中定义）。
- L317-319 取出对应阶段的风险矩阵和空间参照。
- L320-321 **坐标→索引转换**：
  - `col = (x - x_min) / res` —— x 越大列号越大。
  - `row = (y_max - y) / res` —— y 越大行号越小（图像坐标系）。
- L322-323 越界检查：若超出矩阵边界返回 0（认为安全区）。

**修改建议**：
- 如果你的研究需要更多时间阶段（如 8 段而不是 4 段），把 L309-316 的硬编码 if-elif 改为基于配置的循环：
  ```python
  STAGE_TIMES = [0, 5, 10, 15, 20, 25, 30, 45]  # 8 段
  si = sum(1 for t in STAGE_TIMES[1:-1] if t_min >= t)
  ```
- 如果想支持双线性插值（而不是最近邻），把 L320-323 改为四点加权平均。

**性能提示**：这个函数在 `_simulate_queue_at_stop` 中可能被频繁调用。如果性能是瓶颈，可以批量化：把所有需要查询的 `(x, y, t)` 一次性传入，返回结果数组。

---

## 2.10 check_fleet_capacity() 辅助函数 (Lines 330–351)

```python
def check_fleet_capacity(assignment, pop_arr, bus_xy, sink_config=None):
    cfg = sink_config or SINK_CONFIG
    total_pop = sum(pop_arr[i] for i in range(len(assignment)) if assignment[i] != -1)

    round_trip = 2 * cfg["shelter_distance_m"] / cfg["bus_speed_ms"]
    max_trips = max(1, int((cfg["max_evac_duration"] - cfg["dispatch_delay_sec"]) / round_trip))
    fleet_capacity = cfg["fleet_size"] * cfg["bus_capacity"] * max_trips

    return total_pop <= fleet_capacity, dict(
        total_population=total_pop,
        fleet_max_capacity=fleet_capacity,
        max_round_trips=max_trips,
        utilization=total_pop / fleet_capacity if fleet_capacity > 0 else float("inf"),
    )
```

**块功能**：粗略检查方案是否在车队总容量内（不运行完整仿真）。

**逐行解析**：
- L339 累加所有有效分配的人口数。
- L342 单次往返时间。
- L343 **最大往返次数估算**：可用时间 / 单次往返时间。`max(1, ...)` 保证至少 1 次。注意这里减去了 `dispatch_delay`（开头的派车延迟）。
- L344 车队总运力 = 车队规模 × 单车容量 × 最大往返次数。
- L346 返回 `(可行?, 详细统计)`。

**用途**：在 NSGA-II 评估之前做粗筛——如果 `check_fleet_capacity` 返回 `False`，说明方案物理上不可能完成，可直接给极差适应度避免运行 sink 仿真。**目前代码没有调用此函数**，需要你手动集成到 `make_evaluate` 中：
```python
feasible_capacity, _ = check_fleet_capacity(list(ind), pop_arr, bus_xy, sink_config)
if not feasible_capacity:
    return (np.inf, np.inf)
```

---

# 第三部分：optimizer.py

## 3.1 模块文档与导入 (Lines 1–20)

```python
"""
Q-NSGA-II 优化引擎
- QuantumIndividual：量子比特编码
- QuantumRotationGate：自适应旋转门
- 量子交叉 / 变异 / 灾变算子
- 双目标评估函数（人口加权时间 + 沿路网插值风险）
- 主进化循环 run_qnsga2()
- 解选择与真实指标计算
"""
import math
import random
import numpy as np
from deap import base, creator, tools, algorithms

from config import (
    WALK_SPEED, GRID_RES, MAX_WALK_TIME,
    NSGA2_CONFIG, QNSGA2_CONFIG,
)

DEFAULT_MAX_TIME = 45 * 60
```

**块功能**：导入 DEAP 多目标进化框架、随机数库、配置参数。

**关键说明**：
- L13 `deap` 是 Python 多目标进化算法标准库。`base` 提供 Toolbox，`creator` 用于动态创建 Individual 类，`tools` 包含选择/排序算子，`algorithms` 包含 `varOr` 等遗传操作。
- L20 `DEFAULT_MAX_TIME = 45 * 60` = 2700 秒 = 45 分钟。这是步行阶段的最大允许时间。

---

## 3.2 风险查询内联函数 (Lines 26–31)

```python
def _risk(x, y, arr, xm, ym, res=GRID_RES):
    c = int((x - xm) / res)
    r = int((ym - y) / res)
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]:
        return arr[r, c]
    return 0.0
```

**块功能**：单点风险查询（与 `pickup_sink._risk_at` 功能相同，但少了时间阶段判断）。

**为什么单独定义**：性能考虑——这个函数在 `make_evaluate` 中被高频调用（每代 N×T 次），内联在同一文件可避免跨模块调用开销。`pickup_sink._risk_at` 多了一层时间→阶段判断，不能直接复用。

**修改建议**：如果未来重构想统一风险查询逻辑，可以删除这个函数，改为从 `pickup_sink` 导入 `_risk_at` 并用一个固定阶段索引调用。性能损失大约 5-10%。

---

## 3.3 QuantumIndividual 量子个体类 (Lines 37–75)

```python
class QuantumIndividual:
    """
    Q-bit 编码个体。每个居民 i 维护角度向量 θ[i]，
    选择第 k 个可行上车点的概率 ∝ cos²(θ[i][k])。
    """
    __slots__ = ("n", "feasible", "theta")

    def __init__(self, feasible, init="uniform"):
        self.n = len(feasible)
        self.feasible = feasible
        if init == "uniform":
            self.theta = [np.full(len(feasible[i]), math.pi / 4)
                          for i in range(self.n)]
        else:
            self.theta = [np.random.uniform(0, math.pi / 2, len(feasible[i]))
                          for i in range(self.n)]

    def observe(self):
        """按 cos²(θ) 概率抽样 → 经典解"""
        sol = []
        for i in range(self.n):
            pr = np.cos(self.theta[i]) ** 2
            s = pr.sum()
            k = (np.random.choice(len(self.feasible[i]), p=pr / s)
                 if s > 1e-12
                 else random.randint(0, len(self.feasible[i]) - 1))
            sol.append(self.feasible[i][k])
        return sol

    def observe_greedy(self):
        """确定性：取概率最大的选项"""
        return [self.feasible[i][np.argmax(np.cos(self.theta[i]) ** 2)]
                for i in range(self.n)]

    def copy(self):
        new = QuantumIndividual.__new__(QuantumIndividual)
        new.n, new.feasible = self.n, self.feasible
        new.theta = [a.copy() for a in self.theta]
        return new
```

**块功能**：量子比特编码的个体类。每个居民有一个角度向量，向量长度等于该居民的可行上车点数。

**逐块解析**：

**L42 `__slots__`**：声明实例只能有这 3 个属性。优势：(1) 内存占用比普通 dict 小很多；(2) 属性访问更快。代价：不能动态加属性。在大量个体的进化算法中很有用。

**L44-52 `__init__`**：
- L45 `n` = 居民数。
- L46 `feasible` 是列表的列表，`feasible[i]` 是第 i 个居民的可行上车点 ID 列表。
- L47-49 `init="uniform"` 模式：所有角度初始化为 π/4，对应每个选项概率 cos²(π/4) = 0.5（均匀分布）。这是 Han & Kim 经典 QEA 的标准初始化。
- L50-52 `init="random"` 模式：角度在 [0, π/2] 内随机，概率分布也是随机的。用于灾变后重置部分个体。

**L54-64 `observe()`**：
- L56 `sol` 是即将返回的经典解（上车点 ID 列表）。
- L58 `pr = cos²(θ)` 是未归一化概率。
- L59 求和用于归一化。
- L60-62 **核心抽样**：用 `np.random.choice` 按概率分布抽样选项索引 `k`；若概率和过小（数值病态），退化为均匀随机。
- L63 把选项索引 `k` 映射回上车点 ID（`feasible[i][k]`）。

**L66-69 `observe_greedy()`**：取概率最大的选项（`argmax`），用于产生确定性解（在量子旋转门的引导步骤中使用）。

**L71-75 `copy()`**：手动深拷贝。
- L72 `__new__` 跳过 `__init__`，避免重新初始化角度。
- L73 `n` 和 `feasible` 是不变量，可以浅引用。
- L74 `theta` 是关键状态，必须深拷贝（每个 NumPy 数组都要 `.copy()`）。

**修改建议**：
- 如果想用其他量子门（如 H 门或 X 门），添加新方法 `apply_hadamard()` 等，但保持 `observe()` 接口不变。
- 如果想测试不同的初始化策略，可以加 `init="exponential"` 等模式，但建议先在小规模上验证收敛性。

**性能提示**：`observe()` 在主循环中被调用 `mu × n_observations × ngen` 次（默认 400×3×160=192k 次），是热点之一。如果性能不够，可以考虑批量化：一次性产生 `mu` 个个体的所有观测。

---

## 3.4 QuantumRotationGate 旋转门 (Lines 81–113)

```python
class QuantumRotationGate:
    """
    自适应旋转门：早期 Δθ 大（探索），后期 Δθ 小（开发）。
    引导解支配当前解时全幅旋转，否则缩小为 0.3 倍。
    """
    def __init__(self, d_max, d_min):
        self.d_max, self.d_min = d_max, d_min

    def delta(self, gen, ngen):
        return self.d_max - (self.d_max - self.d_min) * gen / max(ngen, 1)

    def rotate(self, qi, cur, guide, cur_f, guide_f, dt, feasible):
        dom = self._dom(guide_f, cur_f)
        dt2 = dt if dom else dt * 0.3
        for i in range(qi.n):
            if cur[i] == guide[i]:
                continue
            try:
                gi = feasible[i].index(guide[i])
                ci = feasible[i].index(cur[i])
            except ValueError:
                continue
            qi.theta[i][gi] = max(0.01, qi.theta[i][gi] - dt2)
            qi.theta[i][ci] = min(math.pi / 2 - 0.01, qi.theta[i][ci] + dt2)

    @staticmethod
    def _dom(a, b):
        """a 是否 Pareto-支配 b（双目标最小化）"""
        if np.isinf(a[0]) or np.isinf(a[1]):
            return False
        if np.isinf(b[0]) or np.isinf(b[1]):
            return True
        return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))
```

**块功能**：自适应量子旋转门，用 Pareto 引导解调整当前个体的角度向量。

**关键行解析**：

**L86-87 `__init__`**：保存最大/最小旋转角度。

**L89-90 `delta(gen, ngen)`**：线性退火——early 代用 d_max，late 代用 d_min。这是经典 QEA 的核心机制，对应「先探索后开发」的搜索策略。

**L92-104 `rotate()` 核心算子**：
- L93 判断引导解是否 Pareto 支配当前解。
- L94 **关键设计**：支配时全幅旋转（dt2 = dt），不支配时缩小为 0.3 倍。这是「保守跟随」——即使引导解不一定更好，也部分采纳其方向。
- L95-104 对每个居民 i：
  - L96-97 如果当前选择与引导选择一致，无需旋转。
  - L98-102 找到引导选项 `gi` 和当前选项 `ci` 在可行域中的索引；若不在可行域中（罕见，可能因数据不一致），跳过。
  - L103 **降低引导选项的角度**——cos² 增大（被选中概率增大）。`max(0.01, ...)` 防止角度落到 0（数值病态）。
  - L104 **增大当前选项的角度**——cos² 减小（被选中概率减小）。`min(π/2 - 0.01, ...)` 防止角度落到 π/2。
- L103-104 这一对操作的物理含义：把概率质量从「当前选项」转移到「引导选项」。

**L106-113 `_dom()` 静态方法**：
- L109-110 如果 `a` 是不可行（inf），不可能支配。
- L111-112 如果 `b` 是不可行而 `a` 可行，`a` 自动支配。
- L113 标准 Pareto 支配定义：`a` 在所有目标上 ≤ `b`，且至少一个目标严格 <。

**修改建议**：
- 若想提高早期探索强度，把 L94 的 `0.3` 改为 `0.5`（不支配时也旋转得更多）。
- 若想用其他退火策略（如指数衰减），改 L90：`return self.d_min + (self.d_max - self.d_min) * np.exp(-3 * gen / ngen)`。
- 若想引入随机扰动避免早熟，在 L103-104 加上 `+ random.uniform(-0.01, 0.01)`。

---

## 3.5 量子算子 — 交叉 / 变异 / 灾变 (Lines 119–146)

```python
def quantum_crossover(q1, q2, rate=0.5):
    """角度空间逐维交换"""
    c1, c2 = q1.copy(), q2.copy()
    for i in range(q1.n):
        if random.random() < rate:
            c1.theta[i], c2.theta[i] = c2.theta[i].copy(), c1.theta[i].copy()
    return c1, c2


def quantum_mutation(q, rate=0.15, pert=0.1 * math.pi):
    """角度随机扰动"""
    m = q.copy()
    for i in range(m.n):
        if random.random() < rate:
            for k in range(len(m.theta[i])):
                if random.random() < 0.5:
                    m.theta[i][k] += random.uniform(-pert, pert)
                    m.theta[i][k] = max(0.01, min(math.pi / 2 - 0.01, m.theta[i][k]))
    return m


def quantum_catastrophe(qpop, rate=0.1):
    """重新初始化部分种群，防止早熟"""
    for idx in random.sample(range(len(qpop)), int(len(qpop) * rate)):
        qi = qpop[idx]
        for i in range(qi.n):
            qi.theta[i] = np.random.uniform(0, math.pi / 2, len(qi.theta[i]))
    return qpop
```

**块功能**：3 个量子算子。

**逐块解析**：

**`quantum_crossover` (L119-125)**：
- L121 拷贝两个父代生成子代。
- L122-124 对每个居民，以概率 `rate=0.5` 交换两个父代该居民的角度向量。注意是**整个向量**交换，不是单元素，这相当于均匀交叉的「按段」版本。
- L124 必须 `.copy()`，否则两个子代会共享同一数组导致后续修改互相影响。

**`quantum_mutation` (L128-137)**：
- L131-136 双层概率：先按 `rate=0.15` 决定该居民是否变异；变异时再按 0.5 决定每个角度元素是否扰动。
- L135 添加均匀分布扰动（[-0.1π, 0.1π]）。
- L136 截断到 [0.01, π/2 - 0.01]。

**`quantum_catastrophe` (L140-146)**：
- L142 `random.sample` 不重复地随机选 `int(N × rate)` 个个体。
- L143-145 把选中个体的所有角度重新初始化为 [0, π/2] 内的随机值。这是「打散重启」机制，防止种群陷入局部最优。

**修改建议**：
- **变异强度**：`pert = 0.1 * math.pi` 是一个较温和的扰动。如果发现进化停滞，可加大到 `0.2 * math.pi`。
- **交叉率**：L122 的 `rate=0.5` 表示每个居民有 50% 概率交换。如果想保留更多父代信息，降到 0.3。
- **灾变强度**：L142 的 `rate=0.1` 表示 10% 个体被打散。在小规模测试时可临时改为 0.0（关闭灾变）以观察单纯进化效果。

---

## 3.6 setup_deap() DEAP 框架配置 (Lines 152–174)

```python
def setup_deap(feasible):
    """创建 DEAP 双目标最小化 Individual 与 Toolbox"""
    if hasattr(creator, "FitnessMulti"): del creator.FitnessMulti
    if hasattr(creator, "Individual"):   del creator.Individual
    creator.create("FitnessMulti", base.Fitness, weights=(-1.0, -1.0))
    creator.create("Individual", list, fitness=creator.FitnessMulti)

    tb = base.Toolbox()
    n = len(feasible)
    tb.register("individual",
                lambda: creator.Individual([random.choice(feasible[i]) for i in range(n)]))
    tb.register("population", tools.initRepeat, list, tb.individual)
    tb.register("mate", tools.cxTwoPoint)

    def _mut(ind, indpb=NSGA2_CONFIG["indpb"]):
        for i in range(len(ind)):
            if random.random() < indpb:
                ind[i] = random.choice(feasible[i])
        return (ind,)

    tb.register("mutate", _mut)
    tb.register("select", tools.selNSGA2)
    return tb
```

**块功能**：配置 DEAP 框架——动态创建 Individual 类，注册交叉/变异/选择算子。

**关键行解析**：
- L154-155 **关键的清理**：如果之前已经创建过 `FitnessMulti` 和 `Individual`，先删除。这是 DEAP 的常见陷阱——`creator.create` 会向 `creator` 模块注入全局类，重复调用会报错。在多次调用 `setup_deap` 的场景（如批量分组）必须先清理。
- L156 创建 Fitness 类，`weights=(-1.0, -1.0)` 表示**双目标最小化**（DEAP 用负权重表示最小化）。
- L157 创建 Individual 类，继承 `list`，挂上 fitness 属性。
- L161-162 注册「individual factory」：每次调用产生一个随机初始化的个体（每个基因从该居民的可行域中随机选一个）。
- L163 注册「population factory」：用 `initRepeat` 工厂模式批量产生个体。
- L164 经典两点交叉。
- L166-170 自定义变异函数：每个基因以 `indpb` 概率被随机替换为其可行域中的另一个上车点。注意 L170 必须返回 tuple（DEAP 约定）。
- L172 注册变异。
- L173 注册 NSGA-II 选择算子（DEAP 内置实现）。
- L174 返回 toolbox。

**修改建议**：
- 若想改变异概率，改 L166 的 `indpb=NSGA2_CONFIG["indpb"]`。
- 若想用其他交叉算子（均匀交叉、PMX），改 L164：`tb.register("mate", tools.cxUniform, indpb=0.5)`。
- 若想用 NSGA-III（适合 3+ 目标），改 L173 为 `tb.register("select", tools.selNSGA3, ref_points=...)`。

**常见陷阱**：L154-155 的清理代码是必需的，删除后第二次调用会抛 `RuntimeError: A class named 'FitnessMulti' has already been created`。

---

## 3.7 make_evaluate() — 函数签名与初始化 (Lines 180–209)

```python
def make_evaluate(res_x, res_y, pop_arr, bus_xy, road_paths,
                  risk_arrays, x_mins, y_maxs,
                  speed=WALK_SPEED, max_time=DEFAULT_MAX_TIME,
                  use_sink=True, sink_config=None):
    """
    返回 evaluate(ind) → (time_obj, risk_obj)

    时间目标 (含 sink 边界):
        T_total = max(walk_time + queue_wait + bus_transit)
        其中 queue_wait 由巴士调度+容量约束决定

    风险目标:
        R_total = walk_risk + queue_risk

    参数:
        use_sink    – 是否启用 sink 边界条件 (默认启用)
        sink_config – PickupSinkModel 配置, None 时使用默认值
    """
    n = len(res_x)
    t_lim_min = int(max_time / 60)
    pop_np = np.asarray(pop_arr, dtype=np.float64)

    # 初始化 sink 模型 (一次性, 复用)
    sink_model = None
    if use_sink:
        from pickup_sink import PickupSinkModel
        sink_model = PickupSinkModel(bus_xy, risk_arrays, x_mins, y_maxs, sink_config)
```

**块功能**：工厂函数。返回一个闭包 `evaluate`，闭包捕获所有外部数据；在调用时只接受一个 `ind` 参数。

**关键行解析**：
- L180-183 函数签名。`use_sink=True` 是新增参数，控制是否启用 sink 边界条件。
- L201 `n` = 居民数。
- L202 `t_lim_min` = 步行风险计算的时间步数（默认 45）。
- L203 把人口数组转为 NumPy float64，供 sink 模型和后续向量化运算使用。
- L206-209 **关键设计**：sink 模型在工厂函数中**只创建一次**，被闭包捕获后多次复用。这避免了每次 evaluate 都重新初始化的开销。

**修改建议**：
- 若想在 sink 模型创建时使用不同的配置（如做敏感性分析），可在调用 `make_evaluate` 时传入 `sink_config={"bus_capacity": 30, ...}`。
- 若想把 sink 模型注入而非内部创建（便于测试或重用），改函数签名加入 `sink_model=None` 参数。

---

## 3.8 evaluate() — Phase 1 步行时间 (Lines 211–229)

```python
    def evaluate(ind):
        # ── Phase 1: 步行阶段 ──
        infos, times = [], []
        max_t = 0.0
        for i in range(n):
            j = ind[i]
            pl = road_paths.get((i, j))
            if pl is None:
                return (np.inf, np.inf)
            d = pl.length
            t = d / speed
            times.append(t)
            infos.append((pl, d, j))
            if t > max_t:
                max_t = t
        if max_t > max_time:
            return (np.inf, np.inf)

        walk_time_weighted = sum(t * p for t, p in zip(times, pop_arr))
```

**块功能**：计算每个居民到达上车点的步行时间，过滤不可行解，计算人口加权总步行时间。

**逐行解析**：
- L213 两个累积列表：`infos` 存储每个居民的 `(路径, 距离, 上车点ID)`，供 Phase 2 使用；`times` 存储每个居民的步行时间。
- L214 `max_t` 跟踪最长步行时间。
- L215-225 主循环：
  - L216 `j = ind[i]` 取出当前个体对该居民的上车点分配。
  - L217 从预计算的路径字典中查找。
  - L218-219 **路径不存在 → 不可行解**，立刻返回 inf。这是 NSGA-II 处理硬约束的标准方式。
  - L220 路径长度（米）。
  - L221 步行时间 = 距离 / 速度（秒）。
  - L222-223 累积。
  - L224-225 更新最大值。
- L226-227 **超时 → 不可行**。注意：与 `build_feasible` 中的过滤不同，这里是双重保险（可行域是基于默认 max_time，运行时可能用了不同的值）。
- L229 人口加权总步行时间——这是双目标的「时间目标」基础部分。

**修改建议**：
- 若想用 max（最长步行时间）而不是 sum（总步行时间）作为目标，把 L229 改为 `walk_time_max = max_t * sum(pop_arr)` 之类的形式。这会改变 Pareto 前沿的形状。
- 若想加上居民起点风险（在路径起点的风险），可在循环内累加。

---

## 3.9 evaluate() — Phase 2 步行风险 (Lines 231–253)

```python
        # ── Phase 2: 步行风险 (沿路径累积) ──
        walk_risk = 0.0
        for minute in range(t_lim_min):
            ts = minute * 60
            si = 0 if minute < 15 else (1 if minute < 25 else (2 if minute < 35 else 3))
            ra, xm, ym = risk_arrays[si], x_mins[si], y_maxs[si]
            rt = 0.0
            for i in range(n):
                pl, d, dj = infos[i]
                dc = ts * speed
                if dc >= d:
                    x, y = bus_xy[dj]
                elif d > 0:
                    pt = pl.interpolate(dc / d, normalized=True)
                    x, y = pt.x, pt.y
                else:
                    x, y = bus_xy[dj]
                rt += _risk(x, y, ra, xm, ym) * pop_arr[i]
            walk_risk += rt * 60

        if not use_sink:
            return walk_time_weighted, walk_risk
```

**块功能**：沿路径插值计算每分钟每个居民位置的辐射风险，累积为总剂量。

**逐行解析**：
- L233 双重循环：外层时间步，内层居民。
- L234 当前时刻（秒）。
- L235 **时间阶段映射**（同 `_risk_at`）：[0,15)→0；[15,25)→1；[25,35)→2；[35,∞)→3。注意这里写在一行的嵌套三元运算符中，可读性较差但执行快。
- L236 取出当前阶段的风险矩阵和参照。
- L237 该分钟的风险累加器。
- L238-247 内层循环：
  - L239 取出该居民的路径信息。
  - L240 **关键计算**：`dc = ts * speed` = 该居民在 `ts` 时刻已步行的距离。
  - L241-242 若已走完路径长度 → 在上车点等待。
  - L243-245 **沿路径插值**：用 shapely 的 `interpolate` 取路径上 `dc/d` 比例处的坐标。
  - L246-247 边界情况（路径长度为 0）→ 在上车点。
  - L248 累加 `风险值 × 人口`。
- L249 该分钟的总风险 × 60 秒（把每分钟值积分为剂量）。
- L251-253 **关键短路**：如果不启用 sink 边界，直接返回 (walk_time, walk_risk)。这是退化为原始模型的路径。

**修改建议**：
- 这个双重循环是性能瓶颈。若想加速，应使用 `optimizer_accel.py` 中的 Numba JIT 版本（但 Numba 版不支持 sink）。
- 若需要更细的时间步（如 30 秒而非 60 秒），把 L233 改为 `range(0, max_time, 30)`，并相应调整 L249 的 60 为 30。

**性能数据**：N=200 居民、45 分钟步行时，这个双重循环约执行 9000 次 `_risk` 调用，单次 evaluate 耗时约 50-100ms。

---

## 3.10 evaluate() — Phase 3 sink 阶段 (Lines 255–277)

```python
        # ── Phase 3: 上车点 sink 阶段 ──
        arrival_times = np.array(times, dtype=np.float64)

        try:
            T_total, R_total, _ = sink_model.process(
                assignment=list(ind),
                arrival_times=arrival_times,
                pop_arr=pop_np,
                walk_risk=walk_risk,
            )
        except Exception:
            return (np.inf, np.inf)

        # T_total 是全员撤离总时长 (秒); 转为人口加权形式与原口径一致
        sink_extra_time = max(0.0, T_total - max(times)) * float(pop_np.sum())
        total_time_obj = walk_time_weighted + sink_extra_time

        return total_time_obj, R_total

    return evaluate
```

**块功能**：调用 sink 模型计算排队/装载阶段的时间和风险，与 Phase 1/2 的结果合成。

**关键行解析**：
- L257 把 Python 列表转为 NumPy 数组（`PickupSinkModel.process` 期望数组输入）。
- L259-265 调用 sink 模型，传入 5 个参数。
- L266-267 **异常保护**：sink 模型内部如果出现意外异常（如除零），返回不可行而非崩溃整个进化过程。
- L272 **关键合成公式**：`sink_extra_time = max(0, T_total - max(times)) * total_pop`。
  - `max(times)` 是最晚到达上车点的居民的步行时间，即 sink 阶段开始之前的时刻。
  - `T_total - max(times)` 是 sink 阶段净时长（如果为负，说明 sink 阶段在所有人到达前就结束了，取 0）。
  - 乘以 `total_pop` 把净时长转为人口加权形式，与 `walk_time_weighted` 量纲一致。
- L273 总时间目标 = walk 部分 + sink 部分。
- L275 返回 `(总时间, 总风险)`，注意 R_total 已经在 sink 模型中被加上了 walk_risk。

**修改建议**：
- 这里的 `sink_extra_time` 公式是一种**简化的近似**。更精确的做法是分别记录每个居民的实际到达避难所时刻，再计算人口加权和。但这需要 sink 模型返回更多信息。
- 若想让 sink 风险与 walk 风险有不同权重，改 L275 为 `(total_time_obj, walk_risk * 0.7 + sink_risk * 1.3)`（自定义权重）。

**常见陷阱**：L266 的裸 `except Exception` 会吞掉所有异常包括逻辑错误。调试时建议改为 `except Exception as e: print(e); return (np.inf, np.inf)`。

---

## 3.11 run_qnsga2() — 主循环初始化 (Lines 283–331)

```python
def run_qnsga2(toolbox, evaluate, feasible,
               mu=None, ngen=None, lamb=None, logger=None):
    mu   = mu   or NSGA2_CONFIG["mu"]
    lamb = lamb or NSGA2_CONFIG["lambda_"]
    ngen = ngen or NSGA2_CONFIG["ngen"]
    qc   = QNSGA2_CONFIG

    toolbox.register("evaluate", evaluate)
    gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])

    header = (f"Q-NSGA-II: mu={mu} ngen={ngen} obs={qc['n_observations']} "
              f"classical={qc['classical_ratio']}")
    print(f"\n🔬 {header}")
    if logger:
        logger.log(header)

    # ---- Step 1: 量子种群初始化 ----
    qpop = [QuantumIndividual(feasible) for _ in range(mu)]

    # ---- Step 2: 初始观测 & 评估 ----
    pop = []
    for qi in qpop:
        for _ in range(qc["n_observations"]):
            sol = qi.observe()
            ind = creator.Individual(sol)
            ind.fitness.values = evaluate(ind)
            pop.append(ind)
    pop = tools.selNSGA2(pop, mu)

    feas = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
    print(f"   Init: {feas}/{mu} feasible")
    if feas == 0:
        raise ValueError("No feasible solutions in initial population!")

    logs = []
```

**块功能**：参数解析、注册评估函数、初始化量子种群、生成初始经典种群。

**关键行解析**：
- L285-287 默认参数解析（None → 配置中的默认值）。`or` 运算符的短路特性。
- L288 `qc` 是 `QNSGA2_CONFIG` 的别名，避免反复写 `QNSGA2_CONFIG`。
- L290 把 evaluate 注册到 toolbox（虽然此处不通过 toolbox 调用，但 NSGA 内部某些机制需要）。
- L291 创建旋转门对象。
- L293-296 打印 / 记录配置信息。
- L314 **Step 1**：创建 `mu` 个量子个体（默认 mu=400）。每个量子个体角度初始化为 π/4（均匀概率）。
- L316-323 **Step 2**：每个量子个体观测 `n_observations` 次（默认 3 次），每次产生一个经典解，立即评估。这意味着初始经典种群大小是 `mu × n_observations = 1200`。
- L324 用 NSGA-II 选择算子从 1200 个个体中选出最好的 `mu` 个，构成初始经典种群。
- L326-329 检查初始可行解数；若全部不可行，终止程序。这通常意味着可行域配置或路径预计算有问题。
- L331 进化日志列表。

**修改建议**：
- **观测次数**：`n_observations=3` 是一个折中值。增大可提高初始多样性但拖慢初始化。
- **避免完全失败**：L329 抛出异常会中断整个批量优化。如果想让其他分组继续运行，改为 `print("Warning"); return [], [], []`。

---

## 3.12 run_qnsga2() — 进化主循环 (Lines 333–425)

```python
    for gen in range(ngen):
        # ---- Step 3: Pareto 前沿 ----
        fp = [x for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        pf = tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

        # ---- Step 4: 量子旋转门 ----
        dt = gate.delta(gen, ngen)
        if pf:
            for qi in qpop:
                cs = qi.observe_greedy()
                ci = creator.Individual(cs)
                ci.fitness.values = evaluate(ci)
                gi = random.choice(pf)
                gate.rotate(qi, cs, list(gi),
                            ci.fitness.values, gi.fitness.values, dt, feasible)

        # ---- Step 5: 量子交叉 + 变异 ----
        random.shuffle(qpop)
        nq = []
        for k in range(0, len(qpop) - 1, 2):
            if random.random() < NSGA2_CONFIG["cxpb"]:
                c1, c2 = quantum_crossover(qpop[k], qpop[k + 1], qc["q_crossover_rate"])
                nq.extend([c1, c2])
            else:
                nq.extend([qpop[k].copy(), qpop[k + 1].copy()])
        if len(qpop) % 2 == 1:
            nq.append(qpop[-1].copy())
        for qi in nq:
            if random.random() < NSGA2_CONFIG["mutpb"]:
                qi.theta = quantum_mutation(
                    qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]
                ).theta
        qpop = nq[:mu]

        # ---- Step 6: 量子灾变 ----
        ci_ = qc["catastrophe_interval"]
        if ci_ > 0 and (gen + 1) % ci_ == 0:
            qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])

        # ---- Step 7: 量子观测子代 ----
        q_off = []
        for qi in qpop:
            for _ in range(qc["n_observations"]):
                ind = creator.Individual(qi.observe())
                try:
                    ind.fitness.values = evaluate(ind)
                except Exception:
                    ind.fitness.values = (np.inf, np.inf)
                q_off.append(ind)

        # ---- Step 8: 经典 GA 子代 ----
        nc = int(lamb * qc["classical_ratio"])
        c_off = algorithms.varOr(
            pop, toolbox, lambda_=nc,
            cxpb=NSGA2_CONFIG["cxpb"], mutpb=NSGA2_CONFIG["mutpb"])
        for ind in c_off:
            try:
                ind.fitness.values = evaluate(ind)
            except Exception:
                ind.fitness.values = (np.inf, np.inf)

        # ---- Step 9: 环境选择 ----
        pop = toolbox.select(pop + q_off + c_off, mu)
```

**块功能**：每代执行 7 个步骤的进化循环。这是 Q-NSGA-II 的核心。

**逐步骤解析**：

**Step 3 (L334-337) — Pareto 前沿提取**：
- L335-336 过滤掉不可行个体（适应度含 inf）。
- L337 用 `sortNondominated(..., first_front_only=True)` 提取第一前沿。如果种群全不可行则前沿为空列表。

**Step 4 (L339-348) — 量子旋转门**：
- L340 计算当前代的旋转角度（线性退火）。
- L341 仅在有非空前沿时进行旋转（早期可能没有可行解）。
- L342-348 对每个量子个体：
  - L343 用 greedy 观测产生当前代表解。
  - L344-345 评估当前代表解（这是额外的评估开销）。
  - L346 从前沿中随机选一个引导解。
  - L347-348 调用旋转门，朝引导解方向调整角度。

**Step 5 (L350-366) — 量子交叉与变异**：
- L351 打乱量子种群顺序。
- L352-360 两两配对交叉（最后落单的个体直接复制）。
- L361-365 每个新个体以 `mutpb=0.2` 概率变异。
- L366 截断为前 `mu` 个，保持种群规模。

**Step 6 (L368-371) — 量子灾变**：
- L369 默认每 50 代灾变一次（`catastrophe_interval=50`）。
- L370-371 触发条件满足时，10% 的量子个体被随机重置。

**Step 7 (L373-382) — 量子观测子代**：
- L375 对每个（变异/交叉后的）量子个体观测 `n_observations` 次。
- L378-381 评估每个观测解；异常时设为不可行。
- 总共产生 `mu × n_observations = 1200` 个量子子代。

**Step 8 (L384-393) — 经典 GA 子代**：
- L385 经典子代数 = `lamb × classical_ratio = 400 × 0.3 = 120`。
- L386-388 用 DEAP 的 `varOr`（变异或交叉，二选一）从当前种群产生 120 个经典子代。
- L389-393 评估每个经典子代。

**Step 9 (L395-396) — 环境选择**：
- L396 把当前种群（mu=400）+ 量子子代（1200）+ 经典子代（120）合并，用 NSGA-II 选出最好的 `mu` 个。这是 (μ + λ) 选择。

**修改建议**：
- **加快收敛**：减小 `n_observations`（如改为 1）或减小 `classical_ratio`（如改为 0.2）。
- **加强多样性**：增大 `q_mutation_rate` 或缩短 `catastrophe_interval`（如 30）。
- **调试时**：在每个步骤后插入 `print(f"Step X done, len={len(...)}")` 观察各阶段种群大小。

**性能瓶颈**：每代的总评估次数 ≈ `mu` (旋转门) + `mu × n_obs` (量子子代) + `nc` (经典子代) = 400 + 1200 + 120 = **1720 次评估**。每次评估约 100ms，每代约 170 秒。160 代约 7 小时。如果太慢应启用 `--accel` 模式（但与 sink 边界不兼容）。

---

## 3.13 run_qnsga2() — 日志与收尾 (Lines 398–425)

```python
        # ---- 日志 ----
        vf = [(x.fitness.values[0], x.fitness.values[1])
              for x in pop
              if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        inf_c = mu - len(vf)
        if vf:
            mt, mr = min(f[0] for f in vf), min(f[1] for f in vf)
            fp2 = [x for x in pop if np.isfinite(x.fitness.values[0])]
            ps = len(tools.sortNondominated(fp2, len(fp2),
                                            first_front_only=True)[0]) if fp2 else 0
        else:
            mt, mr, ps = float("inf"), float("inf"), 0

        line = (f"Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e} "
                f"| PF {ps} | Inf {inf_c} | Δθ {dt/math.pi:.4f}π")
        logs.append(line)
        if gen % 20 == 0 or gen == ngen - 1:
            print(f"   {line}")
        if logger:
            logger.log(line)

    # ---- 最终 Pareto 前沿 ----
    ff = [x for x in pop
          if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
    final_pf = (tools.sortNondominated(ff, len(ff), first_front_only=True)[0]
                if ff else [])
    print(f"✅ Final Pareto front: {len(final_pf)} solutions")
    return pop, final_pf, logs
```

**块功能**：每代日志记录、最终 Pareto 前沿提取、返回结果。

**关键行解析**：
- L399-401 提取所有可行解的适应度元组。
- L402 不可行解数量 = mu - 可行数。
- L403-407 若有可行解：
  - 计算时间和风险目标的最小值（方便监控收敛）。
  - 提取当前 Pareto 前沿大小。
- L411-412 日志行格式：代号、最小时间、最小风险、前沿大小、不可行数、当前旋转角度。
- L414-415 每 20 代打印一次（或最后一代），避免输出过多。
- L416-417 写入持久化日志文件。
- L420-423 主循环结束后提取最终 Pareto 前沿。
- L425 返回三个对象：最终种群、最终前沿、日志列表。

---

## 3.14 select_solution() 解选择 (Lines 431–458)

```python
def select_solution(pf, method="min_risk"):
    feas = [x for x in pf
            if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
    if not feas:
        raise ValueError("Empty Pareto front!")

    if method == "min_risk":
        best = feas[int(np.argmin([x.fitness.values[1] for x in feas]))]
    elif method == "min_time":
        best = feas[int(np.argmin([x.fitness.values[0] for x in feas]))]
    elif method == "knee":
        if len(feas) < 3:
            return select_solution(feas, "min_risk")
        sf = sorted(feas, key=lambda x: x.fitness.values[0])
        ts = [x.fitness.values[0] for x in sf]
        rs = [x.fitness.values[1] for x in sf]
        tr = max(ts) - min(ts) or 1
        rr = max(rs) - min(rs) or 1
        nt = [(t - min(ts)) / tr for t in ts]
        nr = [(r - min(rs)) / rr for r in rs]
        best = sf[int(np.argmax(
            [abs(nt[i] + nr[i] - 1) / np.sqrt(2) for i in range(len(sf))]))]
    else:
        raise ValueError(f"Unknown method: {method}")

    print(f"🎯 Selected ({method}): T={best.fitness.values[0]:.2f}  "
          f"R={best.fitness.values[1]:.2e}")
    return best, method
```

**块功能**：从 Pareto 前沿中选出一个「最优」解。三种策略：min_risk、min_time、knee。

**关键行解析**：
- L432-434 过滤可行解；空前沿抛错。
- L437-438 **min_risk**：选风险最小的解（前沿的右下端）。
- L439-440 **min_time**：选时间最小的解（前沿的左上端）。
- L441-452 **knee 拐点**：
  - L442-443 解少于 3 个时退化为 min_risk。
  - L444 按时间升序排序。
  - L445-446 提取时间和风险数组。
  - L447-448 计算时间和风险的极差（避免除 0）。
  - L449-450 归一化到 [0, 1]。
  - L451-452 **核心公式**：`|nt + nr - 1| / sqrt(2)` 是点到直线 `x + y = 1` 的距离。前沿越「弯」的点距离越大。取最远点作为 knee。
- L456-458 打印选择结果并返回。

**修改建议**：
- 若你的目标量纲差异大（如时间秒/风险 1e-7），归一化是必要的。
- 若想用其他 knee 检测算法（如 Kneedle），可以集成 `kneed` 库。

---

## 3.15 compute_metrics() 真实指标计算 (Lines 464–507)

```python
def compute_metrics(assignment, res_df, bus_xy, active_idx, paths,
                    risk_arrays, x_mins, y_maxs, res_pop, snapped_res,
                    speed=WALK_SPEED, max_time=DEFAULT_MAX_TIME):
    """沿道路网络路径逐分钟仿真，计算真实时间和风险"""
    g2l = {i: il for il, i in enumerate(active_idx)}
    arrival = {}
    for i in range(len(res_df)):
        j = assignment[i]
        if j == -1:
            arrival[i] = np.inf
        elif i in g2l:
            pl = paths.get((g2l[i], j))
            arrival[i] = pl.length / speed if pl else np.inf
        else:
            arrival[i] = 0.0

    total_time = sum(t for t in arrival.values() if np.isfinite(t))
    total_risk = 0.0
    reached = np.zeros(len(res_df), dtype=bool)

    for minute in range(int(max_time / 60) + 1):
        ts = minute * 60
        si = 0 if minute <= 15 else (1 if minute <= 25 else (2 if minute <= 35 else 3))
        ra, xm, ym = risk_arrays[si], x_mins[si], y_maxs[si]
        for i in range(len(res_df)):
            j = assignment[i]
            if j == -1 or not np.isfinite(arrival.get(i, np.inf)):
                continue
            if reached[i]:
                x, y = bus_xy[j]
            elif ts >= arrival[i]:
                x, y = bus_xy[j]; reached[i] = True
            elif i in g2l:
                pl = paths.get((g2l[i], j))
                if pl and pl.length > 0:
                    pt = pl.interpolate(min(ts * speed / pl.length, 1.0), normalized=True)
                    x, y = pt.x, pt.y
                else:
                    x, y = bus_xy[j]
            else:
                x, y = bus_xy[j]
            total_risk += _risk(x, y, ra, xm, ym) * res_pop[i] * 60

    return total_time, total_risk, arrival
```

**块功能**：在选定最优解之后，运行一次完整仿真计算真实指标（用于报告、可视化）。这与 evaluate 中的 Phase 1+2 类似但更完整（处理被排除的居民）。

**关键行解析**：
- L468 `g2l` = global-to-local 映射，把全局居民索引映射到优化时的局部索引（active_idx 中可能有缺失）。
- L469-478 计算每个居民的到达时刻：
  - L472-473 未分配的居民 → 到达时刻 inf。
  - L474-476 在 active 列表中的居民 → 步行时间。
  - L477-478 不在 active 中的居民 → 时刻 0（已经在安全位置）。
- L480 总时间 = 所有有限到达时刻之和（注意未加权）。
- L482 `reached` 数组跟踪每个居民是否已到达。
- L484-505 主仿真循环（逐分钟）：
  - L490-491 跳过未分配/不可达居民。
  - L492-493 已到达的居民停留在上车点。
  - L494-495 该分钟到达的居民——更新 reached 标志。
  - L496-501 在路径上的居民——沿路径插值。
  - L502-504 边界情况。
  - L505 累加风险。
- L507 返回真实总时间、真实总风险、到达时刻字典。

**与 evaluate 的差异**：
- evaluate 只处理 active_idx 中的居民；compute_metrics 处理全部 res_df 中的居民。
- evaluate 中的不可行居民会导致 (inf, inf)；compute_metrics 中跳过不可行居民继续算其他人。
- evaluate 是 Q-NSGA-II 进化中的快速估算；compute_metrics 是最终选解后的详细报告。

**修改建议**：
- 如果想在 compute_metrics 中也加入 sink 边界，调用 `PickupSinkModel.process` 即可（与 evaluate 相同的逻辑）。

---

# 第四部分：main.py

## 4.1 模块文档与导入 (Lines 1–39)

```python
"""
Q-NSGA-II 疏散优化系统 — 主入口
一键运行：python main.py --test --serial
... (省略详细文档) ...
"""
import os
import sys
import traceback
import numpy as np
from datetime import datetime
from multiprocessing import Pool, cpu_count

from config import (...)
from data_loader import (...)
from optimizer import (...)
from visualization import (...)
from export import (...)
```

**块功能**：声明主入口模块，导入所有依赖。

**关键说明**：
- L19 `multiprocessing.Pool`：用于分组级并行（每个分组一个进程）。
- L21-39 5 块导入：配置、数据加载、优化器、可视化、导出。注意没有直接导入 `pickup_sink`——sink 模型在 `optimizer.make_evaluate` 内部按需创建。

---

## 4.2 optimize_group() — 函数签名 (Lines 45–73)

```python
def optimize_group(config, selection_method="min_risk", accelerate=False,
                   use_gpu=False, n_eval_threads=None, use_sink=True):
    """
    对一个年龄-性别分组执行完整的 Q-NSGA-II 优化流程。
    ... (省略文档) ...
    """
    name  = config["group_name"]
    speed = config["speed"]
    out   = config["output_dir"]
    max_t = config.get("max_walk_time_minutes", 45) * 60
    os.makedirs(out, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🔬 {name}  speed={speed} m/s  selection={selection_method}")
    print(f"{'='*60}")

    # ---------- 日志 ----------
    logger = EvacLogger(name, out)
    logger.log(f"Group: {name}  Speed: {speed}  Selection: {selection_method}")
```

**块功能**：单组优化的入口函数。读取配置、创建输出目录、初始化日志。

**关键行解析**：
- L45-46 6 个参数：配置字典、选解策略、加速开关、GPU 开关、评估线程数、sink 开关。
- L61-64 从配置字典中提取常用参数。
- L64 `max_t = max_walk_time_minutes × 60`，默认 45 分钟。
- L65 创建输出目录（`exist_ok=True` 避免目录已存在时报错）。
- L72 创建日志器，会自动在 `out` 目录下创建 `.log` 和 `.csv` 文件。

---

## 4.3 optimize_group() — Step 1-4 数据准备 (Lines 75–123)

```python
    # ---------- Step 1: 加载数据 ----------
    G, nids, ncoords, kd = load_road_network(
        ROAD_NETWORK_SHP, center=CENTER_UTM, clip_radius=ROAD_CLIP_RADIUS)
    res_df   = load_resident_data(config["pop_file"])
    bus_xy, bus_gdf = load_bus_stops(config["bus_file"])
    risk_arrays, x_mins, y_maxs = load_all_risk_data(RISK_VALUE_FILES)

    active_idx = list(range(len(res_df)))
    bus_list   = list(range(len(bus_xy)))

    logger.log(f"Residents: {len(res_df)}  Bus stops: {len(bus_xy)}")

    # ---------- Step 2: 预计算路径 ----------
    road_paths, snapped_res, snapped_bus, validity = precompute_paths(
        res_df, bus_xy, G, active_idx, bus_list, kd, nids, ncoords)

    # ---------- Step 3: 可行域 ----------
    feasible, no_opt = build_feasible(
        road_paths, len(active_idx), bus_list, max_t, speed)

    # ---------- Step 4: 过滤无选项居民 ----------
    valid_ai, valid_f, valid_sr = [], [], []
    for il, i in enumerate(active_idx):
        if feasible[il]:
            valid_ai.append(i)
            valid_f.append(feasible[il])
            valid_sr.append(snapped_res[il])
    excl = len(active_idx) - len(valid_ai)
    if excl:
        print(f"   ❌ Excluded {excl} residents (no reachable stop)")
        logger.log(f"Excluded: {excl}")

    # 重映射路径索引
    o2n = {}
    for ni, i in enumerate(valid_ai):
        o2n[active_idx.index(i)] = ni
    new_paths = {(o2n[ol], j): pl
                 for (ol, j), pl in road_paths.items() if ol in o2n}

    active_idx   = valid_ai
    feasible     = valid_f
    snapped_res  = np.array(valid_sr)
    road_paths   = new_paths

    if not active_idx:
        raise ValueError("No residents with feasible stops!")

    res_pop = res_df["pop"].values[active_idx]
    logger.log(f"Active residents: {len(active_idx)}")
```

**块功能**：4 步数据准备 — 加载数据 → 预计算路径 → 构建可行域 → 过滤无可行解的居民。

**逐步骤解析**：

**Step 1 (L76-85)**：
- L76-77 加载并裁剪路网。
- L78 加载该组居民。
- L79 加载上车点。
- L80 加载 4 个阶段的风险矩阵。
- L82-83 初始的 active/bus 索引——所有居民和所有上车点。后续会被过滤。

**Step 2 (L88-89)**：调用 `precompute_paths` 一次性计算所有 `(居民, 上车点)` 对的路径。

**Step 3 (L92-93)**：基于路径长度和速度限制构建可行域（每个居民的可达上车点列表）。

**Step 4 (L96-123)**：过滤无可行选项的居民。这是关键步骤：
- L96-101 过滤逻辑：保留 `feasible[il]` 非空的居民。
- L102-105 打印被排除的居民数。
- L108-112 **重映射路径索引**：因为部分居民被排除，剩余居民的局部索引发生了变化。例如原来的 `active_idx[5]` 可能被排除，那么后续的 `active_idx[6]` 现在变成局部索引 5。
  - L108-110 构造旧→新映射 `o2n`。
  - L111-112 重建 `road_paths` 字典，键中的居民索引用新索引。
- L114-117 用新数据替换旧数据。
- L119-120 全部被过滤 → 抛错。
- L122 提取剩余居民的人口数组。

**修改建议**：
- 如果不想丢弃无可行解的居民（比如为了完整报告），可改为：把它们的分配标记为 -1，但仍保留在 res_df 中。
- L110 `active_idx.index(i)` 是 O(N) 查找，对大数据可能慢。改为先建立反向索引：`old_pos = {i: pos for pos, i in enumerate(active_idx)}; o2n[old_pos[i]] = ni`。

---

## 4.4 optimize_group() — Step 5-6 优化器配置 (Lines 125–150)

```python
    # ---------- Step 5: 创建 Pareto 可视化器 ----------
    pv = ParetoVisualizer(out, name)

    # ---------- Step 6: 运行 Q-NSGA-II ----------
    tb = setup_deap(feasible)

    if accelerate:
        from optimizer_accel import make_evaluate_accel, run_qnsga2_accel
        print(f"\n   🚀 ACCELERATED MODE (GPU={use_gpu}, threads={n_eval_threads or 'auto'})")
        if use_sink:
            print(f"   ⚠️  Sink boundary not supported in accel mode, falling back to standard evaluate")
            ev = make_evaluate(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_sink=True)
        else:
            ev = make_evaluate_accel(
                snapped_res[:, 0], snapped_res[:, 1], res_pop,
                snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
                use_gpu=use_gpu)
    else:
        print(f"   🚏 Sink boundary: {'ON (bus dispatch + queueing)' if use_sink else 'OFF (baseline)'}")
        ev = make_evaluate(
            snapped_res[:, 0], snapped_res[:, 1], res_pop,
            snapped_bus, road_paths, risk_arrays, x_mins, y_maxs, speed, max_t,
            use_sink=use_sink)
```

**块功能**：创建 Pareto 可视化器和 evaluate 函数。根据 accel 和 sink 开关选择不同的实现。

**逐行解析**：
- L126 创建 Pareto 可视化器，会在 `out/pareto_evolution/` 下保存每代的 Pareto 前沿图。
- L129 创建 DEAP toolbox。
- L131-150 三种模式的 evaluate 创建：
  - **L131-144 加速模式**：导入 `optimizer_accel`，但要检查是否启用 sink。
    - L134-139 **关键退化**：如果同时启用了 sink 和 accel，sink 优先（因为 accel 模式不支持 sink）。打印警告并回退到标准 `make_evaluate`。
    - L140-144 如果不启用 sink，使用 `make_evaluate_accel`（Numba JIT 版本，不含 sink 边界）。
  - **L145-150 标准模式**：使用 `make_evaluate`，根据 `use_sink` 开关决定是否启用 sink。

**修改建议**：
- 若希望加速模式也支持 sink（性能优先），需要 Numba 化 `pickup_sink._simulate_queue_at_stop`，工程量较大。
- 若想做 A/B 对比，可在同一次运行中创建两个 ev：`ev_with_sink` 和 `ev_without_sink`，分别跑两次然后比较结果。

---

## 4.5 optimize_group() — 优化主循环（accel 分支）(Lines 153–160)

```python
    # 优化循环：加速模式使用 run_qnsga2_accel，标准模式内联循环（含可视化记录）
    if accelerate:
        _, final_pf, logs = run_qnsga2_accel(
            tb, ev, feasible, logger=logger, n_eval_threads=n_eval_threads)

        # 为 Pareto 可视化补充最终记录
        if final_pf:
            pv.record(NSGA2_CONFIG["ngen"], final_pf)
            pv.plot_current(NSGA2_CONFIG["ngen"], final_pf)
```

**块功能**：加速模式的优化主循环——直接调用 `run_qnsga2_accel`。

**关键行解析**：
- L154-155 `run_qnsga2_accel` 返回 `(pop, pareto_front, logs)`，第一个返回值不需要所以用 `_`。
- L158-160 加速模式的 `run_qnsga2_accel` 内部不记录每代 Pareto，所以这里在最后补充一次记录和绘图。代价是损失了进化过程的可视化。

---

## 4.6 optimize_group() — 优化主循环（标准分支，内联）(Lines 161–269)

这是 main.py 中最长的一段——把 `run_qnsga2` 的主循环展开内联，目的是在每代结束时调用 `pv.record()` 记录 Pareto 前沿。

```python
    else:
        # 标准模式：带逐代可视化记录的内联循环
        from deap import tools, creator
        from optimizer import (
            QuantumIndividual, QuantumRotationGate,
            quantum_crossover, quantum_mutation, quantum_catastrophe,
            QNSGA2_CONFIG,
        )
        import random, math
        from deap import algorithms as alg

        mu   = NSGA2_CONFIG["mu"]
        lamb = NSGA2_CONFIG["lambda_"]
        ngen = NSGA2_CONFIG["ngen"]
        qc   = QNSGA2_CONFIG
        tb.register("evaluate", ev)
        gate = QuantumRotationGate(qc["delta_theta_max"], qc["delta_theta_min"])
```

**块功能（L162-177）**：函数级导入（避免循环依赖），参数解析。

```python
        qpop = [QuantumIndividual(feasible) for _ in range(mu)]
        pop = []
        for qi in qpop:
            for _ in range(qc["n_observations"]):
                ind = creator.Individual(qi.observe())
                ind.fitness.values = ev(ind)
                pop.append(ind)
        pop = tools.selNSGA2(pop, mu)
```

**块功能（L179-186）**：初始量子种群和经典种群（与 `run_qnsga2` 中相同）。

```python
        feas = sum(1 for x in pop if np.isfinite(x.fitness.values[0]))
        print(f"   Init: {feas}/{mu} feasible")
        if feas == 0:
            raise ValueError("No feasible solutions!")

        logs = []
        for gen in range(ngen):
            fp = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            pf = tools.sortNondominated(fp, len(fp), first_front_only=True)[0] if fp else []

            dt = gate.delta(gen, ngen)
            if pf:
                for qi in qpop:
                    cs = qi.observe_greedy()
                    ci = creator.Individual(cs); ci.fitness.values = ev(ci)
                    gi = random.choice(pf)
                    gate.rotate(qi, cs, list(gi), ci.fitness.values, gi.fitness.values, dt, feasible)
```

**块功能（L188-204）**：可行性检查 + 进化主循环开头（Pareto 前沿提取 + 量子旋转门）。这与 `run_qnsga2` 中的 Step 3-4 完全相同。

```python
            random.shuffle(qpop)
            nq = []
            for k in range(0, len(qpop) - 1, 2):
                if random.random() < NSGA2_CONFIG["cxpb"]:
                    c1, c2 = quantum_crossover(qpop[k], qpop[k+1], qc["q_crossover_rate"])
                    nq.extend([c1, c2])
                else:
                    nq.extend([qpop[k].copy(), qpop[k+1].copy()])
            if len(qpop) % 2 == 1:
                nq.append(qpop[-1].copy())
            for qi in nq:
                if random.random() < NSGA2_CONFIG["mutpb"]:
                    qi.theta = quantum_mutation(qi, qc["q_mutation_rate"], qc["q_mutation_perturbation"]).theta
            qpop = nq[:mu]

            ci_ = qc["catastrophe_interval"]
            if ci_ > 0 and (gen+1) % ci_ == 0:
                qpop = quantum_catastrophe(qpop, qc["catastrophe_rate"])
```

**块功能（L206-223）**：Step 5 量子交叉变异 + Step 6 灾变。与 `run_qnsga2` 中相同。

```python
            q_off = []
            for qi in qpop:
                for _ in range(qc["n_observations"]):
                    ind = creator.Individual(qi.observe())
                    try: ind.fitness.values = ev(ind)
                    except: ind.fitness.values = (np.inf, np.inf)
                    q_off.append(ind)

            nc = int(lamb * qc["classical_ratio"])
            c_off = alg.varOr(pop, tb, lambda_=nc, cxpb=NSGA2_CONFIG["cxpb"], mutpb=NSGA2_CONFIG["mutpb"])
            for ind in c_off:
                try: ind.fitness.values = ev(ind)
                except: ind.fitness.values = (np.inf, np.inf)

            pop = tb.select(pop + q_off + c_off, mu)
```

**块功能（L225-239）**：Step 7-9 量子子代 + 经典子代 + 环境选择。

```python
            vf = [(x.fitness.values[0], x.fitness.values[1]) for x in pop
                  if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            inf_c = mu - len(vf)
            if vf:
                mt, mr = min(f[0] for f in vf), min(f[1] for f in vf)
                fp2 = [x for x in pop if np.isfinite(x.fitness.values[0])]
                ps = len(tools.sortNondominated(fp2, len(fp2), first_front_only=True)[0]) if fp2 else 0
            else:
                mt, mr, ps = float("inf"), float("inf"), 0

            line = f"Gen {gen+1:03d} | MinT {mt:.2f} | MinR {mr:.2e} | PF {ps} | Inf {inf_c}"
            logs.append(line)
            logger.log(line)
            logger.log_gen(gen+1, mt, mr, ps, inf_c, dt)

            if gen % 20 == 0 or gen == ngen - 1:
                print(f"   {line}")
```

**块功能（L241-257）**：日志记录 + 周期性打印。`log_gen` 把每代数据写入 CSV。

```python
            feasible_combined = [x for x in pop + q_off + c_off
                                 if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
            if feasible_combined:
                cur_pf = tools.sortNondominated(feasible_combined, len(feasible_combined), first_front_only=True)[0]
                pv.record(gen+1, cur_pf, feasible_combined)
                if (gen+1) % PARETO_VIZ["save_interval"] == 0 or gen == ngen - 1:
                    pv.plot_current(gen+1, cur_pf, feasible_combined)

        ff = [x for x in pop if np.isfinite(x.fitness.values[0]) and np.isfinite(x.fitness.values[1])]
        final_pf = tools.sortNondominated(ff, len(ff), first_front_only=True)[0] if ff else []
        print(f"✅ Final Pareto: {len(final_pf)} solutions")
```

**块功能（L259-269）**：**关键的 Pareto 可视化记录**：
- L259-262 合并当前种群和子代，提取 Pareto 前沿。
- L263 调用 `pv.record` 把当前代的前沿存入历史。
- L264-265 每 `save_interval`（默认 20）代或最后一代，调用 `plot_current` 绘制并保存当前前沿。
- L267-269 主循环结束后提取最终前沿。

**为什么内联**：原因是 `run_qnsga2` 不知道 `pv` 对象，无法在每代结束时调用 `pv.record`。一种更优雅的方案是给 `run_qnsga2` 增加 `callback` 参数。

**修改建议**：
- 重构为 callback 模式，避免内联代码与 `optimizer.run_qnsga2` 不同步：
  ```python
  # optimizer.py:
  def run_qnsga2(..., on_generation=None):
      for gen in range(ngen):
          ...
          if on_generation:
              on_generation(gen+1, current_pf, feasible_combined)
  
  # main.py:
  _, final_pf, logs = run_qnsga2(tb, ev, feasible, logger=logger,
                                 on_generation=lambda g, pf, fc: (
                                     pv.record(g, pf, fc),
                                     pv.plot_current(g, pf, fc) if g % 20 == 0 else None,
                                 ))
  ```

---

## 4.7 optimize_group() — Step 7-10 后处理 (Lines 271–306)

```python
    # ---------- Step 7: 选择最优解 ----------
    best, method = select_solution(final_pf, selection_method)
    sub_assign = list(best)

    full_assign = [-1] * len(res_df)
    for idx, i in enumerate(active_idx):
        full_assign[i] = sub_assign[idx]

    # ---------- Step 8: 真实指标 ----------
    total_t, total_r, arrival = compute_metrics(
        full_assign, res_df, snapped_bus, active_idx, road_paths,
        risk_arrays, x_mins, y_maxs, res_df["pop"].values, snapped_res, speed, max_t)

    used = set(full_assign) - {-1}
    print(f"✅ {name}: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")
    logger.log(f"RESULT: time={total_t/60:.1f}min  risk={total_r:.2e}  stops={len(used)}")

    # ---------- Step 9: 导出 ----------
    export_pareto_csv(final_pf, os.path.join(out, "pareto_front.csv"))
    pv.plot_final(final_pf, best, method)
    pv.plot_summary()

    # ---------- Step 10: 可视化 ----------
    plot_assignment_map(risk_arrays[-1], snapped_bus, full_assign, res_df, active_idx, out)
    plot_evacuation_stages(risk_arrays, snapped_bus, full_assign, res_df,
                           active_idx, road_paths, snapped_res, snapped_bus, out, speed)

    logger.close()

    return dict(
        group_name=name, gender=config["gender"], age_group=config["age_group"],
        speed=speed, total_time=total_t, total_risk=total_r,
        assignment=full_assign, res_df=res_df, bus_xy=snapped_bus,
        active_indices=active_idx, used_stops=used, paths=road_paths,
        logs=logs, pareto_front=final_pf,
    )
```

**块功能**：从最优解中提取分配方案 → 计算真实指标 → 导出 → 可视化 → 返回结果字典。

**逐步骤解析**：

**Step 7 (L272-277) — 解选择与全局映射**：
- L272 调用 `select_solution` 选出最优解。
- L273 `sub_assign` 是局部分配（仅包含 active_idx 中的居民）。
- L275-277 **关键的局部→全局映射**：构造 `full_assign[len(res_df)]`，未在 active_idx 中的居民标记为 -1。

**Step 8 (L280-286) — 真实指标**：
- L280-282 调用 `compute_metrics` 运行完整仿真，得到真实总时间、真实总风险、每个居民的到达时刻。
- L284 统计被使用的上车点集合。
- L285-286 打印 / 记录最终结果。

**Step 9 (L289-291) — 导出**：
- L289 把 Pareto 前沿写入 CSV。
- L290 绘制最终 Pareto 图（标注选中解）。
- L291 绘制收敛图（每代最小时间和最小风险）。

**Step 10 (L294-298) — 可视化**：
- L294 绘制分配地图（风险底图 + 上车点 + 路径）。
- L295-296 绘制 4 个时间断面的疏散阶段图。
- L298 关闭日志器（flush 文件）。

**返回值 (L300-306)**：包含 12 个字段的字典，供批量运行的 main 函数汇总到 Excel。

**修改建议**：
- 若想加入 sink 仿真的真实指标（不是 evaluate 的估算值），在 Step 8 中也调用 `PickupSinkModel.process` 一次。
- 若想批量保存多个候选解（不只 best），改 L272-273 为循环遍历前沿的 top K 解。

---

## 4.8 _worker() 多进程工作函数 (Lines 312–321)

```python
def _worker(args):
    config, sel, accel, gpu, threads, sink = args
    try:
        return optimize_group(config, sel, accelerate=accel,
                              use_gpu=gpu, n_eval_threads=threads,
                              use_sink=sink)
    except Exception as e:
        print(f"❌ {config['group_name']}: {e}")
        traceback.print_exc()
        return None
```

**块功能**：多进程工作函数。`Pool.map` 要求工作函数只接受一个参数，所以这里把所有参数打包为元组。

**关键行解析**：
- L313 解包元组为 6 个变量。
- L314-317 调用 `optimize_group`。
- L318-320 异常保护：单个组失败不影响其他组，打印错误后返回 None。
- L321 失败时返回 None，主函数会过滤掉。

**常见陷阱**：`multiprocessing` 在 Windows 上要求所有传递给 `Pool.map` 的对象都能 pickle。`config` 字典只包含基础类型所以没问题，但若你想传递 `road_paths` 字典（含 LineString 对象）会失败，因为 LineString 的 pickle 较脆弱。

---

## 4.9 main() 批量入口 (Lines 324–384)

```python
def main(selected_groups=None, parallel=True, n_workers=None,
         selection_method="min_risk", accelerate=False,
         use_gpu=False, n_eval_threads=None, use_sink=True):
    """
    批量运行全部（或指定）分组的 Q-NSGA-II 优化。
    ... (省略文档) ...
    """
    t0 = datetime.now()
    print(f"\n{'='*60}")
    print(f"🔬 Q-NSGA-II Evacuation Optimizer")
    print(f"   Time     : {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Selection: {selection_method}")
    print(f"   Sink BC  : {'ON' if use_sink else 'OFF (baseline)'}")
    print(f"{'='*60}")

    configs = build_group_configs()
    if selected_groups:
        configs = [c for c in configs if c["group_name"] in selected_groups]
    if not configs:
        print("❌ No groups found!")
        return None

    print(f"   Groups: {len(configs)}")
    for c in configs:
        print(f"     - {c['group_name']} ({c['speed']} m/s)")

    if parallel and len(configs) > 1:
        nw = n_workers or min(max(1, cpu_count() - 1), len(configs))
        print(f"\n⚙️  Parallel: {nw} workers")
        with Pool(nw) as pool:
            raw = pool.map(_worker,
                           [(c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink)
                            for c in configs])
        results = [r for r in raw if r is not None]
    else:
        results = []
        for c in configs:
            r = _worker((c, selection_method, accelerate, use_gpu, n_eval_threads, use_sink))
            if r:
                results.append(r)

    if results:
        out_xlsx = os.path.join(
            OUTPUT_ROOT, f"qnsga2_results_{selection_method}.xlsx")
        export_results_excel(results, results[0]["bus_xy"], out_xlsx)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n{'='*60}")
    print(f"🎉 Done: {len(results)}/{len(configs)} groups  "
          f"({elapsed:.0f}s / {elapsed/60:.1f}min)")
    print(f"{'='*60}")
    return results
```

**块功能**：批量运行所有分组——并行或串行模式。

**关键行解析**：
- L340 起始时间戳，用于计算总耗时。
- L348 调用 `build_group_configs` 扫描所有可用分组。
- L349-350 按用户指定过滤分组。
- L351-353 无分组 → 退出。
- L355-357 打印分组列表。
- L359-366 **并行模式**：
  - L360 默认 worker 数 = `min(CPU核数-1, 分组数)`。**减一是为了留一个核给主进程**避免系统卡顿。
  - L362 `with Pool(nw)` 自动管理进程池生命周期。
  - L363-365 `pool.map` 把参数列表分发给工作进程。
  - L366 过滤掉失败的（None）。
- L367-372 **串行模式**：纯 Python 循环（用于调试，避免多进程难以追踪的错误）。
- L374-377 把所有结果写入一个 Excel 文件，供后续分析。
- L379-383 打印总耗时。

**修改建议**：
- 若想强制使用所有 CPU，改 L360 的 `cpu_count() - 1` 为 `cpu_count()`。
- 若并行模式总是失败（多进程序列化问题），临时改用 `concurrent.futures.ProcessPoolExecutor`，错误信息更清晰。

---

## 4.10 命令行入口 (Lines 390–428)

```python
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Q-NSGA-II Evacuation Optimization System")
    parser.add_argument("--groups", nargs="+",
                        help="Groups to process (e.g. m_20-29 f_30-39)")
    parser.add_argument("--test", action="store_true",
                        help="Test mode: only m_20-29")
    parser.add_argument("--serial", action="store_true",
                        help="Serial mode (no multiprocessing)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Number of parallel workers")
    parser.add_argument("--selection", default="min_risk",
                        choices=["min_risk", "min_time", "knee"],
                        help="Pareto solution selection method")
    parser.add_argument("--accel", action="store_true",
                        help="Enable accelerated engine (Numba JIT + thread pool)")
    parser.add_argument("--gpu", action="store_true",
                        help="Enable CuPy GPU risk computation (requires cupy)")
    parser.add_argument("--eval-threads", type=int, default=None,
                        help="Number of evaluate threads (default: auto)")
    parser.add_argument("--no-sink", action="store_true",
                        help="Disable pickup point sink boundary (baseline mode)")

    args = parser.parse_args()
    if args.test:
        args.groups = ["m_20-29"]

    main(
        selected_groups=args.groups,
        parallel=not args.serial,
        n_workers=args.workers,
        selection_method=args.selection,
        accelerate=args.accel,
        use_gpu=args.gpu,
        n_eval_threads=args.eval_threads,
        use_sink=not args.no_sink,
    )
```

**块功能**：命令行参数解析与 main 调用。

**关键参数说明**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--groups` | list[str] | None | 指定要处理的分组列表，如 `--groups m_20-29 f_30-39` |
| `--test` | flag | False | 测试模式，等价于 `--groups m_20-29` |
| `--serial` | flag | False | 串行模式，避免多进程（用于调试） |
| `--workers` | int | auto | 并行进程数 |
| `--selection` | str | min_risk | Pareto 解选择策略 |
| `--accel` | flag | False | 启用 Numba JIT 加速（**与 sink 不兼容**） |
| `--gpu` | flag | False | 启用 CuPy GPU（需要 accel） |
| `--eval-threads` | int | auto | 评估线程数 |
| `--no-sink` | flag | False | **新增**：关闭 sink 边界条件（基线模式） |

**关键行解析**：
- L416-417 `--test` 是开发期使用的快速模式，自动选择 `m_20-29`（一个较小的分组，便于验证流程）。
- L419-428 把 argparse 的命名空间转换为 main 的参数。注意 `parallel=not args.serial`（serial 模式 = 关闭并行）和 `use_sink=not args.no_sink`（no_sink flag = 关闭 sink）。

**典型用法**：

```bash
# 默认：单组测试，启用 sink 边界
python main.py --test --serial

# 关闭 sink 进行基线对比
python main.py --test --serial --no-sink

# 全量并行，4 进程
python main.py --workers 4 --selection knee

# 加速模式（自动回退为标准模式 + sink）
python main.py --test --serial --accel

# 加速模式 + 关闭 sink（真正的加速）
python main.py --test --serial --accel --no-sink
```

---

# 修改总览索引

## 高频修改场景

| 想要修改的功能 | 文件 | 行号 |
|---|---|---|
| 改步行速度 | `data_loader.py` | L73 (`speed=WALK_SPEEDS[idx]`) |
| 改风险阶段时间分段 | `optimizer.py` | L235；`pickup_sink.py` L309-316 |
| 改 sink 边界参数 | `pickup_sink.py` | L63-83 (`SINK_CONFIG`) |
| 改 NSGA 种群规模 | `config.py` | `NSGA2_CONFIG["mu"]` |
| 改进化代数 | `config.py` | `NSGA2_CONFIG["ngen"]` |
| 改解选择策略 | `optimizer.py` | L431-458 (`select_solution`) |
| 改 Pareto 评估保存频率 | `config.py` | `PARETO_VIZ["save_interval"]` |
| 关闭 sink 边界 | 命令行 | `--no-sink` |
| 启用加速 | 命令行 | `--accel`（与 sink 不兼容） |

## 性能优化建议

| 性能问题 | 优化方向 |
|---|---|
| Phase 2 步行风险计算慢 | 用 `optimizer_accel.py` 的 Numba 版本（需关闭 sink） |
| sink 仿真慢 | Numba 化 `_simulate_queue_at_stop`（需重写为定长数组） |
| 数据加载慢 | 缓存 `precompute_paths` 结果到 pickle |
| 多进程序列化失败 | 用 `--serial` 模式 + 减小 mu/ngen |

## 调试建议

| 现象 | 排查方向 |
|---|---|
| 「No feasible solutions」 | `data_loader.precompute_paths` 的吸附距离 / `feasible` 列表是否全空 |
| 适应度全为 inf | 检查 `make_evaluate` 中的 `road_paths.get` 是否找不到键 |
| sink 仿真死循环 | 检查 `_simulate_queue_at_stop` 中事件时刻是否在前进 |
| 多进程报错但串行正常 | 检查 `optimize_group` 返回的对象是否能 pickle |
| Pareto 前沿很小 | 增大 `n_observations`、缩短 `catastrophe_interval` |

---

**文档结束**。本文档覆盖了 4 个核心模块共 1505 行代码，逐块说明了设计意图与可修改点。建议你在修改代码前先对照本文档定位相关逻辑块，再进行针对性的修改。
