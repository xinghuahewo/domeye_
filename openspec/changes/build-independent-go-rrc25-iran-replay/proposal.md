## Why

首次 Python 状态重放虽然在测试中正确，但真实运行需要把全球 RIB 状态长期保留在
内存，并在每个槽末重新扫描全表；同时失败时没有可恢复 checkpoint。真实数据在
第 18 个 catch-up 文件遇到历史属性 21 后终止，证明继续扩大旧实现会把解析、
状态、指标和恢复风险耦合在一起。

## What Changes

- 新建完全独立的 Go 重放模块，不调用旧国家中断检测器，不写生产数据库。
- 完整顺序读取和校验 RIB，但只保留确定 IR origin 的 Prefix×VP cohort。
- 将 84 个 UPDATE 作为并行任务解析成确定性 shard spool，再按时间槽和 shard
  编号顺序应用。
- 使用 key 可见性变化增量维护 ASN、地址族和 Prefix×VP 指标；逐槽只遍历 IR
  cohort，不扫描全球路由表。
- 保存 RIB、每个 catch-up 槽和每个正式槽 checkpoint；失败后可从同一运行目录
  继续，而不重新读取已完成阶段。
- 合成测试和 09:25 真实单文件对账通过后，才允许第二次且最后一次完整真实重放。

## Capabilities

### New Capabilities

- `independent-go-rrc25-iran-replay`：独立 Go MRT 解析、IR 状态重放、增量指标、
  shard spool、checkpoint 和研究结果包。

## Impact

- 新增 `tools/rrc25-iran-replay-go/`，不修改旧检测 Core 或生产运行入口。
- 新增研究输出目录，不部署生产、不扩大到 1,928 个 UPDATE。
- 最终结果仍提供 60 个正式观察点、Incident/Episode/Wave、QUALITY 和中文报告。
