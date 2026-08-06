# RRC25 伊朗事件独立 Go 重放引擎

本模块是 `2026-02-28 18:05–23:00（北京时间）` 伊朗路由事件的独立研究重放器。
它不导入旧检测系统、不连接生产数据库，也不修改旧事件事实表。

固定处理边界：

- 完整读取并校验 `bview.20260228.0800.gz`，但内存中只保留确定属于伊朗的
  seed Prefix×VP cohort；
- 并行解析 `08:00–15:00 UTC` 的 84 个 UPDATE 文件；
- UPDATE 先按稳定 route key 分片，状态按时间槽和 shard 编号顺序应用；
- 国家和 ASN 指标由 key 可见性变化增量维护，不逐槽扫描全球路由表；
- RIB、catch-up 和正式窗口均使用可恢复的原子 checkpoint；
- 正式结果固定输出窗口起点和 59 个槽末，共 60 个观察点，最后一个观察点为
  `2026-02-28T15:00:00Z`。

## 命令

```bash
go test ./...

go run ./cmd/rrc25-replay \
  --raw-root /home/bgpdata/data/ripe \
  --selection /path/to/full-selection.json \
  --compatible-mapping /path/to/compatible-mapping.json \
  --revised-mapping /path/to/revised-mapping.json \
  --output /path/to/state-replay-1805-2300-go-v1
```

真实完整运行只允许在合成测试和 `updates.20260228.0925.gz` 单文件验证通过后启动。
运行目录采用 create-only 语义；中途失败时保留 `RUNNING.json`、已解析 UPDATE
spool 和最新 checkpoint，可用同一命令加 `--resume` 继续，不重新读取已完成阶段。

## RRC25 224-310 RouteEvent 制品库

`cmd/rrc25-route-event-store` 是数据层 S1 的独立入口。它只接受冻结的
`[2026-02-24T00:00:00Z, 2026-03-11T00:00:00Z)`、`rrc25`、4,320 个 UPDATE
和 2 个修复制品身份。

输出根目录会逐字保留冻结输入清单为 `input-selection.json`。全局清单还会记录
两个损坏原文件的 SHA-256/字节数、隔离替换 artifact 的完整身份、双方关系和
`repair_provenance_sha256`。替换文件只能作为新 artifact 被选择，不能覆盖或冒充
损坏原文件；修复血缘同时参与 import run 身份和全局内容身份。

输出按原始 artifact 分区，每个分区包含：

- `records.jsonl.gz`：MRT record ordinal、解压偏移、长度和 SHA-256；
- `events.jsonl.gz`：稳定 RouteEvent ID、VP、prefix、动作、origin、AS_PATH 引用、
  属性 SHA-256 和 record/element 坐标；
- `paths.jsonl.gz`：保留 AS_SEQUENCE、AS_SET 和 confederation segment 的内容寻址
  AS_PATH 字典；
- `manifest.json`：输入 artifact、人口、文件摘要和分区内容身份。

物理事件行不重复全局和分区字段。逻辑 RouteEvent 由全局清单、分区清单中的
`ingest_time_utc` / `parse_time_utc`、事件行、AS_PATH 字典和 record sidecar
确定性组合。操作时间不进入语义 content SHA，因此重跑可以有不同的处理时间，
但不会改变事件、文件和数据集内容身份。

AS4_PATH 按 [RFC 6793](https://datatracker.ietf.org/doc/html/rfc6793) 重建，不将
AS_SET 或 confederation 拍平。紧凑 RouteDelta 的既有二进制格式不变，也不被冒充为
完整 RouteEvent。

无写入 preflight：

```bash
go run ./cmd/rrc25-route-event-store \
  --phase preflight \
  --raw-root /path/to/frozen-input \
  --selection /path/to/224-310.selection.json \
  --implementation-id git:<40位提交SHA>
```

单 artifact 真实输入验证（0 为 Seed RIB，1 为第一个 UPDATE）：

```bash
go run ./cmd/rrc25-route-event-store \
  --phase artifact \
  --artifact-index 1 \
  --raw-root /path/to/frozen-input \
  --selection /path/to/224-310.selection.json \
  --implementation-id git:<40位提交SHA> \
  --output /path/to/create-only-output
```

全窗运行：

```bash
go run ./cmd/rrc25-route-event-store \
  --phase run \
  --raw-root /path/to/frozen-input \
  --selection /path/to/224-310.selection.json \
  --implementation-id git:<40位提交SHA> \
  --output /path/to/create-only-output \
  --workers 8
```

已完成的分区只能在 `--resume` 时逐文件重算大小和 SHA-256 后复用。该入口
还使用输出目录级非阻塞写锁，拒绝两个进程同时写同一候选。它不生成 RouteState、
Prefix×VP Evidence、国家/ASN 指标或 Publication；Prefix×VP 只是后续 RouteState
的主键维度。`implementation_id` 进入 import run、RUNNING 标记和最终清单，禁止
不同实现身份在一次续跑中混用。
