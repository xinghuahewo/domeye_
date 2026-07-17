# Domeye Core

Domeye Core 是从原项目 `/home/bgpdata/Domeye` 独立迁出的路由异常检测核心精简版。新项目部署在 `/home/bgpdata/Domeye-Core`，不覆盖原目录，也不依赖修改原项目分支。

当前版本的目标不是重写检测算法，而是先保留一条可运行、可验证的最小链路：原样迁移 `backend/core`，围绕六类异常提供只读查询 API，再用精简前端展示事件、证据和时序特征。

## 当前保留的能力

- 六类核心异常：前缀劫持、子前缀劫持、路由泄漏、前缀中断、AS 中断、国家中断。
- 事件总表查询、月度事实表详情查询和首页事件统计。
- 全球采集点、国家、ASN 的报文量与资源量特征。
- 全球、国家和 ASN 粒度的中断时序。
- 事件详情中的基础事实、风险等级、持续时间和路径证据。
- Vue 单页前端：首页、事件账本、事件详情、特征分析和 404 页面。
- Flask API 白名单，只开放当前前端需要的 GET 接口。
- `uv` 锁定 Python 环境，Node.js 只用于前端测试和构建。

## 迁移依据与原则

本次迁移以原项目 `docs` 目录的 `00`—`09` 文档，以及 `reports/路由异常检测和识别-技术实现路线-文字版.md` 为范围依据。落实到精简版中的原则是：

1. 以 `BGPRib` 作为共享路由状态底座，保留前缀劫持、子前缀劫持、中断和路由泄漏四条检测路径。
2. 中断检测保持 `prefix → AS → country` 的三级聚合关系，因此对外呈现三类中断事件。
3. 查询链路继续采用“事件总表定位 + 月度事实表取证”的数据组织方式。
4. 前端通过统一请求层访问 `/api/v1/`，不让页面直接耦合后端原始响应格式。
5. 技术路线中提到的多源采集建库、异常分级和证据链用于约束迁移方向；精简版只陈述当前已经迁入并验证的能力。

RPKI、DNS 等报告中的后续增强能力不在本版本承诺范围内。

## 运行结构

```text
浏览器
  │
  ▼
Nginx :28471
  ├── /              → frontend/dist 静态文件
  └── /api/v1/*      → Flask 127.0.0.1:28473
                              │
                              ├── 原有 PostgreSQL 数据库（只读查询）
                              └── /home/bgpdata/Domeye/backend/info（只读复用）

backend/core（离线检测任务，默认不随 Web 服务启动）
```

生产环境沿用原项目的部署拓扑：Nginx 提供前端静态文件并代理 API，后端在独立进程或 GNU Screen 会话中运行。后端只监听回环地址，不直接暴露到外部。

端口特意避开常见开发端口：

| 用途 | 地址 | 说明 |
| --- | --- | --- |
| 前端入口 | `:28471` | 生产环境由 Nginx 监听；开发环境由 Vite 使用 |
| 后端 API | `127.0.0.1:28473` | 仅供本机 Nginx 或开发代理访问 |

## 目录说明

```text
Domeye-Core/
├── backend/              Flask API、查询服务、数据库访问层和原样迁移的 core
├── frontend/             Vue 3 + TypeScript 精简前端
├── deploy/               Nginx、Screen 启停和状态检查配置
└── README.md             项目总览与运行边界
```

后端的详细配置和 API 清单见 [backend/README.md](backend/README.md)，服务器安装、启停和更新步骤见 [deploy/README.md](deploy/README.md)。

## 环境要求

- Linux 服务器。
- Python `>=3.10,<3.11`。
- `uv`，以后端的 `pyproject.toml` 和 `uv.lock` 为依赖真相源。
- Node.js 20 或更高版本，以及 npm。
- 可访问现有 PostgreSQL 数据库；业务查询还需要原项目 `info` 数据目录。
- 生产部署需要 Nginx；Node.js 不参与生产前端运行。

## 后端启动

以下命令以服务器上的目标目录为例：

```bash
cd /home/bgpdata/Domeye-Core/backend
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，填写实际数据库连接信息。若复用原项目配置，应只在服务器上安全复制或人工填写，不要把凭据写入 README、提交记录或版本库。当前阶段通过以下配置只读复用原信息文件：

```dotenv
INFO_DIR=/home/bgpdata/Domeye/backend/info
```

同步锁定环境并启动：

```bash
cd /home/bgpdata/Domeye-Core/backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv sync --locked
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv run --locked python run.py
```

健康检查不依赖数据库或 `info` 文件：

```bash
curl http://127.0.0.1:28473/api/v1/healthz
```

默认的 `AUTO_INIT_DB=false` 和 `LOAD_CORE_DATA_ON_STARTUP=false` 会阻止 Web 服务启动时自动建表或预加载大文件。除健康检查外，事件、统计和特征接口仍需要现有业务数据库；部分特征接口会在首次请求时按需加载原 `info` 文件。

## 前端测试与构建

```bash
cd /home/bgpdata/Domeye-Core/frontend
npm ci
npm test
npm run build
```

构建结果位于 `frontend/dist`。生产环境由 Nginx 直接提供该目录，浏览器通过相对地址 `/api/v1/` 请求后端。

本地开发服务器：

```bash
cd /home/bgpdata/Domeye-Core/frontend
npm run dev
```

Vite 默认监听 `127.0.0.1:28471`，并把 `/api/v1` 代理到 `127.0.0.1:28473`。改变端口时需要同步调整 Vite 与 Nginx 配置，避免前端入口或代理目标不一致。

## 验证

后端测试和核心文件校验：

```bash
cd /home/bgpdata/Domeye-Core/backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv run --locked pytest
sha256sum -c core.sha256
```

`core.sha256` 覆盖本次迁移的 `backend/core` 文件。校验通过表示这些文件仍与迁移基线逐字节一致；后续如果确实需要修改核心算法，应另行立项，而不是在精简迁移阶段直接改写。

前端验证：

```bash
cd /home/bgpdata/Domeye-Core/frontend
npm test
npm run build
```

## 生产部署

首次部署先完成后端锁定环境同步和前端构建，再安装仓库中的 Nginx 配置。服务启停和状态检查使用独立的 `domeye_core_app` Screen 会话，不会匹配原项目的 `app` 会话：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/start-backend.sh
./deploy/status.sh
```

停止精简版后端：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/stop-backend.sh
```

Nginx 配置安装位置、首次部署顺序、日志位置和更新步骤以 [部署说明](deploy/README.md) 为准。安装配置后必须先执行 `nginx -t`，通过后再重新加载 Nginx；构建命令本身不会自动修改系统 Nginx 或启动后端。

## 已删减或关闭的功能

精简版不再注册以下平台型能力：

- 登录、认证和账号管理。
- 事件状态流转、人工研判和通知下发。
- Word 报告导出及报告管理。
- 地理边界、国家拓扑等增强可视化接口。
- 节点状态与运维监控页面。
- 数据查询任务编排。
- 域名增强数据和其他大体量展示数据的启动加载。
- 原完整前端中的管理型页面、旧组件和大体量静态资源。

对应旧接口会返回 `404`，这属于白名单收缩后的预期行为，不是兼容性故障。

## 数据与性能边界

- 当前没有复制原 `info` 目录和业务数据库，新项目通过配置只读复用它们；原项目仍是这些外部数据的维护方。
- Web 启动不会运行 `backend/core` 检测任务，也不会自动初始化数据库结构。
- `backend/core` 保留原任务行为，显式运行其中脚本前必须单独确认输入路径、数据库写入和资源占用。
- 事件查询依赖按月表结构。跨月、较长时间范围或未命中缓存的冷查询可能较慢。
- 首次访问部分国家、ASN 或前缀特征接口时，会按需读取较大的基础信息文件，因此第一次响应可能明显慢于后续请求。
- 当前运行方式保持既有部署习惯，尚未引入新的任务调度、缓存层或数据库迁移机制。

## 安全约定

- `.env` 必须保留在服务器本地并设置严格权限，禁止提交真实凭据。
- 生产后端保持 `HOST=127.0.0.1`、`DEBUG=false`。
- 仅通过 Nginx 暴露前端端口，数据库和 Flask 端口不直接对公网开放。
- 本阶段只读复用原数据；任何建表、检测任务或数据写入都应在单独确认后执行。
