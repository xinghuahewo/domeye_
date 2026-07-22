## ADDED Requirements

### Requirement: 发布确定性研究制品包
系统 MUST 将研究结果发布为新建、只读、内容寻址的文件制品包，至少包含运行 manifest、输入清单、配置、国家映射摘要、RouteEvent、raw refs、五分钟样本、episode、wave、逐 ASN/前缀影响、Evidence Bundle v2、质量报告、对账结果、中文报告和 `SHA256SUMS`。

#### Scenario: 成功发布研究包
- **WHEN** 所有阻断质量门通过且输出目录尚不存在
- **THEN** 系统先在同文件系统 staging 目录完成校验，再原子发布只读目录并记录制品包指纹

#### Scenario: 输出目录已存在
- **WHEN** 目标研究运行目录已经存在
- **THEN** 系统拒绝覆盖；相同运行只允许验证或续跑未发布分块

### Requirement: Evidence v2 引用闭合
系统 MUST 为旧 Incident `country_outage/2026-02-27 09:12:32/IR/1/r` 生成事件级 Evidence Bundle v2，并将其与新 episode/wave 映射、五分钟 MetricWindow、相关 RouteEvent、raw record refs、处理版本、输入哈希和质量限制闭合。旧 Incident 时间 MUST 保留为来源事实，不能被静默改写为新 onset。

#### Scenario: 原始证据完整
- **WHEN** RouteEvent 可精确定位到已验证制品的 record 和 element
- **THEN** Evidence v2 将其标为 `raw_traceable`，并保证每个 raw ref 至少被一条 RouteEvent 引用

#### Scenario: 研究 episode 与旧 Incident 时间冲突
- **WHEN** 新识别的 onset、peak 或 wave 与旧 `s_time/event_info` 不一致
- **THEN** Evidence 包同时保留 source fact 和 research result，以 reconciliation evidence 表达差异，MUST NOT 覆盖来源字段

### Requirement: 结论分级与原报告对账
系统 MUST 对报告中的时间、IPv4 降幅、恢复状态、`199/595`、`73/126`、数据库 `176/556`、主动撤回、物理断路、BGP 会话关闭、流量影响和政府意图逐项输出 `confirmed`、`revised`、`unverifiable` 或 `hypothesis_only`，并为每项提供口径、证据引用、反向证据与限制。

#### Scenario: 数值可由 RRC25 复算但与报告不同
- **WHEN** 冻结口径的复算值与报告值不一致
- **THEN** 系统标记为 `revised`，给出新值、差异和证据，MUST NOT 为匹配报告调整口径

#### Scenario: RRC25 无法证明因果或流量结论
- **WHEN** 主张需要流量、会话、物理链路、主动探测或政治意图证据
- **THEN** 系统标记为 `unverifiable` 或 `hypothesis_only`，MUST NOT 生成因果结论

### Requirement: 质量门失败关闭
系统 MUST 分别报告输入完整性、解析完整性、状态连续性、VP 覆盖、映射覆盖、稳定身份、引用闭合、缺失语义、资源使用和复现性。任一阻断项失败时，制品包 MUST 标为 `incomplete/not_accepted`，且报告不得声称研究闭环完成。

#### Scenario: raw ref 无法解析
- **WHEN** 任一声称 raw_traceable 的 RouteEvent 没有可验证 raw ref
- **THEN** 质量门失败，并定位具体 RouteEvent、artifact、record 和 element

#### Scenario: unknown 被填充为零
- **WHEN** 合同或对账发现未知、缺测或解析失败被序列化为数值 0
- **THEN** 质量门失败并报告字段、时间槽和来源

### Requirement: 可重复入口与语义复现
系统 MUST 提供单一研究入口协调盘点、分块解析、回放、分析、证据和报告；底层步骤 MUST 可独立恢复和验证。相同输入、配置、代码和映射的两次运行 MUST 产生相同稳定身份、记录顺序和语义指纹，运行时间等非语义字段不得进入语义指纹。

#### Scenario: 完整语义复现
- **WHEN** 在不同空输出目录中使用相同冻结输入执行两次研究
- **THEN** 两次运行的业务制品语义指纹一致，差异仅限被明确排除的运行元数据

#### Scenario: 统一入口不可用
- **WHEN** 环境限制无法安全地在一个进程中完成全部步骤
- **THEN** 系统仍提供一个协调命令按分块调用可恢复步骤，并保持每个步骤的资源门和确定性

### Requirement: 分层交付与旧报告保护
系统 MUST 将 OpenSpec、代码、配置、数据字典、测试和中文摘要保存在新项目 Git；大型派生数据保存在服务器独立研究制品目录；Git 中只引用制品路径、release ID、SHA256 和复现命令。系统 MUST NOT 修改原始 Word 报告，而应生成独立的《RRC25 伊朗国家路由中断事件复算与对账报告》。

#### Scenario: 交付研究结果
- **WHEN** 伊朗事件验收结束
- **THEN** Git 中的中文报告可通过服务器制品清单和哈希复核，但不包含大体积原始或派生明细
