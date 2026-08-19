# 国家中断 Interactive Agent 发布说明

本目录暂时保留历史仓库路径，但只管理新架构组件
`domeye_interactive_agent_sidecar`。生产运行根为
`/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent`，进程只监听
`127.0.0.1:28476`，唯一入口为
`agent-sidecar/dist/src/cli/serve-interactive-agent.js`。

## 状态边界

- Candidate 继续声明 `local_evaluation_only` 与 `production_deployed=false`；发布器不得改写它。
- 不可变 `RELEASE-MANIFEST.json` 只绑定源码 commit、annotated tag、源码归档摘要、
  Candidate 身份、30/30 Accepted + DG1 GO 回执、独立验收重放回执，以及固定运行入口。
  它不声明已经部署或验证。
- `prepare` 只信任固定 checkout `/home/bgpdata/Domeye-Core`：tag 必须是 annotated
  tag，解引用 commit、本地 `main` 和 `origin/main` 必须都等于输入 commit；输入归档规范
  解包后的目录、文件路径、文件字节和可执行位必须与该 commit 的 `git archive` 完全一致。
  manager 会先确认 origin 精确指向 GitHub 权威仓库，再联网刷新该 tag 与 `origin/main`；
  无法 fetch 时直接失败，不能把本地伪造的 remote-tracking ref 当成 GitHub 已同步。
- Candidate 的 `base_commit` 必须是 release source commit 的祖先；其后的首个 ancestry commit
  必须单父、父提交精确等于 `base_commit`，且只修改 Candidate manifest。该提交的 manifest
  字节必须与 release source 一致，此后直到 release source commit 都不得再次修改它。
- `deployment/ACCEPTANCE-REPLAY.json` 由正式 Candidate loader 校验全部 `source_files`，并由
  现有首片 finalizer 重放 summary、原始 evidence JSONL 和独立 review；重放产物必须与
  `acceptance-record-final.json` 字节一致。release 保留重放所需的 TypeScript 构建依赖；
  每次 `status`、晋级或回滚校验都会重新运行 loader 与 finalizer，并要求当前结果与冻结
  回执、final record 字节精确一致。
- `state/active.json` 只在 `current`、实际 Node 进程、28476 监听和 Candidate readiness
  精确一致后原子写入 `deployment_state=deployed`。
- `state/promotions/<release-id>.json` 只在 Backend 固定问题得到完整成功回答后写入
  `promotion_state=verified`。回答必须由 Renderer 产生、Guard pass、Finding 与冻结 Oracle
  完全一致且公开答案存在；请求固定经过公开 nginx
  `http://127.0.0.1:28471/api/v2/country-outage/chat`，不以直连 Backend 代替。
- promotion 以 base64 保留 Backend 原始响应并绑定字节摘要；后续每次 `status` 都从该响应
  重新执行晋级 verifier 和正式 Guard。CAP-006、CAP-016、两类 Artifact、Observation 以及
  Finding 的 receipt/artifact 引用必须形成精确闭包。
- 晋级校验不信任 Backend 自报的 Guard 字段：它从公开 Typed Finding 重建 Answer Context
  和 Renderer Draft，调用同 release 的正式 Finding Answer Guard；占位错误文本不能晋级。
- `status`、晋级前后以及回滚恢复都要求 `active.json.runtime.pid`、同 release 入口进程与
  `127.0.0.1:28476` 唯一监听者是同一个 PID；`0.0.0.0`、IPv6 wildcard 或额外监听均拒绝。
- 晋级最终 GET 必须精确绑定本次创建返回的 `conversation_id` 与本次 POST 返回的
  `turn_id`，不能复用旧正确会话。成功闭包固定为三次 cognition 与末次唯一 Renderer，
  四次 attempt 都必须绑定 Candidate 的 provider/model/version/response model、按顺序完成，
  同时保留单 Turn 最多 10 次和 `audit_only`、无费用上限的预算语义。
- 拒绝、回退、澄清、停止、空答案、供应方失败或任一非完整流程都不会晋级。

首个新架构 release 的回滚模式固定为 `fail_closed`：停止新 Sidecar，且不启动任何
旧 Sidecar release。Backend 首次切换由后续集成发布保证旧 route 不回滚；新 Sidecar
不可用时应返回 503。只有当前 release 已经完整 `deployed + verified`，才能准备绑定它的
第二个 release。存在这种前序新架构 release 时，才允许 `same_schema_only` 回滚。

每次切换、停止或回滚前，现有 promotion 都原子移入
`state/promotion-history/<release-id>/`，以 `0600` 保留且禁止覆盖。重新启动前序 release
会产生新的 `active.json` 摘要和激活时间，因此旧 promotion 永远不能复用；必须重新经过
公开 28471 固定问题并生成新 promotion，才算恢复成功。重新验证失败时停止进程、清除
`active.json` 与 `current`，保持 `fail_closed`。停止或回滚时若 `current` 或 `active.json`
任一状态无法清除，manager 会返回失败并保留可处置证据，不会宣称流程完成。

## 配置

将 `country-outage-interactive-agent.env.example` 安装为：

`/home/bgpdata/Domeye-Core-runtime/config/country-outage-interactive-agent.env`

生产文件必须为 `root:root 0600`。Candidate manifest、项目根、模型凭据、数据 API、
会话 TTL、超时、回环 host/port 和内部 Token 都是必填项；未知键、重复键或含空白值
会失败关闭。

## 发布顺序

1. 从已评审提交生成源码归档并创建同名 annotated tag。
2. `manage.sh prepare <release-id> <source.tar.gz> <commit> <annotated-tag>`：构建、测试，
   并冻结 release；发布级门要求最新 final Acceptance Record 与 raw evidence 均为 30/30。
3. `manage.sh start <release-id>`：切换并启动 Sidecar；成功后才写 `active.json`。
4. 让同一 Backend release 读取新配置并重启，然后执行 `manage.sh promote <release-id>`。
5. `manage.sh status` 同时核对 release、active、readiness 与可选 promotion。
6. 失败时执行 `manage.sh rollback`；只可能失败关闭，或重新启动并重新验证同 schema
   新架构 release。

`prepare`、`start` 和 `promote` 分开执行，避免把“制品可发布”“进程已部署”和
“生产正确回答已验证”混成一个状态。
