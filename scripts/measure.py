#!/usr/bin/env python3
"""measure.py — 讲稿时长与文本容纳量的确定性估算。

CONTRACT §4 冻结签名::

    def speaking_seconds(text, *, lang="zh", wpm_en=140.0, cpm_zh=240.0) -> float
    def text_fits(text, box_w_emu, box_h_emu, font_pt, *, lang="zh") -> dict
        # -> {"fits": bool, "est_lines": int, "est_h_emu": int, "overflow_ratio": float}

口径（写在明处，避免被当成精度承诺）：

* ``speaking_seconds`` 中文按 CJK 字符数 / ``cpm_zh``；英文按词数 / ``wpm_en``；
  混排时两部分相加。**不计标点停顿与翻页停顿** —— 它是预算校验用的下界估计，
  实际讲述时长通常略长。
* ``text_fits`` 是粗估：CJK 记 1.0 em、非 CJK 记 0.5 em 宽；
  行高 1.2 em；``\n`` 为强制换行。用于**预警溢出**，不代替真实渲染检查。

纯函数，无副作用；入口 ``cli(argv)``。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import math
from pathlib import Path

EMU_PER_INCH = 914400.0
LINE_HEIGHT_FACTOR = 1.2
CJK_WIDTH_EM = 1.0
LATIN_WIDTH_EM = 0.5

# 常见 CJK / 全角区段（够用即可，不做 Unicode 全表）
_CJK_RANGES = (
    (0x2E80, 0x2EFF),
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0x3100, 0x312F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def text_profile(text: str) -> dict:
    """返回 {chars, cjk, latin_chars, latin_words, lines, blank_lines}。"""
    s = text or ""
    cjk = 0
    latin_chars = 0
    for ch in s:
        if is_cjk(ch):
            cjk += 1
        elif not ch.isspace():
            latin_chars += 1
    return {
        "chars": len(s),
        "cjk": cjk,
        "latin_chars": latin_chars,
        "latin_words": len(s.split()),
        "lines": s.count("\n") + 1,
        "blank_lines": sum(1 for ln in s.split("\n") if not ln.strip()),
    }


def speaking_seconds(text: str, *, lang: str = "zh", wpm_en: float = 140.0, cpm_zh: float = 240.0) -> float:
    """估算讲稿所需秒数（下界；不计停顿）。"""
    prof = text_profile(text)
    lang = (lang or "zh").lower()
    if lang.startswith("en"):
        if not wpm_en or wpm_en <= 0:
            return 0.0
        return round(prof["latin_words"] / float(wpm_en) * 60.0, 3)
    sec = 0.0
    if cpm_zh and cpm_zh > 0:
        sec += prof["cjk"] / float(cpm_zh) * 60.0
    if wpm_en and wpm_en > 0:
        sec += prof["latin_words"] / float(wpm_en) * 60.0
    return round(sec, 3)


def _line_units(line: str) -> float:
    units = 0.0
    for ch in line:
        if ch.isspace():
            units += LATIN_WIDTH_EM * 0.5
        elif is_cjk(ch):
            units += CJK_WIDTH_EM
        else:
            units += LATIN_WIDTH_EM
    return units


def text_fits(text: str, box_w_emu: int, box_h_emu: int, font_pt: float, *, lang: str = "zh") -> dict:
    """粗估文本是否装得进给定文本框（EMU 尺寸 + 字号 pt）。"""
    try:
        box_w = float(box_w_emu or 0)
        box_h = float(box_h_emu or 0)
        fpt = float(font_pt or 0)
    except (TypeError, ValueError):
        return {"fits": False, "est_lines": 0, "est_h_emu": 0, "overflow_ratio": 0.0}
    if box_w <= 0 or box_h <= 0 or fpt <= 0:
        return {"fits": False, "est_lines": 0, "est_h_emu": 0, "overflow_ratio": 0.0}

    units_per_line = max(0.5, (box_w / EMU_PER_INCH * 72.0) / fpt)
    est_lines = 0
    for raw in (text or "").split("\n"):
        line = raw.rstrip()
        units = _line_units(line)
        est_lines += max(1, int(math.ceil(units / units_per_line))) if line.strip() else 1
    est_lines = max(1, est_lines)

    line_h = fpt * LINE_HEIGHT_FACTOR / 72.0 * EMU_PER_INCH
    est_h = int(round(est_lines * line_h))
    ratio = round(est_h / box_h, 4)
    return {
        "fits": est_h <= box_h,
        "est_lines": int(est_lines),
        "est_h_emu": int(est_h),
        "overflow_ratio": ratio,
    }


# ---- CLI ----------------------------------------------------------------


def _read_text(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).read_text(encoding="utf-8", errors="replace")
    return args.text or ""


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="measure.py", description="estimate speaking time and text fit")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("seconds", help="estimate speaking seconds for a text")
    s.add_argument("--text", default=None)
    s.add_argument("--file", default=None)
    s.add_argument("--lang", default="zh", choices=["zh", "en"])
    s.add_argument("--wpm-en", type=float, default=140.0)
    s.add_argument("--cpm-zh", type=float, default=240.0)
    s.add_argument("--json", action="store_true")

    f = sub.add_parser("fit", help="estimate whether a text fits a text box")
    f.add_argument("--text", default=None)
    f.add_argument("--file", default=None)
    f.add_argument("--w-emu", type=int, required=True)
    f.add_argument("--h-emu", type=int, required=True)
    f.add_argument("--font-pt", type=float, required=True)
    f.add_argument("--lang", default="zh", choices=["zh", "en"])
    f.add_argument("--json", action="store_true")
    return ap


def cli(argv) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv or []))
    if not args.cmd:
        parser.print_help()
        return 2
    text = _read_text(args)
    if args.cmd == "seconds":
        sec = speaking_seconds(text, lang=args.lang, wpm_en=args.wpm_en, cpm_zh=args.cpm_zh)
        if args.json:
            out = {"seconds": sec, "profile": text_profile(text), "lang": args.lang}
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write("%.3f\n" % sec)
        return 0
    res = text_fits(text, args.w_emu, args.h_emu, args.font_pt, lang=args.lang)
    if args.json:
        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write("fits=%s lines=%d est_h_emu=%d overflow_ratio=%.4f\n"
                         % (res["fits"], res["est_lines"], res["est_h_emu"], res["overflow_ratio"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
