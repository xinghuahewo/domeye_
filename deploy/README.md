# Domeye Core 部署、切换与回滚

`deploy/` 管理候选准备、不可变制品、生产切换、回滚和只读运行身份采集。任何
`check-*` 命令都必须无生产副作用；数据库恢复、生产激活和回滚只能由独立的
`release-*` 或组件生命周期入口执行。

本文描述仓库当前部署机制，不宣称某个分支、标签或 release 正在生产运行。当前
生产身份必须在目标机器上执行
[实时库存采集](inventory/README.md)后判断。

## 部署边界

- 数据档由 [`../config/data-profile.json`](../config/data-profile.json) 唯一定义；
- API 合同由 [`../contracts/openapi.json`](../contracts/openapi.json) 和实际 Flask
  路由共同定义；
- `backend/core/` 不得在普通部署中改变；
- 数据库、真实 `.env`、共享 token、认证文件和发布状态位于 Git 仓库外；
- 候选准备不等于生产激活；
- Git 提交或标签不等于运行进程已经切换；
- 前端局部功能较新不等于整个 `dist` 都比生产新；
- Sidecar 未配置或外部证据关闭时，不得擅自扩大网络能力。

## 目录职责

| 目录或入口 | 职责 |
| --- | --- |
| `artifacts/` | 信息制品和前端构建的校验、原子安装与回滚 |
| `database/` | 数据库制品、恢复、激活和回滚 |
| `acceptance/` | 候选栈、API、SPA 和隔离验收 |
| `release/` | 统一准备、激活、回滚和候选清理状态机 |
| `nginx/` | 前端服务根与 `/api/v1`、`/api/v2` 代理配置 |
| `country-outage-agent/` | 国家中断 Sidecar 独立生命周期 |
| `inventory/` | 当前生产运行身份只读采集 |
| `start-backend.sh`、`stop-backend.sh`、`status.sh` | 后端 Screen 生命周期和状态 |

数据库与 INFO 数据迁移的历史设计和执行手册通过
[文档索引](../docs/README.md)查阅，不在本文件重复维护每一期参数。

## 统一拓扑

仓库配置声明的 Web 拓扑为：

```text
浏览器
  │
  ▼
Nginx :28471
  ├── / 与 SPA 路由
  │      → /home/bgpdata/Domeye-Core-runtime/web/dist
  ├── /api/v1/*
  │      → Flask 127.0.0.1:28473
  └── /api/v2/*
         → Flask 127.0.0.1:28473
                    │
                    ├── 只读数据库和 Git 外信息制品
                    └── 本机国家中断 Sidecar 127.0.0.1:28474
```

Nginx 服务根来自 [`nginx/domeye-core.conf`](nginx/domeye-core.conf)，前端安装
目标来自 [`lib/frontend-common.sh`](lib/frontend-common.sh)，两者必须完全一致：

```text
/home/bgpdata/Domeye-Core-runtime/web/dist
```

文档门禁会直接解析这两个文件，发现路径漂移时失败。

## 源码、候选、发布和运行身份

| 层次 | 典型证据 | 判断边界 |
| --- | --- | --- |
| 源码基线 | 完整提交 SHA、干净 Worktree、任务合同 | 只能证明修改起点 |
| 候选验收 | 测试、构建、Canary、候选树哈希 | 不能证明生产已切换 |
| 不可变 release | 归档 SHA、源码绑定、组件 manifest | 不能替代进程和 Nginx 回读 |
| Git 发布 | 远端分支、标签及 peeled commit | 不等于运行时 |
| 生产运行身份 | 端口、PID、`cwd`、release、前端树、Nginx、回滚状态 | 只代表采集时刻 |

整体发布必须从唯一、可复核的不可变源码身份构建。服务器历史脏工作树、兄弟
Worktree、旧候选 `dist` 和会话记忆都不能作为隐含发布输入。

## 统一发布状态机

高风险发布入口按副作用拆分：

```bash
make release-prepare \
  RELEASE_ID="<release-id>" \
  DATABASE_ENV_FILE="<受限配置路径>"

make release-activate \
  RELEASE_ID="<release-id>" \
  DATABASE_ENV_FILE="<受限配置路径>" \
  RELEASE_HOST="<发布机身份>" \
  CONFIRM_RELEASE_ID="<release-id>"

make release-rollback \
  RELEASE_ID="<release-id>" \
  DATABASE_ENV_FILE="<受限配置路径>" \
  RELEASE_HOST="<发布机身份>" \
  CONFIRM_RELEASE_ID="<release-id>"
```

具体参数和状态机以 [`release/`](release/) 脚本及
[开发与验收流水线](../docs/开发与验收流水线.md)为准。

`release-prepare` 只准备和验收候选，不允许改变生产数据库链接、在线前端、
Nginx 或活动进程。`release-activate` 必须重新核对输入和候选身份，再按受保护
顺序切换。`release-rollback` 只消费已记录、尚未消费的回滚身份，不猜测目标。

## 前端原子安装

前端构建先写入候选目录并计算确定性树哈希。正式安装入口
[`artifacts/install-frontend-build.sh`](artifacts/install-frontend-build.sh)
执行：

1. 校验候选目录只含普通目录和文件，不接受软链接或特殊对象；
2. 复制到目标同级临时目录并再次计算树哈希；
3. 记录 `frontend-current`、上一 release、上一树哈希和回滚目录；
4. 通过同文件系统重命名原子切换完整目录；
5. 写入安装状态，供崩溃恢复和一次性回滚使用。

回滚入口
[`artifacts/rollback-frontend-build.sh`](artifacts/rollback-frontend-build.sh)
只使用受校验的 journal 和安全生成路径。旧 release 和备份不会因新版本激活而
自动删除。

### 防止整体旧前端覆盖

禁止以下发布推理：

```text
某个国家中断页面或 Agent 子树更新
⇒ 整个候选 frontend/dist 都比在线前端新
```

正确流程是：

1. 固定完整源码提交和允许变更范围；
2. 从该提交构建整个前端；
3. 校验候选树与来源绑定；
4. 在独立端口验证关键页面和 API；
5. 原子安装；
6. 从真实入口重新打开关键页面；
7. 回读在线前端树、入口资源、Nginx root 和 `frontend-current`。

前端验收不能只检查 `index.html`、资源摘要或局部新页面。至少应覆盖：

- `/`；
- `/events` 的固定日期、请求参数和结果；
- `/features`；
- `/countries`；
- `/ases`；
- 国家中断详情的数据观测与报告工作台。

## 后端和 Sidecar

后端管理脚本只操作 Domeye Core 的固定 Screen 和回环监听，不接管未知进程。
健康检查通过后仍需验证 P0 状态、质量、真实事件查询、国家中断读取以及进程
`cwd` 的 release 身份。

国家中断 Sidecar 使用独立流程：

```bash
deploy/country-outage-agent/prepare.sh <release-id> <完整提交SHA> [不可变源码根]
deploy/country-outage-agent/start.sh <release-id>
deploy/country-outage-agent/status.sh
deploy/country-outage-agent/stop.sh
CONFIRM_RELEASE_ID="<当前release-id>" \
  deploy/country-outage-agent/rollback.sh "<当前release-id>"
```

详细的 RRC25、IR、本机监听、模型身份、PDF 运行时和禁用外部证据边界见
[`country-outage-agent/README.md`](country-outage-agent/README.md)。

## 数据库和 INFO

数据库、INFO 和前端属于同一次完整切换时，必须由统一激活状态机协调，不能手工
拆开并留下跨版本组合。昂贵恢复步骤应保留安全检查点，普通后置门禁失败不应盲目
重建数据库。

本仓库保留数据库制品构建、恢复和 INFO 数据迁移工具，但是否允许执行由当前
数据档、发布机身份、受限配置和明确授权共同决定。文档整理、前端文字或普通
校验器变更不授权任何数据库操作。

## 回滚原则

- 前端、后端、数据库、INFO、Nginx 和 Sidecar 分别保留明确的上一身份；
- 自动回滚只恢复本次切换实际改变的组件；
- 任一回滚步骤失败时保留现场并返回失败，不宣布已恢复；
- 不删除新 release 或上一 release；
- 不把无状态的目录猜测为回滚目标；
- 数据库未改变时不执行数据库回滚；
- 回滚完成后重新采集运行库存并复验关键路径。

## 只读生产库存

在受信操作者已经登录目标服务器后执行：

```bash
python3 -B deploy/inventory/collect-production-runtime.py
```

采集器只向标准输出写 JSON，不接受 URL 或路径参数，不执行 SSH、网络请求、
`git fetch`、数据库访问或生产切换，也不读取进程环境、命令行、`.env`、认证文件
和密钥。它联合观察后端端口与进程目录、Sidecar/Canary 端口、固定 Screen、
release 身份文件、Nginx root、前端树、运行依赖和服务器 Git 引用。

库存输出仍是“采集时刻的观察证据”，不是永续状态声明。完整字段和摘要合同见
[`inventory/README.md`](inventory/README.md)。

## 检查与人工复核

文档和架构合同：

```bash
make check-docs
```

完整发布检查按影响范围选择，不应把普通文档整理升级为生产演练。真正发布后还需
人工确认：

- 源提交、远端分支、标签和不可变归档一致；
- 后端 PID、`cwd` 和 release 唯一；
- Sidecar、Screen、collector 和能力边界一致；
- 在线前端树、入口资源、`frontend-current` 和 Nginx root 一致；
- `/events` 等关键页面实际查询正确；
- 数据库、核心哈希和回滚身份未发生未授权改变。
