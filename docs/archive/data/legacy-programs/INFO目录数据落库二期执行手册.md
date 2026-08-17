# INFO 目录数据落库二期执行手册

## 一、适用范围

本文只说明 S2“全部 24 文件闭合”的候选执行方式、证据和失败边界。S2
完成不代表查询语义、API、检测器、性能、激活或生产切换已经通过。

S2 的唯一完成判据是：

- 输入为已经通过 S1 Hook 的同一 `content_id`；
- 24/24 文件逐文件满足“接纳数 + 隔离数 = manifest 逻辑记录数”；
- 24/24 文件逐记录账本数等于 manifest 逻辑记录数；
- 字典型来源的空映射顶层键仍有 `mapping_record`，不会因没有子项而消失；
- 可见业务记录不存在无法关联到来源文件和来源行的记录；
- 每条 accepted 来源账本至少有一个对应业务父记录，空映射也有父记录；
- 隔离记录原因覆盖率为 100%；
- 隔离账本与 `info.quarantine` 逐记录互相镜像；
- 未批准的阻断级质量标记为 0；
- `loaded_not_consumed`、legacy 和历史来源没有业务行被标记为 active；
- release 仍为 `validating`，且没有写入 `active_release`；
- S2 阶段结束 Hook 产生 `status=pass`、`deviation_count=0` 的回执。

## 二、硬边界

1. 来源目录只读，不在其中创建临时文件、锁文件或校验结果。
2. 只允许写入隔离候选数据库；不得连接或修改原项目生产数据库。
3. 不修改 `backend/core/`，不改变现有检测算法。
4. legacy、旧版和用途未决来源只进入带来源身份的保留表，默认不成为当前数据。
5. S2 不激活 release，不切换 API、快照或检测器。
6. 真实源未执行、真实证据不完整或 Hook 未通过时，阶段状态只能是“未完成”。
7. 直接候选导入只接受 Docker 标签
   `domeye.core.database-role=offline-candidate`；无标签或标签不符时，在执行任何
   SQL 前失败关闭。构建、续跑和恢复脚本创建的离线候选会自动带此标签。

## 三、直接扩展已经通过的 S1 候选

```bash
cd /部署代码目录

./deploy/database/import-static-info-full-candidate.sh \
  /home/bgpdata/Domeye/backend/info \
  候选数据库容器名 \
  数据库管理员 \
  数据库名 \
  /受控证据目录/S1 \
  /受控证据目录/S2
```

命令先校验 S1 的 `SHA256SUMS`、S1 回执和 manifest 内容身份，再复制同一
manifest，逐文件装载其余 20 个文件。它不会重新生成另一个内容身份，也不会
接受没有 S1 `pass` 回执的候选库。

S2 importer 当前身份为 `info-full-importer-v2`，解析器身份为
`info-parser-v5`。解析器对 CSV 单字段设置显式 64 MiB 上限；真实
`ip_bgp_entity.csv` 已观测到约 19.25M 字符的 `domain_auth` 字段，因此不能
使用 Python 默认的 128 KiB 上限，也不能截断或拆分该字段。较早代码生成的
manifest 或旧机器合同回执不能直接续接；这类差异不是“续跑”，必须保留旧证据
并从 S0 重新生成同一来源在当前合同下的新 S1 回执。Excel 解析按文件魔数
选择引擎；两个历史 `.xls` 文件实际为 OOXML，必须通过二进制流交给
`openpyxl`，不得按后缀误交给 `xlrd`。

所有文本文件的字符编码也是逐文件合同的一部分：默认严格使用
`utf-8-sig`，历史文件 `ases_cn.csv` 经全文件验证为 `GB18030`，仅该文件
显式使用 `gb18030`。禁止 `errors=replace`、静默跳字节或运行时编码猜测。

导入不会把大文件整体载入 Python 内存，而是写入证据目录所在受控文件系统的
临时 spool，再通过 `COPY` 传入候选数据库。对
`ip_bgp_entity.csv`，解析器在同一次严格 CSV 扫描中分别生成前缀主记录 spool
和带 release、来源文件、来源行、角色、序号及记录摘要的扁平域名 spool；每行
声明数量必须与解析数量相等。候选库通过分区表直接 `COPY` 扁平域名，不再让
PostgreSQL 展开超大 JSONB 数组；事务末尾仍对域名总数强制对账。

S2 在任何 schema 写入前按最大来源
文件执行临时盘预检，要求至少：

```text
最大来源文件字节数 × 4 + 2 GiB
```

不足时直接失败。直接调用 Python CLI 时可用 `DOMEYE_CORE_INFO_SPOOL_DIR`
指定大容量受控文件系统；构建、续跑和候选导入脚本则固定把 spool 放在当前受控
证据目录中，因此必须把证据/制品根放在容量合格的文件系统。临时文件在单文件
事务结束后关闭，成功时删除空 spool 目录，失败时保留尝试目录。

成功后，S2 目录必须包含：

```text
static-info-manifest.json
static-info-full-quality.json
static-info-full-load-result.json
stage-gate-S2.json
SHA256SUMS
```

复核：

```bash
cd /受控证据目录/S2
sha256sum -c SHA256SUMS

jq -e '
  .status == "pass"
  and .deviation_count == 0
  and .stage_id == "S2"
' stage-gate-S2.json

jq -e '
  .status == "pass"
  and .blocking_failure_count == 0
  and .business_traceability_failure_count == 0
  and .accepted_record_visibility_failure_count == 0
  and .blocking_quality_flag_count == 0
  and .quarantine_mirror_failure_count == 0
  and .source_role_activation_failure_count == 0
  and (.files | length) == 24
' static-info-full-quality.json

jq -e '
  (.status == "completed" or .status == "already_completed")
  and .scope == "all_24_files"
  and .database_release_status == "validating"
  and .activated == false
  and .source_file_count == 24
  and .reconciled_source_file_count == 24
  and .unreconciled_record_count == 0
  and .visible_record_traceability_percent == 100
  and .quarantine_reason_coverage_percent == 100
' static-info-full-load-result.json
```

## 四、随数据库候选制品执行

数据库构建默认仍只执行四核心文件 S1。只有同时显式指定来源和全量 scope
才会按 S1→S2 顺序运行：

```bash
export DOMEYE_CORE_STATIC_INFO_SOURCE_DIR=/home/bgpdata/Domeye/backend/info
export DOMEYE_CORE_STATIC_INFO_SCOPE=all_24_files
export DOMEYE_CORE_CODE_COMMIT=代码提交SHA

./deploy/database/build-database-artifact.sh 其余原有参数
```

续跑必须再次提供相同的来源开关和 scope；`build-state.json` 不允许在续跑时
把未启用的构建临时扩大为全量导入，也不允许把全量构建降为四文件导入。

成功发布的数据库组件额外包含：

```text
static-info-evidence.tar.zst
```

数据库组件清单记录该证据包的文件名、SHA256、大小、scope 和
`content_id`。证据包内同时保存：

- S0、S1 和（全量模式下）S2 的报告与 Hook 回执；
- 自动续跑前保留下来的 `.incomplete.*` 失败尝试目录；
- 每阶段内部 `SHA256SUMS`；
- 最终验收文档、分阶段计划和机器合同的只读快照及校验和。

发布复核会解包到受控临时目录，用归档内的合同和文档重新执行 S1/S2
阶段门禁；不能只验证外层压缩包哈希。

## 五、失败与续跑

- 单文件装载使用独立事务；整文件成功后才把该文件标为 `loaded`。
- 相同 manifest、解析器和 importer scope 使用固定幂等键；完整重跑返回
  `already_completed`。
- 中断后已完成的文件不重复形成业务记录，未完成文件从其文件边界重新执行。
- 未完成的 S1/S2 证据目录不会被删除；自动续跑前会改名为带
  `.incomplete.<UTC>.<PID>` 的保留目录，再生成新的完整证据目录。
- 校验和不匹配、合同漂移、来源内容变化或候选库身份不一致属于疑似篡改或越界，
  不会自动归档后继续，而是直接失败关闭。
- S2 质量失败会保留 `failed` import run、逐文件检查点和失败证据；不得通过删除
  失败记录使 Hook 变绿。

## 六、当前实施状态

截至 2026-07-26，同一 `content_id`
`info_v1_400c1e3f74c43cc37088a49b1ad5655f` 已在服务器隔离候选库完成真实 S2：

- 24/24 文件均为 `loaded`；
- 来源逻辑记录共 15,650,189 条，其中 accepted 15,646,770 条、quarantined
  3,419 条，三者逐文件守恒；
- 业务可追溯失败、accepted 可见性失败、阻断质量标记、隔离镜像失败和来源角色
  越界均为 0；
- release 状态仍为 `validating`，`active_release` 记录数为 0；
- `stage-gate-S2.json` 为 `status=pass`、`deviation_count=0`。

真实证据位于隔离工作根的 `evidence/S2`，并已通过目录内 `SHA256SUMS` 复核。
这只证明 S2 的 FA-02、FA-04 到期要求成立，不代表 S3 查询/快照、S4 检测/
非功能、S5 激活/回滚或 S6 运行时收口已经通过。
