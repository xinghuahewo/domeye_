## ADDED Requirements

### Requirement: 冻结有界研究配置
系统 MUST 使用独立版本化研究配置冻结 `collector_id=rrc25`、国家 `IR`、UTC 半开窗口 `[2026-02-27T16:00:00Z, 2026-03-06T08:40:00Z)`、五分钟粒度、基线规则、双栈计量规则、episode/wave 参数和观察截止时间。系统 MUST NOT 修改或复制覆盖 `config/data-profile.json` 的全局常量。

#### Scenario: 加载伊朗研究配置
- **WHEN** 用户启动伊朗事件研究流程
- **THEN** 系统输出规范化研究配置及其 SHA256，并确认结束点不属于 UPDATE 输入槽但可作为最后一个状态边界

#### Scenario: 配置发生变化
- **WHEN** 任一窗口、口径、阈值或映射版本发生变化
- **THEN** 系统生成新的配置哈希和研究运行身份，且 MUST NOT 覆盖旧制品

### Requirement: 状态回放被请求时解析 Seed、基线参考与 UPDATE 输入范围
当 `state_replay_requested=true` 时，系统 MUST 选择研究窗口起点时刻或此前最近一张
完整 RIB 作为 `state_seed_rib`，并额外记录严格早于起点的最近一张完整 RIB 作为
`baseline_reference_rib`。若 seed 早于起点，系统 MUST 包含从 seed 时刻到研究
窗口起点之间为构造起点状态所需的 catch-up UPDATE。研究统计窗口仍 MUST 保持为
冻结的半开区间。

#### Scenario: 状态 Seed 与基线参考可用
- **WHEN** 起点时刻或此前最近 RIB、严格早于起点的参考 RIB，以及必要 catch-up UPDATE 均完整
- **THEN** 系统记录 state seed、baseline reference、catch-up UPDATE 和窗口内制品的独立清单、角色、哈希与槽覆盖

#### Scenario: 伊朗验收窗口输入计数
- **WHEN** 系统盘点冻结的伊朗验收窗口
- **THEN** 半开窗口必须对账为 1,928 个 UPDATE 槽、21 张窗口内 RIB，并额外包含 1 张严格早于起点的 baseline reference RIB

#### Scenario: 基线链不完整
- **WHEN** state seed、baseline reference、catch-up UPDATE 或窗口内任一关键槽不可用或校验失败
- **THEN** 系统将运行标记为 `incomplete`，记录 `missing_reason`，且 MUST NOT 宣称连续状态、恢复或完整影响人口已经生成

#### Scenario: 数据库代理阶段不请求状态回放
- **WHEN** `state_replay_requested=false` 且流程只生成数据库兼容指标代理候选
- **THEN** 系统 MAY 只盘点 RIB/UPDATE 库存而不读取 seed、RIB、catch-up 或
  1,928 个 UPDATE，并 MUST 将连续状态、正式 Episode、逐 VP 人口和完整恢复标为未知

### Requirement: 固定原始制品身份与完整性
系统 MUST 为每个 RIB/UPDATE 保存 `artifact_id`、相对路径、UTC 槽、压缩字节数、文件 SHA256、压缩流 EOF/CRC 状态和解析状态。所有原始输入 MUST 只读打开并拒绝符号链接替换、读取期间变化和未列入清单的文件。

#### Scenario: 输入制品校验通过
- **WHEN** 清单中的文件身份、压缩流和哈希全部一致
- **THEN** 系统生成确定性输入 manifest、覆盖摘要和 `SHA256SUMS`

#### Scenario: 文件在读取期间变化
- **WHEN** 文件 inode、大小、mtime、ctime 或内容哈希在读取期间发生变化
- **THEN** 系统失败关闭并留下可定位的完整性错误，MUST NOT 使用部分结果

### Requirement: 冻结 AS 国家映射
系统 MUST 只读复制旧系统本次检测使用的 AS→国家静态映射，记录来源路径、文件哈希、值域、缺失 ASN 和重复冲突；同时允许生成独立的修订映射视图，但 MUST NOT 静默覆盖兼容映射。

#### Scenario: 形成基线伊朗 ASN cohort
- **WHEN** seed 状态中的 origin ASN 可由兼容映射定位为 `IR`
- **THEN** 系统将其纳入兼容 cohort，并记录该 ASN 的映射证据和映射版本

#### Scenario: ASN 映射未知或冲突
- **WHEN** origin ASN 没有国家映射或存在冲突
- **THEN** 系统保留该路由状态并标记 `country_mapping_unknown` 或 `country_mapping_conflict`，MUST NOT 猜测为 IR 或非 IR

### Requirement: 执行资源与只读边界
系统 MUST 在执行前和执行中记录新增原始读取量、单进程运行时间、临时空间、输出空间和写入目标。任何预计或实际新增原始读取达到 50GB、单次运行达到 10 分钟、临时空间达到 5GB，或涉及数据库写入时，系统 MUST 立即停止并要求人工批准。三项资源上限均为排他上限；只有严格低于对应上限的运行才允许继续，预计将触及上限时必须在触及前停止。

#### Scenario: 有界分块执行
- **WHEN** 完整研究需要多个分块且每个分块严格低于审批阈值
- **THEN** 系统允许断点续跑并汇总累计用量，但每个分块都必须记录自身边界与资源证据

#### Scenario: 检测到数据库写入目标
- **WHEN** 命令参数、连接配置或代码路径会写入 PostgreSQL、SQLite 或其他数据库
- **THEN** 系统在写入前拒绝执行并报告审批需求

#### Scenario: 用户在软停后明确批准同范围延长
- **WHEN** 固定 artifact、总压缩字节、临时空间和零数据库写入边界均未变化，运行仅因
  时间软限停止，且用户明确批准无错误时继续
- **THEN** 系统 MAY 为同一有界请求记录新的时间上限并重新执行；该批准 MUST 写入
  执行记录，且不得授权扩大 artifact、实体或数据库写入范围
