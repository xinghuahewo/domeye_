#!/usr/bin/env bash

set -Eeuo pipefail

readonly TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEPLOY_DIR="$(cd -- "${TEST_DIR}/.." && pwd -P)"
readonly FIXTURE_ROOT="$(mktemp -d /private/tmp/domeye-country-outage-p1-test.fixtures.XXXXXX)"

cleanup() { find "${FIXTURE_ROOT}" -depth -delete; }
trap cleanup EXIT

mkdir -p "${FIXTURE_ROOT}/runtime/config" "${FIXTURE_ROOT}/audit" \
    "${FIXTURE_ROOT}/tools/node/bin" "${FIXTURE_ROOT}/tools/bin"
chmod 0700 "${FIXTURE_ROOT}" "${FIXTURE_ROOT}/runtime" \
    "${FIXTURE_ROOT}/runtime/config" "${FIXTURE_ROOT}/audit" \
    "${FIXTURE_ROOT}/tools" "${FIXTURE_ROOT}/tools/node" \
    "${FIXTURE_ROOT}/tools/node/bin" "${FIXTURE_ROOT}/tools/bin"
for command_name in flock screen ss; do
    printf '#!/usr/bin/env sh\nexit 0\n' > "${FIXTURE_ROOT}/tools/bin/${command_name}"
    chmod 0500 "${FIXTURE_ROOT}/tools/bin/${command_name}"
done
printf 'auth\n' > "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"
chmod 0600 "${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json"

config="${FIXTURE_ROOT}/runtime/config/country-outage-p1-chat.env"
cat > "${config}" <<EOF
COUNTRY_OUTAGE_AGENT_URL=http://127.0.0.1:28475
COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=fixture-token-abcdefghijklmnopqrstuvwxyz
COUNTRY_OUTAGE_AGENT_HOST=127.0.0.1
COUNTRY_OUTAGE_AGENT_PORT=28475
DOMEYE_API_BASE_URL=http://127.0.0.1:28473/api/v2/
COUNTRY_OUTAGE_P1_API_TIMEOUT_MS=15000
COUNTRY_OUTAGE_P1_MODEL_TIMEOUT_MS=75000
COUNTRY_OUTAGE_P1_TURN_TIMEOUT_MS=110000
COUNTRY_OUTAGE_PI_CERTIFIED_REGISTRY_PATH=${FIXTURE_ROOT}/runtime/country-outage-p1-chat/current/agent-sidecar/resources/certified-models/country-outage-p1-semantic-models-v1.json
COUNTRY_OUTAGE_PI_PROFILE=deepseek-v4-flash-pi-0.84.1-v1
COUNTRY_OUTAGE_PI_AUTH_PATH=${FIXTURE_ROOT}/runtime/config/country-outage-pi-auth.json
COUNTRY_OUTAGE_PI_AUDIT_DIRECTORY=${FIXTURE_ROOT}/audit
EOF
chmod 0600 "${config}"
chgrp -R "$(id -g)" "${FIXTURE_ROOT}"

PATH="${FIXTURE_ROOT}/tools/bin:${PATH}" \
DOMEYE_COUNTRY_OUTAGE_P1_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_validate_config

printf 'COUNTRY_OUTAGE_P1_BUSINESS_COST_LIMIT=1\n' >> "${config}"
if PATH="${FIXTURE_ROOT}/tools/bin:${PATH}" \
    DOMEYE_COUNTRY_OUTAGE_P1_TEST_ROOT="${FIXTURE_ROOT}" \
    "${DEPLOY_DIR}/manage.sh" _test_validate_config >/dev/null 2>&1; then
    printf '失败：P1 配置接受了业务总费用上限或未授权键\n' >&2
    exit 1
fi
sed -i.bak '$d' "${config}"
find "${FIXTURE_ROOT}" -name '*.bak' -delete

if PATH="${FIXTURE_ROOT}/tools/bin:${PATH}" \
    DOMEYE_COUNTRY_OUTAGE_P1_TEST_ROOT='/tmp/not-authorized' \
    "${DEPLOY_DIR}/manage.sh" _test_validate_config >/dev/null 2>&1; then
    printf '失败：P1 生命周期接受了边界外测试根\n' >&2
    exit 1
fi

promotion_root="${FIXTURE_ROOT}/p2-promotion"
p2_contract_root='contracts/agent/country-outage-p2-s0b-runtime'
mkdir -p "${promotion_root}/${p2_contract_root}" \
    "${promotion_root}/certification/p2-s0b"
cp -R "${DEPLOY_DIR}/../../../${p2_contract_root}/." \
    "${promotion_root}/${p2_contract_root}/"
cp "${DEPLOY_DIR}/../../../evaluation/country-outage/p2-s0b-runtime/acceptance-manifest.json" \
    "${promotion_root}/certification/p2-s0b/acceptance-manifest.json"
cp "${DEPLOY_DIR}/../../../evaluation/country-outage/p2-s0b-runtime/product-semantic-review.json" \
    "${promotion_root}/certification/p2-s0b/product-semantic-review.json"
while IFS= read -r relative_path; do
    mkdir -p "${promotion_root}/$(dirname -- "${relative_path}")"
    cp "${DEPLOY_DIR}/../../../${relative_path}" \
        "${promotion_root}/${relative_path}"
done < <(
    jq -r '
      .source_identity.runtime_material[]?.path,
      (.snapshot_payload.execution_unit_registry.entries[]?.implementation_files[]?.path)
    ' \
      "${promotion_root}/${p2_contract_root}/candidate.json" \
      "${promotion_root}/${p2_contract_root}/registry-snapshot.json" | sort -u
)
node "${DEPLOY_DIR}/promote-p2-registry.mjs" \
    "${promotion_root}" \
    '20260811T120000Z-country-outage-p1-chat-fixture' \
    '0123456789abcdef0123456789abcdef01234567' \
    '20260811T120000Z-country-outage-p1-chat-fixture' \
    '20260811T043105Z-country-outage-p1-chat-prod32' >/dev/null
jq -e '
  .production_deployed == true and
  .snapshot_payload.activation_scope == "production_active" and
  .snapshot_payload.runtime_integration == "deployed" and
  (.snapshot_payload.candidate_id | test("^p2-s0b6-[a-f0-9]{16}$"))
' "${promotion_root}/${p2_contract_root}/registry-snapshot.json" >/dev/null
if node "${DEPLOY_DIR}/promote-p2-registry.mjs" \
    "${promotion_root}" \
    '20260811T120000Z-country-outage-p1-chat-fixture' \
    '0123456789abcdef0123456789abcdef01234567' \
    '20260811T120000Z-country-outage-p1-chat-fixture' \
    '20260811T043105Z-country-outage-p1-chat-prod32' >/dev/null 2>&1; then
    printf '失败：P2 production promotion 接受了已晋级快照的重复晋级\n' >&2
    exit 1
fi

printf 'P1 Chat deployment fixtures passed\n'
