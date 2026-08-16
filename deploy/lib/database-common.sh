#!/usr/bin/env bash

# 数据库制品脚本共用函数。调用方必须先加载 artifact-common.sh 并启用严格模式。
readonly DOMEYE_CORE_DATABASE_CONTAINER='domeye_core_pg'
readonly DOMEYE_CORE_DATABASE_HOST='127.0.0.1'
readonly DOMEYE_CORE_DATABASE_PORT='29429'
readonly DOMEYE_CORE_DATABASE_IMAGE_DEFAULT='timescaledb:2.11.2-pg12'
readonly DOMEYE_CORE_DATABASE_NAME_DEFAULT='bgp_project'
readonly DOMEYE_CORE_DATABASE_ADMIN_DEFAULT='postgres'
readonly DOMEYE_CORE_DATABASE_READER_DEFAULT='domeye_core_reader'
readonly DOMEYE_CORE_DATABASE_MEMORY='16g'
readonly DOMEYE_CORE_DATABASE_SHARED_BUFFERS='4GB'
readonly DOMEYE_CORE_DATABASE_CONFIG_DEFAULT='/home/bgpdata/Domeye-Core-data/config/database.env'
readonly DOMEYE_CORE_DATABASE_ACTIVE_LINK='/home/bgpdata/Domeye-Core-data/postgres'
readonly DOMEYE_CORE_DATABASE_RELEASE_ROOT='/home/bgpdata/Domeye-Core-data/releases'
readonly DOMEYE_CORE_DATABASE_WORK_ROOT='/home/bgpdata/Domeye-Core-data/work'

domeye_database_load_env() {
    local env_file="$1"
    domeye_artifact_require_regular_file "${env_file}"

    local mode
    mode="$(stat -c '%a' "${env_file}")"
    if (( (8#${mode} & 8#077) != 0 )); then
        domeye_artifact_error "配置文件权限必须为 0600：${env_file}（当前 ${mode}）"
        return 1
    fi

    # shellcheck disable=SC1090
    source "${env_file}"

    # 配置只保留在当前 shell 变量中；具体子进程按最小需要显式接收。
    export -n \
        DOMEYE_CORE_DB_ADMIN_PASSWORD \
        DOMEYE_CORE_DB_READER_PASSWORD \
        DOMEYE_CORE_SECRET_KEY \
        SOURCE_DB_PASSWORD \
        2>/dev/null || true
}

domeye_database_apply_defaults() {
    : "${DOMEYE_CORE_DB_IMAGE:=${DOMEYE_CORE_DATABASE_IMAGE_DEFAULT}}"
    : "${DOMEYE_CORE_DB_NAME:=${DOMEYE_CORE_DATABASE_NAME_DEFAULT}}"
    : "${DOMEYE_CORE_DB_ADMIN_USER:=${DOMEYE_CORE_DATABASE_ADMIN_DEFAULT}}"
    : "${DOMEYE_CORE_DB_READER_USER:=${DOMEYE_CORE_DATABASE_READER_DEFAULT}}"
    : "${DOMEYE_CORE_DB_PORT:=${DOMEYE_CORE_DATABASE_PORT}}"
    export DOMEYE_CORE_DB_IMAGE DOMEYE_CORE_DB_NAME DOMEYE_CORE_DB_ADMIN_USER
    export DOMEYE_CORE_DB_READER_USER DOMEYE_CORE_DB_PORT
}

domeye_database_validate_config() {
    domeye_database_apply_defaults

    if [[ "${DOMEYE_CORE_DB_PORT}" != "${DOMEYE_CORE_DATABASE_PORT}" ]]; then
        domeye_artifact_error "独立数据库端口必须为 ${DOMEYE_CORE_DATABASE_PORT}"
        return 1
    fi
    if [[ ! "${DOMEYE_CORE_DB_NAME}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
        domeye_artifact_error "数据库名称不是安全标识符：${DOMEYE_CORE_DB_NAME}"
        return 1
    fi
    if [[ ! "${DOMEYE_CORE_DB_ADMIN_USER}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
        domeye_artifact_error "管理员角色不是安全标识符：${DOMEYE_CORE_DB_ADMIN_USER}"
        return 1
    fi
    if [[ ! "${DOMEYE_CORE_DB_READER_USER}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
        domeye_artifact_error "只读角色不是安全标识符：${DOMEYE_CORE_DB_READER_USER}"
        return 1
    fi
    if [[ -z "${DOMEYE_CORE_DB_ADMIN_PASSWORD:-}" || -z "${DOMEYE_CORE_DB_READER_PASSWORD:-}" || -z "${DOMEYE_CORE_SECRET_KEY:-}" ]]; then
        domeye_artifact_error '数据库管理员密码、只读账号密码和后端 SECRET_KEY 均不能为空'
        return 1
    fi
    local secret_value
    for secret_value in "${DOMEYE_CORE_DB_ADMIN_PASSWORD}" "${DOMEYE_CORE_DB_READER_PASSWORD}" "${DOMEYE_CORE_SECRET_KEY}"; do
        if [[ "${secret_value}" == *[[:space:]]* ]]; then
            domeye_artifact_error '数据库密码和 SECRET_KEY 不得包含空白字符'
            return 1
        fi
    done
}

domeye_database_wait_container() {
    local container_name="$1"
    local attempt
    for (( attempt = 1; attempt <= 90; attempt++ )); do
        # 首次初始化会短暂启动临时 PostgreSQL；只有 PID 1 已切换为最终 postgres 才算就绪。
        if docker exec "${container_name}" sh -c \
            'test "$(cat /proc/1/comm)" = postgres' \
            >/dev/null 2>&1 \
            && docker exec "${container_name}" pg_isready -q \
                -U "${DOMEYE_CORE_DB_ADMIN_USER}" \
                -d "${DOMEYE_CORE_DB_NAME}"; then
            return 0
        fi
        sleep 1
    done
    domeye_artifact_error "数据库容器 90 秒内未就绪：${container_name}"
    return 1
}

domeye_database_prepare_empty_data_dir() {
    local data_dir="$1"
    local runtime_image="${DOMEYE_CORE_DB_IMAGE_RUNTIME:-${DOMEYE_CORE_DB_IMAGE}}"

    if [[ "${data_dir}" != "${DOMEYE_CORE_DATABASE_RELEASE_ROOT}/"* && "${data_dir}" != "${DOMEYE_CORE_DATABASE_WORK_ROOT}/"* ]]; then
        domeye_artifact_error "候选数据库目录越界：${data_dir}"
        return 1
    fi
    if [[ -e "${data_dir}" ]]; then
        domeye_artifact_error "候选数据库目录已存在，拒绝覆盖：${data_dir}"
        return 1
    fi

    local postgres_uid postgres_gid
    postgres_uid="$(docker run --rm --entrypoint sh "${runtime_image}" -c 'id -u postgres')"
    postgres_gid="$(docker run --rm --entrypoint sh "${runtime_image}" -c 'id -g postgres')"
    install -d -o "${postgres_uid}" -g "${postgres_gid}" -m 0700 "${data_dir}"
}

domeye_database_start_candidate() {
    local container_name="$1"
    local data_dir="$2"
    local runtime_image="${DOMEYE_CORE_DB_IMAGE_RUNTIME:-${DOMEYE_CORE_DB_IMAGE}}"

    if docker inspect "${container_name}" >/dev/null 2>&1; then
        domeye_artifact_error "候选容器名称已存在：${container_name}"
        return 1
    fi
    domeye_database_prepare_empty_data_dir "${data_dir}"

    local postgres_env_file
    postgres_env_file="$(mktemp)"
    printf 'POSTGRES_PASSWORD=%s\n' "${DOMEYE_CORE_DB_ADMIN_PASSWORD}" > "${postgres_env_file}"
    chmod 0600 "${postgres_env_file}"

    if ! docker run --detach \
        --name "${container_name}" \
        --label "domeye.core.database-role=offline-candidate" \
        --memory "${DOMEYE_CORE_DATABASE_MEMORY}" \
        --shm-size 4g \
        --env "POSTGRES_DB=${DOMEYE_CORE_DB_NAME}" \
        --env "POSTGRES_USER=${DOMEYE_CORE_DB_ADMIN_USER}" \
        --env-file "${postgres_env_file}" \
        --volume "${data_dir}:/var/lib/postgresql/data" \
        "${runtime_image}" \
        postgres \
        -c "shared_buffers=${DOMEYE_CORE_DATABASE_SHARED_BUFFERS}" \
        -c 'listen_addresses=*' \
        -c 'timescaledb.telemetry_level=off' \
        >/dev/null; then
        rm -f -- "${postgres_env_file}"
        return 1
    fi
    rm -f -- "${postgres_env_file}"
    domeye_database_wait_container "${container_name}"
}

domeye_database_remove_candidate_container() {
    local container_name="$1"
    if docker inspect "${container_name}" >/dev/null 2>&1; then
        docker stop --time 30 "${container_name}" >/dev/null 2>&1 || true
        docker rm "${container_name}" >/dev/null 2>&1 || docker rm --force "${container_name}" >/dev/null
    fi
}

domeye_database_psql() {
    local container_name="$1"
    shift
    docker exec \
        "${container_name}" \
        psql -X --set ON_ERROR_STOP=1 \
        --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
        --dbname "${DOMEYE_CORE_DB_NAME}" \
        "$@"
}

domeye_database_create_pgpass() {
    local pgpass_path="$1"
    local host="$2"
    local port="$3"
    local database="$4"
    local user="$5"
    local password="$6"

    local escaped_password="${password//\\/\\\\}"
    escaped_password="${escaped_password//:/\\:}"
    printf '%s:%s:%s:%s:%s\n' "${host}" "${port}" "${database}" "${user}" "${escaped_password}" > "${pgpass_path}"
    chmod 0600 "${pgpass_path}"
}

domeye_database_verify_source_env() {
    local required_name
    for required_name in SOURCE_DB_HOST SOURCE_DB_PORT SOURCE_DB_NAME SOURCE_DB_USER SOURCE_DB_PASSWORD; do
        if [[ -z "${!required_name:-}" ]]; then
            domeye_artifact_error "源数据库配置缺少：${required_name}"
            return 1
        fi
    done
    if [[ ! "${SOURCE_DB_PORT}" =~ ^[0-9]{1,5}$ || "${SOURCE_DB_PASSWORD}" == *$'\n'* ]]; then
        domeye_artifact_error '源数据库端口或密码格式无效'
        return 1
    fi
}

domeye_database_restore_archive() {
    local container_name="$1"
    local archive_path="$2"
    DOMEYE_DATABASE_ARCHIVE_RESTORED=false

    domeye_database_psql "${container_name}" --command 'CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;' || return 1
    domeye_database_psql "${container_name}" --command 'SELECT timescaledb_pre_restore();' || return 1
    if ! zstd --quiet --decompress --stdout "${archive_path}" \
        | docker exec \
            --interactive \
            "${container_name}" \
            pg_restore \
                --exit-on-error \
                --no-owner \
                --no-acl \
                --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
                --dbname "${DOMEYE_CORE_DB_NAME}"; then
        domeye_database_psql "${container_name}" --command 'SELECT timescaledb_post_restore();' || true
        return 1
    fi
    DOMEYE_DATABASE_ARCHIVE_RESTORED=true
    domeye_database_psql "${container_name}" --command 'SELECT timescaledb_post_restore();' || return 1
}

domeye_database_apply_reader() {
    local container_name="$1"
    local sql_path="$2"
    local escaped_reader_password="${DOMEYE_CORE_DB_READER_PASSWORD//\\/\\\\}"
    {
        printf '%s\n' \
            'CREATE TEMP TABLE domeye_reader_secret(value text NOT NULL);' \
            'COPY domeye_reader_secret(value) FROM STDIN;'
        printf '%s\n' "${escaped_reader_password}"
        printf '%s\n' '\.'
        cat -- "${sql_path}"
    } | docker exec \
        --interactive \
        "${container_name}" \
        psql -X --set ON_ERROR_STOP=1 \
            --username "${DOMEYE_CORE_DB_ADMIN_USER}" \
            --dbname "${DOMEYE_CORE_DB_NAME}" \
            --set "reader_role=${DOMEYE_CORE_DB_READER_USER}" \
            --set "database_name=${DOMEYE_CORE_DB_NAME}"
}
