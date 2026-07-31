#!/usr/bin/env bash

# 从当前工作树构建“国家中断 Agent 获批叠加包”。
#
# 该制品不是完整代码 release，不能独立启动或部署。它必须由后续受控编排器
# 叠加到一个已验证的实时不可变生产归档，再对组合结果重新生成全量 manifest、
# 复验 backend/core，并经过 canary、联合健康检查和回滚门。

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"
readonly OUTPUT_ROOT="${REPOSITORY_ROOT}/artifacts/country-outage-agent/approved-overlays"

OVERLAY_ID=''
STAGING_DIR=''
TEMPORARY_ARCHIVE=''

error() {
    printf '错误：%s\n' "$*" >&2
}

info() {
    printf '%s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        error "缺少命令：$1"
        return 1
    }
}

cleanup() {
    if [[ -n "${STAGING_DIR}" && -d "${STAGING_DIR}" && ! -L "${STAGING_DIR}" ]]; then
        case "${STAGING_DIR}" in
            "${OUTPUT_ROOT}/.build-"*)
                chmod -R u+w "${STAGING_DIR}" 2>/dev/null || true
                find "${STAGING_DIR}" -depth -delete 2>/dev/null || true
                ;;
        esac
    fi
    if [[ -n "${TEMPORARY_ARCHIVE}" \
        && -f "${TEMPORARY_ARCHIVE}" \
        && ! -L "${TEMPORARY_ARCHIVE}" ]]; then
        case "${TEMPORARY_ARCHIVE}" in
            "${OUTPUT_ROOT}/."*.tar.gz.tmp)
                unlink "${TEMPORARY_ARCHIVE}" 2>/dev/null || true
                ;;
        esac
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
用法：
  build-approved-overlay.sh [--overlay-id <id>]

说明：
  只从当前工作树复制脚本内冻结的白名单。输出到：
  artifacts/country-outage-agent/approved-overlays/

  本命令不提交 Git、不访问远程、不读取秘密目录、不修改生产。
EOF
}

parse_arguments() {
    while (( $# > 0 )); do
        case "$1" in
            --overlay-id)
                if (( $# < 2 )); then
                    error '--overlay-id 缺少值'
                    return 2
                fi
                OVERLAY_ID="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                error "未知参数：$1"
                usage >&2
                return 2
                ;;
        esac
    done
}

validate_overlay_id() {
    local value="$1"
    if (( ${#value} < 24 || ${#value} > 96 )) \
        || [[ ! "${value}" =~ ^[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z-country-outage-agent-approved-overlay-[a-z0-9][a-z0-9-]*$ ]]; then
        error "overlay-id 格式无效：${value}"
        return 1
    fi
}

validate_relative_path() {
    local relative="$1"
    if [[ -z "${relative}" \
        || "${relative}" == /* \
        || "${relative}" == '..' \
        || "${relative}" == ../* \
        || "${relative}" == */../* \
        || "${relative}" == */.. \
        || "${relative}" == *$'\n'* \
        || "${relative}" == *$'\r'* ]]; then
        error "白名单包含非规范相对路径：${relative}"
        return 1
    fi
}

copy_approved_file() {
    local relative="$1"
    local source target target_directory
    validate_relative_path "${relative}" || return 1
    source="${REPOSITORY_ROOT}/${relative}"
    target="${STAGING_DIR}/overlay/${relative}"
    target_directory="$(dirname -- "${target}")"

    if [[ ! -f "${source}" || -L "${source}" ]]; then
        error "获批文件不存在、不是普通文件或是符号链接：${relative}"
        return 1
    fi
    install -d -m 0755 "${target_directory}"
    cp -p "${source}" "${target}"
}

copy_approved_tree() {
    local relative="$1"
    local source file file_relative
    validate_relative_path "${relative}" || return 1
    source="${REPOSITORY_ROOT}/${relative}"
    if [[ ! -d "${source}" || -L "${source}" ]]; then
        error "获批目录不存在、不是目录或是符号链接：${relative}"
        return 1
    fi
    if find "${source}" -type l -print -quit | grep -q .; then
        error "获批目录包含符号链接，拒绝跟随：${relative}"
        return 1
    fi
    while IFS= read -r file; do
        file_relative="${file#"${REPOSITORY_ROOT}/"}"
        copy_approved_file "${file_relative}"
    done < <(find "${source}" -type f -print | LC_ALL=C sort)
}

copy_approved_payload() {
    local relative

    # Agent Sidecar：运行源、测试源、固定资源、构建合同和受控补丁。
    # 明确不复制 node_modules、dist、coverage、缓存和测试输出。
    for relative in \
        agent-sidecar/src \
        agent-sidecar/tests \
        agent-sidecar/scripts \
        agent-sidecar/resources \
        agent-sidecar/vendor-patches; do
        copy_approved_tree "${relative}"
    done
    for relative in \
        agent-sidecar/package.json \
        agent-sidecar/package-lock.json \
        agent-sidecar/tsconfig.json \
        agent-sidecar/.gitignore \
        agent-sidecar/.env.formal-pi.example; do
        copy_approved_file "${relative}"
    done

    # Python 控制面只包含 Agent 身份、窄代理、两处显式装配及受影响测试。
    # backend/core、数据库、迁移、数据制品和无关 backend 测试均不进入包。
    for relative in \
        backend/.env.example \
        backend/web/country_outage_agent_identity.py \
        backend/web/api/v2/country_outage_agent_proxy.py \
        backend/web/api/v2/route.py \
        backend/web/flask_app.py \
        backend/web/tests/test_core_app.py \
        backend/web/tests/test_openapi_contract.py \
        backend/web/tests/test_country_outage_agent_identity.py \
        backend/web/tests/test_country_outage_agent_proxy.py \
        dev/backend/manage-dev-api.sh; do
        copy_approved_file "${relative}"
    done

    # 核心验收合同和正式 API/事实合同；外部证据能力包仍未部署，不进入本包。
    for relative in \
        config/country-outage-agent-acceptance-v2.json \
        config/country-outage-agent-core-acceptance-v3.json \
        contracts/agent/country-outage-report-facts-v1.schema.json \
        contracts/openapi.json; do
        copy_approved_file "${relative}"
    done

    # 风险分级、受影响测试和只读生产库存采集用于组合归档后的逐文件闭环。
    for relative in \
        dev/checks.py \
        dev/tests/test_checks.py \
        dev/tests/test_server_lifecycle.py \
        dev/tests/test_production_runtime_inventory.py; do
        copy_approved_file "${relative}"
    done
    copy_approved_tree deploy/inventory

    # 生命周期、fixture 测试源与核心 A5 防偏离合同；不复制测试输出。
    for relative in \
        deploy/country-outage-agent/README.md \
        deploy/country-outage-agent/country-outage-agent.env.example \
        deploy/country-outage-agent/lib/common.sh \
        deploy/country-outage-agent/manage.sh \
        deploy/country-outage-agent/prepare.sh \
        deploy/country-outage-agent/start.sh \
        deploy/country-outage-agent/stop.sh \
        deploy/country-outage-agent/status.sh \
        deploy/country-outage-agent/rollback.sh \
        deploy/country-outage-agent/probe-sidecar.mjs \
        deploy/country-outage-agent/verify-formal-release.mjs \
        deploy/country-outage-agent/requirements-pdf.txt \
        deploy/country-outage-agent/build-approved-overlay.sh; do
        copy_approved_file "${relative}"
    done
    copy_approved_tree deploy/country-outage-agent/tests
    for relative in \
        .codex/hooks/country_outage_agent_review.py \
        docs/国家中断报告与追问Agent最终验收文档.md \
        docs/国家中断报告与追问Agent分阶段计划.md \
        docs/国家中断报告与追问AgentA0基线.md \
        docs/国家中断Agent正式Pi审计日志运维说明.md \
        docs/国家中断Agent正式身份入口运维说明.md \
        docs/国家中断报告AgentDeepSeek价格证明门禁.md \
        docs/国家中断报告AgentDeepSeek模型认证运行说明.md \
        docs/国家中断报告与追问AgentA1验收记录.md \
        docs/国家中断报告与追问AgentA2验收记录.md \
        docs/国家中断报告与追问AgentA3验收记录.md \
        docs/国家中断报告与追问AgentA4阶段回检记录.md \
        docs/国家中断报告与追问AgentA5联合验收记录.md \
        docs/国家中断报告与追问Agent依赖风险例外批准记录.md; do
        copy_approved_file "${relative}"
    done

    # 前端带本任务明确修改/新增的源码与测试，支持组合后 Git archive 逐文件闭环。
    # dist 仍作为单独的前端激活输入保留；不带 node_modules 或 frontend/tmp。
    for relative in \
        frontend/src/api/events.test.ts \
        frontend/src/api/events.ts \
        frontend/src/api/countryOutageAgent.test.ts \
        frontend/src/api/countryOutageAgent.ts \
        frontend/src/components/CountryOutageDashboard.vue \
        frontend/src/components/CountryOutageReportWorkbench.test.ts \
        frontend/src/components/CountryOutageReportWorkbench.vue \
        frontend/src/pages/EventDetailPage.vue \
        frontend/src/styles/accessibilityColors.test.ts \
        frontend/src/styles/main.css \
        frontend/src/types/openapi.generated.d.ts \
        frontend/src/utils/countryOutageReport.test.ts \
        frontend/src/utils/countryOutageReport.ts \
        frontend/src/utils/countryOutageRuntime.test.ts \
        frontend/src/utils/countryOutageRuntime.ts \
        frontend/src/utils/normalize.test.ts \
        frontend/src/utils/normalize.ts \
        frontend/vite.config.ts; do
        copy_approved_file "${relative}"
    done
    copy_approved_tree frontend/dist
}

validate_payload_boundary() {
    local payload="${STAGING_DIR}/overlay"
    local forbidden_pattern

    if find "${payload}" -type l -print -quit | grep -q .; then
        error '叠加包不得包含符号链接'
        return 1
    fi

    forbidden_pattern='/(backend/core|node_modules|__pycache__|\.pytest_cache|\.cache|coverage|frontend/tmp|output|tmp|artifacts)(/|$)'
    if find "${payload}" -mindepth 1 -print \
        | sed "s#^${payload}##" \
        | grep -E "${forbidden_pattern}" >/dev/null; then
        error '叠加包命中 backend/core、依赖、缓存或输出排除边界'
        return 1
    fi

    if find "${payload}" -type f \
        \( -name '.env' -o -name '*.pem' -o -name '*.key' \
           -o -name '*.p12' -o -name '*.pfx' -o -name '.DS_Store' \
           -o -name '*.map' \) -print -quit | grep -q .; then
        error '叠加包包含环境秘密、私钥、系统杂项或 sourcemap'
        return 1
    fi

    # 只检查典型真实密钥/私钥形态；变量名、示例占位符和认证路径不构成秘密。
    if LC_ALL=C grep -ERIl \
        'sk-[A-Za-z0-9]{20,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' \
        "${payload}" | grep -q .; then
        error '叠加包内容疑似包含真实 API Key 或私钥'
        return 1
    fi
}

write_boundary_document() {
    cat > "${STAGING_DIR}/边界说明.md" <<'EOF'
# 国家中断 Agent 获批叠加包边界说明

## 制品身份

这是从本地当前工作树按固定白名单生成的“获批叠加包”，不是 Domeye 完整生产
归档，不具备独立部署、独立启动、直接覆盖线上目录或直接切换 `current` 的资格。

## 唯一允许的用途

后续发布编排必须先取得正在使用的、只读复验通过的实时不可变生产归档，再把本包
作为一层显式 overlay 组合进去。组合过程必须：

1. 绑定基础归档的 release-id、完整 SHA-256 和运行身份；
2. 验证基础归档中的 `backend/core` 与数据库/数据制品没有被本包替换；
3. 对共享装配文件逐项审查冲突，禁止无条件 `cp -R` 覆盖；
4. 对组合后的完整树重新生成 manifest 和全文件 SHA256SUMS；
5. 重新执行核心回归、Sidecar readiness、Python 控制面联合健康检查、
   同一认证 profile 的一次真实核心报告冒烟；
6. 先 canary，保留基础归档与前端树回滚点，再允许原子切换。

基础归档未绑定、任一组合冲突未解决、`backend/core` 摘要变化，或组合后完整门禁
未通过时，必须失败关闭。本包本身的 SHA256SUMS 只证明本包闭包，不证明组合结果、
生产身份、运行状态或部署成功。

## 已包含

- 国家中断 Agent Sidecar 的运行源、固定 Skill/模型/风险资源、精确依赖 lockfile
  和受控 vendor patch，以及回归测试源；
- Python 控制面的 Agent 身份、只读窄代理与显式装配文件；
- Python 控制面、OpenAPI、风险分级和生命周期的受影响测试源；
- 国家中断 Agent 生命周期脚本、配置样例、核心验收合同和事实/API 合同；
- 生命周期 fixture 测试源（不含任何测试输出）；
- A0 至 A5 核心合同、验收记录和运维说明；
- 本任务获批的前端源码与测试，以及作为独立激活输入的当前 `frontend/dist`；
- 只读生产库存采集脚本、中文说明和测试。

## 明确未包含

- 实时不可变生产基础归档及其 release-id；
- `backend/core`、数据库、迁移、数据文件、模型密钥、共享 token、生产配置、
  审计日志、会话、报告下载件或任何用户数据；
- `node_modules`、Sidecar 编译输出、测试输出、缓存、coverage、
  `frontend/tmp`、sourcemap；
- 外部证据能力包的启用配置、证书、Evidence Gateway 或公开网络能力；Sidecar
  源码树中与正式核心依赖图隔离的未启用模块不代表该能力已经部署；
- Git 提交、远程发布、生产切换、canary 或回滚执行结果。

`DELETIONS.txt` 记录组合时必须显式处理的获批删除项；它不是可直接执行的删除
脚本。编排器只能在已绑定的基础归档副本内逐项核对并删除，不能把该清单用于工作树、
生产 current 或其他宽目录。

## 固定业务边界

本包只服务于国家中断观测报告与事件内追问：唯一 collector 为 RRC25，当前正式
固定历史身份只允许 IR 事件只读 scope；外部证据保持 `disabled/not_configured`。
不得据此扩展成任意国家、任意时间、多 collector、通用 RCA、归因、处置或写入。
EOF
}

write_deletions() {
    cat > "${STAGING_DIR}/DELETIONS.txt" <<'EOF'
config/country-outage-agent-acceptance-v1.json
EOF
}

tree_digest() {
    local root="$1"
    (
        cd -- "${root}"
        find . -type f -print \
            | sed 's#^\./##' \
            | LC_ALL=C sort \
            | while IFS= read -r relative; do
                sha256sum "${relative}"
            done \
            | sha256sum \
            | awk '{print $1}'
    )
}

write_metadata() {
    local git_head git_status dirty payload_file_count payload_bytes
    local payload_digest frontend_digest core_manifest_digest
    git_head="$(git -C "${REPOSITORY_ROOT}" rev-parse --verify HEAD)"
    git_status="$(git -C "${REPOSITORY_ROOT}" status --porcelain --untracked-files=all)"
    if [[ -n "${git_status}" ]]; then
        dirty=true
    else
        dirty=false
    fi
    payload_file_count="$(
        find "${STAGING_DIR}/overlay" -type f -print | wc -l | awk '{$1=$1; print}'
    )"
    payload_bytes="$(
        while IFS= read -r file; do
            wc -c < "${file}"
        done < <(find "${STAGING_DIR}/overlay" -type f -print) \
            | awk '{sum += $1} END {print sum + 0}'
    )"
    payload_digest="$(tree_digest "${STAGING_DIR}/overlay")"
    frontend_digest="$(tree_digest "${STAGING_DIR}/overlay/frontend/dist")"
    core_manifest_digest="$(sha256sum "${REPOSITORY_ROOT}/backend/core.sha256" | awk '{print $1}')"

    jq -n \
        --arg schema_version 'domeye_country_outage_agent_approved_overlay_v1' \
        --arg overlay_id "${OVERLAY_ID}" \
        --arg created_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        --arg source_git_head "${git_head}" \
        --argjson source_worktree_dirty "${dirty}" \
        --arg capture_mode 'approved_path_whitelist_from_current_worktree' \
        --arg payload_sha256 "${payload_digest}" \
        --arg frontend_dist_sha256 "${frontend_digest}" \
        --arg source_backend_core_manifest_sha256 "${core_manifest_digest}" \
        --argjson payload_file_count "${payload_file_count}" \
        --argjson payload_bytes "${payload_bytes}" \
        '{
          schema_version: $schema_version,
          artifact_kind: "approved_overlay_not_standalone_release",
          overlay_id: $overlay_id,
          created_at: $created_at,
          standalone_deployable: false,
          production_activation_authorized: false,
          source: {
            git_head: $source_git_head,
            worktree_dirty: $source_worktree_dirty,
            capture_mode: $capture_mode
          },
          payload: {
            file_count: $payload_file_count,
            bytes: $payload_bytes,
            combined_sha256: $payload_sha256,
            frontend_dist_sha256: $frontend_dist_sha256
          },
          required_base_archive: {
            required: true,
            class: "live_realtime_immutable_production_archive",
            release_id: null,
            archive_sha256: null,
            composition_status: "unbound",
            direct_overlay_copy_allowed: false
          },
          boundaries: {
            collector: "rrc25",
            country_scope: "IR",
            external_evidence: "disabled",
            backend_core_included: false,
            database_or_data_artifacts_included: false,
            secrets_or_runtime_config_included: false,
            node_modules_included: false,
            test_sources_included: true,
            test_outputs_included: false
          },
          approved_deletions: {
            count: 1,
            manifest: "DELETIONS.txt",
            apply_only_to_bound_base_archive_copy: true
          },
          source_checks: {
            backend_core_manifest_sha256_only: $source_backend_core_manifest_sha256,
            note: "该摘要仅记录本地来源，不能替代实时基础归档的独立 readback"
          },
          required_next_gate: [
            "bind_base_release_id_and_archive_sha256",
            "compose_without_backend_core_or_data_changes",
            "resolve_shared_file_conflicts",
            "regenerate_full_archive_manifest_and_sha256sums",
            "run_affected_core_regression",
            "run_sidecar_and_backend_joint_health",
            "run_one_real_certified_core_report_smoke",
            "canary_then_atomic_activation_with_rollback"
          ]
        }' > "${STAGING_DIR}/METADATA.json"
    chmod 0644 "${STAGING_DIR}/METADATA.json"
}

write_lists_and_checksums() {
    (
        cd -- "${STAGING_DIR}/overlay"
        find . -type f -print | sed 's#^\./##' | LC_ALL=C sort
    ) > "${STAGING_DIR}/APPROVED-PATHS.txt"

    (
        cd -- "${STAGING_DIR}"
        find . -type f ! -name SHA256SUMS -print \
            | sed 's#^\./##' \
            | LC_ALL=C sort \
            | while IFS= read -r relative; do
                sha256sum "${relative}"
            done
    ) > "${STAGING_DIR}/SHA256SUMS"
    (
        cd -- "${STAGING_DIR}"
        sha256sum -c SHA256SUMS >/dev/null
    )
}

verify_closed_file_set() {
    if ! diff -u \
        <(
            {
                cut -c 67- "${STAGING_DIR}/SHA256SUMS"
                printf 'SHA256SUMS\n'
            } | LC_ALL=C sort
        ) \
        <(
            cd -- "${STAGING_DIR}"
            find . -type f -print | sed 's#^\./##' | LC_ALL=C sort
        ) >/dev/null; then
        error '叠加包文件集合与 SHA256SUMS 不闭合'
        return 1
    fi
}

main() {
    parse_arguments "$@"
    local command_name git_short final_directory final_archive archive_sha
    for command_name in awk cp cut date diff find git grep install jq \
        mktemp sed sha256sum sort stat tar unlink wc; do
        require_command "${command_name}"
    done
    if [[ ! -d "${REPOSITORY_ROOT}/.git" || -L "${REPOSITORY_ROOT}" ]]; then
        error "项目根目录不是安全的 Git 检出：${REPOSITORY_ROOT}"
        return 1
    fi
    if [[ -z "${OVERLAY_ID}" ]]; then
        git_short="$(git -C "${REPOSITORY_ROOT}" rev-parse --short=12 HEAD)"
        OVERLAY_ID="$(date -u '+%Y%m%dT%H%M%SZ')-country-outage-agent-approved-overlay-${git_short}"
    fi
    validate_overlay_id "${OVERLAY_ID}"

    install -d -m 0755 "${OUTPUT_ROOT}"
    local output_parent
    for output_parent in \
        "${REPOSITORY_ROOT}/artifacts" \
        "${REPOSITORY_ROOT}/artifacts/country-outage-agent" \
        "${OUTPUT_ROOT}"; do
        if [[ ! -d "${output_parent}" || -L "${output_parent}" ]]; then
            error "输出路径不是实际目录或经过受管末端符号链接：${output_parent}"
            return 1
        fi
    done
    (
        cd -- "${REPOSITORY_ROOT}/backend"
        sha256sum -c core.sha256 >/dev/null
    ) || {
        error '当前工作树 backend/core 已偏离冻结摘要，拒绝构建叠加包'
        return 1
    }
    final_directory="${OUTPUT_ROOT}/${OVERLAY_ID}"
    final_archive="${OUTPUT_ROOT}/${OVERLAY_ID}.tar.gz"
    if [[ -e "${final_directory}" || -L "${final_directory}" \
        || -e "${final_archive}" || -L "${final_archive}" \
        || -e "${final_archive}.sha256" || -L "${final_archive}.sha256" ]]; then
        error "同名叠加包已经存在，拒绝覆盖：${OVERLAY_ID}"
        return 1
    fi

    STAGING_DIR="$(mktemp -d "${OUTPUT_ROOT}/.build-${OVERLAY_ID}.XXXXXX")"
    chmod 0755 "${STAGING_DIR}"
    install -d -m 0755 "${STAGING_DIR}/overlay"

    copy_approved_payload
    validate_payload_boundary
    write_boundary_document
    write_deletions
    write_metadata
    write_lists_and_checksums
    verify_closed_file_set

    mv -- "${STAGING_DIR}" "${final_directory}"
    STAGING_DIR=''

    TEMPORARY_ARCHIVE="${OUTPUT_ROOT}/.${OVERLAY_ID}.tar.gz.tmp"
    COPYFILE_DISABLE=1 tar -C "${OUTPUT_ROOT}" -czf "${TEMPORARY_ARCHIVE}" "${OVERLAY_ID}"
    mv -- "${TEMPORARY_ARCHIVE}" "${final_archive}"
    TEMPORARY_ARCHIVE=''
    archive_sha="$(sha256sum "${final_archive}" | awk '{print $1}')"
    printf '%s  %s\n' "${archive_sha}" "$(basename -- "${final_archive}")" \
        > "${final_archive}.sha256"

    (
        cd -- "${final_directory}"
        sha256sum -c SHA256SUMS >/dev/null
    )
    (
        cd -- "${OUTPUT_ROOT}"
        sha256sum -c "$(basename -- "${final_archive}.sha256")" >/dev/null
    )

    info "获批叠加包已生成：${final_directory}"
    info "归档：${final_archive}"
    info "归档 SHA256：${archive_sha}"
    info '状态：未绑定实时不可变生产基础归档；不是完整生产包，禁止独立部署。'
}

main "$@"
