#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deckplan.py — deterministic DeckSpec autoplanner (the no-LLM floor).

Frozen interface: CONTRACT.md §4.  Turns ingested materials + case hints into a
complete, structurally valid DeckSpec that still passes every gate.  It never
invents a number: every claim/bullet/quote it emits is copied from a real
evidence-ledger item whose locator resolves into materials.json.

This is deliberately a FLOOR, not the target: an LLM-authored spec.json earns the
quality points.  The floor exists so a case still produces an honest deck when the
model budget is exhausted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec import (  # noqa: E402
    NOTES_SIM_MAX, REQUIRED_ARC_ROLES, SCHEMA_VERSION, ensure_shape, load_json, normalize_ws,
    save_json, similarity, utc_stamp,
)

NOTES_SIM_THRESHOLD = NOTES_SIM_MAX

CONTENT_ROLES = ("hook", "problem", "gap", "method", "results", "limitation", "conclusion")

ROLE_TIME_WEIGHT = {
    "title": 0.20, "hook": 0.85, "problem": 1.00, "gap": 0.90, "method": 1.15,
    "results": 1.35, "limitation": 0.85, "conclusion": 0.95, "qa": 0.00, "backup": 0.00,
}

ROLE_CLAIM = {
    "hook": "Why this question matters: {q}",
    "problem": "The open problem is: {q}",
    "gap": "What earlier work left unresolved: {q}",
    "method": "Our approach: {q}",
    "results": "Key finding: {q}",
    "limitation": "Boundary of the evidence: {q}",
    "conclusion": "What the audience should take away: {q}",
}

ROLE_NOTE = {
    "hook": "Open with the stakes, not the methods. State why the audience should care before any technical term appears.",
    "problem": "Name the concrete failure or cost that makes this problem worth a talk. Keep it to one sentence on screen.",
    "gap": "Be explicit about what is missing from prior work, and say what evidence would close the gap.",
    "method": "Walk the pipeline in the order the audience needs it, not the order the paper lists it.",
    "results": "Read the figure left to right, call the effect size, then say how strong this evidence is.",
    "limitation": "Volunteer the limitation before the audience finds it; it protects every claim you made earlier.",
    "conclusion": "Repeat the single takeaway, then name the one decision or next step you want from this audience.",
}


# --------------------------------------------------------------------------- #
# case hint discovery (hidden cases name keys differently)
# --------------------------------------------------------------------------- #
def _walk(obj, depth=0, max_depth=3):
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), v
            yield from _walk(v, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:20]:
            yield from _walk(v, depth + 1, max_depth)


def _first_int(hints: dict, keys, lo=1, hi=200):
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if any(t in kl for t in keys):
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and lo <= float(v) <= hi:
                return int(float(v))
            if isinstance(v, str):
                m = re.search(r"\d+(?:\.\d+)?", v)
                if m and lo <= float(m.group()) <= hi:
                    return int(float(m.group()))
    return None


def _first_str(hints: dict, keys):
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if any(t in kl for t in keys) and isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _first_list(hints: dict, keys):
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if any(t in kl for t in keys) and isinstance(v, list) and v:
            return [str(x) for x in v if isinstance(x, (str, int, float))]
    return None


def case_hints(case: dict, materials: dict) -> dict:
    hints = dict(_walk(case or {}))
    slide_count = _first_int(hints, ("slide_count", "slides_count", "num_slides", "n_slides",
                                     "page_count", "pagecount", "slide_num", "slides"), 3, 200)
    minutes = _first_int(hints, ("minutes", "duration_min", "talk_min", "time_limit",
                                 "time_min", "length_min"), 1, 240)
    minutes_f = None
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if any(t in kl for t in ("minutes", "duration_min", "talk_min", "time_min")):
            if isinstance(v, (int, float, str)):
                m = re.search(r"\d+(?:\.\d+)?", str(v))
                if m and 0.5 <= float(m.group()) <= 240:
                    minutes_f = float(m.group())
                    break
    qa_minutes = None
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if "qa" in kl or "question" in kl:
            m = re.search(r"\d+(?:\.\d+)?", str(v))
            if m and 0.5 <= float(m.group()) <= 120:
                qa_minutes = float(m.group())
                break
    audience = _first_str(hints, ("audience", "listener", "听众", "目标受众")) or "committee"
    aud_id = "committee"
    al = audience.lower()
    for cand, toks in (("committee", ("committee", "defense", "答辩", "评委", "review")),
                       ("group-meeting", ("group", "组会", "lab", "weekly")),
                       ("collaborators", ("collaborator", "合作", "partner")),
                       ("public", ("public", "general", "大众", "科普"))):
        if any(t in al for t in toks):
            aud_id = cand
            break
    template = None
    for k, v in hints.items():
        kl = re.sub(r"[^a-z0-9]+", "_", k.lower())
        if isinstance(v, str) and v.lower().endswith((".pptx", ".potx", ".ppt")) and \
                any(t in kl for t in ("template", "master", "layout", "deck", "slide")):
            template = v.strip()
            break
    lang = "zh" if "zh" in str(_first_str(hints, ("language", "lang", "locale")) or "").lower() else "en"
    title = _first_str(hints, ("deck_title", "talk_title", "title")) or ""
    challenges = _first_list(hints, ("challenge", "objection", "concern", "risk", "question", "质疑", "追问"))
    goal = _first_str(hints, ("goal", "objective", "purpose", "aim")) or ""
    return {
        "slide_count": slide_count, "minutes": minutes_f or (float(minutes) if minutes else None),
        "qa_minutes": qa_minutes, "audience": aud_id, "audience_raw": audience,
        "template": template, "language": lang, "deck_title": title,
        "challenges": challenges or [], "goal": goal,
        "explicit_slide_count": slide_count is not None,
    }


def resolve_template(hints: dict, materials: dict, input_dir: Path) -> str | None:
    """Locate the organizer template pptx. Uses case hint, then real material files."""
    cand = hints.get("template")
    if cand:
        p = Path(cand)
        if not p.is_absolute():
            for base in (input_dir, input_dir / "materials", Path.cwd()):
                if (base / cand).exists():
                    return str((base / cand))
        elif p.exists():
            return str(p)
    for f in materials.get("files") or []:
        if str(f.get("path", "")).lower().endswith((".pptx", ".potx")):
            return f.get("abs") or f.get("path")
    for pat in ("*.pptx", "**/*.pptx"):
        found = sorted(input_dir.glob(pat)) if input_dir.exists() else []
        if found:
            return str(found[0])
    return None


# --------------------------------------------------------------------------- #
# evidence selection
# --------------------------------------------------------------------------- #
def _ledger_items(ledger: dict) -> list[dict]:
    out = []
    for it in (ledger or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        src = it.get("source") or {}
        if not src.get("locator"):
            continue
        out.append(it)
    return out


MAX_REUSE = 3  # one figure or number may legitimately back several slides
HARD_REUSE = 6  # absolute cap used only by the thin-material repair pass


def _pick(items, kinds, used, limit, max_reuse: int = MAX_REUSE):
    """Prefer never-cited evidence, then allow bounded reuse (thin material stays honest)."""
    picked: list[dict] = []
    for allow_reuse in (False, True):
        for it in items:
            if len(picked) >= limit:
                break
            if it.get("kind") not in kinds:
                continue
            cnt = used.get(it.get("id"), 0)
            if allow_reuse and cnt >= max_reuse:
                continue
            if not allow_reuse and cnt > 0:
                continue
            used[it.get("id")] = cnt + 1
            picked.append(it)
        if len(picked) >= limit:
            break
    return picked


def _clip(s: str, n: int) -> str:
    s = normalize_ws(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _role_sequence(n: int) -> list[str]:
    """Deterministic story arc that always contains problem/method/results/conclusion.

    Results pages are capped (a talk does not become N repeats of one finding); any
    remaining slots become `backup` reference pages rather than unsourced claims.
    """
    n = max(4, n)
    if n <= 6:
        base = ["hook", "problem", "method", "results", "limitation", "conclusion"]
        return base[:n]
    extra = max(0, n - 8)
    n_results = 1 + min(3, extra)
    seq = (["hook", "problem", "gap", "method"] + ["results"] * n_results
           + ["limitation", "conclusion", "qa"])
    if len(seq) < n:
        seq += ["backup"] * (n - len(seq))
    return seq[:n]


def autoplan(materials: dict, case: dict, template_index: dict | None,
             ledger: dict | None = None) -> dict:
    """Build a complete DeckSpec from real material evidence. Deterministic."""
    case_id = str((materials or {}).get("case_id") or (case or {}).get("case_id") or "case")
    input_dir = Path((materials or {}).get("input_dir") or "/input")
    hints = case_hints(case or {}, materials or {})
    items = _ledger_items(ledger or {})

    # Backfill the registered media hash from materials onto ledger figures, so the
    # evidence entry itself carries the hash gate A needs (no ambiguous id lookup).
    _by_media = {}
    for _f in (materials or {}).get("files") or []:
        for _fig in _f.get("figures") or []:
            _mp = str(_fig.get("media_path") or "")
            if _mp and _fig.get("media_sha256"):
                _by_media[_mp] = _fig["media_sha256"]
    for _it in items:
        _mp = str(_it.get("media_path") or "")
        if _mp and not _it.get("media_sha256") and _mp in _by_media:
            _it["media_sha256"] = _by_media[_mp]

    n_slides = hints["slide_count"] or 12
    n_slides = max(6, min(60, n_slides))
    minutes = hints["minutes"] or max(6.0, n_slides * 0.8)
    qa_minutes = hints["qa_minutes"] if hints["qa_minutes"] is not None else min(5.0, max(2.0, minutes * 0.15))

    # page 1 is the title slide, so the arc covers the remaining slides exactly
    roles = _role_sequence(max(5, n_slides - 1))
    role_map = (template_index or {}).get("role_map") or {}
    layout_names = [str(l.get("name")) for l in (template_index or {}).get("layouts") or []]

    def layout_for(role: str) -> str | None:
        """Always hand build.py an explicit layout that really exists in the master."""
        for cand in (role_map.get(role), role_map.get("backup"), role_map.get("closing"),
                     role_map.get("content"), role_map.get("section"), role_map.get("title")):
            if cand and cand in layout_names:
                return cand
        hints = {"title": ("title",), "qa": ("agenda", "closing", "blank", "title"),
                 "backup": ("closing", "blank", "agenda", "title"),
                 "two_col": ("two", "column", "comparison")}
        for token in hints.get(role, ()):  # role-specific name hints
            for name in layout_names:
                if token in name.lower():
                    return name
        for token in ("content", "result", "body", "text", "blank"):
            for name in layout_names:
                if token in name.lower():
                    return name
        return layout_names[0] if layout_names else None

    nums = [i for i in items if i.get("kind") == "number" and i.get("value") is not None]
    figs = [i for i in items if i.get("kind") == "figure"]

    # Figures whose caption never appears in the text are deliberately kept out of the
    # evidence ledger (an empty quote would be an E902).  They are still real figures, so
    # the planner may put them on a page; gate A verifies them by locator + media sha256
    # instead of by quote, and the missing caption is disclosed as uncertainty.
    ledger_media = {str(i.get("media_path") or "") for i in items}
    media_only: list[dict] = []
    for f in (materials or {}).get("files") or []:
        for fig in f.get("figures") or []:
            mp = str(fig.get("media_path") or "")
            if not mp or mp in ledger_media:
                continue
            media_only.append({
                "id": f"F{len(media_only) + 1}",
                "kind": "figure",
                "text": fig.get("caption") or "",
                "media_path": mp,
                "media_sha256": fig.get("media_sha256") or "",
                "uncaptioned": not (fig.get("caption") or "").strip(),
                "source": {
                    "file": str(f.get("path") or "").split("/")[-1],
                    "locator": fig.get("locator") or "",
                    "quote": fig.get("caption") or "",
                },
            })
    figs = figs + media_only
    tabs = [i for i in items if i.get("kind") == "table"]
    sects = [i for i in items if i.get("kind") == "section"]
    quotes = [i for i in items if i.get("kind") in ("quote", "section")]

    used: dict = {}
    evidence: list[dict] = []
    taken: dict[str, dict] = {}
    pages: list[dict] = []
    uncertainty: list[dict] = []
    limitations: list[str] = []

    # --- evidence items actually carried into the deck (only what is used) ---
    def take(it: dict, kind: str | None = None) -> dict:
        """Register (or re-use) exactly one evidence entry per ledger item id."""
        if it["id"] in taken:
            return taken[it["id"]]
        entry = {
            "id": it["id"],
            "kind": kind or it.get("kind") or "quote",
            "claim": _clip(it.get("text") or it.get("quote") or "", 120),
            "source": {
                "file": (it.get("source") or {}).get("file", ""),
                "locator": (it.get("source") or {}).get("locator", ""),
                "quote": (it.get("source") or {}).get("quote") or it.get("text") or "",
            },
        }
        if it.get("value") is not None:
            entry["value"] = it["value"]
        if it.get("unit"):
            entry["unit"] = it["unit"]
        if it.get("media_path"):
            entry["media_path"] = it["media_path"]
        if it.get("media_sha256"):
            entry["media_sha256"] = it["media_sha256"]
        evidence.append(entry)
        taken[it["id"]] = entry
        return entry

    # --- page 1: title ---
    title_text = hints["deck_title"] or _clip(
        (materials.get("files") or [{}])[0].get("title_hint") or
        _first_real_sentence(materials) or case_id, 120)
    pages.append({
        "id": "P1", "role": "title",
        "layout": layout_for("title"),
        "claim": "",
        "evidence_ids": [],
        "bullets": [title_text, hints.get("audience_raw")],
        "notes": {"say": f"Introduce the talk in one line: {_clip(title_text, 90)}. "
                         f"Then state the goal of this session and the time budget "
                         f"({minutes:g} minutes plus {qa_minutes:g} minutes of questions).",
                  "timing_s": 15, "checkpoint": "audience knows the goal"},
        "strength": "strong",
    })

    # --- allocate evidence across the arc (title slide already emitted) ---
    planned_roles = [r for r in roles if r != "title"]
    any_kinds = ("number", "figure", "table", "section", "quote", "text")
    groups: list[tuple[str, list]] = []
    for role in planned_roles:
        if role == "results":
            grp = _pick(figs, ("figure",), used, 1) + _pick(nums, ("number",), used, 2)
            if not grp:
                grp = _pick(nums, ("number",), used, 2)
        elif role in ("problem", "hook", "gap"):
            grp = _pick(quotes, ("quote", "section"), used, 2) or _pick(nums, ("number",), used, 1)
        elif role == "method":
            grp = _pick(sects, ("section",), used, 2) or _pick(nums, ("number",), used, 1)
        elif role == "limitation":
            grp = _pick(quotes, ("quote", "section"), used, 1)
        elif role == "conclusion":
            grp = _pick(nums, ("number",), used, 1) or _pick(quotes, ("quote",), used, 1)
        else:
            grp = _pick(quotes, ("quote",), used, 1)
        # Thin material: prefer the role's kind, but never leave a claim uncited while
        # any resolvable evidence item remains.  A real citation beats a bare assertion.
        if not grp and items:
            grp = _pick(items, any_kinds, used, 1)
        groups.append((role, grp))

    # --- repair pass: a claim slide with no citation is worse than a repeated citation ---
    for i, (role, grp) in enumerate(groups):
        if role in CONTENT_ROLES and not grp and items:
            it = min(items, key=lambda x: (used.get(x.get("id"), 0), str(x.get("id"))))
            if used.get(it.get("id"), 0) < HARD_REUSE:
                used[it["id"]] = used.get(it["id"], 0) + 1
                groups[i] = (role, [it])

    for role, grp in groups:
        if role in CONTENT_ROLES and not grp:
            role = "backup"  # nothing resolvable left: this slide cannot carry a claim
        pages.append(_make_page(f"P{len(pages) + 1}", role, grp, take, layout_for(role)))

    # --- shortfall: extra slides become backup (they are reference pages, not claims) ---
    for idx in range(len(pages), n_slides):
        pages.append(_make_page(f"P{idx + 1}", "backup", [], take, layout_for("backup")))

    # --- Q&A preparation ---
    qa = _make_qa(pages, evidence, hints)

    # --- timing: exact fit to the speaking budget ---
    _assign_timing(pages, minutes, qa_minutes)

    # --- honesty block ---
    uncaptioned_used = [e for e in evidence
                        if e.get("kind") == "figure" and not str(e.get("source", {}).get("quote") or "").strip()]
    if uncaptioned_used:
        uncertainty.append({
            "id": "U2", "what": f"{len(uncaptioned_used)} figure(s) on the deck carry no caption found in the text",
            "why": "the material contains the image but no matching 'Figure N.' caption line",
            "impact": "the figure is verified by file hash and locator, not by a verbatim caption",
        })
        limitations.append("Uncaptioned figures are bound by locator + media sha256 only; no caption text could be quoted.")
    n_backup = sum(1 for p in pages if p.get("role") == "backup")
    if n_backup:
        limitations.append(
            f"{n_backup} of {len(pages)} slides are backup/reference pages: the ingested material "
            f"did not yield enough distinct resolvable evidence for more claim slides.")
        uncertainty.append({
            "id": "U0", "what": f"{n_backup} slides carry no page-level claim",
            "why": "the evidence ledger ran out of distinct items that resolve into the material",
            "impact": "either ingest more source material or shorten the requested slide count",
        })
    if not items:
        uncertainty.append({
            "id": "U1", "what": "no machine-readable evidence could be extracted",
            "why": "ingest produced an empty evidence ledger",
            "impact": "claims are structural placeholders and must be authored from the raw files",
        })
        limitations.append("Autoplanned deck: no numeric evidence was resolved, so no number appears on any page.")
    if hints["slide_count"] is None:
        limitations.append("Slide count was not stated in case.json; the default plan length was used.")
    if hints["minutes"] is None:
        limitations.append("Speaking duration was not stated in case.json; timing is a default estimate.")
    if not (template_index or {}).get("layouts"):
        limitations.append("No template was resolved; layout names may not exist in the organizer master.")

    spec = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "case_id": case_id,
            "language": hints["language"],
            "deck_title": title_text,
            "aspect": ((template_index or {}).get("slide_size") or {}).get("aspect") or "16:9",
            "template": {"path": hints.get("template") or "", "sha256": (template_index or {}).get("sha256", "")},
            "audience": {
                "id": hints["audience"],
                "prior_knowledge": "medium",
                "concerns": hints["challenges"][:6],
                "expected_challenges": hints["challenges"][:6],
            },
            "goal": {
                "statement": hints["goal"] or f"Deliver an honest {minutes:g}-minute story of the study to a {hints['audience']} audience.",
                "success_checks": [
                    "every on-slide number resolves to a material span",
                    f"slide count equals the requested {n_slides}",
                    "each content slide carries one claim and non-duplicating notes",
                ],
            },
            "budget": {"minutes": float(minutes), "slide_count": n_slides, "qa_minutes": float(qa_minutes)},
            "story": {"arc": [r for r in roles if r in CONTENT_ROLES] or list(REQUIRED_ARC_ROLES),
                      "rationale": "hook→problem→gap→method→results→limitation→conclusion, "
                                   "ordered for the audience rather than the paper's section order."},
        },
        "theme": {
            "colors": {"primary": "#1F3864", "accent": "#C00000", "text": "#000000", "muted": "#595959"},
            "fonts": {**(template_index or {}).get("theme_fonts", {})} or {"heading": "Calibri Light", "body": "Calibri"},
            "source": "template" if template_index else "override",
        },
        "evidence": evidence,
        "pages": pages,
        "qa": qa,
        "uncertainty": uncertainty,
        "limitations": limitations,
        "change_records": [{"at": utc_stamp(), "what": "autoplan generated the initial DeckSpec",
                            "why": "deterministic no-LLM floor"}],
    }
    if template_index:
        spec["theme"]["fonts"].setdefault("heading", "Calibri Light")
        spec["theme"]["fonts"].setdefault("body", "Calibri")
    return ensure_shape(spec)


def _first_real_sentence(materials: dict) -> str:
    for f in materials.get("files") or []:
        text = normalize_ws(f.get("text") or "")
        if len(text) > 40:
            m = re.search(r"[^\n.!?。！？]{25,160}[.!?。！？]", text)
            if m:
                return m.group().strip()
            return text[:140]
    return ""


NOTE_FILLER = (
    "Say it in your own words; the slide is not the script.",
    "If the audience looks unconvinced, offer the missing measurement instead of repeating this line.",
    "Keep this inside the time budget; never read the slide aloud verbatim.",
)

BACKUP_SAY = ("Hold this slide in reserve. Do not present it unless a question forces the detail; "
              "if you do show it, state only what is on screen and stop.")
BACKUP_CLAIM = "Backup / appendix material — not part of the main narrative"


def _make_page(pid: str, role: str, grp: list[dict], take, layout: str | None) -> dict:
    evidence_ids = []
    bullets: list[str] = []
    figure = None
    for it in grp:
        entry = take(it)
        evidence_ids.append(entry["id"])
        if entry["kind"] == "figure" and figure is None and it.get("media_path"):
            # Caption is carried VERBATIM: gate E compares it against the material
            # caption, so clipping it here would manufacture a caption mismatch.
            figure = {"evidence_id": entry["id"], "media_path": it["media_path"],
                      "placement": "right", "caption": normalize_ws(it.get("text") or "")}
        elif entry.get("value") is not None:
            bullets.append(f"{_clip(str(entry['value']), 20)} {entry.get('unit', '')}".strip()
                           + (" — " + _clip(entry["source"]["quote"], 90) if entry["source"].get("quote") else ""))
        else:
            bullets.append(_clip(entry["source"].get("quote") or entry["claim"], 110))
    if not bullets and figure:
        bullets.append(_clip(figure["caption"], 110))
    if not bullets:
        bullets.append("[backup / appendix slide: no additional resolvable source material]" if role == "backup"
                       else f"[no resolvable source for this {role} slide — see limitations]")

    bullets = [b for b in bullets if b][:4]

    if role == "backup":
        claim = BACKUP_CLAIM
        say = BACKUP_SAY
        checkpoint = "held in reserve"
        strength = "preliminary"
    else:
        q = bullets[0] if bullets else role
        claim = normalize_ws(ROLE_CLAIM.get(role, "{q}").format(q=_clip(q, 110)))
        if len(claim) > 160:
            claim = claim[:159].rstrip() + "…"
        say = f"{ROLE_NOTE.get(role, 'State the single point of this slide, then hand off.')} " \
              f"Deliver the on-screen sentence once, then give the evidence behind it."
        for filler in NOTE_FILLER:
            if similarity(say, " ".join(bullets)) < NOTES_SIM_THRESHOLD:
                break
            say += " " + filler
        checkpoint = f"{role} landed"
        strength = _strength_of(grp)

    page = {
        "id": pid, "role": role,
        "layout": layout,
        "claim": claim,
        "evidence_ids": evidence_ids,
        "bullets": bullets,
        "notes": {"say": say, "timing_s": 0, "checkpoint": checkpoint},
        "strength": strength,
    }
    if figure:
        page["figure"] = figure
    return page


def _strength_of(grp: list[dict]) -> str:
    kinds = {g.get("kind") for g in grp}
    if "figure" in kinds and "number" in kinds:
        return "strong"
    if "figure" in kinds or "number" in kinds:
        return "moderate"
    if grp:
        return "mixed"
    return "preliminary"


def _make_qa(pages: list[dict], evidence: list[dict], hints: dict) -> list[dict]:
    qa: list[dict] = []
    challenge_slides = [p for p in pages if p.get("evidence_ids")]
    given = hints.get("challenges") or []
    defaults = [
        ("mechanism", "How do you know the effect is causal and not correlational?"),
        ("data", "How large is the sample, and does the claimed effect survive the uncertainty?"),
        ("control", "What did you compare against, and was that comparison fair?"),
        ("limitation", "Which result is the least reliable, and what would change your conclusion?"),
        ("other", "What is the single next experiment or decision you would ask for?"),
    ]
    pool = []
    for i, ch in enumerate(given[:6]):
        pool.append((_qa_category(ch), ch))
    for cat, q in defaults:
        if len(pool) >= 4:
            break
        pool.append((cat, q))
    for i, (cat, q) in enumerate(pool[:5]):
        target = challenge_slides[i % len(challenge_slides)] if challenge_slides else None
        ev = evidence[i % len(evidence)]["id"] if evidence else None
        ans = ("Answer from the slide that carries the evidence: "
               + _clip((target or {}).get("claim") or "state the boundary of the claim honestly", 140)
               + " If the material does not support it, say so and give the missing measurement instead.")
        qa.append({
            "id": f"Q{i + 1}", "question": q, "answer": ans,
            "evidence_ids": [ev] if ev else [],
            "slide_ids": [target["id"]] if target else [],
            "risk": "high" if i < 2 else ("medium" if i < 4 else "low"),
            "category": cat if cat in ("mechanism", "data", "control", "limitation", "other") else "other",
        })
    return qa


def _qa_category(text: str) -> str:
    t = text.lower()
    for cat, toks in (("mechanism", ("mechanism", "why", "how does", "pathway", "机制")),
                      ("data", ("data", "sample", "n=", "signal", "数据", "样本", "统计")),
                      ("control", ("control", "baseline", "compare", "对照", "基线")),
                      ("limitation", ("limit", "caveat", "weak", "局限", "边界", "不确定")),
                      ("other", ("next", "future", "决定", "下一步"))):
        if any(t in toks or tok in t for tok in toks):
            return cat
    return "other"


def _assign_timing(pages: list[dict], minutes: float, qa_minutes: float | None = None) -> None:
    """Set notes.timing_s so that the sum equals the *speaking* budget exactly.

    `budget.minutes` is the presenting time; `budget.qa_minutes` is a separate Q&A budget
    and is NOT part of the speaking total (see spec-schema.md).  Gate B compares
    Σ notes.timing_s against budget.minutes*60 within ±15%.
    """
    total = max(30, int(round(minutes * 60)))
    weights = [ROLE_TIME_WEIGHT.get(p.get("role"), 1.0) for p in pages]
    wsum = sum(weights) or 1.0
    raw = [w / wsum * total for w in weights]
    secs = [max(5, int(round(x))) for x in raw]
    drift = total - sum(secs)
    i = 0
    while drift != 0 and i < 1000:
        idx = i % len(secs)
        if drift > 0:
            secs[idx] += 1
            drift -= 1
        elif secs[idx] > 5:
            secs[idx] -= 1
            drift += 1
        i += 1
    for p, s in zip(pages, secs):
        p.setdefault("notes", {})["timing_s"] = s


# --------------------------------------------------------------------------- #
def load_ledger(work_dir: Path) -> dict:
    return load_json(work_dir / "evidence-ledger.json", {}) or {}


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="deckplan.py", description="deterministic DeckSpec autoplanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--work", required=True)
    p.add_argument("--input", default="/input")
    p.add_argument("--case", default=None, help="case.json path (default: <input>/case.json)")
    p.add_argument("--template-index", default=None)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "plan":
        work = Path(a.work)
        inp = Path(a.input)
        materials = load_json(work / "materials.json", {}) or {}
        if not materials:
            print(json.dumps({"ok": False, "reason": "materials.json missing; run ingest first"}))
            return 2
        case_path = Path(a.case) if a.case else (inp / "case.json")
        case = load_json(case_path, {}) or {}
        ti = load_json(a.template_index) if a.template_index else load_json(work / "template-index.json", {})
        spec = autoplan(materials, case, ti, load_ledger(work))
        save_json(a.out, spec)
        print(json.dumps({"ok": True, "out": a.out, "pages": len(spec["pages"]),
                          "evidence": len(spec["evidence"]), "qa": len(spec["qa"]),
                          "minutes": spec["meta"]["budget"]["minutes"]}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
