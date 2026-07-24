## ADDED Requirements

### Requirement: 独立运行边界
系统 MUST 作为独立 Go 进程运行，只读访问冻结 MRT 与 mapping，不得导入旧检测
Core、写数据库或调用生产接口。

#### Scenario: 旧系统不可用
- **WHEN** 旧国家中断检测服务和数据库均未启动
- **THEN** Go 引擎仍能完成固定 RRC25 研究重放和文件包输出

### Requirement: RIB 全读但只保留确定 IR 状态
系统 MUST 完整读取并校验 RIB 的全部 physical record；内存状态 MUST 只保留
revised 映射明确为 IR 且 origin 唯一的 Prefix×VP。

#### Scenario: AS_SET 或映射未知
- **WHEN** RIB entry 的 origin 为 AS_SET、confederation 或映射缺失
- **THEN** 系统将其计入 QUALITY，且不得纳入 cohort 分母

### Requirement: UPDATE 并行解析与确定性应用
系统 MUST 将 84 个 UPDATE 并行解析为稳定 shard spool，并按槽时间、shard 编号和
shard 内原始顺序应用。

#### Scenario: 同一 key 在一槽内先撤回再宣告
- **WHEN** 两个事件落入同一 shard 且 raw 坐标依次为撤回、宣告
- **THEN** 槽末状态为宣告，重复执行得到相同结果

### Requirement: 增量指标
系统 MUST 在 baseline key 可见性变化时增量调整 ASN/AFI 与 Prefix×VP 计数，
不得为生成逐槽指标扫描全球路由表。

#### Scenario: 一个 ASN 的一个 VP 撤回
- **WHEN** 该 ASN 仍有其他 baseline Prefix×VP 可见
- **THEN** 系统只更新对应计数并将 ASN 分类为 partially_visible

### Requirement: 可恢复 checkpoint
系统 MUST 保存 RIB、每个 catch-up 槽和每个正式观察点 checkpoint。恢复时 MUST
重建并对账 cohort、路由状态和增量计数，不得重复应用已完成槽。

#### Scenario: 第 21 个正式槽后进程中断
- **WHEN** 使用同一输出目录和 `--resume` 重新启动
- **THEN** 系统从 formal-021 checkpoint 继续，并最终仍只输出 60 个正式观察点

### Requirement: 完整运行验收门
系统 MUST 仅在合成测试、race detector、静态检查和 09:25 真实单文件冻结计数
全部通过后启动最后一次完整重放。

#### Scenario: 09:25 计数不一致
- **WHEN** Go 解析结果不等于 269491 physical、756983 events、721043 announce 和
  35940 withdraw
- **THEN** 系统不得启动完整 RIB 重放
