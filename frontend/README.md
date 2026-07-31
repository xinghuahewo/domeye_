# Domeye Core 前端

`frontend/` 是 Vue 3、TypeScript 和 Vite 实现的固定历史路由观测工作台。页面通过
Vue Router 组织，使用 Axios 读取普通查询 API，使用 Fetch 与 `EventSource` 访问
国家中断报告控制面。依赖版本和可执行命令只以
[`package.json`](package.json) 与锁文件为准。

## 技术栈

- Vue 3 与 Vue Router；
- TypeScript 与 `vue-tsc`；
- Vite；
- Vitest；
- Axios；
- ECharts；
- `openapi-typescript` 生成响应类型。

## 页面路由

以下表格由 [`src/router/index.ts`](src/router/index.ts) 约束。`/__components`
只在开发模式或显式开启组件预览时注册；最后一项是未匹配路由的兜底页。

<!-- architecture-docs:frontend-routes:start -->
| 路径 | 页面 | 使用范围 |
| --- | --- | --- |
| `/` | 核心态势 | 正式页面 |
| `/events` | 六类异常事件检索 | 正式页面 |
| `/events/detail` | 通用事件证据与国家中断工作台 | 正式页面 |
| `/features` | 综合特征 | 正式页面 |
| `/countries` | 国家态势 | 正式页面 |
| `/countries/:country` | 国家档案 | 正式页面 |
| `/ases` | ASN 态势 | 正式页面 |
| `/ases/:asn` | ASN 档案 | 正式页面 |
| `/__components` | 组件标本 | 仅开发或显式预览 |
| `/:pathMatch(.*)*` | 页面不存在 | 路由兜底 |
<!-- architecture-docs:frontend-routes:end -->

路由表是页面入口真相源；README 不允许增加源码没有注册的页面，也不能遗漏已注册
页面。

## API 关系

普通查询客户端在 [`src/api/client.ts`](src/api/client.ts) 中创建：

- `/api/v1/`：健康检查、P0 状态、事件、详情、证据、特征和仪表盘查询；
- `/api/v2/`：国家中断发布快照解析、总览、时序、ASN 矩阵和审计查询。

国家中断报告客户端位于
[`src/api/countryOutageAgent.ts`](src/api/countryOutageAgent.ts)，通过同源
`/api/v2/country-outage/` 调用：

- `POST` 创建报告；
- `POST` 提交同一快照下的追问；
- `GET` 订阅 SSE 状态；
- `POST` 终止运行；
- `GET` 下载 Markdown、PDF 或经授权生成的外部附录；
- `GET` 读取外部证据能力状态。

浏览器不直接访问 Sidecar。所有 Agent 请求先进入 Flask 控制面代理，由后端校验
身份、请求大小、幂等键和本机 Sidecar 边界。接口集合以
[`../contracts/openapi.json`](../contracts/openapi.json) 和后端注册路由为准。

## 固定数据窗口

固定数据档为 `feb-mar-2026`，业务时区为 `Asia/Shanghai`：

```text
2026-02-01T00:00:00+08:00 <= t < 2026-04-01T00:00:00+08:00
snapshot = 2026-03-31T23:59:59+08:00
```

唯一来源是
[`../config/data-profile.json`](../config/data-profile.json)。Vite 在构建时读取
该文件并注入窗口；若数据档无效则拒绝构建。页面不得按浏览器当前日期自行制造
生产查询窗口，运行时缺少固定窗口时应失败关闭。

## 国家中断报告工作台

国家中断详情页同时承载“数据观测”和“报告与追问”两个视图。工作台只能使用当前
合法事件绑定的 `incident_id`、`publication_id`、`revision`、RRC25 和固定时间窗：

- 报告和追问是短生命周期会话，不默认保存永久历史；
- 报告可下载 Markdown 和 PDF；
- SSE 用于状态、断线重连和会话提醒；
- 用户可终止当前运行；
- 外部证据是独立能力，未配置或自检失败时不得影响 Domeye 内部事实报告；
- 页面上的 AI 结果仍需人工审核，不能替代控制面证据边界。

## 开发、测试和构建

在 `frontend/` 目录执行：

```bash
npm ci
npm run dev
npm run typecheck
npm test
npm run test:related -- <受影响文件>
npm run api:types
npm run build
npm run preview
```

命令语义：

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 启动 Vite 开发服务 |
| `npm run typecheck` | 执行 `vue-tsc --noEmit` |
| `npm test` | 执行 Vitest 回归测试 |
| `npm run test:related -- <受影响文件>` | 只执行与改动相关的 Vitest |
| `npm run api:types` | 从 OpenAPI 重新生成前端类型 |
| `npm run build` | 先类型检查，再生成 `dist/` |
| `npm run preview` | 本地预览构建结果 |

开发代理和默认监听设置以 [`vite.config.ts`](vite.config.ts) 为准。它同时代理
`/api/v1`、`/api/v2` 和国家中断控制面；本地代理可用环境变量覆盖，但不能据此
推断生产 Nginx 或运行时身份。

## 构建产物与生产发布

`frontend/dist/` 只是一次构建输出，不等于生产已经发布。正式安装目标由
[`../deploy/lib/frontend-common.sh`](../deploy/lib/frontend-common.sh) 定义，Nginx
服务根由 [`../deploy/nginx/domeye-core.conf`](../deploy/nginx/domeye-core.conf)
定义，两者必须完全一致：

```text
/home/bgpdata/Domeye-Core-runtime/web/dist
```

前端发布必须以完整、已验哈希的构建树执行原子安装，并保留
`frontend-current`、回滚日志和上一目录。不能因为某个局部 Agent 页面较新，就用
来源不明的整个 `dist` 覆盖生产前端。

整体切换后至少真实打开并验证：

- `/`；
- `/events`，包括固定日期和查询结果；
- `/features`；
- `/countries`；
- `/ases`；
- 国家中断详情的数据观测与报告工作台。

入口 HTML、资源文件存在或单个 API 返回 200 都不足以证明前端没有回退。生产身份
必须另行通过[实时库存采集](../deploy/inventory/README.md)确认。
