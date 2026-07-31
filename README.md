# Domeye Core

Domeye Core 是面向 BGP 路由异常观测的精简核心系统。仓库同时包含离线检测核心、
固定历史数据的只读工作台、Vue 前端、Flask 查询与控制面代理，以及受约束的国家
中断报告 Sidecar 接入。它用于整理和展示可审计的控制面事实，不把路由可见性直接
解释为用户连通性、业务影响、事件原因或自动处置结论。

当前仓库数据档为 `feb-mar-2026`。数据范围、快照时间和业务时区只由
[`config/data-profile.json`](config/data-profile.json) 定义；国家中断观测与报告
范围固定为 RRC25。`backend/core/` 是冻结的离线检测核心，任何外围修改都必须继续
通过 `backend/core.sha256` 校验。

## 从这里开始

| 需要了解的内容 | 文档入口 |
| --- | --- |
| 文档分类和历史材料性质 | [文档索引](docs/README.md) |
| 浏览器、前端、后端、Sidecar 与数据库关系 | [Domeye Core 前后端总览](docs/DomeyeCore前后端总览.md) |
| 前端路由、API、测试和构建 | [前端说明](frontend/README.md) |
| 后端 API、数据库和 Sidecar 代理 | [后端说明](backend/README.md) |
| 候选、发布、切换和回滚 | [部署说明](deploy/README.md) |
| 当前生产运行身份如何采集 | [生产实时库存采集](deploy/inventory/README.md) |
| 开发、验收与有状态操作边界 | [开发与验收流水线](docs/开发与验收流水线.md) |
| Worktree 与任务版本边界 | [Codex 版本边界治理](docs/Codex版本边界治理说明.md) |

历史计划、阶段验收和旧发布记录用于解释设计演进，不能替代当前机器合同，也不能
单独证明当前生产运行身份。生产结论必须来自目标机器上实时执行的只读库存采集，
并联合核对进程目录、监听端口、不可变 release、前端树、Nginx 配置和回滚状态。

## 能力边界

Domeye Core 当前提供：

- 六类 BGP 异常事件的分页检索、详情和证据视图；
- 全局、国家和 ASN 的固定窗口特征与中断时序；
- P0 数据状态、质量和指标序列查询；
- RRC25 国家中断发布快照的总览、时序、ASN 矩阵和审计读取；
- 事件限定的报告生成、同快照追问、SSE 状态、终止以及 Markdown/PDF 下载代理；
- 独立的候选验收、不可变源码绑定、前端原子安装和组件级回滚机制。

这些能力不等于：

- 实时全网根因分析或闭环响应；
- 对数据平面、用户体验、业务损失或事件责任的证明；
- 任意 collector、国家或时间窗口的自由切换；
- 允许浏览器或核心后端直接访问互联网；
- 仅凭分支名、构建成功、健康检查或 HTTP 200 即可确认已部署。

## 仓库结构

```text
Domeye-Core/
├── backend/              Flask API、查询服务、数据库访问和冻结 core
├── frontend/             Vue 3、TypeScript、Vite 前端
├── agent-sidecar/        国家中断报告与追问 Sidecar
├── config/               数据档和版本化验收配置
├── contracts/            OpenAPI 与 Agent 机器合同
├── deploy/               候选、发布、切换、回滚和运行库存采集
├── dev/                  本地开发、门禁和只读检查器
├── docs/                 当前说明、设计、计划、验收与历史记录
└── Makefile              统一开发和检查入口
```

## 本地开发与检查

日常开发遵循“启动、观察、最小修改、定向检查”的短循环：

```bash
make dev
make preview
make check-fast
make check-integration
```

文档改动使用：

```bash
make check-docs
```

完整发布检查仍由影响范围决定，不应因普通文档或页面文字调整而重建数据库。涉及
数据库恢复、生产切换、Nginx、Sidecar、`backend/core/` 或真实配置时，必须进入
独立的严格验收与授权流程。

后端依赖以 [`backend/pyproject.toml`](backend/pyproject.toml) 和
[`backend/uv.lock`](backend/uv.lock) 为准；前端依赖和命令以
[`frontend/package.json`](frontend/package.json) 为准；API 路径与响应合同以
[`contracts/openapi.json`](contracts/openapi.json) 和实际注册路由共同为准。

## 版本、候选与生产不是同一状态

| 状态 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 本地工作树 | 某个基线上的源码差异 | 已合并、已发布 |
| 测试或候选通过 | 指定合同和候选路径通过 | 生产正在运行该候选 |
| Git 提交或标签 | 源码身份可追溯 | 运行时已切换 |
| 不可变 release | 发布制品与源码已绑定 | Nginx、进程和前端已指向它 |
| 实时生产库存 | 采集时刻观察到的运行身份 | 未采集时间之后的持续状态 |

因此，更新仓库文档只代表文档与当前代码、机器合同对齐；本任务或任何文档提交都不
自动形成生产发布结论。
