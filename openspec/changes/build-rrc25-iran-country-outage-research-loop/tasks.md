> 执行状态（2026-07-22）：已完成 `bounded_pilot` 的真实单主时槽流程贯通，
> 证据见 `docs/data/RRC25伊朗事件主时槽稀疏证据闭环执行记录.md`。运行结果为
> `incomplete/not_accepted`；下列未勾选项仍需要连续窗口数据，不能用本次稀疏
> pilot 或纯函数测试代替完整伊朗事件验收。

## 1. OpenSpec 与研究合同

- [x] 1.1 初始化并严格校验本 change 的 proposal、四个 capability specs、design 和 tasks
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
- [ ] 4.2 实现 seed RIB 初始化、catch-up 回放、窗口回放和五分钟边界输出
- [ ] 4.3 实现基线 IR 前缀与动态 IR origin 发现，完整保留相关状态变化
- [ ] 4.4 实现 record 边界检查点、不可变 JSONL gzip 分片、原子发布和哈希绑定恢复
- [ ] 4.5 验证中断、重跑、不同并发顺序和检查点失配均不会改变语义结果或覆盖旧制品

## 5. 国家样本与影响人口

- [ ] 5.1 实现兼容/修订 ASN cohort、稳定六小时中位数基线和不稳定时向前扩展规则
- [ ] 5.2 实现同快照五分钟国家指标与 `value_state/missing_reason`，禁止未知补零
- [ ] 5.3 实现 IPv4 地址并集、IPv4 `/24`、IPv6 `/48`、重叠前缀和 MOAS 计量测试
- [ ] 5.4 实现逐 ASN/地址族的触发、峰值、累计、终点集合及前缀/地址损失
- [ ] 5.5 将每个受影响 ASN 和前缀变化关联到 RouteEvent/raw refs

## 6. Episode、Wave 与恢复

- [ ] 6.1 实现版本化异常开始、部分恢复、完全恢复和基线正常波动带规则
- [ ] 6.2 实现 episode/wave 划分、`split_evidence` 和多波次未完全恢复合并逻辑
- [ ] 6.3 实现 `exact/lower_bound/interval/unknown` 持续时间与四类恢复状态
- [ ] 6.4 增加短暂跨99%、两波间完全恢复、未完全恢复、二次下降和数据缺口测试
- [ ] 6.5 输出旧 Incident 到零个/一个/多个研究 episode 的非因果映射

## 7. Evidence、质量门与报告

- [ ] 7.1 组装包含 source fact、episode sidecar、MetricWindow、RouteEvent 和 raw refs 的事件级 Evidence Bundle v2
- [ ] 7.2 扩展质量门以校验状态连续性、同快照分子分母、mapping覆盖、episode证据和引用闭合
- [ ] 7.3 实现主张级 `confirmed/revised/unverifiable/hypothesis_only` 对账生成器
- [ ] 7.4 生成独立《RRC25 伊朗国家路由中断事件复算与对账报告》及机器可读对账 JSON
- [x] 7.5 生成研究包 manifest、语义指纹、文件 inventory、`SHA256SUMS` 和中文验收摘要

## 8. 伊朗单事件执行与验收

- [ ] 8.1 为统一协调入口增加 dry-run、分块、九分钟软限、续跑、验证和只读发布模式
- [ ] 8.2 先运行 dry-run 并确认新增原始读取、临时空间和每分块时间未越审批边界
- [ ] 8.3 在批准边界内分块处理伊朗研究窗口，不运行其他事件或真实全量 A/B
- [ ] 8.4 复算国家曲线、episode/wave、逐 ASN/前缀影响和恢复状态
- [ ] 8.5 对账报告时间、IPv4降幅、`199/595`、`73/126` 与数据库 `176/556`
- [ ] 8.6 抽查至少一个 IPv4 完全不可见、一个部分不可见、一个 IPv6仍可见和一个未恢复 ASN 的完整 raw ref
- [ ] 8.7 在两个空输出目录执行语义复现，验证稳定身份、记录顺序和语义指纹一致
- [x] 8.8 执行定向测试、OpenSpec 严格校验、`git diff --check` 与 `cd backend && sha256sum -c core.sha256`
- [ ] 8.9 将轻量中文报告、制品路径、release ID、SHA256 和复现命令写入 Git，不提交大型数据文件
