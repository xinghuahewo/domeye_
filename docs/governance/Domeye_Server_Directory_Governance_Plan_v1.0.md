# Domeye 服务器目录治理计划 v1.0

## 一、目标、状态与红线

本计划把 Domeye-Core 的服务器源码检出、不可变 release、运行指针、开发数据、日志与
历史制品收敛为可盘点、可隔离、可恢复的目录体系。治理不改变产品功能、RRC25 数据
语义、数据库内容、模型合同或当前生产流量。

截至 2026-08-18：

- 本计划状态为 `Designed`；
- 只读审计工具已在任务分支 `Implemented`，尚未合入 `main`、安装或部署；
- 服务器基线属于带时间戳的 `Observed`，不能替代后续实时复核；
- 本任务没有服务器写入授权，Gate 固定为 `BLOCK_MUTATION`。

以下路径及其进程是绝对保护边界：

- `/home/bgpdata/Domeye/**`；
- `/home/bgpdata/data/**`；
- `/home/bgpdata/AS402425/**`；
- `/home/bgpdata/zhongxin/**`；
- `/home/bgpdata/Domeye-Info-Migration/**`，直到归属和依赖另行冻结。

任何候选路径只要位于保护树内，或被保护树进程、挂载、锁、活动指针引用，就必须失败
关闭。治理不得向旧 Domeye 发送信号、修改配置、移动文件、改变端口或重启进程。

## 二、权威目录与职责

| 目录 | 职责 | 当前治理模式 |
| --- | --- | --- |
| `/home/bgpdata/Domeye-Core` | Domeye-Core 服务器源码检出 | 独立 Gate 后归一到 GitHub `main` |
| `/home/bgpdata/Domeye-Core-runtime` | 活动 release、状态、日志、回滚与运行指针 | 先审计，再隔离 |
| `/home/bgpdata/Domeye-Core-artifacts` | 源码归档和不可变制品 | 先审计，再隔离 |
| `/home/bgpdata/Domeye-Core-data` | 生产数据与数据库配置 | 只读审计，默认永久保护 |
| `/home/bgpdata/Domeye-Core-dev-data` | 开发、研究、Overlay 与复算材料 | 审计后逐批显式批准 |
| `/home/bgpdata/Domeye-Core-governance` | Hook、审批、安装与治理回执 | 只读审计；安装走独立发布 |

GitHub `xinghuahewo/domeye_@main` 是源码权威。服务器 checkout、目录名、Screen、软链接
或 HTTP 200 都不能反向覆盖 `main`，也不能单独证明部署或验证完成。

## 三、阶段计划

### S0：只读资产盘点

执行内容：

1. 读取主机、磁盘、受管根目录和保护根目录元数据；
2. 读取服务器 checkout 的 HEAD、分支、remote 数量和脏文件计数；
3. 校验 Backend、旧 Agent Sidecar、P1 Chat Sidecar 的活动软链接；
4. 统计各 release 根的可见目录、隐藏目录和分配空间；
5. 只读取配置文件名、权限和属主，不读取配置内容；
6. 只读取进程 PID、`comm`、cwd 和 executable，不读取命令行参数或环境变量；
7. 输出 `domeye.server-directory-audit/v1` JSON。

退出条件：清单完整，旧 Domeye 保护进程已识别，审计输出不含秘密，没有服务器写入。

### S1：阻止继续制造无身份目录

所有改动先通过 GitHub PR：

- 版本化只读策略和审计器；
- 部署入口在构建开始、失败和成功时记录临时目录身份；
- 失败构建生成失败回执，不把匿名 `.source-*`、`.frontend-build-*` 当作 release；
- 磁盘阈值固定为 80% warning、85% critical、90% stop-new-builds；
- 生产变更命令必须拒绝脏 checkout、未知主机和保护路径交叉。

本任务只实现审计部分；部署入口的临时目录状态机属于后续独立任务。

### S2：服务器源码 checkout 归一

前置条件：S0/S1 已进入 `main`，并再次证明没有进程、挂载、锁或活动 release 使用
`/home/bgpdata/Domeye-Core` 工作树。

执行顺序：

1. 生成当前脏工作树的 Git 状态、文件清单、时间范围和 SHA-256；
2. 创建只读、可恢复归档，并将摘要写入仓库外治理回执；
3. 把脏 checkout 移入同文件系统 quarantine，观察期内不删除；
4. 不修改服务器 GitHub 账号凭证，使用公开 HTTPS 建立干净 `main` checkout；
5. 读回 `HEAD == origin/main`、分支、remote、clean=true 和治理 Hook 摘要；
6. 任一步失败时恢复原精确路径，不触碰旧 `/home/bgpdata/Domeye`。

实现约束：归一器默认只读预检，`--apply` 才能写入；它固定拒绝保护根、进程 cwd/exe/fd
引用、挂载、Git 锁、跨文件系统移动、活动指针引用源码 checkout、SHA 漂移和带凭证
remote。它先创建可校验 archive，再同文件系统隔离旧 checkout；clone 或读回失败时自动
恢复原精确路径。S2 完成不代表生产发布或业务验证完成。

若无凭证公开 HTTPS 连接经受限时读测确认不可用，才允许本机从冻结 GitHub `main` 生成
完整 Git bundle、记录 bundle SHA-256、经现有 SSH 传入受管制品根并由同一归一器 clone。
新 checkout 的 `origin` 仍固定为公开 GitHub URL；该替代不写入 GitHub 凭证，也不表示
服务器已经具备后续 fetch 能力。bundle 必须随本批 quarantine 长期保留。

### S3：运行身份与凭证治理

- 只治理 Domeye-Core Backend、旧 Agent Sidecar 和 P1 Chat Sidecar；
- 把共享凭证从进程命令行迁入 root-only `0600` EnvFile 或等效受控配置；
- 活动指针、实际进程、release manifest、checksum 和健康检查必须组成身份等式；
- Screen 到 systemd 的迁移另设 Gate，不与目录清理同批执行；
- 旧 Domeye Screen 永久排除。

### S4：Runtime release 与备份治理

每个组件最多保留五套：活动版本、正式回滚版本和最近三个已验证历史版本。以下对象
不受数量上限自动清理：活动、回滚、已接受证据、进程引用、挂载、锁定和 `unknown`。

处置只能按以下状态机进行：

```text
inventory → classified → quarantine_planned → quarantined
          → observed_14_days → delete_authorized → deleted → readback_verified
```

没有精确路径、清单摘要、预计释放空间、恢复路径和单批用户授权时，不得进入
`delete_authorized`。Manifest、checksum、验收记录和正式 tag 长期保留。

### S5：开发数据治理

`Domeye-Core-dev-data` 单独治理：

1. 先识别 `research-runs`、`overlay`、`research-inputs` 的挂载、硬链接和 Candidate 引用；
2. 生产数据、当前研究、复算输入、验收证据和不可重建来源固定保护；
3. 每批只处理一个有清单和摘要的研究运行；
4. 先隔离观察，再取得显式删除授权；
5. 不以目录年龄或大小单独判定可删除。

### S6：持续治理

- 每日只读审计活动链接、脏 checkout、权限、磁盘和匿名目录；
- 每周记录容量变化，不自动删除；
- 80% warning，85% critical，90% 阻止新构建；
- 每次发布 postflight 证明没有新增匿名目录；
- 每月复核五版本保留策略和回滚可用性。

## 四、当前只读执行方式

在仓库根目录执行以下命令，会把版本化策略通过非秘密 base64 环境变量交给远端
Python；审计器从标准输入运行，不在服务器安装或写入文件：

```bash
policy_b64="$(base64 < deploy/governance/server-directory-policy.json | tr -d '\n')"
ssh -o BatchMode=yes -o ConnectTimeout=10 root@10.99.8.16 \
  "DOMEYE_SERVER_GOVERNANCE_POLICY_B64='${policy_b64}' python3 -" \
  < deploy/governance/audit-server-layout.py
```

输出只能作为带时间戳的观察。审计器即使输出 `READ_ONLY_CLEAR`，也不会授权移动、
删除、重启、发布或生产切换。

## 五、批次 Gate

| Gate | 允许进入条件 | 失败动作 |
| --- | --- | --- |
| G0 只读基线 | 策略验证、保护路径、进程引用和活动指针完成 | `BLOCK_MUTATION` |
| G1 仓库准入 | PR、CI、评审、`main` 可达、同 Candidate 证据 | 不安装 |
| G2 checkout 归一 | 无运行引用、归档可恢复、目标 SHA 冻结 | 恢复原路径 |
| G3 凭证迁移 | 配置回读、进程身份、健康和回滚通过 | 恢复旧启动配置 |
| G4 隔离 | 精确对象、无引用、空间与恢复清单、用户授权 | 保持原位 |
| G5 删除 | 隔离满 14 天、无回归、再次显式授权 | 继续隔离 |

## 六、完成标准

服务器治理只有同时满足以下条件才可标记 `Verified`：

- GitHub `main`、服务器干净 checkout 和发布源码身份一致；
- 活动 release、实际进程、配置摘要和健康状态一致；
- 旧 Domeye 路径和进程零写入；
- 无断裂活动链接，无匿名新 release；
- 凭证不出现在进程命令行；
- 每个隔离/删除对象都有清单、摘要、授权、恢复和读回证据；
- 数据和业务行为相对治理前基线不变。

本计划不把“目录变少”当作治理完成；安全边界、身份等式和可恢复性优先于释放空间。
