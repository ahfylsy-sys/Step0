# MEMORY.md — 长期记忆

## 项目：Q-NSGA-II 核事故疏散优化系统

- **作者**: 刘胜禹
- **项目位置**: `c:/Users/Cityuer8261/WorkBuddy/20260411155233/Step0/`
- **原始数据路径**: `E:\CITYU@WORK\WORK-2\Data`（实际路径，config.py已修正）
- **输出路径**: `E:\Q-NSGA2-Results`
- **当前版本**: v5.6（v5.4 + 四阶段安全约束 4-Stage Safety）
- **项目于 2026-04-11 在当前工作区完成重建**（跨3个会话，12个文件全部重建）

### 核心架构
- 10个Python模块 + 5个文档文件（MODEL_CONFIG.md / MODEL_CONFIG_NARRATIVE.md / CHANGELOG.md / Case_Study_Section.md / V56_FOUR_STAGE_SAFETY_REPORT.md）
- 数据流：main.py → data_loader → optimizer/optimizer_accel → pickup_sink → shelter_selector → visualization → export → resi_anime
- config.py 为参数唯一真相源
- MODEL_CONFIG_NARRATIVE.md 为模型配置skill文件，修改后由小佑读取并同步代码

### 关键设计决策
1. load_shelters() 在 shelter_selector.py 而非 data_loader.py
2. 避难所分配：静态预分配为主（allocate_static_multi Top K=50）
3. 4指标权重：距离0.20 / 容量0.20 / 负载均衡0.20 / 动态风险0.40
4. accel v4：Numba Phase1+2 + Python Phase3 sink
5. 风险采样沿直线而非Dijkstra路径
6. v5.3 全局事件堆级联调度（heapq + 预订模式容量扣减）
7. v5.4 动态上车点关闭：到达前重定向+到达后留等（PICKUP_CLOSURE_CONFIG）
8. 道路等级优先：主干道乘数0.70-0.80，支路1.00，小路1.40-1.60（ROAD_HIERARCHY_CONFIG）
9. 动态EPZ：传统5000m仅作基线，实际由量化时变风险场确定
10. 源项：2级PSA源项类（U.S. NRC Level 3 PRA Project, Volume 3D）
11. NSGA2参数确认：μ=100, λ=100, G=100（以代码值为准，非论文Table 2的400/400/160）
12. v5.5 多阶段滚动时域：4阶段[t=0,15,25,35min]，代数[100,60,40,20]，70%热启动+30%随机，非零风险禁用上车点，已到达居民冻结
13. 多阶段文献：Li et al.(2019) rolling horizon, Deb et al.(2007) 热启动NSGA-II, Rogers et al.(2015) NRC PAR
14. v5.6 四阶段安全约束：预计算15/25/35/45min安全掩码→到达阶段判定→可行域安全过滤→评估硬约束；启用时自动禁用动态关闭；`--4stage-safe` CLI参数；12/26上车点被使用，疏散人口17,756

### 已解决的历史问题
- ~~main.py 重复调用 export_results_excel~~ ✅ 已修复
- ~~main.py 重复计算 elapsed~~ ✅ 已修复
- ~~config.py NSGA2参数与论文Table 2不一致~~ ✅ 已确认以代码值100为准

### 用户偏好
- 刘兄要求后续分析类任务默认使用 claude-code skill（Claude Code CLI, Max订阅）
