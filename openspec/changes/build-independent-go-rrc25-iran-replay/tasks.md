## 1. 独立引擎

- [x] 1.1 新建无第三方依赖的 Go 模块和冻结输入选择器
- [x] 1.2 实现 TABLE_DUMP_V2 RIB 全量解析与确定 IR cohort 过滤
- [x] 1.3 实现 BGP4MP UPDATE、AS4_PATH、MP NLRI 和 AS_PATHLIMIT
- [x] 1.4 实现 84 槽并行解析、稳定 shard spool 和顺序应用
- [x] 1.5 实现增量指标、动态 IR 分离和双栈分类

## 2. 恢复与结果

- [x] 2.1 实现 RIB、25 个 catch-up 和 60 个正式 checkpoint
- [x] 2.2 实现 checkpoint 重载、计数重建与坐标对账
- [x] 2.3 实现 Observation、Incident/Episode/Wave、QUALITY 和中文报告
- [x] 2.4 实现 RUNNING/COMPLETE 与交付文件 SHA-256

## 3. 验证

- [x] 3.1 通过合成 MRT、增量状态、分片、恢复和事件模型测试
- [x] 3.2 通过 `go test -race ./...` 与 `go vet ./...`
- [x] 3.3 通过 09:25 真实 UPDATE 单文件冻结计数对账
- [x] 3.4 严格校验 OpenSpec 并固定 Git 提交

## 4. 最后一次真实重放

- [x] 4.1 预检输出目录、文件描述符、CPU、内存和磁盘
- [x] 4.2 执行第二次且最后一次 1+84 完整真实重放
- [x] 4.3 核验 25+60、23:00、checkpoint、QUALITY 和文件哈希
- [x] 4.4 完成数据库代理曲线对账与中文执行记录
- [ ] 4.5 推送最终结果摘要和 Git 闭环
