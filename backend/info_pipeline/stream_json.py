"""不把整个文件读入内存的 JSON 顶层对象解析器。"""

from __future__ import annotations

import json
from typing import Any, Iterator, TextIO, Tuple


class JsonStreamError(ValueError):
    """流式 JSON 的结构或编码不合法。"""


class _CharacterReader:
    def __init__(self, stream: TextIO, chunk_size: int) -> None:
        if chunk_size < 8:
            raise ValueError("chunk_size 不能小于 8")
        self._stream = stream
        self._chunk_size = chunk_size
        self._buffer = ""
        self._offset = 0
        self._eof = False

    def _fill(self) -> None:
        if self._offset < len(self._buffer) or self._eof:
            return
        self._buffer = self._stream.read(self._chunk_size)
        self._offset = 0
        if self._buffer == "":
            self._eof = True

    def peek(self) -> str:
        self._fill()
        if self._eof:
            return ""
        return self._buffer[self._offset]

    def get(self) -> str:
        value = self.peek()
        if value:
            self._offset += 1
        return value

    def skip_whitespace(self) -> None:
        while self.peek() and self.peek().isspace():
            self.get()


def _decode_json(raw: str, label: str) -> Any:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise JsonStreamError(f"{label} 含重复 JSON 键：{key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=no_duplicates)
    except JsonStreamError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JsonStreamError(f"{label} 不是合法 JSON：{exc}") from exc


def _read_string_raw(reader: _CharacterReader, label: str) -> str:
    if reader.get() != '"':
        raise JsonStreamError(f"{label} 应以双引号开始")
    parts = ['"']
    escaped = False
    while True:
        char = reader.get()
        if not char:
            raise JsonStreamError(f"{label} 在字符串结束前到达 EOF")
        parts.append(char)
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return "".join(parts)


def _read_value_raw(reader: _CharacterReader, label: str) -> str:
    first = reader.peek()
    if not first:
        raise JsonStreamError(f"{label} 缺少值")
    if first == '"':
        return _read_string_raw(reader, label)
    if first in "[{":
        parts = []
        stack = []
        in_string = False
        escaped = False
        while True:
            char = reader.get()
            if not char:
                raise JsonStreamError(f"{label} 在容器结束前到达 EOF")
            parts.append(char)
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack:
                    raise JsonStreamError(f"{label} 容器闭合符无对应起始符")
                opening = stack.pop()
                if (opening, char) not in {("[", "]"), ("{", "}")}:
                    raise JsonStreamError(f"{label} 容器闭合符不匹配")
                if not stack:
                    return "".join(parts)
    parts = []
    while True:
        char = reader.peek()
        if not char or char in ",}":
            raw = "".join(parts).strip()
            if not raw:
                raise JsonStreamError(f"{label} 缺少标量值")
            return raw
        parts.append(reader.get())


def iter_top_level_object(
    stream: TextIO,
    *,
    chunk_size: int = 64 * 1024,
) -> Iterator[Tuple[int, str, Any]]:
    """逐项解析一个 JSON 顶层对象，并拒绝任意层级的重复键。"""

    reader = _CharacterReader(stream, chunk_size)
    reader.skip_whitespace()
    if reader.get() != "{":
        raise JsonStreamError("JSON 顶层必须是对象")
    reader.skip_whitespace()
    if reader.peek() == "}":
        reader.get()
        reader.skip_whitespace()
        if reader.peek():
            raise JsonStreamError("JSON 顶层对象后存在多余内容")
        return

    seen = set()
    ordinal = 0
    while True:
        reader.skip_whitespace()
        key_raw = _read_string_raw(reader, "JSON 顶层键")
        key = _decode_json(key_raw, "JSON 顶层键")
        if not isinstance(key, str):
            raise JsonStreamError("JSON 顶层键必须是字符串")
        if key in seen:
            raise JsonStreamError(f"JSON 顶层含重复键：{key!r}")
        seen.add(key)

        reader.skip_whitespace()
        if reader.get() != ":":
            raise JsonStreamError(f"JSON 顶层键 {key!r} 后缺少冒号")
        reader.skip_whitespace()
        value_raw = _read_value_raw(reader, f"JSON 顶层键 {key!r} 的值")
        value = _decode_json(value_raw, f"JSON 顶层键 {key!r} 的值")
        ordinal += 1
        yield ordinal, key, value

        reader.skip_whitespace()
        separator = reader.get()
        if separator == "}":
            reader.skip_whitespace()
            if reader.peek():
                raise JsonStreamError("JSON 顶层对象后存在多余内容")
            return
        if separator != ",":
            raise JsonStreamError(f"JSON 顶层键 {key!r} 后缺少逗号或对象结束符")
