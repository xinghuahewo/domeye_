#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
对比两种读取 bgpdump 输出的方式：
1) bgpdump 输出重定向到临时文件 -> Python 再逐行读取临时文件
2) Python 通过管道直接逐行读取 bgpdump stdout

默认会从 /home/bgpdata/data/ripe/rrc00/2026.01 目录挑选约 10 个 updates 文件做基准测试。

注意：
- 该脚本只对“读取/解析”做对比，不涉及 BGPFeature 的 update_rib / DB 写入（否则需要 RIB+DB 环境）。
- workload=bgpfeature_like 会模拟 core/BGPFeature.py 里的关键字段解析与时间戳转换开销。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


DEFAULT_DIR = "/home/bgpdata/data/ripe/rrc00/2026.01"


@dataclass(frozen=True)
class WorkResult:
    lines_total: int
    lines_skipped: int
    lines_parsed: int


def _process_line_minimal(line: str) -> Tuple[bool, bool]:
    """
    返回 (skipped, parsed)：
    - skipped=True 表示该行被过滤/无效
    - parsed=True 表示解析成功（有效 update 行）
    """
    fields = line.rstrip("\n").split("|")
    if len(fields) < 6:
        return True, False
    try:
        flag = fields[2]
        prefix = fields[5]
    except Exception:
        return True, False
    if flag == "STATE" or prefix == "0.0.0.0/0" or prefix == "::/0":
        return True, False
    return False, True


def _process_line_bgpfeature_like(line: str) -> Tuple[bool, bool]:
    """
    尽量贴近 core/BGPFeature.py:update_checking 里每行做的事情（但不依赖 rib 状态）：
    - split('|') 取 timestamp/flag/vp/prefix
    - 过滤 STATE 与 default route
    - 对 timestamp 做 fromtimestamp + strftime（模拟 t 的构造开销）
    - A 行读取 as_path 字段（若不存在则为空）
    """
    fields = line.rstrip("\n").split("|")
    if len(fields) < 6:
        return True, False
    try:
        timestamp = int(fields[1])
        flag = fields[2]
        prefix = fields[5]
    except Exception:
        return True, False
    if flag == "STATE" or prefix == "0.0.0.0/0" or prefix == "::/0":
        return True, False
    if flag == "A":
        _ = fields[6] if len(fields) > 6 else ""
    _ = _dt.datetime.fromtimestamp(int(timestamp) + 28800, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return False, True


def _iter_bgpdump_stdout(file_path: str, bgpdump_bin: str) -> Iterable[str]:
    proc = subprocess.Popen(
        [bgpdump_bin, "-m", file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        for line in proc.stdout:
            yield line
    finally:
        # 读完 stdout 后，尽快 wait 并收集 stderr 便于报错定位
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"bgpdump failed (rc={proc.returncode}) for {file_path}: {stderr[:4000]}")


def _bgpdump_to_temp_file(file_path: str, bgpdump_bin: str, dump_dir: str) -> str:
    out_path = os.path.join(dump_dir, os.path.basename(file_path) + ".data")
    with open(out_path, "w", encoding="utf-8", errors="replace") as out:
        completed = subprocess.run(
            [bgpdump_bin, "-m", file_path],
            stdout=out,
            stderr=subprocess.PIPE,
            text=True,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"bgpdump failed (rc={completed.returncode}) for {file_path}: {completed.stderr[:4000]}"
        )
    return out_path


def _run_workload(lines: Iterable[str], workload: str) -> WorkResult:
    lines_total = 0
    lines_skipped = 0
    lines_parsed = 0

    if workload == "minimal":
        fn = _process_line_minimal
    elif workload == "bgpfeature_like":
        fn = _process_line_bgpfeature_like
    else:
        raise ValueError(f"unknown workload: {workload}")

    for line in lines:
        lines_total += 1
        skipped, parsed = fn(line)
        if skipped:
            lines_skipped += 1
        if parsed:
            lines_parsed += 1

    return WorkResult(lines_total=lines_total, lines_skipped=lines_skipped, lines_parsed=lines_parsed)


def _pick_updates_files(root_dir: str, n: int) -> List[str]:
    p = Path(root_dir)
    if not p.exists():
        raise FileNotFoundError(f"目录不存在: {root_dir}")
    if not p.is_dir():
        raise NotADirectoryError(f"不是目录: {root_dir}")

    candidates = []
    for child in p.iterdir():
        if not child.is_file():
            continue
        name = child.name
        if name.startswith("updates") or name.startswith("update"):
            candidates.append(child)
    candidates.sort()
    if not candidates:
        raise FileNotFoundError(f"目录下未找到 updates/update 文件: {root_dir}")

    if n <= 0:
        return [str(x) for x in candidates]
    if len(candidates) <= n:
        return [str(x) for x in candidates]

    # 均匀采样（比直接取前 N 个更不容易“偏小文件”）
    step = len(candidates) / float(n)
    picked = []
    for i in range(n):
        idx = int(i * step)
        picked.append(candidates[idx])
    # 去重（极端情况下 step<1 会重复）
    picked = list(dict.fromkeys(picked))
    return [str(x) for x in picked]


def _bench_one_file_filemode(file_path: str, bgpdump_bin: str, workload: str) -> Tuple[float, WorkResult]:
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bgpdump_bench_") as dump_dir:
        dumped = _bgpdump_to_temp_file(file_path=file_path, bgpdump_bin=bgpdump_bin, dump_dir=dump_dir)
        with open(dumped, "r", encoding="utf-8", errors="replace") as f:
            result = _run_workload(f, workload=workload)
    end = time.perf_counter()
    return end - start, result


def _bench_one_file_pipemode(file_path: str, bgpdump_bin: str, workload: str) -> Tuple[float, WorkResult]:
    start = time.perf_counter()
    lines = _iter_bgpdump_stdout(file_path=file_path, bgpdump_bin=bgpdump_bin)
    result = _run_workload(lines, workload=workload)
    end = time.perf_counter()
    return end - start, result


def _format_rate(lines: int, seconds: float) -> str:
    if seconds <= 0:
        return "inf"
    return f"{(lines / seconds):.1f} lines/s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=DEFAULT_DIR, help="updates 文件目录")
    parser.add_argument("--n", type=int, default=10, help="挑选文件数量（0 表示全部）")
    parser.add_argument("--iterations", type=int, default=1, help="重复次数（用于取均值/中位数）")
    parser.add_argument(
        "--workload",
        choices=["minimal", "bgpfeature_like"],
        default="bgpfeature_like",
        help="每行处理开销模型",
    )
    parser.add_argument("--bgpdump", default="bgpdump", help="bgpdump 可执行文件路径")
    args = parser.parse_args()

    bgpdump_bin = args.bgpdump
    if os.path.sep not in bgpdump_bin:
        resolved = shutil.which(bgpdump_bin)
        if not resolved:
            print(f"找不到 bgpdump: {bgpdump_bin}（请确认已安装，或用 --bgpdump 指定路径）")
            return 2
        bgpdump_bin = resolved
    elif not os.path.exists(bgpdump_bin):
        print(f"bgpdump 路径不存在: {bgpdump_bin}")
        return 2

    files = _pick_updates_files(args.dir, args.n)
    print(f"目录: {args.dir}")
    print(f"文件数: {len(files)}")
    print(f"workload: {args.workload}")
    print(f"bgpdump: {bgpdump_bin}")
    print("")
    for f in files:
        print(f"- {f}")
    print("")

    file_times: List[float] = []
    pipe_times: List[float] = []
    file_lines = 0
    pipe_lines = 0

    for it in range(args.iterations):
        print(f"== Iteration {it + 1}/{args.iterations} ==")
        for path in files:
            t1, r1 = _bench_one_file_filemode(path, bgpdump_bin=bgpdump_bin, workload=args.workload)
            t2, r2 = _bench_one_file_pipemode(path, bgpdump_bin=bgpdump_bin, workload=args.workload)

            if r1 != r2:
                print(f"[WARN] 结果不一致: {path}")
                print(f"  file: {r1}")
                print(f"  pipe: {r2}")

            file_times.append(t1)
            pipe_times.append(t2)
            file_lines += r1.lines_total
            pipe_lines += r2.lines_total

            print(
                f"{os.path.basename(path)} | file={t1:.3f}s ({_format_rate(r1.lines_total, t1)}) "
                f"| pipe={t2:.3f}s ({_format_rate(r2.lines_total, t2)})"
            )
        print("")

    def _summ(times: List[float]) -> str:
        if not times:
            return "n/a"
        if len(times) == 1:
            return f"{times[0]:.3f}s"
        return f"mean={statistics.mean(times):.3f}s, median={statistics.median(times):.3f}s"

    total_file = sum(file_times)
    total_pipe = sum(pipe_times)
    print("== Summary ==")
    print(f"file mode: total={total_file:.3f}s, {_summ(file_times)}, total_lines={file_lines}, rate={_format_rate(file_lines, total_file)}")
    print(f"pipe mode: total={total_pipe:.3f}s, {_summ(pipe_times)}, total_lines={pipe_lines}, rate={_format_rate(pipe_lines, total_pipe)}")
    if total_pipe > 0 and total_file > 0:
        speedup = total_file / total_pipe
        print(f"speedup(file/pipe): {speedup:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

