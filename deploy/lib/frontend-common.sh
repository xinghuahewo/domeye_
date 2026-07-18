#!/usr/bin/env bash

# 前端原子安装和回滚脚本的共用校验。调用方必须先启用 set -Eeuo pipefail，
# 并先加载 artifact-common.sh。
readonly DOMEYE_CORE_FRONTEND_PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly DOMEYE_CORE_FRONTEND_TARGET="${DOMEYE_CORE_FRONTEND_PROJECT_ROOT}/frontend/dist"
readonly DOMEYE_CORE_FRONTEND_STATE_DIR="${DOMEYE_CORE_FRONTEND_PROJECT_ROOT}/var/releases"
readonly DOMEYE_CORE_FRONTEND_CURRENT_STATE="${DOMEYE_CORE_FRONTEND_STATE_DIR}/frontend-current"
readonly DOMEYE_CORE_FRONTEND_ROLLBACK_JOURNAL="${DOMEYE_CORE_FRONTEND_STATE_DIR}/frontend-rollback.json"
readonly DOMEYE_CORE_FRONTEND_INSTALL_STATUS="${DOMEYE_CORE_FRONTEND_STATE_DIR}/frontend-install-status.json"
readonly DOMEYE_CORE_FRONTEND_INSTALL_LOCK="${DOMEYE_CORE_FRONTEND_STATE_DIR}/frontend-install.lock"

domeye_frontend_validate_tree() {
    local tree_path="$1"
    local unexpected_path symlink_path

    if [[ ! -d "${tree_path}" || -L "${tree_path}" ]]; then
        domeye_artifact_error "前端目录不存在、不是实际目录或是软链接：${tree_path}"
        return 1
    fi
    if [[ ! -f "${tree_path}/index.html" || -L "${tree_path}/index.html" ]]; then
        domeye_artifact_error "前端目录缺少普通 index.html：${tree_path}"
        return 1
    fi
    symlink_path="$(find "${tree_path}" -type l -print -quit)"
    if [[ -n "${symlink_path}" ]]; then
        domeye_artifact_error "前端目录包含软链接：${symlink_path}"
        return 1
    fi
    unexpected_path="$(find "${tree_path}" ! -type d ! -type f -print -quit)"
    if [[ -n "${unexpected_path}" ]]; then
        domeye_artifact_error "前端目录包含目录和普通文件以外的对象：${unexpected_path}"
        return 1
    fi
}

# 对排序后的“相对路径 NUL 文件哈希 NUL”字节流再做一次 SHA256。
# 目录权限、所有者和时间戳不参与哈希；空目录不影响 Nginx 实际提供的内容。
domeye_frontend_tree_sha256() {
    local tree_path="$1"

    domeye_frontend_validate_tree "${tree_path}"
    (
        cd -- "${tree_path}"
        find . -type f -print0 \
            | LC_ALL=C sort -z \
            | while IFS= read -r -d '' relative_path; do
                printf '%s\0' "${relative_path#./}"
                printf '%s\0' "$(domeye_artifact_sha256 "${relative_path}")"
            done
    ) | sha256sum | awk '{print $1}'
}

domeye_frontend_require_safe_generated_path() {
    local actual_path="$1"
    local expected_prefix="$2"
    local suffix_pattern="$3"
    local suffix

    suffix="${actual_path#"${expected_prefix}"}"
    if [[ "${actual_path}" != "${expected_prefix}"* || "${suffix}" == */* \
        || ! "${suffix}" =~ ${suffix_pattern} ]]; then
        domeye_artifact_error "前端状态记录了越界或命名无效的路径：${actual_path}"
        return 1
    fi
}
