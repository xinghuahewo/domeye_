# 服务器 checkout 刷新：本机 Git bundle 操作合同

状态：`Designed`；本文不是服务器操作回执，不证明已经刷新、部署或验证。

## 适用原因

服务器的公开 GitHub HTTPS 出站连接不能作为已验证能力。首次 S2 归一后，GitHub
`main` 前进时，服务器源码 checkout 可能保持干净但落后主干。该差异只能通过独立的
可恢复 refresh 批次处理，不能借由活动 release、Screen 或健康接口推断已同步。

## 执行不变量

- 输入 bundle 只能由本机从冻结的 GitHub `main` 生成；传入路径必须是
  `/home/bgpdata/Domeye-Core-artifacts/incoming/<operation-id>.bundle`。
- 刷新前冻结当前 HEAD、目标 `main` 和 bundle SHA-256；服务器 checkout 必须为干净
  `main`、唯一 remote 为无凭证的公开 HTTPS `origin`，且不被进程、挂载、Git 锁或
  活动指针引用。
- 输入 bundle 及刷新前 rollback ref 必须保留在受管范围。失败时恢复刷新前 HEAD 和
  `origin/main`；旧 `/home/bgpdata/Domeye`、运行 release、服务、数据库和 GitHub 凭证
  均为零写入。
- 成功只能声明 checkout 的 HEAD、`origin/main`、branch、clean、bundle 摘要和活动
  指针读回一致；不代表生产发布或业务验证。
