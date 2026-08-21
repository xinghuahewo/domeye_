"""国家中断 Agent 的窄身份入口。"""

from __future__ import annotations

import ipaddress
import os
import re
import unicodedata

from flask import request


IDENTITY_MODE_ENV = "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE"
INTERACTIVE_IDENTITY_MODE_ENV = (
    "COUNTRY_OUTAGE_INTERACTIVE_AGENT_IDENTITY_MODE"
)
WSGI_REMOTE_USER_MODE = "wsgi_remote_user"
INTERNAL_FIXED_HISTORY_MODE = "internal_fixed_history"
INTERNAL_USER_ID_ENV = "COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID"
INTERACTIVE_INTERNAL_USER_ID_ENV = (
    "COUNTRY_OUTAGE_INTERACTIVE_AGENT_INTERNAL_USER_ID"
)
TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY = (
    "domeye.country_outage_authorization_scope"
)

_AGENT_PATH_PREFIX = "/api/v2/country-outage/"
_INTERACTIVE_AGENT_PATH_PREFIX = "/api/v2/country-outage/chat/"
_DOMEYE_USER_KEY = "domeye.authenticated_user_id"
_DOMEYE_SCOPE_KEY = "domeye.authorization_scope"
_INTERNAL_IR_READ_SCOPE = "country_outage_event_read:IR"
_COUNTRY_OUTAGE_SCOPE = re.compile(
    r"^country_outage_event_read(?::[A-Z]{2})?$"
)
_SAFE_INTERNAL_USER_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$"
)


def _is_loopback_address(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _normalized_remote_user(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        return None
    return normalized


def _validated_internal_user_id(value: object) -> str | None:
    """校验内部固定历史观测模式的单用户服务身份。

    该身份来自进程环境而不是请求。只接受短 ASCII 标识符，避免空白、
    控制字符、路径分隔符和日志换行进入下游身份审计。
    """

    if not isinstance(value, str) or not _SAFE_INTERNAL_USER_ID.fullmatch(value):
        return None
    return value


def _normalized_authorization_scope(value: object) -> str | None:
    """只接受国家中断只读能力，拒绝把其他角色或管理员 scope 带入 Sidecar。"""

    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.split(",")]
    if not parts or len(parts) > 64 or any(not part for part in parts):
        return None
    if any(not _COUNTRY_OUTAGE_SCOPE.fullmatch(part) for part in parts):
        return None
    unique = set(parts)
    if "country_outage_event_read" in unique:
        normalized = "country_outage_event_read"
    else:
        normalized = ",".join(sorted(unique))
    if len(normalized) > 512:
        return None
    return normalized


def inject_country_outage_agent_principal() -> None:
    """为国家中断 Agent 的显式身份模式构造现有身份上下文。

    此函数不读取任何 ``HTTP_*`` 身份或权限头。默认不启用。内部固定
    历史观测模式只使用进程环境中的单用户标识，并固定授予 IR 事件只读
    scope；WSGI 模式继续要求受信任中间件逐请求提供身份和 ACL 结果。
    """

    if not request.path.startswith(_AGENT_PATH_PREFIX):
        return

    if request.path.startswith(_INTERACTIVE_AGENT_PATH_PREFIX):
        mode_env = INTERACTIVE_IDENTITY_MODE_ENV
        internal_user_env = INTERACTIVE_INTERNAL_USER_ID_ENV
    else:
        # 旧报告和组合调查只保留模块级回归；正式路由已退役。它们不能借用
        # 新 Chat 的身份键重新进入生产，但历史测试仍可验证旧边界。
        mode_env = IDENTITY_MODE_ENV
        internal_user_env = INTERNAL_USER_ID_ENV

    mode = os.environ.get(mode_env, "").strip()
    environ = request.environ

    if mode == INTERNAL_FIXED_HISTORY_MODE:
        # 该模式的 principal 必须完全来自冻结进程配置。清除请求进入时可能
        # 已存在的身份上下文，防止 WSGI 值绕过回环、固定用户或固定 IR scope。
        environ.pop(_DOMEYE_USER_KEY, None)
        environ.pop(_DOMEYE_SCOPE_KEY, None)
        if not _is_loopback_address(environ.get("REMOTE_ADDR")):
            return
        user_id = _validated_internal_user_id(
            os.environ.get(internal_user_env)
        )
        if user_id is None:
            return
        environ[_DOMEYE_USER_KEY] = user_id
        environ[_DOMEYE_SCOPE_KEY] = _INTERNAL_IR_READ_SCOPE
        return

    if mode != WSGI_REMOTE_USER_MODE:
        return

    # 保持已认证 WSGI 中间件直接注入 domeye.* 上下文的兼容性。任一键已
    # 存在时均不拼接或覆盖，避免把两个不同认证来源混合成一个 principal。
    if _DOMEYE_USER_KEY in environ or _DOMEYE_SCOPE_KEY in environ:
        return
    if not _is_loopback_address(environ.get("REMOTE_ADDR")):
        return

    user_id = _normalized_remote_user(environ.get("REMOTE_USER"))
    authorization_scope = _normalized_authorization_scope(
        environ.get(TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY)
    )
    if user_id is None or authorization_scope is None:
        return

    environ[_DOMEYE_USER_KEY] = user_id
    # scope 必须由完成事件 ACL 决策的受信任 WSGI 层逐请求写入专用 environ
    # 键。HTTP 请求头只会落入 HTTP_* 键，不能伪造这里的授权结果。
    environ[_DOMEYE_SCOPE_KEY] = authorization_scope
