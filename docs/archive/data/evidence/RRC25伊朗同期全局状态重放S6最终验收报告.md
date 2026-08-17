# RRC25 伊朗同期全局状态重放 S6 最终验收报告

日期：2026-07-26  
阶段：S6「最终重现与整体效果验收」  
运行身份：`global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`  
数据集身份：`global_dataset_v1_d015e120c2d02d39596af86ea8f8fb7c`  
revision：`global_replay_r2`  
最终结论：通过

## 一、最终效果

本次已将伊朗同期 RRC25 重放从“只保留伊朗状态”闭合为一份共享全球
RouteState：

```text
1 个冻结 RIB
→ 25 个 catch-up UPDATE
→ 59 个正式 UPDATE
→ 北京时间 18:05–23:00 的 60 点全球状态
→ 241 个国家及显式未知桶的同合同国家投影
→ 23:00 完整 RouteState checkpoint
→ 不读 RIB、不重施前 84 槽，追加一个 23:00–23:05 UPDATE
→ 241 个国家统一扩展为 61 点
→ 伊朗和非伊朗事件引用通过同一 API 与同一页面读取
```

最终结果只表示 RRC25 单 collector 的 BGP 控制面状态，不表示全球互联网真实
可达性、国家数据面连通性、用户体验或服务影响。

## 二、冻结输入与运行身份

### 2.1 RIB

| 项目 | 结果 |
| --- | --- |
| 路径 | `rrc25/2026.02/bview.20260228.0800.gz` |
| 时间 | `2026-02-28T08:00:00Z` |
| 压缩字节 | 426,297,361 |
| SHA-256 | `036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c` |
| physical record | 1,398,399 |
| RIB entry | 55,729,118 |

### 2.2 冻结 UPDATE

| 项目 | 结果 |
| --- | ---: |
| catch-up | 25 |
| 正式窗口 | 59 |
| 合计 | 84 |
| physical record | 15,488,874 |
| RouteEvent | 42,682,699 |
| ANNOUNCE | 38,967,304 |
| WITHDRAW | 3,715,395 |
| spool manifest SHA-256 | `f0719a6168f64b2f4783747920f0b73c4504474cee692427ae3ef292ba0ef08b` |

84 个 UPDATE 只形成一条按槽、record ordinal 和 element ordinal 排序的状态流；
所有国家投影均由同一状态应用过程生成。

### 2.3 mapping

| 项目 | 结果 |
| --- | --- |
| mapping version | `41fa4721c1c8f5eb4fe120987eb9672d32382d694889990b93028f4c881f63c4` |
| compatible mapping SHA-256 | `05b9809116c3525769e8dc2bd52497ff810a5b4d063cf3c93442d23ed119f9d5` |
| revised mapping SHA-256 | `0c20c3f522170d0838466ab9fa8da729abf60767fe820038efc73a3f62dd510e` |

国家只表示 origin ASN 的 mapping 结果。AS_PATH 中间节点不参与国家落位；无法
唯一解析 origin 或国家的路由进入显式未知人口。

## 三、全球状态与国家人口

### 3.1 seed RIB

| 项目 | 结果 |
| --- | ---: |
| 唯一 RouteState | 55,729,118 |
| origin 已知 | 55,720,699 |
| origin 未知 | 8,419 |
| 已映射国家 | 55,715,161 |
| mapping unknown | 5,538 |
| 国家及未知桶 | 241 |
| RIB checkpoint shard | 64 |
| seed 状态 digest | `44707bc871806044bb3fa5d01654b6fc85e5c2800ac9e101627a0f7f429ce9ae` |

### 3.2 23:00 正式末状态

| 项目 | 结果 |
| --- | ---: |
| 正式状态点 | 60 |
| 每点国家及未知桶 | 241 |
| 国家快照 | 14,460 |
| ASN 状态 | 5,133,600 |
| RouteState 行数 | 55,772,687 |
| 全球固定 Prefix×VP | 55,729,118 |
| 最终可见固定 Prefix×VP | 55,637,676 |
| 最终当前 Prefix×VP | 55,684,594 |
| 状态 digest | `3a8c63ea5c667d6d62d1ff1b1da9b6abfa4f2a927aad9cb9e70f160935d2ba00` |

60 个状态时间严格从 UTC 10:05 到 15:00，对应北京时间 18:05–23:00，五分钟
间隔、无缺槽、无重复、无窗口外补点。

### 3.3 显式未知桶

| 项目 | 结果 |
| --- | ---: |
| 固定 Prefix×VP | 13,957 |
| IPv4 Prefix×VP | 10,393 |
| IPv6 Prefix×VP | 3,564 |
| 可解析 origin ASN | 239 |
| origin unknown Prefix×VP | 8,419 |

全球固定、可见和当前人口均等于全部国家与未知桶之和；国家内地址族、固定人口、
可见/不可见关系和 ASN 分类逐槽闭合。

## 四、伊朗与非伊朗结果

### 4.1 伊朗不可回退基线

| 项目 | 结果 |
| --- | ---: |
| origin ASN | 563 |
| Prefix×VP | 384,767 |
| IPv4 Prefix×VP | 383,804 |
| IPv6 Prefix×VP | 963 |
| 原基线状态点 | 60 |
| 追加后状态点 | 61 |
| 原基线 ASN 状态 | 33,780 |
| 追加后 ASN 状态 | 34,343 |

前 60 个国家快照和 ASN 状态与既有伊朗不可变交付包逐字段一致。新增第 61 点不
改变固定 cohort 分母。

### 4.2 非伊朗样本

| 国家 | 规模 | origin ASN | Prefix×VP | IPv4 | IPv6 | 61 点 ASN 状态 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| MS | 小 | 1 | 43 | 43 | 0 | 61 |
| KG | 中 | 56 | 19,627 | 19,144 | 483 | 3,416 |
| US | 大 | 18,136 | 14,405,316 | 12,304,563 | 2,100,753 | 1,106,296 |

样本只增加验收覆盖；最终数据实际生成到全部 241 个国家及未知桶。MS 只作为
隔离同期状态验收引用，不构成真实国家中断事件。

## 五、23:00→23:05 接续

新增输入为：

| 项目 | 结果 |
| --- | --- |
| artifact ID | `art_v1_896ce8ba06c29904c0a0cfd0e3bfef30` |
| 路径 | `rrc25/2026.02/updates.20260228.1500.gz` |
| 压缩字节 | 3,911,125 |
| SHA-256 | `768cb34468ef37f7390087b3d679307827ab809fb8228e43f3962a9e692a222f` |

接续结果：

| 项目 | 结果 |
| --- | --- |
| 前一 `data_through` | `2026-02-28T15:00:00Z` |
| 新 `data_through` | `2026-02-28T15:05:00Z` |
| `loaded_rib` | `false` |
| `reapplied_prior_update_count` | 0 |
| 新产品序号 | 86 |
| RouteState 行数 | 55,773,109 |
| 国家及未知桶 | 241 |
| 第 61 点 ASN 状态 | 85,560 |
| 最终国家快照 | 14,701 |
| 最终 ASN 状态 | 5,219,160 |
| 状态 digest | `743b5f03177e0859ede4a83020c29b9e34400ef71b3d68ae7807badec35ab4ff` |
| 修正版产品 SHA-256 | `2532fbfc9c68fa86f7b874c81c8bcc8637246f53b692c9c70384df04cec604e3` |
| 修正版 checkpoint SHA-256 | `a57208ae9054569691c58036ab122791fd491d54130f24bc88ff3c14eb599168` |

这一槽包含 353,763 条 ANNOUNCE、56,125 条 WITHDRAW、131 次国家迁移和
2,179 次替换 ANNOUNCE。所有计数已进入全球及国家活动守恒。

## 六、S6 发现的确定性偏离与修正

### 6.1 发现

S6 按用户要求只重复一个 UPDATE 槽，没有重新读取 RIB 或重跑前 84 槽。两次
真实单槽追加得到完全相同的：

- RouteState 行数与状态 digest；
- 全球和 241 个国家活动；
- 85,560 条 ASN 状态；
- 人口守恒和 64 个 checkpoint shard 人口。

但首次产品 SHA 为：

```text
98cb6de2116e417b02b2ab621da4cb7a9a10eca1f11ea8facdddd704d83411e9
```

独立重复产品 SHA 为：

```text
b876cdfa156f153e3e8b667f4ab3832be947d0ead7dbcd9afa9a3e2e77b22ab0
```

产品内容唯一差异是 append spool manifest 的墙钟 `created_at`。同时，旧
checkpoint writer 直接遍历 Go map，64 个 shard 的记录数完全相同，但 64 个
压缩哈希均因记录顺序不同而变化。该结果按 GSR-03、GSR-11 判为偏离，没有作为
最终包发布。

### 6.2 修正

- append spool manifest 升级为 v2，移除墙钟身份，使用确定性的
  `data_through`；
- continuation checkpoint 升级为 v2，以 `identity_time=data_through`
  形成确定性 manifest；
- checkpoint 写出前按 shard 收集 RouteKey，并在 shard 内按
  VP、peer ASN、AFI 和前缀确定性排序；
- loader 继续兼容既有 v1 checkpoint，但新的写出只使用 v2；
- 新增同一 RouteState 两次写出得到相同 manifest 和 shard 哈希的回归测试；
- 两次旧输出保持原样，用于保留偏离证据。

修正版真实输出与两次旧输出的状态、活动、国家和 ASN 语义完全一致。独立验证器
重新核验三次实际产品、三组 checkpoint、全部 shard 文件和修正版哈希，状态为
`pass`。没有为消除哈希差异修改路由状态或国家数值。

## 七、解析与失败关闭

实施期间真实触发并保留了以下失败：

1. TABLE_DUMP_V2 compact MP_REACH 被误读为完整 UPDATE 属性；
2. IPv4-mapped IPv6 前缀在 checkpoint 往返时被错误降为 IPv4；
3. `__UNKNOWN__` 中 origin unknown 路由的地址族投影遗漏；
4. UTC 15:00 UPDATE 中一个畸形 OTC Extended Length 属性；
5. S6 增量产品与 checkpoint 的非确定性写出身份。

每次均在 COMPLETE 或 `data_through` 推进前失败关闭，失败目录和原因保留。
畸形 OTC 只对严格匹配的 4 字节末尾 OTC 形态执行 Treat-as-withdraw；其他越界
属性继续拒绝。该槽计数为：

| 项目 | 结果 |
| --- | ---: |
| 畸形 OTC | 1 |
| Treat-as-withdraw RouteEvent | 1 |

未知、缺失、解析失败、unsupported、mapping unknown 和 source unavailable
没有被改写为零、正常或恢复。

## 八、通用 API 与页面

隔离候选：

```text
前端：http://10.99.8.16:38679
后端：http://127.0.0.1:28479
```

伊朗真实引用：

```text
country_outage/2026-02-27 09:12:32/IR/1/r
```

MS 隔离引用：

```text
country_outage/2026-02-28 18:05:00/MS/1/rrc25
```

两者均通过同一 `/api/v2/events/resolve`、overview、series、ASN 和 audit
合同以及同一 `CountryOutageDashboard`。最终 API 验收结果：

| 项目 | IR | MS |
| --- | ---: | ---: |
| revision | 2 | 2 |
| data mode | `mixed` | `mixed` |
| `data_through` | 23:05 | 23:05 |
| 状态点 | 61 | 61 |
| 国家 UPDATE 点 | 61 | 61 |
| origin ASN | 563 | 1 |
| baseline 固定读取 | 60 | 60 |

页面实际可见：

- 国家、collector、窗口、cohort、revision 和截止点均来自事件及数据合同；
- 伊朗显示“伊朗 BGP 路由观测”，MS 显示“蒙特塞拉特同期状态验收”；
- 国家 UPDATE 图使用本国 origin 归属，另可切换 RRC25 全量；
- 国家资源能力不可用时对应区块隐藏，不补零；
- MS 不显示真实中断、恢复、原因、前兆或服务影响结论；
- 两页均为 61/61、最后观测 23:05、`mixed`；
- 浏览器页面错误检查为空。

本次 frontend-design 技能用于保持现有工业数据观测层级和诚实降级语义，没有
重新设计伊朗页面，也没有增加国家专用组件。

## 九、GSR-01 至 GSR-14 最终判定

| GSR | 判定 | 最终证据 |
| --- | --- | --- |
| GSR-01 | 通过 | 冻结 RIB、84 个 UPDATE、追加 1 个 UPDATE 均有路径、字节、时间和 SHA；未越过授权范围 |
| GSR-02 | 通过 | 55,729,118 个 seed RouteState，无国家预筛，241 个国家及未知桶 |
| GSR-03 | 已修正后通过 | 84 槽只应用一次；单槽重复状态一致；墙钟和 map 顺序哈希偏离已修正 |
| GSR-04 | 通过 | origin mapping 版本化；origin unknown、mapping unknown 和未知桶分别计数 |
| GSR-05 | 通过 | 全部国家固定 cohort 可审计，动态 UPDATE 不改变分母 |
| GSR-06 | 通过 | 全部国家共享 60 点正式时间轴，追加后共享第 61 点 |
| GSR-07 | 已修正后通过 | 全球、国家、未知桶、地址族、ASN 和 UPDATE 活动逐槽守恒 |
| GSR-08 | 通过 | 替换、跨国迁移和撤回使用上一状态，重复和无状态撤回分别计数 |
| GSR-09 | 通过 | 伊朗 563、384,767、383,804、963 与前 60 点逐字段一致 |
| GSR-10 | 通过 | 241 个同合同国家包；MS、KG、US 大中小样本；MS 未伪造业务事件 |
| GSR-11 | 已修正后通过 | RIB/catch-up/formal/continuation checkpoint 可恢复；独立重复状态一致；v2 确定性写出 |
| GSR-12 | 通过 | 五类真实失败均失败关闭，`data_through` 只在完整产品后推进 |
| GSR-13 | 通过 | run、dataset、revision、mapping、输入、质量、产品、checkpoint 和 COMPLETE 哈希可审计 |
| GSR-14 | 通过 | 同一 API/页面读取 IR 与 MS；23:00 checkpoint 不读 RIB 接续到 23:05；241 国统一新增点 |

## 十、最终测试

| 检查 | 结果 |
| --- | --- |
| Go `test ./...` | 通过 |
| Go `vet ./...` | 通过 |
| Go `test -race ./...` | 通过 |
| 后端国家中断、publication、v2、叙事和 OpenAPI 合同测试 | 24 passed |
| 前端测试 | 41 passed |
| 前端生产构建 | 通过 |
| OpenAPI 生成类型对账 | 一致 |
| `backend/core.sha256` | 全部通过 |
| `git diff --check` | 通过 |
| 241 国最终包独立校验 | pass |
| 修正版候选 API 验收 | pass |
| IR、MS 浏览器验收 | pass |

最终 Linux 重放二进制 SHA-256：

```text
bf170cdc4042d3412ca3627e969105b8f4952f851b0d197e3e078fab57b8da73
```

## 十一、关键最终证据

| 证据 | SHA-256 |
| --- | --- |
| `append-summary.json` | `2619953e8dc75eb3ce0c099f1ec56f8f0da760d7cd4be7b7919f9b4fa5301493` |
| `append-repeat-verification-final.json` | `81bca322e6dbab8535ec8cf78958f1cb50835fb04d1fd2b75ba2cbb6323d6aa0` |
| `country-packages-verification.json` | `17124fcbdd9883864d2c386d6c98f335da0d16537ddbb4b687d07078d0fabbd2` |
| 最终 `country-packages/catalog.json` | `737abe8b56335a2608b8bd7036acd8f8dff961c51c8c3fd49752f5226f3a343b` |
| 最终 `country-packages/COMPLETE.json` | `c859b7799185bb89fc0d3f59fb05acd73f4f3fc17a5b905cd77798d65053affc` |
| 修正版隔离 registry | `ac7dd4acf72a3be1310705db8a9437a4abff6dc48fc77763a52aff8ed9b2a6f2` |
| 修正版 API 验收 | `7b8a49164d47fe8ccf0c8579653c93f0370315eb9a82aa8be25fb1c5238bf76b` |
| 伊朗最终页面截图 | `8eef42a392e78c39948ff615394f92beb4c14c210c553b36adfb1b4b3576c82c` |
| MS 最终页面截图 | `1f826574bb795a3a061a4d4fef49cbf361bc41254cd1c53336f57f775424ef38` |

## 十二、边界与非目标

- 没有修改 `backend/core/`、旧 Detection 或旧业务数据库；
- 没有读取其他 collector、其他日期或 1,928 个 UPDATE；
- 没有为每个国家单独读取 RIB 或重放 UPDATE；
- 没有覆盖既有伊朗不可变交付包；
- 没有切换生产，也没有声称生产实时已经上线；
- `mixed` 表示历史重放结果接续一批离线 UPDATE，不表示实时采集；
- 没有把控制面状态解释为因果、数据面、流量、服务影响或政治结论。

隔离候选继续保留用于人工复核。生产实时接入、持续消费新 UPDATE、迟到槽处理和
正式发布仍需独立授权与验收。

RRC25 全局状态重放最终验收回检：S6 已修正。
