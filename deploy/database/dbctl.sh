#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"

usage() {
    printf '用法：%s <up|down|status> [数据库配置]\n' "${0##*/}" >&2
}

if (( $# < 1 || $# > 2 )); then
    usage
    exit 2
fi

readonly ACTION="$1"
readonly DATABASE_ENV_FILE="${2:-${DOMEYE_CORE_DATABASE_CONFIG_DEFAULT}}"
readonly COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"

for command_name in docker jq readlink; do
    domeye_artifact_require_command "${command_name}"
done
domeye_artifact_require_regular_file "${COMPOSE_FILE}"
domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config

compose() {
    docker compose \
        --project-name domeye_core_database \
        --env-file "${DATABASE_ENV_FILE}" \
        --file "${COMPOSE_FILE}" \
        "$@"
}

validate_active_data() {
    if [[ ! -L "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}" ]]; then
        domeye_artifact_error "独立数据库活动路径不是发布软链接：${DOMEYE_CORE_DATABASE_ACTIVE_LINK}"
        return 1
    fi
    local active_target
    active_target="$(readlink -f "${DOMEYE_CORE_DATABASE_ACTIVE_LINK}")"
    if [[ "${active_target}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"*/postgres ]]; then
        domeye_artifact_error "独立数据库活动路径越界：${active_target}"
        return 1
    fi
    domeye_artifact_require_regular_file "${active_target}/PG_VERSION"
    local restore_state expected_image_id actual_image_id
    restore_state="$(dirname -- "${active_target}")/restore-state.json"
    domeye_artifact_require_regular_file "${restore_state}"
    expected_image_id="$(jq -r '.image_id' "${restore_state}")"
    actual_image_id="$(docker image inspect --format '{{.Id}}' "${DOMEYE_CORE_DB_IMAGE}" 2>/dev/null || true)"
    if [[ -z "${expected_image_id}" || "${actual_image_id}" != "${expected_image_id}" ]]; then
        domeye_artifact_error "活动发布要求的数据库镜像 ID 与当前标签不一致：${expected_image_id} != ${actual_image_id:-missing}"
        return 1
    fi
}

case "${ACTION}" in
    up)
        validate_active_data
        if [[ "$(docker inspect --format '{{.State.Running}}' "${DOMEYE_CORE_DATABASE_CONTAINER}" 2>/dev/null || true)" != 'true' ]]; then
            domeye_artifact_require_command ss
            if ss -H -ltn "sport = :${DOMEYE_CORE_DATABASE_PORT}" | grep -q .; then
                domeye_artifact_error "端口 ${DOMEYE_CORE_DATABASE_PORT} 已被其他进程占用"
                exit 1
            fi
        fi
        compose up --detach
        domeye_database_wait_container "${DOMEYE_CORE_DATABASE_CONTAINER}"
        printf '独立数据库已启动：127.0.0.1:%s\n' "${DOMEYE_CORE_DATABASE_PORT}"
        ;;
    down)
        compose down --remove-orphans
        printf '独立数据库容器已停止，数据目录未删除。\n'
        ;;
    status)
        if [[ "$(docker inspect --format '{{.State.Running}}' "${DOMEYE_CORE_DATABASE_CONTAINER}" 2>/dev/null || true)" != 'true' ]]; then
            printf '独立数据库容器：未运行\n'
            exit 1
        fi
        if ! docker exec "${DOMEYE_CORE_DATABASE_CONTAINER}" pg_isready -q -U "${DOMEYE_CORE_DB_ADMIN_USER}" -d "${DOMEYE_CORE_DB_NAME}"; then
            printf '独立数据库容器：运行中，但数据库未就绪\n'
            exit 1
        fi
        printf '独立数据库：正常（127.0.0.1:%s）\n' "${DOMEYE_CORE_DATABASE_PORT}"
        ;;
    *)
        usage
        exit 2
        ;;
esac
