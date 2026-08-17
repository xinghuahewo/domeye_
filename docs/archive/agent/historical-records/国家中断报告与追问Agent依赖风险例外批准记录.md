# 国家中断报告与追问 Agent 依赖风险例外批准记录

## 一、记录身份

| 项目 | 值 |
|---|---|
| 记录版本 | `country_outage_dependency_risk_exception_approval_v2` |
| 风险例外 ID | `country-outage-pi-ghsa-mh99-v99m-4gvg-20260812-v2` |
| 批准时间 | 2026-07-29 11:03:08（北京时间） |
| 到期边界 | 2026-08-13 00:00:00（北京时间，不含该时刻） |
| 覆盖日期 | 至 2026-08-12，含当日 |
| 机器资源 | `agent-sidecar/resources/risk-exceptions/country-outage-pi-ghsa-mh99-v99m-4gvg-v2.json` |
| 机器资源 SHA-256 | `e2a54ef160aecb444ccba3e412711295e4ea9a9d0ef9ab2a06b7b292b6c8c4bf` |

本记录只批准下述固定正式路径在限定时间内接受已知传递依赖风险，不代表漏洞已经
修复，不代表整个 Pi 或其他 Domeye 路径获得豁免，也不能用来扩大 Agent 能力。
本版同时把已经单独批准的同名 `responseModel` vendor patch 纳入机器约束；它
替代 v1 记录，v1 不能继续作为当前正式路径的批准依据。

## 二、已知风险

固定依赖链为：

```text
@earendil-works/pi-coding-agent@0.82.1
  └─ minimatch@10.2.5
      └─ brace-expansion@5.0.7
```

对应 advisory 为 `GHSA-mh99-v99m-4gvg`。风险是无界 brace expansion 可能导致
内存耗尽和进程拒绝服务。本次批准不降低该风险等级，只依据正式路径当前不触达
相关发现、解析和外部 glob 能力，给予短期、可撤销的例外。

## 三、受控 responseModel 补丁身份

批准的补丁只修改固定 Pi `0.82.1` 内
`@earendil-works/pi-ai@0.82.1` 的以下相对路径：

```text
node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js
```

补丁只移除“响应模型名必须不同于请求模型名”这一额外条件，使供应方返回的非空
同名 `chunk.model` 也能写入 `responseModel`。它不增加网络请求、不增加工具、
不改变资源解析，也不允许用请求模型字段补造响应模型证据。

| 身份 | SHA-256 |
|---|---|
| 补丁前上游源码 | `0d50250fe2931e66e2078279a397814202e1ecddee58faf4b8bc04c278da177a` |
| 补丁后固定源码 | `5805cc08566c4d9437280f68d996ef0fb452c15e2becb67b94c967b7ace2023b` |
| vendor patch 制品 | `c62983d07f150ddbef0e412feb596406648f1e151430f633f406ca018e2412cd` |
| vendor patch manifest | `886b0faf7ccbd0dec19ba74aaa3d92e5b6a218177bf36f1f50a5ece553f8bfba` |

补丁 ID 为 `pi-ai-openai-completions-response-model-v1`，应用模式固定为
`postinstall_exact_hash_replacement_v1`。安装脚本只接受上述补丁前源码摘要后
执行一次精确替换，或接受已经匹配补丁后摘要的源码并完成核验。包版本、固定
相对路径、普通文件身份、源码长度、源码摘要、替换片段出现次数、补丁制品或
manifest 任一不一致均失败关闭；不会对未知版本或未知源码尝试模糊打补丁。
`prebuild` 和 `pretypecheck` 还会再次验证已安装源码摘要。

## 四、批准范围与硬约束

风险例外仅在以下条件全部成立时有效：

1. Pi 固定为 `@earendil-works/pi-coding-agent@0.82.1`。
2. 传递组件固定为 `brace-expansion@5.0.7`，advisory 固定为
   `GHSA-mh99-v99m-4gvg`。
3. 正式路径使用 `country-outage-static-resource-loader-v1`，关闭 Pi
   `PackageManager` 解析、`ModelResolver` 和外部 glob。
4. 只加载固定的 `country-outage-report` Skill。
5. 只注册以下三个只读工具：

   - `country_outage_resolve`
   - `country_outage_get_observation`
   - `country_outage_get_asns`

6. 不允许增加工具、运行时扩展、通用文件系统、Shell、编辑、写入、任意 URL、
   任意事件切换或其他 Agent 能力。
7. 只允许第三节固定的 vendor patch；已安装适配器、补丁制品、manifest 和
   v2 风险例外资源的身份必须同时闭合。
8. 正式 Sidecar 必须在模型、认证和 HTTP Server 预检之前读取并校验机器资源；
   资源缺失、格式错误、身份不符、约束漂移、补丁漂移、尚未生效或到期均失败
   关闭。
9. 安全运行审计只记录风险例外 ID、截止时间和当前状态，不记录批准正文或其他
   风险资源内容。

## 五、立即复评条件

出现下列任一情况时，本批准立即失效，不能等到日历到期：

1. Pi 发布包含相关修复的版本；
2. Pi 版本、传递依赖版本或依赖路径发生变化；
3. 正式资源加载、模型解析、glob、Skill、工具或能力边界发生变化；
4. 发现当前正式路径实际能够触达受影响代码；
5. vendor patch、manifest、目标路径、应用模式或任一固定摘要发生漂移；
6. Pi 上游已经不再需要该补丁，或发布可用的正式修复；
7. 发现新的利用证据、内存耗尽事件或风险等级变化。

复评结果只能是升级到已修复版本并重新认证、形成新的有期限批准，或停止正式
路径。旧例外不能自动续期。

## 六、到期处理

机器到期时间为 `2026-08-12T16:00:00Z`。在该时刻及之后，正式 Sidecar 启动
预检必须返回风险例外到期错误，不得创建模型运行时、HTTP Server 或开始监听。
已经启动的叙述器在每次报告运行前还会再次检查截止时间；超过截止时间的运行不得
创建模型会话或发布报告。

## 七、批准结论

批准上述范围内的短期风险例外，有效期至 2026-08-12（北京时间，含当日）。
批准仅对国家中断报告与追问 Agent 的固定正式路径和第三节固定补丁生效，并以
v2 机器资源、补丁证据和已安装源码通过全部预检为必要条件。该批准不等于
DeepSeek 模型认证、A4 阶段或正式运行验收通过。
