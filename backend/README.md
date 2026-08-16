# Domeye Core 后端

`backend/` 提供精简版只读 Flask API，并保留原样迁移的 `core/` 离线检测实现。Web 服务默认不建表、不加载全量核心数据，也不启动离线检测任务。

## 运行边界

- `backend/core/` 不允许在本阶段修改，完整性由 `core.sha256` 校验。
- API 仅连接本项目独立数据库 `127.0.0.1:29429`。
- 特征接口只从本项目 `backend/info` 读取四个基础信息文件，拒绝软链接文件。
- 后端数据库角色为 `domeye_core_reader`，默认事务只读且只授予白名单表查询权限。
- `AUTO_INIT_DB=false`、`LOAD_CORE_DATA_ON_STARTUP=false` 必须保持关闭。
- 邮件、SSH、采集路径和原平台凭据不进入精简版生产 `.env`。

## 目录

```text
backend/
├── config/             运行参数和数据库连接
├── core/               原样迁移的检测核心
├── database/           数据库查询函数
├── info/               Git 外安装的四文件基础信息制品
├── services/           事件、特征和仪表盘查询服务
├── utils/              响应整理与按需基础数据加载
├── web/api/            API 白名单
├── web/tests/          接口和响应契约回归测试
├── .env.example        脱敏生产配置模板
├── core.sha256         核心文件哈希清单
├── pyproject.toml      Python 直接依赖
├── uv.lock             完整依赖锁
└── run.py              Flask 入口
```

`pyproject.toml` 和 `uv.lock` 是唯一依赖真相源；正式部署不使用 `requirements.txt`。

## 环境安装

```bash
cd /home/bgpdata/Domeye-Core/backend
/home/bgpdata/.local/bin/uv sync --frozen
```

uv 默认创建并管理 `/home/bgpdata/Domeye-Core/backend/.venv`，不再通过 `UV_PROJECT_ENVIRONMENT` 建立另一个 `venv` 目录。

## 生产配置

普通手工启动前可从模板创建配置；正式部署建议使用 `deploy/database/configure-backend-env.sh` 从受限数据库配置生成：

```bash
cd /home/bgpdata/Domeye-Core/backend
cp .env.example .env
chmod 600 .env
```

关键变量：

| 变量 | 生产值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 独立随机值 | 不得使用模板占位值 |
| `HOST` | `127.0.0.1` | 只供本机 Nginx 访问 |
| `PORT` | `28473` | Flask 端口 |
| `DEBUG` | `false` | 关闭调试和重载器 |
| `AUTO_INIT_DB` | `false` | 禁止 Web 启动建库 |
| `LOAD_CORE_DATA_ON_STARTUP` | `false` | 基础数据按需加载 |
| `SOURCE` | `r` | 查询数据源标识 |
| `INFO_DIR` | `/home/bgpdata/Domeye-Core/backend/info` | 四文件制品安装目录 |
| `DB_HOST` | `127.0.0.1` | 独立数据库仅监听回环 |
| `DB_PORT` | `29429` | 独立数据库端口 |
| `DB_NAME` | `bgp_project` | 独立数据库名称 |
| `DB_USER` | `domeye_core_reader` | 默认只读角色 |
| `DB_PASSWORD` | 受限配置生成 | 禁止提交 |
| `MAIL_ENABLED` | `false` | 精简版关闭邮件 |

`run.py` 使用简单的 `KEY=VALUE` 解析器，不处理 shell 引号；部署脚本生成的 `.env` 因此使用无引号且无空白字符的安全值。

## 启动

生产环境使用根目录下的 Screen 脚本：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/start-backend.sh
./deploy/status.sh
```

直接调试启动：

```bash
cd /home/bgpdata/Domeye-Core/backend
/home/bgpdata/.local/bin/uv run --frozen python run.py
```

健康检查：

```bash
curl --fail http://127.0.0.1:28473/api/v1/healthz
```

健康接口只表示 Flask 进程可用。部署激活还会使用只读角色查询非空 `feature_country` 超表，并请求真实 ASN 特征接口，不能只凭健康接口判断数据库迁移成功。

## API 白名单

所有接口均为 GET，并使用 `/api/v1` 前缀。

| 路径 | 作用 |
| --- | --- |
| `/healthz` | 进程健康检查 |
| `/events` | 六类异常事件分页与筛选 |
| `/events/top` | 最新核心事件 |
| `/<event_type>/<start_time>/<problem>/<event_id>/<source>` | 六类月度事实表详情 |
| `/features/top` | 采集点、国家或 ASN 综合特征 |
| `/features/countries` | 国家特征列表 |
| `/features/ases` | ASN 特征列表 |
| `/features/outages/country-as` | 国家 AS 中断时序 |
| `/features/outages/country-prefix` | 国家前缀中断时序 |
| `/features/outages/as-prefix` | ASN 前缀中断时序 |
| `/features/outages/global-as` | 全局 AS 中断时序 |
| `/features/outages/global-prefix` | 全局前缀中断时序 |
| `/dashboard/counts/total` | 首页事件总量 |
| `/dashboard/counts/type` | 指定事件类型统计 |

时间参数采用 `YYYY-MM-DD HH:MM:SS`。发布清单固化了六类非空事件详情、非空中断窗口和 ASN `1299` 特征窗口，部署冒烟从清单读取这些锚点。

登录、研判、通知、报告、地理增强、节点状态和数据任务接口不注册，访问应返回 `404`。

## 测试与核心完整性

```bash
cd /home/bgpdata/Domeye-Core/backend
/home/bgpdata/.local/bin/uv run --frozen pytest
sha256sum -c core.sha256
```

测试固定字段、类型、分页、枚举、可空性和错误响应，不固定实时数值。数据库发布 inventory 另行固定每张表的行数、时间边界和 schema hash，并要求表白名单完整、无额外用户表、所有事件详情引用格式有效且 `orphan_count=0`。

## 数据与性能

- 业务数据只保留 `2026-02-01 00:00:00` 至发布业务快照时刻。
- 源库会话使用 UTC，而业务 `timestamp without time zone` 按 `Asia/Shanghai` 写入；裁剪脚本以业务时间上界处理，避免误删最新八小时。
- ASN 特征只保留 `feature_other` 与十个大国月表族；PostgreSQL 未加引号标识符统一为小写。
- 事件总表及六类事实表按月保留；发布当前月缺表时从同族最近表创建空表，不制造数据。
- 首次加载较大的 AS、前缀元数据以及冷数据库查询仍可能较慢，本阶段不做性能改写。
