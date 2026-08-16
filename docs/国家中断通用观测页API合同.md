# 国家中断通用观测页 API 合同

版本：1.3  
对应阶段：S1「通用事件身份与能力合同闭合」至 S5「最终通用效果验收」  
适用范围：`country_outage` 事件

## 一、统一解析入口

调用方使用旧五段式引用：

```text
country_outage/<本地事件时间>/<国家代码>/<事件序号>/<来源代码>
```

请求：

```text
GET /api/v2/events/resolve?ref=<引用>
```

解析顺序：

1. 校验引用类型、时间、两位国家代码、正整数事件序号和来源代码；
2. 优先读取已发布增强观测注册；
3. 没有增强注册时只读查询旧国家中断事实；
4. 旧事实存在则返回 `legacy_summary`，而不是 `not_configured`；
5. 引用格式非法返回 `400 / invalid_reference`；
6. 引用合法但事实不存在返回 `404 / event_not_found`；
7. 注册表或旧事实数据源失败返回 `503 / unavailable`。

`legacy_summary` 的 incident ID 由规范化引用确定性编码，可在服务重启后稳定恢复，
不依赖进程内临时映射。

解析成功时同时返回 `publication_id`。它表示一次不可变、完整发布的查询快照，
不是事件 revision，也不是临时进程 ID。

## 二、观测完整度

`observation_state` 只允许：

| 状态 | 含义 |
| --- | --- |
| `legacy_summary` | 只有旧事实摘要 |
| `aggregate_available` | 具有国家资源或报文聚合，没有固定人口状态 |
| `state_partial` | 具有部分增强状态 |
| `state_complete` | 固定 cohort、国家状态和 ASN 状态完整 |
| `evidence_complete` | 完整状态之外还有可追溯证据 |

完整度不是质量分数。较低完整度不得通过零值、空 cohort、空图或其他事件数据伪装。

## 三、能力声明

`capability_contract_version` 固定为
`country_outage_capabilities_v1`。

每项 capability 只允许：

| 状态 | 含义 |
| --- | --- |
| `available` | 当前已发布版本可读取 |
| `building` | 正在构建，尚未进入公开截止点 |
| `unavailable` | 当前数据源没有该能力，并给出原因 |
| `not_applicable` | 对当前完整度或口径不适用，并给出原因 |

标准能力至少包括：

- `legacy_summary`
- `fixed_cohort`
- `country_resources`
- `update_activity`
- `address_families`
- `asn_matrix`
- `audit`
- `normal_band`

## 四、来源模式

`data_mode` 只允许：

| 状态 | 含义 |
| --- | --- |
| `legacy` | 旧事实只读兼容 |
| `replay` | 历史重放发布 |
| `live` | 实时或准实时增量发布 |
| `mixed` | 重放起始数据与增量数据共同组成 |

来源模式不改变相同指标的字段、单位或缺失语义。

## 五、四类查询接口

统一 incident ID 进入：

```text
GET /api/v2/country-outages/<incident_id>/overview
GET /api/v2/country-outages/<incident_id>/series
GET /api/v2/country-outages/<incident_id>/asns
GET /api/v2/country-outages/<incident_id>/audit
```

页面必须把解析响应中的 `publication_id` 作为查询参数传给四个接口：

```text
?publication_id=<解析时取得的发布快照>
```

四个响应必须公开完全一致的发布身份：

- `revision`
- `publication_id`
- `publication_state`
- `observation_state`
- `data_mode`
- `data_through`
- `updated_at`
- `is_final`
- `processing_status`
- `missing_slot_count`
- `incident_id`
- `cohort_id`
- `window_start_utc`
- `window_end_utc`
- `capability_contract_version`

前端组合响应时逐字段校验。任何不一致均视为未完成发布，不得拼成同一页面。

每个响应的 ETag 同时绑定 publication、revision、`data_through`、查询参数与响应
内容摘要。即使 publication 身份未变化，API 合同字段在新版本中增加或修正后，
浏览器也不得用旧 304 缓存遮蔽新响应。

不传 `publication_id` 时，接口读取当前已发布快照，仅用于独立调用和兼容。页面
组合读取必须固定 publication。不存在或已经撤回的 publication 返回
`404 / publication_not_found`。

## 六、持续追加与原子发布

- 查询制品先完整生成并通过消费门，注册表活动指针最后切换；
- 活动指针使用同目录临时文件、文件 `fsync`、原子替换和目录 `fsync`；
- 正常追加生成新的不可变 publication，但保持 revision 不变；
- 候选制品的 incident、cohort、既有国家槽和 ASN 槽指纹必须与当前制品一致；
- 候选制品必须严格增加至少一个时间槽，且 `data_through` 等于候选制品最后
  完整观测时间；
- 旧 publication 继续保留并可固定读取；
- 页面每 45 秒重新解析活动事件；只有完整 publication 已切换后才会看到新增点；
- 同一次页面读取固定一个 publication，因此四个接口只能全部读取旧版或全部
  读取新版。

## 七、缺失和值语义

- 没有固定人口时 `cohort = null`，不得返回空 cohort 或零人口；
- 没有规则时间时 `rule_marker = null`；
- 没有公开连续观测截止点时 `data_through = null`；
- 没有时间粒度时 `interval_seconds = null`；
- 没有 ASN 状态时 ASN capability 为 `unavailable`，分页结果为空，但页面不显示
  空矩阵；
- 未知、未记录、不适用、构建中、处理失败和窗口外不得编码为数值 0；
- 旧事实中的受影响 ASN 数量仍可作为摘要事实展示，但不冒充固定 cohort 状态。

增强状态时间点额外公开：

- `slot_state = observed`：该槽有完整观测；
- `slot_state = source_unavailable / not_observed`：源数据已确认缺失；
- `slot_state = processing_gap / parse_failed`：处理尚未形成可发布观测；
- `missing_reason`：缺失原因代码。

已发布时序中缺失槽的全部数值字段为 `null`，折线使用
`connectNulls = false`，ASN 矩阵使用 `unknown (-1)`。缺槽后一槽的相邻差值也为
`null`，避免跨缺口计算变化。

`processing_status` 公开：

- `state`
- `updated_at`
- `attempted_through`
- `reason`
- `last_complete_data_through`

处理失败或等待源文件时，使用新的状态 publication 固定失败状态，但继续引用上一
份完整查询制品；revision 和 `data_through` 均不推进。

## 八、历史补正与最终性

- `append`：严格尾部追加，revision 不变；
- `status`：只发布处理状态，不更换数据制品、不推进截止点；
- `correction`：迟到源、mapping、算法或历史值发生变化，revision 精确增加 1；
- correction 必须声明 `supersedes_publication_id` 和
  `correction_reason`；
- 当前解析默认返回最新已发布 revision；
- audit 返回全部 publication 历史、revision、替代关系和补正原因；
- 旧 publication 继续可固定读取，不被静默覆盖；
- `is_final = false` 时页面继续定时检查新 publication；
- `is_final = true` 时页面标记 `FINAL`，处理状态为 `final`，停止活动轮询。

## 九、审计身份

增强观测与旧库摘要的 audit 均显式返回：

- `algorithm_version`
- `mapping_version`
- `source_system`
- `source_table`
- `source_reference`
- `evidence_level`

增强观测的 `evidence_level` 为
`aggregated_route_state_with_artifact_hashes`，表示聚合 RouteState 已通过交付哈希
核验，但不等同于事件级原始 BGP 报文因果证据。旧库摘要为
`legacy_summary`。缺少 mapping 或算法版本时返回 `null`，不得编造版本。

## 十、兼容边界

- 旧 `/api/v1/events/observations/...` 继续作为兼容入口，内部复用通用解析与观测
  服务；
- 伊朗增强观测继续使用相同标准交付包，不改变 60 个已发布状态点；
- `backend/core/`、旧 Detection 和其他异常类型不在本合同修改范围内；
- 正常追加与原子 publication 固定由 S3 验收；
- 缺槽、处理失败、历史补正和最终性由 S4 验收；
- S5 只做跨 API、桌面、移动端和最终合同的整体效果验收。
