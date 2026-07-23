> 执行状态（2026-07-22）：已完成 `bounded_pilot` 的真实单主时槽流程贯通，
> 证据见 `docs/data/RRC25伊朗事件主时槽稀疏证据闭环执行记录.md`。运行结果为
> `incomplete/not_accepted`；该次 pilot 或纯函数测试不能代替伊朗事件验收。
>
> 执行策略修订（2026-07-23）：只读实库已确认伊朗国家指标研究窗
> `1,928/1,928` 槽完整。数据库改为研究主体和定位索引；未勾选的 raw 任务只针对
> 字段缺口、三个关键槽和代表实体，不再要求先完成 1,928 UPDATE 全窗口重放。

## 0. 数据库优先研究主体

- [x] 0.1 停止 seed/全窗口 UPDATE 执行，确认未启动 1,928 UPDATE 重放且数据库写入为 0
- [x] 0.2 以固定 reader 和只读事务核查旧国家事实、国家曲线、AS/前缀事实与业务时区
- [x] 0.3 对账研究窗 1,928 槽、176/556、22:34:40 活跃 176 ASN 与 2,842 前缀
- [x] 0.4 冻结 06:35、18:45、22:30 三段指标事实、约 6% 降幅和恢复候选
- [x] 0.5 形成数据库可答/派生/raw 必需/不可验证的中文计划与缺口矩阵
- [ ] 0.6 发布 create-only DB-first JSON、中文摘要与 `SHA256SUMS`

## 1. OpenSpec 与研究合同

- [x] 1.1 初始化并严格校验本 change 的 proposal、五个 capability specs、design 和 tasks
- [x] 1.2 新增事件级研究 profile 与 JSON Schema，冻结窗口、seed、基线、双栈、episode/wave 和资源参数
- [x] 1.3 新增 sample、episode、wave、episode-AS、研究运行和对账结果的 JSON Schema 与中文数据字典
- [x] 1.4 为所有 schema 增加 valid/invalid fixture，覆盖未知补零、窗口端点、分子分母异时点和因果越界

## 2. 输入盘点与只读安全

- [x] 2.1 实现 seed RIB、catch-up UPDATE 和窗口 UPDATE resolver，输出角色化输入 manifest
- [x] 2.2 复用文件 SHA256、压缩流 EOF/CRC 和读取期间变化检查，增加本事件窗口覆盖对账
- [x] 2.3 只读复制并冻结旧 AS→国家映射，输出哈希、冲突、缺失和兼容 cohort 摘要
- [x] 2.4 实现 dry-run 资源估算和 50GB/10分钟/5GB/数据库写入失败关闭门禁
- [x] 2.5 验证所有新路径不读取旧数据库、不写原始目录、不修改 `backend/core/`

## 3. RIB 与 UPDATE 规范解析

- [x] 3.1 为 TABLE_DUMP/TABLE_DUMP_V2 建立最小黄金 fixture 和可人工核对的 record/element ordinal 预期
- [x] 3.2 扩展外围 bgpdump 适配能力以解析 RIB，并保留 peer、AFI、prefix、AS_PATH segment 和 raw record
- [x] 3.3 为 RIB 多元素、IPv4/IPv6、AS_SET、空路径、未知类型和损坏流增加失败关闭测试
- [x] 3.4 复核 UPDATE announce/withdraw 与 RIB 共用稳定 `route_event_id_v1` 和 `vp_id_v1` 身份语义
- [x] 3.5 在小样本上完成原始 record → RouteEvent → raw ref 哈希回查，不进入完整研究窗口

## 4. 文件型状态回放

- [x] 4.1 实现 `collector+VP+AFI/SAFI+prefix` 状态机纯函数和确定性回放顺序
- [ ] 4.2 仅为代表实体实现相邻 seed RIB、必要 catch-up 和关键槽回放
- [ ] 4.3 用数据库影响索引定位候选，再以原始映射复核代表 IR 前缀/origin
- [ ] 4.4 实现 record 边界检查点、不可变 JSONL gzip 分片、原子发布和哈希绑定恢复
- [ ] 4.5 验证中断、重跑、不同并发顺序和检查点失配均不会改变语义结果或覆盖旧制品

## 5. 国家样本与影响人口

- [ ] 5.1 从数据库冻结兼容 cohort、六小时中位数基线与实际窗口
- [ ] 5.2 发布 1,928 槽国家指标与 `value_state/missing_reason`，禁止未知补零
- [ ] 5.3 分开解释数据库 IPv4 `/24`、IPv4 等效地址和 IPv6 `/48`，不冒充地址并集
- [ ] 5.4 从事实活动区间派生关键时刻 ASN/前缀集合、全/部分影响和恢复候选
- [ ] 5.5 仅将代表 ASN/前缀变化关联到 RouteEvent/raw refs，并明确样本不可外推

## 6. Episode、Wave 与恢复

- [ ] 6.1 先在数据库曲线上实现版本化开始、部分恢复、完全恢复和正常波动带规则
- [ ] 6.2 用数据库识别 episode/wave 与 `split_evidence`，raw 仅补代表证据
- [ ] 6.3 实现 `exact/lower_bound/interval/unknown` 持续时间与四类恢复状态
- [ ] 6.4 增加短暂跨99%、两波间完全恢复、未完全恢复、二次下降和数据缺口测试
- [ ] 6.5 输出旧 Incident 到零个/一个/多个研究 episode 的非因果映射

## 7. Evidence、质量门与报告

- [ ] 7.1 先组装 source fact、数据库 MetricWindow 和缺口矩阵；再按代表样本补 RouteEvent/raw refs
- [ ] 7.2 扩展质量门以校验状态连续性、同快照分子分母、mapping覆盖、episode证据和引用闭合
- [ ] 7.3 实现主张级 `confirmed/revised/unverifiable/hypothesis_only` 对账生成器
- [ ] 7.4 生成独立《RRC25 伊朗国家路由中断事件复算与对账报告》及机器可读对账 JSON
- [x] 7.5 生成研究包 manifest、语义指纹、文件 inventory、`SHA256SUMS` 和中文验收摘要

## 8. 伊朗单事件执行与验收

- [ ] 8.1 为统一入口增加 DB-first、缺口计划、定向 raw dry-run、九分钟软限和只读发布
- [ ] 8.2 对三个关键槽运行 dry-run，确认新增原始读取、临时空间和每分块时间未越界
- [ ] 8.3 仅处理 06:35、18:45、22:30 关键槽与代表实体，不运行其他事件或全窗口重放
- [ ] 8.4 从数据库复算国家曲线、episode/wave、ASN/前缀影响和恢复状态
- [ ] 8.5 对账报告时间、IPv4降幅、`199/595`、`73/126` 与数据库 `176/556`
- [ ] 8.6 抽查至少一个 IPv4 完全不可见、一个部分不可见、一个 IPv6仍可见和一个未恢复 ASN 的完整 raw ref
- [ ] 8.7 在两个空输出目录执行语义复现，验证稳定身份、记录顺序和语义指纹一致
- [x] 8.8 执行定向测试、OpenSpec 严格校验、`git diff --check` 与 `cd backend && sha256sum -c core.sha256`
- [x] 8.9 将轻量中文报告、制品路径、release ID、SHA256 和复现命令写入 Git，不提交大型数据文件
