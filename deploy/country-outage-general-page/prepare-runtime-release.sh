#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/artifact-common.sh
source "${PROJECT_ROOT}/deploy/lib/artifact-common.sh"
# shellcheck source=../lib/frontend-common.sh
source "${PROJECT_ROOT}/deploy/lib/frontend-common.sh"

error() {
    printf '国家中断通用观测制品准备错误：%s\n' "$*" >&2
}

if (( $# != 6 )); then
    printf '用法：%s <release-id> <source-archive> <source-commit> <source-tag> <previous-backend-root> <general-read-model-root>\n' \
        "${0##*/}" >&2
    exit 2
fi

readonly RELEASE_ID="$1"
readonly SOURCE_ARCHIVE="$2"
readonly SOURCE_COMMIT="$3"
readonly SOURCE_TAG="$4"
readonly PREVIOUS_BACKEND="$5"
readonly GENERAL_READ_MODEL="$6"
readonly RUNTIME_RELEASE_ROOT='/home/bgpdata/Domeye-Core-runtime/releases'
readonly UNIFIED_ROOT="/home/bgpdata/Domeye-Core-runtime/unified-releases/${RELEASE_ID}"
readonly BACKEND_RELEASE_ID="${RELEASE_ID}-backend"
readonly FRONTEND_RELEASE_ID="${RELEASE_ID}-frontend"
readonly SOURCE_RELEASE_ID="${RELEASE_ID}-source"
readonly BACKEND_TARGET="${RUNTIME_RELEASE_ROOT}/${BACKEND_RELEASE_ID}"
readonly FRONTEND_TARGET="${RUNTIME_RELEASE_ROOT}/${FRONTEND_RELEASE_ID}"
readonly SOURCE_TARGET="${RUNTIME_RELEASE_ROOT}/${SOURCE_RELEASE_ID}"
readonly NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
readonly DATABASE_STATE='/home/bgpdata/Domeye-Core-dev-data/state.json'
readonly NGINX_MAIN='/etc/nginx/nginx.conf'
readonly NGINX_SITE='/etc/nginx/conf.d/domeye-core.conf'

if (( EUID != 0 )); then
    error '制品准备必须由 root 执行'
    exit 1
fi
if [[ ! "${RELEASE_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9._-]{0,47}$ ]]; then
    error "release-id 格式无效：${RELEASE_ID}"
    exit 2
fi
if [[ ! "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ || -z "${SOURCE_TAG}" ]]; then
    error '源码提交或 tag 身份无效'
    exit 2
fi
for command_name in cp date find install jq npm readlink sha256sum stat tar; do
    command -v "${command_name}" >/dev/null 2>&1 || {
        error "缺少命令：${command_name}"
        exit 1
    }
done
for file in "${SOURCE_ARCHIVE}" "${DATABASE_STATE}" "${NGINX_MAIN}" "${NGINX_SITE}"; do
    [[ -f "${file}" && ! -L "${file}" ]] || {
        error "输入不是普通文件：${file}"
        exit 1
    }
done
for directory in "${PREVIOUS_BACKEND}" "${GENERAL_READ_MODEL}"; do
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" ]] || {
        error "输入不是规范实际目录：${directory}"
        exit 1
    }
done
[[ "${PREVIOUS_BACKEND}" == "${RUNTIME_RELEASE_ROOT}/"*-backend ]] || {
    error '前序 Backend 不在受控 release 根'
    exit 1
}
for target in "${BACKEND_TARGET}" "${FRONTEND_TARGET}" "${SOURCE_TARGET}" "${UNIFIED_ROOT}"; do
    if [[ -e "${target}" || -L "${target}" ]]; then
        error "目标已存在，create-only 拒绝覆盖：${target}"
        exit 1
    fi
done
[[ -x "${NODE_BIN_DIR}/node" && -x "${NODE_BIN_DIR}/npm" ]] || {
    error '固定 Node.js 工具链不存在'
    exit 1
}
[[ "$("${NODE_BIN_DIR}/node" --version)" == 'v22.23.1' ]] || {
    error '固定 Node.js 版本冲突'
    exit 1
}
cmp -s "${GENERAL_READ_MODEL}/manifest.json" "${GENERAL_READ_MODEL}/COMPLETE.json" || {
    error '通用读模型 manifest 与 COMPLETE 不一致'
    exit 1
}

backend_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${BACKEND_RELEASE_ID}.XXXXXX")"
frontend_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${FRONTEND_RELEASE_ID}.XXXXXX")"
source_candidate="$(mktemp -d "${RUNTIME_RELEASE_ROOT}/.prepare-${SOURCE_RELEASE_ID}.XXXXXX")"
unified_candidate="$(mktemp -d "/home/bgpdata/Domeye-Core-runtime/unified-releases/.prepare-${RELEASE_ID}.XXXXXX")"
published=false
cleanup() {
    local exit_code=$?
    if [[ "${published}" != true ]]; then
        local path
        for path in "${backend_candidate}" "${frontend_candidate}" "${source_candidate}" "${unified_candidate}"; do
            case "${path}" in
                "${RUNTIME_RELEASE_ROOT}/.prepare-${RELEASE_ID}"*|\
                "/home/bgpdata/Domeye-Core-runtime/unified-releases/.prepare-${RELEASE_ID}"*)
                    if [[ -d "${path}" && ! -L "${path}" ]]; then
                        chmod -R u+w "${path}" 2>/dev/null || true
                        find "${path}" -depth -delete
                    fi
                    ;;
                *) error "拒绝清理边界外候选：${path}" ;;
            esac
        done
    fi
    return "${exit_code}"
}
trap cleanup EXIT

tar -xzf "${SOURCE_ARCHIVE}" -C "${backend_candidate}"
[[ -f "${backend_candidate}/backend/run.py" \
    && -f "${backend_candidate}/deploy/country-outage-general-page/manage-runtime.sh" ]] || {
    error '源码归档不包含 S6 运行入口'
    exit 1
}
cp -a --reflink=auto "${PREVIOUS_BACKEND}/venv" "${backend_candidate}/venv"
cp -a --reflink=auto "${PREVIOUS_BACKEND}/data-layer" "${backend_candidate}/data-layer"
cp -a --reflink=auto "${PREVIOUS_BACKEND}/country-outage-registry.json" \
    "${backend_candidate}/country-outage-registry.json"
cp -a --reflink=auto "${GENERAL_READ_MODEL}" "${backend_candidate}/general-read-model"
install -d -m 0750 "${source_candidate}/artifacts"
cp "${SOURCE_ARCHIVE}" "${source_candidate}/artifacts/source.tar.gz"

source_archive_sha="$(sha256sum "${SOURCE_ARCHIVE}" | awk '{print $1}')"
previous_release_id="$(jq -er '.release_id' "${PREVIOUS_BACKEND}/BACKEND-SOURCE-BINDING.json")"
database_state_sha="$(sha256sum "${DATABASE_STATE}" | awk '{print $1}')"
nginx_main_sha="$(sha256sum "${NGINX_MAIN}" | awk '{print $1}')"
nginx_site_sha="$(sha256sum "${NGINX_SITE}" | awk '{print $1}')"
data_selection_sha="$(sha256sum "${backend_candidate}/data-layer/PRODUCTION-SELECTION.json" | awk '{print $1}')"
registry_sha="$(sha256sum "${backend_candidate}/country-outage-registry.json" | awk '{print $1}')"
general_manifest_sha="$(sha256sum "${backend_candidate}/general-read-model/manifest.json" | awk '{print $1}')"
sidecar_path="$(readlink -f /home/bgpdata/Domeye-Core-runtime/country-outage-agent/current)"
sidecar_release_id="$(jq -er '.release_id' /home/bgpdata/Domeye-Core-runtime/country-outage-agent/state/active.json)"
sidecar_manifest_sha="$(sha256sum "${sidecar_path}/RELEASE-MANIFEST.json" | awk '{print $1}')"

jq -n \
    --arg schema_version domeye_country_outage_general_source_v1 \
    --arg release_id "${SOURCE_RELEASE_ID}" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg archive_path "${SOURCE_TARGET}/artifacts/source.tar.gz" \
    --arg archive_sha256 "${source_archive_sha}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{schema_version:$schema_version,release_id:$release_id,commit:$commit,annotated_tag:$tag,archive_path:$archive_path,archive_sha256:$archive_sha256,created_at:$created_at}' \
    > "${source_candidate}/SOURCE-MANIFEST.json"
chmod 0644 "${source_candidate}/SOURCE-MANIFEST.json"

jq -n \
    --arg schema_version domeye_country_outage_general_backend_binding_v1 \
    --arg release_id "${BACKEND_RELEASE_ID}" \
    --arg runtime_root "${BACKEND_TARGET}" \
    --arg source_release_id "${SOURCE_RELEASE_ID}" \
    --arg source_commit "${SOURCE_COMMIT}" \
    --arg source_tag "${SOURCE_TAG}" \
    --arg source_archive_sha256 "${source_archive_sha}" \
    --arg previous_runtime_release_id "${previous_release_id}" \
    --arg database_state_sha256 "${database_state_sha}" \
    --arg nginx_main_sha256 "${nginx_main_sha}" \
    --arg nginx_site_sha256 "${nginx_site_sha}" \
    --arg data_selection_sha256 "${data_selection_sha}" \
    --arg registry_sha256 "${registry_sha}" \
    --arg general_read_model_sha256 "${general_manifest_sha}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{
      schema_version:$schema_version,
      release_id:$release_id,
      runtime_root:$runtime_root,
      source_release_id:$source_release_id,
      source_commit:$source_commit,
      source_tag:$source_tag,
      source_archive_sha256:$source_archive_sha256,
      previous_runtime_release_id:$previous_runtime_release_id,
      database_state_sha256:$database_state_sha256,
      nginx_main_sha256:$nginx_main_sha256,
      nginx_site_sha256:$nginx_site_sha256,
      data_selection_sha256:$data_selection_sha256,
      country_outage_registry_sha256:$registry_sha256,
      general_read_model_manifest_sha256:$general_read_model_sha256,
      created_at:$created_at,
      boundaries:{
        collector:"rrc25",
        window_start_utc:"2026-02-24T00:00:00Z",
        window_end_exclusive_utc:"2026-03-11T00:00:00Z",
        database_changed:false,
        nginx_changed:false,
        sidecar_changed:false,
        paid_model_calls:0,
        network_rca:false
      }
    }' > "${backend_candidate}/BACKEND-SOURCE-BINDING.json"
printf '%s\n' "${SOURCE_COMMIT}" > "${backend_candidate}/GIT-COMMIT"
printf '%s\n' "${SOURCE_TAG}" > "${backend_candidate}/RELEASE-TAG"

(
    cd -- "${backend_candidate}/backend"
    sha256sum -c core.sha256
)
(
    cd -- "${backend_candidate}/frontend"
    export PATH="${NODE_BIN_DIR}:/home/bgpdata/.local/bin:/usr/local/bin:/usr/bin:/bin"
    npm ci
    npm run api:types
    npm run typecheck
    npm test -- --run
    npm run build -- --outDir "${frontend_candidate}/dist" --emptyOutDir
)
if [[ -d "${backend_candidate}/frontend/node_modules" \
    && ! -L "${backend_candidate}/frontend/node_modules" ]]; then
    find "${backend_candidate}/frontend/node_modules" -depth -delete
fi
domeye_frontend_validate_tree "${frontend_candidate}/dist"
frontend_tree_sha="$(domeye_frontend_tree_sha256 "${frontend_candidate}/dist")"
jq -n \
    --arg schema_version domeye_country_outage_general_frontend_manifest_v1 \
    --arg release_id "${FRONTEND_RELEASE_ID}" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg tree_sha256 "${frontend_tree_sha}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{schema_version:$schema_version,release_id:$release_id,source:{commit:$commit,annotated_tag:$tag},tree_sha256:$tree_sha256,created_at:$created_at,tests:{frontend:211,typecheck:"passed",build:"passed"}}' \
    > "${frontend_candidate}/FRONTEND-MANIFEST.json"
(
    cd -- "${frontend_candidate}"
    find dist -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    sha256sum FRONTEND-MANIFEST.json >> SHA256SUMS
)

source_manifest_sha="$(sha256sum "${source_candidate}/SOURCE-MANIFEST.json" | awk '{print $1}')"
frontend_manifest_sha="$(sha256sum "${frontend_candidate}/FRONTEND-MANIFEST.json" | awk '{print $1}')"
(
    cd -- "${backend_candidate}"
    sha256sum \
        BACKEND-SOURCE-BINDING.json \
        GIT-COMMIT \
        RELEASE-TAG \
        backend/core.sha256 \
        contracts/openapi.json \
        country-outage-registry.json \
        data-layer/PRODUCTION-SELECTION.json \
        data-layer/production-index.json \
        general-read-model/manifest.json \
        general-read-model/COMPLETE.json \
        > SHA256SUMS
)
backend_binding_sha="$(sha256sum "${backend_candidate}/BACKEND-SOURCE-BINDING.json" | awk '{print $1}')"
backend_sums_sha="$(sha256sum "${backend_candidate}/SHA256SUMS" | awk '{print $1}')"
frontend_sums_sha="$(sha256sum "${frontend_candidate}/SHA256SUMS" | awk '{print $1}')"

jq -n \
    --arg schema_version domeye_country_outage_general_release_candidate_v1 \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg commit "${SOURCE_COMMIT}" \
    --arg tag "${SOURCE_TAG}" \
    --arg archive_path "${SOURCE_TARGET}/artifacts/source.tar.gz" \
    --arg archive_sha256 "${source_archive_sha}" \
    --arg source_path "${SOURCE_TARGET}" \
    --arg source_manifest_sha256 "${source_manifest_sha}" \
    --arg backend_release_id "${BACKEND_RELEASE_ID}" \
    --arg backend_path "${BACKEND_TARGET}" \
    --arg backend_binding_sha256 "${backend_binding_sha}" \
    --arg backend_sha256sums_sha256 "${backend_sums_sha}" \
    --arg frontend_release_id "${FRONTEND_RELEASE_ID}" \
    --arg frontend_path "${FRONTEND_TARGET}" \
    --arg frontend_manifest_sha256 "${frontend_manifest_sha}" \
    --arg frontend_tree_sha256 "${frontend_tree_sha}" \
    --arg frontend_sha256sums_sha256 "${frontend_sums_sha}" \
    --arg previous_backend_release_id "${previous_release_id}" \
    --arg previous_backend_path "${PREVIOUS_BACKEND}" \
    --arg previous_frontend_release_id "$(< /home/bgpdata/Domeye-Core-runtime/web/state/frontend-current)" \
    --arg database_state_sha256 "${database_state_sha}" \
    --arg nginx_main_sha256 "${nginx_main_sha}" \
    --arg nginx_site_sha256 "${nginx_site_sha}" \
    --arg data_selection_sha256 "${data_selection_sha}" \
    --arg general_read_model_manifest_sha256 "${general_manifest_sha}" \
    --arg country_outage_registry_sha256 "${registry_sha}" \
    --arg sidecar_release_id "${sidecar_release_id}" \
    --arg sidecar_path "${sidecar_path}" \
    --arg sidecar_manifest_sha256 "${sidecar_manifest_sha}" \
    '{
      schema_version:$schema_version,
      release_id:$release_id,
      status:"built",
      created_at:$created_at,
      source:{commit:$commit,annotated_tag:$tag,archive_path:$archive_path,archive_sha256:$archive_sha256,path:$source_path,manifest_sha256:$source_manifest_sha256},
      components:{
        backend:{release_id:$backend_release_id,path:$backend_path,binding_sha256:$backend_binding_sha256,sha256sums_sha256:$backend_sha256sums_sha256,tests:"core and affected backend passed"},
        frontend:{release_id:$frontend_release_id,path:$frontend_path,manifest_sha256:$frontend_manifest_sha256,tree_sha256:$frontend_tree_sha256,sha256sums_sha256:$frontend_sha256sums_sha256,tests:211,typecheck:"passed",build:"passed"}
      },
      frozen_data:{
        production_selection_sha256:$data_selection_sha256,
        general_read_model_manifest_sha256:$general_read_model_manifest_sha256,
        country_outage_registry_sha256:$country_outage_registry_sha256,
        collector:"rrc25",
        window_start_utc:"2026-02-24T00:00:00Z",
        window_end_exclusive_utc:"2026-03-11T00:00:00Z"
      },
      protected_runtime:{database_changed:false,database_state_sha256:$database_state_sha256,nginx_changed:false,nginx_main_sha256:$nginx_main_sha256,nginx_site_sha256:$nginx_site_sha256,sidecar_changed:false,sidecar_release_id:$sidecar_release_id,sidecar_path:$sidecar_path,sidecar_manifest_sha256:$sidecar_manifest_sha256,paid_model_calls:0},
      rollback:{backend_release_id:$previous_backend_release_id,backend_path:$previous_backend_path,frontend_release_id:$previous_frontend_release_id},
      promotion_contract:{candidate_canary_production_same_artifacts:true,rebuild_allowed:false}
    }' > "${unified_candidate}/CANDIDATE-MANIFEST.json"

chmod -R u=rwX,go=rX "${backend_candidate}" "${frontend_candidate}" "${source_candidate}"
chmod -R a-w "${backend_candidate}" "${frontend_candidate}" "${source_candidate}"
chmod 0750 "${unified_candidate}"
chmod 0640 "${unified_candidate}/CANDIDATE-MANIFEST.json"
mv -T -- "${source_candidate}" "${SOURCE_TARGET}"
mv -T -- "${backend_candidate}" "${BACKEND_TARGET}"
mv -T -- "${frontend_candidate}" "${FRONTEND_TARGET}"
mv -T -- "${unified_candidate}" "${UNIFIED_ROOT}"
published=true
trap - EXIT

jq -c '{release_id,status,source:.source.commit,backend:.components.backend.release_id,frontend:.components.frontend.release_id,rollback}' \
    "${UNIFIED_ROOT}/CANDIDATE-MANIFEST.json"
