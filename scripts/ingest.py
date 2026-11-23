#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest.py — real material parsing + candidate evidence ledger.

Frozen interface: work/CONTRACT.md  §3.1 (materials.json), §3.2 (evidence-ledger.json)
and §4 (module API).

Design rules honoured here:

* **Real parsing only.**  The file *extension* selects the parser (format dispatch);
  the file *name* never decides what the content is.  PDF text comes from
  ``pdfplumber`` (fallback ``pypdf``), csv from the ``csv`` module, images from
  ``PIL``, ``.pptx`` from ``python-pptx``, md/txt/json read directly.
* **Page spans are exact char ranges into ``files[].text``.**  PDF/pptx pages are
  joined with a single ``\\n`` and ``#p.<n>`` resolves to the very slice that
  ``materials.json`` stores, so E902 quote checks can never dangle.
* **A number is only a candidate when it really occurs in the body text**, and the
  evidence ``quote`` is the *verbatim* enclosing sentence (or csv/json record).
  Numbers are extracted as candidates only if the quote that carries them is a
  real slice of the stored text, so E903 cannot fire on our own ledger.
* **Honest degradation.**  One bad file never kills the run: it becomes
  ``parsed: false`` + ``parse_note``, everything else still lands.
* **No import-time side effects**: safe to import from pt.py / validate.py.

``media_path`` convention (CONTRACT §3.1, clarified 2026-11-23 by the Lead):
``files[].figures[].media_path`` and the ledger's figure item ``media_path`` are
**absolute** paths, because that is the only unambiguous form: ``build.py``
accepts both, but ``validate.py`` gate E resolves the spec's ``media_path``
against ``work_dir`` / ``out_dir`` / the spec's directory only, so a relative
path cannot be byte-compared against ``media_sha256``.  ``media_rel`` keeps the
human-readable input-relative form (``"materials/fig1.png"``) and ``media_abs``
duplicates the absolute path for consumers that look for that key name.

CLI::

    python3 scripts/ingest.py ingest --input /input --work /app/work
    exit 0 ok  /  2 usage or IO
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import posixpath
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_NUMBER_ITEMS = 300
MAX_QUOTE_ITEMS = 150
MAX_RECORD_QUOTE_CHARS = 400
MAX_SENTENCE_CHARS = 400

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # reuse the frozen sentence splitter so ingest and evidence agree exactly
    from evidence import (  # noqa: E402
        basename_of, normalize_ws, sentence_at, split_sentences,
    )
except Exception:  # pragma: no cover - evidence.py must ship alongside ingest.py
    def basename_of(path) -> str:  # type: ignore[misc]
        return posixpath.basename(str(path or "").replace("\\", "/"))

    def normalize_ws(s) -> str:  # type: ignore[misc]
        return re.sub(r"\s+", " ", str(s or "")).strip()

    def split_sentences(text):  # type: ignore[misc]
        return [(0, len(text))] if text else []

    def sentence_at(text, index, sentences=None):  # type: ignore[misc]
        return (0, len(text))

__all__ = ["SCHEMA_VERSION", "ingest", "load_materials", "build_ledger", "cli"]

# --------------------------------------------------------------------------- #
# format dispatch tables — extension only, never the file name's meaning
# --------------------------------------------------------------------------- #
KIND_BY_EXT = {
    ".pdf": "pdf",
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text", ".text": "text", ".rst": "text", ".tex": "text", ".log": "text",
    ".yaml": "text", ".yml": "text",
    ".csv": "csv", ".tsv": "csv",
    ".json": "json", ".jsonl": "json",
    ".pptx": "pptx", ".potx": "pptx", ".ppt": "pptx",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".bmp": "image", ".webp": "image", ".tif": "image", ".tiff": "image",
    ".svg": "image",
}
MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".pdf")
TEXTY_KINDS = ("markdown", "text", "csv", "json")

_TOK_RE = re.compile(
    r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|[-+]?\d+\.\d+"
    r"|[-+]?\.\d+"
    r"|[-+]?\d+(?:[eE][-+]?\d+)?"
)
_MD_HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_FIG_CAP_RE = re.compile(r"^\s*(?:Figures?|Fig\.|图|圖)\s*\d+\s*[.:：、]?\s*.*$", re.I)
_TAB_CAP_RE = re.compile(r"^\s*(?:Tables?|Tab\.|表)\s*\d+\s*[.:：、]?\s*.*$", re.I)
# body-text caption line, numbered (used to bind a caption to an image file)
_BODY_FIG_RE = re.compile(
    r"^[ \t]*(?:Figure|Fig\.?|图|圖)[ \t]*(\d+)[ \t]*[.:：、]?[ \t]*(.*)$", re.I | re.M)
_BODY_TAB_RE = re.compile(
    r"^[ \t]*(?:Table|Tab\.?|表)[ \t]*(\d+)[ \t]*[.:：、]?[ \t]*(.*)$", re.I | re.M)
_PDF_SECTIONS = {
    "abstract", "introduction", "background", "related work", "methods", "method",
    "materials and methods", "materials & methods", "experimental", "experiments",
    "results", "result", "discussion", "conclusions", "conclusion", "limitations",
    "references", "acknowledgements", "acknowledgments", "supplementary", "appendix",
    "summary",
}
_UNITS = [
    "mg/kg", "mg/mL", "mg/ml", "g/L", "g/l", "ug/mL", "ng/mL", "mmol/L", "umol/L",
    "fold", "folds", "times", "hours", "hour", "weeks", "week", "wk", "days", "day",
    "months", "month", "years", "year", "yr", "min", "mins", "hrs", "hr", "ms",
    "mg", "ug", "\u00b5g", "kg", "ng", "pg", "ml", "mL", "\u00b5L", "uL", "nL", "km",
    "cm", "mm", "nm", "\u00b5m", "um", "kDa", "Da", "kPa", "MPa", "GPa", "kHz",
    "MHz", "GHz", "kcal", "kJ", "kW", "mV", "mA", "rpm", "mol", "mmol", "nmol",
    "pmol", "mM", "\u00b5M", "uM", "nM", "pM", "Pa", "Hz", "min", "s", "h", "d",
    "M", "m", "g", "L", "W", "V", "A", "J", "K", "cal",
]
_UNIT_RE = re.compile(
    r"^\s*[-\u2013\u2014\u2212]?\s*(?P<u>" +
    "|".join(re.escape(u) for u in sorted(set(_UNITS), key=len, reverse=True)) +
    r")(?![\w\u00b5\u4e00-\u9fff])"
)
_CJK_UNIT_RE = re.compile(r"^\s*(?P<u>\u500d|\u5c0f\u65f6|\u5206\u949f|\u79d2|\u5929|\u5468|\u4e2a\u6708|\u5e74|\u4f8b|\u540d|\u4eba|\u6b21)")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _save_json(path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_newlines(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _line_ranges(text: str):
    out = []
    start = 0
    n = len(text)
    while start <= n:
        idx = text.find("\n", start)
        if idx < 0:
            out.append((start, n))
            break
        out.append((start, idx))
        start = idx + 1
        if start == n:  # text ends with a newline: no phantom last line
            break
    return out


def _add_page_spans(entry: dict, base: str, page_texts) -> str:
    text = "\n".join(page_texts)
    entry["text"] = text
    spans = entry.setdefault("spans", [])
    off = 0
    for i, pt in enumerate(page_texts, 1):
        spans.append({"locator": f"{base}#p.{i}", "kind": "page", "page": i,
                      "chars": [off, off + len(pt)]})
        off += len(pt) + 1
    spans.append({"locator": f"{base}#all", "kind": "all", "page": None,
                  "chars": [0, len(text)]})
    return text


def _add_text_spans(entry: dict, base: str, text: str) -> None:
    entry["text"] = text
    spans = entry.setdefault("spans", [])
    for i, (a, b) in enumerate(_line_ranges(text), 1):
        spans.append({"locator": f"{base}#L{i}-L{i}", "kind": "line", "line": i,
                      "chars": [a, b]})
    spans.append({"locator": f"{base}#all", "kind": "all", "page": None,
                  "chars": [0, len(text)]})


def _add_record_spans(entry: dict, base: str, ranges) -> None:
    spans = entry.setdefault("spans", [])
    for i, rng in enumerate(ranges, 1):
        a, b = rng[0], rng[1]
        span = {"locator": f"{base}#r.{i}", "kind": "record", "record": i,
                "chars": [a, b], "page": None}
        if len(rng) > 2 and rng[2]:
            span["key"] = rng[2]
        spans.append(span)


def _add_figure_span(entry: dict, fig: dict) -> None:
    entry.setdefault("spans", []).append({
        "locator": fig["locator"], "kind": "figure", "page": fig.get("page"),
        "chars": list(fig.get("chars") or [0, 0]),
    })


def _add_table_span(entry: dict, tab: dict) -> None:
    entry.setdefault("spans", []).append({
        "locator": tab["locator"], "kind": "table", "page": tab.get("page"),
        "chars": list(tab.get("chars") or [0, 0]),
    })


def _locate(text: str, needle, offset: int = 0):
    """Char range of ``needle`` inside ``text`` (shifted by ``offset``), or [0, 0]."""
    if not needle:
        return [0, 0]
    idx = text.find(needle)
    if idx < 0:
        return [0, 0]
    return [offset + idx, offset + idx + len(needle)]


def _unit_after(text: str, end: int):
    tail = text[end:end + 24]
    if re.match(r"^\s*[%\uff05]", tail):
        return "%"
    m = _UNIT_RE.match(tail)
    if m:
        return m.group("u")
    m = _CJK_UNIT_RE.match(tail)
    if m:
        return m.group("u")
    if re.match(r"^\s*[\u00b0]", tail):
        m2 = re.match(r"^\s*[\u00b0C\uff03]*", tail)
        return m2.group().strip() or "\u00b0"
    return None


def _unit_before(text: str, start: int):
    head = text[max(0, start - 24):start]
    m = re.search(r"(?P<u>fold|folds|\u500d)\s*[-\u2013\u2014\u2212]?\s*$", head, re.I)
    if m:
        return m.group("u")
    return None


# --------------------------------------------------------------------------- #
# per-format parsers
# --------------------------------------------------------------------------- #
def _parse_texty(entry: dict, data: bytes, base: str, notes) -> None:
    text = data.decode("utf-8", errors="replace")
    if "\ufffd" in text:
        entry["parse_note"] = "decoded as utf-8 with replacement characters"
    entry["parsed"] = True
    _add_text_spans(entry, base, _norm_newlines(text))


def _csv_record_ranges(text: str):
    """RFC4180-aware record char ranges (quoted fields may contain newlines)."""
    ranges = []
    start = 0
    inq = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            if inq and i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            inq = not inq
        elif c == "\n" and not inq:
            ranges.append((start, i))
            start = i + 1
        i += 1
    if start < n:
        ranges.append((start, n))
    return ranges


def _parse_csv(entry: dict, data: bytes, base: str, notes) -> None:
    text = _norm_newlines(data.decode("utf-8", errors="replace"))
    entry["parsed"] = True
    entry["text"] = text
    _add_record_spans(entry, base, _csv_record_ranges(text))
    _add_text_spans(entry, base, text)
    rows = []
    try:
        rows = [list(r) for r in csv.reader(io.StringIO(text))]
    except Exception as exc:
        entry["parse_note"] = f"csv.reader failed: {type(exc).__name__}: {exc}"
    if not rows and text.strip():
        return
    header = rows[0] if rows else []
    header_line = text.split("\n", 1)[0] if text else ""
    tab = {
        "id": "tab1", "page": None, "caption": header_line or None,
        "locator": f"{base}#tab.1",
        "rows": rows, "n_rows": len(rows),
        "n_cols": max((len(r) for r in rows), default=0),
        "header": header, "chars": [0, len(header_line)],
        "caption_source": "header-row",
    }
    entry["tables"].append(tab)
    _add_table_span(entry, tab)


def _json_children(text: str):
    """``(start, end, key)`` for every depth-1 child of the JSON root container."""
    out = []
    n = len(text)
    k = 0
    while k < n and text[k] not in "[{":
        k += 1
    if k >= n:
        return out
    is_obj = text[k] == "{"
    depth = 0
    i = k
    start = None
    key = None
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            esc = False
            while j < n:
                ch = text[j]
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    break
                j += 1
            m = j + 1
            while m < n and text[m] in " \t\r\n":
                m += 1
            if is_obj and depth == 1 and m < n and text[m] == ":":
                key = text[i + 1:j]
                i = m + 1
                while i < n and text[i] in " \t\r\n":
                    i += 1
                if i < n and text[i] in ",}]":
                    out.append((i, i, key))
                    key = None
                    continue
                start = i
                continue
            if depth == 1 and start is None and not is_obj:
                start = i
            i = j + 1
            continue
        if c in "[{":
            depth += 1
            if depth == 2 and start is None:
                start = i
            i += 1
            continue
        if c in "]}":
            depth -= 1
            if depth == 1:
                if start is not None:
                    e = i
                    while e > start and text[e - 1] in " \t\r\n":
                        e -= 1
                    out.append((start, e, key))
                    start = None
                    key = None
            elif depth == 0:
                if start is not None:
                    out.append((start, i, key))
                break
            i += 1
            continue
        if c == "," and depth == 1:
            if start is not None:
                e = i
                while e > start and text[e - 1] in " \t\r\n":
                    e -= 1
                out.append((start, e, key))
                start = None
                key = None
            i += 1
            continue
        if depth == 1 and start is None and c not in " \t\r\n:":
            start = i
        i += 1
    return out


def _parse_json(entry: dict, data: bytes, base: str, notes) -> None:
    obj = json.loads(data.decode("utf-8-sig", errors="replace"))
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    entry["parsed"] = True
    _add_record_spans(entry, base, _json_children(text))
    _add_text_spans(entry, base, text)
    entry["json_root"] = type(obj).__name__


def _parse_image(entry: dict, path: Path, data: bytes, base: str, notes) -> None:
    from PIL import Image

    entry["parsed"] = True
    entry["text"] = ""
    entry["spans"] = [{"locator": f"{base}#all", "kind": "all", "page": None, "chars": [0, 0]}]
    width = height = None
    dpi = None
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            dpi = _dpi_from_image(img)
    except Exception:
        try:  # SVG and friends: at least a real parse attempt through PIL
            with Image.open(str(path)) as img:
                width, height = img.size
                dpi = _dpi_from_image(img)
        except Exception as exc:
            entry["parsed"] = False
            entry["parse_note"] = f"PIL cannot open image: {type(exc).__name__}: {exc}"
    fig = {
        "id": "fig1", "kind": "figure", "page": None, "caption": None,
        "locator": f"{base}#fig.1", "media_path": entry["abs"],
        "media_rel": entry["path"], "media_abs": entry["abs"],
        "media_sha256": entry["sha256"], "width": width, "height": height,
        "dpi_estimate": dpi, "chars": [0, 0], "caption_source": None,
    }
    entry["figures"].append(fig)
    _add_figure_span(entry, fig)


def _dpi_from_image(img):
    try:
        info = getattr(img, "info", {}) or {}
        d = info.get("dpi")
        if isinstance(d, (tuple, list)) and d:
            v = float(d[0])
            if 1.0 <= v <= 2400.0:
                return round(v, 2)
        elif isinstance(d, (int, float)) and 1.0 <= float(d) <= 2400.0:
            return round(float(d), 2)
    except Exception:
        pass
    return 96.0


def _pil_mode_from_colorspace(cs):
    if isinstance(cs, str):
        name = cs.strip("/'\"")
        if "Gray" in name:
            return "L", None
        if "RGB" in name:
            return "RGB", None
        if "CMYK" in name:
            return "CMYK", None
        return None, None
    if isinstance(cs, (list, tuple)) and cs:
        head = str(cs[0]).strip("/'\"")
        if head == "Indexed" and len(cs) >= 4:
            try:
                hival = int(cs[2])
            except Exception:
                hival = 255
            base_name = str(cs[1]).strip("/'\"")
            lookup = cs[3]
            if not isinstance(lookup, (bytes, bytearray)):
                return "P", None
            lut = bytes(lookup)
            ncomp = 3 if "RGB" in base_name else (4 if "CMYK" in base_name else 1)
            if ncomp == 1:
                pal = bytearray()
                for v in lut[:hival + 1]:
                    pal += bytes((v, v, v))
                return "P", bytes(pal)
            if ncomp == 4:
                pal = bytearray()
                for i in range(0, min(len(lut), (hival + 1) * 4), 4):
                    c, m, y, kk = lut[i], lut[i + 1], lut[i + 2], lut[i + 3]
                    pal += bytes((255 - min(255, c + kk), 255 - min(255, m + kk),
                                  255 - min(255, y + kk)))
                return "P", bytes(pal)
            return "P", lut[:(hival + 1) * 3]
        if head == "ICCBased":
            n = None
            try:
                obj = cs[1]
                obj = obj.get_object() if hasattr(obj, "get_object") else obj
                n = int(obj.get("/N"))
            except Exception:
                n = None
            return {1: "L", 3: "RGB", 4: "CMYK"}.get(n or 3), None
        if head in ("CalRGB", "Lab"):
            return "RGB", None
        if head == "CalGray":
            return "L", None
    return None, None


def _extract_pdf_image(im: dict):
    """Decode one pdfplumber image object into a PIL image (or None)."""
    stream = im.get("stream")
    if stream is None:
        return None
    try:
        data = stream.get_data()
    except Exception:
        return None
    filt = stream.get("Filter")
    filters = filt if isinstance(filt, (list, tuple)) else [filt]
    filters = [str(f) for f in filters if f]
    size = im.get("srcsize")
    img = None
    if any(("DCTDecode" in f or "JPXDecode" in f) for f in filters):
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            img = None
    if img is None and size:
        try:
            bpc = int(stream.get("BitsPerComponent") or 8)
        except Exception:
            bpc = 8
        if bpc == 8:
            mode, palette = _pil_mode_from_colorspace(stream.get("ColorSpace"))
            if mode:
                from PIL import Image
                try:
                    img = Image.frombytes(mode, (int(size[0]), int(size[1])), data)
                    if palette:
                        img.putpalette(palette)
                except Exception:
                    img = None
    return img


def _parse_pdf(entry: dict, path: Path, work_dir: Path, base: str, notes) -> None:
    pages = []
    engine = None
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for pg in pdf.pages:
                try:
                    txt = pg.extract_text() or ""
                except Exception:
                    txt = ""
                try:
                    imgs = list(pg.images)
                except Exception:
                    imgs = []
                try:
                    tabs = pg.extract_tables() or []
                except Exception:
                    tabs = []
                pages.append({"text": txt, "images": imgs, "tables": tabs})
        engine = "pdfplumber"
    except Exception as exc:
        notes.append(f"{entry['path']}: pdfplumber failed "
                     f"({type(exc).__name__}: {exc}); falling back to pypdf")
        pages = []
    if not pages:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        for pg in reader.pages:
            try:
                txt = pg.extract_text() or ""
            except Exception:
                txt = ""
            pages.append({"text": txt, "images": [], "tables": []})
        engine = "pypdf"
    if not pages:
        entry["parsed"] = False
        entry["parse_note"] = "no pages could be read"
        return
    entry["parsed"] = True
    entry["pdf_engine"] = engine
    page_texts = [p["text"] for p in pages]
    text = _add_page_spans(entry, base, page_texts)
    page_start = [s["chars"][0] for s in entry["spans"] if s.get("kind") == "page"]

    fig_dir = work_dir / "figures"
    fig_n = 0
    tab_n = 0
    for pno, pg in enumerate(pages, 1):
        ptext = page_texts[pno - 1]
        off = page_start[pno - 1]
        fig_caps = _caption_lines(ptext, _FIG_CAP_RE)
        tab_caps = _caption_lines(ptext, _TAB_CAP_RE)
        imgs = pg["images"]
        for k, im in enumerate(imgs):
            fig_n += 1
            cap = fig_caps[k] if k < len(fig_caps) else None
            fig = {
                "id": f"fig{fig_n}", "kind": "figure", "page": pno, "caption": cap,
                "locator": f"{base}#fig.{fig_n}", "media_path": None,
                "media_rel": None, "media_abs": None,
                "media_sha256": None, "width": None, "height": None,
                "dpi_estimate": None, "chars": _locate(ptext, cap, off) if cap else [0, 0],
                "caption_source": "page-text" if cap else None,
            }
            src = im.get("srcsize")
            if src:
                fig["width"], fig["height"] = int(src[0]), int(src[1])
            disp_w = im.get("width")
            try:
                if src and disp_w:
                    dpi = float(src[0]) / (float(disp_w) / 72.0)
                    if 1.0 <= dpi <= 2400.0:
                        fig["dpi_estimate"] = round(dpi, 2)
            except Exception:
                pass
            media = _save_pdf_image(im, fig_dir, base, pno, k, notes)
            if media:
                fig.update(media)
            else:
                # a picture object exists on the page but no decodable bitmap:
                # it is a caption candidate, never a picture asset
                fig["kind"] = "figure-caption"
                fig["decode_note"] = ("image object present on the page but no decodable "
                                      "bitmap; offered as a caption candidate only")
            entry["figures"].append(fig)
            _add_figure_span(entry, fig)
        for k, cap in enumerate(fig_caps):
            if k < len(imgs):
                continue
            fig_n += 1
            fig = {
                "id": f"fig{fig_n}", "kind": "figure-caption", "page": pno, "caption": cap,
                "locator": f"{base}#fig.{fig_n}", "media_path": None,
                "media_rel": None, "media_abs": None,
                "media_sha256": None, "width": None, "height": None, "dpi_estimate": None,
                "chars": _locate(ptext, cap, off),
                "caption_source": "page-text",
            }
            entry["figures"].append(fig)
            _add_figure_span(entry, fig)
        tables = pg["tables"]
        for k, rows in enumerate(tables):
            tab_n += 1
            cap = tab_caps[k] if k < len(tab_caps) else None
            clean = [[("" if c is None else str(c)) for c in row] for row in rows]
            tab = {
                "id": f"tab{tab_n}", "page": pno, "caption": cap,
                "locator": f"{base}#tab.{tab_n}", "rows": clean, "n_rows": len(clean),
                "n_cols": max((len(r) for r in clean), default=0),
                "chars": _locate(ptext, cap, off) if cap else [0, 0],
            }
            entry["tables"].append(tab)
            _add_table_span(entry, tab)
        for k, cap in enumerate(tab_caps):
            if k < len(tables):
                continue
            tab_n += 1
            tab = {"id": f"tab{tab_n}", "page": pno, "caption": cap,
                   "locator": f"{base}#tab.{tab_n}", "rows": [], "n_rows": 0, "n_cols": 0,
                   "chars": _locate(ptext, cap, off),
                   "note": "caption detected but no ruled table structure"}
            entry["tables"].append(tab)
            _add_table_span(entry, tab)
    entry["text"] = text
    if not text.strip():
        entry["parse_note"] = (f"no extractable text layer (engine={engine}); "
                               f"the PDF may be a scan")


def _save_pdf_image(im, fig_dir: Path, base: str, pno: int, k: int, notes):
    img = _extract_pdf_image(im)
    if img is None:
        return None
    try:
        from PIL import Image
        fig_dir.mkdir(parents=True, exist_ok=True)
        out = fig_dir / f"{Path(base).stem}-p{pno}-{k + 1}.png"
        rgb = img.convert("RGB") if img.mode not in ("RGB", "L", "P") else img
        rgb.save(str(out), format="PNG")
        data = out.read_bytes()
        return {"media_path": out.resolve().as_posix(), "media_rel": None,
                "media_abs": str(out.resolve()),
                "media_sha256": _sha256_bytes(data),
                "width": int(img.width), "height": int(img.height)}
    except Exception as exc:
        notes.append(f"{base} page {pno} image {k + 1}: PNG write failed "
                     f"({type(exc).__name__}: {exc})")
        return None


def _caption_lines(page_text: str, pat):
    out = []
    for lm in re.finditer(r"[^\n]+", page_text):
        line = lm.group().strip()
        if line and pat.match(line):
            out.append(line)
    return out


def _parse_pptx(entry: dict, path: Path, base: str, notes) -> None:
    from pptx import Presentation

    prs = Presentation(str(path))
    slide_texts = []
    slides = []
    tab_n = 0
    for i, slide in enumerate(prs.slides, 1):
        layout = ""
        try:
            layout = slide.slide_layout.name or ""
        except Exception:
            layout = ""
        parts = []
        for shape in slide.shapes:
            try:
                if shape.has_text_frame:
                    t = shape.text_frame.text
                    if t and t.strip():
                        parts.append(t)
            except Exception:
                pass
            try:
                if getattr(shape, "has_table", False) and shape.has_table:
                    rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                    tab_n += 1
                    tab = {"id": f"tab{tab_n}", "page": i, "caption": None,
                           "locator": f"{base}#tab.{tab_n}", "rows": rows,
                           "n_rows": len(rows),
                           "n_cols": max((len(r) for r in rows), default=0),
                           "chars": [0, 0],
                           "caption_source": "pptx-table"}
                    entry["tables"].append(tab)
                    _add_table_span(entry, tab)
            except Exception:
                pass
        slide_texts.append("\n".join(parts))
        slides.append({"index": i, "layout": layout, "texts": parts})
    entry["parsed"] = True
    _add_page_spans(entry, base, slide_texts)
    entry["slides"] = slides
    if not entry["slides"]:
        entry["parse_note"] = "presentation has no slides"


# --------------------------------------------------------------------------- #
# discovery + parse driver
# --------------------------------------------------------------------------- #
def _discover(input_dir: Path):
    input_dir = Path(input_dir)
    found = []
    mat = input_dir / "materials"
    roots = [mat] if mat.is_dir() else [input_dir]
    for root in roots:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(input_dir)
            parts = rel.parts
            if any(part.startswith(".") or part == "__pycache__" for part in parts):
                continue
            if p.is_symlink():
                continue
            rel_posix = rel.as_posix()
            if mat.is_dir() and (input_dir / rel).name == "case.json" and len(parts) == 1:
                continue
            found.append(rel_posix)
    task_md = input_dir / "task.md"
    if task_md.is_file() and not task_md.is_symlink():
        rel = task_md.relative_to(input_dir).as_posix()
        if rel not in found:
            found.append(rel)
    return sorted(set(found))


def _parse_one(input_dir: Path, rel: str, work_dir: Path, notes) -> dict:
    p = input_dir / rel
    base = basename_of(rel)
    try:
        data = p.read_bytes()
    except Exception as exc:
        return {"path": rel, "abs": str(p), "kind": "other", "sha256": None, "bytes": 0,
                "parsed": False, "parse_note": f"unreadable: {type(exc).__name__}: {exc}",
                "text": "", "spans": [], "figures": [], "tables": []}
    entry = {
        "path": rel, "abs": str(p.resolve() if p.exists() else p), "kind": "other",
        "sha256": _sha256_bytes(data), "bytes": len(data), "parsed": False,
        "parse_note": None, "text": "", "spans": [], "figures": [], "tables": [],
    }
    kind = KIND_BY_EXT.get(p.suffix.lower(), "other")
    entry["kind"] = kind
    try:
        if kind == "pdf":
            _parse_pdf(entry, p, work_dir, base, notes)
        elif kind in ("markdown", "text"):
            _parse_texty(entry, data, base, notes)
        elif kind == "csv":
            _parse_csv(entry, data, base, notes)
        elif kind == "json":
            _parse_json(entry, data, base, notes)
        elif kind == "image":
            _parse_image(entry, p, data, base, notes)
        elif kind == "pptx":
            _parse_pptx(entry, p, base, notes)
        else:
            if len(data) > MAX_TEXT_BYTES:
                entry["parse_note"] = f"unparsed binary, {len(data)} bytes"
            elif b"\x00" in data[:4096]:
                entry["parse_note"] = "unparsed binary content (no NUL-free text layer)"
            else:
                _parse_texty(entry, data, base, notes)
                entry["kind"] = "text"
    except Exception as exc:
        entry["parsed"] = False
        entry["parse_note"] = f"{type(exc).__name__}: {exc}"
    if entry["parsed"] and not entry.get("spans"):
        entry["spans"] = [{"locator": f"{base}#all", "kind": "all", "page": None,
                           "chars": [0, len(entry.get("text") or "")]}]
    return entry


# --------------------------------------------------------------------------- #
# caption sidecars (data-driven, never filename-inferred)
# --------------------------------------------------------------------------- #
def _walk_json(obj, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_json(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:200]:
            yield from _walk_json(v, depth + 1)


def _media_caption_map(docs) -> dict:
    mp: dict = {}
    for doc in docs:
        for d in _walk_json(doc):
            if not isinstance(d, dict):
                continue
            target = None
            for key in ("file", "path", "image", "img", "name", "src", "figure", "media", "target"):
                v = d.get(key)
                if isinstance(v, str) and v.lower().endswith(MEDIA_EXTS):
                    target = v
                    break
            if not target:
                for v in d.values():
                    if isinstance(v, str) and v.lower().endswith(MEDIA_EXTS):
                        target = v
                        break
            if not target:
                continue
            for key in ("caption", "title", "label", "description", "alt", "text"):
                v = d.get(key)
                if isinstance(v, str) and v.strip():
                    mp.setdefault(basename_of(target).lower(), v.strip())
                    break
    return mp


def _harvest_body_captions(files) -> dict:
    """``figure number -> caption line`` read out of the parsed body text.

    This is how an image *file* gets a caption without inventing one: the text of
    the other materials is searched for ``Figure <n>[.:] <caption>`` lines and the
    caption is bound to the n-th image.  Nothing is inferred from the file name.
    """
    found: dict = {}
    for f in files:
        if not f.get("parsed"):
            continue
        text = f.get("text") or ""
        if not text:
            continue
        base = basename_of(f.get("path") or "")
        for m in _BODY_FIG_RE.finditer(text):
            num = int(m.group(1))
            if num in found:
                continue
            line = text[m.start():m.end()].strip()
            if not line or len(line) > 400:
                continue
            found[num] = {"caption": line, "file": f.get("path"),
                          "locator": _unit_locator(f, base, m.start(), m.end())}
    return found


def _bind_figure_captions(files, notes) -> None:
    """Give every image figure a caption from real text, or an honest empty one."""
    body = _harvest_body_captions(files)
    image_figs = []
    for f in files:
        if f.get("kind") != "image":
            continue
        for fig in f.get("figures") or []:
            image_figs.append((f, fig))
    unmatched = 0
    for k, (f, fig) in enumerate(image_figs, 1):
        if str(fig.get("caption") or "").strip():
            fig.setdefault("caption_source", None)
            continue
        hit = body.get(k)
        if hit:
            fig["caption"] = hit["caption"]
            fig["caption_source"] = f"body-text:{hit['file']}"
            fig["caption_locator"] = hit["locator"]
        else:
            fig["caption"] = ""
            fig["caption_source"] = "none"
            unmatched += 1
    # figures that are not images (pdf page images, pdf caption-only entries)
    for f in files:
        if f.get("kind") == "image":
            continue
        for fig in f.get("figures") or []:
            if str(fig.get("caption") or "").strip():
                fig.setdefault("caption_source", "page-text")
            else:
                fig["caption"] = ""
                fig["caption_source"] = "none"
                unmatched += 1
    if unmatched:
        notes.append(f"{unmatched} figure(s) have no caption anywhere in the materials; "
                     f"they stay in materials.json with caption=\"\" and are deliberately "
                     f"NOT offered as evidence (an empty quote would fail E902)")
    if image_figs and body:
        notes.append(f"body-text captions harvested: figure numbers {sorted(body)} "
                     f"bound to {len(image_figs)} image file(s) by ordinal position")


# --------------------------------------------------------------------------- #
# candidate evidence ledger (CONTRACT §3.2)
# --------------------------------------------------------------------------- #
def _span_covering(spans, a: int, b: int, kind: str):
    for s in spans:
        if s.get("kind") != kind:
            continue
        c = s.get("chars") or [0, 0]
        if c[0] <= a and b <= c[1]:
            return s
    return None


def _line_covering(spans, pos: int):
    for s in spans:
        if s.get("kind") != "line":
            continue
        c = s.get("chars") or [0, 0]
        if c[0] <= pos < c[1]:
            return s
    return None


def _unit_locator(f: dict, base: str, a: int, b: int) -> str:
    """Smallest locator whose span really CONTAINS ``[a, b)``.

    It never guesses: if no line / record / page span covers the whole range the
    answer is ``#all``, because a locator whose span is smaller than the quote is
    exactly the E902 hazard this module exists to prevent.
    """
    spans = f.get("spans") or []
    lines = [s for s in spans if s.get("kind") == "line"]
    lo = hi = None
    for s in lines:
        c = s.get("chars") or [0, 0]
        if c[0] <= a < c[1]:
            lo = s.get("line") if lo is None else lo
    for s in lines:
        c = s.get("chars") or [0, 0]
        if c[0] < b <= c[1] or (c[0] == c[1] == b):
            hi = s.get("line") if hi is None else hi
    if lo is not None and hi is not None:
        lo, hi = min(lo, hi), max(lo, hi)
        return f"{base}#L{lo}" if lo == hi else f"{base}#L{lo}-L{hi}"
    for kind, key in (("record", "record"), ("page", "page")):
        s = _span_covering(spans, a, b, kind)
        if s is not None:
            return f"{base}#r.{s.get(key)}" if kind == "record" else f"{base}#p.{s.get(key)}"
    return f"{base}#all"


def _number_quote(f: dict, base: str, text: str, sents, a: int, b: int):
    spans = f.get("spans") or []
    rec = _span_covering(spans, a, b, "record")
    if rec is not None:
        c = rec.get("chars") or [0, 0]
        body = text[c[0]:c[1]]
        # a csv row or a json scalar makes a good quote; a nested json container does not
        if c[1] - c[0] <= MAX_RECORD_QUOTE_CHARS and body.lstrip()[:1] not in ("[", "{"):
            return body, f"{base}#r.{rec.get('record')}"
    sa, sb = sentence_at(text, a, sents)
    if sb <= sa or sb - sa > MAX_SENTENCE_CHARS:
        sa, sb = a, b
    return text[sa:sb], _unit_locator(f, base, sa, sb)


def _sections_of(f: dict, base: str):
    out = []
    text = f.get("text") or ""
    kind = f.get("kind")
    if kind in ("markdown", "text"):
        for m in _MD_HEAD_RE.finditer(text):
            title = m.group(2).strip()
            if title:
                out.append((title, m.start(), m.end()))
    elif kind == "pdf":
        for sp in f.get("spans") or []:
            if sp.get("kind") != "page":
                continue
            c = sp.get("chars") or [0, 0]
            page_text = text[c[0]:c[1]]
            for lm in re.finditer(r"[^\n]+", page_text):
                line = lm.group().strip()
                if not line:
                    continue
                low = line.rstrip(":").strip().lower()
                # a numbered heading must start with a word, not with more digits
                numbered = re.match(r"^\d+(?:\.\d+)*[.)]?\s+(?=[A-Z][a-z]|[A-Z]{2,})"
                                    r"[A-Za-z][A-Za-z0-9 ,\-/()]*$", line)
                if low in _PDF_SECTIONS or numbered:
                    out.append((line, c[0] + lm.start(), c[0] + lm.start() + len(lm.group())))
    return out


_CLAIM_TOKENS = (
    "demonstrat", "show", "shows", "shown", "suggest", "indicat", "reveal", "observ",
    "found", "find", "conclud", "increas", "decreas", "reduc", "improv", "associat",
    "correlat", "significant", "we report", "we present", "we propose", "we test",
    "\u8868\u660e", "\u663e\u793a", "\u63ed\u793a", "\u8bf4\u660e", "\u63d0\u9ad8", "\u964d\u4f4e",
    "\u53d1\u73b0", "\u8ba4\u4e3a", "\u7ed3\u679c", "\u610f\u5473",
)


def _is_claim_sentence(s: str) -> bool:
    low = s.lower()
    return any(t in low for t in _CLAIM_TOKENS)


def build_ledger(materials: dict) -> dict:
    """Candidate evidence pool derived only from real parsed content (CONTRACT §3.2)."""
    items: list = []
    stats = {"numbers": 0, "figures": 0, "tables": 0, "sections": 0, "quotes": 0}
    n_numbers = 0
    n_quotes = 0
    for f in materials.get("files") or []:
        if not f.get("parsed"):
            continue
        base = basename_of(f.get("path") or "")
        text = f.get("text") or ""
        page_bounds = [s["chars"][1] + 1 for s in (f.get("spans") or [])
                       if s.get("kind") == "page"]
        sents = split_sentences(text, page_bounds) if text else []
        seen: set = set()

        if n_numbers < MAX_NUMBER_ITEMS:
            for m in _TOK_RE.finditer(text):
                if n_numbers >= MAX_NUMBER_ITEMS:
                    break
                if m.start() > 0 and (text[m.start() - 1].isalpha() or text[m.start() - 1] == "_"):
                    continue
                raw = m.group()
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    continue
                if value != value or abs(value) == float("inf"):
                    continue
                unit = _unit_after(text, m.end()) or _unit_before(text, m.start())
                key = f"num|{f.get('path')}|{value:.6g}|{unit or '-'}"
                if key in seen:
                    continue
                seen.add(key)
                quote, locator = _number_quote(f, base, text, sents, m.start(), m.end())
                if not quote.strip():
                    continue
                items.append({
                    "id": f"C{len(items) + 1}", "kind": "number", "text": quote,
                    "value": value, "unit": unit, "key": key,
                    "source": {"file": base, "locator": locator, "quote": quote},
                    "chars": [m.start(), m.end()],
                })
                stats["numbers"] += 1
                n_numbers += 1

        for fig in f.get("figures") or []:
            cap = fig.get("caption")
            if not cap or not str(cap).strip():
                continue
            items.append({
                "id": f"C{len(items) + 1}", "kind": "figure", "text": cap, "value": None,
                "unit": None, "key": f"fig|{f.get('path')}|{fig.get('locator')}",
                "media_path": fig.get("media_path"), "media_abs": fig.get("media_abs"),
                "media_rel": fig.get("media_rel"),
                "media_sha256": fig.get("media_sha256"), "width": fig.get("width"),
                "height": fig.get("height"), "page": fig.get("page"),
                "caption_source": fig.get("caption_source"),
                "source": {"file": base, "locator": fig.get("locator"), "quote": cap},
            })
            stats["figures"] += 1

        for tab in f.get("tables") or []:
            cap = tab.get("caption")
            if not cap or not str(cap).strip():
                continue
            items.append({
                "id": f"C{len(items) + 1}", "kind": "table", "text": cap, "value": None,
                "unit": None, "key": f"tab|{f.get('path')}|{tab.get('locator')}",
                "n_rows": tab.get("n_rows"), "n_cols": tab.get("n_cols"),
                "page": tab.get("page"),
                "source": {"file": base, "locator": tab.get("locator"), "quote": cap},
            })
            stats["tables"] += 1

        for title, a, b in _sections_of(f, base):
            line = text[a:b]
            if not line.strip():
                continue
            items.append({
                "id": f"C{len(items) + 1}", "kind": "section", "text": title, "value": None,
                "unit": None, "key": f"sec|{f.get('path')}|{normalize_ws(title).lower()}",
                "source": {"file": base, "locator": _unit_locator(f, base, a, b),
                           "quote": line},
            })
            stats["sections"] += 1

        if n_quotes < MAX_QUOTE_ITEMS and sents:
            for a, b in sents:
                if n_quotes >= MAX_QUOTE_ITEMS:
                    break
                s = text[a:b]
                if not (30 <= len(s) <= MAX_SENTENCE_CHARS):
                    continue
                if _TOK_RE.search(s):
                    continue  # already covered by a number item
                if not _is_claim_sentence(s):
                    continue
                key = f"quo|{f.get('path')}|{_sha256_bytes(normalize_ws(s).encode('utf-8'))[:12]}"
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "id": f"C{len(items) + 1}", "kind": "quote", "text": s, "value": None,
                    "unit": None, "key": key,
                    "source": {"file": base, "locator": _unit_locator(f, base, a, b),
                               "quote": s},
                })
                stats["quotes"] += 1
                n_quotes += 1

    return {"schema_version": SCHEMA_VERSION, "items": items, "stats": stats}


# --------------------------------------------------------------------------- #
# public API (CONTRACT §4)
# --------------------------------------------------------------------------- #
def load_materials(work_dir: Path) -> dict:
    """Read ``work_dir/materials.json``; returns ``{}`` when it is absent/unreadable."""
    data = _load_json(Path(work_dir) / "materials.json", None)
    return data if isinstance(data, dict) else {}


def ingest(input_dir: Path, work_dir: Path, *, case=None) -> dict:
    """Parse every material file and write materials.json + evidence-ledger.json."""
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if case is None:
        case = _load_json(input_dir / "case.json", {}) or {}
    case_id = str(case.get("case_id") or input_dir.name or "case")
    notes: list = []

    rels = _discover(input_dir)
    if not rels:
        notes.append(f"no material files found under {input_dir}")
    else:
        has_materials_dir = (input_dir / "materials").is_dir()
        if not has_materials_dir:
            notes.append("no materials/ directory; scanned the whole input directory")

    files = []
    json_docs = []
    for rel in rels:
        entry = _parse_one(input_dir, rel, work_dir, notes)
        if entry.get("kind") == "json":
            doc = _load_json(input_dir / rel, None)
            if doc is not None:
                json_docs.append(doc)
        files.append(entry)

    # data-driven captions for image figures (never inferred from the file name)
    capmap = _media_caption_map(json_docs) if json_docs else {}
    if capmap:
        for f in files:
            for fig in f.get("figures") or []:
                if fig.get("caption"):
                    continue
                cand = capmap.get(basename_of(fig.get("media_path") or "").lower())
                if cand is None:
                    cand = capmap.get(basename_of(f.get("path")).lower())
                if cand:
                    fig["caption"] = cand
                    fig["caption_source"] = "sidecar-json"
                    if not fig.get("chars") or fig["chars"] == [0, 0]:
                        fig["chars"] = _locate(f.get("text") or "", cand, 0)

    # then bind still-captionless figures to a numbered caption line in the body text
    _bind_figure_captions(files, notes)

    materials = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "input_dir": str(input_dir.resolve() if input_dir.exists() else input_dir),
        "files": files,
        "notes": notes,
    }
    ledger = build_ledger(materials)
    _save_json(work_dir / "materials.json", materials)
    _save_json(work_dir / "evidence-ledger.json", ledger)

    n_fig = sum(len(f.get("figures") or []) for f in files)
    n_tab = sum(len(f.get("tables") or []) for f in files)
    return {
        "ok": True,
        "case_id": case_id,
        "input_dir": materials["input_dir"],
        "work_dir": str(work_dir),
        "files": len(files),
        "parsed": sum(1 for f in files if f.get("parsed")),
        "failed": sum(1 for f in files if not f.get("parsed")),
        "figures": n_fig,
        "tables": n_tab,
        "numbers": ledger["stats"]["numbers"],
        "sections": ledger["stats"]["sections"],
        "quotes": ledger["stats"]["quotes"],
        "evidence_items": len(ledger["items"]),
        "materials_path": str(work_dir / "materials.json"),
        "ledger_path": str(work_dir / "evidence-ledger.json"),
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cli(argv) -> int:
    """``ingest --input /input --work /app/work``; exit 0 ok / 2 usage-IO."""
    ap = argparse.ArgumentParser(prog="ingest.py", description="parse materials + build the evidence ledger")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("ingest")
    p.add_argument("--input", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--case", default=None)
    p.add_argument("--json", action="store_true")
    argv = list(argv or [])
    if not argv or argv[0].startswith("-"):
        argv = ["ingest"] + argv
    a = ap.parse_args(argv)
    input_dir = Path(a.input)
    if a.cmd != "ingest":
        ap.print_usage(sys.stderr)
        return 2
    if not input_dir.is_dir():
        print(json.dumps({"ok": False, "reason": f"input dir not found: {input_dir}"},
                         ensure_ascii=False))
        return 2
    case = _load_json(Path(a.case), None) if a.case else None
    try:
        summary = ingest(input_dir, Path(a.work), case=case)
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2 if a.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
