# INFO 目录数据落库四期执行手册

## 一、目标

S4 只闭合检测与非功能边界，不激活候选 release。完成后应形成以下可复核效果：

- 同一固定 RIB、UPDATE 决策语料分别由文件快照和数据库快照供给；
- 六类检测的事件数量、自然键、过滤原因、风险级别和关键描述无未批准差异；
- 精确查询、最长前缀匹配、快照建立、峰值 RSS、检测吞吐和容量达到最终阈值；
- 运行角色只读，不能读取联系人表或修改 release、质量数据和数据库结构；
- release、逐文件计数、质量结果、耗时、容量和断点状态均可观察；
- S4 阶段结束 Hook 为 `pass`，且 release 仍未激活。

## 二、实现边界

1. 不修改 `backend/core`，六类判定调用既有 Core 方法。
2. 数据库快照显式固定 `content_id`、manifest SHA256 和 `release_sk`。
3. 普通运行路径只做索引精确查询并缓存；枚举完整映射会被拒绝并计数。
4. 数据库运行角色不具有 `as_contact`、`quarantine`、`source_record`、
   `legacy_record` 和 `import_run` 的 SELECT 权限。
5. 权限负向试验全部在显式只读事务中执行，只针对隔离候选库。
6. 生产和开发数据库只读取容器身份与运行状态，不建立数据库连接。
7. 检查过程不激活 release、不停止服务、不删除数据，也不覆盖既有证据。
8. 任一报告为 `fail` 时不调用阶段 Hook，不进入 S5。

## 三、数据库运行时快照

`backend/info_pipeline/runtime.py` 提供固定 release 的惰性快照。它保留既有
`BGPInfo` 的属性名，但只支持精确键读取：

| 既有属性 | 数据库来源 |
| --- | --- |
| `as_info` | ASN 基本字段、策略成员和嵌入关系 |
| `country` | 国家代码和中英文属性 |
| `important_as_dict` | 重要 AS |
| `important_prefix_dict` | 重要 IPv4/IPv6 前缀 |
| `as_prefix_dict` | pfx2as 历史映射 |
| `as_rel_dict` | provider、customer、peer、sibling |
| `prefix_info` | prefix 业务字段和域名声明 |
| `important_domain_dict` | 重要域名 |
| `private_as_dict` | 公网 AS 到私网 AS 城市映射 |
| `triplet_info` | 路由三元组最后一条胜出值 |

联系人字段在普通运行时投影中为空，适配器不查询联系人表。对映射执行全量迭代会
抛出 `FullTableLoadRejected`，并增加请求路径全表装载计数。

## 四、六类检测 A/B

固定语料从同一 release 按稳定排序规则选择，并把选择结果及其 SHA256 写入检测报告。
语料包含三条正常 RIB 观测路径以及以下 UPDATE 决策：

1. 前缀劫持宣告；
2. 子前缀劫持宣告；
3. 路由泄漏 AS_PATH；
4. 三个观测点的前缀回撤；
5. 重要 AS 的 12/12 前缀中断；
6. 固定 100 个 AS cohort 中 8 个 AS 受影响。

A/B 直接调用既有 Core 的以下决策边界：

```text
BGPHijack.is_hijack_event
BGPHijack.hijack_level
BGPSubHijack.is_sub_hijack_event
BGPSubHijack.sub_hijack_level
BGPLeak.is_leak_event
BGPLeak.get_as_rel
BGPLeak.leak_level
get_leak_triplet
BGPOutage.__prefix_outage_level
BGPOutage.__as_outage_level
BGPOutage.__country_outage_level
```

验收器不调用事件表写入、邮件或在线服务。文件和数据库两侧只比较规范化检测输出，
不比较联系人字段。

## 五、性能与容量

性能报告使用真实计时：

- 预热后执行 800 次 ASN、prefix、AS 关系和域名精确查询；
- 预热后执行 400 次 longest-prefix-match；
- 独立子进程测量旧文件 `BGPInfo` 完整装载与数据库固定 release 惰性快照；
- 文件与数据库投影交替执行 7 轮六类检测吞吐；
- 容量同时计入当前数据库、文件回滚制品、下一 release 构建空间、25% 构建临时
  空间和运行峰值。

最终阈值保持为：

```text
精确查询 p95 <= 20 ms
精确查询 p99 <= 50 ms
LPM p95 <= 30 ms
快照装载时间恶化 <= 10%
快照峰值 RSS 恶化 <= 10%
检测吞吐下降 <= 5%
请求路径全表装载次数 = 0
容量状态 = pass
```

## 六、权限与无副作用

执行前在离线候选库幂等应用既有 `create-reader.sql`。运行角色必须满足：

- 可登录、非超级用户；
- 默认事务只读；
- 可读取普通 INFO 查询表；
- 不能 UPDATE release；
- 不能读取联系人表；
- 不能执行 `info.activate_release`。

负向试验包括 release 更新、active release 插入、质量结果插入、schema 建表和激活
函数调用。每项都在显式只读事务中执行并回滚。检查前后还会比较：

- 原生产数据库容器身份、镜像、启动时间、运行状态和重启次数；
- 开发数据库容器的同类状态；
- 候选 release 状态、激活行数、来源文件数、质量结果数、prefix 数和 ASN 数。

## 七、运维可观测性

运维报告必须包含：

- release 身份、状态和激活状态；
- 24 个文件逐文件逻辑、接纳、隔离和未处理计数；
- 质量规则数、阻断失败数和最近检查时间；
- import run 的 scope、状态、起止时间、耗时、checkpoint 和错误摘要；
- 数据库大小、INFO 索引大小和容量余量；
- 固定语料、文件结果和数据库结果的确定性摘要。

S2 的 `all_24_files` 已完成运行必须有 24 个 `loaded_files` checkpoint，才可把
`checkpoint_resumable` 判为真。

## 八、执行命令

```bash
cd /隔离代码目录

export DOMEYE_CORE_INFO_PYTHON=/home/bgpdata/Domeye-Core/backend/.venv/bin/python

./deploy/database/accept-static-info-s4.sh \
  /home/bgpdata/Domeye/backend/info \
  候选数据库容器名 \
  postgres \
  bgp_project \
  /home/bgpdata/Domeye-Core/backend \
  /受控证据目录/S3 \
  /受控证据目录/S4
```

脚本生成：

```text
static-info-manifest.json
static-info-detector-ab.json
static-info-performance.json
static-info-security.json
static-info-operations.json
stage-gate-S4.json
SHA256SUMS
```

## 九、成功复核

```bash
cd /受控证据目录/S4
sha256sum -c SHA256SUMS

jq -e '
  .status == "pass"
  and .event_type_count == 6
  and .unapproved_difference_count == 0
  and .core_hash_unchanged == true
' static-info-detector-ab.json

jq -e '
  .status == "pass"
  and .exact_query_p95_ms <= 20
  and .exact_query_p99_ms <= 50
  and .longest_prefix_match_p95_ms <= 30
  and .snapshot_load_time_regression_percent <= 10
  and .snapshot_peak_rss_regression_percent <= 10
  and .detector_throughput_regression_percent <= 5
  and .request_path_full_table_load_count == 0
  and .capacity_status == "pass"
' static-info-performance.json

jq -e '
  .status == "pass"
  and .unauthorized_write_success_count == 0
  and .contact_plaintext_exposure_count == 0
  and .check_production_side_effect_count == 0
  and .runtime_role_read_only == true
' static-info-security.json

jq -e '
  .status == "pass"
  and .release_state_observable == true
  and .per_file_counts_observable == true
  and .checkpoint_resumable == true
  and .same_input_reproducible == true
  and .activated == false
' static-info-operations.json

jq -e '
  .stage_id == "S4"
  and .status == "pass"
  and .deviation_count == 0
' stage-gate-S4.json
```

## 十、失败处理

- S4 目录一旦创建不得覆盖；失败后改名为
  `S4.incomplete.<UTC>.<PID>` 并保留。
- 数据语义差异只能修复运行时投影或迁移适配器，不得改 Core 检测逻辑。
- 性能不达标不得减少样本数、删除慢查询或修改最终阈值。
- 权限失败不得用管理员身份替代运行身份完成普通查询。
- 容量失败不得删除上一版本、文件回滚制品或失败证据换取空间。
- Hook 未通过时 release 保持 `validating`，不得进入激活阶段。
