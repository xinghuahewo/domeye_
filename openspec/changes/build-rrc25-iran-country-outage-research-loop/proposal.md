## Why

伊朗国家中断详情当前只有旧检测器生成的一行可变峰值摘要，无法可靠表达真实起点、
峰值、波次、恢复状态、逐 ASN 影响和原始记录证据。只读实库已确认目标研究窗口的
伊朗国家曲线为 `1,928/1,928` 槽，旧国家集合可与关键时刻 176 个活跃 ASN 和
2,842 个活跃中断前缀对账。因此现在的首要问题不是重放完整原始窗口，而是先把
数据库已有事实转换为可复算、可关联、可解释的研究主体，再只为 VP、报文和阶段
路径等真实缺口补原始证据。

本变更以伊朗事件为唯一验收样本，建立可复用于其他国家中断事件的研究型旁路流程；新结果以冻结口径和原始证据为准，不强制复现旧数据库的 `176/556` 或报告的 `199/595、73/126`。

## What Changes

- 新增数据库优先只读导出能力，冻结旧 Incident、1,928 槽国家曲线、关键时刻
  ASN/前缀集合、恢复信号、双时区和来源字段。
- 新增字段级缺口矩阵和最小 raw 请求；默认只选择 `06:35`、`18:45`、`22:30`
  三个关键槽与少量代表实体，不启动 1,928 UPDATE 全窗口重放。
- 保留 RRC25 有界研究输入和文件型 RIB/UPDATE 回放能力，用于补充 IPv4/IPv6、
  VP、AS_PATH、原始 record/element 坐标及稳定身份。
- 新增五分钟不可变国家状态、逐 ASN/前缀影响、动态 episode 与 wave 数据模型；
  由数据决定“一次事件多个波次”还是“多个事件”，不预设前兆因果。
- 新增事件级状态与原始引用关联、确定性文件制品和中文复算对账报告。
- 提供“数据库快照 → 缺口矩阵 → 定向 raw 证据”的统一研究入口；任一关键缺口
  或资源越界均失败关闭，不用 0 或推测结果继续运行。
- 保持旧项目只读，不修改 `backend/core/`，不写数据库，不修改前端，不运行真实全量 A/B，不部署生产。

## Capabilities

### New Capabilities

- `database-first-country-outage-study`: 用固定 reader 在只读事务中冻结伊朗事件研究主体、双时区、缺口矩阵和最小 raw 请求。
- `bounded-rrc25-research-input`: 冻结并校验伊朗事件研究窗口、基线、原始制品、国家映射和执行边界。
- `rrc25-route-state-replay`: 从 RIB 和 UPDATE 重建双栈逐 VP 路由状态并输出可追溯状态变化。
- `country-outage-episode-analysis`: 从五分钟状态序列生成基线、episode、wave、恢复状态和逐 ASN/前缀影响。
- `country-outage-research-output`: 组装研究状态、原始引用、确定性制品、质量报告和原报告对账结果。

### Modified Capabilities

无。仓库尚无已归档的 OpenSpec capability，本变更不重新解释旧项目或历史事实表。

## Impact

- 预计新增 `backend/data_pipeline/research/rrc25_country_outage/` 文件型研究模块、
  `dev/data_quality/` 数据库只读/定向 raw 入口和定向测试。
- 预计新增研究 profile、JSON Schema、fixture、数据字典和中文对账报告模板。
- 复用现有独立数据库作为研究主体和定位索引；复用 MRT artifact manifest、
  RouteEvent 稳定身份和质量门禁补证，但不写回数据库。
- 大型派生制品位于服务器独立研究制品目录；Git 只保存规格、代码、配置、清单、哈希、摘要和可复现命令。
- 本变更不会修改旧项目、原始 MRT、生产数据库、`backend/core/`、前端或生产服务。
