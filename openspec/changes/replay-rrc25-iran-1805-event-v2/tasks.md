## 1. 固定输入与状态层

- [x] 1.1 冻结 1 个 bview、25 个 catch-up 和 59 个正式 UPDATE
- [x] 1.2 实现 seed cohort、双栈 ASN 与 Prefix×VP 同快照分类
- [x] 1.3 实现动态 IR 分离、未知不补零和 catch-up 正常带
- [x] 1.4 实现 create-only 数据包和固定中文报告

## 2. 事件模型

- [x] 2.1 实现 Incident/Episode/Observation/Milestone v2 纯转换器
- [x] 2.2 实现两槽检测、六槽恢复、左删失、多 Episode 和多 Wave
- [x] 2.3 实现同 snapshot peak/trough 和九个结构化字段

## 3. Core 与数据库

- [x] 3.1 将国家中断 Core 入口改为结构化 Observation
- [x] 3.2 新增事务 Repository、追加写冲突检查和旧字段投影
- [x] 3.3 新增 PostgreSQL 迁移和结构化读取入口
- [ ] 3.4 在隔离测试数据库执行迁移、回滚和兼容读取验证

## 4. 伊朗真实执行

- [ ] 4.1 通过定向单元测试、Core 哈希和 OpenSpec 严格校验
- [ ] 4.2 在服务器正式运行一次 1+84 输入
- [ ] 4.3 验证 60 个 Observation、伊朗结果和数据包清单
- [ ] 4.4 完成数据库曲线与 `199/595、73/126、176/556` 中文对账
- [ ] 4.5 提交并推送代码、迁移、规格和轻量结果摘要
