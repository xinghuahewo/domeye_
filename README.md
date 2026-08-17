# Domeye Core

Domeye Core 是 Domeye 路由异常检测系统的核心精简版。项目保留现有核心检测逻辑和只读查询契约，以独立的基础信息制品、独立 PostgreSQL/TimescaleDB、Flask API 和 Vue 前端组成可单独部署的最小系统。

当前进入二三月固定开发阶段。所有真实数据联调只读取 `2026-02-01` 至 `2026-03-31` 的独立只读快照，Domeye Core 不再连接原生产数据库。详细约束见 [二三月固定开发模式](docs/二三月固定开发模式.md)。`backend/core/` 保持迁移基线逐字节不变。

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
  └── /api/v1/*      → 固定开发 Flask 127.0.0.1:28473
                              │
                              ├── PostgreSQL/TimescaleDB 127.0.0.1:31627
                              └── Domeye-Core-dev-data/api/info 四文件制品

backend/core（离线检测核心，Web 启动时不自动运行）
```

精简版运行时不读取原项目目录、不连接原项目数据库，也不复用原项目进程。原项目的目录、Screen 会话、端口和数据库容器不属于本项目部署脚本的操作范围。

## 数据边界

- 固定数据起点为 `2026-02-01 00:00:00`。
- 当前开发数据终点固定为 `2026-03-31T23:59:59+08:00`；业务时区为 `Asia/Shanghai`，后续恢复实时发布前不会自动推进。
- 数据范围、快照时钟和业务时区只在 `config/data-profile.json` 中维护，Mock、开发启动器和部署脚本均读取或校验该文件；端口仍属于部署拓扑配置。
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
│   ├── governance/       服务器分支保护、发布归一检查、安装与夹具
│   └── nginx/            生产 Nginx 配置
└── README.md
```

后端接口和配置见 [后端说明](backend/README.md)，制品生成、部署、刷新和回滚见
[部署说明](deploy/README.md)。任务 Worktree、唯一生产主干、不可变制品晋级和发布后
归一规则见 [主干开发与发布归一治理规范](docs/主干开发与发布归一治理规范.md)。

## 协作与发布主线

`main` 是唯一长期生产主干，`core-work` 是该分支的永久独立 clone。普通功能、
修复、研究和文档任务使用独立短生命周期分支及 Worktree；通过验收的是提交和制品，
不是 Worktree 目录。任务 Worktree 不得直接部署，也不得通过复制兄弟 Worktree 的
未提交文件完成整合。

服务器端保护 Hook 要求 `main` 只能快进到具有评审和 CI 证明的提交，并保护
正式 tag 不被改写。正式发布遵循“一次构建、逐级晋级”：候选、金丝雀与生产使用
同一份不可变制品。发布完成必须证明：

```text
core-work HEAD
= origin/main
= release tag commit
= source archive commit
= Backend、Sidecar、Frontend runtime commit
```

还必须核对实际进程、活动指针、Nginx 和数据库身份。测试通过、提交、评审、合并、
tag、构建、认证、部署和生产验证是不同状态，不能相互替代。版本化 Hook、归一检查
和安装说明见 [治理发布工具](deploy/governance/README.md)。

GitHub Issue、Milestone、Project、Candidate 与 Evidence 的状态同步规则见
[Domeye GitHub 管理与任务收尾同步规则](docs/governance/Domeye_GitHub_Management_Rules_v1.0.md)。
M0/M1 当前唯一的用户旅程与回答边界见
[首个纵向切片锚点合同](docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。
合同落位只表示 Designed；同一 Candidate 的 J1–J5、独立验收和 DG1 决定才可推进
Implemented 或 Verified。

## 本地快速开发

日常前端和接口联调默认使用固定小型快照，不需要配置完整数据库、Nginx、Screen 或生产 `.env`：

```bash
make dev
make dev API_MODE=remote
make check-fast
make preview
make check-integration
make check-release
```

`make dev` 会自动分配不冲突的前端与 API 端口，并在退出时清理临时进程；启动子进程前会清除数据库、生产配置和旧采集路径环境变量。`make risk` 同时输出风险等级、有状态边界、必需检查和是否需要有状态发布。固定快照的数据窗口为 `2026-02-01T00:00:00+08:00 <= t < 2026-04-01T00:00:00+08:00`，支持正常、空数据、超时和错误四类响应，开发环境还提供 `/__components` 组件标本页。

发布命令已按副作用拆为 `release-prepare`、`release-activate`、`release-rollback` 和默认 dry-run 的 `release-gc`；当前固定数据档继续阻止实际生产激活。完整参数和状态机见 [开发与验收流水线](docs/开发与验收流水线.md)。

需要真实数据联调时，服务器可使用 OverlayFS 从已保留候选库派生同一时间窗口的独立开发数据库；`make dev API_MODE=remote` 会通过 SSH 隧道连接只监听服务器回环地址的开发 API，不在本机保存数据库密码。该流程不恢复全量 dump、不复制 84GB PGDATA，说明见 [2–3 月快速开发数据库](dev/database/README.md) 和 [服务器真实开发 API](dev/backend/README.md)。

分层门禁、真实后端模式、OpenAPI 类型生成和完整发布验收边界见 [开发与验收流水线](docs/开发与验收流水线.md)。

项目默认采用“启动 → 看报错 → 最小修改 → 快速测试”的开发循环。只有数据库裁剪/恢复、部署切换、`backend/core/` 或生产配置变更才进入严格验收；脚本解析、清单和普通前端问题一律保留现有数据库并从失败点续跑。

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
| 固定开发 Flask API | `127.0.0.1:28473` |
| 本地联调 Flask API | `127.0.0.1:31629` |
| 二三月只读数据库 | `127.0.0.1:31627` |
| Screen 会话 | `domeye_core_app`、`domeye_core_dev_api` |

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
