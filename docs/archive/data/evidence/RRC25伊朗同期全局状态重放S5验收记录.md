# RRC25 伊朗同期全局状态重放 S5 验收记录

日期：2026-07-26  
阶段：S5「通用 API、页面与增量接续效果闭合」  
运行身份：`global_run_v1_6427ae1ad52037c83d7ec3bb9b5e7757`  
数据集身份：`global_dataset_v1_d015e120c2d02d39596af86ea8f8fb7c`  
revision：`global_replay_r2`  
结论：已修正后通过

## 一、阶段入口与执行边界

S5 从 S4 已闭合的 241 个国家及显式未知桶进入：

- 既有北京时间 18:05–23:00 的 60 点国家包保持不可变；
- 伊朗既有 563 个 origin ASN、384,767 个 Prefix×VP 和 60 点基线不修改；
- 新增处理只接续 UTC 15:00 的一个五分钟 UPDATE 文件；
- 接续过程只从 23:00 完整 RouteState checkpoint 恢复，不读取原始 RIB，
  不重新应用前 84 个 UPDATE；
- 伊朗真实事件与非伊朗隔离验收引用共用同一事件解析、overview、series、ASN、
  audit 和页面组件；
- 非伊朗引用只表示同期状态验收，不写入真实事件列表；
- 候选服务只运行在隔离端口，不切换生产；
- 未修改 `backend/core/`、旧 Detection、旧业务数据库或既有伊朗交付包。

隔离候选地址为：

```text
前端：http://10.99.8.16:38679
后端：http://127.0.0.1:28479
```

这两个端口不属于生产服务。

## 二、23:00 可续接完整 checkpoint

S3、S4 使用的查询 checkpoint 足以恢复阶段进度，但不能独立恢复全部可变路由。
S5 因此一次性从已冻结的 RIB checkpoint 和 84 个派生 spool 槽物化出 23:00
完整 RouteState checkpoint。该物化没有重读原始 MRT，结果仍绑定同一 run、
dataset、revision、mapping 和正式状态 digest。

| 项目 | 结果 |
| --- | --- |
| checkpoint 路径 | `checkpoints/continuation/formal-1500` |
| RouteState 行数 | 55,772,687 |
| 国家及未知桶 | 241 |
| 状态 digest | `3a8c63ea5c667d6d62d1ff1b1da9b6abfa4f2a927aad9cb9e70f160935d2ba00` |
| manifest SHA-256 | `ae04f501e0b11740c8926a19298be0c68eb0fe5ca290e7ed9833e9c4549d4f3d` |
| 处理游标 | UTC `2026-02-28T15:00:00Z` |

checkpoint 由 64 个压缩 shard 保存完整路由键、上一状态归属、路径、地址族、
状态计数、输入游标和前序哈希。加载器会重新计算状态 digest 与人口守恒，并拒绝
损坏、错配或跨运行 checkpoint。

## 三、一次失败关闭与精确修正

第一次追加保留在：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/rrc25-global-state/
global-append-20260228T1500-r2
```

它在 UPDATE physical record 92,814 处失败：

```text
record 92814: path attribute 35 out of bounds
```

失败发生在生成新产品和推进 `data_through` 之前，既有完整截止点仍为 23:00。
原始字节显示该记录的 OTC 属性类型为 35，声明 Extended Length，但发送端实际仍
以单字节长度编码 4 字节 OTC 值。根据 RFC 9234 第 5 节，畸形 OTC 应按
Treat-as-withdraw 处理。

修正只接受同时满足以下条件的末尾 OTC 形态：

- 属性类型严格为 35；
- 可选、传递及 Extended Length 标志与该异常记录一致；
- 实际 OTC 值严格为 4 字节；
- 属性位于路径属性末尾；
- 其余越界属性继续失败关闭。

修正后单文件探针得到：

| 项目 | 结果 |
| --- | ---: |
| physical record | 145,110 |
| RouteEvent | 409,888 |
| ANNOUNCE | 353,763 |
| WITHDRAW | 56,125 |
| origin unknown | 35 |
| 畸形 OTC | 1 |
| Treat-as-withdraw | 1 |

失败目录与失败原因被保留，没有覆盖为成功结果。

## 四、23:00→23:05 增量接续结果

成功追加只消费：

```text
/home/bgpdata/data/ripe/rrc25/2026.02/updates.20260228.1500.gz
```

输入身份为：

| 项目 | 结果 |
| --- | --- |
| artifact ID | `art_v1_896ce8ba06c29904c0a0cfd0e3bfef30` |
| 文件大小 | 3,911,125 字节 |
| SHA-256 | `768cb34468ef37f7390087b3d679307827ab809fb8228e43f3962a9e692a222f` |
| 前一截止点 | `2026-02-28T15:00:00Z` |
| 新截止点 | `2026-02-28T15:05:00Z` |
| 产品序号 | 86 |

接续结果为：

| 项目 | 结果 |
| --- | ---: |
| `loaded_rib` | `false` |
| `reapplied_prior_update_count` | 0 |
| RouteState 行数 | 55,773,109 |
| 国家及未知桶 | 241 |
| 国家内 ASN 状态 | 85,560 |
| 新状态 digest | `743b5f03177e0859ede4a83020c29b9e34400ef71b3d68ae7807badec35ab4ff` |
| 追加产品 SHA-256 | `98cb6de2116e417b02b2ab621da4cb7a9a10eca1f11ea8facdddd704d83411e9` |
| 下一代 checkpoint SHA-256 | `0e380fce376e84262313d1f5c277f088de8562fc4877dae3943c82ddb9fa302d` |
| 人口守恒 | pass |

全球 UPDATE 为 353,763 条 ANNOUNCE、56,125 条 WITHDRAW；国家归属 UPDATE、
跨国家迁移、替换、重复、无状态撤回和未知归属均进入追加产品质量计数。

## 五、全部国家扩展为 61 点

S5 没有为伊朗或验收样本建立专用追加流程。单一追加产品一次投影到全部 241 个
国家及显式未知桶：

| 项目 | 结果 |
| --- | ---: |
| 国家及未知桶 | 241 |
| 每个国家状态点 | 61 |
| 国家快照总数 | 14,701 |
| ASN 状态总数 | 5,219,160 |
| 统一 `data_through` | `2026-02-28T15:05:00Z` |
| 前 60 点正式 manifest | `48e5e5e08f47469a16466490397b2d7562d8214ed03fda1cc937e25139ee88c5` |
| 追加产品 | `98cb6de2116e417b02b2ab621da4cb7a9a10eca1f11ea8facdddd704d83411e9` |

独立验证器逐包重读 241 个交付包后，以下检查全部为 `pass`：

- 全部交付文件与 `COMPLETE` 哈希；
- 61 点共享时间轴；
- 国家、IPv4/IPv6、ASN 分类和显式未知人口守恒；
- 国家 UPDATE 活动之和与全球活动守恒；
- 国家包与共享全球产品投影同一性；
- 伊朗前 60 点与不可变基线逐字段一致；
- 接续未读取 RIB，也未重新应用前 84 个 UPDATE。

伊朗扩展为 61 点后仍保持 563 个 origin ASN 和 384,767 个固定 Prefix×VP。
MS、KG、US 分别扩展为 61、3,416 和 1,106,296 条 ASN 状态，固定 cohort
分母没有随新 UPDATE 改变。

## 六、通用 API 验收

隔离 registry 对每个验收引用发布两个可固定读取的 publication：

- baseline：revision 2、`data_mode=replay`、60 点、截止 23:00；
- append：同一 revision 2、`data_mode=mixed`、61 点、截止 23:05，并显式
  `supersedes` baseline。

这次是正常增量追加，不是历史事实纠正，因此不提升 revision。`mixed` 只表示
历史重放结果上接续了新一批离线 UPDATE，不表示生产实时。

API 验收结果：

| 项目 | 伊朗真实事件 | MS 隔离状态验收 |
| --- | --- | --- |
| 页面国家 | IR | MS |
| 事件解析 | 同一 `/api/v2/events/resolve` | 同一 `/api/v2/events/resolve` |
| revision | 2 | 2 |
| data mode | `mixed` | `mixed` |
| `data_through` | `2026-02-28T15:05:00Z` | `2026-02-28T15:05:00Z` |
| series 点数 | 61 | 61 |
| 国家 UPDATE 点数 | 61 | 61 |
| origin ASN | 563 | 1 |
| Prefix×VP | 384,767 | 43 |
| 固定 baseline 点数 | 60 | 60 |

overview、series、ASN、audit 四个接口的 publication、revision、状态、
capability、cohort、窗口和截止点身份一致，并各自返回 ETag。伊朗与 MS 的
baseline publication 都可固定读取，且前 60 个 series 点与 61 点 publication
的前 60 点完全相同。

## 七、页面可见效果验收

两个引用均通过现有 `/events/detail?ref=...` 路由进入同一个
`CountryOutageDashboard`：

```text
伊朗：
country_outage/2026-02-27 09:12:32/IR/1/r

MS 隔离验收：
country_outage/2026-02-28 18:05:00/MS/1/rrc25
```

浏览器实际观察到：

- 两页都显示北京时间 18:05–23:05、61/61、REV 2、`mixed` 和 23:05 最后观测；
- 伊朗显示自己的 563 个 origin ASN 与 384,767 个 Prefix×VP；
- MS 显示“蒙特塞拉特同期状态验收”、1 个 origin ASN 与 43 个 Prefix×VP；
- UPDATE 图可以在“本国 origin 归属”和“RRC25 全量”之间切换；
- 本国统计来自共享 RouteState：ANNOUNCE 按新 origin 国家，WITHDRAW 按撤回前
  状态国家归属；
- 国家资源能力不可用时不生成资源图，不以零值替代；
- MS 页面没有被写成真实国家中断事件，也没有恢复、原因、前兆或服务影响结论；
- 两页浏览器控制台和页面错误检查为空；
- 桌面宽度完整截图已保存并核对布局、图表、分页和审计折叠区。

前端只做现有工业数据观测叙事的通用数据接线，没有增加国家代码分支、国家专用
组件或伊朗默认字段。缺失能力继续按合同隐藏或标明不可用。

## 八、关键证据身份

| 证据 | SHA-256 |
| --- | --- |
| `append-summary.json` | `3daac19645b598159fd151554ccbd5063112413d6758e7374707d28e92151f1b` |
| `country-packages-verification.json` | `17124fcbdd9883864d2c386d6c98f335da0d16537ddbb4b687d07078d0fabbd2` |
| 61 点 `country-packages/catalog.json` | `47db7f8e64ba2afa6470219094d39aa30362daeb0ba5ce6eb105b98d3a7dcc21` |
| 隔离 `country-outage-registry.json` | `a2ae59d1524015b7adb7b95ff1e5bc5d9666fbf32f1a2cd45e59ff625ae539da` |
| `api-acceptance.json` | `9e5813c829b5cf8e8d33762c0d9afd0aeff5f5249ffd3049d8bf4c5df21deb97` |
| 伊朗页面截图 | `ee8b7de4e475a31928779cd05725ae458a921682e862bf8a0ca9577c4a06a5c5` |
| MS 页面截图 | `019dc81b48b88e6ecb99d20c18bce02572331a0fb138cff1793ff7e3fb2dd129` |

运行数据与截图均位于仓库外的 research/candidate 目录。候选进程不构成生产发布
或实时链路上线。

## 九、阶段判定

- GSR-10：伊朗与非伊朗样本使用同一事件解析、四类 API 和页面；MS 只作为
  隔离状态验收，不伪造真实事件；
- GSR-13：新增 UPDATE、完整 checkpoint、61 点国家包、registry、API 验收和
  页面截图均具有可核对身份与哈希；
- GSR-14：通用 API 与页面能够读取国家自身的身份、固定 cohort、revision、
  capability 和 `data_through`；23:00 checkpoint 不读 RIB 接续到 23:05，
  241 个国家统一新增第 61 点。

S5 出口已成立，可以进入 S6。S6 仍需完成独立重复接续、整体 GSR-01 至 GSR-14
证据映射、完整测试与最终 Hook；不得把隔离候选写成生产实时已经上线。

RRC25 全局状态重放最终验收回检：S5 已修正。
