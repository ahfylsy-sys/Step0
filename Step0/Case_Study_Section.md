# Case Study: Bus-Based Nuclear Evacuation Optimization in the Daya Bay Region

# 案例研究：大亚湾核电站周边巴士疏散优化

---

## 4.1 Study Area and Accident Scenario / 研究区域与事故场景

The proposed Q-NSGA-II framework is applied to a realistic nuclear emergency evacuation scenario in the vicinity of the Daya Bay Nuclear Power Plant (DBNPP), located at the southeastern coast of Shenzhen, Guangdong Province, China. Daya Bay hosts two operational reactor units (Daya Bay Units 1–2) and four additional units at the adjacent Ling Ao site, constituting one of the largest nuclear power complexes in China. The surrounding region is characterized by a mixture of dense urban residential clusters, rural townships, and mountainous terrain traversed by a complex road network.

本文提出的Q-NSGA-II优化框架应用于中国广东省深圳市东南部大亚湾核电站（DBNPP）周边的核事故应急疏散场景。大亚湾核电基地拥有大亚湾机组（1号、2号）及相邻的岭澳站（共4台机组），是中国最大的核电基地之一。该区域地形地貌复杂，涵盖城市高密度居住区、乡镇及山地丘陵，道路网络结构多样。

The geometric center of DBNPP is georeferenced to the UTM Zone 50N coordinate system at $(x_0, y_0) = (247\,413,\, 2\,501\,099)$ m. In accordance with Chinese national nuclear emergency regulations (GB/T 17680.1-2008) and the guidance of NUREG-0654/FEMA-REP-1, Rev. 2, the plume exposure pathway emergency planning zone (EPZ) extends approximately 10 km from the reactor source term. The road network is accordingly clipped to a radius of $r_{\text{clip}} = 10$ km from the plant center. All spatial data, including population distributions, road geometries, bus stop locations, and shelter coordinates, are projected into EPSG:32650 (WGS 84 / UTM Zone 50N) to ensure metric consistency throughout the analysis.

大亚湾核电站几何中心在UTM 50N坐标系下的坐标为 $(x_0, y_0) = (247\,413,\, 2\,501\,099)$ m。依据中国核应急管理相关国家标准（GB/T 17680.1-2008）以及美国NUREG-0654/FEMA-REP-1（Rev. 2）的指导意见，烟羽应急计划区（EPZ）的范围为以反应堆源项为中心的约10 km半径区域。据此，道路网络按照以电厂中心为圆心、$r_{\text{clip}} = 10$ km为半径进行裁剪。所有空间数据（包括人口分布、道路几何、上车点坐标及避难所位置）均统一投影至EPSG:32650（WGS 84 / UTM 50N），以保证全流程的度量一致性。

A hypothetical design-basis accident scenario is assumed, in which a loss-of-coolant accident (LOCA) results in a controlled atmospheric release of radioactive materials. The radiological consequence is represented by time-varying dose-rate fields derived from a Conditional Value-at-Risk (CVaR) atmospheric dispersion model. These fields capture the conditional tail expectation of dose rates at the $\alpha$-quantile level, providing a conservative yet probabilistically rigorous characterization of the spatiotemporal radiation hazard. Four discrete risk stages are defined at $t \in \{15, 25, 35, 45\}$ minutes post-release, each represented as a rasterized dose-rate matrix with a spatial resolution of $\Delta x = 400$ m. This multi-stage representation enables the model to account for the dynamic evolution of the radioactive plume as the evacuation unfolds, consistent with the dynamic dose-based emergency evacuation model (DDEEM) proposed by Miao et al. (2023).

假设一起设计基准事故——冷却剂丧失事故（LOCA），导致放射性物质受控释放至大气。辐射后果以基于条件风险值（CVaR）大气扩散模型计算的时变剂量率场表征。该场量刻画了剂量率分布在$\alpha$分位数水平上的条件尾部期望值，从而提供了对时空辐射危害的保守且概率严格的描述。定义四个离散风险阶段，分别对应释放后 $t \in \{15, 25, 35, 45\}$ 分钟，每个阶段由空间分辨率为 $\Delta x = 400$ m的栅格化剂量率矩阵表示。这种多阶段表征使模型能够刻画放射性烟羽在疏散过程中的动态演化，与Miao等（2023）提出的动态剂量应急疏散模型（DDEEM）的思路一致。

---

## 4.2 Evacuee Population Stratification / 受疏散人群分层

Following the demographic stratification methodology adopted in Chinese off-site nuclear emergency planning (Sun et al., 2024; Ruan et al., 2025), the affected population is disaggregated into 12 demographic subgroups defined by the Cartesian product of two genders and six age cohorts: 20–29, 30–39, 40–49, 50–59, 60–69, and 70+. This fine-grained categorization is motivated by the well-documented dependence of pedestrian walking speed on both age and gender (Bohannon, 1997; Gates et al., 2006).

参照中国核电厂厂外核应急规划中常用的人口分层方法（Sun等, 2024; Ruan等, 2025），受影响人群按性别（2类）与年龄段（6个组距：20–29、30–39、40–49、50–59、60–69、70+岁）的笛卡尔积分为12个人口亚组。这一精细分组的理论依据在于，行人步行速度对年龄和性别均存在显著的依赖关系，这已被大量实证研究所证实（Bohannon, 1997; Gates等, 2006）。

Each subgroup $g$ is assigned a characteristic walking speed $v_g$ (m/s), calibrated from epidemiological and ergonomic literature. The 12 speeds, ordered as male 20–29 through male 70+, then female 20–29 through female 70+, are:

每个人口亚组 $g$ 被赋予特征步行速度 $v_g$（m/s），其取值依据流行病学与人因工程学文献进行标定。按男性20–29至男性70+、女性20–29至女性70+的顺序，12组步行速度依次为：

$$
\mathbf{v} = [2.01, 1.94, 1.87, 1.81, 1.70, 1.55, 1.84, 1.77, 1.72, 1.65, 1.59, 1.28] \quad \text{(m/s)}
$$

The population data for each subgroup is derived from census-level spatial datasets. Each resident $i$ in subgroup $g$ is characterized by a georeferenced centroid $(x_i, y_i)$ and a population weight $p_i$ representing the number of individuals at that location. The spatial distribution follows the gridded population products commonly adopted in Chinese demographic analyses.

各亚组的人口数据来源于普查级空间数据集。亚组 $g$ 中的每位居民 $i$ 由其地理参考质心坐标 $(x_i, y_i)$ 和代表该位置人口数量的权重 $p_i$ 共同表征。空间分布采用中国人口学分析中常用的网格化人口产品。

---

## 4.3 Candidate Pickup Stops and Road Network / 备选上车点与道路网络

### 4.3.1 Bus Pickup Stop Selection / 巴士上车点筛选

Candidate bus pickup stops are sourced from a comprehensive point-of-interest (POI) dataset aggregated from public transportation infrastructure within the EPZ. The raw POI set is preprocessed through a blacklist-based filtering mechanism to exclude stops that are physically inaccessible (e.g., located within restricted military zones, disconnected from the road network, or situated in terrain impassable on foot). Let $\mathcal{J} = \{1, 2, \ldots, J\}$ denote the set of $J$ retained pickup stops, each with UTM coordinates $\mathbf{b}_j = (b_{j,x}, b_{j,y})$.

备选巴士上车点源自EPZ内公共交通基础设施的综合兴趣点（POI）数据集。原始POI数据经黑名单过滤机制预处理，以排除物理上不可达的站点（例如位于军事管制区域内、与路网不连通或位于步行不可通行地形的站点）。令 $\mathcal{J} = \{1, 2, \ldots, J\}$ 表示经筛选保留的 $J$ 个上车点集合，其UTM坐标记为 $\mathbf{b}_j = (b_{j,x}, b_{j,y})$。

### 4.3.2 Road Network Construction / 道路网络构建

The road network is constructed from a Shapefile-formatted dataset of the Shenzhen metropolitan road system. The raw polyline geometries are loaded, reprojected to UTM Zone 50N, and clipped to the 10 km buffer around the plant center. After spatial filtering, the geometries are exploded into individual line segments, and a planar undirected graph $G = (\mathcal{V}, \mathcal{E})$ is constructed, where each node $v \in \mathcal{V}$ corresponds to a road intersection or segment endpoint (snapped to a tolerance of 1 mm to merge near-coincident vertices), and each edge $(u, v) \in \mathcal{E}$ carries a weight equal to its Euclidean length $\ell_{uv}$ (m). A KD-tree spatial index is built over the node coordinate array to enable efficient nearest-neighbor snapping of resident centroids and bus stop locations onto the network.

道路网络基于深圳市道路系统的Shapefile格式数据集构建。原始折线几何经加载、重投影至UTM 50N后，按以电厂中心为圆心的10 km缓冲区进行裁剪。经空间过滤后，几何体被分解为独立线段，并构建平面无向图 $G = (\mathcal{V}, \mathcal{E})$，其中每个节点 $v \in \mathcal{V}$ 对应道路交叉口或线段端点（以1 mm容差进行近邻合并），每条边 $(u,v) \in \mathcal{E}$ 的权重为其欧氏长度 $\ell_{uv}$（m）。基于节点坐标数组构建KD-tree空间索引，以实现居民质心和上车点向路网的高效最近邻吸附。

### 4.3.3 Dijkstra-Based Path Precomputation / 基于Dijkstra算法的路径预计算

For each resident $i$ in the active population, a single-source Dijkstra shortest path computation is performed from the resident's snapped network node to all bus stop nodes. This yields a dictionary of shortest-path distances $d_{ij}$ and the corresponding path geometries $\pi_{ij}$ (represented as ordered sequences of $(x,y)$ coordinates along the road network). The path geometry is stored as a Shapely `LineString` object for downstream visualization and walk-time computation. A strict network-only routing policy is enforced: if no connected path exists between resident $i$ and bus stop $j$ in $G$, the pair $(i, j)$ is marked as infeasible. No Euclidean fallback is employed, ensuring that all computed walking distances faithfully reflect the actual road network topology.

对于每位活跃居民 $i$，从其吸附至路网的节点出发，执行单源Dijkstra最短路径计算，获取至所有上车点节点的最短路径距离 $d_{ij}$ 及对应路径几何 $\pi_{ij}$（以沿路网的有序 $(x,y)$ 坐标序列表示）。路径几何以Shapely `LineString` 对象存储，用于后续可视化和步行时间计算。系统执行严格的路网路由策略：若居民 $i$ 与上车点 $j$ 在图 $G$ 中不存在连通路径，则该对 $(i,j)$ 被标记为不可行。不采用欧氏直线回退机制，确保所有计算的步行距离真实反映实际道路网络拓扑。

---

## 4.4 Feasible Domain Construction / 可行域构建

The feasible assignment set for each resident $i$ is defined as:

每位居民 $i$ 的可行分配集定义为：

$$
\mathcal{F}_i = \left\{ j \in \mathcal{J} \;\middle|\; \pi_{ij} \text{ exists in } G \;\wedge\; \frac{d_{ij}}{v_g} \leq T_{\max} \right\}
$$

where $T_{\max} = 45 \times 60 = 2{,}700$ s is the maximum allowable walking time, consistent with the NUREG-0654/FEMA-REP-1 (Rev. 2) guideline that the plume EPZ population should be notified and begin evacuation within 45 minutes. This time bound also aligns with the 45-minute notification-gathering window adopted in Chinese nuclear emergency practice (Ruan et al., 2025). Residents for whom $\mathcal{F}_i = \varnothing$ are excluded from the optimization and flagged for alternative intervention (e.g., helicopter extraction or in-place sheltering).

其中 $T_{\max} = 45 \times 60 = 2\,700$ s 为最大允许步行时间，与NUREG-0654/FEMA-REP-1（Rev. 2）关于烟羽EPZ内人群应在45分钟内完成通知与开始疏散的指导意见一致，亦与中国核应急实践中采用的45分钟通知-集结时间窗相吻合（Ruan等, 2025）。对于 $\mathcal{F}_i = \varnothing$ 的居民，将其从优化过程中排除，并标记为需要替代干预措施（如直升机撤离或就地避难）。

---

## 4.5 Bi-Objective Optimization Formulation / 双目标优化模型构建

### 4.5.1 Decision Variables / 决策变量

Each individual in the Q-NSGA-II population encodes a complete assignment vector $\mathbf{a} = (a_1, a_2, \ldots, a_N)$, where $a_i \in \mathcal{F}_i$ denotes the pickup stop assigned to resident $i$, and $N$ is the number of active residents in subgroup $g$.

Q-NSGA-II种群中的每个个体编码一个完整的分配向量 $\mathbf{a} = (a_1, a_2, \ldots, a_N)$，其中 $a_i \in \mathcal{F}_i$ 表示分配给居民 $i$ 的上车点，$N$ 为亚组 $g$ 中活跃居民的数量。

### 4.5.2 Objective 1: Population-Weighted Walking Time / 目标1：人口加权步行时间

The first objective to be minimized is the population-weighted aggregate walking time augmented by the sink-phase delay:

第一个最小化目标是人口加权总步行时间与sink阶段延迟的合成值：

$$
f_1(\mathbf{a}) = \underbrace{\sum_{i=1}^{N} p_i \cdot \frac{d_{i,a_i}}{v_g}}_{\text{Phase 1: walk}} \;+\; \underbrace{\max\!\left(0,\; T_{\text{sink}}(\mathbf{a}) - \max_i \frac{d_{i,a_i}}{v_g}\right) \cdot \sum_{i=1}^{N} p_i}_{\text{Phase 3: sink delay}}
$$

where $T_{\text{sink}}(\mathbf{a})$ is the total evacuation completion time returned by the pickup-sink boundary model (described in Section 4.6). The first term captures the spatial efficiency of resident-to-stop assignments; the second term penalizes configurations that induce excessive queuing and bus transit delays beyond the walking phase.

其中 $T_{\text{sink}}(\mathbf{a})$ 为上车点sink边界模型（详见第4.6节）返回的总疏散完成时间。第一项衡量居民-上车点分配的空间效率；第二项对超出步行阶段的过度排队和巴士运输延迟进行惩罚。

### 4.5.3 Objective 2: Cumulative Radiation Exposure / 目标2：累积辐射暴露

The second objective to be minimized is the total radiation dose accumulated by the population during the evacuation:

第二个最小化目标是人群在疏散过程中累积的辐射剂量总量：

$$
f_2(\mathbf{a}) = \underbrace{\sum_{m=0}^{44} \left[\sum_{i=1}^{N} R\!\left(x_i^{(m)}, y_i^{(m)}, s(m)\right) \cdot p_i \right] \cdot 60}_{\text{Phase 2: walk risk}} \;+\; Q_{\text{risk}}(\mathbf{a})
$$

where $(x_i^{(m)}, y_i^{(m)})$ is the interpolated position of resident $i$ along their assigned road path $\pi_{i,a_i}$ at minute $m$ (computed from cumulative walked distance $m \cdot 60 \cdot v_g$ projected onto the path; once the resident reaches the bus stop, the position is held fixed at the stop coordinates); $s(m) \in \{0,1,2,3\}$ is the risk stage index corresponding to the time intervals $[0,15)$, $[15,25)$, $[25,35)$, and $[35, T_{\max}/60)$ minutes (i.e., the fourth stage extends through the end of the walking horizon); $R(x,y,s)$ denotes the dose-rate value at spatial coordinates $(x,y)$ during stage $s$, obtained via bilinear interpolation from the rasterized CVaR dose-rate field with grid resolution $\Delta x = 400$ m; and $Q_{\text{risk}}(\mathbf{a})$ is the aggregate queuing-phase radiation exposure computed by the sink boundary model. The 60-second multiplication converts per-minute dose-rate samples to cumulative dose (dose-rate $\times$ duration), implicitly assuming that the dose rate remains constant within each one-minute sub-interval.

其中 $(x_i^{(m)}, y_i^{(m)})$ 为居民 $i$ 在第 $m$ 分钟沿其分配路径 $\pi_{i,a_i}$ 的插值位置（由累计步行距离 $m \cdot 60 \cdot v_g$ 沿路径投影计算；居民到达上车点后位置固定为站点坐标）；$s(m) \in \{0,1,2,3\}$ 为对应时间区间 $[0,15)$, $[15,25)$, $[25,35)$, $[35, T_{\max}/60)$ 分钟的风险阶段索引（即第四阶段延伸至步行时间窗末端）；$R(x,y,s)$ 表示阶段 $s$ 期间空间坐标 $(x,y)$ 处的剂量率值，通过对栅格化CVaR剂量率场（网格分辨率 $\Delta x = 400$ m）进行双线性插值获取；$Q_{\text{risk}}(\mathbf{a})$ 为sink边界模型计算的排队阶段累积辐射暴露。乘以60秒系数将每分钟剂量率采样转换为累积剂量（剂量率×时间），隐含地假设每个一分钟子区间内剂量率保持恒定。

---

## 4.6 Pickup-Sink Boundary Condition Model / 上车点sink边界条件模型

To incorporate the bus-based second-phase evacuation into the optimization, a discrete-event queuing simulation model (referred to as the "sink boundary condition") is embedded within each fitness evaluation. This model transforms each pickup stop into a time-evolving service node where evacuees queue for bus transport to long-term shelters, extending the pure pedestrian-assignment formulation to a physically realistic two-phase process. The key parameters, calibrated from regulatory standards and transportation engineering literature, are summarized in Table 1.

为将巴士运输的第二阶段疏散纳入优化过程，在每次适应度评估中嵌入一个离散事件排队仿真模型（称为"sink边界条件"）。该模型将每个上车点转化为一个时变服务节点，疏散人员在此排队等待巴士运送至长期避难所，从而将纯步行分配模型扩展为物理上合理的两阶段过程。主要参数依据监管标准和交通工程文献标定，汇总于表1。

**Table 1. Parameters of the pickup-sink boundary condition model.**

**表1. 上车点sink边界条件模型参数**

| Parameter | Symbol | Value | Source |
|---|---|---|---|
| Bus capacity | $Q$ | 50 persons/bus | GB/T 19260-2018 |
| Nominal fleet size | $F$ | 30 buses | Pereira & Bish (2015) |
| Bus cruising speed | $v_b$ | 30 km/h (8.33 m/s) | TCQSM 3rd Ed. |
| Dispatch delay | $\tau_d$ | 600 s (10 min) | Goerigk & Grün (2014) |
| Per-person boarding time | $\tau_b$ | 2.5 s/person | TCQSM Exhibit 6-4 |
| Default shelter distance (fallback) | $d_s^0$ | 30,000 m | FEMA REP (2023) |
| Shelter capacity safety margin (sink scheduler) | $f_s^{\text{sink}}$ | 0.95 | Operational reserve |
| Queue risk discretization step | $\Delta t_q$ | 60 s | — |
| Maximum evacuation duration | $T_{\text{evac}}^{\max}$ | 7,200 s (2 h) | FEMA REP (2023) |

It should be noted that the parameter $F$ specifies the nominal fleet size used in the *a priori* fleet-capacity feasibility check (which verifies whether the total population can in principle be evacuated within $T_{\text{evac}}^{\max}$ given $F$ buses, $Q$ seats, and the maximum number of round trips). Within the actual discrete-event scheduler, however, each utilized pickup stop is treated as having access to its own dedicated bus dispatcher, which independently issues vehicles at the per-stop completion-time intervals derived from boarding and round-trip durations. This per-stop dispatch idealization corresponds to an upper-bound performance assumption commonly invoked in the bus-evacuation literature (Goerigk and Grün, 2014).

需要指出的是，参数 $F$ 仅用于事前的车队容量可行性检查（即给定 $F$ 辆巴士、$Q$ 个座位以及最大往返次数后，验证总人口在 $T_{\text{evac}}^{\max}$ 内原则上能否被疏散）。在实际的离散事件调度器中，每个被使用的上车点被视为拥有独立的专属巴士派遣器，按由上车时间和往返时长推导出的逐站完成时间间隔独立派车。这种"逐站派遣"的理想化假设是巴士疏散文献中常见的性能上界设定（Goerigk和Grün, 2014）。

The simulation proceeds as follows. For each utilized pickup stop $j$, a chronologically sorted arrival queue is formed from the walking times $d_{ij}/v_g$ of all residents assigned to stop $j$. Beginning at time $\tau_d$, buses arrive at stop $j$ in a cyclic pattern with a round-trip time of $2 d_{j \to s}/v_b$, where $d_{j \to s}$ is the road-network distance from stop $j$ to its assigned shelter (see Section 4.7). Each arriving bus loads up to $Q$ evacuees from the queue; partial loading is permitted when the remaining queue length is less than $Q$. During the queuing interval, evacuees are exposed to the time-varying radiation field at the stop location, and the resulting dose is accumulated as $Q_{\text{risk}}$.

仿真过程如下：对于每个被使用的上车点 $j$，将分配至该站的所有居民按步行时间 $d_{ij}/v_g$ 进行时间排序，形成到达队列。从时刻 $\tau_d$ 开始，巴士按周期性模式到达站点 $j$，单次往返时间为 $2 d_{j \to s}/v_b$，其中 $d_{j \to s}$ 为上车点 $j$ 至其分配避难所的路网距离（详见第4.7节）。每辆到站巴士装载至多 $Q$ 名疏散人员；当剩余排队人数不足 $Q$ 时，允许部分装载。在排队期间，疏散人员暴露于上车点位置的时变辐射场中，由此产生的剂量累积为 $Q_{\text{risk}}$。

---

## 4.7 Multi-Criteria Shelter Allocation with Cascade Scheduling / 多准则避难所分配与级联调度

### 4.7.1 Shelter Data / 避难所数据

A total of 678 candidate long-term shelters are sourced from an administrative shelter database covering the Shenzhen metropolitan area, with a combined nominal capacity of 176,948 persons and individual shelter capacities ranging from 10 to 5,000. To exclude facilities too small for operationally meaningful use, shelters with capacity below $C_{\min} = 50$ are filtered out, yielding 567 valid candidates. Each shelter $k$ is characterized by its UTM coordinates $\mathbf{s}_k$, nominal capacity $C_k$, and a unique identifier.

共678个备选长期避难所源自深圳市行政避难所数据库，总名义容量为176,948人，单个避难所容量范围为10至5,000人。为排除运营上无实际意义的过小设施，过滤掉容量低于 $C_{\min} = 50$ 的避难所，最终保留567个有效候选。每个避难所 $k$ 由其UTM坐标 $\mathbf{s}_k$、名义容量 $C_k$ 和唯一标识符表征。

### 4.7.2 Four-Criterion Weighted Scoring Model / 四指标加权评分模型

Inspired by the multi-criteria shelter suitability frameworks in the nuclear emergency literature (Yin et al., 2023; Choi et al., 2023; Wang et al., 2016), a composite scoring function is designed to rank candidate shelters for each pickup stop. For each pickup stop $j$, the algorithm first uses the pre-built KD-tree to retrieve the $K_{\text{pre}} = \max(2K, 100) = 100$ Euclidean-nearest valid shelters as a candidate pool, then computes four normalized indicators on this pool and combines them linearly:

借鉴核应急文献中的多准则避难所适宜性评价框架（Yin等, 2023; Choi等, 2023; Wang等, 2016），设计一个综合评分函数，用于对每个上车点的候选避难所进行排序。对于每个上车点 $j$，算法首先利用预构建的KD-tree检索 $K_{\text{pre}} = \max(2K, 100) = 100$ 个欧氏距离最近的有效避难所作为候选池，然后在该候选池上计算四个归一化指标并进行线性组合：

$$
S_{jk} = 0.20 \cdot \hat{d}_{jk} + 0.20 \cdot \hat{c}_{jk} + 0.20 \cdot \hat{u}_{jk} + 0.40 \cdot \hat{r}_{jk}
$$

where:

- **$\hat{d}_{jk}$ (Distance efficiency)**: the min-max normalized travel distance from stop $j$ to shelter $k$. When the precomputed Dijkstra path-length matrix is available, the road-network shortest-path distance is used; otherwise the system falls back to the Euclidean straight-line distance. This corresponds to the P-median objective $F_1$ in Yin et al. (2023).
- **$\hat{c}_{jk}$ (Capacity tightness)**: the normalized ratio of stop $j$'s demand to shelter $k$'s nominal available capacity $C_k \cdot f_s^{\text{score}}$, where $f_s^{\text{score}} = 0.90$ is the safety margin used in the scoring stage. This implements the capacity constraint of Sun et al. (2024). A hard overflow penalty $\kappa = 10.0$ is applied when $C_k \cdot f_s^{\text{score}} \leq 0$, effectively excluding such candidates.
- **$\hat{u}_{jk}$ (Load balance / dispersion)**: in the multi-shelter cascade mode adopted in this study, this term is set to zero in the *scoring* stage because capacity competition is resolved dynamically by the global event-driven scheduler rather than by greedy pre-assignment. The load-balancing effect therefore emerges naturally from the cascade mechanism of Section 4.7.3, in line with the dispersion philosophy of Sharbaf et al. (2025) and the equity term in Song et al. (2024).
- **$\hat{r}_{jk}$ (Dynamic spatiotemporal risk)**: the path-averaged dose rate sampled along five equidistant points on the straight line from stop $j$ to shelter $k$, evaluated at the latest available risk stage ($s = 3$, corresponding to the post-35-minute interval). Straight-line sampling, rather than full Dijkstra path interpolation, is adopted because (i) inter-shelter distances are at the kilometer scale, where road-network detours have negligible effect on the macroscopic dose integral, and (ii) the scoring function is invoked only once per optimization run during the pre-allocation phase, so further refinement is unwarranted. This implements a coarse-grained version of the "moving expected dose" concept in the DDEEM framework of Miao et al. (2023).

其中：

- **$\hat{d}_{jk}$（距离效率）**：上车点 $j$ 至避难所 $k$ 的运输距离经min-max归一化。当预计算的Dijkstra路径长度矩阵可用时，采用路网最短路径距离；否则回退至欧氏直线距离。该指标对应Yin等（2023）中的P-median目标 $F_1$。
- **$\hat{c}_{jk}$（容量紧度）**：上车点 $j$ 的需求与避难所 $k$ 的名义可用容量 $C_k \cdot f_s^{\text{score}}$ 之比的归一化值，其中 $f_s^{\text{score}} = 0.90$ 为评分阶段所用的容量安全裕度系数。该指标实现了Sun等（2024）的容量约束。当 $C_k \cdot f_s^{\text{score}} \leq 0$ 时施加硬性溢出惩罚 $\kappa = 10.0$，从而将此类候选实质性排除。
- **$\hat{u}_{jk}$（负载均衡 / 分散度）**：在本研究采用的多避难所级联模式下，该项在*评分阶段*被置零，因为容量竞争由全局事件驱动调度器动态解决，而非通过贪婪预分配。负载均衡效应因此自然涌现于第4.7.3节的级联机制之中，与Sharbaf等（2025）的分散度思想及Song等（2024）的公平性项理念一致。
- **$\hat{r}_{jk}$（动态时空风险）**：沿上车点 $j$ 至避难所 $k$ 直线上等距采样5个点的路径平均剂量率，在最晚的可用风险阶段（$s = 3$，对应释放后35分钟之后的时间区间）评估。之所以采用直线采样而非完整Dijkstra路径插值，原因在于：（i）避难所间距处于公里量级，路网绕行对宏观剂量积分的影响可忽略；（ii）评分函数仅在优化运行的预分配阶段调用一次，进一步精细化得不偿失。该指标实现了Miao等（2023）DDEEM框架中"移动预期剂量"概念的粗粒度版本。

The risk indicator receives a doubled weight of 0.40, reflecting the paramount importance of dose minimization in nuclear emergencies. This assignment is empirically supported by the finding of Ren et al. (2024) that dose-aware routing reduces the effective dose by over 60% compared to shortest-path strategies.

风险指标被赋予两倍于其他指标的权重（0.40），反映了核应急场景中剂量最小化的首要重要性。该权重赋值的经验支撑来自Ren等（2024）的研究结论：剂量感知路径规划相比最短路径策略可降低超过60%的有效剂量。

### 4.7.3 Global Cascade Scheduling with Shared Capacity / 共享容量下的全局级联调度

Rather than assigning a single shelter to each pickup stop in a static one-to-one mapping (as in classical P-median formulations), this study implements a dynamic multi-shelter cascade scheduling mechanism. For each stop $j$, the top $K = 50$ candidate shelters are ranked by ascending score $S_{jk}$ and retained as an ordered candidate list $\mathcal{L}_j = (k_1, k_2, \ldots, k_{50})$.

区别于经典P-median模型中为每个上车点静态分配单个避难所的一对一映射策略，本研究实现了一种动态多避难所级联调度机制。对于每个上车点 $j$，按评分 $S_{jk}$ 升序排列前 $K = 50$ 个候选避难所，形成有序候选列表 $\mathcal{L}_j = (k_1, k_2, \ldots, k_{50})$。

During the discrete-event bus dispatch simulation, all pickup stops share a global remaining-capacity vector $\mathbf{R} = (R_1, R_2, \ldots, R_{678})$, initialized as $R_k = C_k \cdot f_s^{\text{sink}}$ at the beginning of each fitness evaluation, where $f_s^{\text{sink}} = 0.95$ is the operational safety margin applied at the scheduling stage (slightly more permissive than the $f_s^{\text{score}} = 0.90$ used for shelter ranking, on the rationale that ranking is a conservative pre-allocation step whereas scheduling reflects realized utilization). When a bus at stop $j$ is loaded with $q$ passengers and ready for dispatch, the scheduler iterates through $\mathcal{L}_j$ to find the first shelter $k_m$ satisfying $R_{k_m} \geq q$. Upon selection, the capacity is immediately debited ($R_{k_m} \leftarrow R_{k_m} - q$) in a *reservation mode*—following the dominant convention in the bus-evacuation literature (Pereira and Bish, 2015)—and the bus's round-trip time is computed using the Dijkstra-derived road-network distance to $k_m$. If all 50 candidates are exhausted, the bus is directed to the top-ranked shelter $k_1$ with an overflow penalty propagated through the fitness function.

在离散事件巴士调度仿真中，所有上车点共享一个全局剩余容量向量 $\mathbf{R} = (R_1, R_2, \ldots, R_{678})$，在每次适应度评估开始时初始化为 $R_k = C_k \cdot f_s^{\text{sink}}$，其中 $f_s^{\text{sink}} = 0.95$ 是调度阶段所用的运营安全裕度（略宽于评分阶段所用的 $f_s^{\text{score}} = 0.90$，理由在于：评分阶段是保守的预分配步骤，而调度阶段反映的是实际利用情况）。当上车点 $j$ 的一辆巴士装载 $q$ 名乘客并准备发车时，调度器遍历 $\mathcal{L}_j$ 寻找满足 $R_{k_m} \geq q$ 的第一个避难所 $k_m$。选定后，以*预订模式*立即扣减容量（$R_{k_m} \leftarrow R_{k_m} - q$）——这一约定与巴士疏散文献中的主流做法相符（Pereira和Bish, 2015）——并依据至 $k_m$ 的Dijkstra路网距离计算该巴士的往返时间。若50个候选全部耗尽，则将巴士指向排名最高的避难所 $k_1$，溢出惩罚通过适应度函数传递。

This mechanism ensures that (i) multiple stops may share the same high-scoring shelter until its capacity is exhausted, (ii) later-dispatched buses automatically cascade to secondary shelters when the primary is full, and (iii) each bus independently computes its round-trip time based on its actual destination, accurately reflecting the heterogeneous travel distances inherent in a cascaded multi-shelter strategy (Pereira and Bish, 2015).

该机制确保：（i）多个上车点可共享同一高评分避难所直至其容量耗尽；（ii）后发车的巴士在首选避难所满载时自动级联至次选避难所；（iii）每辆巴士根据其实际目的地独立计算往返时间，准确反映级联多避难所策略固有的异质运输距离（Pereira和Bish, 2015）。

---

## 4.8 Q-NSGA-II Algorithm Configuration / Q-NSGA-II算法配置

The optimization engine is based on a hybrid Quantum-inspired Non-dominated Sorting Genetic Algorithm II (Q-NSGA-II), which augments the classical NSGA-II framework (Deb et al., 2002) with quantum-inspired individuals and rotation-gate operators to enhance population diversity and global search capability. The algorithmic parameters, established through preliminary sensitivity experiments, are listed in Table 2.

优化引擎基于量子启发的非支配排序遗传算法II（Q-NSGA-II），在经典NSGA-II框架（Deb等, 2002）的基础上引入量子启发个体和旋转门算子，以增强种群多样性和全局搜索能力。算法参数经初步灵敏度实验确定，列于表2。

**Table 2. Q-NSGA-II algorithm parameters.**

**表2. Q-NSGA-II算法参数**

| Parameter | Value | Description |
|---|---|---|
| Population size $\mu$ | 400 | Number of individuals per generation |
| Offspring size $\lambda$ | 400 | Offspring generated per generation |
| Generations $G_{\max}$ | 160 | Maximum number of evolutionary generations |
| Crossover probability $p_c$ | 0.70 | Probability of crossover operation |
| Mutation probability $p_m$ | 0.20 | Probability of mutation per individual |
| Per-gene mutation $p_{\text{ind}}$ | 0.10 | Independent gene flip probability |
| Quantum observations $n_{\text{obs}}$ | 3 | Observations per quantum individual per generation |
| Rotation gate $\Delta\theta_{\max}$ | $0.05\pi$ | Maximum rotation angle |
| Rotation gate $\Delta\theta_{\min}$ | $0.001\pi$ | Minimum rotation angle |
| Quantum crossover rate | 0.50 | Crossover probability in quantum space |
| Quantum mutation rate | 0.15 | Mutation probability in quantum space |
| Catastrophe interval | 50 | Generations between catastrophic resets |
| Catastrophe rate | 0.10 | Fraction of quantum population reset |
| Classical offspring ratio | 0.30 | Fraction of offspring from classical operators |

Each generation produces approximately $\mu \cdot n_{\text{obs}} = 1{,}200$ quantum-observed offspring (Step 7), $\lfloor\lambda \cdot 0.30\rfloor = 120$ classical GA offspring (Step 8), and $\mu = 400$ rotation-gate guidance evaluations (Step 4), totaling roughly 1,720 fitness evaluations per generation and on the order of $2.75 \times 10^5$ across the full 160-generation run. To accelerate computation, a Numba-JIT-compiled evaluation kernel vectorizes the per-minute position interpolation and risk-field lookup of Phase 2, reducing per-evaluation latency from approximately 5 ms (pure Python with Shapely) to 0.25 ms—an approximately 20-fold speedup—while preserving bit-exact equivalence with the reference implementation at float64 precision.

每一代约产生 $\mu \cdot n_{\text{obs}} = 1\,200$ 个量子观测后代（步骤7）、$\lfloor\lambda \cdot 0.30\rfloor = 120$ 个经典GA后代（步骤8），以及 $\mu = 400$ 次旋转门引导评估（步骤4），每代总计约1,720次适应度评估，完整160代优化运行共约 $2.75 \times 10^5$ 次评估。为加速计算，采用Numba JIT编译的评估内核对Phase 2的逐分钟位置插值和风险场查询进行向量化处理，将单次评估延迟从约5 ms（纯Python+Shapely）降低至0.25 ms——实现约20倍加速——且在float64精度下与参考实现保持比特级一致。

---

## 4.9 Solution Selection Strategy / 解选择策略

Upon convergence, three Pareto-front solution selection strategies are available: (i) **minimum-time**, selecting the solution that minimizes $f_1$; (ii) **minimum-risk**, selecting the solution that minimizes $f_2$; and (iii) **knee-point**, identifying the solution at the point of maximum curvature on the Pareto front, which represents the best trade-off between the two objectives. The knee-point strategy is generally recommended for nuclear emergency applications, as it balances evacuation efficiency with radiological safety without requiring an explicit preference articulation from the decision-maker.

算法收敛后，提供三种Pareto前沿解选择策略：（i）**最短时间**，选择最小化 $f_1$ 的解；（ii）**最低风险**，选择最小化 $f_2$ 的解；（iii）**拐点**，识别Pareto前沿上曲率最大处的解，该解代表两个目标之间的最优折中。对于核应急场景，通常推荐拐点策略，因其在疏散效率与辐射安全之间取得平衡，且无需决策者做出显式偏好表达。

---

## References / 参考文献

- Bohannon, R.W. (1997). Comfortable and maximum walking speed of adults aged 20–79 years: Reference values and determinants. *Age and Ageing*, 26(1), 15–19.
- Choi, J.S., Kim, J.W., Joo, H.Y., Moon, J.H. (2023). Applying a big data analysis to evaluate the suitability of shelter locations for the evacuation of residents in case of radiological emergencies. *Nuclear Engineering and Technology*, 55(1), 261–269.
- Deb, K., Pratap, A., Agarwal, S., Meyarivan, T. (2002). A fast and elitist multiobjective genetic algorithm: NSGA-II. *IEEE Transactions on Evolutionary Computation*, 6(2), 182–197.
- Gates, T.J., Noyce, D.A., Bill, A.R., Van Ee, N. (2006). Recommended walking speeds for pedestrian clearance timing based on pedestrian characteristics. *Transportation Research Record*, 1982, 38–47.
- Goerigk, M., Grün, B. (2014). A robust bus evacuation model with delayed scenario information. *OR Spectrum*, 36(4), 923–948.
- Miao, H., Zhang, G., Yu, P., Shi, C., Zheng, J. (2023). Dynamic dose-based emergency evacuation model for enhancing nuclear power plant emergency response strategies. *Energies*, 16(17), 6338.
- Pereira, V.C., Bish, D.R. (2015). Scheduling and routing for a bus-based evacuation with a constant evacuee arrival rate. *Transportation Science*, 49(4), 853–867.
- Ren, Y., Zhang, G., Zheng, J., Miao, H. (2024). An integrated solution for nuclear power plant on-site optimal evacuation path planning based on atmospheric dispersion and dose model. *Sustainability*, 16(6), 2458.
- Ruan, F., Chen, C., He, C., Cheng, Y., Sun, Y. (2025). Optimization method of public decontamination location and allocation problem in off-site nuclear emergency based improved NSGA-II. *Journal of Hazardous Materials*, 489, 137572.
- Sharbaf, M., Bélanger, V., Cherkesly, M., Rancourt, M.-E., Toglia, G.M. (2025). Risk-based shelter network design in flood-prone areas: An application to Haiti. *Omega*, 131, 103188.
- Sibul, G., Schütz, P., Fagerholt, K. (2026). Arctic route planning under ice uncertainty: A risk-averse stochastic shortest path problem. *Transportation Research Part E*, 205, 104507.
- Sun, Y., Yuan, T., Chai, X., Chen, C. (2024). Bus based emergency evacuation organization strategy of nuclear power plant planning restricted area. *Progress in Nuclear Energy*, 169, 105069.
- Wang, W., Yang, S., Hu, F., He, S., Shi, X., Meng, Y., Shi, M. (2016). Integrated optimization model for shelter allocation and evacuation routing with consideration of reliability. *Transportation Research Record*, 2599, 33–42.
- Yin, Y., Zhao, X., Lv, W. (2023). Emergency shelter allocation planning technology for large-scale evacuation based on quantum genetic algorithm. *Frontiers in Public Health*, 10, 1098675.
- Zhao, S., Yang, H., Wang, Y., Yang, Z. (2026). A risk-averse two-stage stochastic programming model for vessel schedule recovery in liner shipping service. *Transportation Research Part E*, 208, 104655.
