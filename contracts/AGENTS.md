# Contracts 分层协作规则

本文件适用于 `contracts/**`，与仓库根 `AGENTS.md` 同时生效，只补充机器合同的
局部规则。发现本文件、根规则、当前任务合同或机器消费者不一致时必须停止并确认。
目录用途先从 [Contracts 导航](README.md)读取，不按文件名、版本号或历史记忆猜测
当前状态。

## 必须

- 修改前必须识别合同类别及其精确生产者、消费者、生成器、验证器和发布用途。OpenAPI、
  Candidate、验签材料、数据/INFO/Research Schema 与 fixture 不是同一种变更边界。
- 每份共享合同只能指定一个写入 Agent；其他模块可以读取、实现或审阅，不得并发编辑
  同一合同文件。
- 公共 API 变化必须在同一任务闭包中核对 Flask 实际路由、路由白名单、
  `contracts/openapi.json`、相关契约测试和前端生成类型。任务未授权所有必要路径时，
  必须停止扩展范围，不能只改单边。
- `frontend/src/types/openapi.generated.d.ts` 等生成输出只能通过现有生成器产生，禁止
  手工修补生成结果来掩盖源合同漂移。
- 修改当前 Candidate `source_files` 中任一文件后，旧 Candidate、评测和 Acceptance
  只继续证明原始字节，不再适用于变化后的工作树；不得沿用旧绿色结论。
- Candidate 的 ID、路径、`source_files` 和摘要禁止手工编辑。successor Candidate
  必须使用新的版本路径；若现有生成器固定写入旧路径，必须在获授权的 successor
  任务中同步版本化生成器，不得覆盖已有且绑定 Evidence 的 Candidate。
- Candidate、真实评测、Acceptance、发布和部署是不同状态，合同生成或 Schema 校验
  通过不得越级声明后续状态。
- 验签公钥、模型运行配置和其他安全相关合同只能在明确的轮换或 successor 任务中修改，
  并同步验证签发方、读取方、失败关闭和回滚边界。
- fixture 只能用于回归验证，不得冒充生产输入、真实模型运行、Acceptance 或运行时证据。
- Schema 校验只证明结构符合约束，不证明数据事实正确、全部消费者已经采用或生产已经
  部署。最终交付必须如实限定证明范围。

## 应该

- 修改 Schema 前应该先分类兼容性，并检查 required 字段、枚举、null 语义、默认值、
  附加字段策略和旧消费者行为；“只新增字段”不能自动视为兼容。
- 应该优先修改现有权威合同，不为同一语义创建平行的 `final`、`new`、`latest` 或阶段
  副本。需要版本化时使用明确 successor 路径并保留迁移边界。
- 数据身份应该显式绑定适用的 collector、事件、publication、revision、窗口、
  population、单位和完整性；缺失身份不得通过文件名或目录上下文猜测。
- 删除合同前应该先证明所有运行消费者、生成器、测试、部署入口和当前 Candidate 引用均
  已迁移或退役；只有 Git 文档引用不构成运行消费者，但必须同步维护当前导航和链接。
- 先运行最小 Schema/生成器/消费者测试，再按根级风险分类升级跨层检查。

## 可以

- 经任务明确授权，可以先实现 Candidate 绑定代码，再在独立步骤冻结 successor；此时
  只能报告“实现已变化、旧 Candidate 不适用于新字节”，不能报告新 Candidate 已验证。
- 仅当合同子树形成独立且稳定的生成器、权限或验收边界时，才可以继续增加更深层
  `AGENTS.md`。
