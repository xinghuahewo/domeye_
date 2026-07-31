#!/usr/bin/env python3
"""用机器任务合同约束 Codex 的 Worktree、基线和改动边界。"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Sequence


TASK_SCHEMA = "domeye_codex_task_contract_v1"
POLICY_SCHEMA = "domeye_codex_version_policy_v1"
STATE_SCHEMA = "domeye_codex_task_guard_state_v1"
DEFAULT_CONTRACT_PATH = ".codex/TASK.json"
DEFAULT_POLICY_PATH = "config/codex-version-boundaries.json"
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_IMMUTABLE_PATHS = ("backend/core/**", "backend/core.sha256")


class GuardError(RuntimeError):
    """合同无效、环境不匹配或边界检查失败。"""


def git(
    root: Path,
    *arguments: str,
    text: bool = True,
    check: bool = True,
) -> str | bytes:
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )
    if check and completed.returncode != 0:
        stderr = (
            completed.stderr.strip()
            if isinstance(completed.stderr, str)
            else completed.stderr.decode("utf-8", errors="replace").strip()
        )
        raise GuardError(
            f"Git 命令失败：git {' '.join(arguments)}"
            + (f"\n{stderr}" if stderr else "")
        )
    return completed.stdout


def discover_root(start: Path | None = None) -> Path:
    cwd = (start or Path.cwd()).resolve()
    output = git(cwd, "rev-parse", "--show-toplevel")
    assert isinstance(output, str)
    return Path(output.strip()).resolve()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GuardError(f"文件不存在：{path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise GuardError(f"无法读取 JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise GuardError(f"JSON 顶层必须是对象：{path}")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GuardError(f"`{field}` 必须是非空字符串。")
    return value.strip()


def validate_repo_pattern(value: Any, field: str) -> str:
    pattern = require_string(value, field)
    candidate = pattern.removesuffix("/**")
    path = PurePosixPath(candidate)
    if (
        pattern.startswith("/")
        or "\\" in pattern
        or candidate in ("", ".")
        or ".." in path.parts
    ):
        raise GuardError(f"`{field}` 不是安全的仓库相对路径模式：{pattern}")
    return pattern


def require_pattern_list(value: Any, field: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "非空数组" if nonempty else "数组"
        raise GuardError(f"`{field}` 必须是{qualifier}。")
    return [
        validate_repo_pattern(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    ]


def validate_relative_path(value: Any, field: str) -> str:
    path = validate_repo_pattern(value, field)
    if any(marker in path for marker in ("*", "?", "[")):
        raise GuardError(f"`{field}` 必须是确定路径，不能含通配符：{path}")
    return path


def path_matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def parse_name_status(output: bytes) -> set[str]:
    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    paths: set[str] = set()
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii", errors="strict")
        index += 1
        path_count = 2 if status.startswith(("R", "C")) else 1
        if not status or index + path_count > len(tokens):
            raise GuardError("Git name-status 输出不完整，无法安全判断改动范围。")
        for _ in range(path_count):
            path = tokens[index].decode("utf-8", errors="strict")
            validate_relative_path(path, "Git 改动路径")
            paths.add(path)
            index += 1
    return paths


def diff_paths(root: Path, *arguments: str) -> set[str]:
    output = git(
        root,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        *arguments,
        text=False,
    )
    assert isinstance(output, bytes)
    return parse_name_status(output)


def untracked_paths(root: Path) -> set[str]:
    output = git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        text=False,
    )
    assert isinstance(output, bytes)
    paths = set()
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="strict")
        paths.add(validate_relative_path(path, "未跟踪路径"))
    return paths


def worktree_changes(root: Path) -> set[str]:
    return (
        diff_paths(root)
        | diff_paths(root, "--cached")
        | untracked_paths(root)
    )


def all_task_changes(root: Path, base_commit: str) -> set[str]:
    return (
        diff_paths(root, f"{base_commit}...HEAD")
        | worktree_changes(root)
    )


def validate_content_rules(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise GuardError(f"`{field}` 必须是数组。")
    rules: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        prefix = f"{field}[{index}]"
        if not isinstance(item, dict):
            raise GuardError(f"`{prefix}` 必须是对象。")
        name = require_string(item.get("name"), f"{prefix}.name")
        include_paths = require_pattern_list(
            item.get("includePaths"),
            f"{prefix}.includePaths",
            nonempty=True,
        )
        expression = require_string(item.get("regex"), f"{prefix}.regex")
        message = require_string(item.get("message"), f"{prefix}.message")
        try:
            compiled = re.compile(expression, re.MULTILINE)
        except re.error as error:
            raise GuardError(f"`{prefix}.regex` 无效：{error}") from error
        rules.append(
            {
                "name": name,
                "includePaths": include_paths,
                "regex": compiled,
                "message": message,
            }
        )
    return rules


def validate_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise GuardError("`requiredChecks` 必须是非空数组。")
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        prefix = f"requiredChecks[{index}]"
        if not isinstance(item, dict):
            raise GuardError(f"`{prefix}` 必须是对象。")
        name = require_string(item.get("name"), f"{prefix}.name")
        command = item.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise GuardError(f"`{prefix}.command` 必须是非空字符串数组。")
        checks.append({"name": name, "command": command})
    return checks


def load_contract(root: Path, relative_path: str) -> dict[str, Any]:
    contract_path = validate_relative_path(relative_path, "contract")
    contract = read_json(root / contract_path)
    if contract.get("schemaVersion") != TASK_SCHEMA:
        raise GuardError(
            f"任务合同 schemaVersion 必须是 `{TASK_SCHEMA}`。"
        )
    task_id = require_string(contract.get("taskId"), "taskId")
    target_version = require_string(contract.get("targetVersion"), "targetVersion")
    target_branch = require_string(contract.get("targetBranch"), "targetBranch")
    worktree_root = Path(
        require_string(contract.get("worktreeRoot"), "worktreeRoot")
    )
    if not worktree_root.is_absolute():
        raise GuardError("`worktreeRoot` 必须是绝对路径。")
    base_commit = require_string(contract.get("baseCommit"), "baseCommit").lower()
    if not FULL_SHA_PATTERN.fullmatch(base_commit):
        raise GuardError("`baseCommit` 必须是 40 位小写 Git 提交 SHA。")
    allowed_paths = require_pattern_list(
        contract.get("allowedPaths"), "allowedPaths", nonempty=True
    )
    forbidden_paths = require_pattern_list(
        contract.get("forbiddenPaths", []),
        "forbiddenPaths",
        nonempty=False,
    )
    references = contract.get("authoritativeReferences")
    if not isinstance(references, list) or not references:
        raise GuardError("`authoritativeReferences` 必须是非空数组。")
    authoritative_references = [
        validate_relative_path(item, f"authoritativeReferences[{index}]")
        for index, item in enumerate(references)
    ]
    non_goals = contract.get("explicitNonGoals")
    if (
        not isinstance(non_goals, list)
        or not non_goals
        or any(not isinstance(item, str) or not item.strip() for item in non_goals)
    ):
        raise GuardError("`explicitNonGoals` 必须是非空字符串数组。")
    return {
        "contractPath": contract_path,
        "taskId": task_id,
        "targetVersion": target_version,
        "worktreeRoot": worktree_root.resolve(),
        "targetBranch": target_branch,
        "baseCommit": base_commit,
        "allowedPaths": allowed_paths,
        "forbiddenPaths": forbidden_paths,
        "authoritativeReferences": authoritative_references,
        "explicitNonGoals": [item.strip() for item in non_goals],
        "forbiddenContentRules": validate_content_rules(
            contract.get("forbiddenContentRules", []),
            "forbiddenContentRules",
        ),
        "requiredChecks": validate_checks(contract.get("requiredChecks")),
    }


def load_policy(root: Path, relative_path: str) -> dict[str, Any]:
    policy_path = validate_relative_path(relative_path, "policy")
    policy = read_json(root / policy_path)
    if policy.get("schemaVersion") != POLICY_SCHEMA:
        raise GuardError(
            f"仓库策略 schemaVersion 必须是 `{POLICY_SCHEMA}`。"
        )
    immutable_paths = require_pattern_list(
        policy.get("immutablePaths"),
        "immutablePaths",
        nonempty=True,
    )
    missing_required = [
        pattern
        for pattern in REQUIRED_IMMUTABLE_PATHS
        if pattern not in immutable_paths
    ]
    if missing_required:
        raise GuardError(
            "仓库策略不得移除内建冻结路径："
            + "、".join(missing_required)
        )
    return {
        "immutablePaths": immutable_paths,
        "forbiddenContentRules": validate_content_rules(
            policy.get("forbiddenContentRules", []),
            "forbiddenContentRules",
        ),
    }


def ensure_commit_exists(root: Path, commit: str) -> None:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise GuardError(f"基线提交不存在：{commit}")


def ensure_base_is_ancestor(root: Path, base_commit: str) -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_commit, "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise GuardError(f"当前 HEAD 不是基线提交 {base_commit} 的后代。")


def ensure_references_exist(root: Path, references: Iterable[str]) -> None:
    missing = [path for path in references if not (root / path).is_file()]
    if missing:
        raise GuardError("权威参考不存在：\n- " + "\n- ".join(missing))


def ensure_contract_is_local(root: Path, contract_path: str) -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", contract_path],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if tracked.returncode == 0:
        raise GuardError(
            f"本地任务合同不得被 Git 跟踪：{contract_path}"
        )
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", contract_path],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ignored.returncode != 0:
        raise GuardError(
            f"本地任务合同必须被 .gitignore 忽略：{contract_path}"
        )


def ensure_contract_identity(root: Path, contract: dict[str, Any]) -> str:
    actual_root = root.resolve()
    if actual_root != contract["worktreeRoot"]:
        raise GuardError(
            "Worktree 不匹配："
            f"合同为 {contract['worktreeRoot']}，当前为 {actual_root}"
        )
    branch_output = git(root, "branch", "--show-current")
    assert isinstance(branch_output, str)
    actual_branch = branch_output.strip()
    if not actual_branch:
        raise GuardError("当前处于 detached HEAD，不能开始实现任务。")
    if actual_branch != contract["targetBranch"]:
        raise GuardError(
            f"分支不匹配：合同为 {contract['targetBranch']}，"
            f"当前为 {actual_branch}"
        )
    ensure_commit_exists(root, contract["baseCommit"])
    ensure_base_is_ancestor(root, contract["baseCommit"])
    ensure_references_exist(root, contract["authoritativeReferences"])
    ensure_contract_is_local(root, contract["contractPath"])
    head_output = git(root, "rev-parse", "HEAD")
    assert isinstance(head_output, str)
    return head_output.strip()


def contract_digest(root: Path, contract_path: str) -> str:
    try:
        content = (root / contract_path).read_bytes()
    except OSError as error:
        raise GuardError(f"无法读取任务合同以计算哈希：{error}") from error
    return hashlib.sha256(content).hexdigest()


def guard_state_path(root: Path) -> Path:
    output = git(root, "rev-parse", "--git-path", "codex-task-guard-state.json")
    assert isinstance(output, str)
    path = Path(output.strip())
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def expected_guard_state(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": STATE_SCHEMA,
        "taskId": contract["taskId"],
        "worktreeRoot": str(contract["worktreeRoot"]),
        "targetBranch": contract["targetBranch"],
        "baseCommit": contract["baseCommit"],
        "contractPath": contract["contractPath"],
        "contractSha256": contract_digest(root, contract["contractPath"]),
    }


def seal_contract(root: Path, contract: dict[str, Any]) -> None:
    state_path = guard_state_path(root)
    expected = expected_guard_state(root, contract)
    if state_path.exists():
        existing = read_json(state_path)
        if existing != expected:
            raise GuardError(
                "当前 Worktree 已封存另一份任务合同；"
                "不得通过修改合同扩大任务范围。请创建新的任务 Worktree。"
            )
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="codex-task-guard-",
        suffix=".tmp",
        dir=state_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(expected, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, state_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def verify_contract_seal(root: Path, contract: dict[str, Any]) -> None:
    state_path = guard_state_path(root)
    if not state_path.is_file():
        raise GuardError(
            "缺少 preflight 合同封存状态；不得在未通过 preflight 的任务上"
            "执行完成验收。"
        )
    state = read_json(state_path)
    if state != expected_guard_state(root, contract):
        raise GuardError(
            "任务合同或 Worktree 身份已在 preflight 后变化；"
            "不得通过事后扩大合同掩盖越界。"
        )


def run_preflight(root: Path, contract: dict[str, Any]) -> str:
    head = ensure_contract_identity(root, contract)
    dirty = sorted(
        worktree_changes(root) - {contract["contractPath"]}
    )
    if dirty:
        raise GuardError(
            "任务开始前工作树不是干净状态：\n- " + "\n- ".join(dirty)
        )
    seal_contract(root, contract)
    return head


def rule_violations(
    root: Path,
    changed_paths: Iterable[str],
    rules: Iterable[dict[str, Any]],
) -> list[str]:
    violations: list[str] = []
    for path in sorted(changed_paths):
        target = root / path
        if not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rule in rules:
            if not any(path_matches(pattern, path) for pattern in rule["includePaths"]):
                continue
            match = rule["regex"].search(content)
            if match is not None:
                line = content.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{rule['name']}：{path}:{line}：{rule['message']}"
                )
    return violations


def check_policy(
    root: Path,
    changed_paths: set[str],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    immutable_hits = sorted(
        path
        for path in changed_paths
        if any(path_matches(pattern, path) for pattern in policy["immutablePaths"])
    )
    if immutable_hits:
        errors.append(
            "修改了仓库级冻结路径：\n  - " + "\n  - ".join(immutable_hits)
        )
    content_hits = rule_violations(
        root, changed_paths, policy["forbiddenContentRules"]
    )
    if content_hits:
        errors.append("命中仓库级禁止依赖：\n  - " + "\n  - ".join(content_hits))
    return errors


def check_task_boundaries(
    root: Path,
    changed_paths: set[str],
    contract: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    errors = check_policy(root, changed_paths, policy)
    forbidden_hits = sorted(
        path
        for path in changed_paths
        if any(
            path_matches(pattern, path)
            for pattern in contract["forbiddenPaths"]
        )
    )
    if forbidden_hits:
        errors.append(
            "修改了任务合同禁止路径：\n  - "
            + "\n  - ".join(forbidden_hits)
        )
    outside_allowed = sorted(
        path
        for path in changed_paths
        if not any(
            path_matches(pattern, path)
            for pattern in contract["allowedPaths"]
        )
    )
    if outside_allowed:
        errors.append(
            "存在任务合同未授权路径：\n  - "
            + "\n  - ".join(outside_allowed)
        )
    content_hits = rule_violations(
        root, changed_paths, contract["forbiddenContentRules"]
    )
    if content_hits:
        errors.append("命中任务级禁止依赖：\n  - " + "\n  - ".join(content_hits))
    return errors


def run_required_checks(root: Path, checks: Iterable[dict[str, Any]]) -> None:
    for item in checks:
        command = item["command"]
        print(
            f"执行检查：{item['name']}：{' '.join(command)}",
            flush=True,
        )
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode != 0:
            raise GuardError(
                f"任务合同检查失败：{item['name']}，退出码 "
                f"{completed.returncode}"
            )


def resolve_base_ref(root: Path, base_ref: str) -> str:
    ref = require_string(base_ref, "base-ref")
    output = git(root, "merge-base", ref, "HEAD")
    assert isinstance(output, str)
    values = [line for line in output.splitlines() if line.strip()]
    if len(values) != 1 or not FULL_SHA_PATTERN.fullmatch(values[0]):
        raise GuardError(f"无法为 {ref} 确定唯一 merge base。")
    return values[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Domeye Codex 版本与任务边界检查器。"
    )
    parser.add_argument(
        "--contract",
        default=os.environ.get("DOMEYE_CODEX_TASK", DEFAULT_CONTRACT_PATH),
        help=f"任务合同路径，默认 {DEFAULT_CONTRACT_PATH}",
    )
    parser.add_argument(
        "--policy",
        default=os.environ.get("DOMEYE_CODEX_POLICY", DEFAULT_POLICY_PATH),
        help=f"仓库策略路径，默认 {DEFAULT_POLICY_PATH}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight", help="开始任务前检查环境和干净状态。")
    postflight = subparsers.add_parser(
        "postflight", help="结束任务前检查完整改动边界。"
    )
    postflight.add_argument(
        "--run-checks",
        action="store_true",
        help="同时执行任务合同声明的定向检查。",
    )
    policy = subparsers.add_parser(
        "policy", help="不依赖本地任务合同，执行仓库级 CI 边界检查。"
    )
    policy.add_argument("--base-ref", required=True, help="PR 或任务基线引用。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = discover_root()
        policy = load_policy(root, args.policy)
        if args.command == "preflight":
            contract = load_contract(root, args.contract)
            head = run_preflight(root, contract)
            print(
                "Codex preflight 通过："
                f"task={contract['taskId']}，"
                f"version={contract['targetVersion']}，"
                f"worktree={root}，"
                f"branch={contract['targetBranch']}，"
                f"base={contract['baseCommit']}，"
                f"head={head}"
            )
            return 0
        if args.command == "postflight":
            contract = load_contract(root, args.contract)
            head = ensure_contract_identity(root, contract)
            verify_contract_seal(root, contract)
            changed_paths = all_task_changes(root, contract["baseCommit"])
            errors = check_task_boundaries(root, changed_paths, contract, policy)
            if errors:
                raise GuardError("\n".join(errors))
            if args.run_checks:
                run_required_checks(root, contract["requiredChecks"])
                changed_paths = all_task_changes(root, contract["baseCommit"])
                errors = check_task_boundaries(
                    root, changed_paths, contract, policy
                )
                if errors:
                    raise GuardError(
                        "定向检查执行后出现新的边界偏离：\n"
                        + "\n".join(errors)
                    )
            print(
                "Codex postflight 通过："
                f"task={contract['taskId']}，"
                f"base={contract['baseCommit']}，"
                f"head={head}，"
                f"changed={len(changed_paths)}"
            )
            for path in sorted(changed_paths):
                print(f"- {path}")
            return 0
        base_commit = resolve_base_ref(root, args.base_ref)
        changed_paths = all_task_changes(root, base_commit)
        errors = check_policy(root, changed_paths, policy)
        if errors:
            raise GuardError("\n".join(errors))
        print(
            f"仓库级版本边界通过：base={base_commit}，"
            f"changed={len(changed_paths)}"
        )
        return 0
    except GuardError as error:
        print(f"版本边界检查失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
