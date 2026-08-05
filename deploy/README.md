# Domeye Core 独立部署说明

本目录负责构建、恢复、验收和切换 Domeye Core 的独立运行依赖。所有生产路径均固定在 Domeye Core 自身目录或 Git 外数据目录；脚本不会停止、修改或写入原项目。

> 当前活动数据档为 `feb-mar-2026`。Domeye Core 只使用 `127.0.0.1:31627` 的二三月只读库；传统实时后端启动、数据库激活、回滚和直接源库构建均会失败关闭。当前启停与恢复条件见 [二三月固定开发模式](../docs/二三月固定开发模式.md)。下文 `29429` 发布流程保留供未来经明确确认恢复实时分析时使用，当前不可执行。

## 固定参数

| 项目 | 配置 |
| --- | --- |
| 项目目录 | `/home/bgpdata/Domeye-Core` |
| 发布制品 | `/home/bgpdata/Domeye-Core-artifacts/releases/<release-id>` |
| 数据根目录 | `/home/bgpdata/Domeye-Core-data` |
| 活动数据库路径 | `/home/bgpdata/Domeye-Core-data/postgres`，指向版本目录的软链接 |
| 前端入口 | `0.0.0.0:28471` |
| Flask API | `127.0.0.1:28473` |
| PostgreSQL | `127.0.0.1:29429` |
| Screen 会话 | `domeye_core_app` |
| 数据库容器 | `domeye_core_pg` |
| 数据库资源 | 内存上限 16GiB，`shared_buffers=4GB`，共享内存 4GiB，TimescaleDB telemetry 关闭 |
| uv 环境 | `/home/bgpdata/Domeye-Core/backend/.venv` |
| Node.js | `/home/bgpdata/.local/node-v22.23.1-linux-x64/bin`，固定 `v22.23.1` |

生产数据库端口只绑定回环。数据库镜像固定为 `timescaledb:2.11.2-pg12`，构建和启动都会校验具体 image ID，不接受同标签漂移。

## 源码、制品与生产身份

部署目录不是 Git 主干。唯一生产主干、任务 Worktree 和发布归一规则见
[主干开发与发布归一治理规范](../docs/主干开发与发布归一治理规范.md)。正式发布只
接受 `codex/prod` 上 annotated tag 对应的明确提交，并从最终提交生成一次不可变
统一候选；候选、金丝雀和生产逐级晋级同一份后端、Sidecar 和前端制品。

每次发布必须在仓库外的 release 证据中绑定：

- release ID、annotated tag 和解引用后的 Git commit；
- 源码归档 SHA-256 与源码文件清单摘要；
- Backend、Sidecar、Frontend 的 release ID、路径、源码绑定和目录摘要；
- `backend/core.sha256` 校验结果；
- 模型注册表、认证 evidence ID 或本次不调用模型的明确边界；
- Nginx 配置摘要、数据档和数据库状态摘要；
- 构建工具链版本和生成时间。

生产服务器 `/home/bgpdata/Domeye-Core` 的源码 checkout 只用于只读诊断、接收受保护
Git 引用和运行版本化发布入口。不能以该 checkout 的当前分支、tag 可见性或工作树
脏净状态证明生产身份；生产身份从 runtime release 的源码绑定、活动指针和实际
进程取得。

生产验证结束时，Backend 与 Sidecar 的实际进程 release ID 必须分别等于各自
`current`，Frontend 线上文件必须与候选 `dist` 逐字节一致，Nginx 和数据库摘要
必须等于批准值。任一不一致都属于 `deployed_identity_drift`，不得写成
`verified`。服务器端保护、归一检查与安装回执见
[治理发布工具](governance/README.md)。

## 目录职责

- `artifacts/`：构建和安装四文件信息制品，原子安装/回滚前端构建，定稿并校验八文件发布集合。
- `database/`：生成、刷新、恢复、激活和回滚独立数据库。
- `acceptance/`：运行随机端口候选栈、核心 API 冒烟、SPA 刷新和旧目录隔离检查。
- `governance/`：版本化服务器端分支保护 Hook、发布归一检查、原子安装和夹具。
- `nginx/`：生产前端和 API 代理配置。
- `start-backend.sh`、`stop-backend.sh`、`status.sh`：只管理 Domeye Core Screen 和状态。

## 1. 准备受限配置

数据库配置位于仓库外：

```bash
install -d -m 0700 /home/bgpdata/Domeye-Core-data/config
install -m 0600 \
  /home/bgpdata/Domeye-Core/deploy/database/database.env.example \
  /home/bgpdata/Domeye-Core-data/config/database.env
```

编辑 `database.env`，至少替换管理员密码、只读密码和 `DOMEYE_CORE_SECRET_KEY`。所有密码和 SECRET_KEY 不得包含空白字符。

若从源数据库生成初始或刷新制品，再在仓库外准备只读源库配置：

```bash
install -m 0600 \
  /home/bgpdata/Domeye-Core/deploy/database/source.env.example \
  /home/bgpdata/Domeye-Core-data/config/source.env
```

源库只在离线制品生成时读取，生产服务不使用该配置。权限边界必须按模式区分：直接初始构建会执行完整 `pg_dump`，账号必须能读取整个源数据库及 TimescaleDB chunks；后续增量刷新只导出白名单月表和 `feature_country` 重叠窗口，可使用仅具连接、schema 使用和目标表 SELECT 权限的受限账号。

## 2. 构建基础信息制品

release-id 使用 UTC 紧凑时间，可附加小写后缀：

```bash
release_id=20260717T124354Z
```

初次构建只读四个明确文件：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/artifacts/build-info-artifact.sh \
  /home/bgpdata/Domeye/backend/info \
  "${release_id}"
```

脚本复制到临时目录后计算每个文件的大小、记录规模和 SHA256，再生成确定性 `info.tar.zst`。来源目录及其文件不会被修改。

## 3. 构建初始数据库制品

### 方式 A：使用预制一致性 dump

预制 dump 必须配套 `source-full-dump.tsv` 和独立 `.sha256`。现行 TSV 只能有固定表头和一行数据：

```text
release_id<TAB>dump_started_at_utc<TAB>dump_completed_at_utc<TAB>source_database<TAB>source_database_size_bytes<TAB>postgresql_version<TAB>image_id<TAB>dump_name<TAB>dump_size_bytes<TAB>dump_sha256
```

要求：

- `release_id` 与目标发布一致。
- 文件名、字节数和 SHA256 与 dump 本体一致。
- PostgreSQL 版本为 `12.16`。
- image ID 与本机冻结镜像一致。
- `dump_started_at_utc` 和 `dump_completed_at_utc` 必须为 UTC `Z` 时间，完成时间不得早于开始时间；快照锚点使用开始时间，例如 `2026-07-17T12:50:03Z`。
- 上海业务快照时间由脚本自动加八小时计算，本例为 `2026-07-17 20:50:03`，不接受手工填写。

构建命令：

```bash
cd /home/bgpdata/Domeye-Core
./deploy/database/build-database-artifact.sh \
  - \
  /home/bgpdata/Domeye-Core-data/config/database.env \
  "${release_id}" \
  /home/bgpdata/Domeye-Core-artifacts \
  '' \
  /home/bgpdata/Domeye-Core-artifacts/work/${release_id}/source-full.pg12.custom.dump \
  /home/bgpdata/Domeye-Core-artifacts/work/${release_id}/source-full-dump.tsv \
  /home/bgpdata/Domeye-Core-artifacts/work/${release_id}/source-full.pg12.custom.dump.sha256
```

dump 可以是 PostgreSQL custom format 原文件，也可以外层使用 zstd。输入必须放在权限受限的 `artifacts/work/<release-id>/` staging，不得混入最终 release 目录。脚本只把来源文件名、大小、SHA256、时间和元数据哈希写入 provenance；最终恢复与发布校验成功后删除 staging 中的大 dump，仅保留正式八文件和 `SHA256SUMS`。

### 方式 B：直接创建源库一致性快照

```bash
cd /home/bgpdata/Domeye-Core
./deploy/database/build-database-artifact.sh \
  /home/bgpdata/Domeye-Core-data/config/source.env \
  /home/bgpdata/Domeye-Core-data/config/database.env \
  "${release_id}"
```

脚本通过导出快照让 `pg_dump` 和快照时间处于同一只读事务。完整临时 dump 权限为 `0600`，裁剪后的最终制品验证成功后自动删除。

两种方式都在候选库完成：

- 删除非白名单公共表。
- 仅保留 2026 年 2 月至业务快照月份的事件总表、六类事实表和 11 类 ASN 特征月表。
- 裁剪 `feature_country` 和所有保留表的业务时间边界。
- 为缺失的快照当前月事件/事实表创建同结构空表。
- 删除 TimescaleDB retention policy。
- 关闭 TimescaleDB telemetry，保留超表所需内部 worker。
- 创建默认事务只读的 `domeye_core_reader`，验证可查询超表且不能建表。
- 记录精确行数、时间范围、schema hash、版本和固定镜像 ID。
- 严格对照从 202602 至快照月的表网格，拒绝缺表、多余公共表和非 TimescaleDB 用户 schema 表。
- 只在候选库删除无法路由的核心事件总表行，按月份和类型记录 `discarded_malformed_event_rows`；随后逐月对所有有效详情键做集合级 Hash Anti Join，要求 `malformed_count=0`、`orphan_count=0`。

完整恢复或刷新成功后，脚本会先原子写入 `pre_prune_context` 检查点，再执行 `prune.sql`；该上下文已落盘且裁剪尚未产生任何输出时可以续跑。若 system identifier、JQ 或状态写入恰好在上下文本身落盘前失败，脚本仍保留候选库并禁止自动重建，但必须先人工复核，不能由续跑入口猜测状态。`prune.sql` 完整返回后，以独立的成功哨兵、`prune-output.sha256`、完成标记和 `build-state.json` 形成交叉校验的 post-prune 续跑点。此后若只读授权、JQ、inventory、schema dump、压缩、清单、打包或冒烟失败，脚本会停止候选容器，但保留候选 PGDATA、状态和裁剪证据，并拒绝同一 release-id 自动重建。数据库组件生成后也保留候选 PGDATA，直到正式发布恢复和完整验收通过。

默认原则是“能续跑就不重建”。只有源转储哈希或一致性错误、`pg_restore` 本身失败、`prune.sql` 或数据本身失败且无法修复、schema/表数量/完整性无法修复，或者数据范围与快照基准发生变化时，才进入人工确认重建流程。脚本解析、JQ、清单、压缩、打包、恢复后门禁或冒烟错误都不得自动删除候选 PGDATA。

若 `pg_restore` 已完整返回、仅 `timescaledb_post_restore()` 失败，构建脚本会写入 `post-restore-pending.tsv` 并保留候选 PGDATA，不把它误判为完整恢复失败而删除。增量刷新已恢复基准库但刷新阶段失败时，会留下 `refresh-pending.tsv` 和候选 PGDATA。两类状态都必须先人工复核：能从失败门禁安全续跑时继续，状态不足以证明幂等时先隔离现场；不得直接重建或越过未完成阶段进入裁剪。

后置阶段失败时，使用错误信息中给出的候选构建目录续跑，不再执行完整恢复和裁剪：

```bash
./deploy/database/resume-database-artifact.sh \
  /home/bgpdata/Domeye-Core-data/config/database.env \
  "${release_id}" \
  /home/bgpdata/Domeye-Core-data/work/build-${release_id}-<pid> \
  /home/bgpdata/Domeye-Core-artifacts
```

续跑入口会先验证 `build-state.json`、候选 PGDATA 和已有裁剪证据均不是软链接，并绑定 release-id、固定数据起点、快照时间、镜像 ID、`prune.sql`、裁剪输出 SHA256 和 PostgreSQL system identifier。若停在完整的 `pre_prune_context` 且不存在任何裁剪输出，入口只补做一次裁剪；若只有一个 pending 输出且存在与其文件名和审计末行匹配的持久成功哨兵，入口可原子晋升该输出，避免重复执行已成功的裁剪。最终输出可信时，缺失的 checksum、完成标记或状态字段可以重建；没有成功哨兵的部分输出一律拒绝猜测和覆盖。通过 post-prune 检查点后才重跑只读授权、版本、完整性、inventory、时间边界、schema、数据库 dump、镜像归档和数据库清单。若上次已发布部分数据库文件，只复用与检查点中可信 staging 大小及 SHA256 完全一致的文件；不一致内容一律拒绝覆盖，`database-manifest.json` 始终最后发布。续跑成功后仍保留候选 PGDATA，待完整发布验收通过后再人工清理。

## 4. 定稿发布

信息和数据库组件都完成后：

```bash
release_dir="/home/bgpdata/Domeye-Core-artifacts/releases/${release_id}"
./deploy/artifacts/finalize-release.sh "${release_dir}"
./deploy/artifacts/verify-release.sh "${release_dir}"
```

定稿脚本不会覆盖不一致的内容：若上次只生成了 `manifest.json`，会保留原 `created_at`、重建期望清单并逐字段比对，一致时只续做 `SHA256SUMS`；若两者都已存在，则直接完整复验。校验器强制八个发布文件恰好各出现一次，验证 64 位十六进制 SHA256，并交叉核对总清单和两个组件清单。`SHA256SUMS` 缺行、重复行、多余文件名或内嵌哈希不一致都会失败。

## 5. 分离候选准备与生产激活

先在发布机准备候选，不切生产流量：

```bash
cd /home/bgpdata/Domeye-Core
release_host="$(hostname -f)"
make release-prepare \
  RELEASE_DIR="/home/bgpdata/Domeye-Core-artifacts/releases/${release_id}" \
  HIDDEN_PATH=/home/bgpdata/Domeye \
  DATABASE_ENV_FILE=/home/bgpdata/Domeye-Core-data/config/database.env \
  RELEASE_HOST="${release_host}"
```

准备状态与所有输入指纹一致后，才显式激活：

```bash
make release-activate \
  RELEASE_DIR="/home/bgpdata/Domeye-Core-artifacts/releases/${release_id}" \
  HIDDEN_PATH=/home/bgpdata/Domeye \
  DATABASE_ENV_FILE=/home/bgpdata/Domeye-Core-data/config/database.env \
  RELEASE_HOST="${release_host}" \
  CONFIRM_RELEASE_ID="${release_id}"
```

禁止直接执行 `deploy/acceptance/full-acceptance.sh`。该脚本只接受 `release-activate` 写入的 `activating` 状态、一次性随机令牌和父进程全局锁。

完整顺序固定为：

1. 校验发布；四文件信息制品先解包到候选临时目录，不触碰生产 `backend/info`。
2. 加载冻结镜像，将数据库恢复到版本目录并复验 inventory、版本和只读查询。
3. `uv sync --frozen`、后端 pytest、`core.sha256` 校验。
4. 断言隔离 Node.js 为 `v22.23.1`，再执行 `npm ci`、前端测试和候选构建；构建输出先写入一次性临时目录，不覆盖在线 Nginx 使用的 `frontend/dist`。
5. 用三个随机高位端口启动候选 PostgreSQL、候选 Flask 和临时 Nginx。
6. 在候选栈运行核心接口、六类详情、五类中断时序、仪表盘、移除接口 404 和 SPA 直达刷新。
7. 在挂载命名空间中遮蔽固定旧目录，重新冷启动后端并执行真实 ASN 特征查询；检查进程环境、文件描述符和日志不存在带路径边界的旧目录引用。
8. `release-activate` 再次核对所有输入、候选哈希、发布机身份和明确 release ID；通过后备份生产 Nginx 配置，短暂停止 Domeye Core Screen，依次原子安装正式信息目录、切换活动数据库软链接与 `.env`，并启动、检查新后端。
9. 新后端健康后才原子安装候选前端构建，随后安装并校验生产 Nginx 配置；最后运行生产状态、完整冒烟和隔离测试。

候选准备不会修改生产信息目录、在线前端制品、Nginx 或活动数据库链接。准备状态记录输入指纹与每个完成门禁，普通后置错误可从安全检查点继续。生产切换前会从实际 Screen 进程树安全捕获其 `INFO_DIR`，不读取或打印其他环境密钥。前端构建采用持久候选目录和确定性目录哈希，并以 `frontend-current`、一次性回滚日志及持久安装状态覆盖子进程中断窗口。切换期间如完整冒烟失败，脚本会先停止新后端，再恢复原前端、信息目录、`.env`、活动链接、Nginx 和原运行状态；只有各项恢复全部成功才重启旧后端。首次安装前不存在 `.env`、前端构建、信息目录或活动数据库链接时，也会恢复为“不存在”而不是制造占位状态。

## 6. 分步恢复和候选排障

需要排查制品恢复或候选栈时，可以在不改生产信息目录、活动数据库链接和 Screen 的情况下分步执行：

```bash
./deploy/database/restore-database.sh \
  "${release_dir}" \
  /home/bgpdata/Domeye-Core-data/config/database.env
./deploy/acceptance/candidate-stack.sh \
  "${release_dir}" \
  /home/bgpdata/Domeye-Core-data/config/database.env \
  /home/bgpdata/Domeye
```

`restore-database.sh` 在完整 `pg_restore` 成功后立即原子写入 `restore-checkpoint.json`，阶段为 `restored_unverified`。后续 inventory、JQ、schema 或只读查询门禁失败时保留 PGDATA；再次执行会校验 release-id、dump SHA256、镜像 ID 和 PostgreSQL system identifier，然后只重跑后置门禁。全部通过后才生成阶段为 `verified` 的 `restore-state.json`。脚本不会仅凭 `PG_VERSION` 提前返回，也不会因后置脚本错误重做昂贵恢复。

生产信息目录、数据库链接和 `.env` 必须作为同一次切换处理。不要在仍运行旧 Screen 时单独执行 `install-info-artifact.sh`，也不要把 `activate-database.sh` 当作日常入口；候选排障通过后，应重新运行第 5 节的 `make release-prepare` 收敛完整状态，再通过 `make release-activate` 完成受保护的最终切换。

## 7. 人工刷新数据

新 release 以最近一次完整发布为基础：

```bash
new_release_id=20260801T010000Z
base_release_dir="/home/bgpdata/Domeye-Core-artifacts/releases/${release_id}"

./deploy/artifacts/build-info-artifact.sh \
  /home/bgpdata/Domeye/backend/info \
  "${new_release_id}"

./deploy/database/refresh-database-artifact.sh \
  /home/bgpdata/Domeye-Core-data/config/source.env \
  /home/bgpdata/Domeye-Core-data/config/database.env \
  "${new_release_id}" \
  "${base_release_dir}"

./deploy/artifacts/finalize-release.sh \
  "/home/bgpdata/Domeye-Core-artifacts/releases/${new_release_id}"
```

刷新规则：

- 新快照 UTC 和上海业务时间都必须严格晚于上一发布。
- 早于上一发布所在月份的表保持不可变。
- 上一发布所在月份在当时仍是当前月，因此跨月刷新时会再导入一次以补齐月末；随后导入新增月份和新的当前月。
- 月表通过 `SELECT * FROM hypertable` 导出，确保包含 TimescaleDB chunks；导入后逐表比较源/候选精确行数。
- `feature_country` 从上一水位前 24 小时重叠导入，按 `(t, source, country)` 更新或插入，并比较 staging 行数。
- 刷新仍生成完整新制品，生产服务不会连接源数据库。

## 8. 状态、启停和回滚

```bash
cd /home/bgpdata/Domeye-Core
./deploy/status.sh
./deploy/stop-backend.sh
./deploy/start-backend.sh
./deploy/database/dbctl.sh status
```

显式回滚最近一次生产切换时，使用活动 release ID、发布机主机名和相同确认值：

```bash
make release-rollback \
  RELEASE_ID="${release_id}" \
  DATABASE_ENV_FILE=/home/bgpdata/Domeye-Core-data/config/database.env \
  RELEASE_HOST="$(hostname -f)" \
  CONFIRM_RELEASE_ID="${release_id}"
```

该入口会先验证数据库、信息和前端三个 current 状态及未消费回滚日志都指向同一版本，再依次恢复组件、数据库配置和切换前 Nginx 配置。任一步失败都会把状态标记为 `rollback_failed` 并保留现场。

候选清理默认只预览：

```bash
make release-gc GC_OLDER_THAN_DAYS=14
```

确认单个候选无活动引用、回滚引用、锁、挂载或 Overlay 使用后，才允许显式删除：

```bash
make release-gc \
  GC_EXECUTE=1 \
  GC_RELEASE_ID="${release_id}" \
  GC_OLDER_THAN_DAYS=14 \
  RELEASE_HOST="$(hostname -f)" \
  CONFIRM_RELEASE_ID="${release_id}"
```

完整验收过程中的失败会自动执行同一顺序，并额外恢复切换前 Nginx 配置及其原运行状态；每个回滚步骤都会记录结果，任一步失败都会汇总为明确的回滚失败。回到旧源库配置时会原子写入 `source-rollback-active.json`，绑定整份 `.env` 的 SHA256、数据库地址和实际 `INFO_DIR`；普通启停只有验证该标记后才允许持久回滚态，不能仅凭活动链接缺失绕过独立库门禁。回滚只操作 Domeye Core 的 Screen、`.env`、独立数据库容器、前端构建、信息目录和活动软链接；不会删除新 release 数据，也不会操作原项目。上一数据库目录、前端构建、信息目录、切换前后端配置及一次性回滚日志保存在已忽略的 `/home/bgpdata/Domeye-Core/var/releases`。成功回滚会把日志标记为已消费，重复调用会被拒绝。

## 9. 安全和运维约定

- `database.env`、`source.env`、真实 `.env`、dump、镜像和数据目录权限必须受限，禁止提交 Git。
- 候选栈和旧目录隔离测试显式禁用项目 `.env` 回填，并拒绝 SSH、SMTP、旧采集路径和源库高权限变量进入候选进程；生产后端仍只读取切换后收口的 `.env`。
- 管理员密码、只读密码和 `SECRET_KEY` 不作为宿主进程命令行参数传递；空库初始化使用临时 `0600` env 文件，只读角色密码经受保护的标准输入设置，候选后端和隔离后端从临时 `0600` 环境文件加载。
- 信息归档安装拒绝多余成员、软链接、大小或 SHA256 不一致，并以同目录重命名原子切换。
- 前端安装拒绝软链接和特殊文件，复制前后核对确定性目录哈希，并以同目录重命名原子切换。
- 活动数据库路径只能指向 `/home/bgpdata/Domeye-Core-data/releases/<release-id>/postgres`。
- `dbctl.sh up` 每次从 `restore-state.json` 校验镜像 ID，防止可变标签漂移。
- 任何脚本都只按精确容器名、Screen 会话名和固定项目路径操作。
- 不手工复制运行中的 PGDATA；数据库迁移使用逻辑 dump 和 TimescaleDB pre/post restore 流程。
- 不删除旧 release、信息目录备份或切换前配置，确认稳定后再按运维流程清理。
- 当前不优化冷查询，不修改既有 API 行为，也不修改 `backend/core`。
