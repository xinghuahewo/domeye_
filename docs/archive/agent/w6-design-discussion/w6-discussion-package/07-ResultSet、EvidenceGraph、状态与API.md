# ResultSet、EvidenceGraph、状态与API

> 文档口径：基于2026-08-14本地W6 v3工作树。Schema、API和本地fixture链存在，不表示28题或生产运行已经闭合。

## 1. 为什么Agent需要正式制品

如果Tool输出只被追加进模型上下文，系统无法稳定回答：

- 计算针对的是完整人口还是第一页；
- 结果来自哪个publication；
- 重跑后引用的是否还是同一集合；
- 某个派生结论究竟依赖哪些输入；
- 部分失败是否污染最终回答；
- 后续追问是否继承了旧数字；
- 回答和执行状态能否原子提交。

因此P2把中间结果设计成内容寻址的正式制品。

## 2. ResultSet

ResultSet是：

> Tool分页执行后，由Host冻结并内容寻址的完整或明确不完整的成员级事实人口。

### 2.1 关键字段

- ResultSet ID、revision和parent revision；
- source identity；
- source Tool及版本；
- normalized query和query digest；
- stable sort及digest；
- source population ID和Schema；
- source dataset digest；
- member identity和dedupe key；
- page manifest；
- returned count和total count；
- set completeness；
- resume page token；
- member segments；
- query、completeness和freeze receipts；
- Evidence refs和limitations；
- manifest和content digest；
- preview views；
- generation origin。

Schema入口：[ResultSet Schema](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/result-set.schema.json)

### 2.2 完整性状态

| 状态 | 含义 |
|---|---|
| complete | 分页闭合、身份一致、成员去重且满足source completeness |
| partial_page | 只冻结了部分页，不能交给要求完整集合的Operator |
| source_incomplete | 查询完成但源人口自身不完整 |

页面预览只是ResultSet的view，不是ResultSet本体。

## 3. EvidenceGraph

EvidenceGraph是：

> 已提交事实、派生事实、限制、未知和执行失败及其关系组成的版本化图。

### 3.1 节点类型

- observed_fact；
- derived_fact；
- result_set；
- limitation；
- unknown；
- execution_failure。

### 3.2 合法关系示例

- derived_from；
- member_of；
- at_time；
- precedes；
- same_window；
- path_contains；
- 直接路径相邻；
- 集合交、差、覆盖和相似；
- supports；
- conflicts_with；
- limited_by；
- requires_external_evidence。

### 3.3 不应未经合同出现的关系

- causes；
- responsible_for；
- customer_of；
- nationwide_outage；
- users_affected；
- recovered_from。

Schema入口：[EvidenceGraph Schema](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-execution-unit-design/evidence-graph.schema.json)

## 4. Trace与EvidenceGraph的区别

| Trace | EvidenceGraph |
|---|---|
| 记录调用发生过 | 记录哪些事实经过验证并提交 |
| 面向调试和可观测性 | 面向事实发布、派生关系和回答约束 |
| Tool成功即可形成span | Tool成功不一定产生可发布Evidence |
| 不自动证明人口完整性 | 必须绑定ResultSet和完整性回执 |
| 可包含失败尝试和原始输出 | 只按合同提交合法节点和限制 |

调用Trace是审计材料，但不能替代EvidenceGraph。

## 5. P1与P2制品对照

| P1制品 | P2制品 | 当前关系 |
|---|---|---|
| UserGoalPlan/P1SemanticPlan | Teacher SemanticPlan | 语义目标相近，合同不同 |
| GroundingPlan | Grounded Recipe/InvestigationPlan | 尚无Common Plan IR |
| Tool receipt | node execution receipt | 语义相似，存储和Envelope不同 |
| Evidence Bundle | ResultSet + EvidenceGraph | 尚无正式双向桥 |
| EvidenceState | committed graph/artifact state | 状态模型不同 |
| DialogState | Turn + Investigation revision | 提交和继承语义不同 |

长期统一不要求所有制品字段完全相同，但必须存在唯一正式Typed Artifact协议和明确Profile差异。

## 6. Answer与Turn

P2回答链将自然语言答案视为被验证的制品，而不是模型调用的直接返回值。

```text
Committed EvidenceGraph
  ↓
冻结Shared Answer Binding
  ↓
Sol Teacher Reference
  ↓
Host Gate
  ↓
DS Student Answer
  ↓
Host Gate与Alignment
  ↓
Answer Artifact
  ↓
Turn + Investigation revision单一CAS提交
```

Shared Binding包含问题、Plan、publication、Registry snapshot、EvidenceGraph和prompt/policy摘要。模型输出不能改变这个绑定。

当前该链只在本地fixture replay中运行，Teacher不是ground truth，外部provider未接入。

## 7. 状态、revision和CAS

Investigation的每次可变操作都绑定：

- expected investigation revision；
- expected current digest；
- idempotency key；
- actor/principal；
- 新制品摘要；
- 新revision和parent revision。

CAS解决：

- 两个并发重跑同时覆盖状态；
- 取消和执行完成竞争；
- 老页面用旧revision提交追问；
- Answer已存储但Investigation没有引用的半提交；
- 同一idempotency key对应不同请求。

内容寻址对象在CAS失败时可能留下不可达staging residue，但不得成为API可消费的current制品。

## 8. 失败和部分完成

P2允许节点局部失败，但失败必须成为显式状态：

- failed；
- cancelled；
- skipped_dependency_failed；
- reused。

EvidenceGraph可以包含execution_failure和limitation。最终Investigation可以是 `partially_completed`，但回答必须准确说明哪些子目标没有完成，不能把局部成功描述成完整调查成功。

## 9. 当前调查API

| API能力 | 路由概念 |
|---|---|
| 创建调查 | POST investigations |
| 读取调查 | GET investigation |
| 启动 | POST start |
| 取消调查 | POST cancel |
| 取消节点 | POST node cancel |
| 重跑节点 | POST node reruns |
| 创建追问Turn | POST turns |
| 读取Turn revision | GET turn revision |
| 读取ResultSet | GET result-set revision |
| 读取EvidenceGraph | GET graph revision |
| 读取receipts | GET receipts |
| 创建导出 | POST exports |
| 读取/下载导出 | GET export / artifact |

权威入口：[OpenAPI](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/openapi.json)

## 10. Host Gate应验证什么

在事实和答案提交前至少验证：

1. identity equality；
2. Registry snapshot和unit摘要；
3. ResultSet完整性；
4. Evidence引用存在且摘要一致；
5. Operator input/output和Profile绑定；
6. observed与derived关系合法；
7. 回答中的每个事件事实都能映射到Graph节点；
8. 限制和unknown没有被丢弃；
9. 没有禁止结论；
10. Answer、Turn和Investigation新revision可以原子提交。

## 11. 当前制品层的主要价值与缺口

已经证明：

- ResultSet可以冻结少量Tool人口；
- EvidenceGraph可以提交少量支持路径；
- Turn和Investigation可以CAS提交；
- 取消、重跑、导出和恢复有本地证据。

尚未证明：

- P1结果统一转换为ResultSet；
- 28题全部输入人口闭合；
- 通用ResultSet到Operator适配；
- 真实provider答案与EvidenceGraph对齐；
- 生产存储、权限和运行身份。
