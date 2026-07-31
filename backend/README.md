# Domeye Core 后端

`backend/` 提供 Flask API、固定历史数据查询、国家中断发布快照读取，以及到本机
Agent Sidecar 的窄控制面代理。Web 服务默认不建表、不加载全量离线检测数据，也不
在启动时运行 `backend/core/`。

## 运行边界

- `backend/core/` 是冻结的离线检测核心，完整性由 `core.sha256` 校验；
- 数据查询面只读，不通过 Web API 修改异常检测事实；
- 数据范围、快照时间和时区以
  [`../config/data-profile.json`](../config/data-profile.json) 为准；
- 数据库地址、角色和真实凭据来自受限运行配置，不由 README 复制；
- Agent 报告控制面包含 `POST`，但只创建短生命周期运行、追问或终止操作，不修改
  已发布的事件和观测事实；
- Sidecar 地址必须是无凭据的本机 HTTP/HTTPS 地址，浏览器不能直连 Sidecar；
- `.env`、数据库密码、共享 token、认证文件和真实运行状态均不得进入 Git。

## 目录

```text
backend/
├── config/             Web 与数据库运行参数
├── core/               冻结的离线检测核心
├── database/           只读查询函数
├── info/               Git 外安装或数据库化的基础信息
├── info_pipeline/      INFO 数据迁移和质量门禁
├── services/           事件、P0、特征和国家中断服务
├── utils/              响应整理与兼容读取
├── web/api/            `/api/v1` 路由
├── web/api/v2/         `/api/v2` 路由与 Sidecar 代理
├── web/tests/          API 和响应合同测试
├── pyproject.toml      Python 直接依赖
├── uv.lock             完整依赖锁
├── core.sha256         冻结核心哈希清单
└── run.py              Flask 入口
```

依赖只以 [`pyproject.toml`](pyproject.toml) 和 [`uv.lock`](uv.lock) 为准。

## 安装与启动

本地安装和测试：

```bash
cd backend
uv sync --frozen
uv run --frozen pytest
sha256sum -c core.sha256
```

直接启动：

```bash
uv run --frozen python run.py
```

部署环境通过仓库根部的管理脚本启动受约束 Screen。实际进程目录、Python、
release 和监听端口必须通过
[生产实时库存采集](../deploy/inventory/README.md)确认，不能从本段命令或某个
分支名推断。

`run.py` 默认读取 `backend/.env`，但在受控启动中可以通过
`DOMEYE_CORE_SKIP_LOCAL_ENV=true` 禁止本地文件回填。`AUTO_INIT_DB` 和
`LOAD_CORE_DATA_ON_STARTUP` 默认均为 `false`。

## API 职责

Flask 在 [`web/flask_app.py`](web/flask_app.py) 中注册两个 Blueprint：

- `/api/v1`：只读查询面，当前注册方法均为 `GET`；
- `/api/v2`：国家中断发布快照查询和 Agent 控制面，包含 `GET` 与受约束的
  `POST`。

路径与方法必须同时匹配
[`../contracts/openapi.json`](../contracts/openapi.json)、
[`web/api/route.py`](web/api/route.py) 和
[`web/api/v2/route.py`](web/api/v2/route.py)。

<!-- architecture-docs:api-routes:start -->
| 方法 | 完整路径 | 职责 |
| --- | --- | --- |
| `GET` | `/api/v1/healthz` | Flask 进程健康 |
| `GET` | `/api/v1/p0/status` | P0 数据档和准入状态 |
| `GET` | `/api/v1/p0/metrics/{metric_name}` | P0 指标序列 |
| `GET` | `/api/v1/p0/quality` | P0 质量报告 |
| `GET` | `/api/v1/events` | 六类事件分页和筛选 |
| `GET` | `/api/v1/events/top` | 事件摘要 |
| `GET` | `/api/v1/events/evidence-bundle/{event_type}/{start_time}/{problem}/{event_id}/{source}` | 事件证据包 |
| `GET` | `/api/v1/events/story/{event_type}/{start_time}/{problem}/{event_id}/{source}` | 事件叙事数据 |
| `GET` | `/api/v1/events/observations/{event_type}/{start_time}/{problem}/{event_id}/{source}` | 兼容观测数据 |
| `GET` | `/api/v1/{event_type}/{start_time}/{problem}/{event_id}/{source}` | 六类事件详情 |
| `GET` | `/api/v1/features/top` | 综合特征 |
| `GET` | `/api/v1/features/countries` | 国家特征列表 |
| `GET` | `/api/v1/features/countries/overview` | 国家工作台概览 |
| `GET` | `/api/v1/features/ases` | ASN 特征列表 |
| `GET` | `/api/v1/features/ases/overview` | ASN 工作台概览 |
| `GET` | `/api/v1/features/ases/events` | ASN 近期事件 |
| `GET` | `/api/v1/features/outages/country-as` | 国家 AS 中断时序 |
| `GET` | `/api/v1/features/outages/country-prefix` | 国家前缀中断时序 |
| `GET` | `/api/v1/features/outages/as-prefix` | ASN 前缀中断时序 |
| `GET` | `/api/v1/features/outages/global-as` | 全局 AS 中断时序 |
| `GET` | `/api/v1/features/outages/global-prefix` | 全局前缀中断时序 |
| `GET` | `/api/v1/dashboard/counts/total` | 首页事件总量 |
| `GET` | `/api/v1/dashboard/counts/type` | 首页事件分类 |
| `GET` | `/api/v1/dashboard/overview` | 首页固定窗口概览 |
| `GET` | `/api/v2/events/resolve` | 旧引用解析为发布身份 |
| `GET` | `/api/v2/country-outages/{incident_id}/overview` | 国家中断总览 |
| `GET` | `/api/v2/country-outages/{incident_id}/series` | 国家中断时序 |
| `GET` | `/api/v2/country-outages/{incident_id}/asns` | ASN 状态矩阵 |
| `GET` | `/api/v2/country-outages/{incident_id}/audit` | 发布与质量审计 |
| `GET` | `/api/v2/country-outage/capabilities/external-evidence` | 外部证据能力状态 |
| `POST` | `/api/v2/country-outage/reports` | 创建事件限定报告 |
| `GET` | `/api/v2/country-outage/reports/{report_id}/events` | SSE 状态流 |
| `POST` | `/api/v2/country-outage/reports/{report_id}/questions` | 同快照追问 |
| `POST` | `/api/v2/country-outage/runs/{run_id}/abort` | 终止运行 |
| `GET` | `/api/v2/country-outage/reports/{report_id}/artifacts/{artifact_format}` | Markdown/PDF 下载 |
| `GET` | `/api/v2/country-outage/reports/{report_id}/questions/{question_id}/artifacts/external-appendix` | 外部附录下载 |
<!-- architecture-docs:api-routes:end -->

OpenAPI 中 `/api/v1` 通过顶层 `servers` 声明前缀，`/api/v2` 路径使用完整前缀。
文档校验器会把两者归一化后与 Flask 实际注册路由比较。

## 国家中断 Agent 代理

[`web/api/v2/country_outage_agent_proxy.py`](web/api/v2/country_outage_agent_proxy.py)
是 Flask 到 Sidecar 的唯一 Web 入口。它负责：

- 只接受 `127.0.0.1`、`localhost` 或 `::1` 的无凭据 Sidecar URL；
- 禁止 HTTP 客户端继承代理和 Netrc 环境；
- 校验 Domeye 用户身份、授权 scope、幂等键、事件引用和请求大小；
- 将报告与追问状态以 `text/event-stream` 透传，并关闭 Nginx 缓冲；
- 限制 JSON、Markdown 和 PDF 响应大小；
- 对下载设置 `no-store`、内容类型和安全响应头；
- 将未配置、认证失败、Sidecar 不可用和结果不确定区分为稳定错误。

报告与追问读取固定 `publication_id` 和 `revision`。控制面 `POST` 只管理短生命周期
任务，不改变数据库中的检测结果。外部证据仍是独立能力；未配置时内部报告、追问和
下载路径应保持可用。

## 数据库与只读语义

数据库连接参数在 [`config/database.py`](config/database.py) 中从环境读取。部署
角色应为只读角色，普通请求不建表、不写事实、不执行离线检测。固定历史窗口为：

```text
2026-02-01T00:00:00+08:00 <= t < 2026-04-01T00:00:00+08:00
timezone = Asia/Shanghai
```

实际数据库端口和 release 属于运行身份，不写死在本说明中。生产库存采集也不会
读取数据库凭据或访问数据库。

## 健康检查不是完整验收

`GET /api/v1/healthz` 只证明 Flask 能响应。完整运行验收还应核对：

- `28473` 监听进程是否唯一，`cwd` 是否属于预期不可变 release；
- P0 状态、质量和真实固定窗口查询；
- 六类事件与关键 `/api/v2` 国家中断读取；
- Sidecar 本机端口、Screen、release 和外部证据能力状态；
- Nginx 实际代理与前端树；
- 数据库状态、核心哈希和回滚身份。

这些证据必须按影响范围采集；单个 HTTP 200、历史验收文档或本地测试不能直接升级
为当前生产结论。
