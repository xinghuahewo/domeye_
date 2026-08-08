# Domeye 数据底座当前架构与边界

版本：1.0<br>
状态：当前说明<br>
状态日期：2026-08-08

## 一、文档定位

本文是当前数据底座的阅读入口，用来区分三个容易混淆的对象：

1. 历史 P0 数据基础；
2. RRC25 224–310 新数据层；
3. 当前国家中断通用观测页的生产读取路径。

本文不替代任何最终验收合同或阶段验收记录。历史文档继续证明当时的范围、状态和结论；
当前状态变化只更新本文，不倒写历史验收结果。

## 二、一句话结论

P0 是早期“把旧数据看清楚”的兼容与质量基础；224–310 数据层是以 RouteEvent 和
RouteState 为核心的可重放新数据链；当前国家中断通用观测页读取冻结文件读模型，
没有把新数据层表写入生产旧库 `bgp_project`。

## 三、三套概念的关系

| 对象 | 解决的问题 | 当前定位 |
| --- | --- | --- |
| P0 数据基础 | 旧二三月事实能否统一读取、解释缺失并追溯来源 | 历史兼容基础；达到 `legacy_compatible`，未达到全窗口 `raw_traceable` |
| 224–310 数据层 | RRC25 数据能否一次解析、确定性重放、形成状态、指标、Publication 和读模型 | 新数据链的正式验收实现；权威范围固定为 `[2026-02-24T00:00:00Z, 2026-03-11T00:00:00Z)` |
| 国家中断通用观测页 | 用户能否读取前缀、AS、IP、受影响 AS 和路径关联 | 当前生产页面读取已冻结的通用文件读模型；不在请求时扫描原始 MRT 或全量证据 |

因此，`P0` 不再作为当前新数据层的总称。没有特别说明时，“当前数据底座”指
224–310 所确定的 RouteEvent → RouteState → 指标 → Publication → 读模型链路。

## 四、当前逻辑数据流

```text
RRC25 原始 RIB / UPDATE
    ↓
不可变 RouteEvent
    ↓
RouteState
Key = collector + VP/peer + prefix + address_family
    ├─→ 国家 / ASN 五分钟指标
    ├─→ RouteState Checkpoint
    └─→ Prefix×VP Evidence 派生视图
                ↓
Incident + Observation / Analysis Publication
                ↓
页面读模型 + 报告快照
                ↓
只读 API 与页面
```

其中：

- RouteState 是唯一状态事实；
- Prefix×VP 是 RouteState 的主键维度，不是独立事实层；
- Checkpoint 是同一 RouteState 的恢复快照；
- Prefix×VP Evidence 是可删除、可重建的下钻制品；
- 国家和 ASN 指标只能从登记的 RouteState 确定性投影；
- Publication 和报告更新必须生成新版本，不能覆盖旧版本。

## 五、物理存储边界

### 5.1 文件层

下列高体量或需要逐文件验真的内容保存在不可变文件层：

- 原始 MRT RIB / UPDATE；
- 完整 RouteEvent；
- RouteState；
- RouteState Checkpoint；
- 高基数 Prefix×VP Evidence 页；
- 完整路径证据和 AS_PATH/属性字典；
- 可重建的页面读模型文件。

文件层对象必须带 URI、SHA-256、窗口、collector、实现版本、来源和质量身份。

### 5.2 PostgreSQL 层

PostgreSQL 保存适合查询或需要事务一致性的低、中基数对象：

- 国家、ASN、collector 五分钟指标；
- 数据集、来源、质量和装载回执；
- Incident、事件事实和生命周期；
- Publication、Revision 和 current 指针；
- 页面读模型的身份、索引和报告指针；
- 影子迁移对账、发布 bundle、权限与切换审计。

PostgreSQL 不保存原始 MRT 二进制，不把完整 RouteState 复制成热表，也不把
Prefix×VP Evidence 建成第二套权威状态事实。

## 六、当前正式候选数据库表

以下表真实存在于服务器的独立候选数据库，不在生产旧库 `bgp_project` 中。

### 6.1 S3 指标库

正式数据库：`domeye_dl_s3_47e38a7_4523a27b`

| 表 | 职责 |
| --- | --- |
| `domeye_data.candidate_registry` | 候选身份 |
| `domeye_data.dataset_registry` | 数据集登记 |
| `domeye_data.evidence_object` | 文件证据对象登记 |
| `domeye_data.metric_subject` | 国家、ASN 等指标主体 |
| `domeye_data.metric_slot_5m` | 五分钟时间槽 |
| `domeye_data.route_metric_5m` | 国家、ASN、collector 五分钟指标 |
| `domeye_data.quality_gap` | 缺槽和质量问题 |
| `domeye_data.load_receipt` | 装载回执 |

### 6.2 S4 事件与发布库

正式数据库：`domeye_dl_s4_2338a76_d0f8092f`

| 表或视图 | 职责 |
| --- | --- |
| `domeye_event.candidate_registry` | 候选身份 |
| `domeye_event.source_binding` | 来源绑定 |
| `domeye_event.incident` | 事件身份与生命周期 |
| `domeye_event.event_fact` | 不可变事件事实 |
| `domeye_event.publication` | 不可变 Publication |
| `domeye_event.pointer_plan` | 指针推进计划 |
| `domeye_event.publication_pointer` | 当前 Publication 指针 |
| `domeye_event.pointer_audit` | 指针审计 |
| `domeye_event.load_receipt` | 装载回执 |
| `domeye_event.current_publication_state` | 当前发布状态视图 |

### 6.3 S5 在线读模型库

正式数据库：`domeye_dl_s5_1d0ae44_bad9b4c0`

| 表 | 职责 |
| --- | --- |
| `domeye_read.candidate_registry` | 候选身份 |
| `domeye_read.source_binding` | S2/S3/S4 来源绑定 |
| `domeye_read.series_object` | 紧凑趋势序列登记 |
| `domeye_read.prefix_vp_evidence_view` | Evidence 派生页的身份和文件位置 |
| `domeye_read.event_read_model` | 事件页面快照 |
| `domeye_read.report_snapshot` | 不可变报告快照 |
| `domeye_read.report_pointer` | 当前报告指针 |
| `domeye_read.load_receipt` | 装载回执 |

`domeye_read.prefix_vp_evidence_view` 不是 Prefix×VP 明细事实表。高基数明细仍在文件层，
该表只保存来源 RouteState、Publication、人口、摘要和文件位置。

### 6.4 S6 迁移与发布控制库

正式数据库：`domeye_dl_s6_5a4f4ef_b2a89ec6`

迁移追溯表：

- `domeye_migration.candidate_registry`
- `domeye_migration.import_batch`
- `domeye_migration.source_table_snapshot`
- `domeye_migration.legacy_country_outage`
- `domeye_migration.source_field_reconciliation`
- `domeye_migration.reconciliation_field`

发布控制表：

- `domeye_control.release_object`
- `domeye_control.object_reference`
- `domeye_control.release_bundle`
- `domeye_control.bundle_object`
- `domeye_control.release_pointer`
- `domeye_control.switch_audit`
- `domeye_control.role_contract`
- `domeye_control.dlae_evidence`
- `domeye_control.load_receipt`

运行和回收视图：

- `domeye_runtime.selected_release`
- `domeye_runtime.selected_object`
- `domeye_control.retention_eligibility`

四个正式候选数据库共 40 张表、4 个视图。服务器还保留若干开发或失败演练数据库；
它们不是正式候选，也不是生产当前选择，后续应按独立保留和清理方案处理。

## 七、当前生产读取边界

截至 2026-08-08：

- 生产后端仍连接受保护的二三月只读 PostgreSQL，端口为 `31627`；
- 生产旧库 `bgp_project` 没有新增上述 40 张业务表；
- `country_outage_incident_v2`、`country_outage_episode_v2` 和
  `country_outage_observation_v2` 只有迁移脚本，当前生产旧库未部署；
- 国家中断通用观测页读取独立冻结的通用文件读模型；
- 该生产文件读模型不等于运行时已经选择 `domeye_read` S5 候选数据库；
- 本轮通用页面 prod27 没有修改数据库、Nginx 或 Sidecar；
- AS 特征等旧产品能力仍可能读取旧 PostgreSQL；不能把“通用页面读取文件”扩大为
  “整个 Domeye 已不再使用 PostgreSQL”。

## 八、路径关联的读模型边界

完整 S3 路径证据约 509 万条。普通页面不会一次下发全部证据：

- 每个“受影响 AS → 关联 AS”关系保留总路径数、关联前缀数、独立观察方向数和观测次数；
- 普通 API 每组最多返回 3 条真实路径样本；
- 页面当前展开的 3 条是样本，不是完整路径数量；
- 如需查看全部路径，应从既有 S3 路径证据构建稳定分页下钻读模型，不需要重新解析原始 MRT；
- 路径关联只表示 RRC25 AS_PATH 中的有序观测关系，不表示客户依赖、真实转发、原因或影响。

## 九、历史文档导航

- [P0 数据基础建设计划](./P0数据基础建设计划.md)：历史 P0 范围、原则与缺口；
- [P0 数据字典](./data/P0数据字典.md)：历史兼容字段和缺失语义；
- [P0 数据验收报告](./data/P0数据验收报告.md)：P0 当时达到的实际准入等级；
- [Domeye 数据层 224–310 最终验收文档](./Domeye数据层224-310最终验收文档.md)：新数据层最终效果合同；
- [Domeye 数据层 224–310 分阶段计划](./Domeye数据层224-310分阶段计划.md)：阶段入口、出口与边界；
- [Domeye 数据层 224–310 S6 验收记录](./data/Domeye数据层224-310S6验收记录.md)：正式候选端到端证据；
- [国家中断通用观测页 S5 最终验收记录](./国家中断通用观测页S5最终验收记录.md)：通用页面实现与浏览器效果证据。

## 十、后续更新规则

1. 当前运行时、数据库选择或页面读路径发生变化时更新本文；
2. 历史验收合同和阶段验收记录保持不可变，不通过事后编辑改变当时结论；
3. 新增表必须说明属于文件索引、指标、事务事实、读模型还是控制面；
4. 任何新增 Prefix×VP 物化都必须声明来源 RouteState，禁止形成第二套权威状态；
5. 页面新增“查看全部路径”能力时，必须采用有界分页并保留样本、总数和完整证据之间的区别。
