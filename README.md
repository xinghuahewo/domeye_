# Domeye Core

Domeye Core 是 Domeye 路由异常检测系统的核心精简版。项目保留现有核心检测逻辑和只读查询契约，以独立的基础信息制品、独立 PostgreSQL/TimescaleDB、Flask API 和 Vue 前端组成可单独部署的最小系统。

当前阶段只做运行依赖收口，不增加业务功能，也不实施新的前端视觉方案。`backend/core/` 保持迁移基线逐字节不变。

## 系统能力

- 查询前缀劫持、子前缀劫持、路由泄漏、前缀中断、AS 中断和国家中断六类事件。
- 通过事件总表定位对应月份事实表，展示风险等级、持续时间、影响对象和路径证据。
- 查询全球采集点、国家和 ASN 的报文量、路由资源量及中断时序。
- 提供首页事件总量、分类统计、事件列表、事件详情和特征分析页面。
- 仅注册精简前端使用的只读 GET 接口；登录、研判、通知、报告和任务编排接口保持关闭。

## 独立运行结构

```text
浏览器
  │
  ▼
Nginx :28471
  ├── /              → frontend/dist
  └── /api/v1/*      → Flask 127.0.0.1:28473
                              │
                              ├── PostgreSQL/TimescaleDB 127.0.0.1:29429
                              └── backend/info 四文件基础信息制品

backend/core（离线检测核心，Web 启动时不自动运行）
```

精简版运行时不读取原项目目录、不连接原项目数据库，也不复用原项目进程。原项目的目录、Screen 会话、端口和数据库容器不属于本项目部署脚本的操作范围。

## 数据边界

- 固定数据起点为 `2026-02-01 00:00:00`。
- 数据终点为每个发布制品记录的一致性快照时刻；清单同时保存 UTC 时间和 `Asia/Shanghai` 业务时间。
- 已结束历史月份保留不变；人工刷新时补齐上一制品的当前月，再导入新增月份和新的当前月。
- 基础信息只包含 `important_as.csv`、`as_entity.csv`、`ip_bgp_entity.csv` 和 `country.xlsx`。
- 数据库转储、数据库镜像、基础信息归档和真实 `.env` 均位于 Git 仓库外。
- TimescaleDB 保留策略会在候选库中移除，避免自动删除 2 月 1 日以来的数据。

## 发布制品

每个 release 位于：

```text
/home/bgpdata/Domeye-Core-artifacts/releases/<release-id>/
```

定稿后必须包含八个受 SHA256 保护的文件：

```text
database-image.tar.zst
database-inventory.json
database-manifest.json
database-schema.sql
database.dump.zst
info-manifest.json
info.tar.zst
manifest.json
```

同目录还包含 `SHA256SUMS`。发布校验会检查文件集合、重复项、组件清单与总清单的 release-id、文件名和内嵌哈希，不能通过删减校验行绕过验证。

数据库 inventory 记录 PostgreSQL/TimescaleDB 版本、固定镜像 ID、每张表的行数、最早/最晚业务时间和 schema hash；同时记录公共表白名单、额外用户 schema 表数量，以及逐月集合校验得到的详情引用 `orphan_count=0`。初次构建可读取源库一致性快照，也可导入带可信 TSV 元数据及独立 SHA256 文件的预制完整 dump。后续刷新以最近一次发布为基础，不建立实时复制或双写。

## 目录

```text
Domeye-Core/
├── backend/              Flask API、查询服务、数据库访问和原样迁移的 core
├── frontend/             Vue 3 + TypeScript 精简前端
├── deploy/
│   ├── acceptance/       候选栈、核心冒烟、隔离和完整验收
│   ├── artifacts/        基础信息制品构建、安装、定稿和校验
│   ├── database/         数据库制品、恢复、激活、回滚和 Compose
│   └── nginx/            生产 Nginx 配置
└── README.md
```

后端接口和配置见 [后端说明](backend/README.md)，制品生成、部署、刷新和回滚见 [部署说明](deploy/README.md)。

## 环境要求

- Linux，具备 GNU Bash、GNU coreutils、GNU tar、zstd、jq、curl、Screen 和 Nginx。
- Docker 与 Docker Compose v2。
- PostgreSQL `12.16`、TimescaleDB `2.11.2` 的冻结镜像 `timescaledb:2.11.2-pg12`。
- Python `>=3.10,<3.11` 和 `/home/bgpdata/.local/bin/uv`。
- 项目隔离安装的 Node.js `v22.23.1`，固定目录为 `/home/bgpdata/.local/node-v22.23.1-linux-x64`，不替换系统 Node.js。
- 初次数据库构建建议准备至少 300GB 临时空间。

## 锁定环境验证

后端使用 uv 默认的 `backend/.venv`：

```bash
cd /home/bgpdata/Domeye-Core/backend
/home/bgpdata/.local/bin/uv sync --frozen
/home/bgpdata/.local/bin/uv run --frozen pytest
sha256sum -c core.sha256
```

前端验证：

```bash
cd /home/bgpdata/Domeye-Core/frontend
export PATH=/home/bgpdata/.local/node-v22.23.1-linux-x64/bin:$PATH
node --version  # 必须为 v22.23.1
npm ci
npm test
npm run build
```

`core.sha256` 覆盖 13 个迁移核心文件。任何哈希失败都应中止部署，不得通过更新清单掩盖变化。

## 生产地址和状态

| 用途 | 地址 |
| --- | --- |
| Nginx 前端入口 | `0.0.0.0:28471` |
| Flask API | `127.0.0.1:28473` |
| 独立数据库 | `127.0.0.1:29429` |
| Screen 会话 | `domeye_core_app` |

完成制品恢复和生产激活后：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/status.sh
```

完整验收会先以随机高位端口运行候选数据库、候选后端和临时 Nginx；候选核心接口、SPA 路由及旧目录隔离测试全部通过后，才切换生产。生产切换后的冒烟或隔离验证失败会恢复切换前的四文件信息目录、Domeye Core 数据库链接、`.env`、Nginx 配置和 Screen 后端。

## 当前不处理的内容

- 不修改 `backend/core` 的算法或既有接口行为。
- 不实施参考图对应的前端改版，不新增国家/AS 页面功能。
- 不处理冷查询约 26 秒的性能问题。
- 不迁回认证、权限、多语言、报告导出、通知和非核心运维模块。
