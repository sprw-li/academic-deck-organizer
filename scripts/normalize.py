#!/usr/bin/env python3
"""normalize.py — 把 python-pptx 的 zip 产物重写成逐字节稳定的归档。

CONTRACT §4 冻结签名::

    def normalize_pptx(path: Path) -> str: ...   # 固定 zip 时间戳/顺序，返回 sha256

做法（全部确定性、与机器/时区无关）：

* 条目名按字节序排序（zip 条目顺序不影响 OOXML 语义）；
* 每个条目的 timestamp 固定为 ``1980-01-01 00:00:00``（DOS epoch）；
* ``create_system = 0``、``external_attr = 0o600 << 16``、``extra = b""``、
  ``comment = b""``、``internal_attr = 0`` —— 清掉平台相关字段；
* 压缩方式与压缩级别固定（目录条目 STORED，其余 DEFLATED level 9）。

本模块只做文件重写，不解释 pptx 内容。除 ``normalize_pptx`` 外还提供
``sha256_file`` / ``sha256_bytes`` / ``write_text`` 给 build.py 复用（避免每模块各写一份）。

当库 import 无副作用；入口用 ``cli(argv)``。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path

# ---- 固定量（改动即破坏确定性，不要"顺手优化"） -------------------------

FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
COMPRESS_LEVEL = 9
CREATE_SYSTEM = 0  # 0 = MS-DOS
FIXED_EXTERNAL_ATTR = 0o600 << 16
MAX_TOTAL_BYTES = 512 * 1024 * 1024  # 防呆：>512MB 的归档不整体读入内存


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: str | os.PathLike, text: str) -> dict:
    """确定性文本写入：UTF-8（无 BOM）+ LF 换行。返回 {path,bytes,sha256}。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (text or "").encode("utf-8")
    with open(p, "wb") as fh:
        fh.write(data)
    return {"path": str(p), "bytes": len(data), "sha256": sha256_bytes(data)}


def stable_zipinfo(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
    zi.create_system = CREATE_SYSTEM
    zi.external_attr = FIXED_EXTERNAL_ATTR
    zi.internal_attr = 0
    zi.extra = b""
    zi.comment = b""
    zi.compress_type = zipfile.ZIP_STORED if name.endswith("/") else zipfile.ZIP_DEFLATED
    return zi


def normalize_pptx(path: str | os.PathLike) -> str:
    """原地重写 pptx 的 zip 结构使其逐字节稳定，返回重写后文件的 sha256。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))

    with zipfile.ZipFile(p, "r") as zin:
        infos = zin.infolist()
        total = sum(max(0, int(i.file_size)) for i in infos)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("pptx too large to normalize in memory: %d bytes" % total)
        entries = [(i.filename, zin.read(i.filename)) for i in infos]

    tmp = p.with_name(p.name + ".normtmp")
    try:
        with zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL, allowZip64=True
        ) as zout:
            for name, data in sorted(entries, key=lambda t: t[0]):
                zi = stable_zipinfo(name)
                zout.writestr(zi, data, compress_type=zi.compress_type, compresslevel=COMPRESS_LEVEL)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return sha256_file(p)


def normalize_report(path: str | os.PathLike) -> dict:
    p = Path(path)
    before = sha256_file(p)
    after = normalize_pptx(p)
    return {
        "path": str(p),
        "bytes": p.stat().st_size,
        "sha256_before": before,
        "sha256": after,
        "rewritten": before != after,
    }


# ---- CLI ----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="normalize.py", description="normalize pptx zip for byte-stable determinism")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("normalize", help="rewrite a pptx in place and print its sha256")
    n.add_argument("--pptx", required=True, help="path to .pptx")
    n.add_argument("--json", action="store_true", help="print a JSON object instead of a bare sha256")
    return ap


def cli(argv) -> int:
    args = _build_parser().parse_args(list(argv or []))
    if not args.cmd:
        _build_parser().print_help()
        return 2
    try:
        report = normalize_report(args.pptx)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        sys.stderr.write("normalize: IO/zip error: %s\n" % exc)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(report["sha256"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
