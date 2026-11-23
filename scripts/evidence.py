#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence.py — material locator resolution + provenance validation.

Frozen interface: work/CONTRACT.md  §3.1 (locator grammar) and  §4 (module API).
This module has NO import-time side effects and is safe to import from any peer
script (pt.py / validate.py / selftest.py).

Locator grammar implemented (CONTRACT §3.1):

    <basename>#p.<n>        page n            (1-based)
    <basename>#L<a>-L<b>    lines a..b        (1-based, inclusive)
    <basename>#L<a>         line a
    <basename>#r.<n>        n-th record       (csv row / json top-level child)
    <basename>#fig.<n>      n-th figure
    <basename>#tab.<n>      n-th table
    <basename>#all          whole file text

Matching rules that the gates depend on:

* ``quote_in_span`` is a *whitespace-normalised substring* test and is
  CASE-SENSITIVE (CONTRACT §3.1).  ``normalize_ws`` is byte-for-byte the same
  rule as ``spec.normalize_ws`` (``re.sub(r"\\s+", " ", s).strip()``).
* ``number_in_quote`` compares numbers *inside the single quote string it is
  given*.  A number that only occurs in a different sentence of the same page
  therefore fails E903 instead of passing, which is the whole point of the
  check.
* Duplicate basenames resolve to the first match and are flagged ``ambiguous``.
"""
from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1

__all__ = [
    "SCHEMA_VERSION",
    "normalize_ws",
    "quote_in_span",
    "number_in_quote",
    "resolve_locator",
    "evidence_index",
    "validate_evidence",
    "split_sentences",
    "sentence_at",
    "cli",
]


# --------------------------------------------------------------------------- #
# text normalisation
# --------------------------------------------------------------------------- #
_WS_RE = re.compile(r"\s+")
_WS_FOLD = {"\u3000": " ", "\u00a0": " ", "\ufeff": ""}


def normalize_ws(s) -> str:
    """Collapse every run of whitespace to one space and strip both ends."""
    if s is None:
        return ""
    text = str(s)
    for src, dst in _WS_FOLD.items():
        if src in text:
            text = text.replace(src, dst)
    return _WS_RE.sub(" ", text).strip()


def quote_in_span(quote, span_text) -> bool:
    """True when the quote is a whitespace-normalised substring of the span text.

    Case-sensitive; an empty quote never matches (it is not verbatim evidence).
    """
    q = normalize_ws(quote)
    if not q:
        return False
    return q in normalize_ws(span_text)


# --------------------------------------------------------------------------- #
# numeric matching  (token-level, within one quote only)
# --------------------------------------------------------------------------- #
_SUP_TRANS = str.maketrans(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207b\u207a",
    "0123456789-+",
)
_SIGN_FOLD = {
    "\u2212": "-",          # minus sign
    "\u2010": "-",          # hyphen
    "\u2011": "-",          # non-breaking hyphen
    "\u00b1": " ",          # +-
    "\u2213": " ",          # -+
    "\u2013": " ",          # en dash  -> range separator
    "\u2014": " ",          # em dash
    "\u2212": "-",
    "\uff0d": "-",
    "\uff0b": "+",
}
_MULT = "\u00d7\u2715\u22c5\u00b7xX"

_NUM_RE = re.compile(
    r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\.\d+|\d+)(?:[eE][+-]?\d+)?"
)


def _prepare_numbers(s: str) -> str:
    """Rewrite unicode maths into a plain token stream that _NUM_RE can read."""
    out = s.translate(_SUP_TRANS)
    for src, dst in _SIGN_FOLD.items():
        if src in out:
            out = out.replace(src, dst)
    out = re.sub("[" + _MULT + r"]\s*10\s*\^?\s*\(?\s*([+-]?\d+)\s*\)?",
                 lambda m: "e" + m.group(1), out)
    out = re.sub(r"(?<![\d.])10\s*\^\s*\(?\s*([+-]?\d+)\s*\)?",
                 lambda m: "1e" + m.group(1), out)
    out = re.sub(r"(\d+(?:\.\d+)?)\s*\^\s*\(?\s*([+-]?\d+)\s*\)?",
                 lambda m: m.group(1) + "e" + m.group(2), out)
    return out


def _to_float(tok: str) -> float:
    t = tok.strip()
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        if len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            t = t.replace(",", "")
        else:
            t = t.replace(",", ".")
    return float(t)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 + 1e-6 * max(abs(a), abs(b))


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _candidate_values(value) -> list:
    """Every number a declared value could legitimately denote."""
    if isinstance(value, bool) or value is None:
        return []
    out: list = []

    def add(x: float) -> None:
        if _finite(x):
            out.append(float(x))
            out.append(abs(float(x)))

    if isinstance(value, (int, float)):
        add(float(value))
        return out
    s = _prepare_numbers(str(value))
    for m in _NUM_RE.finditer(s):
        try:
            base = _to_float(m.group())
        except ValueError:
            continue
        add(base)
        if s[m.end():m.end() + 1] in ("%", "\uff05"):
            add(base / 100.0)
    return out


def number_in_quote(value, quote) -> bool:
    """True when the numeric value occurs as a real token inside this one quote.

    Handles ``2.8`` / ``2.80`` / ``0.028`` / ``1e-3`` / ``12.5%`` / ``1,234`` /
    ``\u00b12.8`` / superscript exponents / ``2.8-fold`` / ``37 \u00b0C``.
    Non-numeric values fall back to a whitespace-normalised substring test.
    """
    if quote is None:
        return False
    quote = str(quote)
    vals = _candidate_values(value)
    if not vals:
        txt = normalize_ws(value)
        return bool(txt) and txt in normalize_ws(quote)
    s = _prepare_numbers(quote)
    for m in _NUM_RE.finditer(s):
        try:
            base = _to_float(m.group())
        except ValueError:
            continue
        cands = [base, abs(base)]
        if s[m.end():m.end() + 1] in ("%", "\uff05"):
            cands += [base / 100.0, abs(base) / 100.0]
        for c in cands:
            for v in vals:
                if _close(c, v):
                    return True
    return False


# --------------------------------------------------------------------------- #
# sentence splitting (verbatim char ranges, used by ingest's quote extraction)
# --------------------------------------------------------------------------- #
_ABBREV = {
    "al", "i.e", "e.g", "fig", "figs", "tab", "eq", "eqs", "vs", "dr", "prof", "approx",
    "cf", "no", "nos", "suppl", "sec", "min", "max", "ca", "sd", "se", "ci", "vol",
    "pp", "ed", "eds", "mr", "ms", "mrs", "ref", "refs", "et", "st", "inc", "ltd",
}
_TERMINATORS = ".!?\u3002\uff01\uff1f"


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x3000 <= o <= 0x303F


_HEAD_LINE_RE = re.compile(r"^[ \t]*#{1,6}[ \t]")


def _lines_of(text: str):
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
        if start == n:
            break
    return out


def split_sentences(text: str, boundaries=None) -> list:
    """Split ``text`` into ``(start, end)`` char ranges that are exact slices.

    The ranges never invent characters: ``text[start:end]`` is the quote.
    Abbreviations, initials and decimal points are protected from splitting, and a
    paragraph break / markdown heading is always a boundary - a quote must never
    drag a heading or a blank line into the sentence it reports.

    ``boundaries`` adds caller-supplied cut offsets (ingest passes page starts, so
    a quote can never span a PDF page break).
    """
    if not text:
        return []
    n = len(text)
    protected: set = set()
    for m in re.finditer(r"(?<![\w.])([A-Za-z]{1,8})\.", text):
        if m.group(1).lower() in _ABBREV:
            protected.add(m.end() - 1)
    for m in re.finditer(r"\d\.\d", text):
        protected.add(m.start() + 1)
    for m in re.finditer(r"(?<![A-Za-z])([A-Z])\.", text):
        protected.add(m.end() - 1)

    cut: set = set()
    for i, ch in enumerate(text):
        if ch not in _TERMINATORS or i in protected:
            continue
        j = i + 1
        nxt = text[j] if j < n else ""
        if nxt and not (nxt.isspace() or _is_cjk(nxt) or nxt in "\"'\u201d\u2019)]}"):
            continue
        cut.add(i + 1)
    for a, b in _lines_of(text):
        line = text[a:b]
        if not line.strip() or _HEAD_LINE_RE.match(line):
            if b + 1 <= n:
                cut.add(b + 1)
    for b in (boundaries or ()):
        try:
            b = int(b)
        except (TypeError, ValueError):
            continue
        if 0 < b < n:
            cut.add(b)

    out: list = []
    start = 0
    for c in sorted(cut):
        if c <= start:
            continue
        out.append((start, c))
        start = c
    if start < n:
        out.append((start, n))

    trimmed: list = []
    for a, b in out:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            trimmed.append((a, b))
    return trimmed


def sentence_at(text: str, index: int, sentences=None) -> tuple:
    """Return the ``(start, end)`` sentence range containing char ``index``."""
    sents = sentences if sentences is not None else split_sentences(text)
    if not sents:
        return (0, 0)
    for a, b in sents:
        if a <= index < b:
            return (a, b)
    best = sents[0]
    best_d = None
    for a, b in sents:
        d = a - index if index < a else index - (b - 1)
        if d < 0:
            d = 0
        if best_d is None or d < best_d:
            best, best_d = (a, b), d
    return best


# --------------------------------------------------------------------------- #
# locator resolution
# --------------------------------------------------------------------------- #
_FRAG_PAGE = re.compile(r"^p\.(\d+)$")
_FRAG_LINE = re.compile(r"^L(\d+)(?:-L(\d+))?$")
_FRAG_REC = re.compile(r"^r\.(\d+)$")
_FRAG_FIG = re.compile(r"^fig\.(\d+)$")
_FRAG_TAB = re.compile(r"^tab\.(\d+)$")
_FRAG_ALL = re.compile(r"^all$")


def basename_of(path) -> str:
    return posixpath.basename(str(path or "").replace("\\", "/"))


def _bad(reason: str, locator: str, basename: str = "") -> dict:
    return {"ok": False, "reason": reason, "locator": locator, "basename": basename,
            "spans": [], "text": ""}


def _file_text(entry: dict) -> str:
    return entry.get("text") or ""


def _slice(entry: dict, chars) -> str:
    text = _file_text(entry)
    if not isinstance(chars, (list, tuple)) or len(chars) != 2:
        return ""
    a, b = int(chars[0]), int(chars[1])
    a = max(0, min(a, len(text)))
    b = max(a, min(b, len(text)))
    return text[a:b]


def _span_chars(span: dict):
    c = span.get("chars")
    if isinstance(c, (list, tuple)) and len(c) == 2:
        try:
            return [int(c[0]), int(c[1])]
        except (TypeError, ValueError):
            return None
    return None


def _find_span(entry: dict, predicate):
    for span in entry.get("spans") or []:
        if isinstance(span, dict) and predicate(span):
            return span
    return None


def _collect(materials: dict, name: str) -> list:
    files = (materials or {}).get("files") or []
    return [f for f in files
            if isinstance(f, dict) and basename_of(f.get("path") or f.get("abs")) == name]


def resolve_locator(materials: dict, file_name: str, locator: str) -> dict:
    """Resolve a locator against ``materials`` (CONTRACT §3.1, incl. the 2026-11-23 note).

    Accepts BOTH forms:

    * **full form** ``"paper.md#L9-L9"`` - the locator's own basename wins; the
      ``file_name`` argument is only a fallback (and an ambiguity record).  It is
      never concatenated in front of a locator that already carries a basename.
    * **naked form** ``"L9-L9"`` / ``"p.4"`` - only then is ``<basename>#`` built
      from ``file_name``.

    A locator that was accidentally doubled (``paper.md#paper.md#L9-L9``) is
    repaired to the full form instead of failing, so an upstream concatenation
    bug can never silently turn every item into E901.

    Returns ``{"ok", "reason", "spans": [{"chars","page","text"}], "text", ...}``.
    Never raises: a malformed locator is an ``ok: False`` result, so callers can
    turn it into E901 instead of crashing.
    """
    locator = str(locator or "").strip()
    file_name = str(file_name or "").strip()
    normalized_from = None
    if "#" in locator:
        loc_base, frag = locator.split("#", 1)
        if "#" in frag:
            head, tail = frag.rsplit("#", 1)
            if tail.strip():
                normalized_from = locator
                loc_base, frag = basename_of(head), tail
                locator = f"{loc_base}#{frag.strip()}"
        loc_base = basename_of(loc_base)
        frag = frag.strip()
        if not frag:
            return _bad("bad-locator", locator, loc_base)
        names = [n for n in (loc_base, basename_of(file_name)) if n]
    else:
        if not file_name:
            return _bad("bad-locator", locator, "")
        frag = locator
        names = [basename_of(file_name)]
        if not names[0]:
            return _bad("bad-locator", locator, "")
        locator = f"{names[0]}#{frag}"
    if not names:
        return _bad("bad-locator", locator, "")
    matches: list = []
    chosen = ""
    for nm in names:
        matches = _collect(materials, nm)
        if matches:
            chosen = nm
            break
    if not matches:
        return _bad("file-not-found", locator, names[0])
    ambiguous = len(matches) > 1
    entry = matches[0]
    text = _file_text(entry)
    out = {
        "ok": True, "reason": "ok", "locator": locator, "basename": chosen,
        "file": entry.get("path"), "kind": None, "chars": [0, 0], "text": "",
        "spans": [], "ambiguous": ambiguous,
    }
    if normalized_from:
        out["normalized_from"] = normalized_from

    m = _FRAG_ALL.match(frag)
    if m:
        out["kind"] = "all"
        out["chars"] = [0, len(text)]
        out["text"] = text
        out["spans"] = [{"locator": locator, "kind": "all", "chars": [0, len(text)],
                         "page": None, "text": text}]
        return out

    m = _FRAG_PAGE.match(frag)
    if m:
        page = int(m.group(1))
        span = _find_span(entry, lambda s: s.get("kind") == "page" and s.get("page") == page)
        if span is None:
            return _bad("page-out-of-range", locator, chosen)
        chars = _span_chars(span) or [0, 0]
        body = _slice(entry, chars)
        out.update(kind="page", chars=chars, text=body,
                   spans=[{"locator": locator, "kind": "page", "chars": chars,
                           "page": page, "text": body}])
        return out

    m = _FRAG_LINE.match(frag)
    if m:
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            return _bad("bad-locator", locator, chosen)
        line_spans = [s for s in (entry.get("spans") or [])
                      if isinstance(s, dict) and s.get("kind") == "line"]
        if not line_spans:
            return _bad("line-out-of-range", locator, chosen)
        by_line = {int(s.get("line") or 0): s for s in line_spans}
        if a not in by_line or b not in by_line:
            return _bad("line-out-of-range", locator, chosen)
        ca = _span_chars(by_line[a]) or [0, 0]
        cb = _span_chars(by_line[b]) or [0, 0]
        chars = [ca[0], cb[1]]
        body = _slice(entry, chars)
        spans = []
        for ln in range(a, b + 1):
            s = by_line[ln]
            c = _span_chars(s) or [0, 0]
            spans.append({"locator": f"{chosen}#L{ln}-L{ln}", "kind": "line", "line": ln,
                          "chars": c, "page": None, "text": _slice(entry, c)})
        out.update(kind="line", chars=chars, text=body, spans=spans,
                   line=[a, b])
        return out

    m = _FRAG_REC.match(frag)
    if m:
        rec = int(m.group(1))
        span = _find_span(entry, lambda s: s.get("kind") == "record" and s.get("record") == rec)
        if span is None:
            return _bad("record-out-of-range", locator, chosen)
        chars = _span_chars(span) or [0, 0]
        body = _slice(entry, chars)
        out.update(kind="record", chars=chars, text=body,
                   spans=[{"locator": locator, "kind": "record", "record": rec,
                           "chars": chars, "page": None, "text": body}])
        return out

    for pat, kind, coll in ((_FRAG_FIG, "figure", "figures"), (_FRAG_TAB, "table", "tables")):
        m = pat.match(frag)
        if not m:
            continue
        idx = int(m.group(1))
        items = [it for it in (entry.get(coll) or []) if isinstance(it, dict)]
        if idx < 1 or idx > len(items):
            return _bad(f"{kind}-out-of-range", locator, chosen)
        item = items[idx - 1]
        span = _find_span(entry, lambda s, loc=item.get("locator"): s.get("locator") == loc)
        chars = _span_chars(span) if span else None
        caption = item.get("caption") or ""
        if chars and chars[1] > chars[0]:
            body = _slice(entry, chars)
        else:
            chars = chars or [0, 0]
            body = caption
        out.update(kind=kind, chars=chars, text=body, item=item,
                   spans=[{"locator": locator, "kind": kind, "chars": chars,
                           "page": item.get("page"), "text": body,
                           "caption": caption}])
        return out

    return _bad("bad-locator", locator, chosen)


# --------------------------------------------------------------------------- #
# evidence index
# --------------------------------------------------------------------------- #
def evidence_index(materials: dict) -> dict:
    """Flat, deterministic lookup tables over ``materials`` (basename -> file)."""
    files = [f for f in ((materials or {}).get("files") or []) if isinstance(f, dict)]
    by_base: dict = {}
    for f in files:
        by_base.setdefault(basename_of(f.get("path") or f.get("abs")), []).append(f)
    ambiguous = sorted(b for b, fs in by_base.items() if len(fs) > 1)

    locators: dict = {}
    for base in sorted(by_base):
        for entry in by_base[base][:1]:
            for span in entry.get("spans") or []:
                if not isinstance(span, dict) or not span.get("locator"):
                    continue
                locators.setdefault(span["locator"], {
                    "file": entry.get("path"), "kind": span.get("kind"),
                    "chars": span.get("chars"), "page": span.get("page"),
                    "line": span.get("line"), "record": span.get("record"),
                    "text": _slice(entry, span.get("chars")),
                })
            for it in entry.get("figures") or []:
                if isinstance(it, dict) and it.get("locator"):
                    locators.setdefault(it["locator"], {
                        "file": entry.get("path"), "kind": "figure",
                        "chars": it.get("chars"), "page": it.get("page"),
                        "text": it.get("caption") or "",
                        "media_path": it.get("media_path"),
                    })
            for it in entry.get("tables") or []:
                if isinstance(it, dict) and it.get("locator"):
                    locators.setdefault(it["locator"], {
                        "file": entry.get("path"), "kind": "table",
                        "chars": it.get("chars"), "page": it.get("page"),
                        "text": it.get("caption") or "",
                    })
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": (materials or {}).get("case_id"),
        "files": {f.get("path"): {"kind": f.get("kind"), "parsed": f.get("parsed"),
                                  "sha256": f.get("sha256"), "bytes": f.get("bytes"),
                                  "n_spans": len(f.get("spans") or [])}
                  for f in files},
        "basenames": {b: [f.get("path") for f in fs] for b, fs in sorted(by_base.items())},
        "ambiguous": ambiguous,
        "locators": locators,
    }


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _as_evidence_list(evidence) -> list:
    if isinstance(evidence, list):
        return evidence
    if isinstance(evidence, dict):
        for key in ("evidence", "items"):
            v = evidence.get(key)
            if isinstance(v, list):
                return v
    return []


def validate_evidence(evidence, materials: dict) -> list:
    """Check every evidence item's provenance.

    Returns ``[{"code": "E901|E902|E903", "id": ..., "detail": ...}]`` and never
    raises.  E901 = locator unresolved, E902 = quote not verbatim in the span,
    E903 = the declared number is not inside its own quote.
    """
    errors: list = []
    items = _as_evidence_list(evidence)
    for i, ev in enumerate(items):
        if not isinstance(ev, dict):
            errors.append({"code": "E901", "id": f"[{i}]",
                           "detail": "evidence item is not an object"})
            continue
        eid = ev.get("id") or f"[{i}]"
        src = ev.get("source") if isinstance(ev.get("source"), dict) else {}
        file_name = src.get("file") or ""
        locator = src.get("locator") or ""
        quote = src.get("quote") or ""
        if not locator:
            errors.append({"code": "E901", "id": eid,
                           "detail": "source.locator is missing"})
            continue
        res = resolve_locator(materials, file_name, locator)
        if not res.get("ok"):
            errors.append({"code": "E901", "id": eid,
                           "detail": f"locator {locator!r} unresolved ({res.get('reason')})"})
            continue
        span_text = res.get("text") or ""
        if not str(quote).strip():
            errors.append({"code": "E902", "id": eid,
                           "detail": f"source.quote is empty; {locator} resolved to "
                                     f"{len(span_text)} chars"})
            continue
        if not quote_in_span(quote, span_text):
            errors.append({"code": "E902", "id": eid,
                           "detail": f"quote is not verbatim (whitespace-normalised) in "
                                     f"{locator}; quote={str(quote)[:100]!r}"})
            continue
        value = ev.get("value")
        if value is not None and not number_in_quote(value, quote):
            errors.append({"code": "E903", "id": eid,
                           "detail": f"value {value!r} does not occur inside its own quote "
                                     f"for {locator}; quote={str(quote)[:100]!r}"})
    return errors


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_json(path) -> object:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise SystemExit(f"cannot parse {p}: {exc}")


def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))


def cli(argv) -> int:
    """``evidence.py [command] --materials M ...``; 0 ok / 1 findings / 2 usage-IO."""
    argv = list(argv or [])
    cmd = argv[0] if argv and not argv[0].startswith("-") else "validate"
    if cmd not in ("validate", "resolve", "index", "number"):
        cmd = "validate"
    else:
        argv = argv[1:]

    ap = argparse.ArgumentParser(prog="evidence.py",
                                 description="material locator resolution + provenance validation")
    ap.add_argument("--materials", required=True, help="path to materials.json")
    ap.add_argument("--evidence", default=None, help="spec.json or evidence list to validate")
    ap.add_argument("--locator", default=None, help="locator to resolve (resolve)")
    ap.add_argument("--file", default=None, help="file name hint (resolve)")
    ap.add_argument("--value", default=None, help="value to search (number)")
    ap.add_argument("--quote", default=None, help="quote to search (number)")
    ap.add_argument("--out", default=None, help="write the JSON result here (index)")
    ap.add_argument("--json", action="store_true", help="pretty JSON output")
    a = ap.parse_args(argv)

    materials = _load_json(a.materials)
    if not isinstance(materials, dict):
        _print({"ok": False, "reason": f"materials.json unreadable: {a.materials}"}, a.json)
        return 2

    if cmd == "resolve":
        if not a.locator:
            _print({"ok": False, "reason": "resolve needs --locator"}, a.json)
            return 2
        res = resolve_locator(materials, a.file or "", a.locator)
        _print(res, a.json)
        return 0 if res.get("ok") else 1

    if cmd == "index":
        idx = evidence_index(materials)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        _print({"ok": True, "files": len(idx["files"]), "locators": len(idx["locators"]),
                "ambiguous": idx["ambiguous"], "out": a.out}, a.json)
        return 0

    if cmd == "number":
        hit = number_in_quote(a.value, a.quote or "")
        _print({"ok": hit, "value": a.value, "hit": hit}, a.json)
        return 0 if hit else 1

    if not a.evidence:
        _print({"ok": False, "reason": "validate needs --evidence"}, a.json)
        return 2
    spec = _load_json(a.evidence)
    if spec is None:
        _print({"ok": False, "reason": f"evidence file unreadable: {a.evidence}"}, a.json)
        return 2
    if isinstance(spec, dict) and not isinstance(spec.get("evidence"), list) \
            and not isinstance(spec.get("items"), list):
        _print({"ok": False, "reason": "evidence file has no 'evidence'/'items' list"}, a.json)
        return 2
    errors = validate_evidence(spec, materials)
    _print({"ok": not errors, "count": len(errors),
            "codes": sorted({e["code"] for e in errors}), "errors": errors}, a.json)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
