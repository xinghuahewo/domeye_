# RRC25 伊朗事件数据库先行复算执行记录

## 1. 结论

已按“数据库作为研究主体、原始数据只补证据缺口”的顺序完成伊朗事件第一阶段。
本阶段没有读取 MRT、没有运行 seed、没有回放 UPDATE，也没有写数据库。

固定 Incident：

```text
country_outage/2026-02-27 09:12:32/IR/1/r
```

代码提交：

```text
2fdad22b54a70c0eb6c28f2c009ffbef8ce3a92b
```

服务器正式制品目录：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/db-first-final-v1
```

第二次只读复现目录：

```text
/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/db-first-final-v1-repro
```

两次导出的稳定研究内容指纹相同：

```text
28df37e1a58c4f8503c37d6e1e5adf8a201e63cb684e301b69682a4b0b2d2846
```

JSON 文件字节哈希不同是预期结果，因为运行时间和只读事务观测时间属于执行收据；
中文摘要字节哈希相同，稳定内容指纹明确排除了这些运行时字段。

## 2. 数据库安全边界

| 项目 | 结果 |
| --- | --- |
| 数据库角色 | `domeye_core_reader` |
| 数据库 | `bgp_project` |
| 系统标识 | `7663836852697006116` |
| 事务 | `REPEATABLE READ READ ONLY` |
| 事务结束 | `rollback_completed` |
| 数据库写入 | `false` |
| raw 读取 | `false` |
| `backend/core` 调用 | `false` |

所有业务查询都在同一个只读快照中完成；发布使用 create-only 目录，已存在目录会失败
关闭。

## 3. 已冻结的数据事实

### 3.1 时间与风险

| 字段 | 数据库结果 |
| --- | --- |
| 首次旧检测时间 | `2026-02-27 09:12:32+08:00` |
| 旧摘要更新时间 | `2026-02-28 22:34:40+08:00` |
| 两者差值 | `134,528` 秒 |
| 类型 | `country_outage` |
| 风险 | `high` |
| 国家事件持续时间 | `unknown`，旧记录没有结束时间 |

这两个时间锚不得合并为一个事件时间，也不能由时间先后推出前兆因果。

### 3.2 国家五分钟序列

研究半开窗口为：

```text
[2026-02-28 00:00:00+08:00, 2026-03-06 16:40:00+08:00)
```

- 预期和观测均为 `1,928` 个五分钟槽；
- 缺槽、越界槽和五项指标 NULL 均为 `0`；
- WITHDRAW 有 1 个真实零值，其余五项观测均为非零；
- IPv4 `/24`、IPv4 地址等值和 IPv6 `/48` 分开保存，禁止跨地址族相加。

固定六小时基线 `[00:00, 06:00)` 的 IPv4 地址等值中位数为 `10,146,432`。

| 数据库观测 | 结果 |
| --- | --- |
| `17:00–19:00` ANNOUNCE | `4,354,758` |
| `17:00–19:00` WITHDRAW | `210,811` |
| `06:40` IPv4 降幅 | `0.675%` |
| `18:45` IPv4 降幅 | `3.667%` |
| `22:30` IPv4 降幅 | `5.691%` |
| `16:15` IPv4 基线占比 | `99.956%` |
| 首个连续六槽达到双栈基线 99% 的候选 | `2026-03-01 22:00+08:00` |
| 半开窗口最后槽 `03-06 16:35` | 基线的 `99.010%` |

恢复时间只是国家聚合指标候选，不代表逐 VP、全网、流量或业务完全恢复。

### 3.3 影响集合

在旧摘要锚点 `2026-02-28 22:34:40+08:00`：

- 国家事实保存 `176/556`；
- 活跃 AS 中断事实为 176 行、176 个唯一 ASN；
- 两个 ASN 集合完全一致，双向差集均为空；
- 锚点活跃 AS 事实中，生命周期历史峰值比例为 1 的有 97 个，介于 0 与 1 的有
  79 个；
- 活跃 Prefix 中断事实为 2,883 行，去重后为 2,842 个
  `distinct(prefix, ASN)`，涉及 218 个 ASN。

`97/79` 是旧事实生命周期历史峰值分类，不是锚点当刻的完全/部分中断比例。

三个关键五分钟桶：

| 桶 | AS 事实/唯一 ASN | Prefix 事实/唯一 Prefix/唯一 ASN |
| --- | ---: | ---: |
| `06:35` | `0/0` | `63/63/3` |
| `18:45` | `99/99` | `1,015/1,015/126` |
| `22:30` | `2/2` | `67/67/2` |

## 4. 数据库仍不能回答的字段

- 稳定 collector、VP、peer IP 和同快照观测人口；
- 支持结论的具体 MRT record、element、文件哈希和 raw 引用；
- 国家级异常前、中、后的完整逐 VP 路由状态；
- 精确传播范围和逐前缀恢复过程；
- 前兆是否导致后续中断；
- 物理断线、流量影响、主动行为或政府意图。

旧 AS/Prefix 事实存在 `pre/eve` AS_PATH 快照，但没有稳定 VP 和 raw 坐标，
`next` 恢复快照覆盖也不足，因此只能作为 legacy 路径旁证。

## 5. 最小 raw 请求

数据库已确定四个代表实体及配对前缀：

| ASN | Prefix | 选择理由 |
| --- | --- | --- |
| `AS48715` | `78.110.120.0/22` | 历史峰值 `76/76` |
| `AS42337` | `2.188.40.0/24` | 大型部分候选，历史峰值 `468/761` |
| `AS39501` | `85.204.30.0/23` | 末波接近完全候选，历史峰值 `72/73` |
| `AS61008` | `2a05:a380::/29` | IPv6 候选 |

初始请求严格限制为三个窗口、13 个 UPDATE 槽：

| 包 | UTC 半开窗口 | UPDATE 槽 |
| --- | --- | ---: |
| `precursor_candidate` | `[2026-02-27T22:30Z, 22:45Z)` | 3 |
| `main_wave_1` | `[2026-02-28T10:35Z, 11:00Z)` | 5 |
| `main_wave_2` | `[2026-02-28T14:20Z, 14:45Z)` | 5 |

DB-first 不可变包内的请求状态仍为 `not_executed`，它记录的是数据库导出完成时点，
不随之后的原始证据执行而改写。后续执行结果见 5.2。第一轮不读取 RIB、不建立
seed；若 UPDATE 命中后仍需前后状态，必须再以具体字段缺口说明 RIB 和 catch-up
范围，不能静默扩大为 1,928 槽全窗口回放。

### 5.1 三个精确锚点的 Native 探针

数据库包发布后，已先对三个精确锚点执行单遍解析探针。该探针只验证源文件、解析器、
人口计数和资源边界，不保留 RouteEvent，因此不冒充事件证据。

| UTC 槽 | Artifact | 压缩字节 | A / W |
| --- | --- | ---: | ---: |
| `2026-02-27 22:35` | `art_v1_78e46b8db37f34c166293b45b71a6899` | `3,982,621` | `384,088 / 24,527` |
| `2026-02-28 10:45` | `art_v1_651c399cf44672bcde2b96fe88fd46c7` | `4,522,720` | `421,050 / 33,204` |
| `2026-02-28 14:30` | `art_v1_e235bb9d78e901e71fc7d47dcee6c652` | `4,223,204` | `408,261 / 59,574` |

结果：

- 三文件均 `complete_single_pass`，每个文件只读一次；
- 实际压缩读取为 `12,728,545` 字节；
- 耗时 `45.3963` 秒；
- RIB、seed、状态回放和数据库写入均为 `0`；
- outcome：
  `probe-ledger/outcomes/outcome-000002-probe_v1_1af9140e566405dd9a19eb78cddfa367.json`；
- outcome SHA256：
  `239608799b7457e63857ce3e432709a4a5c2c8cdff752f806cd8f1881e311dea`；
- 收据指纹：
  `3c0d1561476d6681c2f66c836225934ed3ef4f1130c8584c05734ca5f9bf14af`。

该探针之后已按相同边界执行 13 槽定向消息证据提取，见下一节。

### 5.2 十三槽定向消息证据

执行入口：

`dev/data_quality/rrc25_iran_targeted_raw.py`

正式目录：

`/home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/targeted-raw-final-v1`

执行 Git commit 为
`29a270b83d92ccad4bc53fabb9a852eadf594f18`，plan ID 为
`traw_v1_9f4412297b7327c65bf3a5167c3f797e`，执行器源码 SHA256 为
`7cbf6e979bf058d26ef0f17138572eb0257f1a50908d9a30b2486e5a498e3ac4`。

执行保持四个数据库选定前缀、三个半开窗口和 13 个 UPDATE 槽不变。第一次执行在
第 9 个文件内触发 540 秒软停，未发布半成品；随后只把软停放宽为 1,800 秒，
压缩字节硬限、实体集合和槽集合均未改变。最终结果：

| 项目 | 结果 |
| --- | ---: |
| UPDATE artifact | `13/13`，全部 `complete_single_pass` |
| 压缩读取 | `56,393,248` 字节 |
| 实际耗时 | `786.388` 秒 |
| MRT physical record | `2,161,426` |
| 前缀过滤前 route element | `5,874,362` |
| 保留 RouteEvent/raw ref | `1,923 / 1,923` |
| ANNOUNCE/WITHDRAW | `1,678 / 245` |
| 唯一 VP | `89` |
| 数据库连接/写入 | `0 / 0` |
| RIB/seed/状态回放 | `0 / false / false` |

RouteEvent 与 raw ref 的稳定 ID、artifact、文件 SHA、record ordinal 和 element
ordinal 已逐条一一闭合，1,923 个 RouteEvent ID 与 1,923 个 raw ref ID 均唯一。

| ASN / Prefix | 观测数 | A / W | ANNOUNCE origin 结果 |
| --- | ---: | ---: | --- |
| `AS48715 / 78.110.120.0/22` | `0` | `0 / 0` | 固定窗口无报文观测 |
| `AS42337 / 2.188.40.0/24` | `3` | `2 / 1` | 2 条均匹配目标 origin |
| `AS39501 / 85.204.30.0/23` | `146` | `93 / 53` | 93 条均匹配目标 origin |
| `AS61008 / 2a05:a380::/29` | `1,774` | `1,583 / 191` | 1,583 条均匹配目标 origin |

WITHDRAW 没有 AS_PATH，因此 origin 状态固定为 `not_applicable`。AS48715 的零命中
只表示“固定 13 槽内没有该前缀的 UPDATE 报文”，不等于不可见、恢复、数据缺失，
也未据此自动扩窗。其余三组结果同样只是消息级观测，不能替代 RIB 状态或证明前兆
因果、完整传播范围和恢复过程。

定向包文件哈希：

| 文件 | SHA256 |
| --- | --- |
| `route-events.jsonl.gz` | `a61f6121319c491e08bd98e274402102ab3919aeef85c17937967e1682567532` |
| `raw-record-refs.jsonl.gz` | `05d434cca0ab51ba0cdd9acb484711fb2701f37b03b46f1ae5bf54cec9b0ba15` |
| `parser-stats.json` | `a681f9a299a1e4094437cbd0410cd9cd82ae618f16c3515278feba01aab329c5` |
| `MANIFEST.json` | `0c33098c8077249de367bf434cb764b91df1f5a716ff9c55a0d146faf770ca7b` |
| `SHA256SUMS` | `12076b75c7882c96561074d2a6e9591e84026a1fbc38314ecd5778fc93d76fde` |

包内四个数据文件已通过 `sha256sum -c SHA256SUMS`，两个 gzip 文件也通过完整性
检查；五个文件权限均为只读 `0440`。

复现时必须使用新输出目录，不能覆盖正式包：

```bash
cd /home/bgpdata/Domeye-Core-dev-data/research-worktrees/iran-rrc25-full-p0-code
/home/bgpdata/Domeye-Core-dev-data/api/.venv/bin/python3 \
  dev/data_quality/rrc25_iran_targeted_raw.py run \
  --db-first-json /home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/db-first-final-v1/iran-db-first.json \
  --prepared-directory /home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/prepared-final \
  --raw-root /home/bgpdata/data/ripe \
  --output-directory /home/bgpdata/Domeye-Core-dev-data/research-runs/iran-rrc25-full-p0/20260723T094940Z-full-p0/targeted-raw-repro-v1
```

## 6. 制品哈希

正式目录：

| 文件 | SHA256 |
| --- | --- |
| `iran-db-first.json` | `e27c80192092118bac78266f799bf9bcad67ef2e484f4761c36be2976d63d6a4` |
| `伊朗数据库先行复算摘要.md` | `51a2675be24bac2904d48facf9d363a4d007359de128083637860107c979278c` |
| `SHA256SUMS` | `a762c3b12cd25073d766dfd3eb3d6c67f91fe81505233a9600f83a4063a31815` |

复现目录：

| 文件 | SHA256 |
| --- | --- |
| `iran-db-first.json` | `febebf9614643d5bff0be65b87a3bba88ecd28eae6e4dfe1ee199fd90c1a89e3` |
| `伊朗数据库先行复算摘要.md` | `51a2675be24bac2904d48facf9d363a4d007359de128083637860107c979278c` |
| `SHA256SUMS` | `b0a8f0bfec18073f168aba985d42ebdda6904b94348c2eb0ff1119575c90dec1` |
