## Why

伊朗国家中断详情当前只有旧检测器生成的一行可变峰值摘要，无法可靠表达真实起点、峰值、波次、恢复状态、逐 ASN 影响和原始记录证据。RRC25 在目标研究窗口内的原始 RIB、UPDATE 与国家五分钟特征基本完整，因此现在的核心问题不是继续寻找数据，而是把已有数据转换为可复算、可关联、可解释的数据产品。

本变更以伊朗事件为唯一验收样本，建立可复用于其他国家中断事件的研究型旁路流程；新结果以冻结口径和原始证据为准，不强制复现旧数据库的 `176/556` 或报告的 `199/595、73/126`。

## What Changes

- 新增 RRC25 有界研究输入能力，冻结 UTC 半开窗口、基线 RIB、UPDATE 槽、AS→国家映射、文件哈希、VP 范围和资源门禁。
- 新增文件型 RIB seed + UPDATE 顺序回放能力，保留 IPv4/IPv6、VP、AS_PATH、原始 record/element 坐标及稳定身份。
- 新增五分钟不可变国家状态、逐 ASN/前缀影响、动态 episode 与 wave 数据模型；由数据决定“一次事件两个波次”还是“两个事件”。
- 新增事件级 Evidence Bundle v2 组装、原始证据闭合、确定性文件制品和中文复算对账报告。
- 提供统一、可分块、可断点续跑的研究入口；任一关键缺口或资源越界均失败关闭，不用 0 或推测结果继续运行。
- 保持旧项目只读，不修改 `backend/core/`，不写数据库，不修改前端，不运行真实全量 A/B，不部署生产。

## Capabilities

### New Capabilities

- `bounded-rrc25-research-input`: 冻结并校验伊朗事件研究窗口、基线、原始制品、国家映射和执行边界。
- `rrc25-route-state-replay`: 从 RIB 和 UPDATE 重建双栈逐 VP 路由状态并输出可追溯状态变化。
- `country-outage-episode-analysis`: 从五分钟状态序列生成基线、episode、wave、恢复状态和逐 ASN/前缀影响。
- `country-outage-research-evidence`: 组装 Evidence v2、确定性制品、质量报告和原报告对账结果。

### Modified Capabilities

无。仓库尚无已归档的 OpenSpec capability，本变更不重新解释旧项目或历史事实表。

## Impact

- 预计新增 `backend/data_pipeline/research/rrc25_country_outage/` 文件型研究模块、`dev/data_quality/` 入口和定向测试。
- 预计新增研究 profile、JSON Schema、fixture、数据字典和中文对账报告模板。
- 复用现有 MRT artifact manifest、RouteEvent 稳定身份、Evidence Bundle v2 和质量门禁的安全约束，但不复用当前只支持 UPDATE pilot 的 SQLite 输出作为研究结果存储。
- 大型派生制品位于服务器独立研究制品目录；Git 只保存规格、代码、配置、清单、哈希、摘要和可复现命令。
- 本变更不会修改旧项目、原始 MRT、生产数据库、`backend/core/`、前端或生产服务。
