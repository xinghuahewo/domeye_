## Context

固定输入为 RRC25 08:00 RIB、25 个 catch-up UPDATE 和 59 个正式 UPDATE。
研究窗口为 UTC `[10:05,15:00)`，输出窗口起点加 59 个槽末，共 60 个状态。

## Decisions

### 1. 旧检测系统完全在边界外

Go 引擎只读取冻结 JSON 和只读 MRT 文件，输出独立研究包。它不导入
`BGPOutage`、旧数据库 Repository 或生产服务，也不写任何数据库。

### 2. RIB 全读、状态只留 IR

RIB 的每个 MRT physical record、peer table、prefix 和 AS_PATH 均完整解析并校验。
只有 revised 映射明确属于 IR 且 origin 可唯一解析的 Prefix×VP 进入固定 cohort；
映射未知、AS_SET、confederation 和明确非 IR 只进入 QUALITY 计数。

### 3. UPDATE 解析与状态应用解耦

84 个 UPDATE 由固定 worker pool 并行解析。每个 RouteEvent 按稳定 route key 写入
shard spool。应用严格按槽时间递增，再按 shard 编号递增；同一 shard 内保持原始
record/element 顺序。不同 shard 没有共享 key，因此分片重排不改变状态结果。

### 4. 指标增量维护

baseline key 的 visible 状态变化只更新所属 ASN/AFI 和全局 Prefix×VP 计数。
快照只遍历固定 IR ASN 集合形成分类；动态 IR origin 独立保存且不改变分母。

### 5. checkpoint 是运行合同

RIB 完成后写 `rib.json.gz`；25 个 catch-up 和 60 个正式状态分别保留原子
checkpoint。checkpoint 重载后必须重建增量计数并与保存值对账。UPDATE spool
带每 shard 记录数、字节数和 SHA-256；恢复时不得重复解析或应用已完成槽。

### 6. 第二次运行是最后一次完整尝试

只有 race-enabled 合成测试、静态检查和 09:25 真实单文件冻结计数全部通过后，
才启动第二次完整运行。若运行中断，只能从本次运行目录 checkpoint 继续，不能
新建第三次完整运行。

## Failure Handling

- RIB/UPDATE size、SHA、gzip、MRT framing 或槽时间不一致：失败关闭并保留已完成
  checkpoint。
- shard spool 坐标、记录数或 SHA 不一致：停止应用，不发布 COMPLETE。
- checkpoint 无法重建相同 cohort/计数：拒绝恢复。
- 只有 25+60 人口闭合、最后观察时间为 15:00Z 且结果文件哈希生成后，才删除
  RUNNING 标记并写 COMPLETE。
