#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/data-profile.sh
source "${SCRIPT_DIR}/lib/data-profile.sh"

if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'feb-mar-2026' ]]; then
    printf '错误：冻结后端只允许用于 feb-mar-2026 数据档。\n' >&2
    exit 1
fi

export DOMEYE_CORE_API_PROFILE="${DOMEYE_CORE_FIXED_API_PROFILE}"
exec "${SCRIPT_DIR}/../dev/backend/manage-dev-api.sh" "$@"
