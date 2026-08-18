# Domeye-Core S2 Git bundle 受控替代路径

## 触发事实

2026-08-18 的服务器只读测量中，无凭证 `curl https://github.com/` 在 15 秒超时；首次
公开 HTTPS clone 未接收 Git 对象。归一器自动回滚，原 checkout 恢复，旧 Domeye 与活动
release 指针未改变。该现象不能通过增加或修改服务器 GitHub 凭证解决，也不授权尝试该
操作。

## 受控替代

仅从本机已验证的 GitHub `main@<完整 SHA>` 创建完整 Git bundle，并记录 bundle
SHA-256。bundle 经既有 SSH 上传到：

```text
/home/bgpdata/Domeye-Core-artifacts/incoming/<operation-id>.bundle
```

归一器只接受这一精确路径；它仍先验证进程、挂载、锁、活动指针、原 checkout SHA 与同
文件系统，再 archive、隔离、从 bundle clone，并把 `origin` 固定为公开 GitHub HTTPS
URL。成功后 bundle 移入同批 quarantine，和 archive、原 checkout、回执一同保留。

## 不变量

- 不读写旧 `/home/bgpdata/Domeye` 或其进程；
- 不修改服务器 GitHub 凭证、全局 Git 配置、release 指针或生产服务；
- 新 checkout 必须同时满足 `HEAD == origin/main == 冻结 main SHA`、分支为 `main`、
  `origin` 为公开 URL、工作树干净；
- bundle 传输不等于服务器恢复 GitHub HTTPS 出站；该网络问题另行治理；
- 任一失败恢复原 checkout 精确路径，不删除 archive、bundle 或失败回执。
