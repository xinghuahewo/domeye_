## ADDED Requirements

### Requirement: RIB 与 UPDATE 解析保真
系统 MUST 将 RIB 和 UPDATE 解析为保留 MRT physical record 顺序与 route element 顺序的规范观测。每个观测 MUST 包含 collector、peer IP、peer ASN、AFI/SAFI、prefix、action、AS_PATH segment、origin 解释、event time，以及 `artifact_id + record_ordinal + element_ordinal` 原始坐标。

#### Scenario: 解析 RIB 路由元素
- **WHEN** TABLE_DUMP 或 TABLE_DUMP_V2 记录包含一个或多个 route element
- **THEN** 系统为每个元素生成 `rib_snapshot` 观测，并通过黄金 fixture 证明 ordinal、peer、prefix 和 AS_PATH segment 保真

#### Scenario: 解析 UPDATE 撤回
- **WHEN** BGP4MP UPDATE 包含 IPv4 或 IPv6 withdrawal
- **THEN** 系统生成 `withdraw` 观测，保留 VP 和原始坐标，并将 AS_PATH 明确标为 `not_applicable`

#### Scenario: 遇到未验收 MRT 类型
- **WHEN** 输入是 OPEN、NOTIFICATION、ROUTE-REFRESH、Add-Path 未支持变体或未知类型
- **THEN** 系统记录明确解析状态并失败关闭相关连续性，MUST NOT 伪造成 RouteEvent

### Requirement: Peer 会话观测独立建模
系统 MUST 将 BGP4MP STATE_CHANGE 解析为带 VP、前后状态、时间和 raw ref 的 `PeerSessionObservation`，MUST NOT 将会话事件伪造成带前缀的 RouteEvent。会话不可用导致的观测缺失 MUST 与显式 prefix withdrawal 分开表达。

#### Scenario: Peer 会话进入非 Established 状态
- **WHEN** 某 VP 的原始记录显示会话从 Established 进入其他状态
- **THEN** 系统将该 VP 的后续可观测性标记为受限或未知，并保留会话证据，MUST NOT 自动把其全部前缀计为显式撤回

#### Scenario: 没有结构化会话证据
- **WHEN** 某时间段缺少可解释 VP 覆盖变化的 STATE_CHANGE 记录
- **THEN** 系统只报告已观察到的路由状态与 VP 覆盖限制，MUST NOT 推断 BGP 会话已关闭

### Requirement: 稳定观测身份与确定顺序
系统 MUST 使用文件 SHA256、record ordinal 和 element ordinal 生成稳定 RouteEvent ID，并按事件时间、制品槽、record ordinal、element ordinal 的冻结次序回放。相同输入与配置的重跑 MUST 产生相同稳定 ID、同序语义记录和相同内容指纹。

#### Scenario: 幂等重跑同一分块
- **WHEN** 同一分块使用相同清单、解析器、配置和映射重新运行
- **THEN** 业务制品的记录顺序、稳定 ID 和语义 SHA256 完全一致

#### Scenario: 同一时间存在多个元素
- **WHEN** 多条观测具有相同 event time
- **THEN** 系统使用冻结的制品和原始 ordinal 次序消除非确定性，MUST NOT 依赖并发完成顺序

### Requirement: 双栈逐 VP 路由状态
系统 MUST 以 `collector + VP + AFI/SAFI + prefix` 为状态键，用 seed RIB 初始化状态并按顺序应用 announce/withdraw。系统 MUST 同时处理 IPv4 和 IPv6，保留 MOAS、AS_SET、空 AS_PATH 和 peer 变化的质量语义。

#### Scenario: VP 撤回一个前缀
- **WHEN** 某 VP 对已存在前缀发送 withdrawal
- **THEN** 系统只移除该 VP 的该前缀状态，其他 VP 的状态保持不变

#### Scenario: 前缀发生 origin 变化
- **WHEN** announce 将同一 VP/prefix 的 origin 或 AS_PATH 替换为新值
- **THEN** 系统保存新状态和变化 RouteEvent，同时保留可追溯的前一状态引用

#### Scenario: 输入连续性存在缺口
- **WHEN** 任一必需 UPDATE 槽解析失败或不可用
- **THEN** 从缺口开始的受影响状态 MUST 标记为 `unknown_after_gap`，MUST NOT 跨缺口推断恢复

### Requirement: 相关路由变化的完整保留
系统 MUST 保留基线 IR cohort 的所有前缀变化，以及研究窗口中新出现且 origin 映射为 IR 的前缀变化。系统 MUST 保留所有相关 VP 的变化记录，不得只抽取触发、低谷或恢复时刻的代表样本。

#### Scenario: 窗口内出现新的 IR origin 前缀
- **WHEN** 一个基线中不存在的前缀在窗口内由映射为 IR 的 origin 宣告
- **THEN** 系统将该前缀加入动态研究集合，并从首次相关观测开始保留其后续变化

#### Scenario: 无关前缀出现大量更新
- **WHEN** RouteEvent 与基线 IR cohort 或动态 IR origin 均无关
- **THEN** 系统可以只保留原始制品级统计而不复制该 RouteEvent，但 MUST NOT 改变相关前缀的 record/element 原始坐标

### Requirement: 文件型分块与断点续跑
系统 MUST 使用不可变文件分片、内容清单和原子发布实现分块与断点续跑，MUST NOT 依赖数据库保存研究状态。检查点 MUST 位于完整 record 边界并绑定输入、配置、代码和映射哈希。

#### Scenario: 分块在时间限制前主动结束
- **WHEN** 分块接近冻结的安全运行时限
- **THEN** 系统完成当前 record、原子发布检查点并正常退出，后续命令从下一 record 继续

#### Scenario: 检查点与当前输入不一致
- **WHEN** 恢复时任一输入、配置、代码或映射哈希变化
- **THEN** 系统拒绝复用检查点并要求创建新研究运行
