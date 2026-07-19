# 2–3 月快速开发数据库

该流程从服务器上已经保留的候选 PGDATA 派生两个月真实开发库，不恢复数据库转储，也不复制约 84GB 的数据目录。候选 PGDATA 先挂载为只读 bind view，再作为 OverlayFS lowerdir；PostgreSQL 的运行写入、目录 whiteout 和 `feature_country` 边界裁剪只进入独立 upperdir。

这是 L3 数据与部署操作，只允许在服务器上由 root 执行。脚本不会停止原项目、生产数据库、生产 Nginx 或既有 Screen。

## 本轮固定边界

为减少误操作面，本轮不提供通用参数：

```text
候选 PGDATA  /home/bgpdata/Domeye-Core-data/work/resume-20260717T124354Z-attempt3/postgres
发布目录     /home/bgpdata/Domeye-Core-artifacts/releases/20260717T124354Z
开发数据根   /home/bgpdata/Domeye-Core-dev-data
监听地址     127.0.0.1:31627
数据窗口     2026-02-01 00:00:00 <= t < 2026-04-01 00:00:00
```

不能通过环境变量改写开发数据根或数据库配置路径。数据库密码只从 Git 外、root 所有且权限不宽于 `0600` 的 `/home/bgpdata/Domeye-Core-data/config/database.env` 读取。

## 首次准备与续跑

```bash
cd /home/bgpdata/Domeye-Core
./dev/database/manage-dev-database.sh prepare \
  /home/bgpdata/Domeye-Core-data/work/resume-20260717T124354Z-attempt3/postgres \
  /home/bgpdata/Domeye-Core-artifacts/releases/20260717T124354Z \
  31627
```

首次裁剪前会依次验证：

- 固定路径、`PG_VERSION=12`、离线 PostgreSQL `system_identifier`；
- lower 的 `PG_VERSION` 和 `global/pg_control` SHA256；
- 发布总清单、数据库组件清单和 `database-inventory.json` 的交叉哈希；
- 所有 Docker 容器都没有直接挂载固定 lower PGDATA 或其子路径；
- 源候选 public 表集合与发布 inventory 一致；
- 2、3 月保留月表没有窗口外数据，`feature_country` 与窗口有有效交集。

候选占用锁固定在：

```text
/home/bgpdata/Domeye-Core-data/work/.candidate-use-locks/20260717T124354Z.dev-overlay.lock
```

开发 Overlay 存续期间该锁一直保留，即使开发容器已停止。数据库制品续跑入口使用同一把锁，因此不能在 Overlay 依赖 lower 时直接恢复或改写候选 PGDATA。

状态采用三个阶段：

- `preparing`：尚无可信裁剪提交；裁剪失败时 PostgreSQL 事务整体回滚。
- `pruned`：数据库内 `domeye_dev.prune_checkpoint` 已随裁剪事务一起提交，后置验收失败时从这里继续，不再次裁剪。
- `verified`：37 张业务表、非空数据、六类详情引用和只读授权均已通过。

如果裁剪已经提交、但进程在状态文件晋升前中断，下一次相同 `prepare` 会先读取数据库内持久检查点并晋升到 `pruned`，不会重复执行裁剪。验证 SQL 可以修复后重新运行；检查点只绑定裁剪 SQL 与 inventory，不会因为后置验证脚本修订而要求重建 Overlay。

## 容器身份

容器名固定为 `domeye_core_dev_pg`，并固定以下生命周期标签：

```text
io.domeye.core.role=development-database
io.domeye.core.instance=domeye-core-dev-feb-mar-2026
io.domeye.core.state=/home/bgpdata/Domeye-Core-dev-data/state.json
```

创建、复用、执行 SQL 和停止时都会逐字校验以上标签，同时校验冻结镜像 ID、merged 挂载源、端口、release-id、system identifier 和检查点摘要。发现同名但身份不一致的容器时只拒绝操作，不会停止或删除它。

## 日常管理

```bash
./dev/database/manage-dev-database.sh start
./dev/database/manage-dev-database.sh verify
./dev/database/manage-dev-database.sh status
./dev/database/manage-dev-database.sh stop
```

`stop` 只停止身份完全匹配的容器并卸载已验证的 merged/lower view，不删除容器、upperdir、状态或候选占用锁。`start` 和 `verify` 每次都会重新验收当前数据库；若验证 SQL 已修订，只有验证成功后才把新哈希和时间写入状态。

开发后端使用同一配置中的只读账号，并设置：

```text
DOMEYE_DATA_SNAPSHOT_TIME=2026-03-31 23:59:59
```

这会让 Top 和仪表盘接口以 3 月 31 日为参考时钟，不改变未设置该变量时的生产行为。

## 退役原则

不要单独删除候选占用锁，也不要只删除 state 或 upperdir。需要退役开发库时，先执行 `stop`，再把开发数据根和对应占用锁作为一个整体人工复核、隔离；本脚本不提供自动销毁命令。候选 lower 未解除依赖前，不得让数据库制品续跑入口重新启动该 PGDATA。
