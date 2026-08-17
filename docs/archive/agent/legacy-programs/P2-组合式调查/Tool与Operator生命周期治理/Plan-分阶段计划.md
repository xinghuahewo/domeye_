# P2-S0A Tool 与 Operator 生命周期治理 Plan（分阶段计划）

版本：`country-outage-agent-p2-s0a-lifecycle-plan-v1`

绑定 Task Spec：`country-outage-agent-p2-s0a-lifecycle-governance-v1`

状态：执行计划

## 一、计划原则

本计划只冻结每阶段入口、出口、边界和到期验收，不锁定未来 Runtime Registry 的数据库、
服务框架、部署拓扑或控制面技术。本轮候选始终是离线治理控制面，不接入生产运行时，
不部署、不切换 prod32、不修改远程状态或生产配置。

每阶段结束必须运行：

```bash
python3 .codex/hooks/country_outage_agent_p2_s0a_alignment.py \
  --repo-root . \
  --stage <阶段>
```

Hook 通过只证明本阶段制品、摘要、映射和边界未发生已知偏离。阶段出口还必须保留真实
测试、Oracle、Reviewer 或回滚证据；不能用 Hook 退出码代替产品验收。

## 二、阶段总览

| 阶段 | 核心目标 | 到期出口 |
|---|---|---|
| S0A-0 | 冻结现状、Task Spec 与任务边界 | “已有/部分具备/待建设”证据和文档闭合 |
| S0A-1 | 冻结双 Registry、Schema、Policy 和状态机 | 机器合同可解析，非法状态/身份被拒绝 |
| S0A-2 | 迁移 18 Capability 与 10 Execution Unit | 稳定 ID、版本、三类摘要和双向映射 100% |
| S0A-3 | 建立离线 CRUD、影响分析、认证和计划准入 | 全生命周期入口可执行，非 active 计划拒绝 100% |
| S0A-4 | 建立 Oracle、问题探针和独立语义 Reviewer | 十类治理 Oracle 闭合，Reviewer 阻断为 0 |
| S0A-5 | 建立快照、单元/整版回滚、历史重放和审计 | 两类回滚演练、Tombstone 与重放闭合 |
| S0A-6 | 同候选总验收与 P2-S0B 交接 | manifest、Alignment、postflight 和非部署边界闭合 |

## 三、S0A-0：基线与 Task Spec 冻结

### 入口

- 当前任务 Worktree、分支、基线和 `.codex/TASK.json` 已确认；
- P1 Capability Catalog、Typed Tool Contract、完整 Oracle、OP-04 合同与 release
  验证/回滚脚本可读；
- 生产运行时、prod32、远程状态均不在允许写路径。

### 工作

- 形成“已有、部分具备、待建设”证据表；
- 冻结双 Registry、生命周期、CRUD、兼容、影响、认证、回滚、Tombstone、快照准入、
  权限、安全、RRC25 和 RCA 边界；
- 明确本轮离线候选与 P2-S0B 运行时接入分界；
- 建立 Alignment Hook 的阶段映射和篡改负例要求。

### 出口与到期验收

- Task Spec 覆盖用户要求的全部治理主题；
- Plan 对每阶段写清入口、出口、边界和到期门；
- Task Spec 未把源码、HTTP 200、Hook 或单测写成产品验收；
- `S0A-0` Alignment Hook 通过；
- 任一边界缺失则不得进入 S0A-1。

### 边界

只修改文档与任务合同；不创建伪 Registry，不宣称已实现。

## 四、S0A-1：机器合同与状态机

### 入口

- S0A-0 回执通过且文档版本冻结；
- 双 Registry 字段、状态和版本规则无未决阻断。

### 工作

- 创建 Registry、治理请求、回执、计划准入和快照 Schema；
- 创建 lifecycle policy、兼容矩阵、状态迁移表和权限矩阵；
- 实现规范化摘要、SemVer、revision 单调性、双向引用和终态不复用校验；
- 冻结离线 `active` 不等于 production deployed 的机器标记。

### 出口与到期验收

- 合法最小样例通过，缺字段、未知状态、非法 SemVer、跳级、ID 冲突和摘要漂移负例拒绝；
- capability/unit 映射冲突和非 active 依赖拒绝；
- Schema 与 Policy 摘要进入候选 manifest；
- `S0A-1` Alignment Hook 通过。

### 边界

不接入现有 Grounder/Executor；不修改 P1 合同。

## 五、S0A-2：现有能力与执行单元迁移

### 入口

- S0A-1 Schema/Policy 通过；
- 当前源合同和实现文件摘要可计算。

### 工作

- 迁移 P1 17 selected Capability 和 `CAP-TREND-001`；
- 迁移 `TOOL-01..06`、`OP-01..03`、`OP-04`；
- 基础单元治理版本记为 `1.0.0` 并保留 legacy revision；OP-04 保留 `1.2.0`；
- 生成 contract/implementation/semantic digest、migration map 和双向映射；
- 对用户结果、单位、null、错误、权限、证据、超时和边界做等价性核对。

### 出口与到期验收

- 18/18 Capability、10/10 Execution Unit 完整；
- ID/版本/三类摘要覆盖率与双向引用通过率 100%；
- OP-04 的 TOOL-03 依赖、Profile、模型无依赖和禁止断言不变；
- defer/reject 项未被静默激活；
- `S0A-2` Alignment Hook 通过。

### 边界

迁移是离线治理镜像；不改变当前生产行为或运行时合同加载路径。

## 六、S0A-3：离线治理入口与计划准入

### 入口

- 迁移候选通过双 Registry 校验；
- 状态迁移、权限和 CAS revision 规则冻结。

### 工作

- 实现 create/read/update/impact/transition/delete/snapshot/check-plan/rollback 命令；
- 实现兼容分类、传递依赖影响分析和受影响 Oracle 选择；
- 实现 Oracle、Reviewer、安全、费用、性能证据闭合后的 certify/activate；
- 实现弃用、退役、Tombstone 与 ID 永不复用；
- 实现非 active、快照漂移、版本或摘要不匹配的计划准入拒绝。

### 出口与到期验收

- CRUD 和主状态路径有正例；跳级、原地改写、错误 revision、未认证激活、活动依赖退役、
  Tombstone 复用有负例；
- 非 active 计划准入拒绝率 100%；
- 所有写操作产生不可变回执且失败不产生部分状态；
- `S0A-3` Alignment Hook 通过。

### 边界

只操作调用者明确指定的离线目录；默认不读取/写入生产 runtime root。

## 七、S0A-4：Oracle、探针与独立 Reviewer

### 入口

- 离线治理入口和迁移候选可重复执行；
- Product Semantic Charter 与 Reviewer 独立角色冻结。

### 工作

- 补齐 normal、missing、null、wrong identity、unavailable、boundary、migration、tamper、
  rollback、plan admission 十类治理 Oracle；
- 建立高风险问题探针；
- Reviewer 不导入治理实现，不使用候选自称状态作为真值；
- 对迁移前后用户结果、单位、null、错误、Evidence、权限和 RCA 边界逐项比较。

### 出口与到期验收

- 十类 Oracle 全部至少有一个可执行案例；
- 身份、状态、摘要、快照和 Reviewer 回执篡改均被拒绝；
- Reviewer 输出 PASS 且阻断为 0；构建者和 Reviewer 身份不同；
- `S0A-4` Alignment Hook 通过。

### 边界

Reviewer 审核产品语义，不签署代码质量、部署或生产状态。

## 八、S0A-5：快照、回滚、历史重放与审计

### 入口

- 当前候选已经认证并可生成不可变离线快照；
- 至少存在一个可回滚旧版本/旧快照 fixture。

### 工作

- 演练单单元回滚：新 revision 恢复旧已认证版本并重算依赖；
- 演练整版回滚：活动指针切回完整旧快照，不拼接半版；
- 演练 Tombstone 后历史身份解析、迁移和 ID 复用拒绝；
- 验证历史 Evidence/计划默认按原快照解析；
- 输出费用、性能、安全和 rollback receipt。

### 出口与到期验收

- 单单元与整版回滚各至少 1 次通过；
- before/after revision、snapshot、摘要、影响和回滚原因完整；
- Tombstone 身份可解析、不可执行、不可复用；
- 历史 Evidence/计划不会静默落到最新版；
- `S0A-5` Alignment Hook 通过。

### 边界

回滚仅作用于临时/候选离线目录，不调用现有生产 `manage.sh rollback`。

## 九、S0A-6：同候选最终验收与交接

### 入口

- S0A-0 至 S0A-5 均有通过回执且无未关闭阻断；
- 候选文件集冻结，不再接受无新 revision 的原地修改。

### 工作

- 生成同候选 manifest 和全部文件 SHA-256；
- 重跑受影响的 Registry/Hook/Reviewer/回滚/篡改测试；
- 核对候选 ID、Registry revision、snapshot、合同、实现、Oracle、Reviewer 和回执身份；
- 执行 `backend/core.sha256`、`git diff --check` 和 `make codex-postflight`；
- 形成 P2-S0B 运行时接入建议与未关闭风险。

### 出口与到期验收

- Task Spec 十七项量化门全部满足，一票否决为 0；
- `final` Alignment Hook 和独立 Reviewer 通过；
- 同候选 manifest 可从零复核摘要和制品关系；
- 允许路径外改动、生产/远程状态改动均为 0；
- 交付只表述为本地离线候选通过，不表述为 merged/deployed/production verified。

### 边界

不提交生产发布，不切换 runtime，不把 P2-S0A 验收替代 P2-S0B 端到端验收。

## 十、偏离与续跑规则

- 任一阶段发现 Task Spec 边界冲突，停止后续阶段，先以新 revision 修订 Spec/Plan 并重新
  运行受影响阶段；
- 修复按影响范围续跑，不因普通文档或单个负例修复无差别重跑生产验收；
- 身份、状态机、Schema、Capability/Execution Unit 映射、Oracle、Reviewer 或快照摘要
  变化，必须重跑所有直接和传递受影响阶段；
- 未关闭阻断不得通过改 Hook、删负例、降门槛、改 fixture 或写 PASS 文本掩盖；
- 若未来需要运行时接入、部署或生产灰度，必须新建 P2-S0B 任务合同，不扩大本任务边界。
