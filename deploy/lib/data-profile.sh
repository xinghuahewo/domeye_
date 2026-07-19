#!/usr/bin/env bash

# 当前开发阶段的数据策略。切换到实时发布前必须显式修改该文件并重新走完整验收。
readonly DOMEYE_CORE_ACTIVE_DATA_PROFILE='feb-mar-2026'
readonly DOMEYE_CORE_FIXED_DATA_START='2026-02-01 00:00:00'
readonly DOMEYE_CORE_FIXED_DATA_END_EXCLUSIVE='2026-04-01 00:00:00'
readonly DOMEYE_CORE_FIXED_SNAPSHOT_TIME='2026-03-31 23:59:59'
readonly DOMEYE_CORE_FIXED_DATABASE_PORT='31627'
readonly DOMEYE_CORE_FIXED_API_PORT='28473'
readonly DOMEYE_CORE_FIXED_API_PROFILE='core'

domeye_core_require_realtime_profile() {
    if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'realtime-release' ]]; then
        printf '错误：当前数据档为 %s，禁止连接、激活或恢复实时数据库。\n' \
            "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" >&2
        return 1
    fi
}

domeye_core_require_source_database_access() {
    if [[ "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" != 'realtime-release' ]]; then
        printf '错误：当前数据档为 %s，禁止读取原生产数据库。\n' \
            "${DOMEYE_CORE_ACTIVE_DATA_PROFILE}" >&2
        return 1
    fi
}
