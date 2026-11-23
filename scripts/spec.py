#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spec.py — DeckSpec v1 schema, defaults, validation and output_contract mapping.

Frozen interface: ARMI2611000101v1/work/CONTRACT.md §3.4 / §3.6 / §4.
No side effects at import time; safe to import as a library.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1

ROLES = (
    "title", "hook", "problem", "gap", "method", "results",
    "limitation", "conclusion", "qa", "backup",
)
STRENGTHS = ("strong", "moderate", "preliminary", "mixed")
AUDIENCES = ("committee", "group-meeting", "collaborators", "public", "reviewer", "general")
PLACEMENTS = ("right", "left", "full", "none")
QA_RISKS = ("high", "medium", "low")
QA_CATEGORIES = ("mechanism", "data", "control", "limitation", "other")

CLAIM_MAX = 160
NOTES_SIM_MAX = 0.8

# Story must contain these roles somewhere (contract §5 gate B).
REQUIRED_ARC_ROLES = ("problem", "method", "results", "conclusion")

_CONTENT_ROLES = ("hook", "problem", "gap", "method", "results", "limitation", "conclusion")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def normalize_ws(s: str) -> str:
    """Collapse all runs of whitespace to a single space and strip."""
    return re.sub(r"\s+", " ", s or "").strip()


def shingles(s: str, n: int = 3) -> set:
    words = re.findall(r"[\w\u4e00-\u9fff]+", normalize_ws(s).lower())
    if len(words) < n:
        return set(words) or ({normalize_ws(s)} if normalize_ws(s) else set())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity over 3-grams (deterministic, language-agnostic)."""
    sa, sb = shingles(a), shingles(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path | str, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #
def default_spec(case_id: str, *, language: str = "en") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "case_id": case_id,
            "language": language,
            "deck_title": "",
            "aspect": "16:9",
            "template": {"path": "", "sha256": ""},
            "audience": {
                "id": "committee",
                "prior_knowledge": "medium",
                "concerns": [],
                "expected_challenges": [],
            },
            "goal": {"statement": "", "success_checks": []},
            "budget": {"minutes": 12.0, "slide_count": 12, "qa_minutes": 3.0},
            "story": {"arc": list(REQUIRED_ARC_ROLES), "rationale": ""},
        },
        "theme": {
            "colors": {"primary": "#1F3864", "accent": "#C00000", "text": "#000000", "muted": "#595959"},
            "fonts": {"heading": "Calibri Light", "body": "Calibri"},
            "source": "template",
        },
        "evidence": [],
        "pages": [],
        "qa": [],
        "uncertainty": [],
        "limitations": [],
        "change_records": [],
    }


def ensure_shape(spec: dict) -> dict:
    """Fill missing top-level/known sub-keys with defaults. Never invents evidence."""
    base = default_spec(spec.get("meta", {}).get("case_id", ""))
    out = dict(spec)
    out.setdefault("schema_version", SCHEMA_VERSION)
    for key in ("meta", "theme"):
        merged = dict(base[key])
        merged.update(out.get(key) or {})
        if key == "meta":
            for sub in ("audience", "goal", "budget", "story", "template"):
                m = dict(base["meta"][sub])
                m.update(merged.get(sub) or {})
                merged[sub] = m
        if key == "theme":
            for sub in ("colors", "fonts"):
                m = dict(base["theme"][sub])
                m.update(merged.get(sub) or {})
                merged[sub] = m
        out[key] = merged
    for key in ("evidence", "pages", "qa", "uncertainty", "limitations", "change_records"):
        if not isinstance(out.get(key), list):
            out[key] = []
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_spec(spec: dict, template_index: dict | None = None) -> list[dict]:
    """Structural + semantic issues. Returns [] when clean.

    Each issue: {"code": "S0xx", "where": "...", "detail": "..."}
    """
    issues: list[dict] = []
    if not isinstance(spec, dict):
        return [{"code": "S001", "where": "-", "detail": "spec is not an object"}]

    def bad(code: str, where: str, detail: str) -> None:
        issues.append({"code": code, "where": where, "detail": detail})

    if spec.get("schema_version") != SCHEMA_VERSION:
        bad("S002", "-", f"schema_version must be {SCHEMA_VERSION}")

    meta = spec.get("meta") or {}
    if not meta.get("case_id"):
        bad("S003", "meta.case_id", "missing")

    ev = spec.get("evidence") or []
    if not isinstance(ev, list):
        bad("S004", "evidence", "must be a list")
        ev = []
    ev_ids: dict[str, int] = {}
    for i, e in enumerate(ev):
        if not isinstance(e, dict):
            bad("S004", f"evidence[{i}]", "not an object")
            continue
        eid = e.get("id")
        if not eid:
            bad("S005", f"evidence[{i}]", "missing id")
            continue
        if eid in ev_ids:
            bad("S006", f"evidence[{eid}]", "duplicate evidence id")
        ev_ids[eid] = i
        src = e.get("source") or {}
        if not src.get("locator"):
            bad("S007", f"evidence[{eid}].source", "missing locator")
        if e.get("kind") not in ("number", "figure", "table", "section", "quote", None):
            bad("S008", f"evidence[{eid}].kind", f"unknown kind {e.get('kind')!r}")
        if "value" in e and e.get("value") is not None and src and not src.get("quote"):
            bad("S009", f"evidence[{eid}]", "numeric evidence has no source.quote (unverifiable)")

    pages = spec.get("pages") or []
    if not isinstance(pages, list) or not pages:
        bad("S010", "pages", "must be a non-empty list")
        pages = []
    seen_pages: dict[str, int] = {}
    layouts_ok = None
    if template_index:
        layouts_ok = {str(l.get("name")) for l in (template_index.get("layouts") or [])}
    for i, p in enumerate(pages):
        if not isinstance(p, dict):
            bad("S011", f"pages[{i}]", "not an object")
            continue
        pid = p.get("id")
        if not pid:
            bad("S012", f"pages[{i}]", "missing id")
            continue
        if pid in seen_pages:
            bad("S013", f"pages[{pid}]", "duplicate page id")
        seen_pages[pid] = i
        role = p.get("role")
        if role not in ROLES:
            bad("S014", f"pages[{pid}].role", f"unknown role {role!r}")
        claim = normalize_ws(p.get("claim") or "")
        if role in _CONTENT_ROLES and not claim:
            bad("S015", f"pages[{pid}].claim", "content page needs a claim")
        if len(claim) > CLAIM_MAX:
            bad("S016", f"pages[{pid}].claim", f"claim length {len(claim)} > {CLAIM_MAX}")
        for eid in p.get("evidence_ids") or []:
            if eid not in ev_ids:
                bad("S017", f"pages[{pid}].evidence_ids", f"dangling evidence id {eid!r}")
        if layout_name := p.get("layout"):
            if layouts_ok is not None and layout_name not in layouts_ok:
                bad("S018", f"pages[{pid}].layout", f"layout {layout_name!r} not in template")
        elif layouts_ok is not None:
            pass  # role_layout will resolve it; build records the decision
        if p.get("strength") not in STRENGTHS and p.get("strength") is not None:
            bad("S019", f"pages[{pid}].strength", f"unknown strength {p.get('strength')!r}")
        fig = p.get("figure") or {}
        if fig:
            if fig.get("placement") not in PLACEMENTS and fig.get("placement") is not None:
                bad("S020", f"pages[{pid}].figure.placement", "unknown placement")
            if fig.get("evidence_id") and fig["evidence_id"] not in ev_ids:
                bad("S021", f"pages[{pid}].figure.evidence_id", "dangling evidence id")
            if fig.get("evidence_id") and fig["evidence_id"] not in (p.get("evidence_ids") or []):
                bad("S022", f"pages[{pid}].figure", "figure evidence not listed in page evidence_ids")
        if role in _CONTENT_ROLES:
            notes = p.get("notes") or {}
            say = normalize_ws(notes.get("say") or "")
            if not say:
                bad("S023", f"pages[{pid}].notes.say", "content page needs speaker notes")
            else:
                sim = similarity(say, " ".join(p.get("bullets") or []))
                if sim >= NOTES_SIM_MAX:
                    bad("S024", f"pages[{pid}].notes.say",
                        f"notes duplicate the bullets (similarity {sim:.2f} >= {NOTES_SIM_MAX})")
            if not notes.get("timing_s"):
                bad("S025", f"pages[{pid}].notes.timing_s", "missing timing_s")

    # budget
    budget = meta.get("budget") or {}
    if not isinstance(budget.get("slide_count"), int) or budget.get("slide_count", 0) <= 0:
        bad("S026", "meta.budget.slide_count", "must be a positive int")
    else:
        if pages and len(pages) != budget["slide_count"]:
            bad("S027", "meta.budget.slide_count",
                f"spec has {len(pages)} pages but budget asks {budget['slide_count']}")
    if not budget.get("minutes") or float(budget.get("minutes", 0)) <= 0:
        bad("S028", "meta.budget.minutes", "must be > 0")

    arc = (meta.get("story") or {}).get("arc") or []
    for need in REQUIRED_ARC_ROLES:
        if need not in arc:
            bad("S029", "meta.story.arc", f"arc is missing required role {need!r}")

    # qa
    qa = spec.get("qa") or []
    if len(qa) < 3:
        bad("S030", "qa", f"only {len(qa)} Q&A entries; rubric wants >= 3")
    qa_ids: dict[str, int] = {}
    for i, q in enumerate(qa):
        if not isinstance(q, dict):
            bad("S031", f"qa[{i}]", "not an object")
            continue
        qid = q.get("id")
        if not qid:
            bad("S032", f"qa[{i}]", "missing id")
            continue
        if qid in qa_ids:
            bad("S033", f"qa[{qid}]", "duplicate qa id")
        qa_ids[qid] = i
        if q.get("risk") not in QA_RISKS:
            bad("S034", f"qa[{qid}].risk", f"unknown risk {q.get('risk')!r}")
        if q.get("category") is not None and q.get("category") not in QA_CATEGORIES:
            bad("S035", f"qa[{qid}].category", f"unknown category {q.get('category')!r}")
        if not normalize_ws(q.get("question") or ""):
            bad("S036", f"qa[{qid}]", "empty question")
        if not normalize_ws(q.get("answer") or ""):
            bad("S037", f"qa[{qid}]", "empty answer")
        for sid in q.get("slide_ids") or []:
            if sid not in seen_pages:
                bad("S038", f"qa[{qid}].slide_ids", f"dangling page id {sid!r}")
        for eid in q.get("evidence_ids") or []:
            if eid not in ev_ids:
                bad("S039", f"qa[{qid}].evidence_ids", f"dangling evidence id {eid!r}")

    aud = meta.get("audience") or {}
    if aud.get("id") not in AUDIENCES and aud.get("id") is not None:
        bad("S040", "meta.audience.id", f"unknown audience {aud.get('id')!r}")
    if not normalize_ws((meta.get("goal") or {}).get("statement") or ""):
        bad("S041", "meta.goal.statement", "empty")

    return issues


# --------------------------------------------------------------------------- #
# output_contract mapping (gate H)
# --------------------------------------------------------------------------- #
# semantic family -> ordered list of candidate keys
_CONTRACT_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("deck", ("deck", "slide", "presentation", "ppt", "pptx", "slides", "deck_pptx",
              "editable", "幻灯片", "演示", "slides_path", "slides_file")),
    ("notes", ("note", "script", "speaker", "讲稿", "备注", "remark", "narration")),
    ("evidence_map", ("evidence", "provenance", "mapping", "citation", "引用", "证据", "trace")),
    ("qa", ("qa", "question", "answer", "问答", "backup", "objection")),
    ("preview", ("preview", "render", "image", "png", "thumbnail", "预览")),
    ("pdf", ("pdf", "export", "导出")),
    ("plan", ("plan", "outline", "structure", "页级", "page_plan", "story")),
    ("summary", ("summary", "answer", "result", "output", "report", "text", "content",
                 "description", "main", "conclusion", "摘要", "结论")),
]


def _family_of(key: str) -> str | None:
    k = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", str(key).lower())
    for fam, tokens in _CONTRACT_FAMILIES:
        for t in tokens:
            if t in k:
                return fam
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def contract_value(fam: str, ctx: dict, key: str) -> str:
    """Value for one output_contract key. Never returns '' (gate H forbids empty)."""
    root: Path = ctx["out_dir"]
    art: dict = ctx.get("artifacts") or {}
    summary: str = ctx.get("summary_text") or ""

    def p(name: str) -> str | None:
        a = art.get(name)
        return a["path"] if a else None

    if fam == "deck":
        return p("deck.pptx") or _fallback_summary(ctx)
    if fam == "notes":
        return p("speaker-notes.md") or _fallback_summary(ctx)
    if fam == "evidence_map":
        return p("evidence-map.json") or _fallback_summary(ctx)
    if fam == "qa":
        return p("qa-prep.md") or _fallback_summary(ctx)
    if fam == "preview":
        return p("render_manifest.json") or p("preview/page-0001.png") or _fallback_summary(ctx)
    if fam == "pdf":
        return p("deck.pdf") or _fallback_summary(ctx)
    if fam == "plan":
        return p("deck_plan.json") or _fallback_summary(ctx)
    # summary family -> honest run summary, always non-empty
    return summary or _fallback_summary(ctx)


def _fallback_summary(ctx: dict) -> str:
    gates = (ctx.get("gates") or [])
    g = ", ".join(f"{x.get('id')}={x.get('status')}" for x in gates) or "not gated"
    return (f"status={ctx.get('status')}; slides={ctx.get('slide_count')}; "
            f"evidence={ctx.get('evidence_count')}; gates={g}")


def map_contract(case: dict | None, ctx: dict) -> tuple[dict, list[str]]:
    """Fill every key of case.json.output_contract with a truthful non-empty value.

    Returns (values, unmapped_keys).  Unmapped keys still get the run summary and are
    disclosed in result.audit.unmapped_contract_keys — never silently emptied.
    """
    contract = ((case or {}).get("output_contract") or {})
    values: dict[str, str] = {}
    unmapped: list[str] = []
    if not isinstance(contract, dict):
        return values, unmapped
    for key in contract.keys():
        fam = _family_of(key)
        if fam is None:
            unmapped.append(str(key))
            values[str(key)] = contract_value("summary", ctx, key)
        else:
            values[str(key)] = contract_value(fam, ctx, key)
    return values, unmapped


# --------------------------------------------------------------------------- #
# IO + CLI
# --------------------------------------------------------------------------- #
def load_spec(path: Path | str) -> dict:
    spec = load_json(path)
    if spec is None:
        raise FileNotFoundError(f"spec not found or unreadable: {path}")
    return ensure_shape(spec)


def save_spec(spec: dict, path: Path | str) -> None:
    save_json(path, spec)


def cli(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="spec.py", description="DeckSpec schema utilities")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a DeckSpec")
    v.add_argument("--spec", required=True)
    v.add_argument("--template-index", default=None)
    v.add_argument("--json", action="store_true")

    m = sub.add_parser("map-contract", help="map case.output_contract to real artifact paths")
    m.add_argument("--case", required=True)
    m.add_argument("--out-dir", required=True)
    m.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "validate":
        spec = load_spec(a.spec)
        ti = load_json(a.template_index) if a.template_index else None
        issues = validate_spec(spec, ti)
        if a.json:
            print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))
        else:
            for i in issues:
                print(f"{i['code']} {i['where']}: {i['detail']}")
            print(f"{len(issues)} issue(s)")
        return 0 if not issues else 1
    if a.cmd == "map-contract":
        case = load_json(a.case, {}) or {}
        ctx = {"out_dir": Path(a.out_dir), "artifacts": {}, "status": "unknown",
               "slide_count": 0, "evidence_count": 0, "gates": []}
        vals, unmapped = map_contract(case, ctx)
        if a.json:
            print(json.dumps({"values": vals, "unmapped": unmapped}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
