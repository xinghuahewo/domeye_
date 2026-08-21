#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly TEST_ROOT="${DOMEYE_INTERACTIVE_AGENT_TEST_ROOT:-}"
if [[ -n "${TEST_ROOT}" ]]; then
    [[ "${TEST_ROOT}" =~ ^/((private/)?tmp)/domeye-interactive-agent-test\.[A-Za-z0-9._-]+$ \
        && -d "${TEST_ROOT}" && ! -L "${TEST_ROOT}" ]] || {
        printf 'Interactive Agent 部署错误：测试根目录不在允许边界\n' >&2
        exit 1
    }
    readonly RUNTIME_BASE="${TEST_ROOT}/runtime"
    readonly NODE_BIN_DIR="${TEST_ROOT}/tools/node/bin"
    readonly TRUSTED_CHECKOUT="${TEST_ROOT}/trusted-checkout"
    readonly TEST_MODE=true
else
    readonly RUNTIME_BASE='/home/bgpdata/Domeye-Core-runtime'
    readonly NODE_BIN_DIR='/home/bgpdata/.local/node-v22.23.1-linux-x64/bin'
    readonly TRUSTED_CHECKOUT='/home/bgpdata/Domeye-Core'
    readonly TEST_MODE=false
fi

readonly RUNTIME_ROOT="${RUNTIME_BASE}/country-outage-interactive-agent"
readonly RELEASE_ROOT="${RUNTIME_ROOT}/releases"
readonly CURRENT_LINK="${RUNTIME_ROOT}/current"
readonly STATE_ROOT="${RUNTIME_ROOT}/state"
readonly PROMOTION_ROOT="${STATE_ROOT}/promotions"
readonly PROMOTION_HISTORY_ROOT="${STATE_ROOT}/promotion-history"
readonly ACTIVE_STATE="${STATE_ROOT}/active.json"
readonly ROLLBACK_STATE="${STATE_ROOT}/rollback.json"
readonly CONFIG_FILE="${RUNTIME_BASE}/config/country-outage-interactive-agent.env"
readonly LOCK_FILE="${STATE_ROOT}/lifecycle.lock"
readonly SCREEN_NAME='domeye_interactive_agent_sidecar'
readonly ENTRYPOINT='agent-sidecar/dist/src/cli/serve-interactive-agent.js'
readonly CANDIDATE_RELATIVE='contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json'
readonly NODE="${NODE_BIN_DIR}/node"
readonly NPM="${NODE_BIN_DIR}/npm"

error() { printf 'Interactive Agent 部署错误：%s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }
sha256_file() { sha256sum "$1" | awk '{print $1}'; }
utc_now() { "${NODE}" -e 'process.stdout.write(new Date().toISOString())'; }

require_root() {
    [[ "${TEST_MODE}" == true || "${EUID}" -eq 0 ]] || {
        error '生命周期操作必须由 root 执行'
        return 1
    }
}

require_commands() {
    local name
    for name in awk bash chmod cmp cp curl date dirname env find flock git grep \
        id install jq ln mktemp mv pgrep readlink screen sed sha256sum sleep \
        sort ss stat tar tr unlink xargs; do
        command -v "${name}" >/dev/null 2>&1 || {
            error "缺少命令 ${name}"
            return 1
        }
    done
    [[ -x "${NODE}" ]] || { error "Node 不可执行：${NODE}"; return 1; }
}

trusted_git() {
    /usr/bin/env -i HOME="${HOME:-/root}" PATH=/usr/bin:/bin \
        LANG=C LC_ALL=C SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-}" \
        GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        GIT_CONFIG_SYSTEM=/dev/null GIT_NO_REPLACE_OBJECTS=1 \
        GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0 \
        GIT_SSH_COMMAND='/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes' \
        /usr/bin/git --no-replace-objects -C "${TRUSTED_CHECKOUT}" "$@"
}

trusted_git_line_count() {
    { trusted_git "$@" 2>/dev/null || true; } \
        | /usr/bin/awk 'END { print NR + 0 }'
}

verify_trusted_source_archive() {
    local source_archive="$1" source_commit="$2" source_tag="$3" \
        extracted="$4" scratch_root="$5"
    [[ -d "${TRUSTED_CHECKOUT}" && ! -L "${TRUSTED_CHECKOUT}" \
        && "$(readlink -f -- "${TRUSTED_CHECKOUT}")" == "${TRUSTED_CHECKOUT}" \
        && -d "${TRUSTED_CHECKOUT}/.git" ]] || {
        error "固定受信 Git checkout 无效：${TRUSTED_CHECKOUT}"
        return 1
    }
    local origin_url push_url raw_origin_url remote_names \
        remote_name_count raw_origin_count raw_push_count origin_count \
        push_count expected_origin expected_test_origin
    remote_names="$(trusted_git remote 2>/dev/null || true)"
    remote_name_count="$(trusted_git_line_count remote)"
    raw_origin_url="$(trusted_git config --local --get-all remote.origin.url 2>/dev/null || true)"
    raw_origin_count="$(trusted_git_line_count config --local --get-all remote.origin.url)"
    raw_push_count="$(trusted_git_line_count config --local --get-all remote.origin.pushurl)"
    origin_url="$(trusted_git remote get-url --all origin 2>/dev/null || true)"
    origin_count="$(trusted_git_line_count remote get-url --all origin)"
    push_url="$(trusted_git remote get-url --push --all origin 2>/dev/null || true)"
    push_count="$(trusted_git_line_count remote get-url --push --all origin)"
    expected_test_origin="${TEST_ROOT}/trusted-origin.git"
    if [[ "${TEST_MODE}" == true ]]; then
        expected_origin="${expected_test_origin}"
    else
        expected_origin='git@github.com:xinghuahewo/domeye_.git'
    fi
    [[ "${remote_name_count}" == 1 && "${remote_names}" == origin \
        && "${raw_origin_count}" == 1 \
        && "${raw_origin_url}" == "${expected_origin}" \
        && "${raw_push_count}" == 0 \
        && "${origin_count}" == 1 \
        && "${origin_url}" == "${expected_origin}" \
        && "${push_count}" == 1 \
        && "${push_url}" == "${expected_origin}" ]] || {
        if [[ "${TEST_MODE}" == true ]]; then
            error '测试受信 checkout 的 origin 不在同一临时边界'; return 1
        fi
        error '固定受信 checkout 的 origin 不是唯一且不可改写的官方 GitHub SSH remote'
        return 1
    }
    if ! trusted_git fetch --quiet --force --no-tags origin \
        "+refs/heads/main:refs/remotes/origin/main" \
        "+refs/tags/${source_tag}:refs/tags/${source_tag}"; then
        error '无法从 GitHub 权威 origin 刷新 main 与 annotated tag'
        return 1
    fi
    [[ "$(trusted_git cat-file -t \
        "refs/tags/${source_tag}" 2>/dev/null || true)" == 'tag' ]] || {
        error '发布 tag 不是固定受信 checkout 中的 annotated tag'
        return 1
    }
    [[ "$(trusted_git rev-parse \
        "refs/tags/${source_tag}^{commit}" 2>/dev/null || true)" \
        == "${source_commit}" ]] || {
        error 'annotated tag 解引用后与输入 commit 不一致'
        return 1
    }
    [[ "$(trusted_git rev-parse \
        refs/heads/main 2>/dev/null || true)" == "${source_commit}" ]] || {
        error '固定受信 checkout 的 main 与输入 commit 不一致'
        return 1
    }
    [[ "$(trusted_git rev-parse \
        refs/remotes/origin/main 2>/dev/null || true)" == "${source_commit}" ]] || {
        error '固定受信 checkout 的 origin/main 与输入 commit 不一致'
        return 1
    }

    local comparison canonical archive_tar input_manifest canonical_manifest \
        member_list verbose_list member line path
    comparison="${scratch_root}/source-comparison"
    canonical="${comparison}/canonical"
    archive_tar="${comparison}/canonical.tar"
    input_manifest="${comparison}/input-tree.bin"
    canonical_manifest="${comparison}/canonical-tree.bin"
    member_list="${comparison}/input-members.txt"
    verbose_list="${comparison}/input-members-verbose.txt"
    if ! install -d -m 0700 "${comparison}" "${canonical}"; then
        error '无法创建源码比对临时目录'; return 1
    fi
    if ! tar --list --gzip --file "${source_archive}" > "${member_list}" \
        || ! tar --list --verbose --gzip --file "${source_archive}" \
            > "${verbose_list}"; then
        error '输入源码归档无法完整列举'; return 1
    fi
    [[ -s "${member_list}" && -s "${verbose_list}" ]] || {
        error '输入源码归档为空'; return 1;
    }
    while IFS= read -r member; do
        [[ -n "${member}" \
            && "${member}" != /* \
            && "${member}" != '..' \
            && "${member}" != ../* \
            && "${member}" != */../* \
            && "${member}" != */.. ]] || {
            error '输入源码归档含越界或无效成员路径'
            return 1
        }
    done < "${member_list}"
    while IFS= read -r line; do
        [[ "${line:0:1}" == '-' || "${line:0:1}" == 'd' ]] || {
            error '输入源码归档只能包含普通文件和目录'
            return 1
        }
    done < "${verbose_list}"
    if ! trusted_git archive --format=tar \
        "${source_commit}" > "${archive_tar}"; then
        error '无法从固定受信 commit 生成 git archive'; return 1
    fi
    if ! (
        umask 077
        tar --extract --file "${archive_tar}" --directory "${canonical}" \
            --no-same-owner --no-same-permissions
        tar --extract --gzip --file "${source_archive}" --directory "${extracted}" \
            --no-same-owner --no-same-permissions
    ); then
        error '源码归档规范解包失败'; return 1
    fi
    for path in "${canonical}" "${extracted}"; do
        local unexpected
        if ! unexpected="$(find "${path}" ! -type d ! -type f -print -quit)"; then
            error '无法检查源码树文件类型'; return 1
        fi
        [[ -z "${unexpected}" ]] || {
            error '源码树含 Git archive 不支持的特殊文件'
            return 1
        }
        if ! unexpected="$(find "${path}" -type f -links +1 -print -quit)"; then
            error '无法检查源码树硬链接'; return 1
        fi
        [[ -z "${unexpected}" ]] || {
            error '源码树含硬链接，拒绝非规范文件身份'
            return 1
        }
    done
    tree_manifest() {
        local root="$1" output="$2" entry relative_path executable file_sha
        : > "${output}"
        while IFS= read -r -d '' entry; do
            relative_path="${entry#${root}/}"
            if [[ -d "${entry}" ]]; then
                printf 'directory\0%s\0' "${relative_path}" >> "${output}"
            else
                executable=false
                [[ -x "${entry}" ]] && executable=true
                file_sha="$(sha256_file "${entry}")" || return 1
                printf 'file\0%s\0%s\0sha256:%s\0' \
                    "${relative_path}" "${executable}" \
                    "${file_sha}" >> "${output}" || return 1
            fi
        done < <(find "${root}" -mindepth 1 \( -type d -o -type f \) \
            -print0 | LC_ALL=C sort -z)
    }
    if ! tree_manifest "${extracted}" "${input_manifest}" \
        || ! tree_manifest "${canonical}" "${canonical_manifest}"; then
        error '无法生成源码树规范清单'; return 1
    fi
    cmp -s "${input_manifest}" "${canonical_manifest}" || {
        error '输入源码归档规范解包树与 annotated tag commit 的 git archive 树不一致'
        return 1
    }
    find "${comparison}" -depth -delete
}

verify_candidate_git_parent_chain() {
    local source_commit="$1" candidate_file="$2" base_commit ancestry first_commit \
        parent_line changed_paths later_changes candidate_blob first_blob source_blob
    [[ "${source_commit}" =~ ^[a-f0-9]{40}$ \
        && -f "${candidate_file}" && ! -L "${candidate_file}" ]] || {
        error 'Candidate Git 父链输入无效'; return 1
    }
    base_commit="$(jq -er '
      .payload.base_commit |
      select(type=="string" and test("^[a-f0-9]{40}$"))
    ' "${candidate_file}" 2>/dev/null)" || {
        error 'Candidate payload.base_commit 无效'; return 1
    }
    if ! trusted_git cat-file -e "${base_commit}^{commit}" 2>/dev/null \
        || ! trusted_git merge-base --is-ancestor \
            "${base_commit}" "${source_commit}"; then
        error 'Candidate base_commit 不是 release source commit 的受信祖先'
        return 1
    fi
    ancestry="$(trusted_git rev-list --ancestry-path --reverse --topo-order \
        "${base_commit}..${source_commit}")" || {
        error '无法计算 Candidate 到 release source 的 ancestry path'; return 1
    }
    [[ -n "${ancestry}" ]] || {
        error 'release source 必须包含 base_commit 之后的 Candidate commit'
        return 1
    }
    first_commit="${ancestry%%$'\n'*}"
    parent_line="$(trusted_git rev-list --parents -n 1 "${first_commit}")" || {
        error '无法读取首个 Candidate ancestry commit 的父提交'; return 1
    }
    local -a parent_parts
    read -r -a parent_parts <<< "${parent_line}"
    if (( ${#parent_parts[@]} != 2 )) \
        || [[ "${parent_parts[0]}" != "${first_commit}" \
            || "${parent_parts[1]}" != "${base_commit}" ]]; then
        error '首个 Candidate ancestry commit 必须单父且父提交精确等于 base_commit'
        return 1
    fi
    changed_paths="$(trusted_git diff-tree --no-commit-id --name-only -r \
        "${base_commit}" "${first_commit}")" || {
        error '无法读取首个 Candidate commit 的变更路径'; return 1
    }
    [[ "${changed_paths}" == "${CANDIDATE_RELATIVE}" ]] || {
        error '首个 Candidate commit 必须且只能修改 candidate.json'
        return 1
    }
    later_changes="$(trusted_git log --format=%H --full-history \
        "${first_commit}..${source_commit}" -- "${CANDIDATE_RELATIVE}")" || {
        error '无法审计 Candidate manifest 后续历史'; return 1
    }
    [[ -z "${later_changes}" ]] || {
        error 'Candidate manifest 在首个 Candidate commit 后被再次修改'
        return 1
    }
    candidate_blob="$(mktemp "${RELEASE_ROOT}/.candidate-blob.XXXXXX")" || {
        error '无法创建 Candidate blob 临时文件'; return 1
    }
    if ! trusted_git cat-file blob \
        "${first_commit}:${CANDIDATE_RELATIVE}" > "${candidate_blob}"; then
        unlink "${candidate_blob}" 2>/dev/null || true
        error '首个 Candidate commit 不含受信 candidate.json'
        return 1
    fi
    if ! cmp -s "${candidate_blob}" "${candidate_file}"; then
        unlink "${candidate_blob}" 2>/dev/null || true
        error '首个 Candidate commit 与 release source 的 candidate.json 字节不一致'
        return 1
    fi
    unlink "${candidate_blob}"
    first_blob="$(trusted_git rev-parse \
        "${first_commit}:${CANDIDATE_RELATIVE}" 2>/dev/null)" || {
        error '无法读取首个 Candidate blob 身份'; return 1
    }
    source_blob="$(trusted_git rev-parse \
        "${source_commit}:${CANDIDATE_RELATIVE}" 2>/dev/null)" || {
        error 'release source 不含 Candidate manifest'; return 1
    }
    [[ "${source_blob}" == "${first_blob}" ]] || {
        error 'Candidate manifest 在首个 Candidate commit 后发生变化'
        return 1
    }
}

test_verify_trusted_source_archive() {
    [[ "${TEST_MODE}" == true ]] || {
        error '测试入口只能在显式临时测试根使用'; return 1;
    }
    (( $# == 3 )) || {
        error '用法：_test_verify_source_archive <source.tar.gz> <commit> <annotated-tag>'
        return 2
    }
    local source_archive="$1" commit="$2" tag="$3" scratch extracted staging
    [[ -f "${source_archive}" && ! -L "${source_archive}" \
        && "${commit}" =~ ^[a-f0-9]{40}$ ]] || {
        error '测试源码归档或 commit 无效'; return 1;
    }
    scratch="$(mktemp -d "${RELEASE_ROOT}/.source-gate-test.XXXXXX")"
    extracted="${scratch}/extracted"
    staging="${scratch}/staging"
    install -d -m 0700 "${extracted}" "${staging}"
    local result=0
    verify_trusted_source_archive "${source_archive}" "${commit}" \
        "${tag}" "${extracted}" "${staging}" || result=$?
    chmod -R u+w "${scratch}" 2>/dev/null || true
    find "${scratch}" -depth -delete
    return "${result}"
}

validate_release_id() {
    [[ "$1" =~ ^[0-9]{8}T[0-9]{6}Z-country-outage-interactive-agent-[a-z0-9][a-z0-9-]{0,31}$ ]] || {
        error "release-id 无效：$1"
        return 1
    }
}

release_directory() {
    validate_release_id "$1" || return 1
    printf '%s/%s\n' "${RELEASE_ROOT}" "$1"
}

promotion_file() {
    validate_release_id "$1" || return 1
    printf '%s/%s.json\n' "${PROMOTION_ROOT}" "$1"
}

owner_mode() {
    local path="$1" mode="$2" expected_uid=0 expected_gid=0
    if [[ "${TEST_MODE}" == true ]]; then
        expected_uid="$(id -u)"
        expected_gid="$(id -g)"
    fi
    local actual_uid actual_gid actual_mode
    if stat -c '%u' "${path}" >/dev/null 2>&1; then
        actual_uid="$(stat -c '%u' "${path}")"
        actual_gid="$(stat -c '%g' "${path}")"
        actual_mode="$(stat -c '%a' "${path}")"
    else
        actual_uid="$(stat -f '%u' "${path}")"
        actual_gid="$(stat -f '%g' "${path}")"
        actual_mode="$(stat -f '%Lp' "${path}")"
        actual_mode="${actual_mode#0}"
    fi
    [[ "${actual_uid}" == "${expected_uid}" \
        && "${actual_gid}" == "${expected_gid}" \
        && "${actual_mode}" == "${mode}" ]] || {
        error "所有者或权限不符 ${path}：${actual_uid}:${actual_gid}:${actual_mode}"
        return 1
    }
}

ensure_runtime_directories() {
    install -d -m 0700 "${RUNTIME_ROOT}" "${RELEASE_ROOT}" "${STATE_ROOT}" \
        "${PROMOTION_ROOT}" "${PROMOTION_HISTORY_ROOT}" \
        "${RUNTIME_BASE}/config"
}

read_config_value() {
    local key="$1"
    awk -v wanted="${key}" '
      /^[[:space:]]*(#|$)/ {next}
      {p=index($0,"="); if(p<2) next; if(substr($0,1,p-1)==wanted){n++;v=substr($0,p+1)}}
      END {if(n!=1) exit 2; print v}
    ' "${CONFIG_FILE}"
}

validate_config() {
    [[ -f "${CONFIG_FILE}" && ! -L "${CONFIG_FILE}" ]] || {
        error "配置不是普通文件：${CONFIG_FILE}"
        return 1
    }
    owner_mode "${CONFIG_FILE}" 600 || {
        error 'Interactive Agent 配置必须由受信用户持有且为 0600'
        return 1
    }
    local allowed=' COUNTRY_OUTAGE_AGENT_URL COUNTRY_OUTAGE_AGENT_SHARED_TOKEN COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN COUNTRY_OUTAGE_AGENT_HOST COUNTRY_OUTAGE_AGENT_PORT DOMEYE_API_BASE_URL COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST COUNTRY_OUTAGE_PI_AUTH_PATH COUNTRY_OUTAGE_INTERACTIVE_AGENT_API_TIMEOUT_MS COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONVERSATION_TTL_MS COUNTRY_OUTAGE_INTERACTIVE_AGENT_TURN_TIMEOUT_MS '
    local line key value seen=' '
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" || "${line}" == \#* ]] && continue
        [[ "${line}" == *=* && "${line}" != *$'\r'* ]] || {
            error '配置行无效'; return 1;
        }
        key="${line%%=*}"; value="${line#*=}"
        [[ "${key}" =~ ^[A-Z][A-Z0-9_]*$ && "${allowed}" == *" ${key} "* ]] || {
            error "未授权配置键 ${key}"; return 1;
        }
        [[ -n "${value}" && "${value}" != *[[:space:]]* ]] || {
            error "配置值为空或含空白 ${key}"; return 1;
        }
        [[ "${seen}" != *" ${key} "* ]] || {
            error "配置键重复 ${key}"; return 1;
        }
        seen+="${key} "
    done < "${CONFIG_FILE}"
    local required
    for required in ${allowed}; do
        read_config_value "${required}" >/dev/null || {
            error "配置键必须恰好出现一次 ${required}"; return 1;
        }
    done
    [[ "$(read_config_value COUNTRY_OUTAGE_AGENT_URL)" == 'http://127.0.0.1:28476' \
        && "$(read_config_value COUNTRY_OUTAGE_AGENT_HOST)" == '127.0.0.1' \
        && "$(read_config_value COUNTRY_OUTAGE_AGENT_PORT)" == '28476' \
        && "$(read_config_value DOMEYE_API_BASE_URL)" == 'http://127.0.0.1:28473/api/v2/' \
        && "$(read_config_value COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT)" == "${CURRENT_LINK}/project" \
        && "$(read_config_value COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST)" == "${CURRENT_LINK}/project/${CANDIDATE_RELATIVE}" \
        && "$(read_config_value COUNTRY_OUTAGE_INTERACTIVE_AGENT_API_TIMEOUT_MS)" == '15000' \
        && "$(read_config_value COUNTRY_OUTAGE_INTERACTIVE_AGENT_CONVERSATION_TTL_MS)" == '1800000' \
        && "$(read_config_value COUNTRY_OUTAGE_INTERACTIVE_AGENT_TURN_TIMEOUT_MS)" == '120000' ]] || {
        error 'Interactive Agent 固定运行配置漂移'
        return 1
    }
    local token verifier_token auth
    token="$(read_config_value COUNTRY_OUTAGE_AGENT_SHARED_TOKEN)"
    [[ ${#token} -ge 32 ]] || { error '共享 Token 长度不足'; return 1; }
    case "${token}" in
        replace-with-*|CHANGE_ME*) error '共享 Token 仍是示例占位值'; return 1 ;;
    esac
    verifier_token="$(read_config_value COUNTRY_OUTAGE_AGENT_VERIFIER_TOKEN)"
    [[ ${#verifier_token} -ge 32 && ${#verifier_token} -le 256 ]] || {
        error '独立验证器 Token 长度无效'; return 1;
    }
    case "${verifier_token}" in
        replace-with-*|CHANGE_ME*) error '独立验证器 Token 仍是示例占位值'; return 1 ;;
    esac
    [[ "${verifier_token}" != "${token}" ]] || {
        error '独立验证器 Token 不得与共享 Token 相同'; return 1;
    }
    auth="$(read_config_value COUNTRY_OUTAGE_PI_AUTH_PATH)"
    [[ "${auth}" == /* && -f "${auth}" && ! -L "${auth}" ]] || {
        error '模型凭据文件无效'; return 1;
    }
    owner_mode "${auth}" 600 || { error '模型凭据必须为 0600'; return 1; }
}

validate_release_launch_paths() {
    local release_id="$1" directory="$2" expected_directory current_target \
        project_root candidate_manifest
    expected_directory="$(release_directory "${release_id}")" || return 1
    [[ "${directory}" == "${expected_directory}" \
        && -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" ]] || {
        error '启动目录不是目标 release 的规范真实目录'
        return 1
    }
    [[ -L "${CURRENT_LINK}" ]] || {
        error '启动前 current 不是 symlink'
        return 1
    }
    current_target="$(readlink -f -- "${CURRENT_LINK}" 2>/dev/null || true)"
    [[ -n "${current_target}" \
        && "${current_target}" == "${RELEASE_ROOT}/"* ]] || {
        error 'current 解析路径逃逸 release 根目录'
        return 1
    }
    [[ "${current_target}" == "${directory}" ]] || {
        error 'current 指向错误 release'
        return 1
    }
    project_root="${directory}/project"
    candidate_manifest="${project_root}/${CANDIDATE_RELATIVE}"
    [[ -d "${project_root}" && ! -L "${project_root}" \
        && "$(readlink -f -- "${project_root}")" == "${project_root}" ]] || {
        error 'release project 路径无效或发生逃逸'
        return 1
    }
    [[ -f "${candidate_manifest}" && ! -L "${candidate_manifest}" \
        && "$(readlink -f -- "${candidate_manifest}")" \
            == "${candidate_manifest}" ]] || {
        error 'release Candidate 路径无效或发生逃逸'
        return 1
    }
}

bind_launch_config_line() {
    local line="$1" directory="$2" key
    key="${line%%=*}"
    case "${key}" in
        COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT)
            printf '%s=%s/project\n' "${key}" "${directory}"
            ;;
        COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST)
            printf '%s=%s/project/%s\n' \
                "${key}" "${directory}" "${CANDIDATE_RELATIVE}"
            ;;
        *)
            printf '%s\n' "${line}"
            ;;
    esac
}

verify_release() {
    local release_id="$1" directory
    directory="$(release_directory "${release_id}")"
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" ]] || {
        error "release 目录无效 ${directory}"; return 1;
    }
    [[ -f "${directory}/SHA256SUMS" && ! -L "${directory}/SHA256SUMS" ]] || {
        error '缺少 SHA256SUMS'; return 1;
    }
    if ! (cd -- "${directory}" && sha256sum -c SHA256SUMS >/dev/null); then
        error 'release SHA256SUMS 全制品校验失败'
        return 1
    fi
    cmp -s "${directory}/deployment/verify-release.mjs" \
        "${SCRIPT_DIR}/verify-release.mjs" \
        && cmp -s "${directory}/deployment/probe.mjs" \
            "${SCRIPT_DIR}/probe.mjs" || {
        error 'release 内 verifier/probe 与当前受信 lifecycle 工具不一致'
        return 1
    }
    if ! "${NODE}" "${SCRIPT_DIR}/verify-release.mjs" \
        "${directory}" >/dev/null; then
        error 'release 不可变合同闭包校验失败'
        return 1
    fi
}

verify_legacy_v1_active_release() {
    local release_id="$1" directory promotion legacy_probe
    directory="$(release_directory "${release_id}")" || return 1
    promotion="$(promotion_file "${release_id}")" || return 1
    legacy_probe="${directory}/deployment/probe.mjs"
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(readlink -f -- "${directory}")" == "${directory}" \
        && -f "${directory}/SHA256SUMS" \
        && -f "${legacy_probe}" && ! -L "${legacy_probe}" \
        && -f "${promotion}" && ! -L "${promotion}" ]] || {
        error '旧回答合同 active release 的冻结制品无效'; return 1
    }
    owner_mode "${promotion}" 600 || {
        error '旧回答合同 promotion 不是受信 0600'; return 1
    }
    jq -e --arg release_id "${release_id}" '
      .schema_version=="domeye_interactive_agent_release_manifest_v1" and
      .component=="domeye_interactive_agent_sidecar" and
      .release_id==$release_id
    ' "${directory}/RELEASE-MANIFEST.json" >/dev/null || {
        error '旧回答合同 release manifest 身份无效'; return 1
    }
    if ! (cd -- "${directory}" && sha256sum -c SHA256SUMS >/dev/null); then
        error '旧回答合同 release SHA256SUMS 校验失败'; return 1
    fi
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error '旧回答合同 active 进程身份无效'; return 1
    fi
    if ! "${NODE}" "${legacy_probe}" status \
        "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" "${promotion}" \
        >/dev/null; then
        error '旧回答合同 active/release/promotion 冻结闭包无效'; return 1
    fi
    assert_active_runtime_identity "${release_id}" >/dev/null || {
        error '旧回答合同在只读校验后运行身份漂移'; return 1
    }
}

probe_release() {
    local release_id="$1"; shift
    local directory
    directory="$(release_directory "${release_id}")"
    cmp -s "${directory}/deployment/probe.mjs" "${SCRIPT_DIR}/probe.mjs" \
        && cmp -s "${directory}/deployment/verify-release.mjs" \
            "${SCRIPT_DIR}/verify-release.mjs" || {
        error 'release 内探针工具与当前受信 lifecycle 工具不一致'
        return 1
    }
    if ! "${NODE}" "${SCRIPT_DIR}/probe.mjs" "$@"; then
        error 'Interactive Agent 组合探针失败'
        return 1
    fi
}

active_release_id() {
    [[ -f "${ACTIVE_STATE}" && ! -L "${ACTIVE_STATE}" ]] || return 1
    jq -er '
      select(.schema_version=="domeye_interactive_agent_active_v1") |
      select(.component=="domeye_interactive_agent_sidecar") |
      select(.deployment_state=="deployed") | .release_id
    ' "${ACTIVE_STATE}"
}

assert_verified_active_release() {
    local release_id="$1" directory promotion
    directory="$(release_directory "${release_id}")"
    promotion="$(promotion_file "${release_id}")"
    [[ -f "${promotion}" && ! -L "${promotion}" ]] || {
        error "active release 缺少 verified promotion：${release_id}"
        return 1
    }
    owner_mode "${promotion}" 600 || {
        error 'verified promotion 必须由受信用户持有且为 0600'
        return 1
    }
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error 'verified 前序的 active PID/监听身份无效'
        return 1
    fi
    if ! probe_release "${release_id}" status \
        "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" "${promotion}" \
        >/dev/null; then
        error 'verified 前序的 release/active/promotion 组合状态无效'
        return 1
    fi
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error 'verified 前序在组合检查后的 PID/监听身份漂移'
        return 1
    fi
}

archive_promotion_if_present() {
    local release_id="$1" source directory raw_sha receipt_id verified_at target
    source="$(promotion_file "${release_id}")" || return 1
    [[ -e "${source}" || -L "${source}" ]] || return 0
    [[ -f "${source}" && ! -L "${source}" ]] || {
        error "promotion 不是普通文件：${source}"
        return 1
    }
    owner_mode "${source}" 600 || {
        error '待归档 promotion 必须由受信用户持有且为 0600'
        return 1
    }
    directory="${PROMOTION_HISTORY_ROOT}/${release_id}"
    if ! install -d -m 0700 "${directory}"; then
        error '无法创建 promotion history 目录'; return 1
    fi
    raw_sha="$(sha256_file "${source}")" || {
        error '无法计算待归档 promotion 摘要'; return 1
    }
    receipt_id="$(jq -er '.promotion_id // ""' "${source}" 2>/dev/null || true)"
    verified_at="$(jq -er '.verified_at_utc // "unknown"' "${source}" 2>/dev/null || true)"
    verified_at="${verified_at//[^A-Za-z0-9]/}"
    [[ -n "${verified_at}" ]] || verified_at='unknown'
    [[ "${receipt_id}" =~ ^promotion-sha256:[a-f0-9]{64}$ ]] \
        || receipt_id="promotion-sha256:${raw_sha}"
    target="${directory}/${verified_at}-${receipt_id#promotion-sha256:}.json"
    [[ ! -e "${target}" && ! -L "${target}" ]] || {
        error "promotion history 已存在，拒绝覆盖：${target}"
        return 1
    }
    # promotions 与 history 位于同一 state 文件系统；-n 避免竞态覆盖。
    if ! mv -n "${source}" "${target}"; then
        error 'promotion history 原子转移命令失败'; return 1
    fi
    [[ ! -e "${source}" && ! -L "${source}" \
        && -f "${target}" && ! -L "${target}" \
        && "$(sha256_file "${target}")" == "${raw_sha}" ]] || {
        error 'promotion history 原子转移未完成或发生碰撞'
        return 1
    }
    if ! chmod 0600 "${target}" || ! owner_mode "${target}" 600; then
        error 'promotion history 无法收紧为受信 0600'
        return 1
    fi
    info "已归档旧 promotion：${target}"
}

prepare_release() {
    (( $# == 6 )) || {
        error '用法：prepare <release-id> <source.tar.gz> <commit> <annotated-tag> <approved-candidate-id> <approved-acceptance-record-id>'
        return 2
    }
    local release_id="$1" source_archive="$2" source_commit="$3" source_tag="$4" \
        approved_candidate_id="$5" approved_acceptance_id="$6"
    validate_release_id "${release_id}"
    [[ "${source_commit}" =~ ^[0-9a-f]{40}$ \
        && "${source_tag}" == "${release_id}" ]] || {
        error '提交或 annotated tag 身份无效'; return 1;
    }
    [[ "${approved_candidate_id}" =~ ^manifest:sha256:[a-f0-9]{64}$ \
        && "${approved_acceptance_id}" \
            =~ ^acceptance-record-sha256:[a-f0-9]{64}$ ]] || {
        error '外部批准的 Candidate 或 Acceptance 身份无效'; return 1;
    }
    [[ -f "${source_archive}" && ! -L "${source_archive}" ]] || {
        error '源码归档无效'; return 1;
    }
    local target staging extracted previous='' rollback_mode='fail_closed' \
        active_schema=''
    target="$(release_directory "${release_id}")"
    [[ ! -e "${target}" && ! -L "${target}" ]] || {
        error 'release 已存在'; return 1;
    }
    if [[ -e "${ACTIVE_STATE}" || -L "${ACTIVE_STATE}" ]]; then
        previous="$(active_release_id)" || {
            error 'active.json 存在但不是有效 deployed 状态，拒绝准备新 release'
            return 1
        }
        validate_release_id "${previous}"
        validate_config
        [[ "$(readlink -f -- "${CURRENT_LINK}")" == "$(release_directory "${previous}")" ]] || {
            error 'active.json 与 current 不一致'; return 1;
        }
        active_schema="$(jq -er '.schema_version' \
            "$(release_directory "${previous}")/RELEASE-MANIFEST.json")" || {
            error '无法读取 active release schema'; return 1;
        }
        if [[ "${active_schema}" == 'domeye_interactive_agent_release_manifest_v2' ]]; then
            verify_release "${previous}"
            assert_verified_active_release "${previous}" || {
                error '第二个 v2 release 只能从完整 deployed + verified 的同架构前序准备'
                return 1
            }
            rollback_mode='same_schema_only'
        elif [[ "${active_schema}" == 'domeye_interactive_agent_release_manifest_v1' ]]; then
            verify_legacy_v1_active_release "${previous}"
            # v1 只允许作为切换前现场，绝不成为 v2 的回滚目标。
            previous=''
            rollback_mode='fail_closed'
        else
            error 'active release schema 不在允许迁移边界'; return 1
        fi
    else
        previous=''
        [[ ! -e "${CURRENT_LINK}" && ! -L "${CURRENT_LINK}" ]] || {
            error '没有 active.json 时 current 必须不存在'
            return 1
        }
    fi
    staging="$(mktemp -d "${RELEASE_ROOT}/.prepare-${release_id}.XXXXXX")"
    extracted="$(mktemp -d "${RELEASE_ROOT}/.source-${release_id}.XXXXXX")"
    cleanup_prepare() {
        local path
        for path in "${staging}" "${extracted}"; do
            if [[ -n "${path}" && -d "${path}" && ! -L "${path}" ]]; then
                chmod -R u+w "${path}" 2>/dev/null || true
                find "${path}" -depth -delete
            fi
        done
    }
    trap cleanup_prepare EXIT
    chmod 0700 "${staging}" "${extracted}"
    install -d -m 0700 "${staging}/source"
    cp -P "${source_archive}" "${staging}/source/source.tar.gz"
    [[ -f "${staging}/source/source.tar.gz" \
        && ! -L "${staging}/source/source.tar.gz" ]] || {
        error '冻结源码归档不是普通文件'; return 1;
    }
    verify_trusted_source_archive "${staging}/source/source.tar.gz" \
        "${source_commit}" \
        "${source_tag}" "${extracted}" "${staging}"
    [[ -f "${extracted}/agent-sidecar/package-lock.json" \
        && -f "${extracted}/${CANDIDATE_RELATIVE}" \
        && -f "${extracted}/agent-sidecar/src/cli/serve-interactive-agent.ts" ]] || {
        error '源码归档缺少 Interactive Agent 首片制品'; return 1;
    }
    find "${extracted}" -type l -print -quit | grep -q . && {
        error '源码归档含符号链接'; return 1;
    }
    if ! verify_candidate_git_parent_chain "${source_commit}" \
        "${extracted}/${CANDIDATE_RELATIVE}"; then
        return 1
    fi
    local candidate_id acceptance_path='' acceptance_relative path matches=0
    candidate_id="$(jq -er '.candidate_id' \
        "${extracted}/${CANDIDATE_RELATIVE}")"
    [[ "${candidate_id}" == "${approved_candidate_id}" ]] || {
        error '源码 Candidate 与外部批准的 Candidate ID 不一致'; return 1;
    }
    while IFS= read -r path; do
        if jq -e --arg candidate_id "${candidate_id}" \
          --arg acceptance_id "${approved_acceptance_id}" '
          .schema_version=="domeye_first_slice_acceptance_record_v2" and
          .candidate_id==$candidate_id and
          .acceptance_record_id==$acceptance_id and
          .evaluation_phase=="formal" and
          .acceptance_state=="accepted" and
          .dg1_decision=="GO" and
          .reporting.workflow_answer_success.evaluated_run_count==30 and
          .reporting.workflow_answer_success.successful_answer_count==30 and
          .reporting.workflow_answer_success.pass_at_1_met==true and
          .reporting.workflow_answer_success.pass_power_3_met==true
        ' "${path}" >/dev/null; then
            acceptance_path="${path}"
            ((matches+=1))
        fi
    done < <(find "${extracted}/evaluation/country-outage/first-vertical-slice/runs" \
        -type f -name acceptance-record-final.json | LC_ALL=C sort)
    (( matches == 1 )) || {
        error '源码归档必须恰好包含一份与外部批准身份匹配的 final Acceptance Record'
        return 1
    }
    acceptance_relative="${acceptance_path#${extracted}/}"
    (
        cd -- "${extracted}/agent-sidecar"
        export PATH="${NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        "${NPM}" ci --ignore-scripts
        "${NODE}" scripts/apply_pi_response_model_patch.mjs --apply
        "${NPM}" test
        # TypeScript 是运行期 Acceptance finalizer 重放依赖，必须纳入审计。
        "${NPM}" audit --audit-level=high
    )
    install -d -m 0700 "${staging}/deployment"
    cmp -s "${extracted}/deploy/country-outage-agent/p1-chat/verify-release.mjs" \
        "${SCRIPT_DIR}/verify-release.mjs" \
        && cmp -s "${extracted}/deploy/country-outage-agent/p1-chat/probe.mjs" \
            "${SCRIPT_DIR}/probe.mjs" || {
        error '源码归档中的受信 verifier/probe 与当前 manager 不一致'
        return 1
    }
    "${NODE}" \
        "${extracted}/deploy/country-outage-agent/p1-chat/verify-release.mjs" \
        acceptance-replay "${extracted}" "${acceptance_relative}" \
        "${approved_candidate_id}" "${approved_acceptance_id}" \
        > "${staging}/deployment/ACCEPTANCE-REPLAY.json"
    (
        cd -- "${extracted}/agent-sidecar"
        export PATH="${NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        "${NODE}" scripts/apply_pi_response_model_patch.mjs --verify
    )
    [[ -f "${extracted}/agent-sidecar/dist/src/cli/serve-interactive-agent.js" ]] || {
        error '构建未产生唯一 Interactive Agent 入口'; return 1;
    }
    while IFS= read -r path; do find "${path}" -depth -delete; done < <(
        find "${extracted}/agent-sidecar/node_modules" -type d -name .bin -print
    )

    install -d -m 0700 "${staging}/project"
    cp -R "${extracted}/." "${staging}/project/"
    cp "${extracted}/deploy/country-outage-agent/p1-chat/verify-release.mjs" \
        "${extracted}/deploy/country-outage-agent/p1-chat/probe.mjs" \
        "${staging}/deployment/"
    local source_recheck="${staging}/source-recheck"
    install -d -m 0700 "${source_recheck}"
    verify_trusted_source_archive "${staging}/source/source.tar.gz" \
        "${source_commit}" "${source_tag}" "${source_recheck}" "${staging}"
    find "${source_recheck}" -depth -delete
    find "${staging}" -type l -print -quit | grep -q . && {
        error '运行制品含符号链接'; return 1;
    }
    local candidate_file acceptance_file source_sha replay_file run_relative \
        summary_file evidence_file execution_attestation_file review_file
    candidate_file="${staging}/project/${CANDIDATE_RELATIVE}"
    acceptance_file="${staging}/project/${acceptance_relative}"
    replay_file="${staging}/deployment/ACCEPTANCE-REPLAY.json"
    run_relative="${acceptance_relative%/acceptance-record-final.json}"
    [[ "${run_relative}" != "${acceptance_relative}" ]] || {
        error 'Acceptance Record 运行目录无效'; return 1;
    }
    summary_file="${staging}/project/${run_relative}/summary.json"
    evidence_file="${staging}/project/${run_relative}/evidence.jsonl"
    execution_attestation_file="${staging}/project/${run_relative}/evidence-attestation.json"
    review_file="${staging}/project/${run_relative}/independent-review.json"
    source_sha="$(sha256_file "${staging}/source/source.tar.gz")"
    jq -n \
        --arg release_id "${release_id}" \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg commit "${source_commit}" \
        --arg tag "${source_tag}" \
        --arg source_sha "sha256:${source_sha}" \
        --arg candidate_id "${candidate_id}" \
        --arg candidate_sha "sha256:$(sha256_file "${candidate_file}")" \
        --arg candidate_payload_digest "${candidate_id#manifest:}" \
        --arg candidate_schema "$(jq -er '.payload.schema_version' "${candidate_file}")" \
        --arg attestation_policy_digest "$(jq -er '.payload.attestation_policy_digest' "${execution_attestation_file}")" \
        --arg acceptance_path "project/${acceptance_relative}" \
        --arg acceptance_id "$(jq -er '.acceptance_record_id' "${acceptance_file}")" \
        --arg acceptance_sha "sha256:$(sha256_file "${acceptance_file}")" \
        --arg evaluation_run_id "$(jq -er '.evaluation_run_id' "${acceptance_file}")" \
        --arg evaluation_phase "$(jq -er '.evaluation_phase' "${acceptance_file}")" \
        --arg acceptance_state "$(jq -er '.acceptance_state' "${acceptance_file}")" \
        --arg dg1_decision "$(jq -er '.dg1_decision' "${acceptance_file}")" \
        --arg summary_path "project/${run_relative}/summary.json" \
        --arg summary_digest "$(jq -er '.summary_digest' "${summary_file}")" \
        --arg summary_sha "sha256:$(sha256_file "${summary_file}")" \
        --arg evidence_path "project/${run_relative}/evidence.jsonl" \
        --arg evidence_sha "sha256:$(sha256_file "${evidence_file}")" \
        --arg execution_path "project/${run_relative}/evidence-attestation.json" \
        --arg execution_id "$(jq -er '.attestation_id' "${execution_attestation_file}")" \
        --arg execution_digest "$(jq -er '.execution_attestation_digest' "${acceptance_file}")" \
        --arg execution_sha "sha256:$(sha256_file "${execution_attestation_file}")" \
        --arg review_path "project/${run_relative}/independent-review.json" \
        --arg review_digest "$(jq -er '.independent_review.review_digest' "${acceptance_file}")" \
        --arg review_sha "sha256:$(sha256_file "${review_file}")" \
        --arg replay_sha "sha256:$(sha256_file "${replay_file}")" \
        --arg rollback_mode "${rollback_mode}" \
        --arg previous "${previous}" \
        '{
          schema_version:"domeye_interactive_agent_release_manifest_v2",
          component:"domeye_interactive_agent_sidecar",
          release_id:$release_id,
          created_at_utc:$created_at,
          source:{commit:$commit,annotated_tag:$tag,archive_path:"source/source.tar.gz",archive_sha256:$source_sha},
          candidate:{
            manifest_path:"project/contracts/agent/domeye-first-vertical-slice/v1.1/candidate.json",
            candidate_id:$candidate_id,
            manifest_sha256:$candidate_sha,
            manifest_payload_digest:$candidate_payload_digest,
            schema_version:$candidate_schema,
            attestation_policy_digest:$attestation_policy_digest,
            activation_scope:"local_evaluation_only",
            production_deployed:false
          },
          acceptance:{
            record_path:$acceptance_path,
            record_id:$acceptance_id,
            record_sha256:$acceptance_sha,
            evaluation_run_id:$evaluation_run_id,
            evaluation_phase:$evaluation_phase,
            acceptance_state:$acceptance_state,
            dg1_decision:$dg1_decision,
            summary_path:$summary_path,
            summary_digest:$summary_digest,
            summary_json_sha256:$summary_sha,
            evidence_jsonl_path:$evidence_path,
            evidence_jsonl_sha256:$evidence_sha,
            execution_attestation_path:$execution_path,
            execution_attestation_id:$execution_id,
            execution_attestation_digest:$execution_digest,
            execution_attestation_sha256:$execution_sha,
            independent_review_path:$review_path,
            independent_review_digest:$review_digest,
            independent_review_sha256:$review_sha,
            replay_receipt_path:"deployment/ACCEPTANCE-REPLAY.json",
            replay_receipt_sha256:$replay_sha
          },
          runtime:{
            entrypoint:"agent-sidecar/dist/src/cli/serve-interactive-agent.js",
            host:"127.0.0.1",port:28476,base_path:"/country-outage/chat",
            activation_scope:"local_evaluation_only",candidate_production_deployed:false
          },
          live_verification:{
            public_backend_origin:"http://127.0.0.1:28471",
            backend_base_path:"/api/v2/country-outage/chat",
            internal_sidecar_origin:"http://127.0.0.1:28476",
            internal_record_base_path:"/country-outage/chat/internal",
            public_conversation_schema_version:"domeye_interactive_agent_conversation_v2",
            internal_record_schema_version:"domeye_interactive_agent_turn_internal_record_v1",
            event_reference:"country_outage/2026-02-27 09:12:32/IR/1/r",
            question:"在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？",
            oracle:{metric:"fixed_visible_ipv4_address_count",unit:"unique_ipv4_address",time_slot_count:3455,observed_point_count:3455,null_point_count:0,first:10156800,first_at_utc:"2026-02-27T00:10:00Z",last:10069760,last_at_utc:"2026-03-11T00:00:00Z",minimum:9577728,minimum_at_utc:"2026-02-28T14:35:00Z",maximum:10156800,maximum_at_utc:"2026-02-27T00:10:00Z",difference:579072,net_change:-87040},
            oracle_digest:"sha256:cdcde8dcad6815d891d99fb7da4fd2ebb757b1e368f3692184bfd225be475685"
          },
          rollback:{mode:$rollback_mode,previous_release_id:(if $previous=="" then null else $previous end)}
        }' > "${staging}/RELEASE-MANIFEST.json"
    "${NODE}" "${SCRIPT_DIR}/verify-release.mjs" "${staging}" >/dev/null
    chmod 0500 "${staging}/deployment/verify-release.mjs" \
        "${staging}/deployment/probe.mjs" \
        "${staging}/project/${ENTRYPOINT}"
    (
        cd -- "${staging}"
        find . -type f ! -name SHA256SUMS -print0 \
            | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    )
    chmod -R go-w "${staging}"
    mv "${staging}" "${target}"
    staging=''
    trap - EXIT
    cleanup_prepare
    info "Interactive Agent release 已准备：${target}"
}

list_sessions() {
    screen -ls 2>&1 | awk -v name="${SCREEN_NAME}" \
        '$1 ~ ("^[0-9]+\\." name "$") {print $1}'
}

runtime_pid() {
    local session="$1" directory="$2" root_pid="${session%%.*}" current child argument
    local -a queue=("${root_pid}") children args
    local seen=" ${root_pid} "
    while (( ${#queue[@]} > 0 )); do
        current="${queue[0]}"; queue=("${queue[@]:1}")
        [[ -r "/proc/${current}/cmdline" ]] && {
            args=()
            while IFS= read -r -d '' argument; do
                args+=("${argument}")
            done < "/proc/${current}/cmdline"
            if (( ${#args[@]} == 2 )) \
                && [[ "${args[0]}" == "${NODE}" \
                    && "${args[1]}" == "${directory}/project/${ENTRYPOINT}" \
                    && "$(readlink -f -- "/proc/${current}/cwd")" == "${directory}/project" ]]; then
                    printf '%s\n' "${current}"
                    return 0
            fi
        }
        children=()
        while IFS= read -r child; do
            [[ -n "${child}" ]] || continue
            case "${seen}" in
                *" ${child} "*) ;;
                *) children+=("${child}"); seen+="${child} " ;;
            esac
        done < <(pgrep -P "${current}" 2>/dev/null || true)
        queue+=("${children[@]}")
    done
    return 1
}

listener_output_matches_runtime() {
    local pid="$1" sockets="$2"
    [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || return 1
    awk -v marker="pid=${pid}," '
      NF {
        count += 1
        if ($4 == "127.0.0.1:28476" && index($0, marker) > 0) matched += 1
      }
      END {exit !(count == 1 && matched == 1)}
    ' <<< "${sockets}"
}

assert_runtime_identity() {
    local expected_release="$1" directory session pid sockets
    directory="$(release_directory "${expected_release}")"
    [[ -L "${CURRENT_LINK}" \
        && "$(readlink -f -- "${CURRENT_LINK}")" == "${directory}" ]] || {
        error 'current 未精确指向 active release'; return 1;
    }
    local -a sessions
    sessions=()
    while IFS= read -r session; do
        [[ -n "${session}" ]] && sessions+=("${session}")
    done < <(list_sessions)
    (( ${#sessions[@]} == 1 )) || {
        error 'Interactive Agent 进程会话数量不是 1'; return 1;
    }
    session="${sessions[0]}"
    pid="$(runtime_pid "${session}" "${directory}")" || {
        error 'Interactive Agent 进程入口身份不一致'; return 1;
    }
    if ! sockets="$(ss -H -ltnp 'sport = :28476')"; then
        error '无法查询 28476 监听进程'; return 1
    fi
    if ! listener_output_matches_runtime "${pid}" "${sockets}"; then
        error '28476 监听进程与 release 入口 PID 不一致'; return 1;
    fi
    printf '%s\n' "${pid}"
}

assert_active_runtime_identity() {
    local release_id="$1" listener_pid active_pid
    listener_pid="$(assert_runtime_identity "${release_id}")" || return 1
    [[ -f "${ACTIVE_STATE}" && ! -L "${ACTIVE_STATE}" ]] || {
        error 'active.json 不存在或不是普通文件'; return 1;
    }
    active_pid="$(jq -er --arg release_id "${release_id}" '
      select(.schema_version=="domeye_interactive_agent_active_v1") |
      select(.component=="domeye_interactive_agent_sidecar") |
      select(.release_id==$release_id and .deployment_state=="deployed") |
      .runtime.pid
    ' "${ACTIVE_STATE}")" || {
        error 'active.json 的进程身份无效'; return 1;
    }
    [[ "${active_pid}" == "${listener_pid}" ]] || {
        error 'active.json PID 与 28476 实际监听入口 PID 不一致'
        return 1
    }
    printf '%s\n' "${listener_pid}"
}

stop_process() {
    local -a sessions
    local session
    sessions=()
    while IFS= read -r session; do
        [[ -n "${session}" ]] && sessions+=("${session}")
    done < <(list_sessions)
    (( ${#sessions[@]} <= 1 )) || {
        error '发现多个 Interactive Agent Screen 会话'; return 1;
    }
    if (( ${#sessions[@]} == 1 )); then
        screen -S "${sessions[0]}" -X quit
    fi
    local attempt listeners
    for ((attempt=1; attempt<=30; attempt++)); do
        sessions=()
        while IFS= read -r session; do
            [[ -n "${session}" ]] && sessions+=("${session}")
        done < <(list_sessions)
        if (( ${#sessions[@]} == 0 )); then
            if ! listeners="$(ss -H -ltn 'sport = :28476')"; then
                error '无法查询 28476 监听状态'
                return 1
            fi
            [[ -z "${listeners}" ]] && return 0
        fi
        sleep 0.2
    done
    error 'Interactive Agent 未在 6 秒内停止，或 28476 仍有监听者'
    return 1
}

write_active() {
    local release_id="$1" pid="$2" manifest previous mode temporary \
        activated_at manifest_sha candidate_id temporary_sha active_sha
    manifest="$(release_directory "${release_id}")/RELEASE-MANIFEST.json" \
        || return 1
    previous="$(jq -er '.rollback.previous_release_id // ""' "${manifest}")" \
        || { error '无法读取 active rollback 前序'; return 1; }
    mode="$(jq -er '.rollback.mode' "${manifest}")" \
        || { error '无法读取 active rollback 模式'; return 1; }
    activated_at="$(utc_now)" \
        || { error '无法生成 active 激活时间'; return 1; }
    manifest_sha="$(sha256_file "${manifest}")" \
        || { error '无法计算 release manifest 摘要'; return 1; }
    candidate_id="$(jq -er '.candidate.candidate_id' "${manifest}")" \
        || { error '无法读取 active Candidate 身份'; return 1; }
    temporary="$(mktemp "${STATE_ROOT}/.active.XXXXXX")" \
        || { error '无法创建 active 临时回执'; return 1; }
    if ! jq -n \
        --arg release_id "${release_id}" \
        --arg activated_at "${activated_at}" \
        --arg manifest_sha "sha256:${manifest_sha}" \
        --arg candidate_id "${candidate_id}" \
        --argjson pid "${pid}" --arg previous "${previous}" --arg mode "${mode}" \
        '{
          schema_version:"domeye_interactive_agent_active_v1",
          component:"domeye_interactive_agent_sidecar",
          release_id:$release_id,deployment_state:"deployed",
          activated_at_utc:$activated_at,release_manifest_sha256:$manifest_sha,
          candidate_id:$candidate_id,
          runtime:{screen_name:"domeye_interactive_agent_sidecar",pid:$pid,entrypoint:"agent-sidecar/dist/src/cli/serve-interactive-agent.js",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"},
          rollback:{mode:$mode,previous_release_id:(if $previous=="" then null else $previous end)}
        }' > "${temporary}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法生成 active 临时回执'
        return 1
    fi
    if ! jq -e --arg release_id "${release_id}" \
        --arg activated_at "${activated_at}" \
        --arg manifest_sha "sha256:${manifest_sha}" \
        --arg candidate_id "${candidate_id}" \
        --arg mode "${mode}" --arg previous "${previous}" \
        --argjson pid "${pid}" '
      keys==["activated_at_utc","candidate_id","component","deployment_state","release_id","release_manifest_sha256","rollback","runtime","schema_version"] and
      .schema_version=="domeye_interactive_agent_active_v1" and
      .component=="domeye_interactive_agent_sidecar" and
      .release_id==$release_id and .deployment_state=="deployed" and
      .activated_at_utc==$activated_at and
      .release_manifest_sha256==$manifest_sha and .candidate_id==$candidate_id and
      .runtime=={screen_name:"domeye_interactive_agent_sidecar",pid:$pid,entrypoint:"agent-sidecar/dist/src/cli/serve-interactive-agent.js",host:"127.0.0.1",port:28476,base_path:"/country-outage/chat"} and
      .rollback=={mode:$mode,previous_release_id:(if $previous=="" then null else $previous end)}
    ' "${temporary}" >/dev/null; then
        unlink "${temporary}" 2>/dev/null || true
        error 'active 临时回执内容校验失败'
        return 1
    fi
    if ! chmod 0600 "${temporary}"; then
        unlink "${temporary}" 2>/dev/null || true
        error 'active 临时回执权限设置失败'
        return 1
    fi
    temporary_sha="$(sha256_file "${temporary}")" || {
        unlink "${temporary}" 2>/dev/null || true
        error '无法计算 active 临时回执摘要'
        return 1
    }
    if ! mv -n "${temporary}" "${ACTIVE_STATE}"; then
        unlink "${temporary}" 2>/dev/null || true
        error 'active.json 原子写入命令失败'
        return 1
    fi
    [[ ! -e "${temporary}" && ! -L "${temporary}" \
        && -f "${ACTIVE_STATE}" && ! -L "${ACTIVE_STATE}" ]] || {
        unlink "${temporary}" 2>/dev/null || true
        error 'active.json 已存在或原子写入失败，拒绝覆盖'
        return 1
    }
    active_sha="$(sha256_file "${ACTIVE_STATE}")" || {
        error '无法计算 active.json 原子写入后摘要'
        return 1
    }
    [[ "${active_sha}" == "${temporary_sha}" ]] || {
        error 'active.json 原子写入后内容摘要漂移'
        return 1
    }
    owner_mode "${ACTIVE_STATE}" 600 || return 1
}

write_rollback_state() {
    local temporary
    [[ -f "${ACTIVE_STATE}" && ! -L "${ACTIVE_STATE}" ]] || {
        error '无法从无效 active.json 生成 rollback.json'
        return 1
    }
    temporary="$(mktemp "${STATE_ROOT}/.rollback.XXXXXX")" || {
        error '无法创建 rollback 临时回执'; return 1
    }
    if ! cp "${ACTIVE_STATE}" "${temporary}" \
        || ! chmod 0600 "${temporary}" \
        || ! mv "${temporary}" "${ROLLBACK_STATE}"; then
        unlink "${temporary}" 2>/dev/null || true
        error '无法原子写入 rollback.json'
        return 1
    fi
    owner_mode "${ROLLBACK_STATE}" 600 || return 1
}

launch_release() {
    local release_id="$1" directory link_candidate log_file pid config_sha
    directory="$(release_directory "${release_id}")" || return 1
    link_candidate="${RUNTIME_ROOT}/.current-${release_id}"
    [[ ! -e "${link_candidate}" && ! -L "${link_candidate}" ]] || {
        error 'current 临时链接已存在，拒绝覆盖'; return 1
    }
    if ! ln -s "${directory}" "${link_candidate}" \
        || ! mv -Tf "${link_candidate}" "${CURRENT_LINK}"; then
        unlink "${link_candidate}" 2>/dev/null || true
        error '无法原子切换 current 到目标 release'
        return 1
    fi
    if ! validate_release_launch_paths "${release_id}" "${directory}"; then
        force_fail_closed || true
        error 'current 与目标 release 的启动路径绑定失败'
        return 1
    fi
    # Token 不能作为 env 命令参数展开。current 切换后重新校验配置，子进程再按
    # 同一字节摘要读取 0600 文件；只在子进程内导出键值并覆盖 release 绑定路径。
    if ! validate_config; then
        force_fail_closed || true
        error '启动前 Interactive Agent 配置无效'
        return 1
    fi
    config_sha="$(sha256_file "${CONFIG_FILE}")" || {
        force_fail_closed || true
        error '无法计算启动配置摘要'
        return 1
    }
    log_file="${RUNTIME_ROOT}/interactive-agent-${release_id}.log"
    if ! screen -L -Logfile "${log_file}" -dmS "${SCREEN_NAME}" \
        env -i HOME=/home/bgpdata USER=root LOGNAME=root LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH="${NODE_BIN_DIR}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        bash -c '
          set -euo pipefail
          expected_sha="$1"
          config_file="$2"
          release_directory="$3"
          node="$4"
          entrypoint="$5"
          candidate_relative="$6"
          [[ -f "${config_file}" && ! -L "${config_file}" ]]
          actual_sha="$(sha256sum -- "${config_file}")"
          actual_sha="${actual_sha%% *}"
          [[ "${actual_sha}" == "${expected_sha}" ]]
          while IFS= read -r line || [[ -n "${line}" ]]; do
            [[ -z "${line}" || "${line}" == \#* ]] && continue
            key="${line%%=*}"
            value="${line#*=}"
            case "${key}" in
              COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT)
                value="${release_directory}/project"
                ;;
              COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST)
                value="${release_directory}/project/${candidate_relative}"
                ;;
            esac
            export "${key}=${value}"
          done < "${config_file}"
          actual_sha="$(sha256sum -- "${config_file}")"
          actual_sha="${actual_sha%% *}"
          [[ "${actual_sha}" == "${expected_sha}" ]]
          cd -- "${release_directory}/project"
          exec "${node}" "${release_directory}/project/${entrypoint}"
        ' _ "${config_sha}" "${CONFIG_FILE}" "${directory}" "${NODE}" \
            "${ENTRYPOINT}" "${CANDIDATE_RELATIVE}"; then
        force_fail_closed || true
        error '无法创建 Interactive Agent Screen 进程'
        return 1
    fi
    local attempt
    for ((attempt=1; attempt<=120; attempt++)); do
        if pid="$(assert_runtime_identity "${release_id}" 2>/dev/null)" \
            && probe_release "${release_id}" readiness \
                "${CONFIG_FILE}" "${directory}" >/dev/null 2>&1; then
            # active.json 只在 current、进程、端口和 Candidate readiness 同时一致后生成。
            if ! write_active "${release_id}" "${pid}"; then
                force_fail_closed || true
                error '进程已就绪但 active.json 原子写入失败'
                return 1
            fi
            if probe_release "${release_id}" status \
                "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" - \
                >/dev/null 2>&1 \
                && assert_active_runtime_identity "${release_id}" \
                    >/dev/null 2>&1; then
                return 0
            fi
            if [[ -e "${ACTIVE_STATE}" || -L "${ACTIVE_STATE}" ]] \
                && ! unlink "${ACTIVE_STATE}"; then
                force_fail_closed || true
                error '组合状态失败后无法清除 active.json'
                return 1
            fi
        fi
        sleep 0.5
    done
    if ! force_fail_closed; then
        error "Interactive Agent 启动失败且无法证明失败关闭，日志 ${log_file}"
        return 1
    fi
    error "Interactive Agent 60 秒内未就绪，日志 ${log_file}"
    return 1
}

force_fail_closed() {
    local release_id='' archive_failed=false
    release_id="$(active_release_id 2>/dev/null || true)"
    if [[ -n "${release_id}" ]]; then
        archive_promotion_if_present "${release_id}" || archive_failed=true
    fi
    if ! stop_process; then
        error '无法证明 Interactive Agent 进程与 28476 监听已停止；保留 active/current 供处置'
        return 1
    fi
    if ! clear_active_current_state; then
        error '进程与端口已停止，但无法完整清除 active/current'
        return 1
    fi
    [[ "${archive_failed}" == false ]] || {
        error '失败关闭已完成，但旧 promotion 未能归档'
        return 1
    }
}

clear_active_current_state() {
    # 先清 current；若失败则保留 active 作为可处置的状态证据。
    if [[ -e "${CURRENT_LINK}" || -L "${CURRENT_LINK}" ]] \
        && ! unlink "${CURRENT_LINK}"; then
        error '无法清除 current 链接，保留 active 状态证据'
        return 1
    fi
    if [[ -e "${ACTIVE_STATE}" || -L "${ACTIVE_STATE}" ]] \
        && ! unlink "${ACTIVE_STATE}"; then
        error 'current 已清除但 active.json 无法清除'
        return 1
    fi
}

restore_previous_verified() {
    local previous="$1"
    if ! verify_release "${previous}"; then
        error '同 schema 前序 release 不可变闭包无效，拒绝启动'
        return 1
    fi
    if ! validate_config; then
        error '同 schema 前序恢复配置无效，拒绝启动'
        return 1
    fi
    # 兼容旧 manager 遗留的 stale promotion；它绝不能证明新的 active receipt。
    if ! archive_promotion_if_present "${previous}"; then
        error '同 schema 前序旧 promotion 无法安全归档，拒绝启动'
        return 1
    fi
    if ! launch_release "${previous}"; then
        if force_fail_closed; then
            error '同 schema 前序未重新部署；已证明失败关闭'
        else
            error '同 schema 前序未重新部署，且无法证明进程与端口已失败关闭'
        fi
        return 1
    fi
    if ! promote_runtime "${previous}"; then
        if force_fail_closed; then
            error '同 schema 前序未重新通过公开 E2E 验证；已证明失败关闭'
        else
            error '同 schema 前序晋级失败，且无法证明进程与端口已失败关闭'
        fi
        return 1
    fi
    if ! assert_verified_active_release "${previous}"; then
        if force_fail_closed; then
            error '同 schema 前序恢复后的组合状态无效；已证明失败关闭'
        else
            error '同 schema 前序组合状态无效，且无法证明进程与端口已失败关闭'
        fi
        return 1
    fi
    info "同 schema 前序已重新部署并验证：${previous}"
}

start_runtime() {
    (( $# == 1 )) || { error '用法：start <release-id>'; return 2; }
    local release_id="$1" previous='' restore_previous='' manifest_previous \
        active_schema=''
    verify_release "${release_id}"
    validate_config
    manifest_previous="$(jq -er '.rollback.previous_release_id // ""' \
        "$(release_directory "${release_id}")/RELEASE-MANIFEST.json")"
    if [[ -e "${ACTIVE_STATE}" || -L "${ACTIVE_STATE}" ]]; then
        previous="$(active_release_id)" || {
            error 'active.json 存在但无法证明 deployed 状态，拒绝覆盖'
            return 1
        }
        [[ "${previous}" != "${release_id}" ]] || {
            error '目标 release 已 active'; return 1;
        }
        active_schema="$(jq -er '.schema_version' \
            "$(release_directory "${previous}")/RELEASE-MANIFEST.json")" || {
            error '无法读取切换前 active release schema'; return 1;
        }
        if [[ "${active_schema}" == 'domeye_interactive_agent_release_manifest_v2' ]]; then
            verify_release "${previous}"
            [[ "${manifest_previous}" == "${previous}" ]] || {
                error 'release 绑定的同架构 rollback 前序与 active 不一致'; return 1;
            }
            assert_verified_active_release "${previous}" || {
                error '切换前序不是完整 deployed + verified 状态'; return 1;
            }
            restore_previous="${previous}"
        elif [[ "${active_schema}" == 'domeye_interactive_agent_release_manifest_v1' ]]; then
            [[ -z "${manifest_previous}" ]] || {
                error 'v1→v2 迁移 release 不得把旧合同绑定为回滚前序'; return 1;
            }
            verify_legacy_v1_active_release "${previous}"
        else
            error '切换前 active release schema 不在允许迁移边界'; return 1
        fi
        archive_promotion_if_present "${previous}" || return 1
        write_rollback_state || return 1
        stop_process || return 1
        clear_active_current_state || return 1
    else
        local -a sessions
        local session
        sessions=()
        while IFS= read -r session; do
            [[ -n "${session}" ]] && sessions+=("${session}")
        done < <(list_sessions)
        (( ${#sessions[@]} == 0 )) || {
            error '存在无 active.json 的未知 Interactive Agent 进程'; return 1;
        }
        stop_process
        [[ -z "${manifest_previous}" ]] || {
            error '没有 active release 时不能启动绑定前序的 release'; return 1;
        }
    fi
    if launch_release "${release_id}"; then
        info "Interactive Agent 已部署：${release_id}"
        return 0
    fi
    if [[ -n "${restore_previous}" ]]; then
        info "新 release 启动失败，正在恢复同 schema 前序 ${restore_previous}"
        if restore_previous_verified "${restore_previous}"; then
            error "新 release 启动失败；前序 ${restore_previous} 已重新验证恢复"
        else
            error '新 release 与同 schema 前序均未形成 verified 状态，保持失败关闭'
        fi
    fi
    return 1
}

status_runtime() {
    local release_id promotion output
    release_id="$(active_release_id)" || {
        error '没有真实 deployed 的 active.json'; return 1;
    }
    verify_release "${release_id}"
    validate_config
    assert_active_runtime_identity "${release_id}" >/dev/null
    promotion="$(promotion_file "${release_id}")"
    [[ -f "${promotion}" ]] || promotion='-'
    output="$(probe_release "${release_id}" status \
        "${CONFIG_FILE}" "$(release_directory "${release_id}")" \
        "${ACTIVE_STATE}" "${promotion}")"
    assert_active_runtime_identity "${release_id}" >/dev/null
    printf '%s\n' "${output}"
}

deactivate_runtime() {
    if [[ -f "${ACTIVE_STATE}" && ! -L "${ACTIVE_STATE}" ]]; then
        local release_id
        release_id="$(active_release_id)" || {
            error 'active.json 无法解析，拒绝静默覆盖状态'; return 1;
        }
        archive_promotion_if_present "${release_id}" || return 1
        write_rollback_state || return 1
        stop_process || return 1
        clear_active_current_state || return 1
    else
        stop_process || return 1
        clear_active_current_state || return 1
    fi
    info 'Interactive Agent 已停止；active 状态已清除'
}

rollback_runtime() {
    local current_release manifest mode previous
    current_release="$(active_release_id)" || {
        error '没有可回滚的 active release'; return 1;
    }
    verify_release "${current_release}" || return 1
    manifest="$(release_directory "${current_release}")/RELEASE-MANIFEST.json"
    mode="$(jq -er '.rollback.mode' "${manifest}")" || return 1
    previous="$(jq -er '.rollback.previous_release_id // ""' "${manifest}")" \
        || return 1
    archive_promotion_if_present "${current_release}" || return 1
    write_rollback_state || return 1
    stop_process || return 1
    clear_active_current_state || return 1
    if [[ "${mode}" == 'fail_closed' ]]; then
        info '首个新架构 release 已停止；按 fail_closed 不启动任何旧 release'
        return 0
    fi
    [[ "${mode}" == 'same_schema_only' && -n "${previous}" ]] || {
        error 'rollback 状态无效，保持失败关闭'; return 1;
    }
    verify_release "${previous}" || return 1
    restore_previous_verified "${previous}" || return 1
    info "已回滚并重新验证同 schema Interactive Agent：${previous}"
}

backend_request() {
    local method="$1" url="$2" body="${3:-}"
    local -a arguments=(--disable --noproxy '*' --proto '=http' \
        --max-redirs 0 --fail-with-body --silent --show-error --max-time 125 \
        --request "${method}" --header 'Accept: application/json')
    if [[ -n "${body}" ]]; then
        arguments+=(--header 'Content-Type: application/json' --data-binary "@${body}")
    fi
    curl "${arguments[@]}" "${url}"
}

promote_runtime() {
    (( $# == 1 )) || { error '用法：promote <release-id>'; return 2; }
    local release_id="$1" active_id directory promotion api_base request_id \
        request_epoch current_epoch
    active_id="$(active_release_id)" || { error '没有 deployed active release'; return 1; }
    [[ "${active_id}" == "${release_id}" ]] || {
        error '只能验证当前 active release'; return 1;
    }
    if ! verify_release "${release_id}"; then
        error '晋级前 release 不可变闭包校验失败'; return 1
    fi
    if ! validate_config; then
        error '晋级前运行配置校验失败'; return 1
    fi
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error '晋级前 active PID、入口或 loopback 监听身份无效'; return 1
    fi
    directory="$(release_directory "${release_id}")" || return 1
    promotion="$(promotion_file "${release_id}")" || return 1
    [[ ! -e "${promotion}" && ! -L "${promotion}" ]] || {
        error 'verified promotion 已存在且不可覆盖'; return 1;
    }
    if ! probe_release "${release_id}" readiness \
        "${CONFIG_FILE}" "${directory}" >/dev/null; then
        error '晋级前 Candidate readiness 校验失败'; return 1
    fi
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error 'readiness 后 active 运行身份漂移'; return 1
    fi
    api_base="$(jq -er '
      .live_verification.public_backend_origin
      + .live_verification.backend_base_path + "/"
    ' "${directory}/RELEASE-MANIFEST.json")" || {
        error '无法读取固定公开 Backend 路径'; return 1
    }
    request_epoch="$(date -u +%s)" || {
        error '无法生成晋级请求时间身份'; return 1
    }
    request_id="release-${release_id:0:16}-${request_epoch}-${RANDOM}"
    local temporary='' create_body create_response turn_body turn_response \
        final_response internal_response receipt_tmp='' receipt_sha verified_at event_reference \
        publication revision question conversation_id turn_id timeout_ms deadline state
    temporary="$(mktemp -d "${STATE_ROOT}/.promotion-${release_id}.XXXXXX")" || {
        error '无法创建晋级临时目录'; return 1
    }
    cleanup_promotion_raw_responses() {
        if [[ -n "${temporary:-}" \
            && -d "${temporary}" && ! -L "${temporary}" ]]; then
            find "${temporary}" -depth -delete || return 1
        fi
    }
    cleanup_promotion() {
        local cleanup_failed=false
        if [[ -n "${receipt_tmp:-}" \
            && ( -e "${receipt_tmp}" || -L "${receipt_tmp}" ) ]]; then
            unlink "${receipt_tmp}" 2>/dev/null || cleanup_failed=true
        fi
        cleanup_promotion_raw_responses || cleanup_failed=true
        [[ "${cleanup_failed}" == false ]]
    }
    trap cleanup_promotion EXIT
    create_body="${temporary}/create-request.json"
    create_response="${temporary}/create-response.json"
    turn_body="${temporary}/turn-request.json"
    turn_response="${temporary}/turn-response.json"
    final_response="${temporary}/backend-final.json"
    internal_response="${temporary}/sidecar-internal-record.json"
    event_reference="$(jq -er '.live_verification.event_reference' \
        "${directory}/RELEASE-MANIFEST.json")" || return 1
    publication="$(jq -er '.payload.data_identity.publication_id' \
        "${directory}/project/${CANDIDATE_RELATIVE}")" || return 1
    revision="$(jq -er '.payload.data_identity.revision' \
        "${directory}/project/${CANDIDATE_RELATIVE}")" || return 1
    question="$(jq -er '.live_verification.question' \
        "${directory}/RELEASE-MANIFEST.json")" || return 1
    if ! jq -n --arg reference "${event_reference}" \
        --arg publication "${publication}" --argjson revision "${revision}" \
        --arg key "${request_id}-create" \
        '{event_reference:$reference,publication_id:$publication,revision:$revision,idempotency_key:$key}' \
        > "${create_body}"; then
        error '无法构造固定会话请求'; return 1
    fi
    if ! backend_request POST "${api_base}conversations" \
        "${create_body}" > "${create_response}"; then
        error '公开 Backend 创建会话失败'; return 1
    fi
    conversation_id="$(jq -er '
      select(.deduplicated==false) |
      select((.conversation.turns | length)==0) |
      .conversation.conversation_id
    ' \
        "${create_response}")" || {
        error 'Backend 创建响应不是全新空会话'; return 1
    }
    [[ "${conversation_id}" =~ ^conversation_sha256_[a-f0-9]{64}$ ]] || {
        error 'Backend 返回的 conversation_id 不符合新 Interactive Agent 身份'
        return 1
    }
    if ! jq -n --arg question "${question}" \
        --arg key "${request_id}-turn" \
        '{question:$question,idempotency_key:$key}' > "${turn_body}"; then
        error '无法构造固定问题 Turn 请求'; return 1
    fi
    if ! backend_request POST "${api_base}conversations/${conversation_id}/turns" \
        "${turn_body}" > "${turn_response}"; then
        error '公开 Backend 创建 Turn 失败'; return 1
    fi
    turn_id="$(jq -er '
      select(.deduplicated==false) |
      select(.turn.turn_number==1) |
      .turn.turn_id
    ' "${turn_response}")" || {
        error 'Backend Turn 响应不是全新第一个 Turn'; return 1
    }
    [[ "${turn_id}" =~ ^turn_sha256_[a-f0-9]{64}$ ]] || {
        error 'Backend 返回的 turn_id 不符合新 Interactive Agent 身份'
        return 1
    }
    if ! jq -e --arg turn_id "${turn_id}" --arg question "${question}" '
      .turn.turn_id==$turn_id and .turn.question==$question
    ' "${turn_response}" >/dev/null; then
        error 'Backend Turn 响应未绑定本次固定问题'; return 1
    fi
    timeout_ms="$(read_config_value \
        COUNTRY_OUTAGE_INTERACTIVE_AGENT_TURN_TIMEOUT_MS)" || return 1
    current_epoch="$(date +%s)" || {
        error '无法读取晋级等待起始时间'; return 1
    }
    deadline=$(( current_epoch + (timeout_ms + 999) / 1000 ))
    while true; do
        if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
            error '等待回答期间 active 运行身份漂移'; return 1
        fi
        if ! backend_request GET "${api_base}conversations/${conversation_id}" \
            > "${final_response}"; then
            error '公开 Backend 获取最终会话失败'; return 1
        fi
        if ! jq -e --arg conversation_id "${conversation_id}" \
            --arg turn_id "${turn_id}" --arg question "${question}" '
          .conversation.conversation_id==$conversation_id and
          (.conversation.turns | length)==1 and
          ([.conversation.turns[]? | select(
            .turn_id==$turn_id and .turn_number==1 and .question==$question
          )] | length)==1
        ' "${final_response}" >/dev/null; then
            error '最终 GET 未精确绑定唯一新 conversation 与第一个 Turn'
            return 1
        fi
        state="$(jq -er --arg turn_id "${turn_id}" \
            '.conversation.turns[] | select(.turn_id==$turn_id) | .state' \
            "${final_response}")" || {
            error '最终会话缺少本次 Turn 状态'; return 1
        }
        [[ "${state}" == 'executing' ]] || break
        current_epoch="$(date +%s)" || {
            error '无法读取晋级等待当前时间'; return 1
        }
        (( current_epoch < deadline )) || {
            error 'Backend 固定问题等待超时，未写 verified promotion'; return 1;
        }
        if ! sleep 1; then
            error '晋级等待被异常中断'; return 1
        fi
    done
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error '回答完成后 active 运行身份漂移'; return 1
    fi
    if ! probe_release "${release_id}" internal-record \
        "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" \
        "${conversation_id}" "${turn_id}" > "${internal_response}"; then
        error '无法从固定 loopback 读取本次 Turn 的受信内部记录'
        return 1
    fi
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error '读取内部记录后 active 运行身份漂移'; return 1
    fi
    receipt_tmp="$(mktemp "${PROMOTION_ROOT}/.${release_id}.XXXXXX")" || {
        error '无法创建 promotion 临时回执'; return 1
    }
    verified_at="$(utc_now)" || {
        error '无法生成 promotion 验证时间'; return 1
    }
    if ! "${NODE}" "${SCRIPT_DIR}/verify-release.mjs" promotion \
        "${directory}" "${ACTIVE_STATE}" "${create_response}" \
        "${turn_response}" "${final_response}" "${internal_response}" \
        "${verified_at}" "${conversation_id}" "${turn_id}" \
        > "${receipt_tmp}"; then
        error '公开回答与内部记录未通过 Renderer + Guard + 风格 + 精确 Oracle 晋级门'
        return 1
    fi
    if ! chmod 0600 "${receipt_tmp}"; then
        error '无法收紧 promotion 临时回执权限'; return 1
    fi
    receipt_sha="$(sha256_file "${receipt_tmp}")" || return 1
    if ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        error '写入 promotion 前 active 运行身份漂移'; return 1
    fi
    if ! probe_release "${release_id}" status \
        "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" "${receipt_tmp}" \
        >/dev/null; then
        error '晋级回执与当前进程/readiness 组合状态不一致'
        return 1
    fi
    # 只有临时原始响应已清理，才允许把自包含的受信回执原子发布。
    # 这样 cleanup 失败不会留下可被 status 解释为 verified 的 promotion。
    if ! cleanup_promotion_raw_responses; then
        error '临时原始响应清理失败，未写 verified promotion'
        return 1
    fi
    temporary=''
    if ! mv -n "${receipt_tmp}" "${promotion}"; then
        error 'promotion 原子写入命令失败'
        return 1
    fi
    if [[ -e "${receipt_tmp}" || -L "${receipt_tmp}" \
        || ! -f "${promotion}" || -L "${promotion}" \
        || "$(sha256_file "${promotion}")" != "${receipt_sha}" ]]; then
        unlink "${receipt_tmp}" 2>/dev/null || true
        error 'promotion 已存在或原子写入失败，拒绝覆盖'
        return 1
    fi
    owner_mode "${promotion}" 600 || {
        if ! unlink "${promotion}"; then
            error 'promotion 权限无效且无法删除'
            return 1
        fi
        error 'promotion 最终所有者或权限无效；已删除'
        return 1
    }
    if ! assert_active_runtime_identity "${release_id}" >/dev/null \
        || ! probe_release "${release_id}" status \
            "${CONFIG_FILE}" "${directory}" "${ACTIVE_STATE}" "${promotion}" \
            >/dev/null \
        || ! assert_active_runtime_identity "${release_id}" >/dev/null; then
        if ! unlink "${promotion}"; then
            error '最终组合校验失败且无法删除 promotion'
            return 1
        fi
        error '最终 promotion 路径组合校验失败；已删除回执以允许安全重试'
        return 1
    fi
    receipt_tmp=''
    trap - EXIT
    info "Interactive Agent 已通过生产 E2E 验证：${release_id}"
}

main() {
    require_root
    require_commands
    ensure_runtime_directories
    exec 9>"${LOCK_FILE}"
    flock -n 9 || { error '另一个 Interactive Agent 生命周期操作正在运行'; return 1; }
    local action="${1:-}"; shift || true
    case "${action}" in
        prepare) prepare_release "$@" ;;
        start) start_runtime "$@" ;;
        stop) deactivate_runtime "$@" ;;
        status|probe) status_runtime "$@" ;;
        rollback) rollback_runtime "$@" ;;
        promote) promote_runtime "$@" ;;
        verify-release) (( $# == 1 )) || { error '用法：verify-release <release-id>'; return 2; }; verify_release "$1" ;;
        _test_validate_config)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            validate_config
            ;;
        _test_launch_environment)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 1 )) || return 2
            local test_release_directory test_key test_value
            test_release_directory="$(release_directory "$1")" || return 1
            validate_config
            validate_release_launch_paths "$1" "${test_release_directory}"
            for test_key in COUNTRY_OUTAGE_FIRST_SLICE_PROJECT_ROOT \
                COUNTRY_OUTAGE_FIRST_SLICE_CANDIDATE_MANIFEST; do
                test_value="$(read_config_value "${test_key}")" || return 1
                bind_launch_config_line \
                    "${test_key}=${test_value}" "${test_release_directory}"
            done
            ;;
        _test_archive_promotion)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 1 )) || return 2
            archive_promotion_if_present "$1"
            ;;
        _test_verify_source_archive)
            test_verify_trusted_source_archive "$@"
            ;;
        _test_verify_candidate_git_chain)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 2 )) || return 2
            [[ "$2" == "${TEST_ROOT}/"* ]] || {
                error 'Candidate 父链测试文件越界'; return 1;
            }
            verify_candidate_git_parent_chain "$1" "$2"
            ;;
        _test_listener_identity)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 2 )) || return 2
            [[ "$2" == "${TEST_ROOT}/"* && -f "$2" && ! -L "$2" ]] || {
                error '监听身份测试文件越界'; return 1;
            }
            listener_output_matches_runtime "$1" "$(<"$2")"
            ;;
        _test_verify_release_condition)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 1 )) || return 2
            # 模拟调用方把安全函数放进 if 的 Bash errexit 抑制上下文。
            if verify_release "$1"; then
                return 0
            fi
            return 1
            ;;
        _test_write_active_condition)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 2 )) || return 2
            if write_active "$1" "$2"; then
                return 0
            fi
            return 1
            ;;
        _test_restore_previous_condition)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 1 )) || return 2
            if restore_previous_verified "$1"; then
                return 0
            fi
            return 1
            ;;
        _test_clear_state_condition)
            [[ "${TEST_MODE}" == true ]] || {
                error '测试入口只能在显式临时测试根使用'; return 1;
            }
            (( $# == 0 )) || return 2
            if clear_active_current_state; then
                return 0
            fi
            return 1
            ;;
        *) error '用法：manage.sh {prepare|start|stop|status|rollback|promote|verify-release} ...'; return 2 ;;
    esac
}

main "$@"
