# Contracts 目录说明

本目录保存 API、数据、Agent 和研究阶段的机器可读 Schema、样例、Registry 快照与
候选材料。它是合同资产库，不是当前产品范围或架构优先级的权威入口。开始工作前先读
[首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。

## 状态说明

下列 `Current / Legacy-Frozen / Historical Evidence` 只是本目录的阅读导航标签，不是
GitHub 治理五轴，也不能替代 Candidate、Delivery Maturity、Evidence State 或 Gate。

| 状态 | 在本目录中的含义 |
|---|---|
| **Current** | 被当前任务、锚点和代码明确引用的机器合同；仍要核对版本、digest 与 Candidate |
| **Legacy-Frozen** | 旧 P0/P1/P2 设计或更宽产品命题留下的合同；保留兼容和追溯，不继续扩建 |
| **Historical Evidence** | fixture、快照、回执、旧候选或 wave 输出；只能证明其绑定的那次输入和 Candidate |

机器文件“存在”不等于运行时采用，“Schema 校验通过”也不等于事实正确、独立验收通过
或生产部署完成。

## 目录导航

| 路径 | 职责 | 默认状态与使用方式 |
|---|---|---|
| `openapi.json` | 仓库 API 接口描述 | **Current integration input**；必须与当前代码和任务范围交叉核对 |
| `agent/` | Agent Schema、Registry、阶段候选和执行单元设计 | **Current + Legacy-Frozen + Historical Evidence**；按精确文件和 digest 使用 |
| `data/` | RRC25/read-model/metric 等数据 Schema 与测试 fixture | Schema 可为 **Current**；`fixtures/` 与 `test-fixture/` 默认是 **Historical Evidence** |
| `info/` | 静态信息导入和质量合同 | 当前首个纵向切片外，默认 **Legacy-Frozen** |
| `research/` | episode、sample、wave 和研究测量合同 | 当前首个纵向切片外，默认 **Legacy-Frozen**；`fixtures/` 是历史测试输入 |

`agent/country-outage-p2-s0a-lifecycle/` 与
`agent/country-outage-p2-s0b-runtime/` 中的 Registry 材料可用于解释锚点记录的 shadow
baseline，但不能代表最终纵向切片 Candidate。`agent/country-outage-p2-s1-*` 以及旧
P1 合同保留作兼容和追溯；其中的通用计划、双模型回答、Claim 或旧发布语义若与锚点
冲突，以锚点为准。

## Codex 阅读顺序

1. 先读 [`AGENTS.md`](../AGENTS.md)、当前 Worktree 中的 `.codex/TASK.json`、
   [首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)
   和[能力地图](../docs/architecture/capability-map.md)。
2. 从当前代码或锚点取得合同的精确路径、版本和 digest，再打开对应 Schema 或 Registry。
3. 先读 manifest、索引或候选身份，再读取它明确引用的 fixture、回执和输出；不要反向
   从文件名猜 Candidate 或成熟度。
4. 修改合同前先确认消费者、生产者、兼容策略和 Evidence 重跑范围；普通整理任务不得
   顺手格式化 JSON。

## 保存和证据规则

- 不移动、重命名、删除、重排或批量格式化既有 JSON、JSONL、fixture、Registry
  snapshot、receipt 或 manifest。路径、字节和 digest 可能已被其他 Evidence 引用。
- 新合同不得覆盖旧版本；使用新版本、新 Candidate 身份和新的不可变 digest。
- fixture、本地生成输出和 wave-evidence 不能冒充生产数据、真实模型运行或独立
  Acceptance Record。
- 不要递归读取整个 `contracts/` 来理解一个窄任务；优先使用精确路径和 `rg` 文件名
  定位。不要打开凭据、仓库外运行数据或任何未由任务授权的敏感输入。
