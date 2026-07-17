# Domeye Core 后端

`backend/` 是 Domeye 路由异常检测核心精简版的后端。它包含两部分：

- 只读 Flask API：向精简前端提供事件、统计、详情和特征查询。
- 原样迁移的 `core/`：保留离线检测算法与任务入口，但默认不随 Web 服务启动。

完整平台中的认证、研判、通知、报告、地理增强、节点状态和数据任务编排已从 API 注册链路中移除。

## 设计边界

本次后端迁移遵循原项目 `docs/00`—`docs/09` 与技术路线报告中的核心关系：

- `BGPRib` 提供共享路由状态。
- 四条检测路径分别处理前缀劫持、子前缀劫持、中断和路由泄漏。
- 中断由前缀向 AS、国家两层聚合，对外形成前缀中断、AS 中断和国家中断。
- 事件列表从事件总表读取，详情再定位到对应月份的事实表。
- 风险等级、时间信息和路径事实作为精简证据返回。

当前代码不宣称已经实现 RPKI、DNS 等技术路线中的后续增强能力。

## 目录结构

```text
backend/
├── config/                运行参数、数据库连接和日志配置
├── core/                  原样迁移的检测核心与可视化辅助文件
├── database/              API 和核心任务使用的数据库访问函数
├── services/              事件、特征、首页统计三个精简查询服务
├── utils/                 查询整理与按需基础数据加载
├── web/
│   ├── api/               API 白名单及资源实现
│   └── tests/             API、服务边界和迁移完整性测试
├── .env.example           无真实凭据的环境变量示例
├── core.sha256            核心迁移文件哈希清单
├── pyproject.toml          Python 项目与直接依赖
├── uv.lock                锁定的完整依赖图
└── run.py                 Flask 应用入口
```

`requirements.txt` 是原项目遗留的依赖列表，其中仍包含精简版已移除功能的包；不要用它判断当前依赖。安装、同步和验证均以 `pyproject.toml` 与 `uv.lock` 为准。

## 环境与依赖

- Python `>=3.10,<3.11`。
- 使用 `uv` 管理虚拟环境和依赖。
- PostgreSQL 连接用于事件、详情、统计和特征查询。
- 部分特征查询需要原项目 `info` 基础文件。

创建锁定环境：

```bash
cd /home/bgpdata/Domeye-Core/backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv sync --locked
```

不要使用未锁定的 `pip install -r requirements.txt` 作为正式部署流程，否则运行环境可能偏离已验证版本。

## 配置

`run.py` 会先读取同目录的 `.env`。进程环境中已经存在的变量优先级更高，不会被 `.env` 覆盖。

首次配置：

```bash
cd /home/bgpdata/Domeye-Core/backend
cp .env.example .env
chmod 600 .env
```

常用变量：

| 变量 | 默认值或建议值 | 说明 |
| --- | --- | --- |
| `FLASK_CONFIG` | `production` | 非测试模式会执行运行时初始化判断 |
| `HOST` | `127.0.0.1` | 后端仅向本机代理开放 |
| `PORT` | `28473` | Flask API 端口 |
| `DEBUG` | `false` | 生产环境必须关闭调试 |
| `AUTO_INIT_DB` | `false` | 是否在启动时初始化数据库结构 |
| `LOAD_CORE_DATA_ON_STARTUP` | `false` | 是否在启动时预加载基础数据 |
| `INFO_DIR` | `/home/bgpdata/Domeye/backend/info` | 当前阶段只读复用原信息文件 |
| `DB_HOST` 等 | 无可提交默认值 | 现有 PostgreSQL 连接信息 |
| `SOURCE` | `r` | 数据来源标识 |
| `MODE` | `1` | 核心任务模式；Web 查询不会主动运行任务 |

`.env.example` 中的数据库字段只是占位符。真实密码、SSH 信息和邮件凭据不得写入文档或提交到版本库。

当前精简部署建议保持：

```dotenv
HOST=127.0.0.1
PORT=28473
DEBUG=false
AUTO_INIT_DB=false
LOAD_CORE_DATA_ON_STARTUP=false
INFO_DIR=/home/bgpdata/Domeye/backend/info
```

这组配置只限制 Web 启动行为，不会把数据库账号技术性降权为只读账号。是否具备写权限仍由 PostgreSQL 账号本身决定，因此不要通过 Web 部署流程运行建表或核心检测脚本。

## 启动与健康检查

```bash
cd /home/bgpdata/Domeye-Core/backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv run --locked python run.py
```

服务默认监听 `127.0.0.1:28473`：

```bash
curl http://127.0.0.1:28473/api/v1/healthz
```

健康接口独立于数据库和基础文件，可用于判断 Flask 进程是否可用。其他业务接口需要数据库连接；特征接口还可能按需加载 `INFO_DIR` 中的数据。

## API 白名单

所有接口均为 GET 请求，并统一使用 `/api/v1` 前缀。

| 路径 | 作用 | 主要参数 |
| --- | --- | --- |
| `/healthz` | 进程健康检查 | 无 |
| `/events` | 六类异常事件分页与筛选 | `page_num`、`page_size`、`event_type`、`level`、`country`、`event_info`、`date` 等 |
| `/events/top` | 最新核心事件 | `event_type` |
| `/<event_type>/<start_time>/<problem>/<event_id>/<source>` | 按详情引用读取月度事实表 | 路径参数 |
| `/features/top` | 全球采集点、国家或 ASN 的特征时序 | `target`、`start_time`、`end_time` |
| `/features/countries` | 国家特征列表 | `country`、时间范围、分页参数 |
| `/features/ases` | ASN 特征列表 | `asn`、`country`、时间范围、分页参数 |
| `/features/outages/country-as` | 国家 AS 中断时序 | `country`、时间范围 |
| `/features/outages/country-prefix` | 国家前缀中断时序 | `country`、时间范围 |
| `/features/outages/as-prefix` | 指定 ASN 的前缀中断时序 | `asn`、时间范围 |
| `/features/outages/global-as` | 全局 AS 中断时序 | 时间范围 |
| `/features/outages/global-prefix` | 全局前缀中断时序 | 时间范围 |
| `/dashboard/counts/total` | 首页事件总量时序 | 可选 `country` |
| `/dashboard/counts/type` | 指定异常类型统计 | `event_type` |

时间范围参数的格式为 `YYYY-MM-DD HH:MM:SS`。事件列表的 `date` 使用 `开始日期_结束日期`。事件详情通常由列表响应中的详情引用进入，前端统一请求层负责解析和规范化，不建议手工拼接包含前缀的详情路径。

以下旧接口不会注册，并应返回 `404`：

- `/api/v1/login`
- `/api/v1/events/state`
- `/api/v1/events/judge`
- `/api/v1/events/notify`
- `/api/v1/reports/word-export`
- `/api/v1/geodata/boundaries`
- `/api/v1/node-status`
- `/api/v1/data-query/tasks`

## 启动行为

`create_app()` 只构建 Flask 应用并注册白名单路由。默认配置下：

1. 不执行 `init_db.auto_init_db()`。
2. 不执行全量 `utils.data_loader.init_global_data()`。
3. 不运行 `core/` 中的离线检测任务。
4. 只有业务查询真正需要基础信息时，才调用按需加载逻辑。

因此健康检查可以在数据库或 `info` 尚不可用时启动成功；这不代表其他业务接口也能脱离数据源工作。

## 核心迁移完整性

`core/` 当前保持迁移时的核心逻辑，不在精简阶段重写。可用哈希清单验证：

```bash
cd /home/bgpdata/Domeye-Core/backend
sha256sum -c core.sha256
```

清单覆盖 `BGPDetection.py`、`BGPRib.py`、各类检测器、特征与资源脚本，以及保留的可视化辅助文件。任何校验失败都应先按迁移完整性问题处理，不应直接更新哈希掩盖差异。

如需与原目录做只读对比：

```bash
diff -qr /home/bgpdata/Domeye/backend/core /home/bgpdata/Domeye-Core/backend/core
```

核心任务仍保留原有文件路径、数据库和资源行为。Web 服务不会调用它们；显式运行前应单独审查输入路径、写库目标、数据规模和运行窗口。

## 测试

```bash
cd /home/bgpdata/Domeye-Core/backend
UV_PROJECT_ENVIRONMENT=venv /home/bgpdata/.local/bin/uv run --locked pytest
```

测试覆盖：

- API 路由白名单。
- 已移除平台接口返回 `404`。
- 健康检查不依赖数据库和基础文件。
- 事件、统计和特征服务的响应边界。
- `core.sha256` 中所有迁移文件的哈希一致性。

## 数据和性能限制

- 新项目不复制原数据库，也不维护原 `info` 目录；当前阶段只读复用这些外部资源。
- 业务查询仍受现有月度表、字段格式和数据库索引影响。
- 跨月事件查询、较大时间范围和未缓存的冷查询可能较慢。
- 国家、ASN 和前缀特征的首次查询可能触发大文件加载，第一次响应会慢于后续请求。
- 启动时关闭预加载是为了缩短进程启动并隔离健康检查，不等于消除了业务查询的数据成本。
- 认证、写操作、人工研判、报告、通知、地理增强和任务调度不属于当前后端契约。
