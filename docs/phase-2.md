# Phase 2

当前阶段完成 ReAct 工具增强的工程闭环：

- 统一工具返回结构 `ToolResult`
- `agent_steps` 记录工具输入、输出、观察结果和失败原因
- 高德 `map_tool`：POI 查询、路线耗时；缺 key 或失败时降级
- Tavily `search_tool`：公开参考内容搜索；只保留标题、链接、摘要和来源
- Open-Meteo、高德、Tavily 都经过 TTL 缓存
- 规则先评估工具结果是否与计划冲突，只有冲突时才允许轻量 LLM 修复
- LLM 计划修复只允许基于已有工具结果选择保留/删除现有 route item、补充说明和备用方案，不允许编造新机位或新事实
- 方案 Markdown 会体现地图移动耗时和参考来源

配置：

```bash
AMAP_API_KEY=
TAVILY_API_KEY=
TOOL_CACHE_TTL_SECONDS=1800
LLM_PLAN_REPAIR_MODE=auto
```

兼容旧变量：

```bash
MAPS_API_KEY=
SEARCH_API_KEY=
```

高德适合国内 POI 和路线耗时，Tavily 适合检索公开攻略/旅拍内容。两者都是可选增强，不配置也能生成方案。
