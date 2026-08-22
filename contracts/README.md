# Contracts 目录说明

本目录只保存当前代码、发布流程或验收流程仍在使用的机器合同。产品与架构入口见
[文档导航](../docs/README.md)；首个 Agent 纵向切片以
[锚点合同](../docs/architecture/Domeye_First_Vertical_Slice_Anchor_v1.0.md)为准。

## 当前合同

| 路径 | 当前用途 |
|---|---|
| `openapi.json` | 当前 Flask API 与前端类型生成的接口合同 |
| `agent/domeye-first-vertical-slice/v1.1/candidate.json` | 当前首个纵向切片 Candidate 清单 |
| `agent/domeye-first-vertical-slice/v1.1/attestors/` | 当前执行证据与独立评审验签公钥 |
| `agent/domeye-first-vertical-slice/v1/model-runtime.json` | 当前 Candidate 冻结的模型运行时配置；目录名为 `v1`，但仍属于 v1.1 Candidate 的源闭包 |
| `agent/country-outage-contemporaneous-reference-v1.schema.json` | 当前趋势制品的同期参照合同 |
| `agent/country-outage-evidence-graph-v1.schema.json` | 当前趋势制品的证据图合同 |
| `agent/country-outage-trend-context-v1.schema.json` | 当前趋势分析上下文合同 |
| `agent/country-outage-trend-profile-v1.schema.json` | 当前趋势分析 Profile 合同 |
| `data/` | 当前 RRC25 数据、Publication、读模型、指标和质量门合同及回归 fixture |
| `info/` | 当前静态 INFO 导入、质量检查与发布验收合同 |
| `research/` | 当前 RRC25 研究流水线使用的运行、测量、对账合同及回归 fixture |

## 使用规则

1. 从当前代码、OpenAPI、Candidate 或发布脚本取得精确路径后再读取合同，不按文件名猜测运行状态。
2. `candidate.json`、验签公钥、`model-runtime.json` 以及 Candidate `source_files` 中列出的文件不得在普通整理中修改；任何字节变化都需要新 Candidate 和重新验收。
3. `fixtures/` 只用于回归验证，不能冒充生产数据、真实模型运行或生产验收证据。
4. 修改 Schema 前必须同步核对生产者、消费者、兼容策略、生成类型和对应测试。
5. 机器合同通过校验只证明结构符合约束，不证明数据事实正确、运行时已经采用或生产已经部署。
