# 服务器真实开发 API

该启动器为本地前端联调和当前 Domeye Core 入口提供服务器上的 2–3 月真实数据。它使用独立 uv 环境、独立信息制品目录和开发数据库，不读取生产 `.env`。默认运行档使用 `31629`；`deploy/manage-fixed-backend.sh` 复用同一套门禁，在现有 `28473` 和 `domeye_core_app` 上启动固定开发档，Nginx 配置无需改变。

固定边界：

| 项目 | 值 |
| --- | --- |
| Screen | `domeye_core_dev_api` |
| API | `127.0.0.1:31629` |
| 数据库 | `domeye_core_dev_pg`，端口来自 verified 状态，默认 `127.0.0.1:31627` |
| 数据窗口 | `2026-02-01 00:00:00 <= t < 2026-04-01 00:00:00` |
| 查询快照时钟 | `2026-03-31 23:59:59` |
| INFO_DIR | `/home/bgpdata/Domeye-Core-dev-data/api/info` |
| 应用日志 | `/home/bgpdata/Domeye-Core-dev-data/api/log/app` |
| uv 环境 | `/home/bgpdata/Domeye-Core-dev-data/api/.venv` |

## 1. 准备并确认开发数据库

首次执行必须先按 [2–3 月开发数据库流程](../database/README.md) 运行固定 `prepare` 命令。它会安全创建 `/home/bgpdata/Domeye-Core-dev-data`；不要为了安装信息制品手工创建或放宽该目录权限。后续只需确认数据库已验证并启动：

```bash
./dev/database/manage-dev-database.sh status
./dev/database/manage-dev-database.sh start
```

API 脚本不会自动创建、启动或修复数据库。启动前它会 fail-closed 验证：

- `state.json` 是固定 2–3 月窗口的 `verified` 状态。
- 容器名、冻结镜像 ID、loopback 端口、OverlayFS 挂载和运行状态都与状态文件一致。
- 后端角色是无超级权限、无写权限且默认事务只读的账号。
- 状态文件记录的验收 SQL 哈希与当前代码完全一致。

数据库的 `prepare`、`start`、`verify` 和 `stop` 会拒绝在开发 API Screen 仍运行时执行。需要复验或启停数据库时，顺序必须是：先停止开发 API，再操作数据库，成功后再启动 API。复验失败只把状态保留为可续跑的 `pruned`，不会重建或删除 Overlay upperdir。

## 2. 准备开发信息制品

首次从已定稿 release 安装四文件信息制品：

```bash
cd /home/bgpdata/Domeye-Core
./dev/backend/stage-dev-info.sh \
  /home/bgpdata/Domeye-Core-artifacts/releases/<release-id>
```

`<release-id>` 必须与开发数据库 `state.json` 的 release-id 一致；当前固定候选库为 `20260717T124354Z`。API 启动会再次比对两者，拒绝混用不同发布的数据和基础信息。

该脚本不调用全量 `verify-release.sh`，不计算 `database.dump.zst` 或数据库镜像的 SHA256。它只会严格验证：

- `manifest.json` 内嵌的 info 组件与 `info-manifest.json` 完全一致。
- `info.tar.zst` 的大小、SHA256、四成员白名单和普通文件类型。
- 解包后每个文件的大小和 SHA256。

发布根目录、release 目录和三个源文件还必须由 root 拥有且不可被组或其他用户写入。安装脚本先把两个小清单复制到受控暂存目录；确认不是已安装制品后，才复制归档并只从该受控副本校验、解包，避免校验与解包之间读取到不同文件。

已安装清单与目标 release 一致、且四文件哈希仍匹配时，脚本会直接复用，不重新解包。若已有目录与目标制品不同，脚本会拒绝覆盖，保留现场供人工复核。

只复验已安装四文件：

```bash
./dev/backend/stage-dev-info.sh --verify-installed
```

## 3. 启停与检查

```bash
./dev/backend/manage-dev-api.sh start
./dev/backend/manage-dev-api.sh status
./dev/backend/manage-dev-api.sh health
./dev/backend/manage-dev-api.sh stop
```

`start` 会在独立开发 `.venv` 中执行 `uv sync --frozen`，然后使用 `env -i` 启动 Screen。运行环境显式注入只读数据库、独立 INFO_DIR、固定数据窗口和快照时钟，并固定：

```text
DOMEYE_CORE_SKIP_LOCAL_ENV=true
DOMEYE_LOG_DIR=/home/bgpdata/Domeye-Core-dev-data/api/log/app
PYTHONDONTWRITEBYTECODE=1
DOMEYE_ENFORCE_DATA_WINDOW=true
AUTO_INIT_DB=false
LOAD_CORE_DATA_ON_STARTUP=false
MAIL_ENABLED=false
```

首次启动还会在开发数据目录生成独立的 32 字节随机 `SECRET_KEY`，以 root 所有、`0600` 权限保存于 `/home/bgpdata/Domeye-Core-dev-data/api/secret-key`；不会读取或复用生产配置中的密钥。当前 Flask 进程仍由 root 所属的独立 Screen 承载，隔离边界依赖回环监听、独立目录、固定进程标记和数据库默认只读权限，不应将该端口转发到非开发环境。

启动验收不只依赖不连数据库的 `/healthz`。健康探针通过后，脚本还会请求固定窗口的事件列表，并要求 HTTP 200 及合法 JSON：

```text
/api/v1/events?datetime=2026-03-31%2000%3A00%3A00_2026-03-31%2023%3A59%3A59&page_num=1&page_size=10
```

这一步会真正使用只读账号连接开发数据库；若查询失败，新启动的 Screen 会被精确停止。

`stop` 只会匹配完整名称 `PID.domeye_core_dev_api` 且带有开发 API 身份标记的 Screen。同名但身份不符、多个同名会话或端口被其他进程占用时均拒绝自动操作。

API 仍只监听服务器回环地址。Mac 本地前端通过下列命令建立 SSH 隧道：

```bash
make dev API_MODE=remote
```
