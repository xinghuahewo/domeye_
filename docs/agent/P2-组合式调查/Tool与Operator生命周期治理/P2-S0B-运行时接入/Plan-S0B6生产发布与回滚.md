# P2-S0B6 Tool/Operator Registry 生产发布与回滚计划

版本：`country-outage-agent-p2-s0b6-production-release-v1`

状态：已获生产上线授权；执行中

## 一、发布目标

把已验收的 S0B Registry 准入接入当前国家中断 P1 Sidecar，使生产问答在每轮固定一个
`production_active` Registry Snapshot，并按问题需要调用当前 6 个 Tool、3 个基础 Operator
和 OP-04。

本次只发布 Sidecar，不修改数据库、Backend、Frontend、Nginx、事件 publication、RRC25
数据或远端脏检出。原因、责任、真实用户影响、恢复与 RCA 边界不变。

## 二、入口真值

- 当前生产活动 release、进程、端口、readiness 和回滚点必须在部署前现场读取；
- `codex/prod` 必须是活动 release 源提交，S0A/S0B 提交必须是其线性后继；
- S0B shadow candidate、Snapshot、同候选测试、独立 Reviewer 和 final Hook 必须有效；
- 发布必须从最终提交生成一个源码归档，候选和生产使用同一不可变 Sidecar 制品；
- 远端 `/home/bgpdata/Domeye-Core` 若为脏检出，只允许读取，不得作为源码来源或清理目标。

## 三、Shadow 到 Production Promotion

禁止把以下 shadow 身份原样上线：

```text
activation_scope=runtime_candidate_shadow_only
runtime_integration=implemented_not_deployed
production_deployed=false
```

正式 `prepare` 必须从 shadow 制品派生新的内容寻址 production Snapshot：

```text
candidate_id=p2-s0b6-<digest>
activation_scope=production_active
runtime_integration=deployed
production_deployed=true
```

Promotion identity 必须绑定 source commit、annotated tag、release ID、shadow candidate、shadow
Snapshot、验收 manifest、产品语义 Reviewer 和明确回滚 release。生产模式拒绝 shadow 快照，
shadow 模式也拒绝 production 快照；不得跨模式静默回退。

## 四、发布步骤

### 4.1 生产前冻结

1. 重建 S0B shadow candidate 与同候选证据；
2. 运行 Python、Sidecar 全量测试、生产依赖审计、发布夹具、Reviewer、final Hook 和 core 摘要；
3. 提交发布适配并完成 postflight；
4. 快进整合到 `codex/prod`，创建不可改写 annotated tag；
5. 从该提交只生成一次源码归档并记录 SHA-256。

### 4.2 Prepare

在仓库外 runtime root：

1. 解包源码归档到临时候选；
2. 在完整源码中重跑 Sidecar 与 P2 门；
3. 裁剪生产依赖；
4. 复制 P1 认证、P1/OP-04 合同、P2 shadow 合同和同候选证据；
5. 生成 production promotion、production Snapshot、release manifest 与 SHA256SUMS；
6. 完整校验后原子移动为不可变 release 目录。

Prepare 不停止当前进程，不改变 `current`。

### 4.3 Activate

1. 再次验证当前活动 release 与预期回滚点一致；
2. 停止唯一 P1 Sidecar Screen；
3. 原子切换 `current` 到新 release；
4. 注入 `COUNTRY_OUTAGE_P2_REGISTRY_MODE=production` 和同 release Snapshot 路径；
5. 启动唯一进程；
6. readiness 必须返回 production candidate、Snapshot、revision、scope 和 deployed 标志；
7. 任一步失败立即执行原活动 release 回滚。

### 4.4 产品与资源验证

- readiness、认证、报告路由关闭、RRC25/非 RCA 边界通过；
- 进行一次受控真实事实问题，模型请求不超过现有每轮上限 1；
- 回答执行回执中的 candidate/Snapshot/version/digest 与 readiness、release manifest 一致；
- 越界问题保持零业务 Tool/Operator 执行；
- 只运行一个 Screen、只监听 `127.0.0.1:28475`；
- 对比切换前后 CPU、RSS、进程数、端口、错误日志和调用量；
- RSS 不得同时超过切换前两倍及增加 256 MiB，持续 CPU 异常、进程重启或新错误均阻断。

本次不设置费用审计硬门。保留既有模型用量日志只作为已有审计事实，不参与发布判定。

## 五、回滚

回滚触发条件：启动失败、readiness 身份不闭合、真实问题无 Registry 回执、越界误调用、同轮
混版、资源越门、错误日志新增、活动 manifest 与进程不一致。

回滚必须：

1. 停止新 Sidecar；
2. 通过 lifecycle manager 恢复部署前 release；
3. 验证旧 release manifest、current、active state、唯一进程、端口和 probe；
4. 保留失败 release、Promotion、日志和验收证据，不修改历史文件；
5. 不回滚数据库、Backend 或 Frontend，因为本次未改变它们。

## 六、最终 verified 门

只有下列身份完全一致才可写成生产 verified：

```text
core-work HEAD = origin/codex/prod = annotated tag commit
= source archive commit = release manifest source commit
current release = active state = running process release
readiness P2 candidate/Snapshot = release manifest P2 candidate/Snapshot
```

同时要求生产产品验证、资源与调用量门、回滚就绪和 postflight 全部通过。HTTP 200、Screen、
tag、Hook 或单测均不能单独替代上述闭环。
