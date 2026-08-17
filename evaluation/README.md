# Evaluation 目录说明

本目录保存历次 `country_outage` 评测、阶段回执、原始轨迹、截图和候选发布尝试。现有
内容默认属于 **Historical Evidence**：它们对绑定的旧 Candidate 仍有追溯价值，但
不能自动证明当前首个纵向切片已经实现、通过 DG1、发布或部署。

当前权威评测目标见
[首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。
该合同状态仍是 `Designed`；未来必须用同一个最终 Candidate 完成 J1–J5、独立
Acceptance Record 和 DG1 决定，才能推进状态。

## 状态说明

下列 `Current / Legacy-Frozen / Historical Evidence` 只是本目录的阅读导航标签，不是
GitHub 治理五轴，也不能替代 Candidate、Delivery Maturity、Evidence State 或 Gate。

| 状态 | 在本目录中的含义 |
|---|---|
| **Current** | 未来与当前锚点 digest、最终 Candidate 和预注册 J1–J5 完整绑定的新评测包 |
| **Legacy-Frozen** | 旧评测方法、问题集或阶段流程；不再作为 M0/M1 建设目标扩展 |
| **Historical Evidence** | 现有 case、raw trace、receipt、manifest、review、截图和费用记录 |

目录或文件名中的 `acceptance`、`final`、`prod-release`、`review`、`passed`、`W6` 等词
只属于其原始证据语境。它们不能单独推出当前 `main`、生产进程或首个纵向切片的状态。

## 现有材料导航

| 路径 | 内容 | 当前解释 |
|---|---|---|
| `country-outage/p0-v1*` | 早期评测基线及 Evidence | **Historical Evidence**；不等于新锚点 J1–J5 |
| `country-outage/p1-page-coverage/` | S0–S4 页面、语义、旅程与浏览器材料 | **Legacy-Frozen + Historical Evidence** |
| `country-outage/p1-trend-operator/` | 趋势 Operator 与模型对齐材料 | **Legacy-Frozen + Historical Evidence** |
| `country-outage/p1-prod-release/` | 多次旧候选发布尝试与费用汇总 | **Historical Evidence**；目录名不是当前生产部署证明 |
| `country-outage/p2-s0a-lifecycle/` | Registry 生命周期与治理阶段回执 | **Historical Evidence** |
| `country-outage/p2-s0b-runtime/`、`p2-s0b-prod34-release/` | 旧 runtime/shadow baseline 与发布影响材料 | **Historical Evidence**；锚点仅引用其中冻结 baseline 身份 |
| `country-outage/p2-s1-execution-unit-design/` | 旧执行单元设计阶段材料 | **Legacy-Frozen + Historical Evidence** |
| `country-outage/p2-s1-implementation*/` | 旧实施规划、W0–W6 阶段材料 | **Legacy-Frozen + Historical Evidence**；不能替代最终纵向切片真实运行 |

## Codex 阅读顺序

1. 先读 [`AGENTS.md`](../AGENTS.md)、当前 Worktree 中的 `.codex/TASK.json` 和
   [首个纵向切片锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)。
2. 明确要核验的 Candidate、阶段、场景和声明；先找该范围的 manifest、candidate
   identity、stage receipt 或 acceptance record。
3. 只沿 manifest 的引用读取必要 raw trace、case、review 或截图；校验身份、摘要、
   时间和生成方式后再下结论。
4. 报告时明确区分 fixture replay、本地测试、真实模型调用、独立验收、发布和生产读回。

## 原始 Evidence 保留规则

- 不移动、重命名、删除、覆盖、压缩、重采样、重新截图或批量格式化既有 Evidence。
- 不修补旧 JSON/JSONL 中的值来适配新合同；发现错误时新增勘误或新 Candidate 证据，
  并保留原始字节。
- 不把多个 Candidate、不同时间窗或不同单位的结果拼成一个“完整”评测。
- fixture、mock、本地回放、截图和单次成功都不等于生产或 Verified；Trace 也不能冒充
  Domain Evidence。
- 不要默认递归扫描 `raw/`、全部 case、PNG 或约百 MB 的评测树。先读 manifest 和索引，
  再按明确引用取证；不要打开凭据或仓库外运行数据。
