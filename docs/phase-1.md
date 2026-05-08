# Phase 1

当前阶段完成静态 MVP 初始规划闭环：

- 输入一句旅拍需求
- 解析目标并生成任务计划
- 查询 PostgreSQL `photo_spots` 机位表
- 查询 Open-Meteo 天气，失败时使用保守兜底
- 使用 Astral 计算日出、日落、黄金时刻和蓝调时刻
- 生成 Spot-Time Options
- 用规则评分并优化路线
- 输出 Markdown 方案、机位级拍摄指导和备用方案
- 持久化 `travel_plans`、`spot_time_options`、`plan_route_items`

运行 API 前必须启动 PostgreSQL 和 Redis。若未启动，FastAPI lifespan 会在启动阶段报错，`/health` 也会返回依赖不可用信息。

seed 机位文件位于 `db/seed/spots`。API 启动时默认自动导入，也可以手动执行 `python db/scripts/import_photo_spots.py`。

大模型接入是可选增强：配置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 后，Goal Parser 会尝试使用 OpenAI-compatible chat completions；未配置或调用失败时自动使用规则解析，并在 warnings 中说明。

Phase 2 的地图、搜索和工具调用记录见 `docs/phase-2.md`。
