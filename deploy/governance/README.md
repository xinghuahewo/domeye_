# 治理发布工具

本目录版本化 Domeye Core 的服务器端 Git 保护和只读发布归一检查。它只治理源码、
制品和运行身份，不修改业务、数据库或模型合同，也不会调用 DeepSeek。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `pre-receive` | 保护 `main` 和正式发布时间戳 tag |
| `check-release-normalization.sh` | 核对统一发布证据、组件制品与实际生产身份 |
| `install.sh` | 备份旧版本、原子安装两项服务器治理脚本并生成回执 |
| `server-directory-policy.json` | 固定服务器受管根、保护根、磁盘阈值和零写入策略 |
| `audit-server-layout.py` | 只读盘点目录、Git、活动指针、权限和进程 cwd，不读取秘密 |
| `audit-server-runtime-governance.py` | S3--S6 只读发现运行身份、release/开发数据引用、挂载、锁与硬链接风险 |
| `install-server-directory-audit-schedule.py` | S6：从冻结 `main` 安装、读回或回滚只读审计 systemd timer |
| `normalize-server-checkout.py` | 对指定脏 checkout 执行带归档、自动恢复和身份读回的 S2 归一 |
| `tests/run-fixtures.sh` | 覆盖主干审批、非快进、正式 tag 和归一合同的正反夹具 |
| `tests/check-doc-links.sh` | 检查治理涉及文档的相对链接 |

完整规则见
[主干开发与发布归一治理规范](../../docs/governance/主干开发与发布归一治理规范.md)。

服务器目录的保护边界、阶段 Gate 和隔离/删除授权见
[服务器目录治理计划](../../docs/governance/Domeye_Server_Directory_Governance_Plan_v1.0.md)。

## 服务器目录只读审计

审计器不提供删除、移动、重启、安装或生产切换参数。它只输出
`domeye.server-directory-audit/v1` JSON，默认读取同目录策略。也可以把非秘密策略以
base64 环境变量传给远端标准输入执行，避免在服务器落盘：

```bash
policy_b64="$(base64 < deploy/governance/server-directory-policy.json | tr -d '\n')"
ssh -o BatchMode=yes -o ConnectTimeout=10 root@10.99.8.16 \
  "DOMEYE_SERVER_GOVERNANCE_POLICY_B64='${policy_b64}' python3 -" \
  < deploy/governance/audit-server-layout.py
```

审计只读取配置文件名、权限和属主，不读取配置内容；进程只读取 PID、`comm`、cwd 和
executable，不读取命令行参数或环境变量。任何 finding 或保护进程都会令 Gate 保持
`BLOCK_MUTATION`。

## S3--S6 只读发现

`audit-server-runtime-governance.py` 只能以同一份非秘密策略运行；它不安装定时任务，
不创建隔离目录，也没有迁移、删除、重启、切换或健康接口参数。它读取的范围仅限：

- active link、release/开发数据目录元数据、命名为 manifest 的小文件摘要；
- 进程 `comm`、cwd、executable 和 fd 指向的路径；
- 挂载点、`/proc/locks` 中的活动 inode 锁、锁文件名与硬链接计数。

它不读取配置内容、进程实参或进程环境；因此它会明确把“实参中是否仍有凭证”标为
`not_performed_by_contract`，不能把配置权限合规误报为凭证迁移完成。P1 Chat 当前
没有以 cwd 绑定到活动 release 时，同样只能报告 `not_verified`。

它对每个对象只输出 `inventory` 状态。即使对象暂无进程、挂载、锁或硬链接引用，仍须
另有精确批次清单、空间估算、恢复路径和用户授权才可进入 `quarantine_planned`；删除
还须在隔离观察 14 天后再次单独授权。

文件名以 `.lock` 结尾只作为命名信号，不等于活动锁；只有内核锁表中的 inode 对应到
该对象时才标记 `locked`。内核线程常没有可解析 executable，但其 cwd 和 fd 可完整
检查时不降低进程引用覆盖率；cwd 或 fd 本身不可读时仍失败关闭为 `unknown`。

从最终合入 `main` 的不可变源码目录可按以下只读方式运行，不在服务器落盘：

```bash
policy_b64="$(base64 < deploy/governance/server-directory-policy.json | tr -d '\n')"
ssh -o BatchMode=yes -o ConnectTimeout=10 root@10.99.8.16 \
  "DOMEYE_SERVER_GOVERNANCE_POLICY_B64='${policy_b64}' python3 -" \
  < deploy/governance/audit-server-runtime-governance.py
```

输出 schema 为 `domeye.server-runtime-governance-discovery/v1`。

## S6 定时只读审计

`install-server-directory-audit-schedule.py` 是唯一允许写入 S6 自身治理目录和四个
systemd unit 的安装器。它只接受 `buptserver16` 上干净且与冻结 SHA 一致的
`/home/bgpdata/Domeye-Core` 的 `main`；不会读取配置内容、命令行或环境变量，也不会
迁移 Screen、重启业务服务、切换 release、移动/删除运行或开发数据，更不会修改 GitHub
凭证或旧 `/home/bgpdata/Domeye`。

经独立审批后，从服务器 checkout 的最终 `main` 执行预检和安装：

```bash
cd /home/bgpdata/Domeye-Core
python3 deploy/governance/install-server-directory-audit-schedule.py \
  --operation-id <冻结操作ID> --expected-main <40位main提交SHA>
sudo python3 deploy/governance/install-server-directory-audit-schedule.py \
  --operation-id <冻结操作ID> --expected-main <40位main提交SHA> --apply
```

安装器会在 `/home/bgpdata/Domeye-Core-governance/directory-audit/releases/` 保留版本化
审计来源，将 `current` 原子指向本次来源，安装一个 service template 和 daily/weekly/monthly
三个 timer，并以 `enable --now` 后的 `is-enabled`、`is-active` 和下一次触发时间读回为成功
条件。报告仅写入 root-only 的 `directory-audit/reports/<频率>/`：每日执行目录布局审计，
每周和每月执行运行身份/保留策略发现；所有报告仍是只读观察，绝不触发处置。

安装回执会保留旧 wrapper、`current` 指针和四个 unit 的内容、权限及恢复材料。只有当前
S6 wrapper、unit 和版本指针仍精确等于该回执时，才允许停止三个 timer 并回滚；这避免覆盖
后续安装：

```bash
sudo python3 deploy/governance/install-server-directory-audit-schedule.py \
  --operation-id <原安装操作ID> --rollback
```

回滚同样不接触业务服务、release、数据目录、旧 Domeye 或 GitHub 凭证，并生成独立
root-only 回滚回执。安装或回滚均不能代替 S3 凭证迁移、S4/S5 单批隔离授权。

## S2 checkout 可恢复归一

`normalize-server-checkout.py` 只接受固定的 `buptserver16`、
`/home/bgpdata/Domeye-Core`、`/home/bgpdata/Domeye-Core-artifacts` 与公开 HTTPS
GitHub remote。默认只读预检；只有明确给出 `--apply` 才会创建 archive、把原 checkout
原子移动到 `Domeye-Core-artifacts/quarantine/checkouts/<operation-id>/`、clone 干净
`main` 并写入 root-only 回执。

该脚本会拒绝进程 cwd/exe/fd 引用、挂载、Git 锁、活动指针引用源码 checkout、跨文件
系统隔离、SHA 漂移或带凭证的 remote。clone 或读回失败时会把原 checkout 恢复到原精确
路径；它不修改 GitHub 账号凭证、运行指针、生产配置、服务或旧 Domeye。

若服务器到公开 GitHub HTTPS 的无凭证连接已明确失败，可传入受管
`--bundle-path /home/bgpdata/Domeye-Core-artifacts/incoming/<operation-id>.bundle`。bundle
只能由本机从冻结的 GitHub `main` 生成并经 SSH 传入；脚本会 clone 该 bundle 后把新
checkout 的 `origin` 固定为公开 HTTPS URL，读回 HEAD、`origin/main`、分支和 clean
状态。bundle SHA-256 与原 checkout archive 一同保留在本次 quarantine 回执中。此替代
不证明服务器已恢复 GitHub HTTPS 出站能力。

## S2 后续 checkout 刷新

首次归一后，GitHub `main` 可以继续前进；服务器 checkout 不得因此长期停留在旧 SHA。
`refresh-server-checkout.py` 只允许在现有 checkout 为干净 `main`、唯一 remote 为固定公开
HTTPS `origin`，且没有 cwd/exe/fd、挂载、Git 锁或活动指针引用时运行。它只接受本机从
冻结 `main` 制作、先传至 `Domeye-Core-artifacts/incoming/<operation-id>.bundle` 的 bundle。

刷新时它保留输入 bundle，在 `.git/refs/domeye-governance/checkout-refresh/` 建立刷新前
commit 的 rollback ref，导入 bundle、更新本地 `origin/main` 跟踪引用并硬重置到冻结目标。
任一读回失败会把 checkout 与 `origin/main` 恢复到刷新前 SHA。该操作不接触运行 release、
服务、旧 Domeye 或 GitHub 凭证，也不证明服务器具备 GitHub HTTPS 出站能力。

脚本必须先从已合入 `main` 的不可变提交运行，并由独立 S2 任务冻结 operation ID 与两个
预期 SHA。生产服务器不安装该脚本；经审批后可通过标准输入运行，避免新增服务器脚本文件。

本目录中的目标引用、审批 schema 和夹具统一指向 `main`，只表示仓库定义已经迁移。
生产服务器现有 Hook 不会随 Git 提交自动更新；只有 `install.sh` 的不可变来源、安装
回执、安装后 SHA-256、夹具和当前 release 归一检查全部读回一致，才能声明服务器端
治理已迁移。

`install.sh` 不创建、移动或删除 Git 分支，也不修改 `core-work` 的 checkout/upstream。
生产 cutover 还必须在独立高风险任务中读回 `refs/heads/main`、`origin/main` 与
`core-work` 的精确 SHA 和 upstream；这些检查没有完成时，运行验证脚本对 `main` 的
要求会失败关闭，这是预期保护行为。

## 服务器端保护合同

`pre-receive` 默认从
`/home/bgpdata/Domeye-Core-governance/approvals/<commit>.json` 读取审批。生产仓库由
root 管理，审批文件必须是 root 持有的普通文件，且组和其他用户不可写。测试仓库
可以通过本地 Git 配置指定隔离目录：

```bash
git -C <repository> config \
  domeye.governanceApprovalRoot /absolute/test/approvals
```

生产修改该配置属于治理变更，必须单独审计。Hook 允许普通任务分支推送，但对
`main` 和正式 tag 执行失败关闭：

- `main` 只能快进，禁止删除和强推；
- 新目标提交必须具有格式正确的评审与 CI 证明；
- 已存在 tag 禁止修改和删除；
- 名称匹配 `YYYYMMDDTHHMMSSZ-*` 的 tag 必须为 annotated tag；
- 正式 tag 目标必须已经进入当前或同批更新的 `main`。

## 发布归一检查

运行：

```bash
/home/bgpdata/Domeye-Core-governance/bin/check-release-normalization.sh \
  <release-id>
```

检查器只读以下生产状态：

- `/home/bgpdata/Domeye-Core` 中的 `main` 与 annotated tag；
- `/home/bgpdata/Domeye-Core-runtime/unified-releases/<release-id>` 的候选、部署、
  验证和身份等式 JSON；
- 源码归档 SHA-256；
- Backend、Sidecar、Frontend 的源码绑定、活动指针和实际运行状态；
- Nginx 配置摘要和生产健康接口；
- 数据库 `changed=false` 与费用门禁。

通过时输出 `domeye_release_normalization_gate_v1` JSON。该结果只证明列明的身份等式
成立，不替代未在证据中声明的业务效果或模型认证。

## 安装与回滚边界

只能从最终提交解出的不可变源码目录运行安装：

```bash
sudo ./deploy/governance/install.sh <release-id>
```

默认安装到：

```text
/home/bgpdata/Domeye-Core/.git/hooks/pre-receive
/home/bgpdata/Domeye-Core-governance/bin/check-release-normalization.sh
```

旧文件备份到
`/home/bgpdata/Domeye-Core-governance/backups/<release-id>/`，安装回执写入
`/home/bgpdata/Domeye-Core-governance/installations/<release-id>.json`。相同 release ID
只允许幂等复核，不允许覆盖不同内容。

安装失败时脚本会尝试恢复安装前版本。人工回滚必须从安装回执指定的备份恢复两个
文件，核对 SHA-256，再运行本目录夹具和当前生产 release 的归一检查；不得只恢复
其中一项后继续发布。

## 本地检查

```bash
bash -n \
  deploy/governance/pre-receive \
  deploy/governance/check-release-normalization.sh \
  deploy/governance/install.sh \
  deploy/governance/tests/run-fixtures.sh \
  deploy/governance/tests/check-doc-links.sh
bash deploy/governance/tests/run-fixtures.sh
bash deploy/governance/tests/check-doc-links.sh
```

本地夹具不连接生产，不修改真实审批目录，也不构建业务组件。正式治理发布仍需从
最终提交构建一次统一制品，完成同制品候选、金丝雀、生产晋级，并在生产运行版本化
归一检查。
