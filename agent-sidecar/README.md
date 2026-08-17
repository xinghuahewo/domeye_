# Agent Sidecar 目录说明

本目录承载内部 `country_outage` Agent 的 Node.js Sidecar 实现、测试、运行资源与少量
辅助脚本；产品主名称是 **Domeye 国家网络中断调查 Agent**，当前首片只接入 RRC25 BGP
控制面证据能力。它是实现工作区，不是产品能力清单，也不是生产状态证明。当前 M0/M1
的产品和执行边界先看
[首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。

## 状态说明

下列 `Current / Legacy-Frozen / Historical Evidence` 只是本目录的阅读导航标签，不是
GitHub 治理五轴，也不能替代 Candidate、Delivery Maturity、Evidence State 或 Gate。

| 状态 | 在本目录中的含义 |
|---|---|
| **Current** | 当前任务可能使用的实现表面；仍须由任务合同、测试和同一 Candidate 的 Evidence 证明具体状态 |
| **Legacy-Frozen** | 为兼容、追溯或旧候选保留的实现；不得因文件仍可运行就继续扩建旧架构 |
| **Historical Evidence** | 绑定旧 Candidate、认证或依赖处置的材料；不能自动继承给首个纵向切片 |

本目录是混合区。目录名中的 `formal`、`acceptance`、`certified`、`p1`、`p2` 或 `prod`
只描述当时的流程或材料，不能单独推出当前能力为 Implemented、Verified、Released 或
production deployed。

## 目录导航

| 路径 | 职责 | 默认状态与使用方式 |
|---|---|---|
| [`package.json`](package.json)、`package-lock.json`、`tsconfig.json` | Node.js、TypeScript、Pi 依赖与命令入口 | **Current**；运行命令前仍要核对当前任务允许的副作用 |
| `src/` | application、chat、core、domain、Pi、report、runtime、server 等实现 | **Current + Legacy-Frozen**；只打开当前 Action 链直接涉及的文件，不把旧整体规划或发布链当成新架构 |
| `tests/` | 单元、集成、契约与旧阶段回归测试 | **Current + Legacy-Frozen**；fixture 通过只证明对应测试边界 |
| `scripts/` | Vendor patch 校验/应用、认证文件创建、PDF 渲染 | **Current supporting tools**；逐个审查后运行，禁止批量执行 |
| `resources/skills/` | Sidecar 现有实现使用的冻结 Skill 资源 | **Current implementation input**；不是新滚动 Action 架构的默认依赖，资源存在也不证明运行时已加载或评测通过 |
| `resources/vendor-patches/`、`vendor-patches/` | Pi 依赖补丁清单与补丁制品 | **Current dependency control**；必须按精确版本和摘要校验 |
| `resources/model-*`、`resources/certified-models/`、`resources/dependency-security/`、`resources/risk-exceptions/` | 模型候选、认证、依赖处置和风险例外 | 现有实现仍可能引用；其中认证与处置记录按绑定身份作为 **Historical Evidence** 阅读，不得自动继承为新锚点 Candidate |

首个纵向切片当前只认可这一条目标链：Pi 提出一个下一步 Action，Host 逐 Action 准入，
`TOOL-03 read_metric_series` 读取冻结序列，`OP-01 series_extrema` 做确定性计算，Host
形成 Typed Finding 和最小 Answer Context，Renderer 起草，Response Guard 决定
`pass/block`，最后输出 Answer 或确定性回退。不要从本目录反向推导通用 DAG、Claim
Validator/Publisher 或新的耐久工作流引擎。

## Codex 阅读顺序

1. 先读仓库根 [`AGENTS.md`](../AGENTS.md) 和当前 Worktree 中的 `.codex/TASK.json`。
2. 再读[首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)与
   [能力地图](../docs/architecture/capability-map.md)。
3. 根据任务只定位一个入口、其直接依赖和对应测试；先看 [`package.json`](package.json)
   中已有命令，不要遍历运行所有 CLI。
4. 需要引用 Registry、合同或评测结果时，先确认 Candidate、版本和 digest，再转到
   [`contracts/`](../contracts/README.md)或 [`evaluation/`](../evaluation/README.md)。

## 不要默认扫描或执行

- 不要读取任何真实 `.env`、认证文件、API Key、运行时目录或仓库外凭据；示例文件也
  不能替代实际权限证明。
- 不要运行认证创建脚本、模型认证/晋级命令、服务启动命令、Vendor patch `--apply`
  或带外部调用的 CLI，除非当前任务明确授权并给出输入、身份和回滚边界。
- 不要为理解一个窄任务递归读取全部 `src/`、`tests/`、`resources/` 或生成目录。
- 不要把 fixture、本地回放、单元测试、HTTP 200、模型认证文件或本地报告当作生产
  Evidence 或当前纵向切片的 Verified 结论。
