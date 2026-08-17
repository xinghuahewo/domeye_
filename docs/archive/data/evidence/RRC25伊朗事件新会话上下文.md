# RRC25 伊朗事件新会话上下文

> 用途：新 Session 只需阅读本文件，不需要回看旧对话。
>
> 最新任务已收敛为北京时间 2026-02-28 18:05–23:00 的一次 bview 状态重放，
> 并明确允许改造国家中断相关 core 代码。详细需求以
> `docs/data/RRC25伊朗事件1805-2300状态重放与事件模型改造需求.md` 为准；
> 它覆盖本文中更早的“不修改 backend/core”限制。缺少的逻辑允许直接实现；
> 不做繁琐审计和逐条追溯；不得设置文件处理或总运行时间上限。

## 1. 当前目标

围绕固定事件完成研究型数据闭环：

```text
country_outage/2026-02-27 09:12:32/IR/1/r
```

当前正确顺序：

1. 现有数据库作为研究主体；
2. 原始 MRT 只补明确的数据缺口；
3. 不重新执行 1,928 个 UPDATE 的全量全球回放；
4. 只验收伊朗事件；
5. 前候选事件与主事件只保留时间关系，当前不判断因果。

固定研究窗口：

```text
UTC:      [2026-02-27 16:00:00Z, 2026-03-06 08:40:00Z)
北京时间: [2026-02-28 00:00:00+08:00, 2026-03-06 16:40:00+08:00)
粒度:     5 分钟
槽数:     1,928
```

## 2. 必须遵守的边界

- 不修改旧 `backend/core` 检测逻辑，除非用户重新明确授权；
- 不写现有数据库；
- 不把 NULL/未知值转成 0；
- 不把 ANNOUNCE/WITHDRAW 报文数解释为可见前缀、地址空间或流量；
- 不把固定窗口无 UPDATE 解释为不可见、正常或恢复；
- 不把数据库指标代理冒充正式路由状态；
- 不把时间先后解释为前兆因果、物理断路或政府意图；
- 需要新增能力时优先放在外围新研究模块；
- 若必须扩大原始数据范围，先写明要补的具体字段、实体和时间边界。

## 3. 已完成的工作

### 3.1 数据库优先复算

- 国家五分钟曲线完整：`1,928/1,928`；
- 缺槽、越界槽和五项指标 NULL 均为 0；
- 固定六小时基线的旧 IPv4 地址等价值中位数：`10,146,432`；
- 数据库 `176/556` 内部一致，但不等于同快照真实网络人口；
- 数据库读取使用只读事务，数据库写入为 0。

### 3.2 数据库指标代理

```text
workflow_state=completed
acceptance_state=not_accepted
```

代理结果：

| 项目 | 结果 |
| --- | --- |
| 候选开始 | 2026-02-28 18:05+08:00 |
| 连续两槽确认 | 2026-02-28 18:10+08:00 |
| 全窗低谷 | 2026-02-28 22:30+08:00 |
| 低谷旧 IPv4 等价值 | 9,569,024 |
| 相对基线降幅 | 5.691% |
| 首次 99% 部分恢复候选 | 2026-03-01 22:00+08:00 |
| 六槽确认 | 2026-03-01 22:30+08:00 |
| 后续是否再次跌破 | 是 |
| 完全恢复候选 | 无 |
| 持续时间 | 至少 513,300 秒 |
| metric-only wave | 4 个 |

`5.691%` 来自旧 `/24 × 256` 等价值，不是同快照去重 IPv4 地址并集。

### 3.3 十三槽定向原始消息

固定窗口：

| 阶段 | 北京时间 | UTC | 槽数 |
| --- | --- | --- | ---: |
| 前候选窗口 | 02-28 06:30–06:45 | 02-27 22:30–22:45 | 3 |
| 第一主窗口 | 02-28 18:35–19:00 | 02-28 10:35–11:00 | 5 |
| 第二主窗口 | 02-28 22:20–22:45 | 02-28 14:20–14:45 | 5 |

结果：

```text
UPDATE artifact:       13/13
RouteEvent/raw ref:    1,923/1,923
ANNOUNCE/WITHDRAW:     1,678/245
唯一 VP 身份:          89
RIB/seed/状态回放:     0/否/否
数据库连接/写入:       0/0
```

代表实体：

| ASN / Prefix | 消息数 | A / W | VP | 边界 |
| --- | ---: | ---: | ---: | --- |
| AS61008 / `2a05:a380::/29` | 1,774 | 1,583 / 191 | 47 | IPv6 高频路由震荡 |
| AS39501 / `85.204.30.0/23` | 146 | 93 / 53 | 42 | 第二主窗口集中变化 |
| AS42337 / `2.188.40.0/24` | 3 | 2 / 1 | 2 | 证据过于稀疏 |
| AS48715 / `78.110.120.0/22` | 0 | 0 / 0 | 0 | 零命中不能解释为状态 |

观察现象：

- 前候选窗口已经出现 AS61008 IPv6 的大量宣告与撤回交织；
- 第一主窗口仍以 AS61008 IPv6 为主；
- 第二主窗口同时出现 AS61008 IPv6 与 AS39501 IPv4 的集中变化；
- 第二主窗口定向样本的 WITHDRAW 占比为 26.0%，但样本组成发生变化，不能外推为
  全国撤回率；
- 第二主窗口前 10 分钟有 430 条匹配消息，随后仅剩 1 条，最后两槽零命中；
- 消息迅速稀疏不能单独证明恢复。

### 3.4 报告主张对账

```text
confirmed=1
revised=3
unverifiable=4
hypothesis_only=3
```

关键评级：

- 报告事件时间 `16:14`：`revised`；
- IPv4 约下降 6%：`revised`，数值接近但指标口径不同；
- 恢复状态：`revised`，只有部分恢复候选，之后再次下降；
- 报告 `199/595`：`unverifiable`；
- 报告 `73/126`：`unverifiable`；
- 数据库 `176/556`：只确认旧事实内部一致；
- 主动回撤、物理断路、政府意图：不能由 RRC25 单源确认。

## 4. 仍未解决的 P0 数据缺口

1. **旧事件事实模型未改造**
   `country_outage` 仍是 `s_time/e_time + 可覆盖峰值摘要`，没有正式保存
   `detected_at/onset_at/peak_at/trough_at/partial_recovery_at/
   full_recovery_at/observation_end_at`。

2. **没有逐槽路由状态**
   尚未获得 `VP × AFI × Prefix → Route` 的异常前、中、后连续状态。

3. **没有统一 ASN 人口**
   `199/595`、`73/126` 与 `176/556` 仍不能同口径比较。

4. **没有同快照双栈分类**
   尚未重算完全不可见、部分可见、IPv6 仍可见和观察结束仍未恢复 ASN。

5. **恢复仅为指标代理**
   连续六槽规则已经用于数据库曲线，但没有逐 VP/前缀状态支持正式恢复结论。

6. **原始引用仅到消息层**
   已有 RouteEvent → raw ref → artifact/ordinal/SHA256；尚无
   Incident → State Sample → RouteEvent 的完整映射。

7. **旧查询语义仍有问题**
   `backend/database/feature_country.py` 仍使用 `or 0`，且没有 `v6ip_num`。

## 5. 下一阶段建议

不要重复 DB-first 导出，也不要重复 13 槽消息扫描。下一步应做“最小状态补证”：

1. 在外围新模块建立不可变 Incident/Episode 事实模型；
2. 从相邻 RIB 冻结 IR origin ASN、Prefix 和 VP 基线人口；
3. 只预过滤并处理 IR 相关前缀；
4. 回放到少量关键快照：基线、代理开始、低谷、部分恢复、观察结束；
5. 生成 `Prefix × VP → Origin ASN` 状态；
6. 重新计算 `199/595` 与 `73/126`；
7. 验收至少四类代表 ASN：IPv4 完全不可见、部分可见、IPv6 仍可见、未恢复；
8. 生成正式 Sample/Episode/Wave，并闭合到相关 RouteEvent；
9. 仍不需要做 1,928 个全球 UPDATE 文件的无过滤全量回放。

## 6. 关键本地文件

```text
工作区：
/Users/botongwu/Documents/domeye/core-work

完整复算报告：
docs/data/RRC25伊朗国家路由中断事件复算与对账报告.md

数据库先行执行记录：
docs/data/RRC25伊朗事件数据库先行复算执行记录.md

十八点零五至二十三点状态重放与模型改造需求（当前未提交）：
docs/data/RRC25伊朗事件1805-2300状态重放与事件模型改造需求.md

十三槽观察简报（当前未提交）：
docs/data/RRC25伊朗事件十三槽定向重放观察简报.md

OpenSpec：
openspec/changes/build-rrc25-iran-country-outage-research-loop/

数据库最终化入口：
dev/data_quality/rrc25_iran_db_proxy_finalize.py

十三槽消息入口：
dev/data_quality/rrc25_iran_targeted_raw.py

研究 Profile：
config/research/iran-rrc25-202602.json

报告主张清单：
config/research/iran-rrc25-report-claims.json
```

## 7. 服务器制品

基础目录：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0
```

关键包：

```text
db-first-final-v1
targeted-raw-final-v1
db-proxy-final-v2
db-proxy-final-v2-repro
```

当前交付身份：

```text
生产数据 release: 20260717T124354Z
Run ID:            research_run_v1_84d1096fa441de59d1f9dc9a
语义指纹:          af2c1e6c72573ad41dae8e4d839b68ebecb55ecd0d703d4d4637660e0e03ce81
```

`db-proxy-final-v2` 与 `db-proxy-final-v2-repro` 逐字节一致，哈希通过，文件权限为
`0440`。

## 8. Git 状态

```text
分支: codex/rrc25-iran-research-loop
HEAD: f994282
远端: origin/codex/rrc25-iran-research-loop
标签: 20260723T150948Z-rrc25-iran-db-proxy-final-01
```

已完成验证：

```text
65 项相关测试通过
OpenSpec strict 通过
backend/core SHA256 清单通过
```

当前工作区只有以下已知未提交文件：

```text
docs/data/RRC25伊朗事件1805-2300状态重放与事件模型改造需求.md
docs/data/RRC25伊朗事件十三槽定向重放观察简报.md
docs/data/RRC25伊朗事件新会话上下文.md
```

远端是裸 SSH Git 仓库，没有 PR 接口。

## 9. 新 Session 首条指令

```text
请先阅读：
/Users/botongwu/Documents/domeye/core-work/docs/data/RRC25伊朗事件新会话上下文.md

不要重新执行 DB-first 导出、13 槽消息扫描或 1,928 槽全球全量回放。
现有结论是 workflow_state=completed、acceptance_state=not_accepted。

下一步严格按以下需求执行：
/Users/botongwu/Documents/domeye/core-work/docs/data/RRC25伊朗事件1805-2300状态重放与事件模型改造需求.md

固定使用北京时间 2026-02-28 18:05–23:00、最近的 08:00Z bview 和
84 个 UPDATE，完成一次有界状态重放，然后改造 country_outage 事件模型。

此次允许修改国家中断相关的 backend/core/BGPOutage.py 和
backend/database/country_outage.py；缺少逻辑可以直接实现，不做繁琐审计，不设置
文件处理时间上限；不得扩大到其他事件或 1,928 槽全量回放。
```
