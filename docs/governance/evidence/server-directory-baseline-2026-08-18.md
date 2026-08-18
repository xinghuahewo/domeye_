# Domeye 服务器目录治理 S0 只读基线

## 结论

2026-08-18 19:05（Asia/Shanghai）在 `buptserver16` 完成只读盘点。审计没有在服务器
落盘，没有移动、删除、重启、安装、发布或切换任何对象，也没有读取配置内容、进程
命令行参数或环境变量。

Gate 结论为 `BLOCK_MUTATION`。当前只允许继续完善 GitHub 上的治理工具和分类证据，
不允许执行服务器目录变更。

机器证据见
[server-directory-baseline-2026-08-18.json](server-directory-baseline-2026-08-18.json)。

## 权威身份

| 项目 | 观察值 |
| --- | --- |
| GitHub 基线 | `main@e3790075c12ec64228444d15c7d25c76b469a0d4` |
| 任务分支 | `codex/server-governance-s0-s1` |
| 审计器 SHA-256 | `5c2987b017677e7cd48b42ba330330531a076d81a27a4a3aff5ef8bccd04bd7f` |
| 策略文件 SHA-256 | `bca752347e32fd2624ee658e75ecaad062a8dc55cd31d34a5266f08c3d6085d5` |
| 策略规范化 SHA-256 | `c7dc276248ef49add2e90ed323ae79f81c9a07f497e6ddfce6f920f25922ab8c` |

## 主要观察

- 文件系统使用率为 `84.1%`，进入 warning，剩余 `1,875,167,260,672` 字节；
- `/home/bgpdata/Domeye-Core` 为 `main@db230634…`，没有 remote，存在 48 个修改、
  51 个未跟踪对象；禁止作为发布来源；
- 三条活动指针均存在、未断裂且位于允许的 release 根；
- Runtime 配置目录有三个文件，均为 `0600`，审计没有读取其内容；
- 组件 release 根合计有 98 个可见组件目录、8 个隐藏构建/source 目录；另有 16 个
  unified release 记录；
- `/home/bgpdata` 顶层有 5 个对象尚未分类，包括旧 candidate、backfill 脚本与日志；
- 发现 7 个 cwd 位于旧 `/home/bgpdata/Domeye/backend` 的保护进程；本治理不得向它们
  发送信号或修改其目录；
- 当前有 5 个 Screen 会话，其中旧 `domeye` 与 `domeye_bgpdetection` 受保护。

## Findings

| 严重度 | 代码 | 含义 |
| --- | --- | --- |
| warning | `source_checkout_dirty` | 服务器 checkout 不是干净 `main` |
| warning | `hidden_release_directories` | 8 个隐藏构建目录只能先分类、隔离 |
| warning | `disk_usage_threshold` | 使用率越过 80% warning |
| warning | `unclassified_top_level_entries` | 5 个顶层对象尚未归属 |

## 下一 Gate

S0/S1 工具必须先通过 PR、CI 和评审进入 `main`。之后另开高风险任务执行 S2：重新
确认没有运行引用，冻结脏 checkout 的清单和可恢复归档，再决定是否隔离并建立干净
`main` checkout。当前证据不授权该动作，也不授权处理任何旧 Domeye 文件或进程。
