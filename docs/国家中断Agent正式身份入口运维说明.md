# 国家中断 Agent 正式身份入口运维说明

## 1. 结论与边界

国家中断 Agent 有两个互斥的窄身份模式。面向正式多用户环境的链路为：

```text
浏览器
  ↓
受信任反向代理 / OIDC 认证层
  ↓ 仅在服务端 WSGI 环境写入身份与事件 ACL 决策
Domeye Python 控制面
  ↓ 转发最小 country_outage_event_read[:CC] 范围
国家中断 Agent Sidecar
```

该入口只为 `/api/v2/country-outage/` 下的报告、追问、事件流、取消和制品下载接口注入身份，不影响其他 Domeye 路由。它不读取 `X-Remote-User`、`Remote-User`、`X-Domeye-User` 或任何浏览器提供的权限头。

本地代码与自动化测试只证明“窄适配器按合同工作”，**不等于生产 OIDC、反向代理、TLS、会话管理或身份旅程已经验收**。

当前固定伊朗历史观测环境尚无 WSGI/OIDC 身份层时，可临时使用
`internal_fixed_history` 单用户模式：

```text
浏览器
  ↓ 普通 Nginx
回环 Flask（REMOTE_ADDR 必须是 127.0.0.1 或 ::1）
  ↓ 进程环境中的固定单用户 ID + 代码固定 IR 只读 scope
国家中断 Agent Sidecar
```

该模式不是通用认证方案，不读取用户登录态，不提供用户隔离，不得暴露为公网 Flask
入口，也不得扩展到其他国家、其他事件类型或其他 Domeye 路由。

## 2. 模式一：正式 WSGI/OIDC

正式环境必须同时满足：

1. 受信任 OIDC/WSGI 层已经完成登录、令牌或会话验证；
2. 认证层与 Flask 进程位于同一受控主机，并从回环地址访问 Flask；
3. 认证层在 WSGI 环境中写入标准 `REMOTE_USER`，并把已完成的事件 ACL 决策写入
   专用键 `domeye.country_outage_authorization_scope`，而不是把身份或权限放入 HTTP
   请求头；
4. 设置 `COUNTRY_OUTAGE_AGENT_IDENTITY_MODE=wsgi_remote_user`；
5. 不配置任何静态验收用户或静态验收 scope；旧的
   `COUNTRY_OUTAGE_AGENT_ACCEPTANCE_MODE`、`COUNTRY_OUTAGE_AGENT_ACCEPTANCE_USER_ID`
   和 `COUNTRY_OUTAGE_AGENT_ACCEPTANCE_SCOPE` 已不再被程序读取；
6. Sidecar 地址与内部凭据继续遵循本机、最小权限配置。

只有精确选择 `wsgi_remote_user` 才会执行本节映射；选择
`internal_fixed_history` 时改按下一节构造固定内部身份，留空、拼写错误或其他值
均不启用身份。WSGI 模式下的非回环来源、缺失身份、控制字符身份或超过 256 字符
的身份都会按未认证请求拒绝。

## 3. 模式二：内部固定历史观测

只有当前内部、固定伊朗历史事件观测环境可显式配置：

```text
COUNTRY_OUTAGE_AGENT_IDENTITY_MODE=internal_fixed_history
COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID=internal-history-observer
```

该模式必须同时满足：

1. Nginx 与 Flask 位于同一受控主机，Flask 只监听回环地址；
2. Flask 看到的 `REMOTE_ADDR` 必须可解析为 IPv4 或 IPv6 回环地址；
3. `COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID` 必须匹配
   `[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}`，不允许空白、控制字符、路径分隔符、
   非 ASCII 字符或超过 128 字符；
4. scope 不从环境、WSGI 或 HTTP 请求读取，而是固定为
   `country_outage_event_read:IR`；
5. 只允许现有 `/api/v2/country-outage/` Agent 路由使用该身份，不为其他 Flask
   路由注入 principal。

模式未显式设置、用户 ID 缺失或非法、请求不是回环来源时，适配器不会创建
principal，现有代理按未认证请求返回 `401 authentication_required`。即使请求携带
`X-Domeye-User`、`X-Domeye-Authorization-Scope`、`Remote-User` 等头，或者请求
environ 中已经存在其他 principal，也不能覆盖固定用户、扩大 IR scope 或绕过回环
限制。

该模式解决的是“普通 Nginx → Flask 没有 WSGI/OIDC 注入时所有 Agent 请求均为
401”的当前内部接线问题。它不证明生产身份、ACL 或多用户隔离已经完成。接入正式
身份系统时必须切换到 `wsgi_remote_user` 并删除静态内部用户配置。

## 4. 身份与权限语义

`wsgi_remote_user` 模式仅进行以下映射：

```text
REMOTE_USER
  → domeye.authenticated_user_id

domeye.country_outage_authorization_scope
  → domeye.authorization_scope
```

授权键只接受 `country_outage_event_read` 或
`country_outage_event_read:<两位大写国家码>`，可用逗号组合；缺失、空值、其他角色、
写权限或格式非法均失败关闭。客户端不能扩大 scope，普通 HTTP 头只会进入
`HTTP_*` WSGI 键，不能伪造这个专用键。现有受信任 WSGI 中间件若已经同时注入
`domeye.authenticated_user_id` 和 `domeye.authorization_scope`，适配器不会覆盖，保持
既有集成兼容；若只注入其中一项，适配器不会拼接另一个认证来源，请求仍会失败关闭。

WSGI 层负责把 Domeye 的用户级事件 ACL 决策投影为最小国家范围；Sidecar 再次核对
请求事件国家是否落在该范围，并确认它属于已有合法 `country_outage`。生成、追问、
SSE、取消和下载会在每次访问时重新核对创建者、原授权范围与事件权限。

`internal_fixed_history` 模式不执行上述 WSGI 映射：它只读取进程环境中的固定用户
ID，强制使用 IR 只读 scope，并在写入前丢弃请求进入时已有的 Domeye principal。

## 5. 反向代理注意事项

普通 Nginx `proxy_set_header` 只能生成 HTTP 头，不能等价为受信任的 WSGI `REMOTE_USER`。即使把 `X-Remote-User` 或 `Remote-User` 转发给 Flask，本实现也会忽略。

生产接入应由已经验证 OIDC 身份并查询当前 Domeye 事件 ACL 的本地 WSGI 中间件、
认证网关适配器或应用服务器认证模块，在完成验签、发行方、受众、时效、会话和授权
校验后直接写入 WSGI environ。不得把公网请求中的同名头直接改名后当作
`REMOTE_USER` 或授权结果。

内部固定模式下，Nginx 不得传递或改写身份头。Flask 必须只在回环监听，且 Nginx
到 Flask 的实际源地址仍为回环；如果经过容器网桥、远端代理或其他非回环网络，该
模式会按设计失败关闭，不能通过信任 `X-Forwarded-For` 放宽。

Sidecar 的事实 API 固定使用回环 Flask v2 地址：

```text
DOMEYE_API_BASE_URL=http://127.0.0.1:28473/api/v2/
```

当前外部能力固定为 `disabled/not_configured`：核心报告、追问和下载不访问外部
网络，不读取外部 URL，也不生成外部附录。未来部署外部能力包时必须独立配置、
独立审计和独立验收，不得通过身份模式顺带开启。

## 6. 上线前证据

生产验收至少还需补齐：

- 实际身份提供方、issuer、audience 与 claim 映射的冻结配置；
- 反向代理至 Flask 的真实进程、端口或 Unix socket 拓扑证据；
- 正常登录、过期会话、退出、无身份、非回环和浏览器头欺骗的端到端记录；
- Sidecar 收到的用户标识与最小只读 scope 审计记录，且无令牌、Cookie 或 API Key；
- 同一合法事件下有权限用户成功、无权限用户为 403，且报告标识、SSE、问答和下载
  都不能旁路事件 ACL；
- 生产运行身份、日志留存和回滚证据。

在这些证据完成前，只能表述为“正式身份入口代码候选已实现并通过本地测试”，不能表述为“生产 OIDC 已接通”或“生产身份验收通过”。

内部固定模式的部署证据应单独表述为“当前内部固定历史观测身份已启用”，并至少
包含：实际 Flask 回环监听、Nginx 到 Flask 的回环源地址、非法用户 ID 和非回环
401、HTTP 头欺骗无效、Sidecar 收到固定用户与固定 IR 只读 scope、其他路由未注入
身份。它不能替代上面的正式身份验收。
