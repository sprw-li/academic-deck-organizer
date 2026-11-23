#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — CONTRACT §5 闸门 A–J（ARMI2611000101v1）。

设计约束（来自 CONTRACT §1.5 / §5）：
  * 每条闸门只输出三态 ``pass`` / ``fail`` / ``warn``：
      - ``pass``  = 真的把真实文件读进来比过了；
      - ``fail``  = 硬错（§5 表里列出的可执行 fail 条件）；
      - ``warn``  = **只用于无法验证**（渲染器不可用 / 本地拿不到材料图 / 契约键集合读不到 /
                    确定性缺第二份构建哈希）。warn 会经 ``status_suggestion`` 传导为
                    ``result.json.status = partial``（由 pt.py 落实，见本文件 ``apply_status_suggestion``）。
  * 每条 detail 必带实测数字（计数 / 比值 / sha256 片段 / 字节数），禁止形容词。
  * 闸门消费**真实文件**：deck.pptx 用 python-pptx 打开真读、deck.pdf 真读 ``%PDF-`` 与页数
    （pypdf + ``/Count`` 双路）、preview PNG 真读 pHYs dpi、render_manifest.json / evidence-map.json /
    result.json / spec.json / materials.json / template-index.json 逐个读入，并把每个被消费文件的
    sha256+bytes 写进 ``validation_report.json["consumed"]`` 作为"确实读过"的凭证。

退出码（``cli`` / ``check``）：0 全 pass（允许 warn）｜1 任一 fail｜3 产物缺失｜2 用法/IO。

契约偏离（明说，不静默）：
  D1.（已按 Lead 2026-11-23 澄清对齐）§5 表 A 只查**内容页** role ∈
      {hook,problem,gap,method,results,limitation,conclusion}；title/qa/backup 免检。
  D2. A 增加了 CONTRACT §1.3 的落地检查：**内容页** 的 claim/bullets 里出现的每个数字必须能在该页
      evidence 的 quote 里找到（否则计入 ``untraceable_numbers`` → fail）。这是"禁止伪造"的唯一
      可执行形式，但比 §5 表字面更严。
  D3. C 的配色检查带容差：``theme.source=template`` 时字体必须逐字等于模板 theme 字体；配色则要求
      primary/accent/text/muted 至少命中模板调色板 1 项（完全外来调色板才 fail）。纯字面"配色不完全
      一致即 fail"会把合法深色主题全部误杀。
  D4. G 只有拿到第二份构建哈希（``work/determinism.json`` 或 ``work/deck_build1.pptx`` +
      ``work/deck_build2.pptx``）才能真比；拿不到 → warn（无法验证），不伪 pass。
  D5. warn → ``status=partial`` 默认**不自动改写** result.json（避免 check 不幂等），改为在报告里输出
      ``status_suggestion``；``--apply-status`` 才落盘。pt.py 负责落实。

返回值保证：``run_gates`` 返回的 dict（含 ``gates``）是**纯 JSON 可序列化且无环**的——每个 gate 只有
str/int/bool/list/dict，绝无跨 gate 的对象自引用（gate J 只保留前置闸门的 {id,status} 摘要副本）。
``report["json_clean"]`` 记录一次 ``json.dumps(gates)`` 自检是否通过；失败时会把 gates 降级成纯字符串副本
并在该 gate 记 ``json_repaired``。

CLI::
    python validate.py check --out O --spec S --work W [--materials M] [--json]
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1
GATE_IDS = list("ABCDEFGHIJ")

# role 枚举（CONTRACT §3.4）
ROLES = ("title", "hook", "problem", "gap", "method", "results",
         "limitation", "conclusion", "qa", "backup")
# A：**只查内容页**（Lead 2026-11-23 澄清 CONTRACT §5）。
# title/qa/backup 不承载主张（title 是标题、qa/backup 的答案在 spec.qa 里）→ 免检。
CONTENT_ROLES = ("hook", "problem", "gap", "method", "results", "limitation", "conclusion")
# B：内容页（claim 非空且 ≤160 字）的判定口径
CLAIM_EXEMPT_ROLES = {"title", "qa", "backup"}
CLAIM_MAX_CHARS = 160
TIMING_TOLERANCE = 0.15          # B：Σ timing_s 偏离 budget.minutes 的容忍度
NOTES_SIM_LIMIT = 0.8            # D：notes 与 bullets 归一化相似度阈值
FULL_PAGE_IMAGE_RATIO = 0.8      # D：整页位图面积占比阈值

REQUIRED_OUT_ARTIFACTS = [
    "deck.pptx", "deck_plan.json", "speaker-notes.md",
    "evidence-map.json", "qa-prep.md", "render_manifest.json", "result.json",
]
REQUIRED_WORK_ARTIFACTS = ["materials.json", "template-index.json"]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _norm_ws(s) -> str:
    """连续空白 → 单空格，去首尾（CONTRACT §3.1 的"空白归一化"）。"""
    return re.sub(r"\s+", " ", str("" if s is None else s)).strip()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _similarity(a, b) -> float:
    """归一化相似度 ∈ [0,1]：包含关系 → 1.0，否则 difflib 比率。"""
    na, nb = _norm_ws(a), _norm_ws(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 4)


# 数字切分：逗号**只在「后接恰好 3 位数字」时**才算千分位。
# 否则 CSV 行引用里的 `2.8,85` 会被当成一个数（`2.885`）→ 页内 2.8 / 85 被判"无出处"（CJK 场景实测误杀）。
#   "1,234.5" → 1234.5 ｜ "2.8,85" → 2.8, 85 ｜ "1e-3" → 0.001
_NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _extract_numbers(text):
    """返回 [(原样 token, float)]；千分位逗号会被还原。"""
    out = []
    for m in _NUM_RE.finditer(_norm_ws(text)):
        raw = m.group(0)
        try:
            out.append((raw, float(raw.replace(",", ""))))
        except ValueError:
            continue
    return out


def _num_equal(a: float, b: float, rel: float = 1e-6, abs_tol: float = 1e-9) -> bool:
    if a == b:
        return True
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


def number_in_quote(value, quote) -> bool:
    """value 的每个数字都必须出现在 quote 里（容忍 2.8/2.80、1e-3、百分号、千分位）。"""
    q = _norm_ws(quote)
    if not q:
        return False
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        targets = [float(value)]
    else:
        targets = [v for _, v in _extract_numbers(str(value))]
        if not targets:
            return True          # 非数字 value：无可比数字
    has_pct = "%" in q
    qnums = [v for _, v in _extract_numbers(q)]
    for t in targets:
        hit = False
        for x in qnums:
            if _num_equal(t, x):
                hit = True
                break
            if has_pct and (_num_equal(t * 100.0, x) or _num_equal(t / 100.0, x)):
                hit = True
                break
        if not hit:
            return False
    return True


def quote_in_span(quote, span_text) -> bool:
    q, t = _norm_ws(quote), _norm_ws(span_text)
    return bool(q) and q in t


def _load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:                                   # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)


def _short_digest(path: Path, n: int = 12) -> str:
    try:
        return _sha256_file(path)[:n]
    except Exception as exc:                                   # noqa: BLE001
        return "<%s>" % type(exc).__name__


def _load_evidence_module():
    """优先复用 ingest-evidence 的 evidence.py（单一真相源），不可用时用本文件内建实现。"""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import evidence as _ev                                      # type: ignore
    except Exception:                                               # noqa: BLE001
        return None
    need = ("resolve_locator", "normalize_ws", "quote_in_span", "number_in_quote")
    if all(callable(getattr(_ev, k, None)) for k in need):
        return _ev
    return None


# --------------------------------------------------------------------------- #
# locator 解析（fallback：evidence.py 未就位时按 CONTRACT §3.1 文法自解析）
# --------------------------------------------------------------------------- #
def _lines_with_offsets(text: str):
    """1-based 行 → (chars[a,b], 文本)。"""
    out, pos = [], 0
    for ln in str(text or "").split("\n"):
        out.append(((pos, pos + len(ln)), ln))
        pos += len(ln) + 1
    return out


def fb_resolve_locator(materials: dict, file_name: str, locator: str) -> dict:
    """CONTRACT §3.1：locator 权威形态自带 basename（`paper.md#L9-L9`）。

    file_name 只在裸形态下用于拼 basename；防御 ingest 曾把 basename 重复拼一次的历史事故
    （`paper.md#paper.md#L9-L9`）——会剥掉重复前缀并在 ``stripped_prefix`` 里留痕。
    """
    loc = str(locator or "")
    head, _, tail = loc.partition("#")
    selector = head or str(file_name or "")
    stripped = False
    while tail and "#" in tail and tail.split("#", 1)[0] == selector:
        tail = tail.split("#", 1)[1]
        stripped = True
    norm_loc = ("%s#%s" % (selector, tail)) if tail else selector
    out = _fb_resolve_core(materials, selector, str(file_name or ""), norm_loc)
    if isinstance(out, dict):
        out["stripped_prefix"] = stripped
        out["normalized_locator"] = norm_loc
    return out


def _fb_resolve_core(materials: dict, file_name: str, fallback_name: str, locator: str) -> dict:
    files = (materials or {}).get("files") or []
    matches = [f for f in files if Path(str(f.get("path", ""))).name == str(file_name)]
    if not matches and fallback_name and fallback_name != file_name:
        matches = [f for f in files if Path(str(f.get("path", ""))).name == str(fallback_name)]
    ambiguous = len(matches) > 1
    if not matches:
        return {"ok": False, "reason": "file-not-found:%s" % file_name,
                "spans": [], "text": "", "ambiguous": False}
    f = matches[0]
    text = f.get("text") or ""
    spans = f.get("spans") or []
    frag = locator.split("#", 1)[1] if "#" in str(locator) else "all"

    def _pack(a, b, page=None, piece=None):
        piece = text[a:b] if piece is None else piece
        return {"ok": True, "reason": "", "ambiguous": ambiguous,
                "spans": [{"chars": [a, b], "page": page, "text": piece}], "text": piece}

    try:
        if frag == "all":
            return _pack(0, len(text))
        if frag.startswith("p."):
            n = int(frag[2:])
            for sp in spans:
                if sp.get("kind") == "page" and int(sp.get("page") or 0) == n:
                    a, b = sp.get("chars") or [0, len(text)]
                    return _pack(int(a), int(b), page=n)
            if "\f" in text:                      # 次选：换页符
                parts = text.split("\f")
                if 1 <= n <= len(parts):
                    a = sum(len(x) + 1 for x in parts[: n - 1])
                    return _pack(a, a + len(parts[n - 1]), page=n)
            return {"ok": False, "reason": "no-page-span:p.%d" % n,
                    "spans": [], "text": "", "ambiguous": ambiguous}
        if frag.startswith("L"):
            m = re.match(r"L(\d+)(?:-L(\d+))?$", frag)
            if not m:
                return {"ok": False, "reason": "bad-L-locator:%s" % frag,
                        "spans": [], "text": "", "ambiguous": ambiguous}
            a_ln, b_ln = int(m.group(1)), int(m.group(2) or m.group(1))
            lines = _lines_with_offsets(text)
            if a_ln < 1 or b_ln > len(lines) or a_ln > b_ln:
                return {"ok": False, "reason": "line-out-of-range:%d-%d/%d" % (a_ln, b_ln, len(lines)),
                        "spans": [], "text": "", "ambiguous": ambiguous}
            start = lines[a_ln - 1][0][0]
            end = lines[b_ln - 1][0][1]
            return _pack(start, end)
        if frag.startswith("r."):
            n = int(frag[2:])
            recs = [ln for ln in str(text or "").split("\n") if ln.strip()]
            if n < 1 or n > len(recs):
                return {"ok": False, "reason": "record-out-of-range:%d/%d" % (n, len(recs)),
                        "spans": [], "text": "", "ambiguous": ambiguous}
            return _pack(0, 0, piece=recs[n - 1])
        if frag.startswith("fig.") or frag.startswith("tab."):
            key, n = frag.split(".", 1)
            n = int(n)
            pool = f.get("figures" if key == "fig" else "tables") or []
            cand = None
            for i, it in enumerate(pool):
                ids = {str(it.get("id", "")), "%s%d" % (key, n), "%s.%d" % (key, n), "%s-%d" % (key, n)}
                if ("%s%d" % (key, n)) in ids or n in (i + 1,):
                    cand = it
                    break
            if cand is None:
                return {"ok": False, "reason": "no-%s:%d" % (key, n),
                        "spans": [], "text": "", "ambiguous": ambiguous}
            cap = cand.get("caption") or ""
            return {"ok": True, "reason": "", "ambiguous": ambiguous,
                    "spans": [{"chars": [0, 0], "page": cand.get("page"), "text": cap}],
                    "text": cap, "caption_only": True}
    except Exception as exc:                                        # noqa: BLE001
        return {"ok": False, "reason": "%s:%s" % (type(exc).__name__, exc),
                "spans": [], "text": "", "ambiguous": ambiguous}
    return {"ok": False, "reason": "unknown-locator:%s" % locator,
            "spans": [], "text": "", "ambiguous": ambiguous}


# --------------------------------------------------------------------------- #
# pptx / pdf / png 真读
# --------------------------------------------------------------------------- #
def _emu_area(sh):
    try:
        return int(sh.width or 0) * int(sh.height or 0)
    except Exception:                                               # noqa: BLE001
        return 0


def read_pptx(path: Path) -> dict:
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except Exception as exc:                                        # noqa: BLE001
        return {"ok": False, "reason": "python-pptx-unavailable:%s" % exc}
    try:
        prs = Presentation(str(path))
    except Exception as exc:                                        # noqa: BLE001
        return {"ok": False, "reason": "open-failed:%s" % exc}
    slide_area = int(prs.slide_width or 0) * int(prs.slide_height or 0)
    slides, fonts = [], set()
    # 模板注入的页码/日期/页脚不是作者文本，不参与"页内数字溯源"
    template_ph_types = ("SLIDE_NUMBER", "DATE", "FOOTER")
    for idx, slide in enumerate(prs.slides, 1):
        ph, tb, pics = [], [], []
        author_chunks, tmpl_chunks = [], []
        for sh in slide.shapes:
            try:
                is_ph = bool(sh.is_placeholder)
            except Exception:                                       # noqa: BLE001
                is_ph = False
            ph_type = ""
            if is_ph:
                try:
                    ph_type = str(sh.placeholder_format.type)
                except Exception:                                   # noqa: BLE001
                    ph_type = ""
            txt = ""
            try:
                if sh.has_text_frame:
                    txt = sh.text_frame.text or ""
                    for para in sh.text_frame.paragraphs:
                        for run in para.runs:
                            if run.font is not None and run.font.name:
                                fonts.add(run.font.name)
            except Exception:                                       # noqa: BLE001
                pass
            if txt.strip():
                if any(t in ph_type for t in template_ph_types):
                    tmpl_chunks.append(txt)
                else:
                    author_chunks.append(txt)
            if is_ph:
                ph.append({"name": sh.name, "text": txt, "type": ph_type})
            elif txt.strip():
                tb.append({"name": sh.name, "text": txt})
            try:
                if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    pics.append({"name": sh.name, "area": _emu_area(sh)})
            except Exception:                                       # noqa: BLE001
                pass
        notes = ""
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text or ""
        except Exception:                                           # noqa: BLE001
            notes = ""
        try:
            layout_name = slide.slide_layout.name
        except Exception:                                           # noqa: BLE001
            layout_name = "<unknown>"
        ph_text = _norm_ws(" ".join(p["text"] for p in ph))
        tb_text = _norm_ws(" ".join(t["text"] for t in tb))
        biggest_pic = max([p["area"] for p in pics], default=0)
        slides.append({
            "index": idx, "layout": layout_name,
            "placeholders": ph, "textboxes": tb, "pictures": pics,
            "ph_text": ph_text, "tb_text": tb_text, "notes": _norm_ws(notes),
            # 该页**全部作者文本**（占位符 + 自建 textbox；排除页码/日期/页脚）——gate A 的数字扫描面
            "all_text": _norm_ws(" ".join(author_chunks)),
            "template_text": _norm_ws(" ".join(tmpl_chunks)),
            "pic_area_ratio": round(biggest_pic / slide_area, 4) if slide_area else 0.0,
        })
    return {"ok": True, "slide_count": len(slides), "slides": slides,
            "fonts": sorted(fonts), "slide_area": slide_area,
            "size": [int(prs.slide_width or 0), int(prs.slide_height or 0)]}


def pdf_page_count(path: Path) -> dict:
    """双路：pypdf 真解析 + 原始字节 /Count /Type /Page。返回两路 + 头部校验。"""
    info = {"is_pdf": False, "bytes": 0, "pypdf": None, "raw_count": None,
            "raw_page_objs": None, "method": "none", "error": None}
    try:
        data = path.read_bytes()
    except Exception as exc:                                        # noqa: BLE001
        info["error"] = "%s:%s" % (type(exc).__name__, exc)
        return info
    info["bytes"] = len(data)
    info["is_pdf"] = data.startswith(b"%PDF-")
    if not info["is_pdf"]:
        return info
    counts = [int(x) for x in re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", data)
              + re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages", data)]
    if counts:
        info["raw_count"] = max(counts)
    info["raw_page_objs"] = len(re.findall(rb"/Type\s*/Page(?![sD])", data))
    try:
        from pypdf import PdfReader
        info["pypdf"] = len(PdfReader(str(path)).pages)
    except Exception as exc:                                        # noqa: BLE001
        info["error"] = "pypdf:%s" % exc
    if info["pypdf"] is not None:
        info["method"] = "pypdf+raw" if info["raw_count"] is not None else "pypdf"
    elif info["raw_count"] is not None:
        info["method"] = "raw-/Count"
    return info


def png_info(path: Path) -> dict:
    out = {"ok": False, "size": None, "dpi": None, "sha256": _short_digest(path, 16)}
    try:
        from PIL import Image
        with Image.open(str(path)) as im:
            out["size"] = list(im.size)
            dpi = im.info.get("dpi")
            if dpi:
                out["dpi"] = [round(float(dpi[0]), 2), round(float(dpi[1]), 2)]
        out["ok"] = True
    except Exception as exc:                                        # noqa: BLE001
        out["reason"] = "%s:%s" % (type(exc).__name__, exc)
    return out


def _real_key(p) -> str:
    """realpath 归一化（Windows 大小写不敏感），用于按真实路径匹配图片。"""
    try:
        import os
        return os.path.realpath(str(p)).lower().replace("/", "\\")
    except Exception:                                               # noqa: BLE001
        return str(p).lower().replace("/", "\\")


def _figure_media_expectation(ctx: Ctx, ev_item: dict) -> dict:
    """figure evidence 的期望 sha256（Lead 2026-11-23 权威取值顺序）。

    1. ``spec.evidence[].media_sha256``（deckplan 已回填）——正常路径一定有，以它为准；
    2. 否则回 ``materials.files[].figures[]``，**按 media_path 的 realpath 匹配**（Windows 大小写不敏感）：
       命中 >1 → ``ambiguous``（绝不取第一条）；命中 0 → ``无法复核``。
    图片实际 sha256 一律**真算一遍**再比（调用方负责）。
    """
    eid = str(ev_item.get("id"))
    paths = []
    if ev_item.get("media_path"):
        paths.append(str(ev_item["media_path"]))
    for p in ctx.pages:
        fig = p.get("figure") or {}
        if str(fig.get("evidence_id") or "") == eid and fig.get("media_path"):
            paths.append(str(fig["media_path"]))
    want = ev_item.get("media_sha256")
    if want:
        return {"paths": paths, "expected": str(want), "source": "spec.evidence.media_sha256",
                "ambiguous": 0, "matches": 1}
    targets = []
    for raw in paths:
        local = next((c for c in _media_candidates(ctx, raw) if c.is_file()), None)
        if local is not None:
            targets.append(_real_key(local))
    if not targets:
        return {"paths": paths, "expected": None, "source": "none", "ambiguous": 0, "matches": 0}
    key = targets[0]
    matches = []
    for f in (ctx.materials or {}).get("files") or []:
        for it in (f.get("figures") or []):
            mp = it.get("media_path")
            if not mp:
                continue
            local = next((c for c in _media_candidates(ctx, str(mp)) if c.is_file()), None)
            if _real_key(local if local is not None else mp) == key:
                matches.append(it)
    if len(matches) == 1:
        return {"paths": paths, "expected": (str(matches[0].get("media_sha256") or "") or None),
                "source": "materials.realpath", "ambiguous": 0, "matches": 1}
    if len(matches) > 1:
        return {"paths": paths, "expected": None, "source": "ambiguous",
                "ambiguous": len(matches), "matches": len(matches)}
    return {"paths": paths, "expected": None, "source": "none", "ambiguous": 0, "matches": 0}


def _media_candidates(ctx: Ctx, raw: str):
    """media_path 候选根（绝对路径优先；相对路径依次试 work_dir / out_dir / spec 目录 / input_dir）。"""
    p = Path(raw)
    if p.is_absolute():
        return [p]
    input_dir = (ctx.materials or {}).get("input_dir")
    cands = [ctx.work_dir / p, ctx.out_dir / p, ctx.spec_path.parent / p]
    if input_dir:
        base = Path(str(input_dir))
        cands += [base / p, base / "materials" / p.name, base / p.name, base.parent / p]
    cands += [p]
    dedup, seen = [], set()
    for c in cands:
        k = str(c)
        if k not in seen:
            seen.add(k)
            dedup.append(c)
    return dedup


def _renderer_unavailable(manifest) -> bool:
    if not isinstance(manifest, dict):
        return False
    if manifest.get("ok") is False and "render" in str(manifest.get("reason", "")).lower():
        return True
    if str(manifest.get("renderer", "")).lower() in ("unavailable", "renderer-unavailable"):
        return True
    if str(manifest.get("reason", "")) == "renderer-unavailable":
        return True
    return False


# --------------------------------------------------------------------------- #
# 上下文
# --------------------------------------------------------------------------- #
class Ctx:
    def __init__(self, out_dir, spec_path, work_dir, materials=None):
        self.out_dir = Path(out_dir)
        self.work_dir = Path(work_dir)
        self.spec_path = Path(spec_path)
        self.spec, self.spec_err = _load_json(self.spec_path)
        self.spec = self.spec or {}
        if materials is None:
            self.materials, self.materials_err = _load_json(self.work_dir / "materials.json")
            self.materials = self.materials or {}
        else:
            self.materials, self.materials_err = materials, None
        self.template_index, self.ti_err = _load_json(self.work_dir / "template-index.json")
        self.template_index = self.template_index or {}
        self.result, self.result_err = _load_json(self.out_dir / "result.json")
        self.result = self.result or {}
        self.evidence_map, self.em_err = _load_json(self.out_dir / "evidence-map.json")
        self.evidence_map = self.evidence_map or {}
        self.render_manifest, self.rm_err = _load_json(self.out_dir / "render_manifest.json")
        self.render_manifest = self.render_manifest or {}
        self.export_report, _ = _load_json(self.out_dir / "export_report.json")
        self.case, self.case_path = self._find_case()
        self.evmod = _load_evidence_module()
        self.ev_source = "module:evidence.py" if self.evmod else "fallback:validate.py"
        self._pptx_cache = None
        self._consumed = {}
        # resolve() 的统计计数器（gate A detail 用；必须在 __init__ 初始化）
        self.resolve_calls = 0
        self.resolve_module_ok = 0
        self.resolve_fallback_used = 0
        self.resolve_agree = 0
        self.resolve_disagree = []
        self.stripped_locators = 0
        for rel, p in [("spec.json", self.spec_path),
                       ("materials.json", self.work_dir / "materials.json"),
                       ("template-index.json", self.work_dir / "template-index.json"),
                       ("result.json", self.out_dir / "result.json"),
                       ("evidence-map.json", self.out_dir / "evidence-map.json"),
                       ("render_manifest.json", self.out_dir / "render_manifest.json"),
                       ("deck.pptx", self.out_dir / "deck.pptx"),
                       ("deck.pdf", self.out_dir / "deck.pdf")]:
            if p.is_file():
                self._consumed[rel] = {"path": str(p), "bytes": p.stat().st_size,
                                       "sha256": _sha256_file(p)}

    # ---- 用例输入 ----
    def _find_case(self):
        cands = [self.work_dir / "case.json",
                 self.work_dir.parent / "input" / "case.json",
                 self.out_dir.parent / "input" / "case.json"]
        ind = (self.materials or {}).get("input_dir")
        if ind:
            cands.append(Path(str(ind)) / "case.json")
        for c in cands:
            if c.is_file():
                obj, err = _load_json(c)
                if obj is not None:
                    return obj, c
        return {}, None

    # ---- pptx（缓存真读） ----
    @property
    def pptx(self):
        if self._pptx_cache is None:
            self._pptx_cache = read_pptx(self.out_dir / "deck.pptx")
        return self._pptx_cache

    # ---- 证据接口（module 优先，fallback 兜底 + 双实现分歧留痕） ----
    def resolve(self, file_name, locator, materials=None):
        """原样传入 locator（绝不自己拼 basename，CONTRACT §3.1）。

        用 evidence.py 与本地 fallback **各算一次**：module 判 ok 就以 module 为准，否则用 fallback
        结果（并记 ``fallback_used`` / 分歧项），这样即使 evidence.py 有 bug 也不会把整片证据误杀。
        """
        mats = self.materials if materials is None else materials
        mod = None
        if self.evmod is not None:
            try:
                mod = self.evmod.resolve_locator(mats, file_name, locator)
                if not isinstance(mod, dict) or "ok" not in mod:
                    mod = None
            except Exception as exc:                                # noqa: BLE001
                mod = {"ok": False, "reason": "evidence.py-raised:%s" % exc,
                       "spans": [], "text": ""}
        fb = fb_resolve_locator(mats, file_name, locator)
        self.resolve_calls += 1
        if fb.get("stripped_prefix"):
            self.stripped_locators += 1
        if mod is None:
            self.resolve_module_ok += 1 if fb.get("ok") else 0
            self.resolve_fallback_used += 1
            return fb
        if bool(mod.get("ok")) == bool(fb.get("ok")):
            self.resolve_agree += 1
        else:
            self.resolve_disagree.append("%s#%s module=%s/%s fallback=%s/%s"
                                         % (file_name, locator, mod.get("ok"), mod.get("reason"),
                                            fb.get("ok"), fb.get("reason")))
        if mod.get("ok"):
            return mod
        if fb.get("ok"):
            self.resolve_fallback_used += 1
            fb["module_reason"] = mod.get("reason")
            fb["module_disagreed"] = True
            return fb
        return mod

    def quote_ok(self, quote, span_text):
        try:
            if self.evmod is not None:
                return bool(self.evmod.quote_in_span(quote, span_text))
        except Exception:                                           # noqa: BLE001
            pass
        return quote_in_span(quote, span_text)

    def num_ok(self, value, quote):
        try:
            if self.evmod is not None:
                return bool(self.evmod.number_in_quote(value, quote))
        except Exception:                                           # noqa: BLE001
            pass
        return number_in_quote(value, quote)

    # ---- spec 便捷访问 ----
    @property
    def pages(self):
        return self.spec.get("pages") or []

    @property
    def evidence(self):
        return self.spec.get("evidence") or []

    @property
    def meta(self):
        return self.spec.get("meta") or {}

    def evidence_by_id(self):
        return {e.get("id"): e for e in self.evidence if e.get("id")}


def _g(gate_id, status, detail, failures=None, warnings=None, notes=None):
    return {"id": gate_id, "status": status, "detail": detail,
            "failures": failures or [], "warnings": warnings or [], "notes": notes or []}


def _status(failures, warnings):
    if failures:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


# --------------------------------------------------------------------------- #
# A — 证据溯源
# --------------------------------------------------------------------------- #
def gate_A(ctx: Ctx) -> dict:
    ev, pages = ctx.evidence, ctx.pages
    byid = ctx.evidence_by_id()
    failures, warnings = [], []

    dup_eid = sorted({i for i in [e.get("id") for e in ev] if [e.get("id") for e in ev].count(i) > 1})
    if dup_eid:
        failures.append("evidence id 重复: %s" % dup_eid)

    resolved = quote_hit = quote_checked = num_hit = num_checked = no_value = ambiguous = 0
    fig_no_cap = 0
    fig_quote_unverifiable = 0
    fig_media_ok = fig_media_bad = fig_media_warn = fig_media_ambiguous = 0
    for e in ev:
        src = e.get("source") or {}
        eid, kind = e.get("id"), e.get("kind")
        fn, loc, quote = src.get("file"), src.get("locator"), src.get("quote")
        if not fn or not loc:
            failures.append("%s E901 缺 source.file/locator" % eid)
            continue
        r = ctx.resolve(fn, loc)
        if r.get("ambiguous"):
            ambiguous += 1
        if not r.get("ok"):
            failures.append("%s E901 未解析 %s#%s (%s)" % (eid, fn, loc, r.get("reason")))
            continue
        resolved += 1
        # V11：figure evidence 的 locator 解析到**空 caption**（材料里根本没有可比图注）→
        # quote 无锚点可核验 → warn（无法验证），并要求写进 uncertainty（gate J 会查）。
        if kind == "figure" and _norm_ws(quote) and not _norm_ws(r.get("text")):
            fig_quote_unverifiable += 1
            warnings.append("%s figure evidence 的 %s#%s 解析到空 caption（材料无图注）→ quote 无法逐字"
                            "核验，改判 warn 并要求写入 uncertainty" % (eid, fn, loc))
            continue

        # CONTRACT §5 gate A：figure evidence 无 caption（quote 归一化后为空）→ 跳过 E902，
        # 改判「locator 可解析（已在上一步） + media 真实文件 sha256 == media_sha256」。
        # 注意：带 value 的数字类 evidence 不走此分支（E903 一律不放宽）。
        if kind == "figure" and not _norm_ws(quote) and e.get("value") is None:
            fig_no_cap += 1
            exp = _figure_media_expectation(ctx, e)
            local = None
            for raw in exp.get("paths") or []:
                local = next((c for c in _media_candidates(ctx, raw) if c.is_file()), None)
                if local is not None:
                    break
            if exp.get("ambiguous"):
                fig_media_ambiguous += 1
                warnings.append("%s figure evidence 无 caption：media_path 命中 %d 条材料登记（ambiguous），"
                                "不取第一条、无法复核字节" % (eid, exp["ambiguous"]))
            elif local is None:
                fig_media_warn += 1
                warnings.append("%s figure evidence 无 caption 且 media 定位不到（paths=%s）→ 仅有 locator "
                                "解析作为证据" % (eid, exp.get("paths")))
            elif not exp.get("expected"):
                fig_media_warn += 1
                warnings.append("%s figure evidence 无 caption 且期望 sha256 缺失（来源 %s）→ 无法复核字节"
                                % (eid, exp.get("source")))
            else:
                got = _sha256_file(local)
                if got == exp["expected"]:
                    fig_media_ok += 1
                else:
                    fig_media_bad += 1
                    failures.append("%s figure evidence 无 caption：media %s 实际 sha256 %s ≠ 期望 %s（来源 %s）"
                                    % (eid, local.name, got[:12], str(exp["expected"])[:12],
                                       exp.get("source")))
            continue

        if not ctx.quote_ok(quote, r.get("text") or ""):
            quote_checked += 1
            failures.append("%s E902 quote 未逐字命中 %s#%s" % (eid, fn, loc))
            continue
        quote_hit += 1
        quote_checked += 1
        if e.get("value") is None:
            no_value += 1
        else:
            num_checked += 1
            if ctx.num_ok(e.get("value"), quote):
                num_hit += 1
            else:
                failures.append("%s E903 数字 %r 不在 quote" % (eid, e.get("value")))

    dangling, covered, exempt = [], 0, 0
    for p in pages:
        pid, role = p.get("id"), p.get("role")
        eids = list(p.get("evidence_ids") or [])
        for x in eids:
            if x not in byid:
                dangling.append("%s evidence_ids 悬空: %s" % (pid, x))
        if role not in CONTENT_ROLES:
            exempt += 1
        elif eids:
            covered += 1
        else:
            failures.append("%s (role=%s) claim=%r 无 evidence_ids" %
                            (pid, role, _norm_ws(p.get("claim"))[:40]))
    need = len([p for p in pages if p.get("role") in CONTENT_ROLES])
    # 悬空 evidence_ids（判据读 spec.pages[].evidence_ids，不读 deck_plan——build 可能已过滤掉悬空项）
    if dangling:
        failures.append("evidence_ids 悬空 %d 项: %s"
                        % (len(dangling), ", ".join(dangling[:8])))

    # §1.3 落地：**内容页**数字必须能在该页 evidence 的 quote 里找到出处。
    # 扫描面 = 三处并集（V03+V06）：spec 文本 ∪ deck.pptx 实际形状文本（含自建 textbox）∪ notes_slide 文本。
    # 只读 spec 会让"幻灯片上自建 textbox 写 99.9"或"只改备注写 77.7"绕过，A–J 全过。
    untraceable, page_num_total, page_num_ok = [], 0, 0
    SRC_KEYS = ("spec", "pptx", "notes")
    src_tot = {k: 0 for k in SRC_KEYS}
    src_ok = {k: 0 for k in SRC_KEYS}
    px_slides = (ctx.pptx.get("slides") or []) if ctx.pptx.get("ok") else []
    for idx, p in enumerate(pages):
        if p.get("role") not in CONTENT_ROLES:
            continue
        quotes = []
        for eid in p.get("evidence_ids") or []:
            e = byid.get(eid)
            if e:
                quotes.append((e.get("source") or {}).get("quote") or "")
        fig = p.get("figure") or {}
        for eid in ([fig.get("evidence_id")] if fig.get("evidence_id") else []):
            e = byid.get(eid)
            if e:
                quotes.append((e.get("source") or {}).get("quote") or "")
        sl = px_slides[idx] if idx < len(px_slides) else {}
        texts = {
            "spec": " ".join([str(p.get("claim") or "")] + [str(b) for b in (p.get("bullets") or [])]),
            "pptx": str(sl.get("all_text") or ""),
            "notes": str(sl.get("notes") or ""),
        }
        for src, txt in texts.items():
            for raw, val in _extract_numbers(txt):
                src_tot[src] += 1
                page_num_total += 1
                if any(q and ctx.num_ok(val, q) for q in quotes):
                    src_ok[src] += 1
                    page_num_ok += 1
                else:
                    untraceable.append("%s[%s]:%s" % (p.get("id"), src, raw))
    if untraceable:
        failures.append("内容页 %d 个数字在 evidence quote 中无出处: %s"
                        % (len(untraceable), ", ".join(untraceable[:8])))

    # 真实产物交叉核验：evidence-map.json 覆盖率
    em_pages = ctx.evidence_map.get("pages") or ctx.evidence_map.get("page_map") or []
    em_ids = set()
    for it in em_pages:
        key = it.get("slide_id") or it.get("page_id") or it.get("id")
        if key:
            em_ids.add(str(key))
    em_covered = sum(1 for p in pages if str(p.get("id")) in em_ids)
    if not em_pages:
        warnings.append("evidence-map.json 无 pages/page_map 条目（无法交叉核验页级映射）")
    elif em_covered < len(pages):
        failures.append("evidence-map.json 仅覆盖 %d/%d 页（缺 %s）"
                        % (em_covered, len(pages),
                           ",".join(str(p.get("id")) for p in pages if str(p.get("id")) not in em_ids)))

    detail = ("evidence %d 项: locator 解析 %d/%d, quote 逐字命中 %d/%d (另有无 caption figure %d 跳过), "
              "数字在 quote %d/%d; "
              "figure-evidence-without-caption %d (media sha256 相符 %d / 不符 %d / "
              "无法复核 %d / ambiguous %d); figure-caption-不可核验 %d; "
              "内容页证据覆盖 %d/%d (免检 title/qa/backup %d); "
              "悬空 evidence_ids %d; "
              "内容页数字可溯源 %d/%d (spec %d/%d, pptx 文本 %d/%d, notes %d/%d); "
              "evidence-map 覆盖 %d/%d 页; 解析器=%s; "
              "module/fallback 判定分歧 %d 项(剥重复 basename %d 次); ambiguous 文件 %d"
              % (len(ev), resolved, len(ev), quote_hit, quote_checked, fig_no_cap,
                 num_hit, num_checked,
                 fig_no_cap, fig_media_ok, fig_media_bad, fig_media_warn, fig_media_ambiguous,
                 fig_quote_unverifiable,
                 covered, need, exempt, len(dangling),
                 page_num_ok, page_num_total,
                 src_ok["spec"], src_tot["spec"], src_ok["pptx"], src_tot["pptx"],
                 src_ok["notes"], src_tot["notes"],
                 em_covered, len(pages), ctx.ev_source,
                 len(ctx.resolve_disagree), ctx.stripped_locators, ambiguous))
    if ctx.resolve_disagree:
        notes_extra = ["module/fallback 分歧: " + "; ".join(ctx.resolve_disagree[:5])]
    else:
        notes_extra = []
    if ctx.resolve_fallback_used:
        notes_extra.append("fallback 兜底 %d/%d 次调用" % (ctx.resolve_fallback_used, ctx.resolve_calls))
    return _g("A", _status(failures, warnings), detail, failures, warnings, notes_extra)


# --------------------------------------------------------------------------- #
# B — 叙事与预算
# --------------------------------------------------------------------------- #
def _as_number(v):
    """显式强转（V10）：数字、或能转的数字串 → float；否则 None（不许静默降级成 warn）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def gate_B(ctx: Ctx) -> dict:
    pages, meta = ctx.pages, ctx.meta
    budget = meta.get("budget") or {}
    story = meta.get("story") or {}
    failures, warnings, notes = [], [], []

    n_pages = len(pages)
    sc_raw = budget.get("slide_count")
    sc = _as_number(sc_raw)
    if sc is None:
        failures.append("budget.slide_count 缺失或类型错误: %r (%s)"
                        % (sc_raw, type(sc_raw).__name__))
    else:
        if not isinstance(sc_raw, int):
            notes.append("budget.slide_count 由 %s %r 强转为 %d" % (type(sc_raw).__name__, sc_raw, int(sc)))
        if int(sc) != n_pages:
            failures.append("页数 %d ≠ budget.slide_count %d" % (n_pages, int(sc)))

    tsum = 0.0
    for p in pages:
        try:
            tsum += float((p.get("notes") or {}).get("timing_s") or 0)
        except Exception:                                           # noqa: BLE001
            pass
    minutes_raw = budget.get("minutes")
    minutes = _as_number(minutes_raw)
    dev = None
    if minutes is None:
        failures.append("budget.minutes 缺失或类型错误: %r (%s)" % (minutes_raw, type(minutes_raw).__name__))
    elif minutes <= 0:
        failures.append("budget.minutes 非正数: %r" % minutes_raw)
    else:
        if not isinstance(minutes_raw, (int, float)):
            notes.append("budget.minutes 由 %s %r 强转为 %.2f" % (type(minutes_raw).__name__,
                                                               minutes_raw, minutes))
        target = float(minutes) * 60.0
        dev = abs(tsum - target) / target
        if dev > TIMING_TOLERANCE:
            failures.append("Σtiming_s %.1f 偏离 budget.minutes=%.2fmin(%.0fs) %.1f%% > %.0f%%"
                            % (tsum, minutes, target, dev * 100, TIMING_TOLERANCE * 100))

    empty_claim, long_claim = [], []
    for p in pages:
        role = p.get("role")
        if role in CLAIM_EXEMPT_ROLES:
            continue
        claim = _norm_ws(p.get("claim"))
        if not claim:
            empty_claim.append(str(p.get("id")))
        elif len(claim) > CLAIM_MAX_CHARS:
            long_claim.append("%s(%d)" % (p.get("id"), len(claim)))
    if empty_claim:
        failures.append("内容页 claim 为空: %s" % ",".join(empty_claim))
    if long_claim:
        failures.append("内容页 claim >%d 字: %s" % (CLAIM_MAX_CHARS, ",".join(long_claim)))

    arc = story.get("arc") or []
    arc_set = {str(x) for x in arc}
    req = ["problem", "method", "results", "conclusion"]
    missing_arc = [x for x in req if x not in arc_set]
    if missing_arc:
        failures.append("story.arc 缺 %s" % ",".join(missing_arc))

    bad_role = sorted({str(p.get("role")) for p in pages if p.get("role") not in ROLES})
    if bad_role:
        failures.append("role 不在枚举内: %s" % ",".join(bad_role))

    type_note = ""
    if sc_raw is not None and not isinstance(sc_raw, int):
        type_note += " [slide_count 类型错误: %s]" % type(sc_raw).__name__
    elif sc_raw is None:
        type_note += " [slide_count 缺失]"
    if minutes_raw is not None and not isinstance(minutes_raw, (int, float)):
        type_note += " [minutes 类型错误: %s]" % type(minutes_raw).__name__
    elif minutes_raw is None:
        type_note += " [minutes 缺失]"
    detail = ("spec 页数 %d vs budget.slide_count %s; Σtiming_s %.1f vs %smin=%.0fs (%s); "
              "内容页 claim 空 %d, >%d 字 %d; story.arc %d 项缺 %s; 非法 role %d%s"
              % (n_pages, sc_raw, tsum, minutes_raw if minutes is not None else "?",
                 float(minutes or 0) * 60,
                 ("偏离 %.1f%%" % (dev * 100)) if dev is not None else "无法比对",
                 len(empty_claim), CLAIM_MAX_CHARS, len(long_claim),
                 len(arc), ",".join(missing_arc) or "0", len(bad_role), type_note))
    return _g("B", _status(failures, warnings), detail, failures, warnings, notes)


# --------------------------------------------------------------------------- #
# C — 模板合规
# --------------------------------------------------------------------------- #
def gate_C(ctx: Ctx) -> dict:
    pages, ti = ctx.pages, ctx.template_index
    failures, warnings, notes = [], [], []

    layouts = [str(l.get("name")) for l in (ti.get("layouts") or [])]
    layout_set = set(layouts)
    masters = ti.get("masters") or []
    theme_fonts = ((masters[0].get("theme_fonts") or {}) if masters else {})
    theme_colors = [str(v).upper() for v in (((masters[0].get("theme_colors") or {}) if masters else {}).values())]
    src_name = str((ctx.spec.get("theme") or {}).get("source") or "template")

    if not layouts:
        warnings.append("template-index.json 无 layouts（无法校验 layout 合法性）")

    # spec 显式 layout 必须合法；layout=null/缺失 = "交由 build 解析"，不算 fail（CONTRACT §5 适用范围）
    explicit = [(str(p.get("id")), str(p.get("layout"))) for p in pages
                if p.get("layout") not in (None, "", "null")]
    null_layout = [str(p.get("id")) for p in pages if p.get("layout") in (None, "", "null")]
    bad_layout = [pid for pid, lay in explicit if layouts and lay not in layout_set]
    if bad_layout:
        failures.append("spec 页 layout 不在模板: %s" % ",".join(bad_layout))

    px = ctx.pptx
    pptx_pages = px.get("slide_count") if px.get("ok") else None
    if not px.get("ok"):
        warnings.append("deck.pptx 无法用 python-pptx 打开: %s" % px.get("reason"))
    elif pptx_pages != len(pages):
        failures.append("deck.pptx %d 页 ≠ spec %d 页" % (pptx_pages, len(pages)))

    # deck_plan.json 是 build 回写的实际 layout 权威
    plan, plan_err = _load_json(ctx.out_dir / "deck_plan.json")
    plan_layouts = {}
    if isinstance(plan, dict):
        for it in (plan.get("slides") or plan.get("pages") or []):
            key = it.get("slide_id") or it.get("page_id") or it.get("id")
            if key:
                plan_layouts[str(key)] = str(it.get("layout") or "")
    if not plan_layouts:
        warnings.append("deck_plan.json 无可解析的逐页 layout（%s）→ 只按 pptx×spec 比对"
                        % (plan_err or "slides[] 缺 layout"))

    pptx_layout_bad, plan_mismatch, spec_mismatch, per_slide_match = [], [], [], 0
    tb_abuse, ph_body = [], 0
    if px.get("ok"):
        for i, (sl, sp) in enumerate(zip(px["slides"], pages)):
            pid = str(sp.get("id"))
            lay, spec_lay = str(sl.get("layout")), sp.get("layout")
            if layouts and lay not in layout_set:
                pptx_layout_bad.append("%s:%s" % (pid, lay))
            p_lay = plan_layouts.get(pid)
            if p_lay and lay == p_lay:
                per_slide_match += 1
            elif p_lay and lay != p_lay:
                plan_mismatch.append("%s pptx=%s plan=%s" % (pid, lay, p_lay))
            if spec_lay not in (None, "", "null") and lay != str(spec_lay):
                spec_mismatch.append("%s pptx=%s spec=%s" % (pid, lay, spec_lay))
            if sl.get("ph_text"):
                ph_body += 1
            elif sl.get("tb_text") and sp.get("role") in CONTENT_ROLES:
                tb_abuse.append(pid)
        if pptx_layout_bad:
            failures.append("deck.pptx 页 layout 不在模板: %s" % ",".join(pptx_layout_bad))
        if plan_mismatch:
            failures.append("deck.pptx 逐页 layout 与 deck_plan.json 不一致 %d 页: %s"
                            % (len(plan_mismatch), "; ".join(plan_mismatch[:4])))
        if spec_mismatch:
            failures.append("deck.pptx 逐页 layout 与 spec 显式 layout 不一致 %d 页: %s"
                            % (len(spec_mismatch), "; ".join(spec_mismatch[:4])))
        if tb_abuse:
            failures.append("内容页正文写在自建 textbox 而非 layout 占位符: %s" % ",".join(tb_abuse))
        if null_layout:
            notes.append("spec layout=null 的页 %d 个（%s）→ 以 build 回写的 deck.pptx/deck_plan 为准"
                         % (len(null_layout), ",".join(null_layout[:6])))
        notes.append("占位符 geom 改动（height/width/left）不视为违规：只校验 is_placeholder、"
                     "layout 合法性与一致性")

    font_ok = font_bad = None
    if px.get("ok"):
        allowed = {str(theme_fonts.get("major", "")), str(theme_fonts.get("minor", ""))} - {""}
        used = set(px.get("fonts") or [])
        font_ok, font_bad = len(used & allowed), sorted(used - allowed)
        if not allowed:
            warnings.append("template-index masters[0].theme_fonts 缺失，字体合规无法比对")
        elif font_bad and src_name != "override":
            failures.append("pptx 用字体 %s 不在模板 theme %s 且 theme.source=%s"
                            % (",".join(font_bad), sorted(allowed), src_name))

    colors = (ctx.spec.get("theme") or {}).get("colors") or {}
    color_hit = sum(1 for v in colors.values() if str(v).upper() in theme_colors)
    if not theme_colors:
        warnings.append("template-index theme_colors 缺失，配色合规无法比对")
    elif colors and color_hit == 0 and src_name != "override":
        failures.append("spec 配色 0/%d 命中模板调色板且 theme.source=%s" % (len(colors), src_name))

    detail = ("template-index: %d layouts / %d masters; spec 显式 layout 合法 %d/%d (null %d); "
              "deck.pptx %s 页 vs spec %d 页; pptx layout ∈ 模板 %d/%d; pptx↔deck_plan 一致 %d/%d; "
              "正文占位符 %d/%d 页 (内容页自建 textbox 滥用 %d 页); "
              "pptx 字体 %s ∩ 模板 theme %d/%d (越界 %s); 配色命中模板 %d/%d (theme.source=%s)"
              % (len(layouts), len(masters), len(explicit) - len(bad_layout), len(explicit),
                 len(null_layout), pptx_pages if pptx_pages is not None else "打不开", len(pages),
                 len(px["slides"]) - len(pptx_layout_bad) if px.get("ok") else 0, len(pages),
                 per_slide_match, len(plan_layouts), ph_body, len(pages), len(tb_abuse),
                 sorted(px.get("fonts") or []), font_ok if font_ok is not None else 0,
                 len(px.get("fonts") or []) if px.get("ok") else 0,
                 ",".join(font_bad or []) or "0", color_hit, len(colors), src_name))
    return _g("C", _status(failures, warnings), detail, failures, warnings, notes)


# --------------------------------------------------------------------------- #
# D — 可编辑与讲稿
# --------------------------------------------------------------------------- #
def gate_D(ctx: Ctx) -> dict:
    pages = ctx.pages
    failures, warnings, notes = [], [], []
    px = ctx.pptx
    bitmap, no_notes = [], []
    sims_pptx = []                       # [(page_id, role, sim)] —— 显式配对，避免下标错位
    if not px.get("ok"):
        warnings.append("deck.pptx 无法打开，讲稿/位图检查无法执行: %s" % px.get("reason"))
    else:
        for sl, sp in zip(px["slides"], pages):
            pid, role = str(sp.get("id")), sp.get("role")
            if sl.get("pic_area_ratio", 0) >= FULL_PAGE_IMAGE_RATIO and not sl.get("ph_text") \
                    and not sl.get("tb_text"):
                bitmap.append(pid)
            ntxt = _norm_ws(sl.get("notes"))
            if ntxt:
                sims_pptx.append((pid, role, _similarity(sl.get("ph_text") or sl.get("tb_text"), ntxt)))
            else:
                # 内容页必须有备注；title/qa/backup 若 spec 声明了 notes.say 也必须有
                declared = bool(_norm_ws((sp.get("notes") or {}).get("say")))
                if role in CONTENT_ROLES or declared:
                    no_notes.append(pid)
        if bitmap:
            failures.append("整页位图（不可编辑）: %s" % ",".join(bitmap))
        if no_notes:
            failures.append("deck.pptx 缺备注页: %s" % ",".join(no_notes))

    # 去重检查只判内容页（CONTRACT §5 适用范围）
    sims_spec = []                       # [(page_id, role, sim)]
    empty_bullets, claim_copy = [], []
    for p in pages:
        pid, role = str(p.get("id")), p.get("role")
        notes_obj = p.get("notes") or {}
        say = notes_obj.get("say")
        bullets = " ".join(str(b) for b in (p.get("bullets") or []))
        if role in CONTENT_ROLES and say is None:
            warnings.append("%s (内容页) notes 缺 say 字段" % pid)
        # V12：内容页 bullets 为空 + notes 照抄 claim（相似度 ≥0.8）→ 等同于没有讲稿
        if role in CONTENT_ROLES and not _norm_ws(bullets) and say is not None:
            empty_bullets.append(pid)
            sim_claim = _similarity(say, p.get("claim"))
            if sim_claim >= NOTES_SIM_LIMIT:
                claim_copy.append("%s=%.2f" % (pid, sim_claim))
        if say is None:
            continue
        if not _norm_ws(say):
            if role in CONTENT_ROLES:
                failures.append("%s (内容页) notes.say 为空" % pid)
            else:
                warnings.append("%s.notes.say 为空但非内容页，仅告警" % pid)
            continue
        sims_spec.append((pid, role, _similarity(bullets, say)))
    if claim_copy:
        failures.append("内容页 bullets 为空且 notes 与 claim 相似度 ≥%.1f: %s"
                        % (NOTES_SIM_LIMIT, ",".join(claim_copy)))

    spec_content = [(i, s) for i, r, s in sims_spec if r in CONTENT_ROLES]
    over_spec = [(i, s) for i, s in spec_content if s >= NOTES_SIM_LIMIT]
    max_spec = max([s for _, s in spec_content], default=0.0)
    exempt_spec = len(sims_spec) - len(spec_content)
    if over_spec:
        failures.append("notes 与 bullets 相似度 ≥%.1f: %s"
                        % (NOTES_SIM_LIMIT, ",".join("%s=%.2f" % (i, s) for i, s in over_spec)))

    pptx_content = [(i, s) for i, r, s in sims_pptx if r in CONTENT_ROLES]
    over_pptx = [(i, s) for i, s in pptx_content if s >= NOTES_SIM_LIMIT]
    max_pptx = max([s for _, s in pptx_content], default=0.0)
    exempt_pptx = len(sims_pptx) - len(pptx_content)
    if over_pptx:
        failures.append("pptx 正文与备注相似度 ≥%.1f: %s"
                        % (NOTES_SIM_LIMIT, ",".join("%s=%.2f" % (i, s) for i, s in over_pptx)))

    detail = ("deck.pptx %s 页: 备注非空 %d/%d (缺 %d), 整页位图 %d/%d(阈值面积 %.1f); "
              "内容页 bullets 空 %d (其中 notes↔claim ≥%.1f 的 %d); "
              "spec 内容页 bullets↔notes.say 最大相似度 %.2f (≥%.1f 的 %d/%d, 非内容页免检 %d); "
              "pptx 内容页 正文↔备注 最大相似度 %.2f (≥%.1f 的 %d/%d, 非内容页免检 %d)"
              % (px.get("slide_count") if px.get("ok") else "打不开",
                 len(pages) - len(no_notes) if px.get("ok") else 0, len(pages), len(no_notes),
                 len(bitmap), len(pages), FULL_PAGE_IMAGE_RATIO,
                 len(empty_bullets), NOTES_SIM_LIMIT, len(claim_copy),
                 max_spec, NOTES_SIM_LIMIT, len(over_spec), len(spec_content), exempt_spec,
                 max_pptx, NOTES_SIM_LIMIT, len(over_pptx), len(pptx_content), exempt_pptx))
    return _g("D", _status(failures, warnings), detail, failures, warnings, notes)


# --------------------------------------------------------------------------- #
# E — 图表绑定
# --------------------------------------------------------------------------- #
def gate_E(ctx: Ctx) -> dict:
    pages, mats = ctx.pages, ctx.materials
    byid = ctx.evidence_by_id()
    failures, warnings = [], []
    fig_pages = [p for p in pages if (p.get("figure") or {}).get("evidence_id")
                 or (p.get("figure") or {}).get("media_path")]
    media_ok = media_missing = sha_ok = sha_bad = 0
    caption_same = caption_trunc = caption_diff_ok = caption_diff_bad = caption_unknown = 0
    searched = 0

    # 材料侧索引：evidence locator → 材料 figure/table
    mat_figs = {}
    for f in (mats.get("files") or []):
        for kind in ("figures", "tables"):
            for it in (f.get(kind) or []):
                for key in (it.get("id"), it.get("locator")):
                    if key:
                        mat_figs[str(key)] = it
        for it in (f.get("figures") or []):
            if it.get("media_path"):
                mat_figs["media:" + Path(str(it["media_path"])).name] = it

    for p in fig_pages:
        pid = str(p.get("id"))
        fig = p.get("figure") or {}
        feid = fig.get("evidence_id")
        eids = list(p.get("evidence_ids") or [])
        if feid and feid not in eids:
            failures.append("%s figure.evidence_id=%s 不在页 evidence_ids %s" % (pid, feid, eids))
        e = byid.get(feid) if feid else None
        loc = ((e or {}).get("source") or {}).get("locator")
        key_hits = [mat_figs.get(str(k)) for k in (feid, loc) if k and mat_figs.get(str(k))]
        mat_fig = next((x for x in key_hits if x), None)

        # media 存在性 + sha256（media_path 可能是绝对路径，也可能是 work_dir/input 相对路径）
        mp = fig.get("media_path")
        if mp:
            searched += 1
            cands = _media_candidates(ctx, str(mp))
            local = next((c for c in cands if c.is_file()), None)
            declared = mat_figs.get("media:" + Path(str(mp)).name)
            if local is not None:
                media_ok += 1
                want = (declared or {}).get("media_sha256")
                if want:
                    got = _sha256_file(local)
                    if got == want:
                        sha_ok += 1
                    else:
                        sha_bad += 1
                        failures.append("%s media %s sha256 %s ≠ 材料登记 %s"
                                        % (pid, mp, got[:12], str(want)[:12]))
                else:
                    warnings.append("%s media %s 找到但材料未登记 media_sha256（无法比对字节）" % (pid, mp))
            elif declared is not None:
                warnings.append("%s media %s 本地不可读（试过 %d 个候选根），但 materials.json 已登记 "
                                "sha256=%s（无法复核字节）"
                                % (pid, mp, len(cands), str(declared.get("media_sha256"))[:12]))
                media_ok += 1
            else:
                media_missing += 1
                failures.append("%s figure.media_path 不存在且材料未登记: %s（候选 %s）"
                                % (pid, mp, " | ".join(str(c) for c in cands[:6])))

        # caption 一致性（三分支；长图注被版式截断是**合理处置**，不算缺陷）
        cap = _norm_ws(fig.get("caption"))
        if cap:
            src_cap = _norm_ws((mat_fig or {}).get("caption"))
            adapted_flag = bool(fig.get("caption_adapted"))
            low = cap.lower()
            if not src_cap:
                caption_unknown += 1
                warnings.append("%s 图注无法与材料 caption 比对（材料无 caption）" % pid)
            elif adapted_flag or "adapt" in low or "缩写" in cap or "adapt" in src_cap.lower():
                caption_diff_ok += 1
            elif cap == src_cap:
                caption_same += 1
            else:
                a = re.sub(r"[\s.…·]+$", "", cap)
                b = re.sub(r"[\s.…·]+$", "", src_cap)
                shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
                # 前缀 + 至少 20 字符且 ≥40% 长度，才算"截断"
                if longer.startswith(shorter) and len(shorter) >= 20 and len(shorter) >= 0.4 * len(longer):
                    caption_trunc += 1
                else:
                    caption_diff_bad += 1
                    failures.append("%s 图注与材料 caption 不符(sim=%.2f) 且未标 adapted: %r vs %r"
                                    % (pid, _similarity(cap, src_cap), cap[:60], src_cap[:60]))

    if not fig_pages:
        warnings.append("spec 无任何带 figure 的页（图表绑定无样本可验）")

    detail = ("带图页 %d/%d; figure.evidence_id ∈ 页 evidence_ids %d/%d; "
              "media_path 可解析 %d/%d (sha256 校验 %d, 不符 %d, 无法复核 %d); "
              "caption 一致 %d, 一致(截断) %d, adapted %d, 不符 %d, 无法比对 %d"
              % (len(fig_pages), len(pages),
                 len(fig_pages) - len([f for f in failures if "不在页 evidence_ids" in f]), len(fig_pages),
                 media_ok, searched, sha_ok, sha_bad, len([w for w in warnings if "无法复核字节" in w]),
                 caption_same, caption_trunc, caption_diff_ok, caption_diff_bad, caption_unknown))
    return _g("E", _status(failures, warnings), detail, failures, warnings)


# --------------------------------------------------------------------------- #
# F — 渲染与导出
# --------------------------------------------------------------------------- #
def gate_F(ctx: Ctx) -> dict:
    failures, warnings, notes = [], [], []
    px = ctx.pptx
    pptx_pages = px.get("slide_count") if px.get("ok") else None
    pdf = ctx.out_dir / "deck.pdf"
    pdfinfo = pdf_page_count(pdf) if pdf.is_file() else {"is_pdf": False, "bytes": 0}
    man = ctx.render_manifest
    unavailable = _renderer_unavailable(man)
    prev_dir = ctx.out_dir / "preview"
    pngs = sorted(prev_dir.glob("*.png")) if prev_dir.is_dir() else []
    preview_dir = Path(str((man or {}).get("preview_dir") or "preview"))
    if not preview_dir.is_absolute():
        preview_dir = ctx.out_dir / preview_dir
    man_pc = man.get("pageCount") if isinstance(man, dict) else None
    man_dpi = None
    try:
        man_dpi = float(man.get("dpi")) if isinstance(man, dict) and man.get("dpi") is not None else None
    except (TypeError, ValueError):
        man_dpi = None
    slide_size_in = (man or {}).get("slide_size_in") or {}
    w_in = None
    if isinstance(slide_size_in, dict) and slide_size_in.get("w"):
        w_in = float(slide_size_in["w"])
    elif (ctx.template_index.get("slide_size") or {}).get("w_in"):
        w_in = float(ctx.template_index["slide_size"]["w_in"])
    elif px.get("ok") and px.get("size") and px["size"][0]:
        w_in = px["size"][0] / 914400.0

    # ---- (1) pdf 头/页数 ----
    pdf_n = None
    if not pdf.is_file() or not pdfinfo.get("is_pdf"):
        if unavailable:
            warnings.append("deck.pdf 缺失且 render_manifest 声明渲染器不可用（%s）→ 无法验证导出"
                            % man.get("reason"))
        else:
            failures.append("deck.pdf 缺失或非 PDF 头（bytes=%s）" % pdfinfo.get("bytes"))
    else:
        pdf_n = pdfinfo.get("pypdf") if pdfinfo.get("pypdf") is not None else pdfinfo.get("raw_count")
        if pdfinfo.get("pypdf") is not None and pdfinfo.get("raw_count") is not None \
                and pdfinfo["pypdf"] != pdfinfo["raw_count"]:
            failures.append("deck.pdf 页数两路不一致: pypdf=%s raw/Count=%s"
                            % (pdfinfo["pypdf"], pdfinfo["raw_count"]))

    # ---- (2) 页数四方一致 PNG == manifest.pageCount == pdf == pptx ----
    # CONTRACT §5/§6：渲染器不可用是**诚实降级** → warn + status partial，pptx 仍交付，不得判 fail。
    counts_consistent = "渲染器不可用(未核验)"
    if unavailable:
        warnings.append("渲染器不可用（reason=%r, renderer=%r）→ 无法核验导出/页数一致性；"
                        "pptX 仍交付，需写入 uncertainty 并把 status 降为 partial"
                        % (man.get("reason"), man.get("renderer") if isinstance(man, dict) else None))
    else:
        counts = {"PNG": len(pngs), "manifest.pageCount": man_pc, "deck.pdf": pdf_n,
                  "deck.pptx": pptx_pages}
        present = {k: v for k, v in counts.items() if isinstance(v, int)}
        counts_consistent = (bool(len(set(present.values())) <= 1) if len(present) >= 2
                             else "样本不足(%d)" % len(present))
        if len(set(present.values())) > 1:
            failures.append("页数不一致: " + " / ".join("%s=%s" % (k, v) for k, v in counts.items()))
        if not isinstance(man_pc, int):
            warnings.append("render_manifest.pageCount 缺失/非整数（%r）" % man_pc)
        if isinstance(pptx_pages, int) and isinstance(man_pc, int) and pptx_pages != man_pc:
            failures.append("render_manifest.pageCount %d ≠ deck.pptx %d 页" % (man_pc, pptx_pages))

    if not pngs:
        if unavailable:
            warnings.append("preview/ 无 PNG 且渲染器不可用 → 无法验证逐页预览")
        else:
            failures.append("preview/ 缺 PNG（期望 %s 页，实测 0）" % pptx_pages)

    # ---- (3) dpi 反推：像素宽 ≈ round(w_in × dpi) ±2% ----
    dpi_rev_ok = dpi_rev_bad = dpi_unknown = 0
    for p in pngs:
        info = png_info(p)
        if not info.get("ok") or not info.get("size"):
            dpi_unknown += 1
            warnings.append("%s 像素尺寸读不出（%s）" % (p.name, info.get("reason")))
            continue
        got = float(info["size"][0])
        if man_dpi and w_in:
            expect = w_in * man_dpi
            if expect > 0 and abs(got - expect) / expect <= 0.02:
                dpi_rev_ok += 1
            else:
                dpi_rev_bad += 1
                failures.append("%s 像素宽 %d ≠ round(%s×%.0f)=%.0f（偏差>2%%）"
                                % (p.name, got, w_in, man_dpi, expect))
        elif info.get("dpi"):
            # 无 manifest dpi 时退化为 PNG 自带 pHYs 自洽性检查
            dpi_rev_ok += 1
            notes.append("%s 用 PNG pHYs dpi=%s（manifest 缺 dpi/slide_size_in）" % (p.name, info["dpi"]))
        else:
            dpi_unknown += 1
            warnings.append("%s 无法核验 dpi：manifest dpi=%s, slide_size_in.w=%s, PNG pHYs 缺失"
                            % (p.name, man_dpi, w_in))

    # ---- (4) sha256 真算比对：pages[] / files{} / pdf_sha256 ----
    def _resolve_rel(raw):
        cands = []
        p = Path(str(raw))
        if p.is_absolute():
            cands.append(p)
        else:
            cands += [ctx.out_dir / p, ctx.out_dir.parent / p, preview_dir / p.name, p]
        for c in cands:
            if c.is_file():
                return c
        return None

    sha_verified = sha_mismatch = sha_unresolved = 0
    for it in (man.get("pages") or []) if isinstance(man, dict) else []:
        raw = it.get("path") or it.get("file")
        want = it.get("sha256")
        if not raw or not want:
            continue
        f = _resolve_rel(raw)
        if f is None:
            sha_unresolved += 1
            failures.append("manifest.pages[%s] 文件不存在: %s" % (it.get("page"), raw))
        elif _sha256_file(f) == str(want):
            sha_verified += 1
        else:
            sha_mismatch += 1
            failures.append("manifest.pages[%s] sha256 不符: %s" % (it.get("page"), Path(str(raw)).name))
    files_entry = (man or {}).get("files") if isinstance(man, dict) else None
    if isinstance(files_entry, dict):
        for name, want in files_entry.items():
            if not isinstance(want, str):
                want = (want or {}).get("sha256") if isinstance(want, dict) else None
            if not want:
                continue
            f = _resolve_rel(name)
            if f is None:
                sha_unresolved += 1
                failures.append("manifest.files[%s] 文件不存在" % name)
            elif _sha256_file(f) == want:
                sha_verified += 1
            else:
                sha_mismatch += 1
                failures.append("manifest.files[%s] sha256 不符" % name)
    elif isinstance(files_entry, list):
        for it in files_entry:
            if not isinstance(it, dict):
                continue
            raw, want = it.get("path") or it.get("file"), it.get("sha256")
            if not raw or not want:
                continue
            f = _resolve_rel(raw)
            if f is None:
                sha_unresolved += 1
                failures.append("manifest.files 条目文件不存在: %s" % raw)
            elif _sha256_file(f) == want:
                sha_verified += 1
            else:
                sha_mismatch += 1
                failures.append("manifest.files sha256 不符: %s" % Path(str(raw)).name)
    pdf_sha_want = (man or {}).get("pdf_sha256") if isinstance(man, dict) else None
    if pdf_sha_want and pdf.is_file():
        if _sha256_file(pdf) == str(pdf_sha_want):
            sha_verified += 1
        else:
            sha_mismatch += 1
            failures.append("manifest.pdf_sha256 与 deck.pdf 不符")
    elif pdf_sha_want and not pdf.is_file():
        sha_unresolved += 1
    if sha_verified == 0 and not unavailable:
        if sha_mismatch or sha_unresolved:
            failures.append("render_manifest sha256 一项都没核验成功（核验 0 / 不符 %d / 定位失败 %d）"
                            % (sha_mismatch, sha_unresolved))
        else:
            warnings.append("render_manifest 无 pages[].sha256 / files{} / pdf_sha256 可核验项"
                            "（keys=%s）" % ",".join(sorted(map(str, (man or {}).keys()))[:10]))

    mf = (man or {}).get("missingFonts")
    if mf:
        warnings.append("渲染缺字体 %d 个: %s" % (len(mf), ",".join(map(str, mf[:5]))))

    detail = ("deck.pdf %s 页 (pypdf=%s, raw/Count=%s, method=%s, bytes=%d, %%PDF-=%s); "
              "deck.pptx %s 页; preview PNG %d (preview_dir=%s); manifest pageCount=%s dpi=%s "
              "slide_size_in.w=%s missingFonts=%s; 页数四方一致=%s; "
              "dpi 反推 一致 %d / 不符 %d / 无法核验 %d; sha256 真算核验 %d / 不符 %d / 定位失败 %d; "
              "渲染器不可用=%s"
              % (pdf_n, pdfinfo.get("pypdf"), pdfinfo.get("raw_count"), pdfinfo.get("method"),
                 pdfinfo.get("bytes"), pdfinfo.get("is_pdf"), pptx_pages,
                 len(pngs), preview_dir.name, man_pc, man_dpi, w_in,
                 len(mf) if isinstance(mf, list) else mf,
                 counts_consistent,
                 dpi_rev_ok, dpi_rev_bad, dpi_unknown, sha_verified, sha_mismatch, sha_unresolved,
                 unavailable))
    return _g("F", _status(failures, warnings), detail, failures, warnings, notes)


# --------------------------------------------------------------------------- #
# G — 确定性
# --------------------------------------------------------------------------- #
def gate_G(ctx: Ctx) -> dict:
    failures, warnings, notes = [], [], []
    decl = ((ctx.result.get("audit") or {}).get("determinism")) if ctx.result else None
    pair = None
    dj, err = _load_json(ctx.work_dir / "determinism.json")
    if isinstance(dj, dict):
        a = dj.get("build_a_sha256") or dj.get("sha_a") or dj.get("sha256_a")
        b = dj.get("build_b_sha256") or dj.get("sha_b") or dj.get("sha256_b")
        if a and b:
            pair = (str(a), str(b), "work/determinism.json")
    if pair is None:
        f1, f2 = ctx.work_dir / "deck_build1.pptx", ctx.work_dir / "deck_build2.pptx"
        if f1.is_file() and f2.is_file():
            pair = (_sha256_file(f1), _sha256_file(f2), "deck_build1/2.pptx")

    if pair is None:
        warnings.append("无第二份构建哈希（work/determinism.json 或 deck_build1/2.pptx），"
                        "确定性无法本地验证；result.audit.determinism=%r" % decl)
        detail = ("确定性无法验证: work/determinism.json 缺失, deck_build1/2.pptx 缺失, "
                  "result.audit.determinism=%r, deck.pptx sha256=%s(%d B)"
                  % (decl, _short_digest(ctx.out_dir / "deck.pptx"),
                     (ctx.out_dir / "deck.pptx").stat().st_size if (ctx.out_dir / "deck.pptx").is_file() else 0))
        return _g("G", _status(failures, warnings), detail, failures, warnings, notes)

    a, b, src = pair
    # V04：声明的哈希必须与**实算**交叉核验——否则两个自造同值哈希就能骗过"确定性"。
    deck = ctx.out_dir / "deck.pptx"
    actual = _sha256_file(deck) if deck.is_file() else None
    extra = ctx.work_dir / "_determinism" / "deck.pptx"
    actual2 = _sha256_file(extra) if extra.is_file() else None
    declared_ok = (a == b)
    if not declared_ok:
        failures.append("两次构建 deck.pptx sha256 不同: %s ≠ %s (%s)" % (a[:12], b[:12], src))
    if actual is None:
        warnings.append("out_dir/deck.pptx 不存在，声明哈希无法与实算比对")
    elif a != actual:
        failures.append("声明哈希与实算不符（疑似伪造/过期）: 声明 %s vs deck.pptx 实算 %s (source=%s)"
                        % (a[:12], actual[:12], src))
    elif a == b == actual:
        notes.append("声明 == 实算（%s），确定性证据与交付件一致" % actual[:12])
    if actual2 is not None and actual is not None and actual2 != actual:
        failures.append("work/_determinism/deck.pptx 实算 %s ≠ 交付 deck.pptx %s"
                        % (actual2[:12], actual[:12]))
    if src == "deck_build1/2.pptx" and actual is not None and a == b and a != actual:
        # 文件对本身是真证据（内部一致），但没覆盖交付件 → 不能据此声称 byte-identical
        warnings.append("dek_build1/2 互相一致但均 ≠ 交付 deck.pptx（%s vs %s）→ 未覆盖交付件"
                        % (a[:12], actual[:12]))
    if decl == "byte-identical" and (failures or (actual is not None and a != actual)):
        failures.append("result.audit.determinism='byte-identical' 与实算不符")
    elif decl == "unknown" and declared_ok:
        warnings.append("result.audit.determinism=unknown 但存在相同哈希对（声明落后于证据）")
    detail = ("两次构建 sha256 %s vs %s (%s, source=%s, 声明互等=%s); "
              "deck.pptx 实算 %s; work/_determinism/deck.pptx 实算 %s; "
              "声明==实算 %s; result.audit.determinism=%r"
              % (a[:12], b[:12], "一致" if declared_ok else "不同", src, declared_ok,
                 actual[:12] if actual else "缺失", actual2[:12] if actual2 else "无",
                 bool(actual and a == actual), decl))
    return _g("G", _status(failures, warnings), detail, failures, warnings, notes)


# --------------------------------------------------------------------------- #
# H — 契约完整
# --------------------------------------------------------------------------- #
def gate_H(ctx: Ctx) -> dict:
    failures, warnings = [], []
    res = ctx.result
    contract = (ctx.case or {}).get("output_contract")
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        keys = [str(k) for k in contract]
    else:
        keys = []
    if not keys:
        warnings.append("case.json 缺 output_contract（case_path=%s）→ 契约键集合无法核验"
                        % (ctx.case_path or "未找到"))
    empty, missing = [], []
    for k in keys:
        if k not in res:
            missing.append(k)
        else:
            v = res.get(k)
            # V08：只填空白（"   "）不算非空
            if v is None or (isinstance(v, (str, list, dict)) and len(v) == 0) \
                    or (isinstance(v, str) and not v.strip()):
                empty.append(k)
    if missing:
        failures.append("result.json 缺 output_contract 键: %s" % ",".join(missing))
    if empty:
        failures.append("result.json output_contract 空值键: %s" % ",".join(empty))

    arts = res.get("artifacts") or {}
    art_exist = art_bytes_ok = art_sha_ok = 0
    via = {"out_dir": 0, "out_dir.parent": 0, "as-is": 0}
    items = arts.items() if isinstance(arts, dict) else list(enumerate(arts))
    for name, meta in items:
        if not isinstance(meta, dict):
            meta = {"path": str(meta)}
        # CONTRACT §3.6: artifacts[].path 是**相对 out_dir**；pt.py 同时写 rel
        raw = meta.get("path") or meta.get("rel") or meta.get("file") or ""
        p = Path(str(raw))
        tried = []
        if p.is_absolute():
            cands = [("as-is", p)]
        else:
            cands = [("out_dir", ctx.out_dir / p),
                     ("out_dir.parent", ctx.out_dir.parent / p),
                     ("as-is", p)]
        hit = hit_via = None
        for tag, c in cands:
            tried.append(str(c))
            if c.is_file():
                hit, hit_via = c, tag
                break
        if hit is None:
            failures.append("artifacts[%s] 三种解析都不存在: %s" % (name, " | ".join(tried)))
            continue
        via[hit_via] = via.get(hit_via, 0) + 1
        art_exist += 1
        size = hit.stat().st_size
        if meta.get("bytes") is None or int(meta.get("bytes")) == size:
            art_bytes_ok += 1
        else:
            failures.append("artifacts[%s] bytes %s ≠ 实际 %d" % (name, meta.get("bytes"), size))
        want = meta.get("sha256")
        if want and _sha256_file(hit) == want:
            art_sha_ok += 1
        elif want:
            failures.append("artifacts[%s] sha256 不符: %s ≠ %s" % (name, want[:12], _sha256_file(hit)[:12]))

    unmapped = (res.get("audit") or {}).get("unmapped_contract_keys") or []
    if not isinstance(arts, dict) or not arts:
        warnings.append("result.json artifacts 为空（无法核验产物登记）")
    detail = ("output_contract %d 键: 非空 %d, 缺失 %d, 空值 %d (case=%s); artifacts %d 项: 存在 %d "
              "(out_dir 直解 %d, out_dir.parent %d, 原样 %d), bytes 相符 %d, sha256 相符 %d; "
              "audit.unmapped_contract_keys %d"
              % (len(keys), len(keys) - len(missing) - len(empty), len(missing), len(empty),
                 ctx.case_path or "未找到", len(arts) if isinstance(arts, dict) else len(arts),
                 art_exist, via["out_dir"], via["out_dir.parent"], via["as-is"],
                 art_bytes_ok, art_sha_ok, len(unmapped)))
    return _g("H", _status(failures, warnings), detail, failures, warnings)


# --------------------------------------------------------------------------- #
# I — 问答准备
# --------------------------------------------------------------------------- #
def gate_I(ctx: Ctx) -> dict:
    failures, warnings = [], []
    qa = ctx.spec.get("qa") or ctx.result.get("qa") or []
    byid = ctx.evidence_by_id()
    page_ids = {str(p.get("id")) for p in ctx.pages}
    if not isinstance(qa, list):
        qa = []
    if len(qa) < 3:
        failures.append("qa 条数 %d < 3" % len(qa))
    dq_slide = dq_ev = high_no_ans = shell = 0
    # V09：空壳问答阈值。**答案**要求 ≥10 字（"N/A"/"见上"/"有" 这类必须拦）；
    # **问题**只要求 ≥5 字 —— 中文短问句（"重复性如何保证？" 8 字）是正常的，
    # 用 10 字会制造假阳性（实测：绿夹具因此被误杀）。这是对 Lead 字面口径的**收紧-放宽说明**。
    QA_ANSWER_MIN_CHARS = 10
    QA_QUESTION_MIN_CHARS = 5
    for q in qa:
        qid = q.get("id")
        if q.get("risk") == "high" and not _norm_ws(q.get("answer")):
            high_no_ans += 1
            failures.append("%s high 风险缺 answer" % qid)
        ans_n, q_n = _norm_ws(q.get("answer")), _norm_ws(q.get("question"))
        if len(ans_n) < QA_ANSWER_MIN_CHARS or len(q_n) < QA_QUESTION_MIN_CHARS:
            shell += 1
            failures.append("%s 疑似空壳（question %d 字 / answer %d 字，阈值 q≥%d a≥%d）: q=%r a=%r"
                            % (qid, len(q_n), len(ans_n), QA_QUESTION_MIN_CHARS,
                               QA_ANSWER_MIN_CHARS, q_n[:30], ans_n[:30]))
        for s in q.get("slide_ids") or []:
            if str(s) not in page_ids:
                dq_slide += 1
                failures.append("%s slide_ids 悬空: %s" % (qid, s))
        for e in q.get("evidence_ids") or []:
            if e not in byid:
                dq_ev += 1
                failures.append("%s evidence_ids 悬空: %s" % (qid, e))

    prep = ctx.out_dir / "qa-prep.md"
    covered = 0
    if not prep.is_file():
        failures.append("qa-prep.md 缺失")
        prep_bytes = 0
    else:
        prep_bytes = prep.stat().st_size
        txt = _norm_ws(prep.read_text(encoding="utf-8", errors="replace"))
        for q in qa:
            if _norm_ws(q.get("question")) and _norm_ws(q.get("question")) in txt:
                covered += 1
        if qa and covered < len(qa):
            warnings.append("qa-prep.md 仅收录 %d/%d 问题（其余可能被改写）" % (covered, len(qa)))
    detail = ("spec.qa %d 条 (阈值 ≥3); 引用页悬空 %d; 引用 evidence 悬空 %d; "
              "high 风险缺 answer %d; 空壳问答(q<%d 字 或 a<%d 字) %d; "
              "qa-prep.md %d B, 问题逐字收录 %d/%d; 引用页集合 %d"
              % (len(qa), dq_slide, dq_ev, high_no_ans, QA_QUESTION_MIN_CHARS,
                 QA_ANSWER_MIN_CHARS, shell,
                 prep_bytes, covered, len(qa), len(page_ids)))
    return _g("I", _status(failures, warnings), detail, failures, warnings)


# --------------------------------------------------------------------------- #
# J — 诚实性
# --------------------------------------------------------------------------- #
def gate_J(ctx: Ctx, prior_gates) -> dict:
    # 只留 {id,status} 摘要：绝不能把 gates 实名对象挂进本 gate 的字段，
    # 否则 run_gates 把本 gate append 回同一 list 后会形成自引用（json.dumps 报 Circular reference）。
    prior = [{"id": g.get("id"), "status": g.get("status")} for g in (prior_gates or [])]
    failures, warnings, notes = [], [], []
    res = ctx.result
    status = res.get("status")
    n_fail = sum(1 for g in prior if g["status"] == "fail")
    if n_fail and status == "ok":
        failures.append("有 %d 条闸门 fail 但 result.status=ok" % n_fail)
    if n_fail and status is None:
        failures.append("有 %d 条闸门 fail 但 result.status 缺失" % n_fail)

    spec_unc = ctx.spec.get("uncertainty") or []
    res_unc = res.get("uncertainty") or []
    if spec_unc and not res_unc:
        failures.append("spec 有 uncertainty %d 条但 result.json 未写入" % len(spec_unc))
    spec_lim = ctx.spec.get("limitations") or []
    res_lim = res.get("limitations") or []
    if spec_lim and not res_lim:
        failures.append("spec 有 limitations %d 条但 result.json 未写入" % len(spec_lim))

    unparsed = [str(f.get("path")) for f in (ctx.materials.get("files") or [])
                if f.get("parsed") is False]
    declared_txt = _norm_ws(" ".join([json.dumps(res_unc, ensure_ascii=False),
                                      json.dumps(res_lim, ensure_ascii=False)]))
    undeclared = []
    for p in unparsed:
        base = Path(p).name
        if base not in declared_txt and p not in declared_txt:
            undeclared.append(p)
    if undeclared:
        failures.append("材料 parsed=false 未在 uncertainty/limitations 声明: %s" % ",".join(undeclared))

    # CONTRACT §5 gate A/J：无 caption 的 figure evidence 必须在 result.uncertainty 里有对应条目
    uncap = [e for e in ctx.evidence
             if e.get("kind") == "figure" and not _norm_ws((e.get("source") or {}).get("quote"))
             and e.get("value") is None]
    uncap_ok, uncap_missing = 0, []
    for e in uncap:
        eid = str(e.get("id"))
        loc = str((e.get("source") or {}).get("locator") or "")
        base = Path(str((e.get("source") or {}).get("file") or "")).name
        blob = _norm_ws(json.dumps(res_unc, ensure_ascii=False)).lower()
        hit = (eid.lower() in blob) or (loc and loc.lower() in blob) \
            or (base and base.lower() in blob) or ("caption" in blob) or ("图注" in blob)
        if hit:
            uncap_ok += 1
        else:
            uncap_missing.append(eid)
    if uncap_missing:
        failures.append("无 caption 的 figure evidence 未在 result.uncertainty 声明: %s"
                        % ",".join(uncap_missing))

    # V11：页图注在材料里**根本没有可比 caption**（E 只能 warn）→ 必须进 uncertainty（诚实）
    no_src_cap, no_src_cap_ok = [], 0
    mat_caps = {}
    for f in (ctx.materials or {}).get("files") or []:
        for it in (f.get("figures") or []):
            for key in (it.get("id"), it.get("locator"),
                        Path(str(it.get("media_path") or "")).name):
                if key:
                    mat_caps[str(key)] = _norm_ws(it.get("caption"))
    for p in ctx.pages:
        fig = p.get("figure") or {}
        if not _norm_ws(fig.get("caption")):
            continue
        feid = str(fig.get("evidence_id") or "")
        e = ctx.evidence_by_id().get(feid) or {}
        loc = str((e.get("source") or {}).get("locator") or "")
        cands = [mat_caps.get(feid), mat_caps.get(loc),
                 mat_caps.get(Path(str(fig.get("media_path") or "")).name)]
        if any(c for c in cands if c):
            continue                                    # 材料里有 caption → E 已负责比对
        blob_l = _norm_ws(json.dumps(res_unc, ensure_ascii=False)).lower()
        pid = str(p.get("id"))
        if (pid.lower() in blob_l) or (feid and feid.lower() in blob_l) \
                or ("caption" in blob_l) or ("图注" in blob_l):
            no_src_cap_ok += 1
        else:
            no_src_cap.append(pid)
    if no_src_cap:
        failures.append("页图注在材料中无 caption 可比且未在 result.uncertainty 声明: %s"
                        % ",".join(no_src_cap))

    if status == "partial" and n_fail == 0 and not any(g["status"] == "warn" for g in prior):
        # 把 status 从 ok 收紧为 partial 是**诚实**行为，不是缺陷 → 只记 note，绝不让 J 变 warn
        # （否则 J 与 pt.py 的 status 写回互为输入，形成 ping-pong 不动点）
        notes.append("status=partial 但前置闸门 0 fail / 0 warn：这是保守声明，不是缺陷")
    detail = ("闸门 fail %d 个 vs result.status=%r; spec uncertainty %d → result 收录 %d; "
              "limitations %d → %d; materials parsed=false %d 个 → 已声明 %d; "
              "无 caption 的 figure evidence %d 个 → uncertainty 已声明 %d; "
              "图注无材料可比 %d 页 → uncertainty 已声明 %d; 前置闸门状态=%s"
              % (n_fail, status, len(spec_unc), len(res_unc), len(spec_lim), len(res_lim),
                 len(unparsed), len(unparsed) - len(undeclared),
                 len(uncap), uncap_ok, len(no_src_cap) + no_src_cap_ok, no_src_cap_ok,
                 ",".join("%s:%s" % (g["id"], g["status"][:4]) for g in prior)))
    return _g("J", _status(failures, warnings), detail, failures, warnings, prior + notes)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def missing_artifacts(out_dir, spec_path, work_dir, materials=None) -> list:
    out_dir, work_dir = Path(out_dir), Path(work_dir)
    miss = [str(out_dir / n) for n in REQUIRED_OUT_ARTIFACTS if not (out_dir / n).is_file()]
    if not Path(spec_path).is_file():
        miss.append(str(spec_path))
    if materials is None:
        miss += [str(work_dir / n) for n in REQUIRED_WORK_ARTIFACTS if not (work_dir / n).is_file()]
    else:
        miss += [str(work_dir / "template-index.json")] if not (work_dir / "template-index.json").is_file() else []
    man, _ = _load_json(out_dir / "render_manifest.json")
    if not _renderer_unavailable(man):
        if not (out_dir / "deck.pdf").is_file():
            miss.append(str(out_dir / "deck.pdf"))
        if not (out_dir / "preview").is_dir() or not list((out_dir / "preview").glob("*.png")):
            miss.append(str(out_dir / "preview" / "*.png"))
    return miss


def run_gates(out_dir, spec_path, work_dir, *, materials=None) -> dict:
    """实现 CONTRACT §5 闸门 A–J；写 out_dir/validation_report.json。"""
    out_dir, work_dir = Path(out_dir), Path(work_dir)
    ctx = Ctx(out_dir, spec_path, work_dir, materials)
    miss = missing_artifacts(out_dir, spec_path, work_dir, materials)
    if miss:
        report = {"schema_version": SCHEMA_VERSION,
                  "exit": 3, "status": "missing-artifacts",
                  "missing": miss, "gates": [],
                  "status_suggestion": "failed",
                  "consumed": {}}
        try:
            (out_dir / "validation_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:                                           # noqa: BLE001
            pass
        return {"gates": [], "exit": 3, "missing": miss, "report": report}

    gates = [gate_A(ctx), gate_B(ctx), gate_C(ctx), gate_D(ctx), gate_E(ctx),
             gate_F(ctx)]
    gates.append(gate_G(ctx))
    gates.append(gate_H(ctx))
    gates.append(gate_I(ctx))
    gates.append(gate_J(ctx, gates))

    n_fail = sum(1 for g in gates if g["status"] == "fail")
    n_warn = sum(1 for g in gates if g["status"] == "warn")
    n_pass = sum(1 for g in gates if g["status"] == "pass")
    status_suggestion = "partial" if n_warn else "ok"
    if n_fail:
        status_suggestion = "failed"
    exit_code = 1 if n_fail else 0

    # JSON 安全闸：gates 必须是纯可序列化结构且无环（pt.py 会 json.dumps 返回值）。
    json_clean = True
    try:
        json.dumps(gates, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        json_clean = False
        gates = [{"id": str(g.get("id")), "status": str(g.get("status")),
                  "detail": str(g.get("detail")),
                  "failures": [str(x) for x in (g.get("failures") or [])],
                  "warnings": [str(x) for x in (g.get("warnings") or [])],
                  "notes": [str(x) for x in (g.get("notes") or [])],
                  "json_repaired": "%s: %s" % (type(exc).__name__, exc)} for g in gates]
    report = {
        "schema_version": SCHEMA_VERSION,
        "exit": exit_code,
        "checks_passed": n_pass, "checks_failed": n_fail, "checks_warned": n_warn,
        "status_suggestion": status_suggestion,
        "json_clean": json_clean,
        "warnings": [{"gate": g["id"], "warning": w} for g in gates for w in g["warnings"]],
        "gates": gates,
        "missing": [],
        "consumed": ctx._consumed,
        "evidence_resolver": ctx.ev_source,
    }
    try:
        (out_dir / "validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:                                        # noqa: BLE001
        report["report_write_error"] = "%s: %s" % (type(exc).__name__, exc)
    return {"gates": gates, "exit": exit_code, "report": report}


def expected_status(gates) -> str:
    """warn → partial（CONTRACT §5 最后一行）。供 pt.py 复用。"""
    if any(g.get("status") == "fail" for g in gates):
        return "failed"
    if any(g.get("status") == "warn" for g in gates):
        return "partial"
    return "ok"


def apply_status_suggestion(out_dir, gates=None) -> bool:
    """把 warn 落成 ``result.json.status=partial`` 并追加 uncertainty（**双向且幂等**）。

    - 有 warn 且 status=ok → 降为 partial，并补 ``U-GATE-<id>`` uncertainty；
    - 0 fail / 0 warn 且 status=partial（只回退由闸门引起的 partial）→ 恢复为 ok，并清掉
      ``U-GATE-*`` 条目。**绝不**动 ``failed`` / ``needs_human``——那两种不是闸门能判定的。
    """
    out_dir = Path(out_dir)
    res_path = out_dir / "result.json"
    res, err = _load_json(res_path)
    if res is None or not isinstance(res, dict):
        return False
    if gates is None:
        rep, _ = _load_json(out_dir / "validation_report.json")
        gates = (rep or {}).get("gates") or []
    want = expected_status(gates)
    warns = [g for g in gates if g.get("status") == "warn"]
    changed = False
    if want == "partial" and res.get("status") == "ok":
        res["status"] = "partial"
        changed = True
    elif want == "ok" and res.get("status") == "partial":
        res["status"] = "ok"
        changed = True
        unc = [u for u in (res.get("uncertainty") or [])
               if not (isinstance(u, dict) and str(u.get("id", "")).startswith("U-GATE-"))]
        if unc != (res.get("uncertainty") or []):
            res["uncertainty"] = unc
    if want == "partial":
        unc = res.setdefault("uncertainty", [])
        for g in warns:
            entry = {"id": "U-GATE-%s" % g["id"], "what": "闸门 %s 无法验证" % g["id"],
                     "why": g["detail"], "impact": "相应检查未执行，交付状态降级为 partial"}
            if not any(isinstance(u, dict) and u.get("id") == entry["id"] for u in unc):
                unc.append(entry)
                changed = True
    if changed:
        res_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="validate.py", description="CONTRACT §5 闸门 A–J")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="跑 A–J 闸门")
    c.add_argument("--out", required=True)
    c.add_argument("--spec", required=True)
    c.add_argument("--work", required=True)
    c.add_argument("--materials", default=None, help="可选 materials.json 路径")
    c.add_argument("--json", action="store_true", help="stdout 输出完整报告 JSON")
    c.add_argument("--apply-status", action="store_true", help="warn → result.json status=partial")
    c.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd != "check":
        ap.print_help()
        return 2
    try:
        mats = None
        if args.materials:
            mats, err = _load_json(Path(args.materials))
            if mats is None:
                print("materials 读不到: %s" % err, file=sys.stderr)
                return 2
        r = run_gates(args.out, args.spec, args.work, materials=mats)
    except Exception as exc:                                        # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("IO/内部错误: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r["report"], ensure_ascii=False, indent=2))
    elif not args.quiet:
        rep = r["report"]
        for g in r["gates"]:
            print("%-2s %-4s %s" % (g["id"], g["status"].upper(), g["detail"]))
        if r["exit"] == 3:
            print("产物缺失 exit=3: %s" % ", ".join(r.get("missing") or []))
        print("exit=%s pass=%s fail=%s warn=%s status_suggestion=%s"
              % (r["exit"], rep.get("checks_passed"), rep.get("checks_failed"),
                 rep.get("checks_warned"), rep.get("status_suggestion")))
    if args.apply_status:
        apply_status_suggestion(args.out, r["gates"])
    return r["exit"]


if __name__ == "__main__":
    raise SystemExit(cli())
