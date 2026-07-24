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
