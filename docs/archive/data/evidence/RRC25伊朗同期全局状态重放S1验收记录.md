# RRC25 伊朗同期全局状态重放 S1 验收记录

版本：1.0  
核验日期：2026-07-26  
阶段：S1「全球状态、国家落位与 cohort 合同闭合」

## 一、阶段结论

S1 合同与合成验证通过，可以进入 S2 真实全球 RIB 初始状态构建。本阶段没有执行
真实 55,729,118 条 RIB entry 重放，也没有把合成测试结果写成全球数据已经生成。

## 二、已闭合合同

- 同一冻结 compatible mapping 展开全部两位国家代码；
- revised delta 只覆盖事件截止日前的 IR 修订；
- mapping 冲突、空国家和未出现 ASN 进入显式 `__UNKNOWN__` 桶；
- RouteState 同时保存固定 seed 归属和当前归属；
- ANNOUNCE 换源同时更新旧固定 cohort 可见性和新当前国家；
- WITHDRAW 使用状态中保存的上一国家，不从无 origin 报文重新猜测；
- 动态新路由进入当前人口，但不改变固定 cohort；
- 全球固定、可见、当前人口分别与全部国家及未知桶闭合；
- cohort 使用与顺序无关的人口摘要；IR 同时保留既有
  `cohort_go_v1` 身份算法；
- RIB checkpoint 采用确定性 shard、文件哈希、mapping、输入和状态摘要绑定；
- checkpoint 重载后重新核对 RouteState、人口守恒和状态摘要。

## 三、实现范围

新增独立 Go 文件：

```text
tools/rrc25-iran-replay-go/global_mapping.go
tools/rrc25-iran-replay-go/global_state.go
tools/rrc25-iran-replay-go/global_checkpoint.go
tools/rrc25-iran-replay-go/global_rib.go
tools/rrc25-iran-replay-go/global_runner.go
tools/rrc25-iran-replay-go/cmd/rrc25-global-replay/main.go
```

没有修改 `backend/core/`、旧 Detection、旧数据库或既有伊朗交付包。

## 四、验证证据

使用 Go `1.25.11`：

```text
go test ./...        PASS
go vet ./...         PASS
go test -race ./...  PASS
```

覆盖的新增验证包括：

- IR、US 与未知桶的种子人口守恒；
- IR→US 换源后旧 cohort 不可见、新当前国家不重复；
- WITHDRAW 归属于上一状态中的 US；
- 动态 ANNOUNCE/WITHDRAW 不改变固定 cohort；
- 不同 seed 插入顺序得到相同状态与 cohort 摘要；
- 全球引擎的 IR cohort ID 与既有 IR 引擎一致；
- 四 shard RIB checkpoint 写入、哈希读取、重载和人口对账；
- 合成 TABLE_DUMP_V2 同时保留 IR、US 和 mapping unknown。

同时执行：

```text
cd backend && sha256sum -c core.sha256
```

结果全部 `OK`。

## 五、阶段边界

- 上述证据证明状态合同和合成实现成立；
- 尚未证明真实 RRC25 全球 route 数、国家数、未知桶数和资源消耗；
- 尚未证明真实 checkpoint 重载与伊朗 384,767 Prefix×VP 对账；
- S2 必须在隔离运行目录完整读取一次冻结 RIB，生成真实 checkpoint 后再封口。

## 六、Hook 回检

```text
python3 .codex/hooks/rrc25_global_state_replay_review.py --stage S1
```

回检判定：S1 一致；未跨越生产、扩窗、旧核心或事件因果边界。
