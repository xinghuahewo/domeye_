# INFO 目录数据落库三期执行手册

## 一、适用范围

本文只说明 S3“查询与快照语义闭合”的候选执行方式、证据和失败边界。S3
不切换对外 API，不改变检测结果，不把候选 release 激活为当前 release。

S3 的完成判据是：

- 输入为已经通过 S2 Hook 的同一 `content_id`；
- 重新校验 24 个来源文件的大小和 SHA256，来源仍与 manifest 完全一致；
- 候选库中的 release 仍为 `validating`，24/24 文件已闭合且没有激活记录；
- 文件后端和数据库后端对 ASN、prefix、国家、域名、AS 关系、pfx2as、
  私有 AS、重要 AS、重要域名、重要前缀和三元组的完整语义集合一致；
- 对照内容包含自然键、字段值、空值、来源顺序、列表顺序、first-wins、
  last-wins、域名来源优先级和 prefix 规范化状态；
- S2 隔离记录逐项列明来源文件、来源行、自然键、原因和记录摘要，只作为已批准
  例外，不被伪装成正常记录；
- 联系人只比较内存摘要，联系人原文不会进入报告；
- `static-info-shadow-diff.json` 的三类未批准差异均为 0；
- S3 阶段结束 Hook 产生 `status=pass`、`deviation_count=0` 的回执。

## 二、硬边界

1. 来源目录只读，不在旧 INFO 目录中创建文件。
2. 只读取带 `offline-candidate` 标签的隔离候选数据库。
3. 不修改 `backend/core/`，不改变六类检测算法。
4. 文件结果和数据库结果均不影响对外响应或检测运行。
5. 不用抽样正确率、模糊匹配或聚合行数代替完整语义集合对账。
6. 不把 S2 隔离记录记成未批准差异，也不把未批准差异归入隔离例外。
7. 任何中断或失败均保留原证据目录，不覆盖为成功结果。

## 三、执行命令

```bash
cd /隔离代码目录

export DOMEYE_CORE_INFO_PYTHON=/home/bgpdata/Domeye-Core/backend/.venv/bin/python

./deploy/database/compare-static-info-shadow.sh \
  /home/bgpdata/Domeye/backend/info \
  候选数据库容器名 \
  数据库管理员 \
  数据库名 \
  /受控证据目录/S2 \
  /受控证据目录/S3
```

脚本先复核 S2 的 `SHA256SUMS`、S2 Hook 回执和 manifest 身份，再执行文件/数据库
双读。报告只在全部读取完成后一次性写出；中途不会生成可被误认为通过的半成品。

## 四、语义对账范围

| 对账集合 | 关键语义 |
| --- | --- |
| ASN | 字段、空值、文件顺序、import/export、Sibling、IPv4/IPv6 关系 |
| 联系人 | 仅比较值摘要和位置，不输出原文 |
| 重要 AS | 整数键、标签、首条语义 |
| prefix | raw 键、CIDR、非规范状态、route/bgp、声明计数 |
| prefix-domain | 普通/权威角色、列表序号、完整展开集合 |
| 国家 | alpha-2/3、中英文别名、数字码、坐标与缺失值 |
| 域名 | `website_entity.csv` 优先、URL first-wins、查询字段和来源顺序 |
| pfx2as | ASN 字符串键、空映射键、prefix 顺序及原始数值 |
| AS 关系 | provider/customer/peer/peers/sibling、列表顺序和空列表 |
| 私有 AS | 公网/私网 ASN 字符串键、`ip_num`、city 和列表顺序 |
| 重要域名 | 域名键和完整 JSON 值 |
| 三元组 | 三个 ASN 自然键及旧加载器的最后一条稳定度胜出语义 |
| 重要 prefix | IPv4 文件优先、跨文件 first-wins 和原字段值 |

每条规范语义记录先转成确定性 JSON，再计算 SHA256。完整集合使用计数、
模 2^256 求和、异或和平方和组合摘要，既不把大集合装入证据，也能识别重复项。
对顺序敏感的序号和来源位置作为记录字段参与摘要。

## 五、成功证据

S3 目录必须包含：

```text
static-info-manifest.json
static-info-shadow-diff.json
stage-gate-S3.json
SHA256SUMS
```

复核：

```bash
cd /受控证据目录/S3
sha256sum -c SHA256SUMS

jq -e '
  .status == "pass"
  and .scope == "all_static_queries_and_snapshot"
  and .deterministic_query_unapproved_difference_count == 0
  and .full_set_unapproved_difference_count == 0
  and .snapshot_unapproved_difference_count == 0
  and .contact_plaintext_exposure_count == 0
  and .activated == false
  and ([.sections[].status] | all(. == "pass"))
' static-info-shadow-diff.json

jq -e '
  .stage_id == "S3"
  and .status == "pass"
  and .deviation_count == 0
' stage-gate-S3.json
```

## 六、失败处理

- 依赖缺失、来源身份变化、候选库状态变化和数据库查询错误均在写出通过报告前
  失败关闭。
- 已存在的 S3 目录不得覆盖；失败目录改名为
  `S3.incomplete.<UTC>.<PID>` 后保留。
- 某个集合的计数或摘要不一致时，该集合状态为 `fail`，三类未批准差异至少一项
  大于 0，Hook 不允许通过。
- 修复只能调整旁路读取、规范化或落库适配器；不得修改 `backend/core` 或放宽
  最终验收合同。

## 七、实际执行结果

本期在隔离候选环境完成，证据目录为：

```text
/home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/evidence/S3
```

执行结果：

- 进程退出码为 `0`，墙钟耗时 `1:41:26`；
- 15 个语义段全部为 `pass`；
- 确定性查询用例数为 `8,226,223`；
- 查询、完整集合和快照三类未批准差异均为 `0`；
- 联系人明文暴露数为 `0`；
- 3,419 条批准隔离例外逐项保留，未被混入未批准差异；
- `stage-gate-S3.json` 共 20 项检查通过，`deviation_count=0`；
- release 仍为 `validating`，`info.active_release` 为 0 行。

本期报告的 `content_id` 为：

```text
info_v1_400c1e3f74c43cc37088a49b1ad5655f
```

对应 manifest SHA256 为：

```text
400c1e3f74c43cc37088a49b1ad5655f7705081460efc1df51f3dc477abf4a78
```

两次未完成尝试均作为失败现场独立保留，没有覆盖成功证据：

```text
S3.incomplete.20260726T120339Z.2312713
S3.incomplete.20260726T121217Z.2412364
```

第一份是隔离副本默认 Python 缺少 Excel 依赖；第二份是实现审计期间的预防性
停止。最终成功执行显式使用重构项目的 Python 3.10 虚拟环境。
