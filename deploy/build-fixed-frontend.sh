#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/artifact-common.sh
source "${SCRIPT_DIR}/lib/artifact-common.sh"
# shellcheck source=lib/data-profile.sh
source "${SCRIPT_DIR}/lib/data-profile.sh"

if (( $# != 1 )); then
    printf '用法：%s <release-id>\n' "${0##*/}" >&2
    exit 2
fi
if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'feb-mar-2026' ]]; then
    domeye_artifact_error '固定窗口前端构建只允许用于 feb-mar-2026 数据档'
    exit 1
fi

readonly RELEASE_ID="$1"
readonly PROJECT_ROOT='/home/bgpdata/Domeye-Core'
readonly FRONTEND_DIR="${PROJECT_ROOT}/frontend"
readonly NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
readonly CANDIDATE_ROOT="${PROJECT_ROOT}/var/frontend-candidates"
domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in chmod curl install mktemp; do
    domeye_artifact_require_command "${command_name}"
done
if [[ ! -x "${NODE_BIN_DIR}/node" || ! -x "${NODE_BIN_DIR}/npm" ]]; then
    domeye_artifact_error "缺少固定 Node.js：${NODE_BIN_DIR}"
    exit 1
fi

install -d -m 0750 "${CANDIDATE_ROOT}"
candidate_dist="$(mktemp -d "${CANDIDATE_ROOT}/fixed-${RELEASE_ID}.XXXXXX")"
cleanup() {
    if [[ "${candidate_dist}" == "${CANDIDATE_ROOT}/fixed-${RELEASE_ID}."* \
        && -d "${candidate_dist}" && ! -L "${candidate_dist}" ]]; then
        rm -rf -- "${candidate_dist}"
    fi
}
trap cleanup EXIT

(
    cd -- "${FRONTEND_DIR}"
    export PATH="${NODE_BIN_DIR}:/home/bgpdata/.local/bin:/usr/local/bin:/usr/bin:/bin"
    export VITE_DATA_WINDOW_START="${DOMEYE_CORE_FIXED_DATA_START/ /T}"
    export VITE_DATA_WINDOW_END="${DOMEYE_CORE_FIXED_SNAPSHOT_TIME/ /T}"
    [[ "$(node --version)" == 'v22.23.1' ]]
    npm ci
    npm test
    npm run build -- --outDir "${candidate_dist}" --emptyOutDir
)
chmod -R u=rwX,go=rX "${candidate_dist}"
"${SCRIPT_DIR}/artifacts/install-frontend-build.sh" "${candidate_dist}" "${RELEASE_ID}"
curl --fail --silent --show-error --max-time 5 'http://127.0.0.1:28471/' >/dev/null
printf '二三月固定窗口前端已原子安装：%s\n' "${RELEASE_ID}"
