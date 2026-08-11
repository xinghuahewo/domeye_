# 国家中断 P1 Chat Sidecar 发布说明

本目录发布一个与现有报告 Sidecar 分离的 P1 问答进程：

- 仅监听 `127.0.0.1:28475`；
- 仅处理 `country_outage`、RRC25 当前事件问答；
- 每轮最多一次真实模型请求，工具执行由宿主确定性控制；
- 不设业务总费用上限，但每次真实调用必须记录 token 与估算美元费用；
- 报告生成、报告下载、外部证据和 RCA 均关闭；
- 正式运行只能加载 P1 专用认证注册表及其同候选认证回执。

## 配置

把 `country-outage-p1-chat.env.example` 安装为：

`/home/bgpdata/Domeye-Core-runtime/config/country-outage-p1-chat.env`

文件必须是 `root:root 0600`。内部 Token 与现有国家中断 Agent Token 保持一致；模型凭据继续复用现有只读凭据文件。审计目录固定为 `/var/log/domeye/country-outage-p1-pi-audit`，权限 `0700`。

## 发布顺序

1. 从已经评审、CI 通过并打 annotated tag 的提交生成唯一源码归档。
2. `manage.sh prepare <release-id> <source.tar.gz> <commit> <tag>` 生成不可变 P1 Sidecar release。
3. `manage.sh start <release-id>` 启动并验证 readiness、认证身份、费用审计合同和报告路由关闭状态。
4. 再激活同一源码归档构建的 Backend，最后激活 Frontend。
5. 用 `manage.sh status` 复核；失败时按 Frontend、Backend、P1 Sidecar 的逆序回滚。

`prepare` 会执行 Sidecar 全量测试、生产依赖审计、Vendor Patch 摘要检查，并逐项校验模型认证的源码、28 条回执、正式注册表和有效期。
