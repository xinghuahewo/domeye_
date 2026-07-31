#!/usr/bin/env bash

set -Eeuo pipefail

readonly TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEPLOY_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
readonly FIXTURE_ROOT="$(mktemp -d /private/tmp/domeye-country-outage-agent-test.fixtures.XXXXXX)"

cleanup() {
    find "${FIXTURE_ROOT}" -depth -delete
}
trap cleanup EXIT

mkdir -p \
    "${FIXTURE_ROOT}/project" \
    "${FIXTURE_ROOT}/runtime/config" \
    "${FIXTURE_ROOT}/runtime/country-outage-agent/state" \
    "${FIXTURE_ROOT}/audit" \
    "${FIXTURE_ROOT}/tools/node/bin" \
    "${FIXTURE_ROOT}/inputs"
chgrp -R "$(id -g)" "${FIXTURE_ROOT}"
chmod 0700 \
    "${FIXTURE_ROOT}" \
    "${FIXTURE_ROOT}/project" \
    "${FIXTURE_ROOT}/runtime" \
    "${FIXTURE_ROOT}/runtime/config" \
    "${FIXTURE_ROOT}/runtime/country-outage-agent" \
    "${FIXTURE_ROOT}/runtime/country-outage-agent/state" \
    "${FIXTURE_ROOT}/audit" \
    "${FIXTURE_ROOT}/tools" \
    "${FIXTURE_ROOT}/tools/node" \
    "${FIXTURE_ROOT}/tools/node/bin" \
    "${FIXTURE_ROOT}/inputs"

printf 'auth\n' > "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
printf 'fixture-font\n' > "${FIXTURE_ROOT}/inputs/font.otf"
printf '#!/usr/bin/env sh\nexit 0\n' > "${FIXTURE_ROOT}/inputs/python3"
chmod 0600 "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
chmod 0400 "${FIXTURE_ROOT}/inputs/font.otf"
chmod 0500 "${FIXTURE_ROOT}/inputs/python3"
font_sha="$(sha256sum "${FIXTURE_ROOT}/inputs/font.otf" | awk '{print $1}')"

config="${FIXTURE_ROOT}/runtime/config/country-outage-agent.env"
cat > "${config}" <<EOF
COUNTRY_OUTAGE_AGENT_URL=http://127.0.0.1:28474
COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=fixture-token-abcdefghijklmnopqrstuvwxyz
COUNTRY_OUTAGE_AGENT_IDENTITY_MODE=internal_fixed_history
COUNTRY_OUTAGE_AGENT_INTERNAL_USER_ID=domeye-fixed-history
COUNTRY_OUTAGE_AGENT_NARRATOR=pi-sdk-certified
COUNTRY_OUTAGE_AGENT_HOST=127.0.0.1
COUNTRY_OUTAGE_AGENT_PORT=28474
COUNTRY_OUTAGE_AGENT_REPORT_CACHE_TTL_MS=3600000
COUNTRY_OUTAGE_AGENT_PYTHON_BOOTSTRAP=${FIXTURE_ROOT}/inputs/python3
DOMEYE_API_BASE_URL=http://127.0.0.1:28473/api/v2/
DOMEYE_API_TIMEOUT_MS=5000
DOMEYE_REPORT_PYTHON_EXECUTABLE=${FIXTURE_ROOT}/runtime/country-outage-agent/current/pdf-venv/bin/python
DOMEYE_REPORT_FONT_PATH=${FIXTURE_ROOT}/inputs/font.otf
DOMEYE_REPORT_FONT_SHA256=${font_sha}
DOMEYE_REPORT_PDF_TIMEOUT_MS=45000
COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH=${FIXTURE_ROOT}/runtime/country-outage-agent/current/agent-sidecar/resources/certified-models/country-outage-pi-models-v1.json
COUNTRY_OUTAGE_PI_PROFILE=deepseek-v4-flash-pi-0.82.1-v1
COUNTRY_OUTAGE_PI_AUTH_PATH=${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json
COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY=${FIXTURE_ROOT}/audit
EOF
chmod 0600 "${config}"

DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_validate_config >/dev/null

if DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" bash -c '
    set -Eeuo pipefail
    source "$1/lib/common.sh"
    ss() { return 42; }
    coa_require_port_free
' _ "${DEPLOY_DIR}" >/dev/null 2>&1; then
    printf '失败：ss 查询失败被误判为 28474 空闲\n' >&2
    exit 1
fi

if DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" bash -c '
    set -Eeuo pipefail
    source "$1/lib/common.sh"
    coa_list_sessions() { printf "123.domeye_country_outage_agent\n"; }
    coa_require_no_managed_sessions
' _ "${DEPLOY_DIR}" >/dev/null 2>&1; then
    printf '失败：残留受管 Screen 被误判为已清空\n' >&2
    exit 1
fi

if DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" bash -c '
    set -Eeuo pipefail
    source "$1/lib/common.sh"
    screen() { return 2; }
    coa_require_no_managed_sessions
' _ "${DEPLOY_DIR}" >/dev/null 2>&1; then
    printf '失败：screen -ls 查询失败被误判为无受管会话\n' >&2
    exit 1
fi

printf 'UNAUTHORIZED_KEY=value\n' >> "${config}"
if DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_validate_config >/dev/null 2>&1; then
    printf '失败：未拒绝未授权配置键\n' >&2
    exit 1
fi
sed -i.bak '$d' "${config}"
find "${FIXTURE_ROOT}/runtime/config" -name '*.bak' -delete

release_id='20260730T140000Z-country-outage-agent-core-fixture'
release_dir="${FIXTURE_ROOT}/runtime/country-outage-agent/releases/${release_id}"
mkdir -p "${release_dir}/pdf-venv/bin"
printf 'payload\n' > "${release_dir}/payload.txt"
printf '#!/usr/bin/env sh\nexit 0\n' > "${release_dir}/pdf-venv/bin/python"
chmod 0500 "${release_dir}/pdf-venv/bin/python"
cat > "${release_dir}/RELEASE-MANIFEST.json" <<EOF
{"schema_version":1,"component":"country_outage_agent_sidecar","release_id":"${release_id}","created_at":"2026-07-30T14:00:00Z","git_sha":"0000000000000000000000000000000000000000","data_profile":"feb-mar-2026","collector":"rrc25","country_scope":"IR","external_evidence":"disabled","node_version":"v22.23.1","pi_version":"0.82.1","model_profile":"deepseek-v4-flash-pi-0.82.1-v1","hashes":{"pdf_runtime":"0000000000000000000000000000000000000000000000000000000000000000"},"font_sha256":"${font_sha}"}
EOF

DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" bash -c \
    'set -Eeuo pipefail; source "$1/lib/common.sh"; coa_write_release_checksums "$2"' \
    _ "${DEPLOY_DIR}" "${release_dir}"
DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_verify_release "${release_id}" >/dev/null

printf 'tampered\n' >> "${release_dir}/payload.txt"
if DOMEYE_COUNTRY_OUTAGE_AGENT_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_verify_release "${release_id}" >/dev/null 2>&1; then
    printf '失败：未拒绝 release 文件篡改\n' >&2
    exit 1
fi

printf 'fixture tests passed\n'
