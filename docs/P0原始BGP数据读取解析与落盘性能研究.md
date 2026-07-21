# P0 原始 BGP 数据读取、解析与落盘性能研究

> 文档身份：这是 2026-07-20 的三文件小样本历史研究，用于估算解析与落盘成本，不是最终 D3 覆盖报告，也不证明五天目录之外的原始数据完整。最终覆盖、RouteEvent pilot 和准入结论以 `docs/data/P0数据验收报告.md` 为准。

## 1. 结论摘要

本次研究仅执行只读元数据盘点和 3 个文件的小样本实验，没有全量导入，没有连接或写入任何 PostgreSQL 实例，没有修改 `backend/core/`，没有重建 PGDATA、重启服务或安装依赖。

主要结论如下：

1. `/home/bgpdata/data/ripe/rrc25/2026.02` **不是完整 2026 年 2 月目录**。当前仅有 2026-02-24 至 2026-02-28 五天数据，共 1,455 个文件、14.215 GB。每天有 288 个 5 分钟 UPDATE 和 3 个 8 小时 RIB，五天内部时间粒度完整；2 月 1–23 日是否存在于其他位置仍未知。
2. 文件全部为 gzip 压缩 MRT。UPDATE 是 MRT `BGP4MP`（type 16），样本含 subtype 0/1/4/5；RIB 是 `TABLE_DUMP_V2`（type 13），样本含 Peer Index Table、IPv4 Unicast RIB 和 IPv6 Unicast RIB（subtype 1/2/4）。
3. **主要瓶颈不是磁盘读取，也不是文件级 SHA256。** UPDATE 样本直接读取仅 0.05–0.06 秒，gzip 解压 0.16–0.21 秒，完整 `bgpdump` 解析却需要 4.3–5.7 秒。最主要成本是 MRT/BGP 语义解析和 `bgpdump` 文本展开；字段规范化几乎没有进一步增加墙钟时间。
4. 逐 MRT record SHA256 有可见成本，但仍低于完整 BGP 解析：UPDATE 样本由 framing 的 0.41–0.54 秒增加至 0.75–0.96 秒；大 RIB 由 22.04 秒增加至 42.45 秒。文件级 SHA256 每个文件只需计算一次，大 RIB 为 1.61 秒。
5. 现有 `bgpdump -p` 能输出 MRT record ordinal；同一 ordinal 会对应多条路由元素。因此 RouteEvent 的稳定逻辑定位必须是 `artifact_id + record_ordinal + element_ordinal`，不能只保存 `record_ordinal`。
6. 最可靠、成本合理的原始定位组合是：
   - 文件级：完整压缩文件 SHA256；
   - MRT record 级：ordinal、解压后字节偏移、record length、record SHA256；
   - 逻辑路由元素级：element ordinal。
   不建议把 gzip 压缩流偏移作为主定位字段；没有 DEFLATE 窗口/checkpoint 时，它不能稳定随机解码。
7. 不应重复保存完整解析结果或原始载荷。推荐保留原始 gzip MRT，只建立紧凑 RouteEvent、原始 record sidecar 和文件 manifest；`as_path` 使用内容寻址字典。两个 UPDATE 样本中唯一路径文本仅占全部路径文本字符量的约 11.6%–12.3%。
8. 当前服务器没有 PyArrow、Fastparquet、Polars、DuckDB、BGPKIT、PyBGPStream 或 mrtparse。Parquet 未做实测，不能仅凭理论性能直接推荐。当前可落地的最小原型应使用现成的 gzip、`bgpdump 1.6.2` 和旁路代码；若要比较新解析器或 Parquet，必须另行申请安装依赖。
9. 现有五天数据按样本缩放约会产生 13.0 亿条逻辑路由元素；RIB 只取得一个文件 170 秒超时前的部分结果，所以该数字不确定性很高并可能偏低。按相同元素量计算，即使紧凑 JSONL 也约 288 GB；因此“仅存 RouteEvent 索引和原始引用”不是可选优化，而是容量前提。
10. 对“完整 28 天”的数字只能给出**等比例情景估算**，不能当作已发现的数据事实。按当前五天等比例外推，压缩输入约 79.6 GB、解压流约 650 GB、逻辑路由元素中心情景约 72.9 亿；单进程解析的偏低情景约 19.3 小时，数据库 COPY 和索引成本尚未实测。

## 2. 范围、边界与口径

### 2.1 读取的项目约束

- 本地项目：`/Users/botongwu/Documents/domeye/core-work`
- 服务器项目：`/home/bgpdata/Domeye-Core`
- 原始目录：`/home/bgpdata/data/ripe/rrc25/2026.02`
- 固定数据档：`config/data-profile.json`，ID 为 `feb-mar-2026`
- 固定窗口：`2026-02-01T00:00:00+08:00 <= t < 2026-04-01T00:00:00+08:00`
- 本地 Git SHA：`961df60d066c97f4f1fcdb422648296581397e3a`
- 服务器 Git SHA：`db230634a31a289d3d2ba6aa27a862117c04ade0`

服务器与本地 `AGENTS.md`、`config/data-profile.json` 的 SHA256 一致。`docs/P0数据基础建设计划.md` 当前只在本地工作树中存在，服务器工作树中没有该文件。本次没有尝试同步或修改服务器代码。

### 2.2 实验约束执行结果

| 边界 | 本次结果 |
| --- | --- |
| 原始目录写入/删除 | 未执行 |
| 原生产数据库连接或写入 | 未执行 |
| 任意 PostgreSQL 写入 | 未执行 |
| `backend/core/` 修改 | 未执行 |
| PGDATA、Nginx、Screen、API 操作 | 未执行 |
| 新依赖或系统软件安装 | 未执行 |
| 全月哈希或解析 | 未执行 |
| 代表文件数 | 3 |
| 单阶段最长时间 | 170.00 秒，低于 10 分钟门槛 |
| 临时落盘 | 337.17 MB，低于 5 GB 门槛 |
| 基准累计压缩输入读取量 | 小于 5 GB，低于 50 GB 门槛 |

### 2.3 指标口径

- MB、GB 使用十进制 `10^6`、`10^9` 字节。
- `MRT records` 是 MRT common header 定义的物理记录数。
- `route elements` 是 `bgpdump -m` 展开的逻辑路由行数。一个 MRT record 可以产生多个 route elements。
- CPU 时间为进程及其子进程的 user + system time；峰值内存为最大 RSS。
- 顺序读取使用 `dd iflag=direct`，减少页缓存对结果的影响。
- gzip、framing、hash、解析均流式处理，不生成完整解压文件。
- JSONL/TSV 写入实验只写 200,000 行，未执行 `fsync`，结果代表序列化和页缓存写入成本，不代表持久化数据库吞吐。

## 3. 原始目录画像

### 3.1 文件数量与容量

| 类型 | 文件数 | 压缩总字节 | 平均文件 | 最小文件 | 最大文件 |
| --- | ---: | ---: | ---: | ---: | ---: |
| UPDATE | 1,440 | 7,814,038,802 | 5.43 MB | 3.21 MB | 31.39 MB |
| RIB | 15 | 6,401,199,173 | 426.75 MB | 425.83 MB | 428.97 MB |
| 合计 | 1,455 | 14,215,237,975 | 9.77 MB | 3.21 MB | 428.97 MB |

全体文件大小分位数：P25 4.20 MB、P50 4.71 MB、P75 5.67 MB、P90 7.59 MB、P95 10.33 MB、P99 31.39 MB。RIB 数量少但占压缩容量的 45.0%。

### 3.2 时间分布与粒度

| 日期（UTC） | UPDATE 数 | RIB 数 | 当日压缩容量 |
| --- | ---: | ---: | ---: |
| 2026-02-24 | 288 | 3 | 2.721 GB |
| 2026-02-25 | 288 | 3 | 2.867 GB |
| 2026-02-26 | 288 | 3 | 3.255 GB |
| 2026-02-27 | 288 | 3 | 2.820 GB |
| 2026-02-28 | 288 | 3 | 2.552 GB |

- UPDATE 命名：`updates.YYYYMMDD.HHMM.gz`，每 5 分钟一个。
- RIB 命名：`bview.YYYYMMDD.HHMM.gz`，每天 UTC 00:00、08:00、16:00 各一个。
- 样本 MRT header 时间与文件名 UTC 时间一致。
- 目录没有 2026-02-01 至 2026-02-23 的文件。不能将目录名 `2026.02` 等同于完整月份。

### 3.3 压缩和 MRT 类型

所有 1,455 个文件均为 gzip。三个样本通过 MRT common header framing 得到：

| 文件 | MRT type/subtype | 记录数 |
| --- | --- | ---: |
| `updates.20260228.2145.gz` | 16/0、16/1、16/4、16/5 | 133,866 |
| `updates.20260224.0400.gz` | 16/0、16/1、16/4、16/5 | 171,806 |
| `bview.20260226.0000.gz` | 13/1、13/2、13/4 | 1,398,929 |

类型解释：

- type 16：BGP4MP；subtype 0/5 为 2 字节/4 字节 ASN 的状态变化，1/4 为 2 字节/4 字节 ASN 的 BGP message。
- type 13：TABLE_DUMP_V2；subtype 1 为 Peer Index Table，2 为 IPv4 Unicast RIB，4 为 IPv6 Unicast RIB。

### 3.4 文件系统与已有血缘制品

- 文件系统：ext4，挂载在 `/`。
- 容量：约 11 TB，已用 8.9 TB，可用 1.5 TB，使用率 86%。
- 原始目录内全部一级条目均为普通文件，没有子目录或符号链接。
- 在原始目录两级范围内未发现 manifest、SHA256/checksum、MD5、处理日志或 processed 标记。
- 目录和文件归属为 `root:root`；目录模式 `0755`。

因此当前文件虽然可读，但还没有可证明“已冻结、已清点、已处理”的文件级证据链。

## 4. 实验环境

| 项目 | 值 |
| --- | --- |
| 主机 | `buptserver16` |
| 内核 | Linux 5.15.0-176-generic x86_64 |
| CPU | 2 路 Intel Xeon E5-2620 v4，16 物理核、32 逻辑 CPU，2.10 GHz |
| 内存 | 188 GiB，总可用约 124 GiB |
| 实验前负载 | 4.58 / 4.20 / 3.91 |
| 实验前 CPU 空闲 | 约 86% |
| gzip | 1.10 |
| pigz | 2.6，存在但未用于正式结果 |
| MRT 解析器 | `bgpdump 1.6.2-2` |
| Python | 3.10.12 |
| 已有数据相关 Python 包 | pandas、psycopg2 |
| 不存在的解析包 | mrtparse、PyBGPStream、BGPKIT parser |
| 不存在的列式包/工具 | PyArrow、Fastparquet、Polars、DuckDB |

旧项目中已有 `bgpdump -> 临时文本 -> Python` 和 `bgpdump stdout -> Python` 两种路径的基准脚本，但生产辅助函数仍会先生成完整 `.data` 文本。旧日志还记录了 4 次 `bgpdump 1.6.2` 属性断言失败。该风险发生在其他月份，不代表本次三个样本失败，但说明全量导入必须按文件隔离错误并固定解析器版本。

## 5. 样本选择

| 样本 | 文件 | 压缩大小 | 解压大小 | 文件 SHA256 |
| --- | --- | ---: | ---: | --- |
| 小 UPDATE | `updates.20260228.2145.gz` | 3.212 MB | 22.350 MB | `786206f74886b4efb7b074c74e9b4fcae3a6585965fd1db39199df6601bf11c2` |
| 中位 UPDATE | `updates.20260224.0400.gz` | 4.713 MB | 29.640 MB | `07351df935fe6891eada46b290ad80b88d0fba3dec9c666d3106f6f7fb4e9fc9` |
| 大 RIB | `bview.20260226.0000.gz` | 428.968 MB | 4,347.523 MB | `85974652881a7eb93f0193999992544c7c149116029d0f581c508fbfb41db11f` |

小 UPDATE 是全目录最小文件；中位 UPDATE 是全目录 P50 文件；大 RIB 是全目录最大文件。

## 6. 分层基准结果

### 6.1 核心阶段

每个单元格格式为“墙钟秒 / CPU 秒 / 峰值 RSS / 吞吐”。

| 阶段 | 小 UPDATE | 中位 UPDATE | 大 RIB |
| --- | --- | --- | --- |
| 顺序读取到 `/dev/null` | 0.047 / 0.005 / 11.0 MB / 68.2 MB/s | 0.065 / 0.006 / 11.0 MB / 73.0 MB/s | 0.966 / 0.032 / 11.1 MB / 444.0 MB/s |
| gzip 解压到 `/dev/null` | 0.165 / 0.144 / 11.0 MB / 135.7 MB/s 解压流 | 0.215 / 0.197 / 11.0 MB / 138.0 MB/s 解压流 | 23.051 / 23.006 / 11.1 MB / 188.6 MB/s 解压流 |
| MRT framing，不解析 BGP | 0.414 / 0.415 / 11.8 MB / 323.7k records/s | 0.541 / 0.542 / 11.9 MB / 317.7k records/s | 22.041 / 22.042 / 11.9 MB / 63.5k records/s |
| framing + 每 record SHA256 | 0.751 / 0.753 / 12.8 MB / 178.2k records/s | 0.964 / 0.966 / 12.8 MB / 178.2k records/s | 42.448 / 42.446 / 12.9 MB / 33.0k records/s |
| 文件级 SHA256 | 0.015 / 0.017 / 15.1 MB / 212.3 MB/s | 0.022 / 0.023 / 16.6 MB / 216.5 MB/s | 1.614 / 1.615 / 20.1 MB / 265.8 MB/s |
| `bgpdump -m` 完整解析，不落盘 | 4.319 / 4.541 / 11.6 MB / 66.2k elements/s | 5.675 / 5.923 / 11.7 MB / 93.4k elements/s | 170.003 / 193.121 / 11.7 MB / 194.9k elements/s，超时、仅下界 |
| 完整解析 + 最小 RouteEvent 字段 | 4.307 / 5.094 / 11.7 MB / 66.3k elements/s | 5.709 / 7.620 / 11.7 MB / 92.8k elements/s | 未继续，避免重复长实验 |

大 RIB 在 170 秒内已流式输出 33,134,008 个 route elements、4.973 GB `bgpdump` 文本，但进程达到硬超时，未获得完整元素数。这些文本只经过管道计数，没有写入临时文件。

### 6.2 UPDATE 记录构成

| 指标 | 小 UPDATE | 中位 UPDATE |
| --- | ---: | ---: |
| MRT records | 133,866 | 171,806 |
| route elements | 285,736 | 529,882 |
| Announce | 262,682 | 474,196 |
| Withdraw | 22,831 | 55,473 |
| STATE | 223 | 213 |
| IPv4 elements | 110,544 | 244,989 |
| IPv6 elements | 175,192 | 284,893 |
| VP（peer IP + ASN）组合 | 180 | 180 |
| 唯一前缀 | 18,248 | 22,622 |
| 含路径元素 | 262,905 | 474,409 |
| 唯一 AS_PATH | 30,688 | 47,614 |
| 全部路径字符 / 唯一路径字符 | 8.04 MB / 0.99 MB | 16.55 MB / 1.91 MB |

`bgpdump -p` 小样本的输出 index 从 0 到 133,865，和 MRT record ordinal 完全对应；285,736 行中有 152,542 行与前一行共享 ordinal。这证明一个 MRT record 会展开为多个逻辑元素，必须增加 `element_ordinal`。

### 6.3 小批量临时文件写入

每种格式只写 200,000 个 route elements。完整 JSONL 是“字段对象 + 原 `bgpdump` 文本”的保守上界；紧凑 JSONL 使用短字段名并只保留查询字段和原始引用占位；COPY TSV 是拟用于 COPY 的紧凑传输格式。

| 格式 | 小 UPDATE：时间 / 大小 / 字节每行 | 中位 UPDATE：时间 / 大小 / 字节每行 |
| --- | --- | --- |
| 完整 JSONL | 3.967 秒 / 104.69 MB / 523.5 B | 3.887 秒 / 100.35 MB / 501.7 B |
| 紧凑 JSONL | 3.586 秒 / 44.14 MB / 220.7 B | 3.327 秒 / 44.39 MB / 222.0 B |
| COPY TSV | 3.423 秒 / 21.69 MB / 108.5 B | 2.618 秒 / 21.90 MB / 109.5 B |

临时输出总量为 337.17 MB，位于 `/tmp/domeye-p0-bench-9mv2nybs`。这些数字包含 `bgpdump` 生产者和 Python 序列化开销；写入使用页缓存，没有 `fsync`，不能代替 PostgreSQL COPY 实测。

## 7. 瓶颈判断及证据

### 7.1 当前主要瓶颈排序

1. **MRT/BGP 语义解析和文本展开。** UPDATE 解析比直接读取慢约 75–92 倍，比单纯解压慢约 26 倍。RIB 在 170 秒内仍未完成。
2. **JSON 序列化或数据库写入。** JSONL 序列化已有明显 CPU 和容量放大；数据库尚未实测，可能在单写入端成为新的主瓶颈。
3. **逐 record 哈希。** 相比 framing，UPDATE 增加约 0.34–0.42 秒，大 RIB 增加约 20.4 秒；可接受，但应每个物理 MRT record 计算一次，而不是每个 RouteEvent 重复计算。
4. **gzip 解压。** 单线程 CPU 受限，但绝对时间显著低于完整解析。
5. **磁盘读取与文件级 SHA256。** 当前样本中均不是瓶颈。

### 7.2 字段规范化不是当前瓶颈

小 UPDATE 的纯解析和最小字段规范化墙钟分别为 4.319 秒和 4.307 秒；中位 UPDATE 为 5.675 秒和 5.709 秒。规范化消费者与 `bgpdump` 生产者并行，整体仍由 `bgpdump` 限速。

### 7.3 数据库落盘仍是未知项

本次没有向 PostgreSQL 写入。以下结论不能从本次实验推出：

- COPY 的真实 rows/s；
- WAL、checkpoint、autovacuum 对持续导入的影响；
- 分区和主键维护成本；
- 次要索引创建时间与临时空间；
- TimescaleDB 与普通 PostgreSQL 分区表的差异。

因此不能断言数据库一定比解析快。后续只有在获准使用明确的临时数据库后，才能执行 COPY 验证。

## 8. RouteEvent 存储方案比较

| 方案 | 本次证据 | 优点 | 问题 | 结论 |
| --- | --- | --- | --- | --- |
| 完整 JSONL | 501.7–523.5 B/element | 可读、调试方便 | 重复键名、属性和原始文本，五天元素下界已约 667 GB | 不作为主存储 |
| 紧凑 JSONL | 220.7–222.0 B/element | 流式、版本化、适合批次制品和重放 | 查询和单条定位较弱，仍有 JSON 开销 | 适合作为小批次交换/审计制品 |
| COPY TSV | 108.5–109.5 B/element | 当前实测最紧凑，适合 COPY | 自描述性较差，不应单独承担长期契约 | 推荐作为导入传输格式 |
| Parquet/列式 | 当前没有可用引擎，未实测 | 理论上适合分析扫描和压缩 | 安装依赖需要审批，单条证据定位与事务写入需另测 | 保留为后续对照，不在本次直接推荐 |
| PostgreSQL 分区表 + COPY | 未写数据库 | 适合 Evidence Bundle 查询、约束、关联和事务 | 行开销、WAL、索引和分区成本未知 | 推荐作为服务层候选，但必须先做临时库实测 |
| 紧凑 RouteEvent + 原始引用 | framing、ordinal、路径重复率均已实测 | 不复制原始载荷，能追溯、校验和重放 | 需要 manifest、raw record sidecar、路径字典和版本契约 | **推荐方案** |

### 8.1 是否保存完整解析结果

不保存完整解析结果，原因是：

- 原始 gzip MRT 已是不可变事实来源；重复保存完整解析对象没有增加原始真实性。
- 逻辑 route elements 数量远大于 MRT records，重复属性会迅速放大存储。
- 解析器版本变化时，完整派生结果仍需重建，不能代替原始引用。
- Evidence Bundle 需要的是可查询的规范字段、原始定位、完整性校验和限制说明，不需要在每行复制所有 BGP 属性。

应保存紧凑查询字段；对暂不进入规范契约的 community、aggregator、MED、local-pref 等属性，通过原始 record 引用按需复核。若后续查询证明某属性是高频证据，再以版本化扩展表加入，而不是预先复制所有字段。

## 9. 原始记录稳定定位方案

### 9.1 候选比较

| 定位项 | 稳定性 | 读取/计算成本 | 存储成本 | 建议 |
| --- | --- | --- | --- | --- |
| 文件 SHA256 | 极高；绑定完整压缩字节 | 每文件一次，大 RIB 1.61 秒 | 32 B/文件 | 必须，放 manifest |
| MRT record ordinal | 文件字节不变时稳定 | framing 时自然获得 | 4–8 B/record | 必须 |
| gzip 压缩流偏移 | 单独不可随机解码，可能涉及 bit offset 和 32 KiB 窗口 | 正确索引复杂 | 高 | 不作为主引用 |
| 解压后偏移 | 文件不变时稳定，便于顺序校验 | framing 时自然获得 | 8 B/record | 建议保存 |
| 单 record SHA256 | 可独立证明所取 record 正确 | UPDATE 约增加 0.4 秒/文件，大 RIB 增加 20.4 秒 | 32 B/record | 建议在 raw record sidecar 保存一次 |
| element ordinal | 同一 parser contract 下稳定 | `bgpdump -p` 输出流中计数 | 2–4 B/element | RouteEvent 必须 |

### 9.2 推荐组合

规范原始引用：

```text
artifact_id = SHA256(domain_tag || length_prefixed(source_id, collector_id, compressed_file_sha256))
raw_record_key = (artifact_id, record_ordinal)
route_element_key = (artifact_id, record_ordinal, element_ordinal, schema_version)
```

校验信息：

```text
file_sha256
uncompressed_offset
record_length
record_sha256
mrt_type / mrt_subtype
```

`file_sha256 + record_ordinal` 已能唯一定位固定文件中的物理 record；`record_sha256` 用于无需重算全文件时快速证明单条 record；`uncompressed_offset + record_length` 用于快速顺序扫描和检测 ordinal/长度错误。`element_ordinal` 区分同一 BGP message 展开的多个 NLRI/withdraw 元素。

字段字节口径必须冻结：`uncompressed_offset` 指向 12 字节 MRT common header 的起点；`record_length` 保存 header + payload 总长度；`record_sha256` 覆盖同一段 header + payload。若还保留 MRT header 中的 payload length，应使用不同字段名 `mrt_payload_length`，不能混用。

### 9.3 gzip 偏移限制

普通 gzip/DEFLATE 不能只凭压缩字节偏移从任意位置恢复，解码还需要 block bit 状态和滑动窗口。若未来确实需要 RIB 内部随机读取，应另建文件级 indexed-gzip checkpoint（压缩位置、bit 状态、32 KiB window、对应解压偏移），但这属于加速索引，不应进入证据身份。

## 10. 推荐数据结构

### 10.1 文件级 `artifact_manifest`

每个压缩 MRT 文件一行：

```text
artifact_id
source_id                    # ripe_ris
collector_id                 # rrc25
relative_object_path
filename
compression                  # gzip
compressed_size
file_sha256
filename_time_start
first_mrt_time / last_mrt_time
mrt_type_counts
record_count
route_element_count
manifest_schema_version
discovered_at
lineage_status
quality_flags
```

`source`、`collector_id`、文件 SHA、压缩方式和路径不应在每条 RouteEvent 重复保存。

### 10.2 `raw_mrt_record`

每个物理 MRT record 一行：

```text
artifact_id
record_ordinal
mrt_time
mrt_type
mrt_subtype
uncompressed_offset
record_length
record_sha256
quality_flags
```

主键为 `(artifact_id, record_ordinal)`。按样本缩放，现有五天约有 3.22 亿个物理 MRT records；仅 32 B record hash 的中心情景约 10.3 GB，因此 hash 必须只存一次，不能在多个 route elements 中复制。

### 10.3 `route_event`

每个逻辑路由元素一行：

```text
route_event_id               # 由 artifact + record + element + schema 稳定生成
event_date                   # 分区键，UTC
collector_key                # 小整数维表键
artifact_id
record_ordinal
element_ordinal
event_time_utc
action                       # announce / withdraw / rib_snapshot
afi_safi
prefix
vp_key                       # 指向版本化 VP 维表
path_id                      # 指向 AS_PATH 内容寻址字典，可空
origin_asn                   # 无法唯一确定时为空
quality_flags                # 位图或小整数数组
```

字段归属建议：

| 候选字段 | 保存位置 | 说明 |
| --- | --- | --- |
| `route_event_id` | RouteEvent | 必须逐元素稳定 |
| `source` | manifest/collector 维表 | 不重复字符串 |
| `collector_id` | manifest；RouteEvent 保存小整数分区键 | 查询和分区需要受控冗余 |
| `vp_id`、`vp_asn` | VP 维表；RouteEvent 保存 `vp_key` | `vp_id` 建议由 collector + peer IP + peer ASN 组成 |
| `event_time` | RouteEvent | 必须，使用 UTC `timestamptz` |
| `action`、`afi_safi`、`prefix` | RouteEvent | 必须 |
| `as_path` | AS_PATH 字典；RouteEvent 保存 `path_id` | 保留 segment 类型，不只存扁平 ASN 数组 |
| `origin_asn` | RouteEvent | 可空；AS_SET/空路径/异常路径需质量标记 |
| `artifact_id`、`record_ordinal`、`element_ordinal` | RouteEvent | 必须的原始引用 |
| `file_sha256` | manifest | 不在 RouteEvent 重复 |
| `record_offset`、`record_length`、`record_hash` | raw record sidecar | 同一 record 的多个元素共享 |
| `parser_version`、`import_run_id` | import run 和 artifact-run 关联表 | 不在每行重复 |
| `quality_flags` | 各层保存本层质量 | 文件、record、element 分层表达 |

### 10.4 AS_PATH 字典

路径字典键应由“保留 segment 类型的规范二进制或规范文本”内容寻址生成。两个 UPDATE 样本中，唯一路径数只占含路径行数的 10.0%–11.7%，路径字符可减少约 87.7%–88.4%。

不能把 `{...}` AS_SET、confederation segment 或 4-byte ASN 简单拍平成整数数组；否则会改变原始语义。`origin_asn` 在末段不唯一时必须为 `null` 并设置 `origin_ambiguous`。

### 10.5 STATE_CHANGE 单独建模

BGP session STATE_CHANGE 没有 prefix，不应伪造成普通 RouteEvent。建议存入 `peer_session_event`，同时保留相同 `raw_record_key`，供 Collector/VP 健康和缺失解释使用。

## 11. 推荐的单次读取流水线

现有依赖已经验证 `gzip -cd file | bgpdump -m -p /dev/stdin` 能得到与直接读取相同的 285,736 行，墙钟 4.24 秒，与直接 `bgpdump` 的 4.32 秒相当。因此可以在 `backend/core/` 外实现以下旁路导入器：

```text
只读打开压缩 MRT
  │
  ├─ 压缩字节流同时计算 file SHA256
  │
  ▼
流式 gzip 解压
  │
  ├─ MRT framing：ordinal、解压偏移、长度、type/subtype、record SHA256
  │
  └─ 原始 MRT frame 通过 stdin 喂给 bgpdump -m -p /dev/stdin
                                  │
                                  ▼
                     ordinal + bgpdump route elements
                                  │
                                  ├─ 同 ordinal 内生成 element_ordinal
                                  ├─ 规范化 VP、前缀、动作、AS_PATH
                                  ├─ 路径字典去重
                                  └─ 按批次生成 COPY 流/小制品
```

实现要点：

1. 原始文件始终以只读方式打开，不在原始目录创建任何文件。
2. 不生成完整中间解压文件，也不生成完整 `bgpdump .data` 文件。
3. file SHA256 在压缩字节首次读取时完成，只计算一次。
4. framing 和 record hash 在同一解压流中完成。
5. `bgpdump` stdout 必须并发消费，避免管道背压死锁。
6. 只有一个 MRT record 的所有 route elements 完成后才能切批，避免半条 record checkpoint。
7. 建议单批 50,000–250,000 elements，或控制在 32–256 MB；两个阈值先到者触发 flush。
8. 先 COPY 主数据，完成后再创建非必要的时间、前缀、VP、origin 次要索引。
9. 解析器 stderr、退出码、异常 record ordinal 和最后成功批次必须进入 import run 日志。
10. 每个文件在独立进程中解析；`bgpdump` 崩溃只失败当前文件，不影响其他文件。

当前 `bgpdump` 是外部文本解析器，虽然可通过 stdin 复用单次解压流，但错误表达和属性保真仍有限。进入原型后应先验证 RIB 的 `-p` ordinal、异常属性和全部字段契约；不能直接把现有小样本成功推广为全量稳定性结论。

## 12. 分区、批量写入和索引

### 12.1 分区

建议物理上分开：

- `route_event_update`：announce、withdraw；
- `route_snapshot_entry`：RIB snapshot；
- `peer_session_event`：BGP session 状态变化；
- 通过只读视图提供统一 observation 接口。

分区优先级：

1. UTC 事件日期 RANGE；
2. collector LIST；
3. 若单日单 collector 仍过大，再评估 6 小时子分区，而不是一开始制造大量空分区。

分区键必须出现在唯一约束中。对外稳定 ID 仍由 artifact/record/element 生成，不依赖物理分区。

### 12.2 COPY 方案

后续获准后建议比较两种路径：

1. 新空分区直接 `COPY FROM STDIN`，每批一个事务，主键在导入期间存在，次要索引导入后建立。
2. COPY 到临时/专用 staging，再以 `INSERT ... ON CONFLICT` 合并；吞吐较低但重跑更直接。

不建议逐条 INSERT。COPY 测试必须记录 WAL 字节、事务提交时间、checkpoint、索引大小、表膨胀和失败重放时间，而不只记录 rows/s。

## 13. 断点续跑与幂等

### 13.1 状态机

```text
discovered
  -> hash_verified
  -> parsing
  -> loading(batch N)
  -> validating
  -> complete

任一步可转 failed，保留失败 ordinal、batch、stderr 和版本。
```

### 13.2 文件与批次状态

`import_run` 记录：

```text
import_run_id
schema_version
parser_name / parser_version / parser_binary_sha256
normalizer_git_sha
host
started_at / finished_at
status
config_hash
```

`import_artifact_state` 记录：

```text
artifact_id
import_run_id
last_committed_record_ordinal
last_committed_element_ordinal
committed_route_count
committed_batch_id
rolling_output_digest
status / error
```

### 13.3 幂等规则

- 相同压缩文件字节得到相同 `artifact_id`。
- 相同 artifact、record、element 和 schema 得到相同 `route_event_id`。
- 批次事务只有在数据和 batch 状态同时提交后才算完成。
- 失败事务整体回滚；重跑时从最后已提交 MRT record 边界开始。
- 目标表的唯一约束阻止重复 RouteEvent。
- 同一 artifact 同一 schema 同时只能有一个 writer；其他进程只可读取状态或等待。
- 解析版本变化产生新的 materialization/version，不静默覆盖旧结果。

### 13.4 gzip 恢复策略

没有 indexed-gzip 时，checkpoint ordinal 不能直接跳到压缩流中部。恢复时从文件开头重新解压并快速丢弃已提交 ordinal，正确性优先。5 分钟 UPDATE 文件重放成本很低；大 RIB 样本单次解析约数分钟，文件级重放仍可接受。

只有当 RIB 重放成为实测瓶颈后，才增加每 64–256 MiB 解压流的 DEFLATE checkpoint。该索引只用于加速，不改变证据 ID。

## 14. 全月时间与空间估算

### 14.1 先区分事实和情景

**已观测事实**只有 2 月 24–28 日五天。下面“28 天等比例”使用 `28 / 5 = 5.6` 缩放，只回答容量规划问题，不表示 2 月 1–23 日文件已经存在。

### 14.2 数据量估算

| 指标 | 已观测五天/样本外推 | 28 天等比例情景 | 主要误差来源 |
| --- | ---: | ---: | --- |
| 文件数 | 1,455 | 8,148 | 假设每天 291 个文件 |
| 压缩输入 | 14.22 GB | 79.61 GB | 日容量范围给出约 71–91 GB |
| 解压 MRT 流 | 约 116 GB | 约 650 GB | 仅 2 个 UPDATE、1 个 RIB 的压缩比 |
| UPDATE route elements | 约 8.04 亿，范围 6.95–8.79 亿 | 约 45.0 亿，范围 38.9–49.2 亿 | 两个 UPDATE 样本差异 |
| RIB route elements | 约 4.97 亿的偏低情景 | 约 27.8 亿的偏低情景 | 15 倍缩放单个 RIB 的 170 秒部分输出，不能给出封闭误差范围 |
| 全部 route elements | 约 13.0 亿的中心情景 | 约 72.9 亿的中心情景 | RIB 不完整且只测一个文件，实际可能更高或因文件差异而变化 |

### 14.3 解析时间估算

按压缩字节吞吐和文件数缩放，单文件单进程：

| 阶段 | 当前五天 | 28 天等比例 | 说明 |
| --- | ---: | ---: | --- |
| 直接读取 | 约 2.1 分钟 | 约 11 分钟 | 小文件固定开销使误差较大 |
| gzip 解压 | 约 12.0 分钟 | 约 1.1 小时 | 单线程 |
| framing | 约 21.2 分钟 | 约 2.0 小时 | Python framing 样本 |
| framing + record SHA256 | 约 38.8 分钟 | 约 3.6 小时 | 可与主流水线融合，不应另读一次 |
| file SHA256 | 约 1.0 分钟 | 约 5 分钟 | 可与首次压缩读取融合 |
| `bgpdump` 完整解析 | 约 3.45 小时的偏低情景 | 约 19.3 小时的偏低情景 | 按单个 RIB 超时值和 UPDATE 吞吐缩放 |

这些阶段在推荐流水线中会重叠，不能简单相加。完整解析是当前主导项。考虑文件大小、UPDATE 活跃度和 RIB 未完成，单进程端到端解析建议按 **五天 3.5–5.5 小时、28 天 20–30 小时** 做容量预算，并保留至少 ±30% 误差。

### 14.4 数据库时间情景

本次未实测数据库。仅用约 13.0 亿 route elements 的中心情景计算 COPY 服务率情景：

| 持续 COPY 速率 | 当前五天 | 28 天等比例 |
| ---: | ---: | ---: |
| 50k rows/s | 7.2 小时 | 40.5 小时 |
| 100k rows/s | 3.6 小时 | 20.2 小时 |
| 200k rows/s | 1.8 小时 | 10.1 小时 |

这不是 PostgreSQL 性能结论，只说明 COPY 低于约 100k rows/s 时会和解析同量级或成为瓶颈。创建次要索引、WAL 和验证时间未包含。

### 14.5 最终存储估算

按 route element 中心情景和小批量测得的平均字节每行：

| 格式 | 当前五天缩放情景 | 28 天等比例情景 |
| --- | ---: | ---: |
| 完整 JSONL | 约 667 GB | 约 3.74 TB |
| 紧凑 JSONL | 约 288 GB | 约 1.61 TB |
| COPY TSV | 约 142 GB | 约 0.79 TB |

PostgreSQL 尚未实测。按紧凑列、路径字典、必要主键和少量索引的工程情景，当前五天建议预留 **0.25–0.55 TB**，28 天等比例建议预留 **1.4–3.1 TB**；这是容量情景，不是测量值。索引策略、TOAST、fillfactor、WAL 保留和路径字典命中率都会显著改变结果。

原文件系统目前只剩约 1.5 TB 可用且使用率已达 86%。在没有精确 COPY/表大小原型前，不应把完整 28 天 RouteEvent 直接写入当前根文件系统。

### 14.6 CPU、内存和临时空间建议

- 当前解析器每文件基本占用 1 个 CPU 核。服务器有 32 逻辑 CPU，但应先限制在 2–4 个文件 worker，观察共享磁盘和数据库 writer。
- 实测峰值 RSS 约 11–22 MB；加入路径字典、批次对象和数据库缓冲后，建议每 worker 预算 256–512 MB，总体预留 2–4 GB 即可，不需要依赖大内存换取全量缓存。
- 主流水线不生成完整解压文件；单批临时空间控制在 32–256 MB。
- 若数据库不可用，最多保留少量已校验批次制品，并设置总临时空间硬上限 5 GB；达到上限必须停机而不是继续堆积。
- 导入完成后的次要索引创建可能需要额外数百 GB 到 TB 临时/最终空间，必须通过原型测量后再决定。

## 15. 在不修改检测核心的情况下支持 Evidence Bundle

### 15.1 可实现的旁路结构

在 `backend/core/` 外增加独立导入和只读关联层：

```text
原始 MRT（只读）
   ├─> evidence_ingest：artifact/raw record/RouteEvent
   └─> 既有 bgpdump 文本 -> backend/core（保持不变）

六类历史事实表（只读）
   + RouteEvent / raw record sidecar
   -> incident_route_observation_link
   -> Evidence Bundle v2 只读组装
```

Evidence Bundle 可以返回：

- `collector_id`；
- `vp_id` 和 `vp_asn`；
- `route_event_id`；
- `artifact_id + record_ordinal + element_ordinal`；
- `file_sha256 + record_sha256`；
- parser/import/schema 版本；
- 覆盖、缺失和关联置信度。

### 15.2 当前核心造成的精确血缘边界

当前检测循环读取 `bgpdump` 文本时，把 `fields[4]`（peer ASN）作为 `vp`，没有保留 `fields[3]`（peer IP）；同一 ASN 的多个 peering session 可能被合并。事实表也没有原始 record ID。

因此：

- 新旁路 RouteEvent 能恢复原始数据中的 VP 身份和原始引用；
- 但不能仅凭历史事实表，事后证明某条 RouteEvent 就是某个异常事实的唯一触发输入；
- 基于时间、前缀、AS_PATH、事件阶段的历史关联只能标记为 `correlated_candidate`，不能标记为精确因果 lineage；
- `raw_traceable` 应只用于能从事实稳定回到 route element 的新重放/新处理结果；历史无法精确映射的记录继续标记 `legacy_untraceable` 或 `legacy_compatible`。

### 15.3 后续精确关联方式

不修改核心业务逻辑的前提下，可在核心调用边界外包装：

1. 旁路导入器先生成 RouteEvent ID；
2. 将同一 `bgpdump -p` 行按原格式交给未修改的检测核心；
3. 外层 orchestrator 维护“当前 artifact/record/element”上下文；
4. 对核心新产生的事实主键建立 `incident_route_event` 关联；
5. 关联写入独立 evidence schema，并记录 orchestrator 和 detector 版本。

这一方案是否能做到一条输入与一个事实的严格事务绑定，仍需检查各检测器的返回值和数据库写入边界。若核心只在内部异步/聚合写事实，外层只能记录批次或时间窗关联，不能伪造逐条关联。

## 16. 风险与未知项

| 风险/未知项 | 影响 | 处理建议 |
| --- | --- | --- |
| 目录只覆盖五天 | 无法宣称完成 2026 年 2 月 | 先确认 2 月 1–23 日是否存在及获准路径，不假设 3 月路径 |
| 没有原始 manifest/checksum | 无法证明文件集合已冻结 | 先生成只读元数据 manifest；全文件哈希需单独批准全月操作 |
| RIB `bgpdump` 170 秒未完成 | RIB 元素数和完整吞吐只有下界 | 原型对一个 RIB 设明确上限并记录进度，不立即扩大 |
| `bgpdump 1.6.2` 旧日志有断言失败 | 单文件可使进程崩溃 | 每文件独立进程、捕获 stderr、失败隔离、解析器二次比较 |
| gzip 无天然随机恢复 | RIB 中途重启需从头解压 | 先文件级重放，必要时再建 indexed-gzip 加速索引 |
| VP 在核心中降为 peer ASN | 历史逐 VP 精确关联不可恢复 | RouteEvent 保留 peer IP + ASN；历史关联明确标记限制 |
| AS_SET/confed/异常属性 | origin 和路径规范化可能失真 | 保留 segment 类型、原始引用和质量标记 |
| STATE_CHANGE 无 prefix | 不能强塞普通 RouteEvent | 单独 `peer_session_event` 建模 |
| 没有 Parquet 引擎 | 无法实测列式方案 | 若需要比较，先报告安装成本并等待批准 |
| 未执行 PostgreSQL COPY | 最终瓶颈和表大小未知 | 仅在获准临时库后做受控 COPY |
| 根文件系统使用率 86% | 最终表、索引和 WAL 可能耗尽空间 | 原型先测 bytes/row 和索引空间，设置硬容量门禁 |
| 当前样本仅一个 collector | 不能外推其他 collector 的 VP/流量特征 | 分区和维表支持 collector，但容量按 collector 单独估计 |
| 本地与服务器 Git SHA 不同 | 实验脚本/计划与部署版本可能漂移 | 原型制品记录双方 SHA 和解析器二进制 SHA |

## 17. 下一步小规模原型计划

本研究完成后应停在方案评审，不直接进入实现或全量导入。若主任务批准原型，建议分三步：

### P1：无数据库单文件原型

- 仍使用本次 2 个 UPDATE 和 1 个 RIB，不扩大文件集合。
- 在 `backend/core/` 外实现单次压缩读取、framing、file/record hash、`bgpdump -p /dev/stdin`、element ordinal 和紧凑批次输出。
- 对相同输入运行两次，验证 manifest、RouteEvent ID、批次 hash 完全一致。
- 随机抽取至少 100 个 route elements，根据 raw ref 重放并核对前缀、动作、VP、AS_PATH。
- 验证 `bgpdump` 异常退出、管道 BrokenPipe、截断 gzip 和重复运行。
- 预计读取小于 3 GB、单次小于 10 分钟、临时空间小于 1 GB；仍不写数据库、不安装依赖。

### P2：解析器和格式对照

- 先决定是否允许安装一个候选解析器和一个 Parquet 引擎。
- 若允许，必须固定版本和制品 hash，再用相同文件对比字段保真、ordinal/offset、吞吐、内存和错误处理。
- 未获安装批准时，只比较现有 `bgpdump`、紧凑 JSONL 和 COPY TSV。

### P3：临时 PostgreSQL COPY

- 在主任务明确批准的全新临时数据库中执行，不连接现有生产或只读快照实例。
- 只写 1–5 百万 route elements，分别测试直接 COPY 与 staging 合并。
- 记录 rows/s、bytes/row、WAL、主键成本、次要索引时间、临时空间、失败回滚和重放。
- 达到 10 分钟、5 GB 临时空间或任何未预期数据库目标时立即停止。

## 18. 最终建议

1. 先把当前目录定义为“rrc25 2026-02-24 至 2026-02-28 原始制品集合”，不要称为完整 2 月。
2. 采用 manifest、raw MRT record sidecar、RouteEvent、VP 维表、AS_PATH 字典和 import run 六层结构。
3. 主原始引用使用 `file SHA256 + record ordinal + element ordinal`；保存解压偏移、长度和 record SHA256 做快速定位与验证；不使用 gzip 压缩偏移作为身份。
4. 保留原 gzip MRT，不重复保存完整原始载荷或完整解析 JSON；紧凑 TSV 用于 COPY，紧凑 JSONL 只用于小批次审计和重放。
5. 用现有依赖先完成单文件单次读取原型；解析器/Parquet 安装和 PostgreSQL 写入分别作为独立审批门槛。
6. 文件 worker 先从 2–4 个开始，批次 50k–250k elements，按 MRT record 边界 checkpoint；一个文件失败不影响其他文件。
7. Evidence Bundle 通过旁路层获取 VP 和 raw ref；历史事实只能给候选关联和限制说明，不能伪造精确逐记录因果血缘。
8. 在获得 COPY 实测和最终存储数据前，不进入全量导入；当前根文件系统 1.5 TB 可用空间不足以安全接受保守的完整月 PostgreSQL 方案。
