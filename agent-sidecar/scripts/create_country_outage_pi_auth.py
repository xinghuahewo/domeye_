#!/usr/bin/env python3
"""以终端静默输入方式创建国家中断 Agent 专用 Pi 认证文件。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from pathlib import Path


MAX_API_KEY_BYTES = 8_192


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "创建权限为 0600 的 DeepSeek 专用认证文件；"
            "密钥不会显示在终端或命令历史中。"
        )
    )
    parser.add_argument("path", help="认证文件绝对路径；目标文件必须不存在")
    return parser.parse_args()


def validate_key(value: str) -> str:
    if not value:
        raise ValueError("密钥不能为空")
    if value != value.strip():
        raise ValueError("密钥首尾不能包含空白")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("密钥不能包含控制字符")
    if len(value.encode("utf-8")) > MAX_API_KEY_BYTES:
        raise ValueError("密钥长度超过允许上限")
    return value


def create_auth_file(path: Path, api_key: str) -> None:
    if not path.is_absolute():
        raise ValueError("认证文件必须使用绝对路径")
    if path.exists() or path.is_symlink():
        raise FileExistsError("目标已存在；为避免覆盖，请更换路径或先人工确认旧文件")
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, parents=True)
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode):
        raise ValueError("认证文件父路径不是目录")
    if parent.st_uid != os.getuid():
        raise PermissionError("认证文件父目录不属于当前用户")

    payload = (
        json.dumps(
            {"deepseek": {"type": "api_key", "key": api_key}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

    descriptor: int | None = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("认证文件写入未完成")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except Exception:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)

    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
    ):
        path.unlink(missing_ok=True)
        raise PermissionError("创建后的认证文件权限或所有者校验失败")


def main() -> int:
    args = parse_args()
    target = Path(args.path)
    try:
        first = validate_key(getpass.getpass("请输入 DeepSeek API Key："))
        second = getpass.getpass("请再次输入以确认：")
        if first != second:
            raise ValueError("两次输入不一致")
        create_auth_file(target, first)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消，未创建认证文件。", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"创建失败：{error}", file=sys.stderr)
        return 1

    print(f"认证文件已创建：{target}")
    print("已校验：普通文件、当前用户所有、权限 0600；未输出密钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
