# 国家中断 Agent Sidecar 发布与运行

本目录只服务于 `feb-mar-2026` 固定历史观测环境。运行边界固定为：

- Sidecar 仅监听 `127.0.0.1:28474`；
- 国家范围固定为伊朗（`IR`），collector 固定为 `rrc25`；
- 外部证据 Provider 固定为 `disabled`；
- 身份模式固定为 `internal_fixed_history`；
- 不修改数据库、`backend/core`、数据档或外部证据能力。

## 固定目录

- 项目检出：`/home/bgpdata/Domeye-Core`
- 运行根：`/home/bgpdata/Domeye-Core-runtime/country-outage-agent`
- 配置：`/home/bgpdata/Domeye-Core-runtime/config/country-outage-agent.env`
- release：`.../country-outage-agent/releases/<release-id>`
- current：`.../country-outage-agent/current`
- Screen：`domeye_country_outage_agent`

配置目录必须为 `root:root 0700`，配置、认证文件和状态文件必须为
`root:root 0600`，审计目录必须为 `root:root 0700`。脚本逐键解析配置，
不会 `source` 配置，也不会把共享令牌写入命令行或日志。配置样例见
`country-outage-agent.env.example`。

## 发布流程

所有命令均以 root 执行。release-id 示例：
`20260730T140000Z-country-outage-agent-core-a5`。

```bash
deploy/country-outage-agent/prepare.sh <release-id> <完整40位git-sha>
deploy/country-outage-agent/start.sh <release-id>
deploy/country-outage-agent/status.sh
deploy/country-outage-agent/stop.sh
CONFIRM_RELEASE_ID=<当前release-id> \
  deploy/country-outage-agent/rollback.sh <当前release-id>
```

生产不可变归档叠加路径不读取或修改服务器主 Git 工作树。候选 release 根必须先包含
`COUNTRY-OUTAGE-SOURCE-BINDING.json` 和与其 SHA-256 一致的完整代码归档，然后使用：

```bash
deploy/country-outage-agent/prepare.sh \
  <release-id> <获批叠加包完整40位git-sha> <已绑定的不可变候选release根>
```

第三个参数只接受固定生产 release 根下的实际目录，并强制复核基础 release、
基础归档 SHA-256、组合归档 SHA-256、RRC25/IR/禁用外部证据、`backend/core`
14/14 保持及数据库未改变等绑定字段。省略第三个参数时，仍保持原有干净 Git
检出模式。

`prepare` 要求干净检出、固定 Node.js `v22.23.1`、A5 Hook、`backend/core`
哈希、Pi `0.82.1` 风险例外、正式模型 profile 和认证有效期全部通过。
它还会读取版本化的模型认证复用身份锚点，逐项核对六组 21 个源码/schema 的
A3 基线 SHA-256、固定 Skill bundle SHA-256，以及安装后
`responseModel` adapter 的补丁摘要；任一项漂移都会在模型或监听器启动前失败。
候选只额外复制这 21 项所需的
`contracts/agent/country-outage-report-facts-v1.schema.json`，不会扩大合同目录。
Node 依赖通过 lockfile 安装并禁止自动生命周期脚本，只显式应用已冻结的
Pi vendor patch。PDF 使用 release 内独立 Python 3.10 venv，依赖版本固定，
中文字体按路径、所有者、不可写权限和 SHA256 校验。

`prepare` 仅生成不可变 release，不切换 current、不启动进程。`start`
原子切换 current，在独立 Screen 启动后同时校验 ready 日志、端口和
readiness API；任一步失败都会停止已知进程并尽力恢复旧 current。
`status` 只读验证 current、manifest、全文件 SHA256、配置摘要、Screen
进程标记、端口和 disabled Provider。`rollback` 只切换到记录的上一
release；当前 Sidecar 已崩溃时也可回滚，但遇到未知或多个 Screen 会拒绝。

## 回滚和故障边界

- release 从不自动删除或覆盖；
- 回滚必须显式提供当前 release-id，且与状态和 current 同时匹配；
- 配置变更后必须显式停止并重新启动；
- 不接管身份不匹配的 Screen 或占用 `28474` 的未知进程；
- 认证、风险例外、中文字体、文件集合或任一 SHA256 漂移均失败关闭；
- 外部证据未部署不影响核心 Agent，但本流程绝不会启用它。

## 本地检查

以下检查不连接生产、不启动 Sidecar：

```bash
bash -n deploy/country-outage-agent/*.sh \
  deploy/country-outage-agent/lib/common.sh
node --check deploy/country-outage-agent/verify-formal-release.mjs
node --check deploy/country-outage-agent/probe-sidecar.mjs
deploy/country-outage-agent/tests/run-fixtures.sh
```
