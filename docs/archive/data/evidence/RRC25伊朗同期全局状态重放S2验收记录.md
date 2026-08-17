# RRC25 伊朗同期全局状态重放 S2 验收记录

日期：2026-07-26  
阶段：S2「全球 RIB 初始状态闭合」  
运行身份：`global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`  
结论：通过

## 一、执行边界

- 只读取冻结的 RRC25 `2026-02-28T08:00:00Z` seed RIB；
- 本阶段未应用 84 个 UPDATE；
- 未按国家预筛 RIB，全部国家和显式未知桶进入同一 RouteState；
- 未修改 `backend/core/`、旧 Detection、旧业务数据库或既有伊朗不可变交付包；
- 运行目录位于仓库外：
  `/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-global-state/global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`。

## 二、冻结身份

| 项目 | 结果 |
| --- | --- |
| RIB 相对路径 | `rrc25/2026.02/bview.20260228.0800.gz` |
| RIB 压缩字节 | `426,297,361` |
| RIB SHA-256 | `036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c` |
| mapping version | `41fa4721c1c8f5eb4fe120987eb9672d32382d694889990b93028f4c881f63c4` |
| compatible mapping SHA-256 | `05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5` |
| revised mapping SHA-256 | `0c20c3f522170d0838466ab9fa8da729abf60767fe820038efc73a3f62dd510e` |

关键元数据哈希：

| 文件 | SHA-256 |
| --- | --- |
| `input-summary.json` | `46ba0dbbb5de5cdbfe758d63a34d5f05b383c94ccc3e6076823abaf2a1bdfaa1` |
| `mapping-summary.json` | `a115ee59bab46a58bffc454f268b43467e7c8967a841c9ed90a519f43d9c6790` |
| `rib-quality.json` | `c68a56eefb68f4eb5423fdf290738c7644fcffdaa5b2cf2eb21a6d4e8348b963` |
| `progress.json` | `cdfef80ccdb504f5ef8db252b71ecae20d0dd139cb53214f6737eef54657a07a` |
| `checkpoints/rib/manifest.json` | `5d1788be48516d300c1eef639d0a2e8682c59162e27d334bc25c5870c24838c9` |

## 三、全球初始状态

| 指标 | 结果 |
| --- | ---: |
| RIB physical records | 1,398,399 |
| RIB entries | 55,729,118 |
| 唯一 RouteState 键 | 55,729,118 |
| 重复 RouteState 行 | 0 |
| origin 已知 | 55,720,699 |
| origin 未知 | 8,419 |
| 已映射国家 | 55,715,161 |
| mapping unknown | 5,538 |
| 国家及未知桶 | 241 |
| compact MP_REACH 属性 | 10,256,273 |
| checkpoint 分片 | 64 |
| checkpoint 压缩字节 | 616,271,966 |
| RouteState digest | `44707bc871806044bb3fa5d01654b6fc85e5c2800ac9e101627a0f7f429ce9ae` |

人口守恒结果：

```text
全球固定 Prefix×VP       55,729,118
全部国家与未知桶之和      55,729,118
全球当前 Prefix×VP       55,729,118
全部国家当前人口之和      55,729,118
显式未知桶 Prefix×VP          13,957
状态                         pass
```

## 四、伊朗基线无损对账

| 指标 | 全球状态结果 | 既有伊朗基线 | 结论 |
| --- | ---: | ---: | --- |
| origin ASN | 563 | 563 | 一致 |
| Prefix×VP | 384,767 | 384,767 | 一致 |
| IPv4 Prefix×VP | 383,804 | 383,804 | 一致 |
| IPv6 Prefix×VP | 963 | 963 | 一致 |
| cohort ID | `cohort_go_v1_4ff75dc68f95249de99c11bec48391fb` | 同值 | 一致 |

伊朗 seed 状态为 563 个 origin ASN 全可见，受影响 ASN 为 0；此处仅表示 seed
时点的固定控制面状态，不表示事件结论。

## 五、真实失败与修正

### 1. TABLE_DUMP_V2 compact MP_REACH

首次真实读取在 `MP_REACH AFI=4138` 处失败关闭。复核后确认该字段不是异常 AFI：
按照 [RFC 6396 第 4.3 节](https://datatracker.ietf.org/doc/html/rfc6396#section-4.3)，
TABLE_DUMP_V2 的 MP_REACH 属性只保留 next-hop 长度和地址，AFI、SAFI 和 NLRI
已由 RIB 头部表达。

修正后：

- RIB 按 compact MP_REACH 格式严格校验 next-hop；
- `10,256,273` 项作为 compact MP_REACH 正常计数；
- UPDATE 仍按完整 MP_REACH 格式严格解析，没有放宽 UPDATE 质量门；
- 旧的误分类质量字段已在 checkpoint 重载时原子迁移并移除。

### 2. IPv4-mapped IPv6 前缀族

首次 checkpoint 重载定位到
`::ffff:5c2f:90c0/127`。通用地址解析会将其自动降为 IPv4，导致 `/127` 失效。

修正后：

- prefix 地址严格依据 AFI 构造；
- IPv6 字节形态即使落在 `::ffff/96` 也保持 IPv6；
- RIB checkpoint 和 UPDATE spool 共用相同修正；
- 合成回归同时覆盖 checkpoint 与 spool 往返。

## 六、checkpoint 重载

64 个分片已全部重新读取并逐文件核对压缩字节、SHA-256、记录数和 shard 坐标。
重载结果：

- RouteState 行数仍为 `55,729,118`；
- RouteState digest 仍为
  `44707bc871806044bb3fa5d01654b6fc85e5c2800ac9e101627a0f7f429ce9ae`；
- 全球、国家、未知桶人口守恒仍为 `pass`；
- 伊朗 563 ASN、384,767 Prefix×VP 与 cohort ID 不变；
- 重载日志 SHA-256 为
  `d0b528d11e8ad795a25cbabdf27b686fc9ce4e11ae73b81da27d4a0fbefd4409`。

重载只读取派生 checkpoint；后续 UPDATE 阶段使用独立的 checkpoint 入口，不再
读取原始 RIB。

## 七、质量门

- 本地 `go test ./...`：通过；
- 本地 `go vet ./...`：通过；
- 本地 `go test -race ./...`：通过；
- Linux `go test ./...`：通过；
- Linux `go vet ./...`：通过；
- Linux `go test -race ./...`：通过；
- `backend/core.sha256`：全部通过。

## 八、阶段判定

- GSR-01：冻结 RIB 身份、路径、字节和哈希可复核；
- GSR-02：完整 RRC25 IPv4/IPv6 unicast seed RIB 已形成唯一全球状态；
- GSR-04：国家 mapping、unknown origin 和 mapping unknown 分开计数；
- GSR-05：所有非零国家人口形成固定 cohort，伊朗 cohort 无损；
- GSR-07：全球、国家和未知桶人口守恒；
- GSR-11：RIB checkpoint 可重载并得到同人口、同状态哈希；
- GSR-12：真实解析与重载问题均失败关闭，未发布伪 COMPLETE；
- GSR-13：输入、mapping、质量、checkpoint 和关键文件哈希可审计。

S2 出口已成立，可以进入 S3。S3 只能复用冻结的 84 槽 spool，形成一条有序
状态流；不得启动国家级独立重放或读取 UTC 15:00 之后的数据。

RRC25 全局状态重放最终验收回检：S2 已修正。
