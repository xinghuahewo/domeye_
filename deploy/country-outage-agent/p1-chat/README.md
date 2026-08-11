# 国家中断 P1 Chat Sidecar 发布说明

本目录发布一个与现有报告 Sidecar 分离的 P1 问答进程：

- 仅监听 `127.0.0.1:28475`；
- 仅处理 `country_outage`、RRC25 当前事件问答；
- 每轮最多一次真实模型请求，工具执行由宿主确定性控制；
- 生产发布不设费用审计硬门；保留既有单次模型用量记录，但发布只审核 CPU、RSS、调用量和错误日志；
- 报告生成、报告下载、外部证据和 RCA 均关闭；
- 正式运行只能加载 P1 专用认证注册表；模型层沿用证据只覆盖未变化的模型、提示、
  Profile、解析器和 P1 合同，新增 Registry 准入由独立影响认证负责，二者不得互相冒充。
- Tool/Operator 只能从同 release 派生的 `production_active` P2 Registry Snapshot 准入；
  shadow 快照与 production 快照禁止跨模式回退。

## 配置

把 `country-outage-p1-chat.env.example` 安装为：

`/home/bgpdata/Domeye-Core-runtime/config/country-outage-p1-chat.env`

文件必须是 `root:root 0600`。内部 Token 与现有国家中断 Agent Token 保持一致；模型凭据继续复用现有只读凭据文件。审计目录固定为 `/var/log/domeye/country-outage-p1-pi-audit`，权限 `0700`。

## 发布顺序

1. 从已经评审、CI 通过并打 annotated tag 的提交生成唯一源码归档。
2. `manage.sh prepare <release-id> <source.tar.gz> <commit> <tag>` 验证 S0B shadow
   同候选证据，并从它派生绑定 release、回滚点和源码身份的 production Snapshot。
3. `manage.sh start <release-id>` 启动并验证 readiness、模型认证、P2 Registry
   production 身份和报告路由关闭状态。
4. 再激活同一源码归档构建的 Backend，最后激活 Frontend。
5. 用 `manage.sh status` 复核；失败时按 Frontend、Backend、P1 Sidecar 的逆序回滚。

`prepare` 会执行 Sidecar 全量测试、生产依赖审计、Vendor Patch 摘要检查、P2
Alignment Hook、独立产品语义 Reviewer 和独立发布影响 Reviewer。旧 P1 认证的 28 条回执、
注册表和有效期仍逐项校验；15 个认证源码中 14 个必须逐字节不变，唯一允许差异是
`runtime-v2-semantic.ts` 的内容寻址 Registry 准入改动，并必须由影响政策、P2 同候选证据、
Reviewer 回执和一次生产真实烟测共同闭合。生产进程由发布脚本注入 P2 production 模式及
同 release 快照路径，不需要人工编辑生产配置文件。
