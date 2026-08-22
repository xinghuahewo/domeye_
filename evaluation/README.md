# Evaluation 目录说明

本目录只保留当前首个纵向切片 v1.1 Candidate 的评测源码，以及与它绑定的 Pilot 和 Formal 证据。旧 P0/P1/P2 与 v1 试跑已经删除；需要追溯时使用 Git 历史。

当前权威评测目标见
[首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。
当前保留的 Formal 记录证明其绑定 Candidate 已完成 30/30、独立 Acceptance Record 与 DG1 `GO`；该结论不自动证明当前生产运行身份。

## 状态说明

下列 `Current / Legacy-Frozen / Historical Evidence` 只是本目录的阅读导航标签，不是
GitHub 治理五轴，也不能替代 Candidate、Delivery Maturity、Evidence State 或 Gate。

| 状态 | 在本目录中的含义 |
|---|---|
| **Current** | 与当前 v1.1 Candidate、锚点 digest 和预注册场景完整绑定的评测包 |
| **Historical Evidence** | 已删除，仅可从 Git 历史按提交身份追溯 |

目录或文件名中的 `acceptance`、`final`、`prod-release`、`review`、`passed`、`W6` 等词
只属于其原始证据语境。它们不能单独推出当前 `main`、生产进程或首个纵向切片的状态。

## 当前材料导航

| 路径 | 内容 | 当前解释 |
|---|---|---|
| `country-outage/first-vertical-slice/*.mjs` | 当前评测器、案例注册和源加载器 | 被 v1.1 Candidate 固定的评测源码 |
| `country-outage/first-vertical-slice/runs/pilot-answer-style-v2-r2-20260821T052659Z/` | 3/3 Pilot 证据 | 只证明绑定的 v1.1 Candidate |
| `country-outage/first-vertical-slice/runs/formal-answer-style-v2-r2-20260821T055545Z/` | 30/30 Formal、独立复核与 Acceptance Record | 只证明绑定的 v1.1 Candidate，不代替生产读回 |

## Codex 阅读顺序

1. 先读 [`AGENTS.md`](../AGENTS.md)、当前 Worktree 中的 `.codex/TASK.json` 和
   [首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。
2. 明确要核验的 Candidate、阶段、场景和声明；先找该范围的 manifest、candidate
   identity、stage receipt 或 acceptance record。
3. 只沿 manifest 的引用读取必要 raw trace、case、review 或截图；校验身份、摘要、
   时间和生成方式后再下结论。
4. 报告时明确区分 fixture replay、本地测试、真实模型调用、独立验收、发布和生产读回。

## Evidence 保留规则

- 当前 Candidate 仍引用的 Evidence 不移动、重命名、覆盖、压缩、重采样、重新截图或批量格式化。
- 不修补旧 JSON/JSONL 中的值来适配新合同；发现错误时新增勘误或新 Candidate 证据，
  并保留原始字节。
- 不把多个 Candidate、不同时间窗或不同单位的结果拼成一个“完整”评测。
- fixture、mock、本地回放、截图和单次成功都不等于生产或 Verified；Trace 也不能冒充
  Domain Evidence。
- 先读当前 run 的配置、summary、Acceptance Record 和签名，再按明确引用取证；不要打开凭据或仓库外运行数据。
