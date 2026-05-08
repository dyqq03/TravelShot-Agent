from __future__ import annotations

from app.agent.state import AgentState


def build_task_plan(parsed_goal: dict) -> list[dict]:
    destination = parsed_goal.get("destination", "目的地")
    return [
        {
            "task_id": "candidate_discovery",
            "title": "多模态意图分析与机位发现",
            "purpose": f"为{destination}解析文字/参考图中的点位、场景和风格，先查内置库，再按需调用外部工具。",
            "success_criteria": "候选机位不少于 3 个；用户明确点位未命中或缺坐标时才调用高德 POI。",
        },
        {
            "task_id": "spot_fusion",
            "title": "机位融合与地图验证",
            "purpose": "合并内置机位、地图 POI 和公开参考线索，去重并计算可信度。",
            "success_criteria": "不编造经纬度；缺坐标或外部工具失败时在方案中提示不确定性。",
        },
        {
            "task_id": "transport_planning",
            "title": "多方式交通规划",
            "purpose": "使用机位库经纬度调用高德基础 LBS 路线规划，比较步行、骑行、打车和公交/地铁。",
            "success_criteria": "每段相邻机位都有推荐出行方式和可选交通方案；缺坐标时给出兜底说明。",
        },
        {
            "task_id": "reference_search",
            "title": "搜索参考内容",
            "purpose": "搜索公开攻略和旅拍内容，只提取标题、链接和摘要作为参考线索。",
            "success_criteria": "记录参考来源；失败时不影响主方案生成。",
        },
        {
            "task_id": "weather_lookup",
            "title": "查询天气",
            "purpose": "获取小时级温度、降水、云量和风力，用于判断出发与拍摄风险。",
            "success_criteria": "给出天气摘要、风险和可执行的光线建议。",
        },
        {
            "task_id": "sunlight_lookup",
            "title": "计算日照窗口",
            "purpose": "得到日出、日落、黄金时刻、蓝调时刻和正午强光窗口。",
            "success_criteria": "每个拍摄日期都有可用光线窗口。",
        },
        {
            "task_id": "visual_goal",
            "title": "构建视觉目标",
            "purpose": "把自然语言风格转成可评分的画面元素、氛围和动作优先级。",
            "success_criteria": "形成 must-have、optional 和 weather adaptation。",
        },
        {
            "task_id": "spot_time_options",
            "title": "生成 Spot-Time Options",
            "purpose": "把机位与时间窗口组合成候选拍摄项。",
            "success_criteria": "每个候选项都有机位、时间、拍摄目标、风险和动作建议。",
        },
        {
            "task_id": "scoring",
            "title": "规则评分",
            "purpose": "用风格、元素、光线、天气、交通和风险进行规则评分。",
            "success_criteria": "输出排序后的候选项和分项分数。",
        },
        {
            "task_id": "route_optimizer",
            "title": "路线优化",
            "purpose": "选择不冲突、不过度绕路且保留关键光线窗口的一日路线，并调用地图工具估算移动耗时。",
            "success_criteria": "输出 3-4 个 route items、移动耗时和每个机位拍摄指导。",
        },
        {
            "task_id": "final_formatter",
            "title": "生成 Markdown",
            "purpose": "把结构化结果整理为用户可执行方案。",
            "success_criteria": "包含目标总结、天气光线、路线、指导和备用方案。",
        },
    ]


def planner_node(state: AgentState) -> AgentState:
    return {"task_plan": build_task_plan(state["parsed_goal"])}
