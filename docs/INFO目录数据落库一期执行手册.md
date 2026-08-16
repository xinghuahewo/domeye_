# INFO 目录数据落库一期执行手册

## 1. 本期目标

一期执行完整 24 文件的内容冻结和逻辑计数，但只把以下四个当前核心文件
导入候选数据库：

- `as_entity.csv`
- `important_as.csv`
- `ip_bgp_entity.csv`
- `country.xlsx`

输出是一个 `validating` 状态的 shadow release。它不能激活为 `core`，
不能替代现有四文件运行时制品，也不会修改
`/home/bgpdata/Domeye/backend/info`。

## 2. 前提

1. 来源目录必须是实际目录，不能是软链接。
2. 目录必须恰好含合同规定的 24 个数据文件；允许额外存在 `README.md`，
   不允许其他文件。
3. Python 必须是项目 3.10 环境，并已安装 `openpyxl`、`xlrd`。
4. 候选 PostgreSQL 必须是独立容器，不得把生产在线库作为导入目标。
5. 首轮仍按设计预留至少 80 GiB 可用空间；临时 spool 位于系统临时目录，
   需额外预留与四核心文件转换结果相当的空间。

## 3. 分步生成证据

在仓库根目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python \
  -m backend.info_pipeline manifest \
  --source-dir /home/bgpdata/Domeye/backend/info \
  --source-release-label 20260724T000000Z-info-shadow \
  --output /受控证据目录/static-info-manifest.json

PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python \
  -m backend.info_pipeline probe \
  --source-dir /home/bgpdata/Domeye/backend/info \
  --manifest /受控证据目录/static-info-manifest.json \
  --output /受控证据目录/static-info-quality.json
```

`probe` 返回非零表示阻断规则失败。不得跳过质量报告直接 COPY。

## 4. 导入一个正在运行的候选容器

推荐使用封装命令：

```bash
./deploy/database/import-static-info-candidate.sh \
  /home/bgpdata/Domeye/backend/info \
  20260724T000000Z-info-shadow \
  候选容器名 \
  数据库管理员角色 \
  bgp_project \
  /受控证据目录/static-info-import \
  代码提交SHA
```

命令会顺序执行 manifest、质量探针和四文件导入，最终生成：

```text
static-info-manifest.json
static-info-quality.json
static-info-load-result.json
stage-gate-S0.json
stage-gate-S1.json
SHA256SUMS
```

成功结果必须同时满足：

```text
status = completed | already_completed
scope = core_four_files
activated = false
database_release_status = validating   # 首次完成时
```

若该命令在现有数据库构建脚本之外单独运行，导入后还需按发布流程重新执行
`create-reader.sql`，让应用只读角色获得新业务表的最小 SELECT 权限。

## 5. 接入数据库候选制品构建

构建和续跑脚本采用显式 opt-in。只有设置以下环境变量才会读取完整 INFO
目录：

```bash
export DOMEYE_CORE_STATIC_INFO_SOURCE_DIR=/home/bgpdata/Domeye/backend/info
export DOMEYE_CORE_STATIC_INFO_SCOPE=core_four_files
export DOMEYE_CORE_CODE_COMMIT=代码提交SHA
```

随后按原有 `build-database-artifact.sh` 或
`resume-database-artifact.sh` 参数执行。钩子位置固定为：

```text
恢复/裁剪完成
-> static INFO manifest
-> 四文件质量探针
-> info schema + 四文件 shadow 导入
-> 创建/收紧只读角色
-> 完整性检查
-> inventory/schema dump
-> 数据库制品打包
```

未设置 `DOMEYE_CORE_STATIC_INFO_SOURCE_DIR` 时，旧发布流程不会读取旧项目
INFO 目录。若恢复出的数据库已经含 `info` schema，仍会执行精确白名单、
计数、生命周期和内容身份验收。

`DOMEYE_CORE_STATIC_INFO_SCOPE` 默认且仅一期使用 `core_four_files`。只有按
[INFO 目录数据落库二期执行手册](INFO目录数据落库二期执行手册.md) 显式设置为
`all_24_files`，构建和续跑才会在 S1 Hook 通过后继续执行 S2。启用 INFO 导入
时，阶段报告、Hook 回执和合同快照会封装为
`static-info-evidence.tar.zst`，由数据库组件清单和总发布校验和共同保护。

续跑必须使用相同来源内容。来源变化会产生不同 `content_id`，而候选库中
遗留的 failed/loading release 会使完整性门禁失败，不能被新 release
静默掩盖。

## 6. 一期质量规则

阻断规则包括：

- 24 文件集合、普通文件类型、内容哈希、逻辑记录数和版本化表头；
- JSON 顶层对象结构及任意层级重复键；
- ASN 范围、ASN 自然键唯一性、AS 关系列表元素；
- prefix 非空、可解析、raw 键唯一；
- AS 与域名字符串化列表只能由安全字面量解析，最终类型必须是 list；
- 域名列表成员必须是非空字符串；
- 合法 alpha-2 国家代码唯一；
- manifest 与质量报告的 `content_id`、`manifest_sha256` 完全一致。

非阻断但必须记录：

- 非规范 prefix 数量；
- 国家 alpha-2 或经纬度缺失数量。

## 7. 数据库验收查询

候选库中至少检查：

```sql
SELECT
    release_sk,
    content_id,
    manifest_sha256,
    status,
    loaded_scope,
    quality_summary
FROM info.dataset_release
ORDER BY release_sk DESC;

SELECT
    name,
    role,
    load_status,
    logical_record_count,
    loaded_record_count,
    quarantined_record_count
FROM info.source_file
WHERE release_sk = :release_sk
ORDER BY name;

SELECT * FROM info.active_release;
```

一期预期：

- 一条目标 release 为 `validating`；
- 四核心文件为 `loaded`，其余 20 文件为 `pending`；
- 每个 loaded 文件满足
  `loaded_record_count + quarantined_record_count = logical_record_count`；
- `info.active_release` 为空。

## 8. 失败和恢复

- manifest/探针失败：不连接数据库，修复合同或明确隔离数据后重跑。
- 单文件 COPY/归一化失败：该文件事务整体回滚，其他已完成文件保留。
- 构建后置门禁失败：保留候选 PGDATA 和证据，从
  `resume-database-artifact.sh` 续跑，不自动重建。
- 不得手工把一期 release 改为 `ready/active`；不得直接写
  `info.active_release` 绕过函数门禁。
- 不删除 failed release、质量结果或隔离证据来“让门禁变绿”。

## 9. 下一里程碑

下一期按设计顺序实现域名与 pfx2as，随后增加 `InfoRepository` 的
API shadow 对账。只有全部 24 文件完成逐文件对账、API 行为一致、检测
A/B 通过后，才评审 `ready/active` 和生产切换。

## 10. 阶段结束 Hook

最终效果以
[INFO 目录数据落库最终验收文档](INFO目录数据落库最终验收文档.md)
为准，各阶段入口、出口和边界以
[INFO 目录数据落库分阶段计划](INFO目录数据落库分阶段计划.md)
为准。机器门禁合同位于
`contracts/info/static-info-final-acceptance-v1.json`。

一期封装流程会在质量基线完成后自动执行 S0 Hook，在四核心文件 shadow
导入完成后自动执行 S1 Hook，证据目录新增：

```text
stage-gate-S0.json
stage-gate-S1.json
```

后续阶段统一在阶段结束时执行：

```bash
./deploy/database/static-info-stage-end-hook.sh \
  S2 \
  /本阶段证据目录 \
  /本阶段证据目录/stage-gate-S2.json \
  /前序证据目录/stage-gate-S1.json
```

S0 没有前序回执，因此只传前三个参数。退出码含义：

- `0`：本阶段未发现相对最终验收文档的偏离；
- `1`：已生成 `fail` 回执，存在明确偏离；
- `2`：合同、文档、参数或证据本身无法被安全读取。

Hook 会核对最终验收文档和阶段计划哈希、前序通过链、内容身份、证据文件
哈希、阶段规定字段和跨证据一致性。回执格式由
`contracts/info/static-info-stage-gate-report-v1.schema.json` 约束，并采用
排他创建，不允许覆盖既有结果。

Hook 只读取证据并创建一个新回执，不连接数据库、不切换生产、不停止服务。
它验证证据是否符合合同，但不替代产生这些证据的业务检查，也不授予激活权限。
