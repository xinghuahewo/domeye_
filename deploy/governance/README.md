# 治理发布工具

本目录版本化 Domeye Core 的服务器端 Git 保护和只读发布归一检查。它只治理源码、
制品和运行身份，不修改业务、数据库或模型合同，也不会调用 DeepSeek。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `pre-receive` | 保护 `main` 和正式发布时间戳 tag |
| `check-release-normalization.sh` | 核对统一发布证据、组件制品与实际生产身份 |
| `install.sh` | 备份旧版本、原子安装两项服务器治理脚本并生成回执 |
| `tests/run-fixtures.sh` | 覆盖主干审批、非快进、正式 tag 和归一合同的正反夹具 |
| `tests/check-doc-links.sh` | 检查治理涉及文档的相对链接 |

完整规则见
[主干开发与发布归一治理规范](../../docs/主干开发与发布归一治理规范.md)。

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
