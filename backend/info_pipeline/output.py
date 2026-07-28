"""受控证据文件的排他写入。"""

from __future__ import annotations

import os
from pathlib import Path


def write_text_exclusive(path: os.PathLike[str] | str, text: str) -> None:
    """创建新文件并拒绝软链接或覆盖既有证据。"""

    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise OSError(f"输出父目录不存在或不是目录：{parent}")
    if parent.is_symlink():
        raise OSError(f"输出父目录是软链接：{parent}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            target.unlink()
        except OSError:
            pass
        raise
