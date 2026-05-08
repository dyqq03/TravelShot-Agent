# 旅拍助手 Agent 可落地完整设计方案

> 版本：Plan-and-Execute + ReAct + Adaptive Planning Loop + Spot-Time Option 架构版  
> 项目定位：一个能够根据用户想拍的照片、目的地、天气、光线、交通、机位、票务和现场状态，生成并持续修正旅拍执行方案的 AI Agent。

---

## 目录

1. 项目总览
2. 核心产品定义
3. 核心 Agent 架构
4. 四个核心设计范式
5. 系统整体技术架构
6. 项目目录结构
7. 核心数据模型
8. 数据库设计
9. 后端 API 设计
10. Agent 工作流设计
11. 前端页面设计
12. 外部工具与服务设计
13. Prompt 与 Agent 节点设计
14. 分阶段开发路线
15. 每阶段具体搭建步骤
16. MVP 范围与验收标准
17. 测试与评测体系
18. 风险、合规与降级策略
19. 商业化与后续扩展
20. 最终开发建议

---

# 1. 项目总览

## 1.1 项目名称

推荐名称：

- 旅拍助手 Agent
- TravelShot AI
- 出片规划 Agent
- AI 旅拍策划师
- PhotoTrip Planner

本文统一称为：**旅拍助手 Agent**。

---

## 1.2 一句话定位

**旅拍助手 Agent 是一个面向旅行拍照用户、内容创作者和旅拍摄影师的 AI 决策系统。用户只需要描述想拍的照片、目的地、日期、设备或参考图，系统就能结合参考内容、机位库、地图、天气、光线、交通、票务和现场状态，生成并持续调整一份可执行的旅拍方案。**

---

## 1.3 这个项目要解决什么问题

用户真正想要的不是一篇旅游攻略，而是：

```text
我想拍这种照片。
我应该去哪？
哪天去？
几点去？
买几点到达的票？
站在哪里？
摄影师站在哪里？
用手机几倍焦段？
摆什么动作？
如果下雨、晚到、人太多，应该怎么改？
```

所以本项目不是普通旅行 Agent，也不是普通摄影问答工具，而是一个 **动态旅拍决策 Agent**。

---

## 1.4 和普通旅行规划产品的区别

| 普通旅行规划 | 旅拍助手 Agent |
|---|---|
| 以景点为核心 | 以机位和时间窗口为核心 |
| 关注好不好玩 | 关注能不能出片 |
| 按地理距离排行程 | 按风格、天气、光线、交通、票务排行程 |
| 输出旅游攻略 | 输出拍摄执行方案 |
| 一次性计划 | 持续根据现场状态修正 |
| 推荐景点 | 推荐 Spot-Time Option |
| 告诉你去哪 | 告诉你几点去哪、站哪里、怎么拍 |

---

# 2. 核心产品定义

## 2.1 典型输入

### 示例 1：目的地 + 风格

```text
我周末从上海去杭州，想拍西湖日系清新旅拍，白裙、湖边、树荫、夕阳，用 iPhone 拍。
```

### 示例 2：只知道想拍什么

```text
我想拍那种海边、蓝天、白裙、阳光、风吹头发的照片，帮我推荐适合去哪拍。
```

### 示例 3：现场动态调整

```text
我晚到了 40 分钟，现在还想尽量拍到夕阳。
```

### 示例 4：天气变化

```text
现在下雨了，原计划还能拍吗？
```

### 示例 5：参考图复刻

```text
我想拍这张图的感觉，下个月去杭州，帮我找类似机位和拍法。
```

---

## 2.2 典型输出

系统最终输出不应该只是建议，而是一份 **旅拍执行方案**：

```markdown
# 杭州西湖日系清新旅拍方案

## 1. 核心结论
建议周六拍摄，13:30 前到杭州东。
下午先拍湖边树荫人像，傍晚转北山街拍湖边街道氛围。
夕阳不一定明显，所以不要把逆光作为唯一目标。

## 2. 到达与购票建议
建议购买 13:30 前到达杭州东的车票。
不建议 14:30 后到达，否则会压缩下午柔光拍摄窗口。

## 3. 推荐路线
13:30 到达杭州东
15:00 柳浪闻莺湖边树荫
16:30 曲院风荷湖边长椅
17:30 北山街傍晚湖边街道

## 4. 机位级拍摄指导
每个机位包含：
- 几点到
- 拍什么
- 人站哪里
- 摄影师站哪里
- 手机几倍焦段
- 怎么构图
- 摆什么动作
- 避开什么

## 5. 动态备用方案
如果下雨，改为雨天江南电影感。
如果晚到 40 分钟，取消曲院风荷，直接保留柳浪闻莺和北山街。
```

---

# 3. 核心 Agent 架构

本项目采用四个核心设计范式：

```text
Plan-and-Execute
负责全局规划

ReAct
负责局部任务执行、工具调用和观察

Spot-Time Option
负责旅拍决策建模

Adaptive Planning Loop
负责根据当前状态持续修正策略
```

---

## 3.1 总体架构图

```text
User Request
  ↓
Goal Parser
  ↓
Strategic Planner 〈Plan-and-Execute〉
  ↓
Task Plan
  ↓
Task Executor 〈ReAct〉
  ├── Reason
  ├── Act: call tools
  ├── Observe
  └── Update State
  ↓
Candidate Discovery
  ├── Reference Research
  ├── Internal Spot DB
  ├── Map POI Search
  └── User-Specified Spot Resolver
  ↓
Spot Fusion & Resolution
  ↓
Real-time Context Collection
  ├── Weather
  ├── Sunlight
  ├── Route Time
  ├── Ticket / Opening Hours
  └── Arrival Advice
  ↓
Visual Goal Builder
  ↓
Spot-Time Option Generator
  ↓
Scoring Engine
  ↓
Route Optimizer
  ↓
Shooting Guide Generator
  ↓
Backup Planner
  ↓
Initial Plan
  ↓
Execution State
  ↓
Adaptive Planning Loop
  ├── Observe current status
  ├── Evaluate plan validity
  ├── Decide adjustment level
  ├── ReAct tool calls if needed
  ├── Re-score remaining options
  └── Output next best action
```

---

# 4. 四个核心设计范式

## 4.1 Plan-and-Execute

### 用途

负责全局规划。

适合处理：

- 初次生成完整旅拍方案
- 多步骤任务拆解
- 多日旅拍计划
- 用户改变目的地
- 用户改变拍摄风格
- 全局路线重规划

### 输出

```json
{
  "global_goal": "为用户生成杭州西湖一日旅拍方案",
  "task_plan": [
    {
      "task_id": "T1",
      "name": "解析用户目标",
      "purpose": "明确目的地、出发地、日期、风格、设备"
    },
    {
      "task_id": "T2",
      "name": "发现候选机位",
      "purpose": "通过参考内容、内置机位库、地图 POI 和用户指定地点发现可拍地点"
    },
    {
      "task_id": "T3",
      "name": "采集实时上下文",
      "purpose": "查询天气、日落、黄金时刻、交通耗时和到达建议"
    },
    {
      "task_id": "T4",
      "name": "构建视觉目标",
      "purpose": "把用户想拍的感觉转成可执行摄影目标"
    },
    {
      "task_id": "T5",
      "name": "生成并评分 Spot-Time Options",
      "purpose": "判断每个机位在不同时间窗口是否适合拍摄"
    },
    {
      "task_id": "T6",
      "name": "优化路线",
      "purpose": "从高分选项中组合出可执行路线"
    },
    {
      "task_id": "T7",
      "name": "生成拍摄指导和备用方案",
      "purpose": "输出具体站位、动作、焦段、构图和风险预案"
    }
  ],
  "success_criteria": [
    "包含推荐到达时间",
    "包含一日路线",
    "每个机位有具体拍摄指导",
    "包含天气备用方案"
  ]
}
```

---

## 4.2 ReAct

### 用途

负责每个任务内部的推理、工具调用、观察和状态更新。

流程：

```text
Reason → Act → Observe → Update
```

例如采集实时上下文时：

```text
Reason:
需要知道杭州周末天气、小时级降水、云量、日落时间、黄金时刻、杭州东到西湖的交通时间。

Act:
调用 weather_tool。

Observe:
周六多云转晴，下午降水概率 10%，云量 45%。

Reason:
天气基本适合日系清新，但夕阳不一定明显，需要查黄金时刻。

Act:
调用 sunlight_tool。

Observe:
日落 18:58，黄金时刻 17:50-18:58。

Reason:
需要判断用户几点前到杭州东合适。

Act:
调用 map_tool 查询杭州东到柳浪闻莺耗时。

Observe:
打车约 35 分钟。

Update:
建议用户 13:30 前到达杭州东，不建议 14:30 后到。
```

---

## 4.3 Spot-Time Option

### 定义

**Spot-Time Option = 某个地点 + 某个时间窗口 + 当前天气光线交通条件 + 一个拍摄目标。**

它是这个系统最核心的决策对象。

---

### 为什么不是直接推荐地点

同一个地点，在不同时间和天气下完全不同：

| 地点 | 时间 | 天气 | 适合情况 |
|---|---|---|---|
| 海边 | 12:00 | 晴天 | 不适合人像，光太硬 |
| 海边 | 17:30 | 晴天 | 适合夕阳、逆光、发丝光 |
| 海边 | 17:30 | 阴天大风 | 不适合白裙清透感 |
| 古镇 | 小雨 | 傍晚 | 适合雨天电影感 |
| 咖啡店窗边 | 阴天 | 下午 | 适合柔和人像 |

所以系统真正应该判断：

```text
这个地点，在这个时间段，当前天气下，是否适合拍用户想要的画面？
```

---

### 示例

```json
{
  "option_id": "opt_001",
  "spot_id": "hangzhou_liulangwenying_lakeside",
  "spot_name": "柳浪闻莺湖边树荫区域",
  "time_window": "15:00-16:10",
  "shoot_goal": "树荫下日系清新人像",
  "expected_visual": ["湖边", "树荫", "白裙", "自然走动"],
  "style_fit": 9.0,
  "visual_element_fit": 8.8,
  "light_fit": 8.2,
  "weather_fit": 8.5,
  "transport_fit": 8.0,
  "risk_score": 7.5,
  "ticket_fit": 10,
  "constraint_fit": 9.0,
  "final_score": 8.8,
  "risks": ["游客较多", "湖边背景可能杂乱"],
  "recommended_shots": [
    "湖边慢走",
    "坐在长椅看湖",
    "树荫下回头",
    "低头整理头发"
  ]
}
```

---

## 4.4 Adaptive Planning Loop

### 用途

让 Agent 根据当前信息不断修正策略。

它不是一次性生成方案，而是持续执行：

```text
Observe 当前状态
  ↓
Evaluate 原计划是否仍成立
  ↓
Decide 继续 / 微调 / 局部重规划 / 全局重规划
  ↓
ReAct 调用必要工具
  ↓
Re-score 剩余 Spot-Time Options
  ↓
Re-plan
  ↓
Output Next Best Action
```

---

### 调整等级

| 等级 | 含义 | 示例 |
|---|---|---|
| continue | 继续原计划 | 只晚了 5 分钟 |
| minor_adjust | 轻微调整 | 缩短当前机位拍摄时间 |
| partial_replan | 局部重规划 | 取消一个机位，保留核心路线 |
| full_replan | 全局重规划 | 天气完全变化，切换风格和路线 |

---

# 5. 系统整体技术架构

## 5.1 技术栈推荐

### 前端

- Next.js
- React
- TypeScript
- Tailwind CSS
- shadcn/ui
- Zustand 或 Jotai
- Mapbox / 高德地图 / Google Maps SDK

### 后端

- Python
- FastAPI
- Pydantic
- Uvicorn

### Agent 编排

- LangGraph

### 数据库

- PostgreSQL
- pgvector
- Redis

### 文件与图片存储

- S3 / Cloudflare R2 / 阿里云 OSS / 腾讯云 COS

### 部署

- Docker
- Docker Compose
- Vercel / Cloudflare Pages 前端
- Render / Fly.io / AWS / 阿里云后端
- Supabase / Neon / RDS PostgreSQL

---

## 5.2 服务模块

```text
apps/web
  前端应用

apps/api
  FastAPI 后端

services/agent
  LangGraph Agent 工作流

services/tools/weather
  天气工具

services/tools/sunlight
  日出日落和黄金时刻工具

services/tools/maps
  地图和路线工具

services/tools/search
  参考内容搜索工具

services/tools/tickets
  到达时间和票务建议工具

services/tools/vision
  参考图分析工具

services/spot
  机位库和机位融合服务

services/scoring
  Spot-Time Option 评分服务

services/planning
  路线优化和重规划服务
```

---

# 6. 项目目录结构

```text
travel-shot-agent/
├── apps/
│   ├── web/
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   ├── stores/
│   │   └── package.json
│   │
│   └── api/
│       ├── app/
│       │   ├── main.py
│       │   ├── api/
│       │   ├── core/
│       │   ├── models/
│       │   ├── schemas/
│       │   ├── services/
│       │   └── db/
│       ├── pyproject.toml
│       └── Dockerfile
│
├── services/
│   ├── agent/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── nodes/
│   │   │   ├── planner.py
│   │   │   ├── react_executor.py
│   │   │   ├── goal_parser.py
│   │   │   ├── candidate_discovery.py
│   │   │   ├── spot_fusion.py
│   │   │   ├── visual_goal.py
│   │   │   ├── spot_time_options.py
│   │   │   ├── scoring.py
│   │   │   ├── route_optimizer.py
│   │   │   ├── evaluator.py
│   │   │   ├── replanner.py
│   │   │   └── formatter.py
│   │   └── prompts/
│   │
│   ├── tools/
│   │   ├── weather.py
│   │   ├── sunlight.py
│   │   ├── maps.py
│   │   ├── search.py
│   │   ├── tickets.py
│   │   └── vision.py
│   │
│   ├── spot/
│   │   ├── repository.py
│   │   ├── fusion.py
│   │   └── seed_data/
│   │
│   ├── scoring/
│   │   └── spot_time_scoring.py
│   │
│   └── planning/
│       ├── route_optimizer.py
│       └── replanner.py
│
├── db/
│   ├── migrations/
│   └── seed/
│
├── docs/
│   ├── product.md
│   ├── architecture.md
│   ├── agent-design.md
│   └── api.md
│
├── docker-compose.yml
├── .env.example
├── README.md
└── package.json
```

---

# 7. 核心数据模型

## 7.1 TravelShotAgentState

```python
from typing import TypedDict, List, Dict, Any, Optional

class TravelShotAgentState(TypedDict):
    # 原始输入
    user_input: str

    # Plan-and-Execute
    global_goal: Dict[str, Any]
    task_plan: List[Dict[str, Any]]
    current_task_id: Optional[str]
    completed_tasks: List[str]
    pending_tasks: List[str]

    # ReAct
    current_reasoning_context: Dict[str, Any]
    tool_calls: List[Dict[str, Any]]
    observations: List[Dict[str, Any]]

    # 用户目标
    parsed_goal: Dict[str, Any]

    # 候选发现
    reference_clues: List[Dict[str, Any]]
    internal_spots: List[Dict[str, Any]]
    map_pois: List[Dict[str, Any]]
    user_specified_spots: List[Dict[str, Any]]
    candidate_spots: List[Dict[str, Any]]

    # 实时上下文
    real_time_context: Dict[str, Any]
    weather_context: Dict[str, Any]
    sunlight_context: Dict[str, Any]
    route_time_context: Dict[str, Any]
    ticket_context: Dict[str, Any]
    arrival_advice: Dict[str, Any]

    # 决策数据
    visual_goal: Dict[str, Any]
    spot_time_options: List[Dict[str, Any]]
    ranked_options: List[Dict[str, Any]]
    optimized_route: List[Dict[str, Any]]

    # 执行状态
    execution_state: Dict[str, Any]
    completed_route_items: List[str]
    skipped_route_items: List[str]
    current_location: Optional[Dict[str, Any]]
    current_time: Optional[str]
    user_feedback: List[str]

    # Adaptive Loop
    plan_validity: Dict[str, Any]
    replan_needed: bool
    replan_scope: Optional[str]
    updated_strategy: Optional[Dict[str, Any]]

    # 输出
    shooting_guides: List[Dict[str, Any]]
    backup_plans: List[Dict[str, Any]]
    final_markdown: str
    next_best_action: Optional[Dict[str, Any]]

    # 错误和警告
    errors: List[str]
    warnings: List[str]
```

---

## 7.2 Parsed Goal

```json
{
  "destination": "杭州",
  "departure_city": "上海",
  "date_range": ["2026-06-06", "2026-06-07"],
  "shooting_style": ["日系清新", "湖边", "自然"],
  "visual_elements": ["白裙", "湖边", "树荫", "夕阳"],
  "subject": ["人像"],
  "equipment": ["iPhone"],
  "platform": ["小红书"],
  "budget": null,
  "constraints": [],
  "missing_fields": []
}
```

---

## 7.3 Candidate Spot

```json
{
  "spot_id": "hangzhou_liulangwenying_lakeside",
  "name": "柳浪闻莺湖边树荫区域",
  "city": "杭州",
  "lat": 30.238,
  "lng": 120.151,
  "spot_type": "湖边公园",
  "source_types": ["reference", "internal_db", "map_poi"],
  "source_confidence": 0.91,
  "geo_verified": true,
  "suitable_styles": ["日系清新", "湖边人像", "自然感"],
  "visual_elements": ["湖边", "树荫", "长椅", "绿植"],
  "best_time_hint": ["15:00-17:00"],
  "weather_preference": ["晴天", "多云"],
  "ticket_required": false,
  "opening_hours": null,
  "crowd_risk": "medium",
  "phone_friendly": true
}
```

---

## 7.4 Visual Goal

```json
{
  "primary_goal": "西湖日系清新人像",
  "must_have_elements": ["湖边", "树荫", "白裙", "自然状态"],
  "optional_elements": ["夕阳", "逆光", "湖面反光"],
  "style_interpretation": {
    "color": ["低饱和", "浅色", "绿色", "米白"],
    "lighting": ["柔和自然光", "侧光", "轻微逆光"],
    "composition": ["留白", "前景遮挡", "人物小比例环境人像"],
    "mood": ["安静", "松弛", "自然"]
  },
  "weather_adaptation": {
    "if_sunny": "强化蓝天、湖面反光和发丝光。",
    "if_cloudy": "强化树荫、长椅、低对比清新感。",
    "if_rainy": "转为雨天江南电影感。"
  },
  "priority_shots": [
    "湖边树荫人像",
    "白裙自然走动",
    "长椅坐姿",
    "傍晚湖边街道氛围"
  ]
}
```

---

## 7.5 Spot-Time Option

```json
{
  "option_id": "opt_001",
  "spot_id": "hangzhou_liulangwenying_lakeside",
  "spot_name": "柳浪闻莺湖边树荫区域",
  "time_window": "15:00-16:10",
  "shoot_goal": "树荫下日系清新人像",
  "expected_visual": ["湖边", "树荫", "白裙", "自然走动"],
  "style_fit": 9.0,
  "visual_element_fit": 8.8,
  "light_fit": 8.2,
  "weather_fit": 8.5,
  "transport_fit": 8.0,
  "risk_score": 7.5,
  "ticket_fit": 10,
  "constraint_fit": 9.0,
  "final_score": 8.8,
  "risks": ["游客较多", "湖边背景可能杂乱"],
  "recommended_shots": [
    "湖边慢走",
    "坐在长椅看湖",
    "树荫下回头",
    "低头整理头发"
  ]
}
```

---

## 7.6 Execution State

```json
{
  "plan_id": "plan_001",
  "current_time": "16:20",
  "current_location": {
    "lat": 30.238,
    "lng": 120.151
  },
  "current_weather": {
    "condition": "小雨",
    "precipitation_probability": 70,
    "wind_speed": 18
  },
  "completed_route_items": [
    {
      "item_id": "route_001",
      "spot": "柳浪闻莺",
      "completed_at": "16:05",
      "completed_shots": ["湖边慢走", "长椅坐姿"]
    }
  ],
  "remaining_route_items": [
    "route_002",
    "route_003"
  ],
  "minutes_behind_schedule": 25,
  "user_feedback": [
    "现在下雨了"
  ],
  "user_status": {
    "energy_level": "medium",
    "battery_level": "medium",
    "outfit_ready": true
  }
}
```

---

# 8. 数据库设计

## 8.1 users

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE,
  name TEXT,
  avatar_url TEXT,
  subscription_tier TEXT DEFAULT 'free',
  plan_limit INT DEFAULT 5,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.2 travel_plans

```sql
CREATE TABLE travel_plans (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  title TEXT,
  destination TEXT,
  departure_city TEXT,
  date_range JSONB,
  shooting_style JSONB,
  visual_elements JSONB,
  subject JSONB,
  platform JSONB,
  equipment JSONB,
  budget INT,
  status TEXT DEFAULT 'draft',
  parsed_goal JSONB,
  visual_goal JSONB,
  final_markdown TEXT,
  plan_json JSONB,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.3 agent_runs

```sql
CREATE TABLE agent_runs (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  run_type TEXT,
  status TEXT,
  input JSONB,
  output JSONB,
  error TEXT,
  warnings JSONB,
  started_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP
);
```

---

## 8.4 agent_steps

```sql
CREATE TABLE agent_steps (
  id UUID PRIMARY KEY,
  run_id UUID REFERENCES agent_runs(id),
  task_id TEXT,
  step_type TEXT,
  reasoning_summary TEXT,
  tool_name TEXT,
  tool_input JSONB,
  tool_output JSONB,
  observation JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.5 photo_spots

```sql
CREATE TABLE photo_spots (
  id UUID PRIMARY KEY,
  city TEXT NOT NULL,
  name TEXT NOT NULL,
  address TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  spot_type TEXT,
  suitable_styles JSONB,
  visual_elements JSONB,
  best_time_hint JSONB,
  weather_preference JSONB,
  ticket_required BOOLEAN DEFAULT FALSE,
  ticket_note TEXT,
  opening_hours JSONB,
  crowd_risk TEXT,
  phone_friendly BOOLEAN DEFAULT TRUE,
  base_photo_score DOUBLE PRECISION,
  shooting_tips JSONB,
  source_type TEXT DEFAULT 'manual',
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.6 reference_clues

```sql
CREATE TABLE reference_clues (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  destination TEXT,
  clue_name TEXT,
  related_spot_name TEXT,
  visual_elements JSONB,
  common_time JSONB,
  common_poses JSONB,
  common_lens JSONB,
  source_summary TEXT,
  confidence DOUBLE PRECISION,
  raw_sources JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.7 candidate_spots

```sql
CREATE TABLE candidate_spots (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  spot_id UUID REFERENCES photo_spots(id),
  name TEXT,
  city TEXT,
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  source_types JSONB,
  source_confidence DOUBLE PRECISION,
  geo_verified BOOLEAN DEFAULT FALSE,
  resolved_data JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.8 spot_time_options

```sql
CREATE TABLE spot_time_options (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  candidate_spot_id UUID REFERENCES candidate_spots(id),
  time_window TEXT,
  shoot_goal TEXT,
  expected_visual JSONB,
  style_fit DOUBLE PRECISION,
  visual_element_fit DOUBLE PRECISION,
  light_fit DOUBLE PRECISION,
  weather_fit DOUBLE PRECISION,
  transport_fit DOUBLE PRECISION,
  risk_score DOUBLE PRECISION,
  ticket_fit DOUBLE PRECISION,
  constraint_fit DOUBLE PRECISION,
  final_score DOUBLE PRECISION,
  risks JSONB,
  recommended_shots JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.9 plan_route_items

```sql
CREATE TABLE plan_route_items (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  option_id UUID REFERENCES spot_time_options(id),
  sequence INT,
  start_time TEXT,
  end_time TEXT,
  item_type TEXT,
  spot_name TEXT,
  shoot_goal TEXT,
  route_note TEXT,
  guide JSONB,
  completed BOOLEAN DEFAULT FALSE,
  skipped BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.10 execution_states

```sql
CREATE TABLE execution_states (
  id UUID PRIMARY KEY,
  plan_id UUID REFERENCES travel_plans(id),
  current_time TEXT,
  current_location JSONB,
  current_weather JSONB,
  completed_route_items JSONB,
  skipped_route_items JSONB,
  remaining_route_items JSONB,
  minutes_behind_schedule INT,
  user_status JSONB,
  user_feedback JSONB,
  plan_validity JSONB,
  next_best_action JSONB,
  updated_strategy JSONB,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 8.11 external_query_cache

```sql
CREATE TABLE external_query_cache (
  id UUID PRIMARY KEY,
  cache_key TEXT UNIQUE,
  provider TEXT,
  query JSONB,
  response JSONB,
  expires_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

# 9. 后端 API 设计

## 9.1 创建计划

```http
POST /api/plans
```

请求：

```json
{
  "user_input": "我周末从上海去杭州，想拍西湖日系清新旅拍，白裙、湖边、树荫、夕阳，用 iPhone 拍。"
}
```

返回：

```json
{
  "plan_id": "uuid",
  "status": "created",
  "parsed_goal": {}
}
```

---

## 9.2 生成初始方案

```http
POST /api/plans/{plan_id}/generate
```

返回：

```json
{
  "plan_id": "uuid",
  "status": "completed",
  "final_markdown": "...",
  "route": [],
  "spot_time_options": []
}
```

---

## 9.3 获取方案

```http
GET /api/plans/{plan_id}
```

---

## 9.4 获取 Spot-Time Options

```http
GET /api/plans/{plan_id}/spot-time-options
```

---

## 9.5 获取路线

```http
GET /api/plans/{plan_id}/route
```

---

## 9.6 开始现场模式

```http
POST /api/plans/{plan_id}/live/start
```

---

## 9.7 更新执行状态

```http
PATCH /api/plans/{plan_id}/execution-state
```

请求：

```json
{
  "current_time": "16:20",
  "current_location": {
    "lat": 30.238,
    "lng": 120.151
  },
  "user_feedback": "现在下雨了"
}
```

---

## 9.8 动态调整

```http
POST /api/plans/{plan_id}/adjust
```

请求：

```json
{
  "reason": "我晚到了 40 分钟，现在还想尽量拍到夕阳",
  "current_location": {
    "lat": 30.238,
    "lng": 120.151
  },
  "current_time": "16:40"
}
```

返回：

```json
{
  "replan_scope": "partial_replan",
  "plan_validity": {},
  "updated_strategy": {},
  "updated_route": [],
  "next_best_action": {}
}
```

---

# 10. Agent 工作流设计

## 10.1 LangGraph 主流程

```text
START
  ↓
goal_parser_node
  ↓
planner_node
  ↓
task_router_node
  ↓
react_executor_node
  ↓
state_update_node
  ↓
plan_validity_evaluator_node
  ↓
conditional_router
   ├── continue_next_task → task_router_node
   ├── minor_adjust → minor_adjust_node
   ├── partial_replan → replanner_node
   ├── full_replan → planner_node
   └── finish → final_formatter_node
```

---

## 10.2 节点职责

### goal_parser_node

解析用户目标，输出 parsed_goal。

### planner_node

生成或更新 task_plan。

### task_router_node

根据 pending_tasks 选择下一个任务。

### react_executor_node

执行当前任务，可以调用工具。

### state_update_node

把工具结果写入状态。

### plan_validity_evaluator_node

判断当前计划是否仍然有效。

### replanner_node

根据状态执行局部或全局重规划。

### final_formatter_node

生成 Markdown 方案、页面结构数据或下一步行动。

---

# 11. 前端页面设计

## 11.1 首页

功能：

- 大输入框
- 示例需求
- 热门目的地
- 热门风格
- 最近方案

示例输入：

```text
我周末从上海去杭州，想拍西湖日系清新旅拍，白裙、湖边、树荫、夕阳，用 iPhone 拍。
```

---

## 11.2 新建计划页

字段：

- 自然语言输入
- 出发地
- 目的地
- 日期范围
- 风格
- 拍摄对象
- 设备
- 预算
- 是否需要查票
- 参考图上传

---

## 11.3 方案详情页

Tabs：

1. 总览
2. 视觉目标
3. 天气与光线
4. 到达与交通
5. 推荐路线
6. Spot-Time Options
7. 机位详情
8. 动作指导
9. 备用方案
10. Checklist
11. 导出

---

## 11.4 Spot-Time Options 页面

展示：

- 机位
- 时间窗口
- 拍摄目标
- 综合评分
- 风格匹配
- 光线匹配
- 天气匹配
- 交通可行性
- 风险
- 是否进入最终路线

这个页面是增强用户信任和调试质量的关键。

---

## 11.5 现场模式页

展示：

```text
当前时间：16:35
当前计划状态：局部风险
下一步建议：直接前往北山街
原因：你已经晚到 25 分钟，曲院风荷会影响傍晚拍摄窗口
```

功能：

- 当前任务卡片
- 下一步行动
- 完成当前机位
- 跳过当前机位
- 突发情况输入
- 动态重规划
- 当前剩余核心画面

---

# 12. 外部工具与服务设计

## 12.1 weather_tool

### 功能

- 当前天气
- 小时级天气
- 降水概率
- 云量
- 风力
- 温度

### 推荐

MVP 使用 Open-Meteo。

---

## 12.2 sunlight_tool

### 功能

- 日出
- 日落
- 黄金时刻
- 蓝调时刻
- 正午强光时间

### 推荐

MVP 可使用 Sunrise-Sunset API 或 Python astral。

---

## 12.3 map_tool

### 功能

- 地址解析
- POI 搜索
- 经纬度
- 路线耗时
- 附近备用点

### 推荐

国内优先高德地图，海外优先 Google Maps / Mapbox。

---

## 12.4 search_tool

### 功能

- 搜索公开攻略
- 搜索旅拍内容
- 提炼参考内容线索

### 注意

不要直接复制博主图片和文案，只做风格、动作、机位线索提炼。

---

## 12.5 ticket_advisor_tool

### 功能

MVP 不做真实出票。

只做：

- 推荐到达时间
- 判断用户已买票是否合适
- 提醒去官方平台确认余票

---

## 12.6 internal_spot_db_tool

### 功能

查询自建机位库。

第一版建议先做：

- 杭州
- 青岛
- 厦门

每个城市 10-20 个高质量机位。

---

# 13. Prompt 与 Agent 节点设计

## 13.1 Planner Prompt

```text
你是旅拍助手 Agent 的 Strategic Planner。
你的任务是根据用户目标生成一个可执行的任务计划。

你使用 Plan-and-Execute 范式。
你不直接输出最终方案，而是拆解任务。

请输出：
- global_goal
- task_plan
- 每个任务的 purpose
- success_criteria

用户输入：
{{user_input}}
```

---

## 13.2 ReAct Executor Prompt

```text
你是旅拍助手 Agent 的 ReAct Executor。
你负责执行当前任务。

你需要遵循：
Reason → Act → Observe → Update

当前任务：
{{current_task}}

当前状态：
{{state}}

可用工具：
{{tools}}

要求：
1. 判断当前任务需要什么信息。
2. 选择合适工具。
3. 根据工具结果更新状态。
4. 如果信息不足，可以继续调用工具。
5. 不要编造工具结果。
6. 输出更新后的结构化结果。
```

---

## 13.3 Spot-Time Option Prompt

```text
你是 Spot-Time Option 生成模块。

请根据候选机位、视觉目标、天气、光线、交通和门票上下文，生成多个拍摄候选项。

每个选项表示：
某个地点，在某个时间窗口，适合拍什么画面。

输出字段：
- option_id
- spot_id
- spot_name
- time_window
- shoot_goal
- expected_visual
- style_fit
- visual_element_fit
- light_fit
- weather_fit
- transport_fit
- risks
- recommended_shots

输入：
candidate_spots: {{candidate_spots}}
visual_goal: {{visual_goal}}
real_time_context: {{real_time_context}}
```

---

## 13.4 Plan Validity Evaluator Prompt

```text
你是计划有效性评估模块。
请判断当前旅拍计划是否仍然成立。

输入：
original_plan: {{original_plan}}
execution_state: {{execution_state}}
current_context: {{current_context}}
remaining_goals: {{remaining_goals}}

请输出：
- validity_score
- status: valid / at_risk / partially_invalid / invalid
- broken_assumptions
- remaining_goal_coverage
- recommended_action: continue / minor_adjust / partial_replan / full_replan
```

---

## 13.5 Replanner Prompt

```text
你是 Adaptive Replanner。
请根据当前状态修正旅拍策略。

你需要判断：
1. 是否继续原计划
2. 是否轻微调整
3. 是否局部重规划
4. 是否全局重规划

输入：
original_plan: {{original_plan}}
execution_state: {{execution_state}}
plan_validity: {{plan_validity}}
remaining_spot_time_options: {{remaining_spot_time_options}}
current_context: {{current_context}}

输出：
- replan_scope
- updated_visual_goal
- updated_route
- next_best_action
- changes_summary
```

---

# 14. 分阶段开发路线

## Phase 0：项目底座搭建

### 目标

让项目能跑起来，完成基础架构。

### 技术栈

- Next.js
- FastAPI
- PostgreSQL
- Redis
- Docker Compose

### 具体步骤

1. 创建项目仓库
2. 创建前端 `apps/web`
3. 创建后端 `apps/api`
4. 配置 Docker Compose
5. 配置 PostgreSQL 和 Redis
6. 配置 `.env.example`
7. 创建 `/health` 接口
8. 前端创建输入页和结果页
9. 后端返回 mock 方案

### 完成标准

- 前端能启动
- 后端能启动
- 数据库能连接
- 用户输入一句需求能看到 mock 方案

---

## Phase 1：静态 MVP 初始规划

### 目标

完成“输入一句需求 → 生成初始旅拍方案”的闭环。

### 技术栈

- LangGraph
- FastAPI
- PostgreSQL
- Open-Meteo
- Sunrise-Sunset API 或 Astral
- 内置机位库

### 具体任务

1. 实现 Goal Parser
2. 实现 Strategic Planner
3. 实现基础 ReAct Executor
4. 建立杭州、青岛、厦门机位库
5. 接入天气查询
6. 接入日出日落查询
7. 实现 Visual Goal Builder
8. 实现 Spot-Time Option Generator
9. 实现 Scoring Engine
10. 实现 Route Optimizer
11. 实现 Shooting Guide Generator
12. 实现 Final Formatter

### 完成标准

用户输入：

```text
我周末从上海去杭州，想拍西湖日系清新旅拍，白裙、湖边、树荫、夕阳，用 iPhone 拍。
```

系统能输出：

- 用户目标总结
- 推荐到达时间
- 天气和光线判断
- 一日路线
- 每个机位拍摄指导
- 备用方案
- Markdown 方案

---

## Phase 2：ReAct 工具增强

### 目标

让 Agent 真正具备工具调用能力，而不是固定流程拼接。

### 技术栈

- LangGraph Tool Nodes
- 外部 API 缓存
- agent_steps 记录表
- search_tool
- map_tool

### 具体任务

1. 封装工具接口
2. 给 ReAct Executor 注册工具
3. 实现工具调用记录
4. 实现 observation 写入
5. 接入地图 POI 查询
6. 接入路线耗时
7. 接入参考内容搜索
8. 增加工具失败降级策略

### 完成标准

- Agent 能根据任务选择工具
- 工具调用过程可记录
- 工具失败不会导致整个方案崩溃
- 方案能体现实时工具结果和不确定性

---

## Phase 3：Adaptive Planning Loop

### 目标

实现动态策略修正能力。

### 技术栈

- execution_states 表
- Plan Validity Evaluator
- Adaptive Replanner
- 剩余 Option 重评分
- 现场模式 API

### 具体任务

1. 创建 execution_states 表
2. 实现现场模式开始接口
3. 实现执行状态更新接口
4. 实现 Plan Validity Evaluator
5. 实现 Replanner
6. 实现调整等级判断
7. 实现 next_best_action 输出
8. 前端实现现场模式页面
9. 支持完成、跳过、突发情况输入

### 完成标准

用户输入：

```text
我晚到了 40 分钟，现在下雨了。
```

系统能输出：

- 原计划哪里失效
- 是微调、局部重规划还是全局重规划
- 哪些机位取消
- 哪些目标保留
- 下一步去哪
- 现在怎么拍

---

## Phase 4：参考内容与机位融合增强

### 目标

提升候选机位质量。

### 技术栈

- search_tool
- Spot Fusion
- 地图 POI 验证
- 机位可信度评分
- pgvector，可选

### 具体任务

1. 实现搜索关键词生成
2. 实现参考内容摘要
3. 实现 shooting_clues 提取
4. 实现机位去重
5. 实现地点实体化
6. 实现地图验证
7. 合并参考内容、内置库、地图 POI、用户指定地点
8. 计算 source_confidence

### 完成标准

- 系统能从公开内容提炼拍摄线索
- 能把模糊线索转成真实地点
- 候选机位池质量明显提升
- 不直接复制外部内容

---

## Phase 5：参考图分析

### 目标

支持用户上传参考图并复刻风格。

### 技术栈

- 图片上传
- 对象存储
- 多模态模型
- 图片 embedding，可选

### 具体任务

1. 实现图片上传
2. 实现参考图风格分析
3. 识别光线、天气、构图、焦段感、动作
4. 将图片分析结果加入 Visual Goal Builder
5. 根据参考图生成类似机位和拍法

### 完成标准

用户上传图片后，系统能输出：

- 图片风格
- 适合天气
- 适合时间
- 可能地点类型
- 构图方式
- 姿势动作
- 可复刻机位路线

---

## Phase 6：产品化和商业化

### 目标

从工具变成产品。

### 技术栈

- Auth
- 支付
- PDF 导出
- 用户历史
- 订阅限制
- 团队协作，可选

### 具体任务

1. 用户账号
2. 历史计划
3. PDF 导出
4. 小红书文案生成
5. 多日规划
6. Pro 功能限制
7. 支付
8. 旅拍摄影师版本
9. 团队协作

### 完成标准

- 有免费版和 Pro 版
- 用户可以保存和导出方案
- 有真实用户可持续使用
- 有付费测试能力

---

# 15. 每阶段如何一步一步搭建

## 15.1 Phase 0 操作步骤

### Step 1：创建 Monorepo

```bash
mkdir travel-shot-agent
cd travel-shot-agent
git init
mkdir -p apps/web apps/api services db docs
```

### Step 2：创建前端

```bash
cd apps
npx create-next-app@latest web --typescript --tailwind --eslint --app
```

### Step 3：创建后端

```bash
cd apps
mkdir api
cd api
uv init
uv add fastapi uvicorn pydantic pydantic-settings python-dotenv sqlalchemy asyncpg alembic redis
```

### Step 4：创建 Docker Compose

包含：

- postgres
- redis

### Step 5：创建环境变量

`.env.example`：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/travelshot
REDIS_URL=redis://localhost:6379
LLM_API_KEY=
OPEN_METEO_BASE_URL=https://api.open-meteo.com
MAPS_API_KEY=
SEARCH_API_KEY=
```

### Step 6：实现 `/health`

```http
GET /health
```

返回：

```json
{
  "status": "ok"
}
```

### Step 7：前端输入页

先实现：

- 文本输入框
- 提交按钮
- mock 结果展示

---

## 15.2 Phase 1 操作步骤

### Step 1：定义 Agent State

创建：

```text
services/agent/state.py
```

### Step 2：实现 Goal Parser

输入用户文本，输出 parsed_goal。

### Step 3：实现 Planner

输出 task_plan。

### Step 4：创建内置机位 seed 数据

先维护 3 个城市：

```text
db/seed/spots_hangzhou.json
db/seed/spots_qingdao.json
db/seed/spots_xiamen.json
```

每个城市 10-20 个机位。

### Step 5：封装 weather_tool

先支持：

- 城市
- 日期
- 小时级天气

### Step 6：封装 sunlight_tool

支持：

- 日出
- 日落
- 黄金时刻
- 蓝调时刻

### Step 7：实现 Spot-Time Option 生成

根据：

- candidate_spots
- visual_goal
- weather
- sunlight

生成多个候选项。

### Step 8：实现评分

先用规则评分，不要一开始完全交给 LLM。

### Step 9：实现路线优化

第一版可以用贪心策略：

1. 优先选择高分 option
2. 时间不冲突
3. 路线不过度绕
4. 日落窗口留给日落类 option

### Step 10：生成 Markdown

输出完整方案。

---

## 15.3 Phase 2 操作步骤

### Step 1：统一工具接口

每个工具统一结构：

```python
class ToolResult(TypedDict):
    success: bool
    data: dict
    error: str | None
    source: str
    fetched_at: str
```

### Step 2：记录工具调用

写入 agent_steps：

- tool_name
- tool_input
- tool_output
- observation

### Step 3：实现地图工具

优先支持：

- geocode
- route_time
- poi_search

### Step 4：实现搜索工具

先只返回：

- 标题
- 链接
- 摘要
- 来源

再由 LLM 提炼 reference_clues。

### Step 5：实现缓存

天气、地图、搜索都要缓存，避免重复调用。

---

## 15.4 Phase 3 操作步骤

### Step 1：创建 execution_state

用户进入现场模式时初始化。

### Step 2：实现完成/跳过机位

更新：

- completed_route_items
- skipped_route_items
- remaining_route_items

### Step 3：实现 Plan Validity Evaluator

判断：

- 是否晚点
- 是否错过时间窗口
- 天气是否变化
- 核心目标是否受影响

### Step 4：实现 Replanner

根据 recommended_action 调整：

- continue
- minor_adjust
- partial_replan
- full_replan

### Step 5：实现 next_best_action

下一步必须非常明确：

```json
{
  "type": "go_to_spot",
  "spot": "北山街",
  "depart_now": true,
  "reason": "当前时间不足，应跳过曲院风荷以保留傍晚窗口"
}
```

---

# 16. MVP 范围与验收标准

## 16.1 MVP 必做

- 自然语言输入
- 目标解析
- Plan-and-Execute 外层任务计划
- ReAct Executor 基础工具调用
- 内置机位库
- 天气查询
- 日出日落查询
- Spot-Time Option
- 评分
- 路线优化
- 拍摄指导
- 备用方案
- Markdown 展示

---

## 16.2 MVP 不做

- 自动购票
- 自动订酒店
- 大规模爬小红书
- 完整移动 App
- 支付
- 团队协作
- 多日复杂路线
- 高精度太阳方位模拟

---

## 16.3 MVP 验收

用户输入后，系统必须输出：

- 用户目标总结
- 是否推荐出发
- 天气和光线判断
- 推荐到达时间
- 一日路线
- 每个机位拍摄目标
- 人物站位
- 摄影师站位
- 手机/相机焦段
- 动作清单
- 风险与备用方案

---

# 17. 测试与评测体系

## 17.1 单元测试

测试：

- Goal Parser
- Spot-Time Option 评分
- 路线时间冲突
- 天气适配逻辑
- Replanner 决策等级
- Prompt JSON 解析

---

## 17.2 集成测试

测试完整链路：

```text
输入用户需求
→ 目标解析
→ 候选机位发现
→ 上下文采集
→ 视觉目标建模
→ Option 生成
→ 评分
→ 路线优化
→ 输出方案
```

---

## 17.3 动态测试

测试：

- 晚到 40 分钟
- 下雨
- 没有夕阳
- 某机位人太多
- 某机位关闭
- 用户体力不足
- 用户临时换风格

---

## 17.4 人工评分标准

| 维度 | 分值 |
|---|---:|
| 是否理解用户目标 | 20 |
| 机位是否匹配风格 | 20 |
| 天气与光线判断是否合理 | 20 |
| 路线是否可执行 | 20 |
| 拍摄指导是否具体 | 10 |
| 动态调整是否合理 | 10 |

80 分以上才算可发布给内测用户。

---

# 18. 风险、合规与降级策略

## 18.1 票务风险

不能承诺：

- 一定有票
- 某车次一定可以买
- 某景点一定有预约名额

只能说：

- 推荐这个时间段到达
- 请以官方购票平台为准
- 如果买不到，使用备用路线

---

## 18.2 内容版权风险

不要直接复制博主图片、视频和文案。

可以做：

- 风格分析
- 机位趋势总结
- 构图和动作提炼
- 链接引用
- 用户上传图片分析

---

## 18.3 安全风险

提醒用户：

- 不要进入禁止区域
- 不要靠近悬崖、海浪、铁轨、车道
- 不要攀爬栏杆
- 夜间拍摄注意安全
- 无人机遵守当地法规

---

## 18.4 工具失败降级

如果天气 API 失败：

- 使用缓存
- 提醒天气信息未更新
- 给出通用方案

如果地图失败：

- 使用内置机位顺序
- 不输出精确交通时间

如果搜索失败：

- 使用内置机位库
- 不编造博主内容

---

# 19. 商业化与后续扩展

## 19.1 免费版

- 每月 3-5 个方案
- 基础城市
- 基础天气和光线
- Markdown 输出

---

## 19.2 Pro 版

- 无限方案
- 更多城市
- 参考图分析
- PDF 导出
- 现场模式
- 多日规划
- 小红书文案生成

---

## 19.3 摄影师版

- 客户版 proposal
- 摄影师执行版
- 姿势引导语
- 镜头清单
- 报价辅助
- 客户管理

---

## 19.4 API 版

面向：

- OTA 平台
- 旅行 App
- 旅拍机构
- 内容社区

提供：

- 机位推荐 API
- 旅拍路线 API
- Spot-Time Option API
- 天气光线评分 API

---

# 20. 最终开发建议

第一阶段一定要小而准。

不要一开始做：

- 自动购票
- 全平台内容抓取
- 复杂地图编辑
- 多日跨城路线
- 商业化系统

先做这个闭环：

```text
输入一句旅拍需求
→ 解析目标
→ 查天气和光线
→ 从内置机位库找候选点
→ 生成 Spot-Time Options
→ 评分
→ 生成一日路线
→ 输出每个机位怎么拍
→ 支持晚到/下雨的动态调整
```

核心产品原则：

> 用户不是要一篇攻略，而是要知道：  
> **我几点到哪里，站在哪里，怎么拍，如果情况变了怎么改。**

最终系统应当是：

```text
Plan-and-Execute 管全局
ReAct 管局部工具调用
Spot-Time Option 管旅拍决策
Adaptive Planning Loop 管动态修正
```

这就是旅拍助手 Agent 的核心竞争力。
