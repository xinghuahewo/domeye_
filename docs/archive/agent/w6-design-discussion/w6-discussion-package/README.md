# Domeye Agent 设计路线讨论包

## 1. 文档目的

本讨论包用于向产品、Agent、后端、数据、BGP语义、安全和验收人员说明：

1. 当前国家中断问答与调查系统实际上能做什么；
2. Tool、Operator、Host、LLM、Registry分别承担什么职责；
3. P1与P2为什么不应被理解成两套能力系统；
4. 当前任务规划、DAG、ResultSet、EvidenceGraph和状态提交如何工作；
5. 为什么现有基础设施较完整，但28题正式回答能力仍为0；
6. 下一轮架构讨论真正需要决定哪些问题。

本文档不是生产运行说明，不宣布部署状态，也不把离线fixture验收解释为真实模型认证。

## 2. 事实快照

本文档以以下本地工作树为主要事实来源：

`../country-outage-agent-p2-s1-w6-offline-certification-v3-work`

快照时间：2026-08-14。

需要特别说明：

- W5实现候选提交为 `b8d5b04b67c41d2d5f9f4ec1f9c64972bd90fa73`；
- W6认证文件目前位于该工作树的未提交修改和未跟踪文件中；
- W6清单明确记录 `production_deployed=false`；
- W6外部provider调用数为0；
- 当前结论只描述这个本地W6 v3工作树，不代表当前生产环境。

权威状态入口：

- [W6验收清单](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/w6-certification/acceptance-manifest.json)
- [W5实现证据](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/contracts/agent/country-outage-p2-s1-implementation/wave-evidence/W5.json)
- [W6验收说明](../country-outage-agent-p2-s1-w6-offline-certification-v3-work/docs/agent/P2-组合式调查/实体调查实现工程/W6-离线确定性实现验收说明.md)

## 3. 一句话结论

当前系统不是从零开始，也不是Tool/Operator设计失败；它已经分别证明了受控问答和持久调查的关键机制，但P1和P2仍运行在两套计划、执行和制品链上。

正确的长期抽象应是：

```text
统一能力与执行单元治理
        ↓
统一Plan编译与执行内核
        ├── P1受限问答Profile
        └── P2持久调查Profile
        ↓
统一Typed Artifact与EvidenceGraph
```

## 4. 状态词约定

| 标记 | 含义 |
|---|---|
| 当前实现 | 当前W6 v3工作树存在实际代码路径或可执行本地运行时 |
| 已有合同 | Schema、Catalog、Oracle或设计合同已冻结，但不自动表示运行时可用 |
| 本地验收 | 在离线fixture或本地隔离候选上形成了可重放证据 |
| 当前限制 | 当前正式执行链不能完成，或仅能阻断/延期 |
| 目标模型 | 用于路线讨论的收敛架构，不应被描述成已经实现 |
| 生产能力 | 必须另有合并、构建、部署和运行身份一致性证据；本讨论包不作此声明 |

## 5. 阅读顺序

1. [01-当前系统功能与能力边界](01-当前系统功能与能力边界.md)
2. [02-原子Tool设计与目录](02-原子Tool设计与目录.md)
3. [03-确定性Operator设计与目录](03-确定性Operator设计与目录.md)
4. [04-Registry合同与生命周期治理](04-Registry合同与生命周期治理.md)
5. [05-LLM与Host职责边界](05-LLM与Host职责边界.md)
6. [06-任务规划与DAG执行模型](06-任务规划与DAG执行模型.md)
7. [07-ResultSet、EvidenceGraph、状态与API](07-ResultSet、EvidenceGraph、状态与API.md)
8. [08-当前断层与架构路线讨论](08-当前断层与架构路线讨论.md)
9. [09-术语表与源码索引](09-术语表与源码索引.md)

## 6. 建议的评审参与者

| 角色 | 重点阅读 |
|---|---|
| 产品负责人 | 01、05、08 |
| Agent/LLM负责人 | 05、06、08 |
| 后端与平台负责人 | 04、06、07、08 |
| 数据与BGP负责人 | 01、02、03、07 |
| 安全与权限负责人 | 04、05、07 |
| 测试与认证负责人 | 01、04、07、08 |

