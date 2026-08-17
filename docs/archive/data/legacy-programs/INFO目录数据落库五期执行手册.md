# INFO 目录数据落库五期执行手册

## 一、阶段目标

S5 只在隔离 INFO 候选库内闭合受控激活与回滚边界。阶段通过后应能观察到：

- S0 至 S4 到期门禁均通过，候选内容身份与全部证据一致；
- 激活需要非空授权 ID 和逐字匹配的 `content_id` 确认；
- 数据库 release 只通过 `info.activate_release` 改变活动指针；
- 运行后端按“文件 → 数据库 → 文件 → 数据库”完成原子切换；
- 切换前已经开始的数据库快照继续固定原 `release_sk`，不混入其他内容；
- 文件后端真实完成一次 `BGPInfo` 快照装载，证明回滚材料可用；
- 激活与回滚不修改业务表、不改变原 INFO 来源、不影响生产容器；
- 既有 `.incomplete.` 失败证据在演练前后目录和文件哈希完全一致。

S5 通过不代表生产激活已经获准。证据中的授权范围固定为
`isolated_offline_candidate_only`，并明确记录
`production_activation_authorized=false`。

## 二、输入边界

执行脚本需要：

1. 只读旧 INFO 目录；
2. `network=none` 且带 INFO 离线候选标签的数据库容器；
3. 候选库管理员与数据库名；
4. 未修改的 Domeye-Core `backend/core` 目录及 `core.sha256`；
5. 已通过且校验和完整的 S4 证据目录；
6. 位于迁移工作根内、Git 仓库外的运行后端状态目录；
7. 新建且不可覆盖的 S5 证据目录；
8. 本次隔离验收授权 ID；
9. 与 manifest 完全一致的确认 `content_id`。

脚本拒绝软链接输入、既有 S5 证据目录、空授权、身份不匹配、非隔离容器以及
S4 证据漂移。

## 三、受控边界

数据库 release 从 `validating` 晋级 `ready` 后，只调用既有
`info.activate_release('core', ...)` 建立活动指针。运行后端状态写在仓库外，
每次切换均：

- 持有排他文件锁；
- 校验预期前一后端；
- 先写同目录临时普通文件并 `fsync`；
- 原子替换当前状态；
- 追加一份不可覆盖的代次日志。

发生异常时，控制器尽力把运行后端恢复为文件模式；失败现场由阶段脚本改名为新的
`.incomplete.` 目录，不能覆盖或删除。

## 四、执行

在服务器隔离工作仓库根目录执行：

```bash
./deploy/database/accept-static-info-s5.sh \
  /home/bgpdata/Domeye/backend/info \
  domeye_info_s1_v5_20260725T131422Z \
  postgres \
  bgp_project \
  /home/bgpdata/Domeye-Core/backend \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/evidence/S4 \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/runtime/static-info \
  /home/bgpdata/Domeye-Info-Migration/20260725T131422Z-s1-v5/evidence/S5 \
  info-s5-isolated-20260726 \
  info_v1_400c1e3f74c43cc37088a49b1ad5655f
```

授权 ID 只标识本次隔离候选验收，不是生产发布授权。

## 五、出场检查

S5 证据目录必须包含并通过 `SHA256SUMS`：

- `static-info-manifest.json`；
- `static-info-release-acceptance.json`；
- `stage-gate-S5.json`；
- `SHA256SUMS`。

其中至少满足：

- `status=pass`；
- `activation_authorized=true`、`activated=true`；
- `safe_boundary_observed=true`；
- `mixed_content_run_count=0`；
- `rollback_tested=true`；
- `previous_release_available=true`；
- `failure_evidence_preserved=true`；
- `business_data_unchanged=true`；
- `production_side_effect_count=0`；
- S5 Hook 为 `status=pass`、`deviation_count=0`。

只有以上结果全部成立，才允许进入 S6 运行时收口。
