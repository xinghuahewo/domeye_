#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SQL_DIR="${SCRIPT_DIR}/sql"
# shellcheck source=../lib/artifact-common.sh
source "${SCRIPT_DIR}/../lib/artifact-common.sh"
# shellcheck source=../lib/database-common.sh
source "${SCRIPT_DIR}/../lib/database-common.sh"

usage() {
    printf '%s\n' \
        "用法一（源库快照）：${0##*/} <源库配置> <独立库配置> <release-id> [制品根目录] [上一发布目录]" \
        "用法二（预制完整 dump）：${0##*/} - <独立库配置> <release-id> [制品根目录] '' <dump路径> <source-full-dump.tsv> <dump.sha256>" \
        >&2
}

if (( $# < 3 || $# > 8 )); then
    usage
    exit 2
fi

readonly SOURCE_ENV_FILE="$1"
readonly DATABASE_ENV_FILE="$2"
readonly RELEASE_ID="$3"
readonly ARTIFACT_ROOT="${4:-${DOMEYE_CORE_DEFAULT_ARTIFACT_ROOT}}"
readonly BASE_RELEASE_DIR="${5:-}"
readonly PREBUILT_FULL_DUMP="${6:-}"
readonly PREBUILT_METADATA="${7:-}"
readonly PREBUILT_CHECKSUM="${8:-}"

PREBUILT_SOURCE_NAME=''
PREBUILT_SOURCE_SIZE=''
PREBUILT_SOURCE_SHA=''
PREBUILT_METADATA_SHA=''
PREBUILT_CHECKSUM_SHA=''
PREBUILT_DUMP_COMPLETED_AT=''
PREBUILT_SOURCE_DATABASE=''
PREBUILT_SOURCE_DATABASE_SIZE=''

domeye_artifact_validate_release_id "${RELEASE_ID}"
for command_name in date docker jq mkfifo mktemp sha256sum stat tail zstd; do
    domeye_artifact_require_command "${command_name}"
done

if [[ -n "${PREBUILT_FULL_DUMP}" ]]; then
    if [[ -n "${BASE_RELEASE_DIR}" || "${SOURCE_ENV_FILE}" != '-' ]]; then
        domeye_artifact_error '预制完整 dump 模式要求源库配置为 -，且不能同时指定上一发布目录'
        exit 2
    fi
    domeye_artifact_require_regular_file "${PREBUILT_FULL_DUMP}"
    domeye_artifact_require_regular_file "${PREBUILT_METADATA}"
    domeye_artifact_require_regular_file "${PREBUILT_CHECKSUM}"
    metadata_header="$(sed -n '1p' "${PREBUILT_METADATA}")"
    current_header=$'release_id\tdump_started_at_utc\tdump_completed_at_utc\tsource_database\tsource_database_size_bytes\tpostgresql_version\timage_id\tdump_name\tdump_size_bytes\tdump_sha256'
    legacy_header=$'release_id\tdump_basename\tsize\tsha256\tpostgresql_version\timage_id\tdump_started_at'
    if [[ "$(wc -l < "${PREBUILT_METADATA}" | tr -d ' ')" != '2' ]]; then
        domeye_artifact_error '预制 dump 元数据必须恰好包含固定表头和一行数据'
        exit 2
    fi
    if [[ "${metadata_header}" == "${current_header}" ]]; then
        IFS=$'\t' read -r \
            metadata_release \
            PREBUILT_SNAPSHOT_TIME \
            PREBUILT_DUMP_COMPLETED_AT \
            PREBUILT_SOURCE_DATABASE \
            PREBUILT_SOURCE_DATABASE_SIZE \
            metadata_pg_version \
            metadata_image_id \
            PREBUILT_SOURCE_NAME \
            PREBUILT_SOURCE_SIZE \
            PREBUILT_SOURCE_SHA \
            metadata_extra \
            < <(sed -n '2p' "${PREBUILT_METADATA}")
        if [[ -n "${metadata_extra:-}" || -z "${PREBUILT_SOURCE_DATABASE}" || ! "${PREBUILT_SOURCE_DATABASE_SIZE}" =~ ^[0-9]+$ ]]; then
            domeye_artifact_error '预制 dump 的源数据库元数据格式无效'
            exit 2
        fi
        if [[ ! "${PREBUILT_DUMP_COMPLETED_AT}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ || "${PREBUILT_DUMP_COMPLETED_AT}" < "${PREBUILT_SNAPSHOT_TIME}" ]]; then
            domeye_artifact_error '预制 dump 的完成时间无效或早于开始时间'
            exit 2
        fi
    elif [[ "${metadata_header}" == "${legacy_header}" ]]; then
        IFS=$'\t' read -r metadata_release PREBUILT_SOURCE_NAME PREBUILT_SOURCE_SIZE PREBUILT_SOURCE_SHA metadata_pg_version metadata_image_id PREBUILT_SNAPSHOT_TIME metadata_extra < <(sed -n '2p' "${PREBUILT_METADATA}")
        if [[ -n "${metadata_extra:-}" ]]; then
            domeye_artifact_error '预制 dump 旧版元数据列数无效'
            exit 2
        fi
    else
        domeye_artifact_error '预制 dump 元数据表头不受支持'
        exit 2
    fi
    if [[ "${metadata_release}" != "${RELEASE_ID}" || "${PREBUILT_SOURCE_NAME}" != "$(basename -- "${PREBUILT_FULL_DUMP}")" ]]; then
        domeye_artifact_error '预制 dump 元数据的 release-id 或文件名不匹配'
        exit 2
    fi
    if [[ ! "${PREBUILT_SOURCE_SIZE}" =~ ^[0-9]+$ || "${PREBUILT_SOURCE_SIZE}" != "$(stat -c '%s' "${PREBUILT_FULL_DUMP}")" ]]; then
        domeye_artifact_error '预制 dump 元数据的文件大小不匹配'
        exit 2
    fi
    if [[ ! "${PREBUILT_SOURCE_SHA}" =~ ^[0-9a-f]{64}$ || "${PREBUILT_SOURCE_SHA}" != "$(domeye_artifact_sha256 "${PREBUILT_FULL_DUMP}")" ]]; then
        domeye_artifact_error '预制 dump 元数据的 SHA256 不匹配'
        exit 2
    fi
    read -r checksum_sha checksum_name checksum_extra < "${PREBUILT_CHECKSUM}"
    if [[ -n "${checksum_extra:-}" || "${checksum_sha}" != "${PREBUILT_SOURCE_SHA}" || "${checksum_name}" != "${PREBUILT_SOURCE_NAME}" ]]; then
        domeye_artifact_error '预制 dump 独立 SHA256 文件不匹配'
        exit 2
    fi
    if [[ "${metadata_pg_version}" != '12.16' || "${metadata_image_id}" != "$(docker image inspect --format '{{.Id}}' timescaledb:2.11.2-pg12 2>/dev/null || true)" ]]; then
        domeye_artifact_error '预制 dump 的 PostgreSQL 版本或冻结镜像 ID 不匹配'
        exit 2
    fi
    if [[ ! "${PREBUILT_SNAPSHOT_TIME}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
        domeye_artifact_error '预制 dump 的 dump_started_at 必须是 UTC Z 时间'
        exit 2
    fi
    PREBUILT_SNAPSHOT_LOCAL="$(date -u -d "${PREBUILT_SNAPSHOT_TIME} +8 hours" '+%Y-%m-%d %H:%M:%S')"
    PREBUILT_METADATA_SHA="$(domeye_artifact_sha256 "${PREBUILT_METADATA}")"
    PREBUILT_CHECKSUM_SHA="$(domeye_artifact_sha256 "${PREBUILT_CHECKSUM}")"
elif (( $# > 5 )); then
    domeye_artifact_error '只有预制完整 dump 模式可以使用第 6 至第 8 个参数'
    exit 2
fi

for sql_file in prune.sql inventory.sql validate-integrity.sql create-reader.sql prepare-refresh-table.sql upsert-feature-country.sql merge-feature-country.sql; do
    domeye_artifact_require_regular_file "${SQL_DIR}/${sql_file}"
done

if [[ -z "${PREBUILT_FULL_DUMP}" ]]; then
    domeye_database_load_env "${SOURCE_ENV_FILE}"
    domeye_database_verify_source_env
fi
domeye_database_load_env "${DATABASE_ENV_FILE}"
domeye_database_validate_config

if ! docker image inspect "${DOMEYE_CORE_DB_IMAGE}" >/dev/null 2>&1; then
    domeye_artifact_error "本机缺少固定数据库镜像：${DOMEYE_CORE_DB_IMAGE}"
    exit 1
fi

readonly RELEASE_DIR="$(domeye_artifact_release_dir "${ARTIFACT_ROOT}" "${RELEASE_ID}")"
domeye_artifact_assert_safe_release_dir "${ARTIFACT_ROOT}" "${RELEASE_DIR}"
install -d -m 0750 "${ARTIFACT_ROOT}/releases" "${RELEASE_DIR}" "${DOMEYE_CORE_DATABASE_WORK_ROOT}"

readonly LOCK_DIR="${RELEASE_DIR}/.database-build.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    domeye_artifact_error "同一发布版本正在构建数据库制品：${RELEASE_ID}"
    exit 1
fi

for output_name in \
    "${DOMEYE_CORE_DATABASE_ARCHIVE}" \
    "${DOMEYE_CORE_IMAGE_ARCHIVE}" \
    database-inventory.json \
    database-schema.sql \
    "${DOMEYE_CORE_DATABASE_MANIFEST}"; do
    if [[ -e "${RELEASE_DIR}/${output_name}" ]]; then
        domeye_artifact_error "该 release-id 已包含数据库输出，拒绝覆盖：${output_name}"
        rmdir "${LOCK_DIR}" 2>/dev/null || true
        exit 1
    fi
done

work_dir="$(mktemp -d "${RELEASE_DIR}/.database-build.XXXXXX")"
readonly BUILD_DATA_ROOT="${DOMEYE_CORE_DATABASE_WORK_ROOT}/build-${RELEASE_ID}-$$"
readonly CANDIDATE_DATA_DIR="${BUILD_DATA_ROOT}/postgres"
container_suffix="${RELEASE_ID//[^a-zA-Z0-9]/_}"
readonly CANDIDATE_CONTAINER="domeye_core_build_${container_suffix}_$$"
readonly SOURCE_PGPASS="${work_dir}/source.pgpass"

snapshot_active=false
snapshot_pid=''

finish_source_snapshot() {
    if [[ "${snapshot_active}" == true ]]; then
        printf 'ROLLBACK;\n\\q\n' >&9 2>/dev/null || true
        exec 9>&- 2>/dev/null || true
        exec 8<&- 2>/dev/null || true
        wait "${snapshot_pid}" 2>/dev/null || true
        snapshot_active=false
    fi
}

cleanup() {
    local exit_code=$?
    finish_source_snapshot
    domeye_database_remove_candidate_container "${CANDIDATE_CONTAINER}" || true
    if [[ "${BUILD_DATA_ROOT}" == "${DOMEYE_CORE_DATABASE_WORK_ROOT}/build-${RELEASE_ID}-$$" && -d "${BUILD_DATA_ROOT}" ]]; then
        rm -rf -- "${BUILD_DATA_ROOT}"
    fi
    if [[ -d "${work_dir}" ]]; then
        rm -rf -- "${work_dir}"
    fi
    rmdir "${LOCK_DIR}" 2>/dev/null || true
    return "${exit_code}"
}
trap cleanup EXIT

if [[ -z "${PREBUILT_FULL_DUMP}" ]]; then
    domeye_database_create_pgpass \
        "${SOURCE_PGPASS}" \
        "${SOURCE_DB_HOST}" \
        "${SOURCE_DB_PORT}" \
        "${SOURCE_DB_NAME}" \
        "${SOURCE_DB_USER}" \
        "${SOURCE_DB_PASSWORD}"
fi

source_psql() {
    docker run --rm \
        --network host \
        --volume "${SOURCE_PGPASS}:/run/secrets/source.pgpass:ro" \
        --env 'PGPASSFILE=/run/secrets/source.pgpass' \
        "${DOMEYE_CORE_DB_IMAGE}" \
        psql -X --set ON_ERROR_STOP=1 \
            --host "${SOURCE_DB_HOST}" \
            --port "${SOURCE_DB_PORT}" \
            --username "${SOURCE_DB_USER}" \
            --dbname "${SOURCE_DB_NAME}" \
            "$@"
}

start_source_snapshot() {
    local input_fifo="${work_dir}/snapshot-input.fifo"
    local output_fifo="${work_dir}/snapshot-output.fifo"
    mkfifo "${input_fifo}" "${output_fifo}"
    docker run --rm --interactive \
        --network host \
        --volume "${SOURCE_PGPASS}:/run/secrets/source.pgpass:ro" \
        --env 'PGPASSFILE=/run/secrets/source.pgpass' \
        "${DOMEYE_CORE_DB_IMAGE}" \
        psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
            --host "${SOURCE_DB_HOST}" \
            --port "${SOURCE_DB_PORT}" \
            --username "${SOURCE_DB_USER}" \
            --dbname "${SOURCE_DB_NAME}" \
        < "${input_fifo}" > "${output_fifo}" &
    snapshot_pid=$!
    exec 9>"${input_fifo}"
    exec 8<"${output_fifo}"
    snapshot_active=true

    printf '%s\n' \
        'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;' \
        "SELECT pg_export_snapshot() || '|' || to_char(transaction_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') || '|' || to_char(timezone('Asia/Shanghai', transaction_timestamp()), 'YYYY-MM-DD HH24:MI:SS');" \
        >&9

    IFS='|' read -r SNAPSHOT_ID SNAPSHOT_TIME SNAPSHOT_LOCAL <&8
    if [[ ! "${SNAPSHOT_ID}" =~ ^[A-Fa-f0-9-]+$ || ! "${SNAPSHOT_LOCAL}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]][0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]; then
        domeye_artifact_error '未能从源数据库取得一致性快照标识'
        return 1
    fi
    readonly SNAPSHOT_ID SNAPSHOT_TIME SNAPSHOT_LOCAL
    SNAPSHOT_MONTH="${SNAPSHOT_LOCAL:0:7}"
    SNAPSHOT_MONTH="${SNAPSHOT_MONTH//-/}"
    readonly SNAPSHOT_MONTH
}

source_pg_dump() {
    docker run --rm \
        --network host \
        --volume "${SOURCE_PGPASS}:/run/secrets/source.pgpass:ro" \
        --env 'PGPASSFILE=/run/secrets/source.pgpass' \
        "${DOMEYE_CORE_DB_IMAGE}" \
        pg_dump \
            --host "${SOURCE_DB_HOST}" \
            --port "${SOURCE_DB_PORT}" \
            --username "${SOURCE_DB_USER}" \
            --dbname "${SOURCE_DB_NAME}" \
            "$@"
}

source_copy_to_file() {
    local copy_expression="$1"
    local output_file="$2"

    printf '%s\n' \
        'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;' \
        "SET TRANSACTION SNAPSHOT '${SNAPSHOT_ID}';" \
        "\\copy ${copy_expression} TO STDOUT WITH (FORMAT binary)" \
        'COMMIT;' \
        | docker run --rm --interactive \
            --network host \
            --volume "${SOURCE_PGPASS}:/run/secrets/source.pgpass:ro" \
            --env 'PGPASSFILE=/run/secrets/source.pgpass' \
            "${DOMEYE_CORE_DB_IMAGE}" \
            psql -X --quiet --set ON_ERROR_STOP=1 \
                --host "${SOURCE_DB_HOST}" \
                --port "${SOURCE_DB_PORT}" \
                --username "${SOURCE_DB_USER}" \
                --dbname "${SOURCE_DB_NAME}" \
            > "${output_file}"
    chmod 0600 "${output_file}"
    domeye_artifact_require_regular_file "${output_file}"
}

restore_base_release() {
    local base_dir="$1"
    "${SCRIPT_DIR}/../artifacts/verify-release.sh" "${base_dir}"
    local base_archive="${base_dir}/${DOMEYE_CORE_DATABASE_ARCHIVE}"
    domeye_artifact_require_regular_file "${base_archive}"
    domeye_database_restore_archive "${CANDIDATE_CONTAINER}" "${base_archive}"
}

refresh_from_source() {
    local base_dir="$1"
    local base_manifest="${base_dir}/${DOMEYE_CORE_DATABASE_MANIFEST}"
    local base_snapshot_time base_snapshot_local base_month overlap_start
    base_snapshot_time="$(jq -r '.snapshot_time' "${base_manifest}")"
    base_snapshot_local="$(jq -r '.snapshot_local' "${base_manifest}")"
    if [[ "${SNAPSHOT_TIME}" < "${base_snapshot_time}" || "${SNAPSHOT_TIME}" == "${base_snapshot_time}" || "${SNAPSHOT_LOCAL}" < "${base_snapshot_local}" || "${SNAPSHOT_LOCAL}" == "${base_snapshot_local}" ]]; then
        domeye_artifact_error '新快照时间必须严格晚于上一数据库制品'
        return 1
    fi
    base_month="${base_snapshot_local:0:7}"
    base_month="${base_month//-/}"
    overlap_start="$(date -d "${base_snapshot_local} 24 hours ago" '+%Y-%m-%d %H:%M:%S')"
    if [[ ! "${overlap_start}" < "${base_snapshot_local}" || ! "${base_snapshot_local}" < "${SNAPSHOT_LOCAL}" ]]; then
        domeye_artifact_error "feature_country 重叠窗口顺序无效：${overlap_start} < ${base_snapshot_local} < ${SNAPSHOT_LOCAL}"
        return 1
    fi

    local table_query
    table_query="SELECT tablename FROM pg_tables WHERE schemaname='public' AND ((tablename ~ '^(event_table|hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage)_[0-9]{6}$') OR (tablename ~ '^feature_(other|us|br|cn|ru|in|gb|id|de|au|pl)_[0-9]{6}$')) AND right(tablename, 6) BETWEEN '${base_month}' AND '${SNAPSHOT_MONTH}' ORDER BY tablename;"

    refresh_table_output="$(source_psql --quiet --no-align --tuples-only \
        --command "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '${SNAPSHOT_ID}'; ${table_query} COMMIT;")"
    mapfile -t refresh_tables <<< "${refresh_table_output}"
    if [[ ${#refresh_tables[@]} -eq 1 && -z "${refresh_tables[0]}" ]]; then
        refresh_tables=()
    fi

    local table_name family is_feature binary_path
    for table_name in "${refresh_tables[@]}"; do
        if [[ ! "${table_name}" =~ ^(event_table|hijack|sub_hijack|leak_event|prefix_outage|as_outage|country_outage|feature_(other|us|br|cn|ru|in|gb|id|de|au|pl))_[0-9]{6}$ ]]; then
            domeye_artifact_error "源库返回非法刷新表名：${table_name}"
            return 1
        fi
        family="${table_name%_[0-9][0-9][0-9][0-9][0-9][0-9]}"
        is_feature=false
        if [[ "${table_name}" == feature_* ]]; then
            is_feature=true
        fi

        binary_path="${work_dir}/${table_name}.bin"
        source_copy_to_file "(SELECT * FROM public.${table_name})" "${binary_path}"
        source_count="$(source_psql --quiet --no-align --tuples-only --command "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '${SNAPSHOT_ID}'; SELECT count(*) FROM public.${table_name}; COMMIT;")"
        docker exec --interactive \
            --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
            "${CANDIDATE_CONTAINER}" \
            psql -X --set ON_ERROR_STOP=1 \
                --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
                --dbname "${DOMEYE_CORE_DB_NAME}" \
                --set "table_name=${table_name}" \
                --set "table_family=${family}" \
                --set "is_feature=${is_feature}" \
                < "${SQL_DIR}/prepare-refresh-table.sql"
        docker exec --interactive \
            --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
            "${CANDIDATE_CONTAINER}" \
            psql -X --set ON_ERROR_STOP=1 \
                --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
                --dbname "${DOMEYE_CORE_DB_NAME}" \
                --command "\\copy public.${table_name} FROM STDIN WITH (FORMAT binary)" \
                < "${binary_path}"
        candidate_count="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command "SELECT count(*) FROM public.${table_name};")"
        if [[ "${candidate_count}" != "${source_count}" ]]; then
            domeye_artifact_error "刷新表行数不一致：${table_name}（源 ${source_count}，候选 ${candidate_count}）"
            return 1
        fi
        rm -f -- "${binary_path}"
    done

    local country_binary="${work_dir}/feature_country.bin"
    source_copy_to_file \
        "(SELECT * FROM public.feature_country WHERE t >= timestamp '${overlap_start}' AND t <= timestamp '${SNAPSHOT_LOCAL}' ORDER BY t, source, country)" \
        "${country_binary}"
    domeye_database_psql "${CANDIDATE_CONTAINER}" --file=- < "${SQL_DIR}/upsert-feature-country.sql"
    docker exec --interactive \
        --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
        "${CANDIDATE_CONTAINER}" \
        psql -X --set ON_ERROR_STOP=1 \
            --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
            --dbname "${DOMEYE_CORE_DB_NAME}" \
            --command '\copy public.__domeye_feature_country_refresh FROM STDIN WITH (FORMAT binary)' \
            < "${country_binary}"
    source_country_count="$(source_psql --quiet --no-align --tuples-only --command "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '${SNAPSHOT_ID}'; SELECT count(*) FROM public.feature_country WHERE t >= timestamp '${overlap_start}' AND t <= timestamp '${SNAPSHOT_LOCAL}'; COMMIT;")"
    candidate_country_count="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command 'SELECT count(*) FROM public.__domeye_feature_country_refresh;')"
    if [[ "${candidate_country_count}" != "${source_country_count}" ]]; then
        domeye_artifact_error "feature_country 重叠窗口行数不一致（源 ${source_country_count}，候选 ${candidate_country_count}）"
        return 1
    fi
    domeye_database_psql "${CANDIDATE_CONTAINER}" --file=- < "${SQL_DIR}/merge-feature-country.sql"
    rm -f -- "${country_binary}"
}

domeye_database_start_candidate "${CANDIDATE_CONTAINER}" "${CANDIDATE_DATA_DIR}"
if [[ -n "${PREBUILT_FULL_DUMP}" ]]; then
    SNAPSHOT_TIME="${PREBUILT_SNAPSHOT_TIME}"
    SNAPSHOT_LOCAL="${PREBUILT_SNAPSHOT_LOCAL}"
    SNAPSHOT_MONTH="${SNAPSHOT_LOCAL:0:7}"
    SNAPSHOT_MONTH="${SNAPSHOT_MONTH//-/}"
    readonly SNAPSHOT_TIME SNAPSHOT_LOCAL SNAPSHOT_MONTH
    readonly FULL_DUMP="${work_dir}/source-full.dump.zst"
    if zstd --quiet --test "${PREBUILT_FULL_DUMP}" >/dev/null 2>&1; then
        zstd --quiet --decompress --stdout "${PREBUILT_FULL_DUMP}" \
            | docker run --rm --interactive "${DOMEYE_CORE_DB_IMAGE}" pg_restore --list >/dev/null
        install -m 0600 "${PREBUILT_FULL_DUMP}" "${FULL_DUMP}"
    else
        docker run --rm \
            --volume "$(dirname -- "${PREBUILT_FULL_DUMP}"):/input:ro" \
            "${DOMEYE_CORE_DB_IMAGE}" \
            pg_restore --list "/input/$(basename -- "${PREBUILT_FULL_DUMP}")" \
            >/dev/null
        zstd --quiet --threads=0 -6 --stdout "${PREBUILT_FULL_DUMP}" > "${FULL_DUMP}"
        chmod 0600 "${FULL_DUMP}"
    fi
    domeye_database_restore_archive "${CANDIDATE_CONTAINER}" "${FULL_DUMP}"
elif [[ -n "${BASE_RELEASE_DIR}" ]]; then
    restore_base_release "${BASE_RELEASE_DIR%/}"
    start_source_snapshot
    refresh_from_source "${BASE_RELEASE_DIR%/}"
    finish_source_snapshot
else
    start_source_snapshot
    readonly FULL_DUMP="${work_dir}/source-full.dump.zst"
    source_pg_dump \
        --format=custom \
        --compress=0 \
        --no-owner \
        --no-acl \
        --snapshot="${SNAPSHOT_ID}" \
        | zstd --quiet --threads=0 -6 -o "${FULL_DUMP}"
    chmod 0600 "${FULL_DUMP}"
    finish_source_snapshot
    domeye_database_restore_archive "${CANDIDATE_CONTAINER}" "${FULL_DUMP}"
fi

readonly PRUNE_OUTPUT="${work_dir}/prune-output.txt"
readonly PRUNE_AUDIT="${work_dir}/prune-audit.json"
docker exec --interactive \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "data_start=${DOMEYE_CORE_DATA_START}" \
        --set "snapshot_local=${SNAPSHOT_LOCAL}" \
        --set "snapshot_month=${SNAPSHOT_MONTH}" \
        < "${SQL_DIR}/prune.sql" \
        > "${PRUNE_OUTPUT}"
tail -n 1 "${PRUNE_OUTPUT}" > "${PRUNE_AUDIT}"
domeye_artifact_json_file "${PRUNE_AUDIT}"
if [[ -n "${BASE_RELEASE_DIR}" ]]; then
    base_snapshot_local="$(jq -r '.snapshot_local' "${BASE_RELEASE_DIR%/}/${DOMEYE_CORE_DATABASE_MANIFEST}")"
    base_month_for_audit="${base_snapshot_local:0:7}"
    base_month_for_audit="${base_month_for_audit//-/}"
    jq -n \
        --arg base_month "${base_month_for_audit}" \
        --slurpfile previous "${BASE_RELEASE_DIR%/}/database-inventory.json" \
        --slurpfile current "${PRUNE_AUDIT}" \
        '(
          [$previous[0].integrity.detail_references.discarded_malformed_event_rows.by_month_type[]
           | select(.month < $base_month)]
          + $current[0].by_month_type
        )
        | sort_by(.month, .event_type)
        | {total: ([.[].row_count] | add // 0), by_month_type: .}' \
        > "${PRUNE_AUDIT}.merged"
    mv -- "${PRUNE_AUDIT}.merged" "${PRUNE_AUDIT}"
fi
if ! jq -e \
    '(.total | type) == "number"
     and .total >= 0
     and ([.by_month_type[].row_count] | add // 0) == .total' \
    "${PRUNE_AUDIT}" >/dev/null; then
    domeye_artifact_error '异常事件裁剪审计结果无效'
    exit 1
fi

domeye_database_apply_reader "${CANDIDATE_CONTAINER}" "${SQL_DIR}/create-reader.sql"

readonly POSTGRES_VERSION="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command 'SHOW server_version;')"
readonly TIMESCALEDB_VERSION="$(domeye_database_psql "${CANDIDATE_CONTAINER}" --quiet --no-align --tuples-only --command "SELECT extversion FROM pg_extension WHERE extname='timescaledb';")"
if [[ "${POSTGRES_VERSION}" != '12.16' || "${TIMESCALEDB_VERSION}" != '2.11.2' ]]; then
    domeye_artifact_error "数据库版本不符合冻结基线：PostgreSQL=${POSTGRES_VERSION}，TimescaleDB=${TIMESCALEDB_VERSION}"
    exit 1
fi

reader_result="$(docker exec \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_READER_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'SHOW transaction_read_only; SELECT count(*) FROM public.feature_country;')"
if [[ "${reader_result%%$'\n'*}" != 'on' ]]; then
    domeye_artifact_error '只读账号没有启用默认只读事务'
    exit 1
fi
reader_count="${reader_result##*$'\n'}"
if [[ ! "${reader_count}" =~ ^[0-9]+$ || "${reader_count}" == '0' ]]; then
    domeye_artifact_error '只读账号未能读取非空的 feature_country 超表'
    exit 1
fi
if docker exec \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_READER_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_READER_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --command 'CREATE TABLE public.__domeye_readonly_probe(id integer);' \
        >/dev/null 2>&1; then
    domeye_database_psql "${CANDIDATE_CONTAINER}" --command 'DROP TABLE IF EXISTS public.__domeye_readonly_probe;'
    domeye_artifact_error '只读账号意外获得了建表能力'
    exit 1
fi

readonly INTEGRITY_TMP="${work_dir}/database-integrity.json"
docker exec --interactive \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "data_start=${DOMEYE_CORE_DATA_START}" \
        --set "snapshot_time=${SNAPSHOT_TIME}" \
        --set "snapshot_month=${SNAPSHOT_MONTH}" \
        < "${SQL_DIR}/validate-integrity.sql" \
        > "${INTEGRITY_TMP}"
domeye_artifact_json_file "${INTEGRITY_TMP}"
jq --slurpfile discarded "${PRUNE_AUDIT}" \
    '.detail_references.discarded_malformed_event_rows = $discarded[0]' \
    "${INTEGRITY_TMP}" > "${INTEGRITY_TMP}.merged"
mv -- "${INTEGRITY_TMP}.merged" "${INTEGRITY_TMP}"
if ! jq -e \
    '.table_whitelist.ok == true
     and .detail_references.ok == true
     and .detail_references.malformed_count == 0
     and .detail_references.orphan_count == 0' \
    "${INTEGRITY_TMP}" >/dev/null; then
    jq '.table_whitelist, .detail_references' "${INTEGRITY_TMP}" >&2
    domeye_artifact_error '候选库白名单或事件详情引用完整性门禁失败'
    exit 1
fi

readonly INVENTORY_RAW="${work_dir}/database-inventory-raw.json"
readonly INVENTORY_TMP="${work_dir}/database-inventory.json"
docker exec --interactive \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    psql -X --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        --set "data_start=${DOMEYE_CORE_DATA_START}" \
        --set "snapshot_time=${SNAPSHOT_TIME}" \
        < "${SQL_DIR}/inventory.sql" \
        > "${INVENTORY_RAW}"
domeye_artifact_json_file "${INVENTORY_RAW}"
jq -s '.[0] + {integrity: .[1]}' "${INVENTORY_RAW}" "${INTEGRITY_TMP}" > "${INVENTORY_TMP}"
domeye_artifact_json_file "${INVENTORY_TMP}"

if ! jq -e \
    --arg start "${DOMEYE_CORE_DATA_START}" \
    --arg end "${SNAPSHOT_LOCAL}" \
    'all(.tables[]; ((.min_time == null or .min_time >= $start) and (.max_time == null or .max_time <= $end)))' \
    "${INVENTORY_TMP}" >/dev/null; then
    domeye_artifact_error '候选库存在超出固定时间范围的数据'
    exit 1
fi

readonly SCHEMA_TMP="${work_dir}/database-schema.sql"
docker exec \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    pg_dump \
        --schema-only \
        --no-owner \
        --no-acl \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        > "${SCHEMA_TMP}"
chmod 0600 "${SCHEMA_TMP}"

readonly DATABASE_TMP="${work_dir}/${DOMEYE_CORE_DATABASE_ARCHIVE}"
docker exec \
    --env "PGPASSWORD=${DOMEYE_CORE_DB_ADMIN_PASSWORD}" \
    "${CANDIDATE_CONTAINER}" \
    pg_dump \
        --format=custom \
        --compress=0 \
        --no-owner \
        --no-acl \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
    | zstd --quiet --threads=0 -6 -o "${DATABASE_TMP}"
chmod 0600 "${DATABASE_TMP}"

readonly IMAGE_TMP="${work_dir}/${DOMEYE_CORE_IMAGE_ARCHIVE}"
docker image save "${DOMEYE_CORE_DB_IMAGE}" \
    | zstd --quiet --threads=0 -6 -o "${IMAGE_TMP}"
chmod 0600 "${IMAGE_TMP}"
readonly IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${DOMEYE_CORE_DB_IMAGE}")"
readonly IMAGE_DIGEST="$(docker image inspect --format '{{join .RepoDigests ","}}' "${DOMEYE_CORE_DB_IMAGE}")"

readonly MANIFEST_TMP="${work_dir}/${DOMEYE_CORE_DATABASE_MANIFEST}"
jq -n \
    --argjson schema_version 1 \
    --arg release_id "${RELEASE_ID}" \
    --arg created_at "$(domeye_artifact_iso_utc_now)" \
    --arg data_start "${DOMEYE_CORE_DATA_START}" \
    --arg snapshot_time "${SNAPSHOT_TIME}" \
    --arg snapshot_local "${SNAPSHOT_LOCAL}" \
    --arg business_timezone 'Asia/Shanghai' \
    --arg postgres_version "${POSTGRES_VERSION}" \
    --arg timescaledb_version "${TIMESCALEDB_VERSION}" \
    --arg archive_name "${DOMEYE_CORE_DATABASE_ARCHIVE}" \
    --arg archive_sha256 "$(domeye_artifact_sha256 "${DATABASE_TMP}")" \
    --argjson archive_size "$(stat -c '%s' "${DATABASE_TMP}")" \
    --arg inventory_name 'database-inventory.json' \
    --arg inventory_sha256 "$(domeye_artifact_sha256 "${INVENTORY_TMP}")" \
    --arg schema_name 'database-schema.sql' \
    --arg schema_sha256 "$(domeye_artifact_sha256 "${SCHEMA_TMP}")" \
    --arg image_archive "${DOMEYE_CORE_IMAGE_ARCHIVE}" \
    --arg image_archive_sha256 "$(domeye_artifact_sha256 "${IMAGE_TMP}")" \
    --arg image_ref "${DOMEYE_CORE_DB_IMAGE}" \
    --arg image_id "${IMAGE_ID}" \
    --arg image_digest "${IMAGE_DIGEST}" \
    --arg source_dump_name "${PREBUILT_SOURCE_NAME}" \
    --arg source_dump_size "${PREBUILT_SOURCE_SIZE}" \
    --arg source_dump_sha256 "${PREBUILT_SOURCE_SHA}" \
    --arg source_metadata_sha256 "${PREBUILT_METADATA_SHA}" \
    --arg source_checksum_sha256 "${PREBUILT_CHECKSUM_SHA}" \
    --arg source_dump_completed_at "${PREBUILT_DUMP_COMPLETED_AT}" \
    --arg source_database "${PREBUILT_SOURCE_DATABASE}" \
    --arg source_database_size "${PREBUILT_SOURCE_DATABASE_SIZE}" \
    --arg base_release "$(if [[ -n "${BASE_RELEASE_DIR}" ]]; then jq -r '.release_id' "${BASE_RELEASE_DIR%/}/${DOMEYE_CORE_RELEASE_MANIFEST}"; fi)" \
    --slurpfile inventory "${INVENTORY_TMP}" \
    '{
      schema_version: $schema_version,
      component: "database",
      release_id: $release_id,
      created_at: $created_at,
      data_start: $data_start,
      snapshot_time: $snapshot_time,
      snapshot_local: $snapshot_local,
      snapshot_timezone: $business_timezone,
      base_release: (if $base_release == "" then null else $base_release end),
      versions: {postgresql: $postgres_version, timescaledb: $timescaledb_version},
      archive: {name: $archive_name, sha256: $archive_sha256, size: $archive_size},
      inventory: {name: $inventory_name, sha256: $inventory_sha256, table_count: ($inventory[0].tables | length)},
      integrity: {
        source: $inventory_name,
        table_whitelist_ok: $inventory[0].integrity.table_whitelist.ok,
        malformed_detail_count: $inventory[0].integrity.detail_references.malformed_count,
        orphan_detail_count: $inventory[0].integrity.detail_references.orphan_count,
        discarded_malformed_event_rows: $inventory[0].integrity.detail_references.discarded_malformed_event_rows
      },
      schema: {name: $schema_name, sha256: $schema_sha256},
      image: {archive: $image_archive, archive_sha256: $image_archive_sha256, ref: $image_ref, id: $image_id, digest: $image_digest}
      ,provenance: (
        if $source_dump_name == "" then
          {mode: (if $base_release == "" then "source_snapshot" else "incremental_refresh" end)}
        else
          {
            mode: "prebuilt_full_dump",
            source_dump: {
              name: $source_dump_name,
              size: ($source_dump_size | tonumber),
              sha256: $source_dump_sha256,
              dump_started_at: $snapshot_time,
              dump_completed_at: (if $source_dump_completed_at == "" then null else $source_dump_completed_at end),
              source_database: (if $source_database == "" then null else $source_database end),
              source_database_size_bytes: (if $source_database_size == "" then null else ($source_database_size | tonumber) end),
              metadata_sha256: $source_metadata_sha256,
              checksum_file_sha256: $source_checksum_sha256
            }
          }
        end
      )
    }' > "${MANIFEST_TMP}"
chmod 0600 "${MANIFEST_TMP}"

mv -- "${DATABASE_TMP}" "${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_ARCHIVE}"
mv -- "${IMAGE_TMP}" "${RELEASE_DIR}/${DOMEYE_CORE_IMAGE_ARCHIVE}"
mv -- "${INVENTORY_TMP}" "${RELEASE_DIR}/database-inventory.json"
mv -- "${SCHEMA_TMP}" "${RELEASE_DIR}/database-schema.sql"
mv -- "${MANIFEST_TMP}" "${RELEASE_DIR}/${DOMEYE_CORE_DATABASE_MANIFEST}"
printf '数据库制品已生成：%s\n' "${RELEASE_DIR}"
