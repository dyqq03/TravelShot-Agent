# TravelShot Agent

旅拍助手 Agent 是一个本地可运行的 AI 旅拍规划 Demo。用户输入目的地、日期、拍摄风格或参考图后，系统会结合 LLM、天气、日照、地图、搜索和内置机位库，生成可执行的旅拍路线、拍摄建议、风险提示和追问调整结果。

这个仓库已整理为适合本地演示和上传 GitHub 的版本：不包含 `.env`、日志、依赖安装目录、Python 缓存和本地运行产物。

## Demo 看点

- 多轮 Agent 规划：LLM 先理解需求，再按工具结果继续决策。
- 工具白名单：天气、日照、搜索、地理编码、高德 POI 和路线规划都经过后端校验。
- 可追溯轨迹：前端展示 LLM/工具步骤、失败原因和耗时。
- 参考图理解：支持最多 3 张 JPG/PNG/WebP，会在浏览器压缩并由后端校验真实图片类型。
- 历史与追问：历史方案可打开、删除、继续追问；追问会追加到原方案对话里。
- Demo 体验优化：同样输入会命中最终方案缓存；历史 7 天未访问自动清理；生成中防重复触发。

## 技术栈

- Frontend: Next.js, React, TypeScript
- Backend: FastAPI, Pydantic, asyncpg
- Agent: OpenAI-compatible Chat Completions API, strict JSON contracts, tool loop
- Storage: PostgreSQL, Redis
- Tools: Open-Meteo, Astral, Tavily, Nominatim, Amap

## 项目结构

```text
apps/
  api/                  FastAPI 后端与 Agent 编排
  web/                  Next.js 前端工作台
db/
  schema.sql            PostgreSQL 表结构
  seed/spots/           内置城市旅拍机位数据
  scripts/              seed 导入与坐标补全脚本
docs/                   阶段性开发记录
docker-compose.yml      本地 PostgreSQL/Redis/API/Web 编排
旅拍助手Agent可落地完整设计方案.md
```

## 快速开始

### 1. 准备环境变量

复制示例配置：

```bash
cp .env.example .env
```

至少填写：

```env
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

可选增强：

```env
VISION_API_KEY=your_vision_key
AMAP_API_KEY=your_amap_key
TAVILY_API_KEY=your_tavily_key
NOMINATIM_EMAIL=you@example.com
```

没有地图/搜索 key 时，系统会显示降级提示；没有 LLM key 时不会生成伪方案。

### 2. 启动 PostgreSQL 和 Redis

```bash
docker compose up postgres redis
```

默认端口只绑定本机：

- PostgreSQL: `127.0.0.1:5433`
- Redis: `127.0.0.1:6380`

### 3. 安装依赖

```bash
pip install -e apps/api
npm install --cache .npm-cache
```

### 4. 启动服务

打开一个终端启动 API：

```bash
npm run dev:api
```

再打开一个终端启动 Web：

```bash
npm run dev:web
```

访问：

- Web: http://localhost:3000
- API health: http://localhost:8000/health

## 推荐演示路径

1. 打开 Web 页面，点击北京或厦门示例。
2. 点击“生成方案”，等待系统生成最终路线和拍摄方案。
3. 查看最终方案、路线、风险提示和“工具轨迹”。
4. 追问一句：`如果下午下雨，路线怎么调整？`
5. 打开历史记录，展示原方案和追问都保留在同一个会话里。
6. 再次提交相同输入，展示最终方案缓存命中，生成速度明显更快。

## 核心流程

```text
用户输入/参考图
  -> 创建 travel_plans 记录
  -> LLM 意图分析，输出严格 JSON
  -> 后端校验工具请求
  -> 执行天气、日照、搜索、地图等工具
  -> LLM 基于工具证据生成方案
  -> 写入路线、工具轨迹、最终 Markdown
  -> 前端展示结果并支持追问
```

## 关键环境变量

```env
AGENT_MAX_LLM_CALLS=7
AGENT_MAX_TOOL_ROUNDS=4
AGENT_MAX_TOOL_REQUESTS_PER_BATCH=10
AGENT_MAX_ROUTE_REQUESTS=4
PLAN_CACHE_TTL_SECONDS=86400
HISTORY_RETENTION_DAYS=7
TOOL_CACHE_TTL_SECONDS=1800
```

- `PLAN_CACHE_TTL_SECONDS`：相同输入和参考图在有效期内会复用已完成方案。
- `HISTORY_RETENTION_DAYS`：历史方案超过 N 天未打开或追问会自动清理。
- `AGENT_MAX_*`：控制 LLM 和工具调用上限，避免无限循环和费用失控。

## 数据表

- `photo_spots`：内置机位库。
- `travel_plans`：用户需求、解析结果、最终方案、缓存 hash。
- `agent_steps`：LLM 和工具执行轨迹，包含耗时。
- `plan_route_items`：最终路线。
- `plan_messages`：追问对话历史。
- `spot_time_options`：兼容保留的候选时段表。

## 校验

```bash
python -m compileall apps/api/app
npm run lint:web
```

## 后续可优化

- 真实流式进度：用 SSE 或轮询接口替代当前前端预计进度。
- 生产部署：增加用户登录、对象存储、请求体大小限制和 Redis 限流。
- 演示资产：补充 README 截图或 GIF，面试展示会更直观。
