# Agent Sidecar 分层协作规则

本文件适用于 `agent-sidecar/` 及其全部子目录，并继承仓库根
[`AGENTS.md`](../AGENTS.md)。根级 Worktree、任务合同、Git、发布和状态语义继续有效；
本文件只增加 Sidecar 约束，不放宽或复制根规则。任务特例必须在 preflight 前写入该任务
合同；合同封存后发现扩权需求，必须停止并新建干净 Worktree/TASK，不得原地修改合同或
通过临时修改本文件扩权。

## 架构依据

本文件只提炼可执行不变量；详细责任、理由和合同继续以
[目标架构 v1.1](../docs/architecture/Domeye_Agent_Target_Architecture_v1.1.md)、
[ADR-001](../docs/adr/ADR-001-pi-as-agent-runtime.md)、
[首个纵向切片锚点](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)和
[用户回答与内部证据边界](../docs/architecture/Domeye_Agent_User_Answer_and_Internal_Evidence_Boundary_v1.0.md)
为依据。实现、测试或任务合同与这些依据冲突时必须停止，不得自行选择解释，并按下文
“架构变更协议”处理。

## MUST：实施硬约束

### 当前性与遗留边界

- 开始修改前，先按根级优先级读取当前任务合同、机器合同、直接调用链和对应测试；
  [`README.md`](README.md) 只提供导航，不证明某条路径已实现、已验证或已部署。
- 不得根据 `formal`、`acceptance`、`certified`、`p1`、`p2` 或目录名判断当前性。
  当前首个交互纵向切片的入口是 `src/cli/serve-interactive-agent.ts`；其他路径须以当前
  importer、`package.json` 命令、部署引用和测试共同定性。
- Formal、Acceptance 和 A4 链只允许维护，不得扩建：可以修复仍有消费者的兼容、构建
  或安全问题，也可以在独立退役任务中删除已证明无消费者的资产；不得为其新增能力、
  路由、状态机、Registry、持久化链或生产主路径。
- `start:formal:p2-s1-w5` 是明确退役的失败关闭入口，必须继续以退出码 `78` 拒绝并引导
  到 `start:interactive-agent`；不得重新接回旧 P2 DAG、调查链或执行 Registry。

### Candidate 与生成目录

- 任务引用 Candidate 时，必须读取任务指定 manifest 的 `payload.source_files` 和
  `activation`；该清单是精确证据绑定，不是全部当前代码的目录清单，也不是永久禁改表。
- 修改任一已绑定文件，或改变代码、模型、Prompt、Registry、数据、Policy、运行时 import
  闭包后，旧 Candidate 和旧 Evidence 不再适用于新字节。可以在获授权的实现任务中修改，
  但不得继续声称旧 Candidate 仍匹配、仍已验证或仍可晋级。
- 不得为维持旧绿色状态手工修改 `source_files`、digest 或 Candidate ID。生成、覆盖或新建
  successor Candidate 必须由单独明确的任务边界授权，并按架构变更协议重新取证。
- `dist/`、`node_modules/`、`output/` 和 `.tmp/` 是本地生成目录；不得手工编辑或提交。
  即使其中某个文件被 Candidate 绑定，也必须通过锁定依赖、受控 Vendor patch 和构建重现。

### 架构硬边界

- Pi 是 Agent Runtime，负责模型交互、认知循环、上下文、下一项 Capability Proposal 和
  回答草拟；Domeye Trust Kernel 独立负责身份、授权、预算、Policy、Registry、逐 Action
  准入、实现绑定、Evidence 准入与身份绑定、Receipt 和正式状态提交；L6 Artifact/Evidence
  层负责不可变或有明确 revision 的保存与重放。Pi 不得自授权、选择凭据或直接调用任意
  handler。
- 开放任务每轮只提出一个下一项 Proposal，观察真实的成功、失败、拒绝或部分结果后再
  规划。不得在入口生成完整执行 DAG，不得把 Question Template 或 Capability Family
  作为生产问题路由。
- Resolver 只能在准入后绑定已登记实现；Tool 只读取受限事实，语义计算放在版本化、可重放
  的 Domain Operator。Adapter 不得静默改变语义，也不得接受模型提供的任意 SQL 或代码。
- Interactive Action 与 Durable Investigation Job 只能按恢复、取消、重试、异步等待、
  受控 fan-out、跨会话或多次正式提交的真实耐久需求区分。Job 必须经独立准入；Workflow
  只能在 Job 内可靠执行已批准工作，不能成为第二个 Agent 或扩大权限。
- 每次正式执行必须绑定 principal、tenant、publication、预算、Policy、Registry snapshot、
  实现和输入输出身份。Trace 不是 Evidence，digest 不是授权；Artifact 和 Typed Finding
  必须保留人口、单位、时间、完整性、版本、来源和局限。
- 对外业务事实只能来自合格 Typed Finding。Host 只投影本轮最小 Answer Context；Renderer
  只能消费该 Context；Response Guard 检查的正文必须与用户最终看到的正文完全一致，
  Guard 通过后 Backend、前端、正则或第二个模型均不得改写。
- Guard `block` 时不得发布原草稿或再次调用模型。确定性回退、澄清、拒绝、停止、超时、
  Provider/Tool/Renderer 失败都不得记为 `answer_success` 或 `workflow_completed`；只有正确的
  Renderer 正文通过 Guard 且正常发布才算回答成功。
- 普通用户正文不得泄露 Candidate、Receipt、Finding、Artifact、Trace、内部 Evidence、
  SHA、代码路径、端口、内部服务名或凭据；完整审计材料保留在同一轮内部记录。
- 当前首片只认证 RRC25 固定 publication/window 上的 `TOOL-03 read_metric_series` 到
  `OP-01 series_extrema` 链。错身份、错单位、缺槽、null、empty 或不完整必须失败关闭，
  不得补成 `0`；BGP 控制面观测不得扩写为全国或用户影响、原因、责任或真实恢复。

### 命令副作用与门禁

- `npm run typecheck` 和 `npm run vendor:patch:verify` 是本地校验；`npm run build`、
  `npm test` 和 `npm run candidate:first-slice -- --dry-run` 会重建被忽略的 `dist/`。其中 dry-run
  不写 Candidate JSON，但不是文件系统只读命令。
- 未改变依赖时使用 `npm ci`；它会重建 `node_modules/`、可能访问包源，并通过 postinstall
  应用受控 Vendor patch。依赖升级只能在任务明确允许 package 与 lockfile 同步变化时，
  使用精确包名和版本执行。
- 未经任务明确授权输入、外部身份、输出位置和回滚边界，不得运行模型认证或晋级、价格
  证明、账本初始化或对账、Acceptance 报告写入、非 dry-run Candidate 生成、手工
  `vendor:patch:apply`、任何 `start:*` 或其他真实外部调用。不得读取真实 `.env`、API Key、
  生产 endpoint 或仓库外运行状态来完成普通开发测试。
- 代码修改先运行对应定向测试；交付前至少执行 `npm run typecheck`、`npm test`，最后从
  仓库根执行 `make codex-postflight`。局部测试、HTTP 200、fixture 或本地报告均不能替代
  同一 Candidate 的真实评测、独立验收或发布证据。

## 架构变更协议（MUST）

以下任一变化都属于架构或合同变化：调整 Pi 与 Trust Kernel 的责任；放宽逐 Action 准入；
改变 Action/Job/Workflow 分界；引入问题模板、固定 DAG、通用 Plan/Claim 平台；改变 Tool、
Operator、Adapter、Registry 或 Gate 语义；改变 Finding、Answer Context、Renderer、Guard、
用户最终正文或内部证据边界；扩大数据来源、observer scope、Claim 强度；改变首片固定问题、
身份、指标、执行链、J1–J5、阈值或硬边界。

发生上述变化时必须：

1. 在写实现前停止并在新任务合同中声明冲突、目标、不变量、允许路径和验证方法；当前任务
   未授权的范围不得边做边扩。
2. 先核对目标架构、ADR-001、首片锚点和用户回答边界；重要责任选择使用新 ADR，合同语义
   变化提升版本，不得静默改写既有含义或历史证据。
3. 存在未验证关键不确定性时，先做有界 Spike，用真实路径给出 `GO / REPAIR / STOP`；
   `GO` 只证明可行，不自动授权产品化、发布或部署。
4. 同步受影响的机器合同、Registry/Policy、测试、Candidate 绑定和评测协议；Candidate
   改变后重新判断旧 Evidence、Acceptance 和 Gate 的适用性，不得沿用旧绿色状态。
5. 只有同一 Candidate 的实现、真实运行、独立 Acceptance 和相应 Decision Gate 完整通过，
   才能宣称 `Verified`；合并、构建或本地测试不得越级写成 `Released` 或生产已验证。

## SHOULD：默认做法

- 只读取当前入口、直接依赖和对应测试，优先做解决当前问题所需的最小充分改动。
- 先跑受影响测试形成短循环，再跑全量 Sidecar 门禁；测试默认使用 fixture/fake，不接真实
  Provider、生产服务或长期账本。
- 仅核对旧 Candidate 是否仍匹配时，直接重算既有 `source_files` 摘要；不要用因新 TASK
  `baseCommit` 变化而生成的新 Candidate ID 与旧 ID 比较。
- 修改依赖、Pi 适配或 Vendor patch 时，将版本、lockfile、patch manifest、patch 制品、
  安装后目标摘要和相应安全测试作为一个不可拆分边界审查。
- 删除 Legacy-Frozen 资产前，检查当前 importer、package script、部署引用、测试和运行身份，
  并将“无消费者”与“未部署”分别举证。

## MAY：有条件允许

- 在任务已授权的隔离 Worktree 中执行 build、test、`npm ci` 和 Candidate dry-run，并接受其
  明确列出的本地生成或网络副作用。
- 在实现任务中修改 Candidate 绑定源码而暂不冻结 successor Candidate；此时交付状态只能是
  实现变化，必须明确旧 Candidate 已失配，不能携带旧验收结论。
- 在独立维护任务中修复 Formal、Acceptance 或 A4 的现有消费者；在独立退役任务中删除已
  证明无消费者的资产。两者都不得借机恢复旧 P1/P2 主路径或扩建新架构。
