## Why

数据库代理只能定位伊朗事件的聚合曲线，不能回答每个 RRC25 观测点上的
Prefix×VP 状态、逐 ASN 可见性和窗口内恢复。旧 `country_outage` 又把检测时间、
峰值集合、分子分母和恢复时间混在一条可变摘要中，导致页面虽然诚实显示缺失，
但无法形成结构化事件事实。

## What Changes

- 精确使用 `bview.20260228.0800.gz`、25 个 catch-up UPDATE 和
  `[10:05,15:00)` 的 59 个 UPDATE，生成 60 个状态观察点。
- 用固定 seed cohort 计算 IPv4、IPv6、双栈 ASN 和 Prefix×VP 可见状态，动态
  IR origin 单独报告，不改变分母。
- 新增 Incident/Episode/Observation/Milestone/SourceContext v2，明确九个时间与
  状态字段。
- 将 `BGPOutage.__check_country_outage` 改成 Observation → 纯状态转换器 →
  Repository；旧 Core 缺少 Prefix×VP 时恢复保持 unknown。
- 新增事务化 PostgreSQL 表、迁移和旧字段 peak snapshot 兼容投影。
- 正式真实数据只运行一次；不扩大到 1,928 槽，不部署生产。

## Capabilities

### New Capabilities

- `rrc25-iran-bounded-state-replay-v2`：固定窗口双栈逐 VP 状态、cohort 和文件包。
- `country-outage-event-model-v2`：结构化国家中断事件、事务持久化和旧接口投影。

## Impact

- 修改 `backend/core/BGPOutage.py` 的国家中断入口及其哈希清单。
- 修改 `backend/database/country_outage.py`，新增 v2 Repository 与迁移。
- 新增有界重放入口、纯函数测试、真实伊朗事件结果包和中文报告。
- 不写原始 MRT，不运行其他事件，不修改前端，不发布生产。
