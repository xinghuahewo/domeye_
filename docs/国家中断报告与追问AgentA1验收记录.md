# 国家中断报告与追问 Agent A1 验收记录

版本：1.2
阶段：A1
验收范围：RG-01 至 RG-04、SCE-02 和 SCE-03 的数据侧

> 1.1 以前的 6 项测试和旧 fact set 仅作为历史候选记录，不再证明当前 A1。
> 2026-07-29 已使用修复后源码重新执行全量测试和固定伊朗事件真实只读装配。
> 当前证据位于
> `artifacts/country-outage-agent/a1-current-semantic-closure-20260729T203017+0800/`。

## 一、阶段结果

- 只接受合法 `country_outage` 引用；
- 只调用 Domeye 现有只读 v2 API；
- 只接受 `collector_id=rrc25`、`collector_count=1`；
- `resolve` 后使用固定 `publication_id` 并行读取 `overview`、`series` 和 `audit`；
- 所有接口的 incident、publication、revision、data through、最终性、窗口和
  cohort 必须一致；
- 发现冲突时整批丢弃并重新 resolve，超过冻结次数后失败关闭；
- ASN 分页结果必须与报告快照身份一致；
- 完整五分钟时间网格、观测数、缺槽数、audit 缺槽清单逐项对账；
- 可见性与国家资源两条 UPDATE 轨道分别闭合，零 UPDATE 比例只接受 `null`；
- 国家资源极值只回指国家资源序列，资源数量、地址换算和相邻 delta 逐槽闭合；
- 起点、最低点、结束点、最大单槽下降和最大单槽回升由确定性代码选择；
- 派生数字保留操作数、公式、单位、值和稳定事实标识；
- 正式报告最低门槛不满足时抛出明确的数据不足错误；
- 非 RRC25、非法引用和不可确认快照均不能进入正式报告。

## 二、形成的合同与代码

- `agent-sidecar/src/domain/contracts.ts`：快照、事实、来源和资格合同；
- `agent-sidecar/src/domain/domeye-client.ts`：固定 publication 的只读客户端；
- `agent-sidecar/src/domain/observation-assembler.ts`：确定性事实装配与公式；
- `agent-sidecar/src/domain/errors.ts`：可区分的失败状态；
- `contracts/agent/country-outage-report-facts-v1.schema.json`：
  跨进程事实集合 JSON Schema；
- `agent-sidecar/tests/observation-assembler.test.ts`：身份、门槛、公式和重试测试。

代码没有读取数据库、文件系统中的事件数据、Codex 记忆或互联网资料，也没有引入
Shell、文件编辑、SQL 和写入能力。

## 三、代表性事件只读核对

对 A0 冻结的伊朗事件进行实时只读装配，得到：

| 项目 | 结果 |
|---|---|
| incident | `incident_go_v1_a1de26f854831330c616a72af21597eb` |
| fact set | `facts_44ce6ba951e4774835b0459eabf186e6` |
| publication | `publication_v1_38bddead083db3f49023c2e1` |
| revision | `1` |
| data through | `2026-02-28T15:00:00Z` |
| collector | `rrc25` |
| 时间网格 | 可见性 60 槽、国家资源 60 槽、5 分钟间隔、0 缺槽 |
| 正式报告资格 | 通过 |
| 降级能力 | `normal_band=unavailable` |
| 起点 | 18:05，367,215 Prefix×VP |
| 最低点 | 22:35，316,733 Prefix×VP |
| 结束点 | 23:00，333,938 Prefix×VP |
| 最大单槽下降所在点 | 18:30 |
| 最大单槽回升所在点 | 22:40 |
| 起点至最低点减少 | 50,482 Prefix×VP |
| 最低点后回升 | 17,205 Prefix×VP |
| 窗口结束相对起点缺口 | 33,277 Prefix×VP |
| 回升占此前损失 | 0.34081454775959746 |

这些数字由事实合同计算得到，与人工认可报告使用的关键数字一致。原始比例保留完整
精度，面向用户的百分比舍入留到报告格式化层处理。

## 四、测试与边界证据

当前全量 Sidecar 测试共 430 项，全部通过。其中事实装配覆盖：

1. 固定快照身份、完整 60 槽网格、固定人口和派生公式；
2. 嵌套事件、国家、publication、revision、cohort、窗口和观测状态冲突；
3. 非 RRC25、缺槽、静默截断、质量失败和正式门槛不足；
4. 可见率、ASN 三态、地址族、UPDATE 和国家资源的逐槽数值闭合；
5. 两条 UPDATE 轨道独立、零 UPDATE 比例、极值来源和相邻 delta；
6. 客户端整批冲突重读、引用规范化、取消和超时。

验证命令：

```bash
cd agent-sidecar
npm run typecheck
npm test
python3 -m json.tool ../contracts/agent/country-outage-report-facts-v1.schema.json
cd ../backend
sha256sum -c core.sha256
```

结果：构建和类型检查通过，430/430 测试通过，真实固定快照只读装配通过，
Schema JSON 有效，`backend/core.sha256` 14/14 通过。未读取认证、未调用模型、
未访问公开外部网站、未写入 Domeye 数据。

当前接口还出现未作为固定页面正式报告事实源的 `country_update_*` 第三轨道。
本轮没有静默切换到该轨道；若以后将其改为唯一正式事实源，需重新评估合同和迁移。

## 五、阶段出口判断

- RG-01：合法事件、用户触发和唯一 RRC25 的代码入口已闭合；
- RG-02：固定 publication 和整批身份一致性已闭合；
- RG-03：最低数据门槛和能力降级已闭合；
- RG-04：事实来源、稳定标识、派生公式和单位已闭合；
- SCE-02 数据侧：门槛不足失败、扩展能力缺失降级；
- SCE-03 数据侧：快照冲突整批重读或失败关闭；
- 尚未宣告报告文风、模型、前端、问答、下载和生产效果通过。

A1 Hook 回检通过后，A1 判定为“已修正”，可以进入 A2。
