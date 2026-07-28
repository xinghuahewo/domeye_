#!/usr/bin/env bash

# static INFO 候选库导入共用函数。调用方必须先加载 artifact-common.sh。

domeye_static_info_assert_offline_candidate() {
    local container_name="$1"
    local candidate_role

    if ! candidate_role="$(
        docker inspect \
            --format '{{ index .Config.Labels "domeye.core.database-role" }}' \
            -- "${container_name}" 2>/dev/null
    )"; then
        domeye_artifact_error "候选容器不存在：${container_name}"
        return 1
    fi
    if [[ "${candidate_role}" != "offline-candidate" ]]; then
        domeye_artifact_error \
            "拒绝连接未标记为 offline-candidate 的数据库容器：${container_name}"
        return 1
    fi
}

domeye_static_info_python() {
    local repository_root="$1"
    local candidate="${DOMEYE_CORE_INFO_PYTHON:-${repository_root}/backend/.venv/bin/python}"
    if [[ -x "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    command -v python3
}

domeye_static_info_archive_incomplete_evidence() {
    local evidence_dir="$1"
    local archived_path="${evidence_dir}.incomplete.$(date -u '+%Y%m%dT%H%M%SZ').$$"
    if [[ -e "${archived_path}" || -L "${archived_path}" ]]; then
        domeye_artifact_error "INFO 未完成证据归档目标已存在：${archived_path}"
        return 1
    fi
    mv -- "${evidence_dir}" "${archived_path}"
    chmod -R go-rwx "${archived_path}"
    printf '已保留上次未完成的 INFO 证据：%s\n' "${archived_path}" >&2
}

domeye_static_info_reuse_s1_evidence() {
    local repository_root="$1"
    local source_info_dir="$2"
    local release_id="$3"
    local container_name="$4"
    local database_user="$5"
    local database_name="$6"
    local evidence_dir="$7"
    local required_names=(
        static-info-manifest.json
        static-info-quality.json
        static-info-load-result.json
        stage-gate-S0.json
        stage-gate-S1.json
        SHA256SUMS
    )
    local name

    [[ -e "${evidence_dir}" || -L "${evidence_dir}" ]] || return 1
    if [[ -L "${evidence_dir}" || ! -d "${evidence_dir}" ]]; then
        domeye_artifact_error "S1 证据路径不是实际目录：${evidence_dir}"
        return 2
    fi
    for name in "${required_names[@]}"; do
        if [[ ! -f "${evidence_dir}/${name}" || -L "${evidence_dir}/${name}" ]]; then
            domeye_static_info_archive_incomplete_evidence "${evidence_dir}"
            return 1
        fi
    done
    if ! (
        cd -- "${evidence_dir}"
        sha256sum -c SHA256SUMS
    ) >/dev/null; then
        domeye_artifact_error "S1 证据校验和不匹配，拒绝覆盖：${evidence_dir}"
        return 2
    fi

    local verify_dir
    verify_dir="$(mktemp -d "${evidence_dir%/*}/.static-info-s1-verify.XXXXXX")"
    local current_manifest="${verify_dir}/static-info-manifest.json"
    local current_receipt="${evidence_dir}/.stage-gate-S1-reverify.$$"
    local python_bin
    python_bin="$(domeye_static_info_python "${repository_root}")"
    if [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
        rm -rf -- "${verify_dir}"
        domeye_artifact_error '缺少可执行的 Python 3.10 INFO 导入环境'
        return 2
    fi
    if ! (
        cd -- "${repository_root}"
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" -m backend.info_pipeline manifest \
            --source-dir "${source_info_dir}" \
            --source-release-label "${release_id}" \
            --output "${current_manifest}" \
            >/dev/null
    ); then
        rm -rf -- "${verify_dir}"
        return 2
    fi
    if ! jq -e \
        --slurpfile current "${current_manifest}" \
        '.content_id == $current[0].content_id
         and .manifest_sha256 == $current[0].manifest_sha256' \
        "${evidence_dir}/static-info-manifest.json" >/dev/null; then
        rm -rf -- "${verify_dir}"
        domeye_artifact_error 'INFO 来源内容已变化，不能复用 S1 证据'
        return 2
    fi
    if ! "${repository_root}/deploy/database/static-info-stage-end-hook.sh" \
        S1 \
        "${evidence_dir}" \
        "${current_receipt}" \
        "${evidence_dir}/stage-gate-S0.json" \
        >/dev/null; then
        rm -f -- "${current_receipt}"
        rm -rf -- "${verify_dir}"
        domeye_artifact_error '既有 S1 证据不再满足当前阶段合同'
        return 2
    fi
    rm -f -- "${current_receipt}"

    local content_id database_state
    content_id="$(jq -r '.content_id' "${current_manifest}")"
    database_state="$(
        docker exec "${container_name}" \
            psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
            --username "${database_user}" \
            --dbname "${database_name}" \
            --command "
SELECT release.status || '|' ||
       (release.loaded_scope @> ARRAY['core_four_files']::text[]) || '|' ||
       (
         SELECT count(*)
         FROM info.source_file AS source
         WHERE source.release_sk = release.release_sk
           AND source.name IN (
             'as_entity.csv', 'important_as.csv',
             'ip_bgp_entity.csv', 'country.xlsx'
           )
           AND source.load_status = 'loaded'
           AND source.loaded_record_count + source.quarantined_record_count
               = source.logical_record_count
       ) || '|' ||
       (active.release_sk IS NOT NULL)
FROM info.dataset_release AS release
LEFT JOIN info.active_release AS active
  ON active.release_sk = release.release_sk
WHERE release.content_id = '${content_id}';
"
    )"
    rm -rf -- "${verify_dir}"
    if [[ "${database_state}" != 'validating|true|4|false' ]]; then
        domeye_artifact_error \
            "S1 证据存在但候选库状态不匹配：${database_state:-missing}"
        return 2
    fi
    printf '复用已校验的 static INFO S1 证据：%s\n' "${evidence_dir}"
    return 0
}

domeye_static_info_load_shadow() {
    local repository_root="$1"
    local source_info_dir="$2"
    local release_id="$3"
    local container_name="$4"
    local database_user="$5"
    local database_name="$6"
    local evidence_dir="$7"
    local code_commit="$8"

    if [[ ! -d "${source_info_dir}" || -L "${source_info_dir}" ]]; then
        domeye_artifact_error \
            "static INFO 来源必须是实际目录且禁止软链接：${source_info_dir}"
        return 1
    fi
    if [[ -e "${evidence_dir}" || -L "${evidence_dir}" ]]; then
        local reuse_status=0
        domeye_static_info_reuse_s1_evidence \
            "${repository_root}" \
            "${source_info_dir}" \
            "${release_id}" \
            "${container_name}" \
            "${database_user}" \
            "${database_name}" \
            "${evidence_dir}" \
            || reuse_status=$?
        case "${reuse_status}" in
            0) return 0 ;;
            1) ;;
            *) return "${reuse_status}" ;;
        esac
    fi
    install -d -m 0700 "${evidence_dir}"

    local python_bin
    python_bin="$(domeye_static_info_python "${repository_root}")"
    if [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
        domeye_artifact_error '缺少可执行的 Python 3.10 INFO 导入环境'
        return 1
    fi
    if ! "${python_bin}" -c 'import openpyxl, xlrd' >/dev/null 2>&1; then
        domeye_artifact_error 'INFO 导入 Python 环境缺少 openpyxl 或 xlrd'
        return 1
    fi

    local manifest_path="${evidence_dir}/static-info-manifest.json"
    local quality_path="${evidence_dir}/static-info-quality.json"
    local result_path="${evidence_dir}/static-info-load-result.json"
    local stage_zero_receipt="${evidence_dir}/stage-gate-S0.json"
    local stage_one_receipt="${evidence_dir}/stage-gate-S1.json"
    local spool_dir="${evidence_dir}/.spool"
    install -d -m 0700 "${spool_dir}"
    (
        cd -- "${repository_root}"
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" -m backend.info_pipeline manifest \
            --source-dir "${source_info_dir}" \
            --source-release-label "${release_id}" \
            --output "${manifest_path}"
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" -m backend.info_pipeline probe \
            --source-dir "${source_info_dir}" \
            --manifest "${manifest_path}" \
            --output "${quality_path}"
        "${repository_root}/deploy/database/static-info-stage-end-hook.sh" \
            S0 \
            "${evidence_dir}" \
            "${stage_zero_receipt}"
        DOMEYE_CORE_INFO_SPOOL_DIR="${spool_dir}" \
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" -m backend.info_pipeline load-core \
            --source-dir "${source_info_dir}" \
            --manifest "${manifest_path}" \
            --quality-report "${quality_path}" \
            --container "${container_name}" \
            --db-user "${database_user}" \
            --db-name "${database_name}" \
            --code-commit "${code_commit}" \
            --result "${result_path}"
    )
    rmdir "${spool_dir}"

    if ! jq -e \
        '(.status == "completed" or .status == "already_completed")
         and .scope == "core_four_files"
         and .activated == false
         and (.content_id | test("^info_v1_[0-9a-f]{32}$"))' \
        "${result_path}" >/dev/null; then
        domeye_artifact_error 'static INFO 导入结果未通过 shadow/未激活门禁'
        return 1
    fi

    "${repository_root}/deploy/database/static-info-stage-end-hook.sh" \
        S1 \
        "${evidence_dir}" \
        "${stage_one_receipt}" \
        "${stage_zero_receipt}"

    (
        cd -- "${evidence_dir}"
        sha256sum \
            static-info-manifest.json \
            static-info-quality.json \
            static-info-load-result.json \
            stage-gate-S0.json \
            stage-gate-S1.json \
            > SHA256SUMS
    )
    chmod 0600 \
        "${manifest_path}" \
        "${quality_path}" \
        "${result_path}" \
        "${stage_zero_receipt}" \
        "${stage_one_receipt}" \
        "${evidence_dir}/SHA256SUMS"
}

domeye_static_info_reuse_s2_evidence() {
    local repository_root="$1"
    local source_info_dir="$2"
    local container_name="$3"
    local database_user="$4"
    local database_name="$5"
    local s1_evidence_dir="$6"
    local s2_evidence_dir="$7"
    local required_names=(
        static-info-manifest.json
        static-info-full-quality.json
        static-info-full-load-result.json
        stage-gate-S2.json
        SHA256SUMS
    )
    local name

    [[ -e "${s2_evidence_dir}" || -L "${s2_evidence_dir}" ]] || return 1
    if [[ -L "${s2_evidence_dir}" || ! -d "${s2_evidence_dir}" ]]; then
        domeye_artifact_error "S2 证据路径不是实际目录：${s2_evidence_dir}"
        return 2
    fi
    for name in "${required_names[@]}"; do
        if [[ ! -f "${s2_evidence_dir}/${name}" || -L "${s2_evidence_dir}/${name}" ]]; then
            domeye_static_info_archive_incomplete_evidence "${s2_evidence_dir}"
            return 1
        fi
    done
    if ! (
        cd -- "${s2_evidence_dir}"
        sha256sum -c SHA256SUMS
    ) >/dev/null; then
        domeye_artifact_error "S2 证据校验和不匹配，拒绝覆盖：${s2_evidence_dir}"
        return 2
    fi

    local verify_dir
    verify_dir="$(mktemp -d "${s2_evidence_dir%/*}/.static-info-s2-verify.XXXXXX")"
    local current_manifest="${verify_dir}/static-info-manifest.json"
    local python_bin
    python_bin="$(domeye_static_info_python "${repository_root}")"
    if [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
        rm -rf -- "${verify_dir}"
        domeye_artifact_error '缺少可执行的 Python 3.10 INFO 导入环境'
        return 2
    fi
    if ! (
        cd -- "${repository_root}"
        PYTHONDONTWRITEBYTECODE=1 "${python_bin}" -m backend.info_pipeline manifest \
            --source-dir "${source_info_dir}" \
            --source-release-label 's2-evidence-reverify' \
            --output "${current_manifest}" \
            >/dev/null
    ); then
        rm -rf -- "${verify_dir}"
        return 2
    fi
    if ! jq -e \
        --slurpfile current "${current_manifest}" \
        '.content_id == $current[0].content_id
         and .manifest_sha256 == $current[0].manifest_sha256' \
        "${s2_evidence_dir}/static-info-manifest.json" >/dev/null; then
        rm -rf -- "${verify_dir}"
        domeye_artifact_error 'INFO 来源内容已变化，不能复用 S2 证据'
        return 2
    fi
    rm -rf -- "${verify_dir}"

    local current_receipt="${s2_evidence_dir}/.stage-gate-S2-reverify.$$"
    if ! "${repository_root}/deploy/database/static-info-stage-end-hook.sh" \
        S2 \
        "${s2_evidence_dir}" \
        "${current_receipt}" \
        "${s1_evidence_dir}/stage-gate-S1.json" \
        >/dev/null; then
        rm -f -- "${current_receipt}"
        domeye_artifact_error '既有 S2 证据不再满足当前阶段合同'
        return 2
    fi
    rm -f -- "${current_receipt}"

    local content_id database_state
    content_id="$(jq -r '.content_id' "${s2_evidence_dir}/static-info-manifest.json")"
    database_state="$(
        docker exec "${container_name}" \
            psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
            --username "${database_user}" \
            --dbname "${database_name}" \
            --command "
SELECT release.status || '|' ||
       (release.loaded_scope @> ARRAY['all_24_files']::text[]) || '|' ||
       count(*) FILTER (
           WHERE source.load_status = 'loaded'
             AND source.loaded_record_count + source.quarantined_record_count
                 = source.logical_record_count
             AND (
                 SELECT count(*)
                 FROM info.source_record AS record
                 WHERE record.release_sk = source.release_sk
                   AND record.source_file_sk = source.source_file_sk
             ) = source.logical_record_count
       ) || '|' ||
       (
         SELECT count(*)
         FROM info.source_record AS record
         WHERE record.release_sk = release.release_sk
           AND record.disposition = 'quarantined'
           AND (
             record.reason_code IS NULL
             OR btrim(record.reason_code) = ''
           )
       ) || '|' ||
       (active.release_sk IS NOT NULL)
FROM info.dataset_release AS release
JOIN info.source_file AS source
  ON source.release_sk = release.release_sk
LEFT JOIN info.active_release AS active
  ON active.release_sk = release.release_sk
WHERE release.content_id = '${content_id}'
GROUP BY release.release_sk, active.release_sk;
"
    )"
    if [[ "${database_state}" != 'validating|true|24|0|false' ]]; then
        domeye_artifact_error \
            "S2 证据存在但候选库状态不匹配：${database_state:-missing}"
        return 2
    fi
    printf '复用已校验的 static INFO S2 证据：%s\n' "${s2_evidence_dir}"
    return 0
}

domeye_static_info_load_full_shadow() {
    local repository_root="$1"
    local source_info_dir="$2"
    local container_name="$3"
    local database_user="$4"
    local database_name="$5"
    local s1_evidence_dir="$6"
    local s2_evidence_dir="${s1_evidence_dir}/S2"

    if [[ -e "${s2_evidence_dir}" || -L "${s2_evidence_dir}" ]]; then
        local reuse_status=0
        domeye_static_info_reuse_s2_evidence \
            "${repository_root}" \
            "${source_info_dir}" \
            "${container_name}" \
            "${database_user}" \
            "${database_name}" \
            "${s1_evidence_dir}" \
            "${s2_evidence_dir}" \
            || reuse_status=$?
        case "${reuse_status}" in
            0) return 0 ;;
            1) ;;
            *) return "${reuse_status}" ;;
        esac
    fi
    "${repository_root}/deploy/database/import-static-info-full-candidate.sh" \
        "${source_info_dir}" \
        "${container_name}" \
        "${database_user}" \
        "${database_name}" \
        "${s1_evidence_dir}" \
        "${s2_evidence_dir}"
}

domeye_static_info_bundle_evidence() {
    local repository_root="$1"
    local evidence_dir="$2"
    local scope="$3"
    local output_path="$4"
    if [[ -e "${output_path}" || -L "${output_path}" ]]; then
        domeye_artifact_error "static INFO 证据包已存在，拒绝覆盖：${output_path}"
        return 1
    fi
    if [[ -L "${evidence_dir}" || ! -d "${evidence_dir}" ]]; then
        domeye_artifact_error "static INFO 证据目录无效：${evidence_dir}"
        return 1
    fi
    (
        cd -- "${evidence_dir}"
        sha256sum -c SHA256SUMS >/dev/null
    )
    if [[ "${scope}" == 'all_24_files' ]]; then
        if [[ -L "${evidence_dir}/S2" || ! -d "${evidence_dir}/S2" ]]; then
            domeye_artifact_error 'all_24_files 模式缺少 S2 证据目录'
            return 1
        fi
        (
            cd -- "${evidence_dir}/S2"
            sha256sum -c SHA256SUMS >/dev/null
        )
    elif [[ "${scope}" != 'core_four_files' ]]; then
        domeye_artifact_error "未知 static INFO 证据 scope：${scope}"
        return 1
    fi

    local contract_root="${evidence_dir}/contract-root"
    local acceptance_document='docs/INFO目录数据落库最终验收文档.md'
    local stage_plan_document='docs/INFO目录数据落库分阶段计划.md'
    local machine_contract='contracts/info/static-info-final-acceptance-v1.json'
    if [[ ! -e "${contract_root}" && ! -L "${contract_root}" ]]; then
        install -d -m 0700 \
            "${contract_root}/docs" \
            "${contract_root}/contracts/info"
        install -m 0600 \
            "${repository_root}/${acceptance_document}" \
            "${contract_root}/${acceptance_document}"
        install -m 0600 \
            "${repository_root}/${stage_plan_document}" \
            "${contract_root}/${stage_plan_document}"
        install -m 0600 \
            "${repository_root}/${machine_contract}" \
            "${contract_root}/${machine_contract}"
        (
            cd -- "${contract_root}"
            sha256sum \
                "${acceptance_document}" \
                "${stage_plan_document}" \
                "${machine_contract}" \
                > SHA256SUMS
        )
        chmod 0600 "${contract_root}/SHA256SUMS"
    elif [[ -L "${contract_root}" || ! -d "${contract_root}" ]]; then
        domeye_artifact_error 'static INFO 合同快照路径无效'
        return 1
    fi
    (
        cd -- "${contract_root}"
        sha256sum -c SHA256SUMS >/dev/null
    )
    local contract_relative
    for contract_relative in \
        "${acceptance_document}" \
        "${stage_plan_document}" \
        "${machine_contract}"; do
        if [[ "$(domeye_artifact_sha256 "${repository_root}/${contract_relative}")" \
            != "$(domeye_artifact_sha256 "${contract_root}/${contract_relative}")" ]]; then
            domeye_artifact_error \
                "static INFO 合同快照与当前仓库不一致：${contract_relative}"
            return 1
        fi
    done

    local archive_members=("${evidence_dir##*/}")
    local archived_candidate
    for archived_candidate in "${evidence_dir}.incomplete."*; do
        [[ -e "${archived_candidate}" || -L "${archived_candidate}" ]] || continue
        if [[ -L "${archived_candidate}" || ! -d "${archived_candidate}" ]]; then
            domeye_artifact_error \
                "static INFO 历史失败证据路径无效：${archived_candidate}"
            return 1
        fi
        archive_members+=("${archived_candidate##*/}")
    done
    tar --create --file=- \
        --directory "${evidence_dir%/*}" \
        "${archive_members[@]}" \
        | zstd --quiet --threads=0 -6 -o "${output_path}"
    chmod 0600 "${output_path}"
}
