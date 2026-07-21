#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR="${SCRIPT_DIR}/.."
# shellcheck source=../lib/artifact-common.sh
source "${DEPLOY_DIR}/lib/artifact-common.sh"
# shellcheck source=../lib/backend-common.sh
source "${DEPLOY_DIR}/lib/backend-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${DEPLOY_DIR}/lib/frontend-common.sh"
# shellcheck source=../lib/database-common.sh
source "${DEPLOY_DIR}/lib/database-common.sh"
# shellcheck source=../lib/release-common.sh
source "${DEPLOY_DIR}/lib/release-common.sh"

execute_gc=false
selected_release=''
older_than_days=14
expected_host=''
while (( $# > 0 )); do
    case "$1" in
        --execute)
            execute_gc=true
            shift
            ;;
        --release-id)
            [[ $# -ge 2 ]] || { printf '错误：--release-id 缺少值\n' >&2; exit 2; }
            selected_release="$2"
            shift 2
            ;;
        --older-than-days)
            [[ $# -ge 2 ]] || { printf '错误：--older-than-days 缺少值\n' >&2; exit 2; }
            older_than_days="$2"
            shift 2
            ;;
        --host)
            [[ $# -ge 2 ]] || { printf '错误：--host 缺少值\n' >&2; exit 2; }
            expected_host="$2"
            shift 2
            ;;
        --help)
            printf '用法：%s [--release-id <id>] [--older-than-days <天数>] [--execute --host <发布机>]\n' "${0##*/}"
            printf '默认仅列出候选；真正删除时必须同时提供 --execute、--release-id 和匹配的 CONFIRM_RELEASE_ID。\n'
            exit 0
            ;;
        *)
            printf '错误：未知参数：%s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if [[ ! "${older_than_days}" =~ ^[0-9]+$ || "${older_than_days}" -gt 3650 ]]; then
    domeye_artifact_error '--older-than-days 必须是 0 至 3650 的整数'
    exit 2
fi
if [[ -n "${selected_release}" ]]; then
    domeye_artifact_validate_release_id "${selected_release}"
fi
if [[ "${execute_gc}" == true ]]; then
    domeye_core_require_realtime_profile || exit 1
    domeye_release_require_root
    domeye_release_require_host "${expected_host}"
    if [[ -z "${selected_release}" \
        || "${CONFIRM_RELEASE_ID:-}" != "${selected_release}" ]]; then
        domeye_artifact_error '执行清理必须指定单个 release-id，且 CONFIRM_RELEASE_ID 必须完全一致'
        exit 2
    fi
fi

for command_name in awk cat docker find findmnt grep hostname install jq mkdir mv readlink rm sha256sum sort stat; do
    domeye_artifact_require_command "${command_name}"
done
if ! docker info >/dev/null 2>&1; then
    domeye_artifact_error '无法读取 Docker 挂载状态，GC 失败关闭'
    exit 1
fi

lock_owned=false
cleanup() {
    local exit_code=$?
    if [[ "${lock_owned}" == true ]]; then
        domeye_release_release_lock
    fi
    return "${exit_code}"
}
trap cleanup EXIT
global_lock_present=false
if [[ "${execute_gc}" == true ]]; then
    domeye_release_acquire_lock gc "${selected_release}"
    lock_owned=true
elif [[ -e "${DOMEYE_CORE_RELEASE_COMMAND_LOCK}" \
    || -L "${DOMEYE_CORE_RELEASE_COMMAND_LOCK}" ]]; then
    global_lock_present=true
fi

declare -A candidate_ids=()
declare -A protected_ids=()
record_candidate_id() {
    local release_id="$1"
    if domeye_artifact_validate_release_id "${release_id}" >/dev/null 2>&1; then
        candidate_ids["${release_id}"]=1
    else
        printf '保护：发现无法识别 release-id 的候选路径，不自动处理：%s\n' "${release_id}" >&2
    fi
}
record_state_id() {
    local state_path="$1"
    local value
    if [[ -f "${state_path}" && ! -L "${state_path}" ]]; then
        value="$(<"${state_path}")"
        if domeye_artifact_validate_release_id "${value}" >/dev/null 2>&1; then
            protected_ids["${value}"]=1
        fi
    fi
}

record_state_id "${DOMEYE_CORE_RELEASE_STATE_DIR}/database-current"
record_state_id "${DOMEYE_CORE_RELEASE_STATE_DIR}/info-current"
record_state_id "${DOMEYE_CORE_FRONTEND_CURRENT_STATE}"
database_previous_state="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-previous"
if [[ -f "${database_previous_state}" && ! -L "${database_previous_state}" ]]; then
    database_previous_value="$(<"${database_previous_state}")"
    if domeye_artifact_validate_release_id "${database_previous_value}" >/dev/null 2>&1; then
        protected_ids["${database_previous_value}"]=1
    elif [[ "${database_previous_value}" == "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres ]]; then
        protected_ids["$(basename -- "$(dirname -- "${database_previous_value}")")"]=1
    fi
fi
if [[ -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
    active_target="$(readlink -f -- "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")"
    if [[ "${active_target}" == "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres ]]; then
        protected_ids["$(basename -- "$(dirname -- "${active_target}")")"]=1
    fi
fi
database_journal="${DOMEYE_CORE_RELEASE_STATE_DIR}/database-rollback.json"
if [[ -f "${database_journal}" && ! -L "${database_journal}" ]] \
    && jq -e '.rollback_available == true' "${database_journal}" >/dev/null 2>&1; then
    journal_release="$(jq -r '.release_id // empty' "${database_journal}")"
    journal_previous="$(jq -r '.previous_target // empty' "${database_journal}")"
    if domeye_artifact_validate_release_id "${journal_release}" >/dev/null 2>&1; then
        protected_ids["${journal_release}"]=1
    fi
    if [[ "${journal_previous}" == "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres ]]; then
        protected_ids["$(basename -- "$(dirname -- "${journal_previous}")")"]=1
    fi
fi

shopt -s nullglob
for candidate_path in \
    "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}"/* \
    "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}"/*; do
    [[ -e "${candidate_path}" || -L "${candidate_path}" ]] || continue
    record_candidate_id "${candidate_path##*/}"
done
for candidate_path in \
    "${DOMEYE_CORE_DATABASE_WORK_ROOT}"/build-* \
    "${DOMEYE_CORE_DATABASE_WORK_ROOT}"/resume-*; do
    [[ -e "${candidate_path}" || -L "${candidate_path}" ]] || continue
    candidate_name="${candidate_path##*/}"
    candidate_release="${candidate_name#build-}"
    candidate_release="${candidate_release#resume-}"
    candidate_release="${candidate_release%-*}"
    record_candidate_id "${candidate_release}"
done
shopt -u nullglob

if [[ -n "${selected_release}" ]]; then
    candidate_ids=(["${selected_release}"]=1)
fi
if (( ${#candidate_ids[@]} == 0 )); then
    printf '没有发现候选发布目录。\n'
    exit 0
fi

path_is_in_use() {
    local candidate_path="$1"
    local candidate_real mount_output container_id mount_source
    candidate_real="$(readlink -f -- "${candidate_path}")"
    mount_output="$(findmnt -rn -o SOURCE,TARGET,OPTIONS)"
    if grep -F -- "${candidate_real}" <<< "${mount_output}" >/dev/null; then
        return 0
    fi
    while IFS= read -r container_id; do
        [[ -n "${container_id}" ]] || continue
        while IFS= read -r mount_source; do
            [[ -n "${mount_source}" ]] || continue
            if [[ "${mount_source}" == "${candidate_real}" \
                || "${mount_source}" == "${candidate_real}/"* \
                || "${candidate_real}" == "${mount_source}/"* ]]; then
                return 0
            fi
        done < <(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "${container_id}")
    done < <(docker ps -aq)
    return 1
}

candidate_paths_for_release() {
    local release_id="$1"
    local path
    for path in \
        "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}/${release_id}" \
        "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${release_id}" \
        "${DOMEYE_CORE_DATABASE_WORK_ROOT}"/build-"${release_id}"-* \
        "${DOMEYE_CORE_DATABASE_WORK_ROOT}"/resume-"${release_id}"-*; do
        [[ -e "${path}" || -L "${path}" ]] && printf '%s\n' "${path}"
    done
}

gc_count=0
while IFS= read -r release_id; do
    [[ -n "${release_id}" ]] || continue
    mapfile -t release_paths < <(candidate_paths_for_release "${release_id}")
    if (( ${#release_paths[@]} == 0 )); then
        printf '跳过：未找到 release-id 对应候选目录：%s\n' "${release_id}"
        continue
    fi
    protect_reason=''
    if [[ -n "${protected_ids[${release_id}]+yes}" ]]; then
        protect_reason='active-or-rollback-reference'
    elif [[ "${global_lock_present}" == true \
        || -e "${DOMEYE_CORE_ARTIFACT_ROOT}/releases/${release_id}/.database-build.lock" \
        || -e "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/.${release_id}.restore.lock" \
        || -e "${DOMEYE_CORE_DATABASE_WORK_ROOT}/.candidate-use-locks/${release_id}.dev-overlay.lock" \
        || -e "${DOMEYE_CORE_FRONTEND_INSTALL_LOCK}" ]]; then
        protect_reason='locked'
    fi
    state_file="$(domeye_release_state_file "${release_id}")"
    if [[ -z "${protect_reason}" && -f "${state_file}" && ! -L "${state_file}" ]]; then
        state_stage="$(jq -r '.stage // "invalid"' "${state_file}" 2>/dev/null || printf 'invalid')"
        case "${state_stage}" in
            active|activating|rolling_back|activation_failed|rollback_failed|invalid)
                protect_reason="state-${state_stage}"
                ;;
        esac
    fi
    if [[ -z "${protect_reason}" ]]; then
        for candidate_path in "${release_paths[@]}"; do
            if [[ -L "${candidate_path}" || ! -d "${candidate_path}" ]]; then
                protect_reason='unsafe-path-type'
                break
            fi
            if [[ -z "$(find "${candidate_path}" -maxdepth 0 -mtime "+${older_than_days}" -print -quit)" ]]; then
                protect_reason='retention-window'
                break
            fi
            if path_is_in_use "${candidate_path}"; then
                protect_reason='mounted-or-container-used'
                break
            fi
        done
    fi
    if [[ -n "${protect_reason}" ]]; then
        printf '保护：%s（%s）\n' "${release_id}" "${protect_reason}"
        continue
    fi

    printf '%s：%s\n' "$(if [[ "${execute_gc}" == true ]]; then printf '清理'; else printf '可清理（dry-run）'; fi)" "${release_id}"
    printf '  %s\n' "${release_paths[@]}"
    if [[ "${execute_gc}" == true ]]; then
        for candidate_path in "${release_paths[@]}"; do
            case "${candidate_path}" in
                "${DOMEYE_CORE_RELEASE_COMMAND_ROOT}/${release_id}"|"${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/${release_id}"|"${DOMEYE_CORE_DATABASE_WORK_ROOT}/build-${release_id}-"*|"${DOMEYE_CORE_DATABASE_WORK_ROOT}/resume-${release_id}-"*) ;;
                *)
                    domeye_artifact_error "GC 路径越界：${candidate_path}"
                    exit 1
                    ;;
            esac
            rm -rf -- "${candidate_path}"
        done
        gc_count=$(( gc_count + 1 ))
    fi
done < <(printf '%s\n' "${!candidate_ids[@]}" | sort)

if [[ "${execute_gc}" == true ]]; then
    printf '安全 GC 完成，清理 release-id 数量：%s\n' "${gc_count}"
else
    printf '以上仅为 dry-run；未删除任何目录。\n'
fi
