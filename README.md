# Domeye：从这里开始

这个仓库同时承载两条有关联但不能混为一谈的主线：已经存在的 **Domeye Core
运行时**，以及以首个纵向切片为锚点的 **Domeye 国家网络中断调查 Agent 重构**。

## 先认清两条主线

| 主线 | 当前含义 | 状态依据 | 不能据此宣称 |
|---|---|---|---|
| Domeye Core 现有运行时 | Flask API、Vue 前端、只读数据访问、离线检测核心及部署工具；当前使用二三月固定数据档 | 当前代码、测试、`config/data-profile.json` 与运行回执 | Agent 新架构已经实现或通过真实评测 |
| **Domeye 国家网络中断调查 Agent** | 当前首片只在一个已绑定事件、publication、revision、单一 RRC25、固定 IPv4 人口和冻结时间窗内生成有范围与局限说明的控制面事实 | [产品边界](docs/architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md)与[首片锚点](docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) | 已具备全国中断判断、真实影响、原因、责任、恢复或 RCA |

“首个纵向切片”只是第一条完整用户闭环：从一个固定问题，经真实 Tool、确定性
Operator、Typed Finding 和回答边界，到安全答案。它不是新产品名，也不是要求先建完
所有架构层。当前合同状态是 `Designed`；旧代码、旧评测、PR 或 Issue 状态都不能自动
把它提升为 `Implemented` 或 `Verified`。

产品主名称描述长期调查目标；当前能力说明是 **“当前已接入证据能力：RRC25 BGP
控制面”**。两者必须同时展示，不能用当前单一数据能力替代产品名，也不能依据产品名
放大当前事实边界。

## 七份核心交接文档

新接手者按下列顺序建立心智模型。七份材料分工不同，不构成七个平行开发项目。

| 顺序 | 文档 | 只回答什么 |
|---:|---|---|
| 1 | [当前代码基线](docs/architecture/Domeye_Current_Code_Baseline_2026-08-16.md) | `main@6a4bbd4…` 当时真实存在什么；它是固定观察，不会随 `main` 自动更新 |
| 2 | [产品、数据与 Claim 边界 v1.1](docs/architecture/Domeye_Product_Data_Claim_Boundary_v1.1.md) | 产品叫什么、当前数据能支持哪些事实、哪些结论必须禁止 |
| 3 | [首个纵向切片锚点合同 v1.0](docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md) | M0/M1 当前只交付哪一条真实用户闭环，以及 J1–J5、DG1 怎样验收 |
| 4 | [目标架构 v1.1](docs/architecture/Domeye_Agent_Target_Architecture_v1.1.md) | 长期职责如何划分；不是要求在 M0/M1 建完所有层 |
| 5 | [22 项整改审计对应表 v1.1](docs/requirements/Domeye_Adversarial_Review_22_Items_Traceability_v1.1.md) | R01–R22 是否遗漏、偷换或弱化 |
| 6 | [22 项开发优先级与阶段计划 v1.1](docs/roadmap/Domeye_22_Items_Development_Priority_and_Stages_v1.1.md) | 哪些问题先处理、哪些必须由真实证据触发后再建设 |
| 7 | [Domeye GitHub 治理套件 v1.0](docs/governance/README.md) | 上述工作如何进入 Issue、Project、Candidate、Evidence 和状态同步 |

旧 W6 讨论包、旧 P0–P2 计划和历史 Evidence 继续保留，但不能覆盖这套交接主线。

## 现有 Core 能力概览

- 查询前缀劫持、子前缀劫持、路由泄漏、前缀中断、AS 中断和国家中断六类历史事件；
- 展示事件列表、事件详情、统计、资源量、报文量和受约束的观测时序；
- v1 Core 数据查询接口仍是只读；仓库另有 v2 report、chat、investigation 状态端点和
  旧 Candidate 实现，它们的存在不等于新锚点已经实现或通过评测；
- `backend/core/` 是离线检测核心，Web 启动不会自动运行检测管线。

## 按目的开始阅读

- 运行或修改现有 Core：先读[二三月固定开发模式](docs/二三月固定开发模式.md)、
  [开发与验收流水线](docs/开发与验收流水线.md)、[后端说明](backend/README.md)和
  [部署说明](deploy/README.md)。
- 参与 Agent 重构：先读上面的七份核心交接文档，再按当前任务进入
  [Capability Map](docs/architecture/capability-map.md)、[Epics](docs/roadmap/epics.md)或
  [Feature Breakdown](docs/roadmap/feature-breakdown.md)。
- 查看 GitHub 进度与状态语义：读
  [GitHub 管理与任务收尾同步规则](docs/governance/Domeye_GitHub_Management_Rules_v1.0.md)。
- 查找旧设计、历史验收或模板：从[文档导航](docs/README.md)进入，不要用文件名中的
  “最终验收”推断当前 Candidate 已通过。

## 权威顺序

执行具体任务时，以当前 Worktree 为边界，优先级固定为：

1. 当前 Worktree 的 `.codex/TASK.json`；
2. 当前 Worktree 的测试与机器合同；
3. 当前 Worktree 的代码；
4. 当前 Worktree 的文档。

文档按问题分权威：产品名称与事实强度看产品边界；M0/M1 固定问题、身份、执行链、
J1–J5 和 DG1 看锚点合同；长期职责看目标架构；R01–R22 完整性看审计表，开发顺序看
阶段计划；治理文档只管理版本、GitHub 和交付状态。当前代码行为仍以对应 commit 的测试、
机器合同和代码为事实。旧 W6 设计、旧 P0–P2 计划及历史 Evidence 只能追溯，不能覆盖
上述当前文档。

`main` 是长期权威主干。任务分支、Worktree 路径、未提交差异和聊天描述都不是可发布
Candidate 身份。

## 顶层目录职责

| 目录 | 职责 |
|---|---|
| `.codex/` | Codex 任务合同示例、任务边界检查、阶段 Hook 与项目级收尾代理配置 |
| `.github/` | Issue / PR 模板和 GitHub Actions 工作流 |
| `agent-sidecar/` | Pi/模型接入、会话与执行适配、技能、认证资源和 Agent 测试；包含不同历史 Candidate 的资产，不等于新锚点已实现 |
| `backend/` | Flask API、查询服务、数据管线与原样迁移的离线检测核心；`backend/core/` 属于冻结迁移基线 |
| `config/` | 数据档、性能预算、Agent 程序及研究输入配置；`config/data-profile.json` 是数据范围与快照时钟的唯一配置源 |
| `contracts/` | API、数据、Tool、Operator、Registry 和历史评测使用的机器可读合同 |
| `deploy/` | 候选验收、制品、数据库、运行管理、Nginx、治理 Hook、发布与回滚工具 |
| `dev/` | 本地开发入口、快速检查、固定夹具、数据质量研究和开发数据库/API 工具 |
| `docs/` | 当前权威文档、冻结旧设计、历史 Evidence 说明和模板；分类见文档导航 |
| `evaluation/` | 各历史 Candidate 的案例、轨迹、回执和评测制品；只证明其自身绑定的对象 |
| `frontend/` | Vue 3 + TypeScript 页面、组件、API 客户端和前端测试 |
| `openspec/` | 研究或变更提案、设计、规格与任务记录，不自动代表已经实现 |
| `tools/` | 数据构建、RRC25 重放、候选生成和离线分析工具 |

## 常用开发入口

日常开发采用短循环：

```bash
make dev
make risk
make check-fast
```

按变更风险和任务合同要求，再升级运行 `make check-integration` 或
`make check-release`；不要把全套检查当成每次编辑的固定前置步骤。

需要连接受控的服务器开发 API 时使用：

```bash
make dev API_MODE=remote
```

Codex 任务开始和结束分别执行：

```bash
make codex-preflight
make codex-postflight
```

`check-*` 命令无生产副作用。数据库候选准备、生产激活和回滚是单独的 `release-*`
流程，不能把本地检查、HTTP 200、PR 合并或页面可见当成生产验证；具体边界见
[开发与验收流水线](docs/开发与验收流水线.md)。

## 当前硬边界

- 现有 Core 的只读查询范围包含前缀劫持、子前缀劫持、路由泄漏、前缀中断、AS 中断和
  国家中断六类事件；未注册能力的实际边界见[后端说明](backend/README.md)。
- 现有 Core 只读取 `2026-02-01` 至 `2026-03-31` 的独立固定快照，不连接原生产数据库；
  精确边界以 `config/data-profile.json` 为准。
- 本地开发保持隔离：不读取原项目目录或原生产数据库，不复用原项目进程；需要远端
  开发 API 时只能使用受控入口并遵守任务合同。
- `backend/core/` 的业务逻辑在迁移阶段不得修改；外围变更影响该边界时必须在
  `backend/` 执行 `sha256sum -c core.sha256`。
- 运行数据、数据库、凭据和真实 `.env` 位于仓库外，不得写入 Git。
- Agent 首切片只使用冻结 publication、revision、RRC25、时间窗和
  `fixed_visible_ipv4_address_count`；控制面可见地址量不是用户数、设备数或流量。
- M0/M1 不建设通用计划 IR、预生成 DAG、Claim Validator/Publisher、Verified Claim
  Set、通用 RCA、多采集器或新的耐久工作流引擎。
- `implemented`、`committed`、`reviewed`、`merged`、`verified`、`deployed` 和
  `released` 是不同状态，必须分别提供对应证据。

现有部署基线要求 Linux、GNU Bash/coreutils/tar、zstd、jq、curl、Screen、Nginx、
Docker Compose v2、Python `>=3.10,<3.11`、PostgreSQL `12.16`、TimescaleDB `2.11.2`
和项目锁定的 Node.js `v22.23.1`；初次数据库制品构建建议预留至少 300GB 临时空间。
具体路径、镜像和操作步骤以[部署说明](deploy/README.md)为准。

生产拓扑、制品、发布、回滚和环境要求统一留在[部署说明](deploy/README.md)及
[主干开发与发布归一治理规范](docs/主干开发与发布归一治理规范.md)，首页不再重复维护。
