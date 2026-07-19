#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"
# shellcheck source=../lib/data-profile.sh
source "${SCRIPT_DIR}/../lib/data-profile.sh"

domeye_core_require_realtime_profile || exit 1

if (( $# > 1 )); then
    printf '用法：%s [数据库配置]\n' "${0##*/}" >&2
    exit 2
fi

readonly DATABASE_ENV_FILE="${1:-${DOMEYE_CORE_DATABASE_CONFIG_DEFAULT}}"
readonly BACKEND_ENV='/home/bgpdata/Domeye-Core/backend/.env'
readonly BACKEND_DIR='/home/bgpdata/Domeye-Core/backend'
readonly RELEASE_STATE_DIR='/home/bgpdata/Domeye-Core/var/releases'

domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config
if [[ ! -d "${BACKEND_DIR}" || -L "${BACKEND_DIR}" ]]; then
    domeye_artifact_error "后端目录不存在或是软链接：${BACKEND_DIR}"
    exit 1
fi

if [[ -e "${BACKEND_ENV}" ]]; then
    if [[ ! -f "${BACKEND_ENV}" || -L "${BACKEND_ENV}" ]]; then
        domeye_artifact_error "拒绝覆盖非普通文件或软链接：${BACKEND_ENV}"
        exit 1
    fi
    if [[ "${DOMEYE_CORE_SKIP_BACKEND_ENV_BACKUP:-false}" != true ]]; then
        install -d -m 0750 "${RELEASE_STATE_DIR}"
        backup_path="${RELEASE_STATE_DIR}/backend-env-configure-$(date -u '+%Y%m%dT%H%M%SZ')-$$"
        install -m 0600 "${BACKEND_ENV}" "${backup_path}"
        printf '原后端配置已备份：%s\n' "${backup_path}"
    fi
fi

env_tmp="${BACKEND_DIR}/.env.tmp.$$"
{
    printf '%s\n' \
        'FLASK_CONFIG=production' \
        'HOST=127.0.0.1' \
        'PORT=28473' \
        'DEBUG=false' \
        'AUTO_INIT_DB=false' \
        'LOAD_CORE_DATA_ON_STARTUP=false' \
        'SOURCE=r' \
        'INFO_DIR=/home/bgpdata/Domeye-Core/backend/info' \
        'DB_HOST=127.0.0.1' \
        'DB_PORT=29429'
    printf 'SECRET_KEY=%s\n' "${DOMEYE_CORE_SECRET_KEY}"
    printf 'DB_NAME=%s\n' "${DOMEYE_CORE_DB_NAME}"
    printf 'DB_USER=%s\n' "${DOMEYE_CORE_DB_READER_USER}"
    printf 'DB_PASSWORD=%s\n' "${DOMEYE_CORE_DB_READER_PASSWORD}"
    printf '%s\n' \
        'MAIL_ENABLED=false' \
        'FEATURE_COUNTRY_TABLE=feature_country' \
        'FEATURE_OTHER_TABLE=feature_other' \
        'FEATURE_ASN_MONTHLY_ENABLED=true' \
        'FEATURE_ASN_OLD_SUFFIX=_old'
} > "${env_tmp}"
chmod 0600 "${env_tmp}"
mv -- "${env_tmp}" "${BACKEND_ENV}"
printf '后端生产配置已收口：%s\n' "${BACKEND_ENV}"
