# Domeye Agent Loop 五个核心难点与实施计划

> 状态：Design Note
>
> 目的：将 Agent Loop 从架构目标拆解为可验证、可实施的工程任务。

## 1. 背景

当前 Domeye 已具备 Tool、Operator、Registry、Artifact、Receipt 等可信执行资产，但旧路径仍偏向一次性规划：

```
用户问题
 → 意图识别
 → Host 生成完整 Plan/DAG
 → 执行
 → 返回结果
```

目标 Agent Loop：

```
用户目标
 → Pi 理解目标
 → 提出 Capability Proposal
 → Trust Kernel 准入
 → Tool/Operator 执行
 → Observation 返回
 → Pi Replan / Finish
```

核心原则：

- Pi 负责理解、提议、观察和重新规划；
- Trust Kernel 负责身份、权限、Registry、预算和发布权；
- Tool/Operator 负责确定性事实生产；
- Artifact/Evidence/Claim 负责可信回答闭环。

---

# 2. 五个核心难点

## 难点一：下一步决定权归属

目标：Pi 真正决定下一项 Capability，而不是 Host 预先生成 DAG。

实现：

- 使用 Pi 原生 Tool Calling；
- Tool 面向 Capability，不暴露 TOOL/OP 内部编号；
- Capability Gateway 将 Capability 解析到 Execution Unit；
- 移除生产路径中的 question → template → DAG。

---

## 难点二：Goal State / Planning State

目标：Agent 根据结构化状态决策，而不是依赖聊天历史。

第一版状态：

- 用户目标；
- trusted domain binding；
- 已观察 Artifact；
- 当前限制；
- budget。

后续根据真实失败轨迹增加：

- unresolved requirements；
- attempt history；
- stall detection。

禁止提前设计完整 Planning Graph。

---

## 难点三：Capability View

目标：模型看到业务能力，而不是底层实现。

首片只暴露：

```
read_bound_metric_series
 derive_series_extrema
```

模型不知道：

```
TOOL-03
OP-01
handler
数据库接口
```

Gateway 内部负责：

```
Capability
 → Registry
 → Execution Unit
 → Tool/Operator
```

---

## 难点四：Observation 驱动 Replan

目标：执行结果真正影响下一步决策。

Observation 最小类型：

- Completed；
- Partial；
- Rejected；
- Failed。

必须包含：

- artifact_ref；
- limitation；
- failure reason；
- retry 信息。

禁止 Observation 直接告诉模型下一步动作，否则重新退化为 Workflow。

---

## 难点五：Agent Eval

目标：证明 Agent 会规划，而不是证明固定流程会执行。

评测关注：

- Goal 理解；
- Capability 选择；
- Observation 后决策；
- 安全边界；
- 最终任务结果。

快速验证阶段：

- 6 个自然语言表达；
- 2 个拒绝场景；
- 2 个 Tool failure/partial 场景；
- 2 个干扰场景。

通过后再进入正式 Agent Certification。

---

# 3. 快速验证实施计划

## Spike 1：Pi Capability Loop

目标：验证 Pi 是否能够自主调用 Capability。

范围：

- 两个 Capability；
- 一个 Tool；
- 一个 Operator。

不做：

- UI；
- Durable Job；
- 完整 Claim 平台。

---

## Spike 2：Capability Gateway

建立可信边界：

```
Pi
 ↓
Capability Gateway
 ↓
Registry Resolver
 ↓
Tool/Operator
```

---

## Spike 3：Observation Replan

验证：

```
Action
 → Observation
 → 下一 Action
```

而不是：

```
预生成 DAG
 → 执行
```

---

## Spike 4：Agent Loop Eval

记录：

- Trial；
- 成功率；
- 失败类型；
- Tool 调用；
- 延迟；
- 成本。

---

# 4. 当前不建设内容

快速验证阶段暂不建设：

- Durable Workflow Engine；
- Common Plan IR；
- 通用 Compute Engine；
- 多 Agent；
- 全量 Registry 重构。

这些应由真实任务数据触发。

---

# 5. 最终目标

```
Pi Agent Runtime
        |
Capability Gateway
        |
Trust Kernel
        |
Domain Tool / Operator
        |
Artifact / Evidence
        |
Verified Claim
        |
Answer
```

第一阶段真正要验证的问题：

> 去掉 Host 固定规划以后，Pi 是否能够在可信边界内完成真实纵向任务。
