#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pt.py — one CLI for the whole academic-deck pipeline.

Frozen interface: CONTRACT.md §4/§7.

    python3 scripts/pt.py ingest  --input /input --work /app/work
    python3 scripts/pt.py plan    --work /app/work --input /input --out /app/work/spec.json
    python3 scripts/pt.py build   --spec /app/work/spec.json --work /app/work --out /app/output
    python3 scripts/pt.py result  --out /app/output --input /input --work /app/work
    python3 scripts/pt.py check   --out /app/output --spec /app/work/spec.json --work /app/work
    python3 scripts/pt.py run     --input /input --work /app/work --out /app/output

Exit codes: 0 ok / 1 a gate failed / 2 usage or IO / 3 artefacts missing / 4 illegal spec.
"""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from spec import (  # noqa: E402
    SCHEMA_VERSION, ensure_shape, load_json, map_contract, save_json, sha256_file,
    utc_stamp, validate_spec,
)

TEXT_ARTIFACTS = (
    "deck.pptx", "deck_plan.json", "speaker-notes.md", "evidence-map.json", "qa-prep.md",
    "deck.pdf", "render_manifest.json", "export_report.json", "validation_report.json",
)

_MODULE_CACHE: dict[str, object] = {}


class MissingModule(RuntimeError):
    pass


def _mod(name: str):
    """Import a sibling module lazily with a clear error if a peer has not shipped yet."""
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    try:
        m = importlib.import_module(name)
    except Exception as exc:  # pragma: no cover - depends on peer delivery
        raise MissingModule(f"module {name!r} unavailable: {type(exc).__name__}: {exc}") from exc
    _MODULE_CACHE[name] = m
    return m


def _fail(msg: str, code: int = 2) -> int:
    print(json.dumps({"ok": False, "reason": msg}, ensure_ascii=False))
    return code


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def step_ingest(input_dir: Path, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    case = load_json(input_dir / "case.json", {}) or {}
    ingest = _mod("ingest")
    summary = ingest.ingest(input_dir, work_dir, case=case)
    return {"case": case, "summary": summary}


def step_template(input_dir: Path, work_dir: Path, case: dict, materials: dict) -> dict:
    """Resolve the organizer template and index its masters/layouts."""
    deckplan = _mod("deckplan")
    template = _mod("template")
    hints = deckplan.case_hints(case, materials)
    path = deckplan.resolve_template(hints, materials, input_dir)
    index: dict = {}
    if path and Path(path).exists():
        try:
            index = template.inspect_template(Path(path))
            index["resolved_from"] = str(path)
        except Exception as exc:
            index = {"path": str(path), "error": f"{type(exc).__name__}: {exc}",
                     "layouts": [], "masters": [], "role_map": {}, "warnings": [str(exc)]}
    else:
        index = {"path": "", "layouts": [], "masters": [], "role_map": {},
                 "warnings": ["no template pptx found in /input"]}
    save_json(work_dir / "template-index.json", index)
    return index


def step_plan(input_dir: Path, work_dir: Path, case: dict, spec_out: Path) -> dict:
    deckplan = _mod("deckplan")
    materials = load_json(work_dir / "materials.json", {}) or {}
    ti = load_json(work_dir / "template-index.json", {}) or {}
    spec = deckplan.autoplan(materials, case, ti, deckplan.load_ledger(work_dir))
    spec["meta"]["template"] = {"path": ti.get("resolved_from") or ti.get("path") or "",
                                "sha256": ti.get("sha256", "")}
    save_json(spec_out, spec)
    return spec


def step_build(spec_path: Path, work_dir: Path, out_dir: Path) -> dict:
    build = _mod("build")
    spec = ensure_shape(load_json(spec_path, {}) or {})
    ti = load_json(work_dir / "template-index.json", {}) or {}
    issues = validate_spec(spec, ti or None)
    hard = [i for i in issues if i["code"] in ("S002", "S003", "S004", "S010", "S011", "S012")]
    if hard:
        print(json.dumps({"ok": False, "reason": "spec rejected", "issues": hard}, ensure_ascii=False))
        return {"ok": False, "exit": 4, "issues": issues}
    return build.build(spec, work_dir, out_dir, template_index=ti or None)


def step_render(out_dir: Path, dpi: int = 144) -> dict:
    render = _mod("render")
    pptx = out_dir / "deck.pptx"
    if not pptx.exists():
        return {"ok": False, "reason": "deck.pptx missing"}
    return render.render_deck(pptx, out_dir, dpi=dpi)


def _tools_report(out_dir: Path, work_dir: Path) -> dict:
    tools: dict = {}
    try:
        import pptx  # noqa: F401
        tools["python_pptx"] = getattr(pptx, "__version__", "?")
    except Exception:
        tools["python_pptx"] = "unavailable"
    rm = load_json(out_dir / "render_manifest.json", {}) or {}
    tools["renderer"] = rm.get("renderer") or ("unavailable" if not (out_dir / "deck.pdf").exists() else "unknown")
    tools["render_dpi"] = rm.get("dpi")
    tools["missing_fonts"] = rm.get("missingFonts") or []
    ti = load_json(work_dir / "template-index.json", {}) or {}
    tools["template_layouts"] = len(ti.get("layouts") or [])
    tools["template_resolved"] = bool(ti.get("resolved_from") or ti.get("path"))
    return tools


def write_result(out_dir: Path, input_dir: Path, work_dir: Path,
                 status_override: str | None = None) -> dict:
    """Compose /app/output/result.json. Never leaves an output_contract key empty."""
    case = load_json(input_dir / "case.json", {}) or {}
    spec = ensure_shape(load_json(work_dir / "spec.json", {}) or {})
    plan = load_json(out_dir / "deck_plan.json", {}) or {}
    vreport = load_json(out_dir / "validation_report.json", {}) or {}
    materials = load_json(work_dir / "materials.json", {}) or {}
    timing = load_json(work_dir / "run-timing.json", {}) or {}
    rm = load_json(out_dir / "render_manifest.json", {}) or {}

    artifacts: dict = {}
    # Self-referential files are excluded: hashing a report we are about to rewrite is
    # always stale, and it made `pt.py check` non-idempotent (gate H failed on run 2+).
    _SELF_REF = {"result.json", "validation_report.json"}
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out_dir).as_posix()
            if rel in _SELF_REF:
                continue
            artifacts[rel] = {"path": rel, "rel": rel, "bytes": p.stat().st_size,
                              "sha256": sha256_file(p)}

    gates = vreport.get("gates") or []
    n_fail = sum(1 for g in gates if g.get("status") == "fail")
    n_warn = sum(1 for g in gates if g.get("status") == "warn")
    n_pass = sum(1 for g in gates if g.get("status") == "pass")

    names = {k for k in artifacts}
    unmapped_contract: list[str] = []
    ctx = {
        "out_dir": out_dir,
        "artifacts": {Path(v["path"]).name: dict(v) for v in artifacts.values()},
        "status": "pending",
        "slide_count": len(spec.get("pages") or []),
        "evidence_count": len(spec.get("evidence") or []),
        "gates": gates,
    }
    ctx["summary_text"] = ""

    # status resolution (CONTRACT §5 gate J)
    status = "ok"
    if status_override:
        status = status_override
    elif n_fail:
        status = "partial"
    elif n_warn:
        status = "partial"
    if not (out_dir / "deck.pptx").exists():
        status = "failed" if not status_override else status_override
    if spec.get("uncertainty"):
        # uncertainty alone does not degrade, but is always disclosed
        pass
    ctx["status"] = status
    ctx["summary_text"] = (
        f"{spec.get('meta', {}).get('deck_title') or plan.get('deck_title') or 'deck'}: "
        f"{len(spec.get('pages') or [])} slides for "
        f"{spec.get('meta', {}).get('budget', {}).get('minutes')} min; "
        f"{len(spec.get('evidence') or [])} evidence items, "
        f"{len(spec.get('qa') or [])} prepared Q&A; status={status}; "
        f"gates pass={n_pass} warn={n_warn} fail={n_fail}"
    )

    contract_values, unmapped_contract = map_contract(case, ctx)

    # unfilled contract keys (should be empty: map_contract always yields non-empty)
    unfilled = [k for k, v in contract_values.items() if not str(v or "").strip()]

    evidence_rows = []
    for e in spec.get("evidence") or []:
        src = e.get("source") or {}
        evidence_rows.append({
            "id": e.get("id"), "kind": e.get("kind"), "claim": e.get("claim"),
            "value": e.get("value"), "unit": e.get("unit"),
            "source": {"file": src.get("file"), "locator": src.get("locator"),
                       "quote": src.get("quote")},
            "verified": bool(src.get("locator")),
        })

    page_map = plan.get("pages") or [
        {"slide_id": p.get("id"), "role": p.get("role"), "layout": p.get("layout"),
         "claim": p.get("claim"), "evidence_ids": p.get("evidence_ids"),
         "strength": p.get("strength")} for p in (spec.get("pages") or [])
    ]

    result: dict = {
        "case_id": str(case.get("case_id") or spec.get("meta", {}).get("case_id") or "unknown"),
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "deck_title": spec.get("meta", {}).get("deck_title") or plan.get("deck_title"),
        "audience": spec.get("meta", {}).get("audience"),
        "budget": spec.get("meta", {}).get("budget"),
        "artifacts": artifacts,
        "sources": [
            {"file": f.get("path"), "kind": f.get("kind"), "sha256": f.get("sha256"),
             "bytes": f.get("bytes"), "parsed": f.get("parsed"),
             "used": f.get("parsed") is True}
            for f in (materials.get("files") or [])
        ],
        "evidence": evidence_rows,
        "page_map": page_map,
        "qa": [{"id": q.get("id"), "question": q.get("question"),
                "slide_ids": q.get("slide_ids"), "risk": q.get("risk"),
                "category": q.get("category")} for q in (spec.get("qa") or [])],
        "uncertainty": spec.get("uncertainty") or [],
        "limitations": spec.get("limitations") or [],
        "change_records": spec.get("change_records") or [],
        "audit": {
            "gates": gates,
            "checks_passed": n_pass, "checks_warned": n_warn, "checks_failed": n_fail,
            "unfilled_fields": unfilled,
            "unmapped_contract_keys": unmapped_contract,
            "determinism": (load_json(work_dir / "determinism.json", {}) or {}).get("result", "unknown"),
            "render": {"page_count": rm.get("pageCount"), "dpi": rm.get("dpi"),
                       "missing_fonts": rm.get("missingFonts") or [],
                       "renderer": rm.get("renderer")},
            "spec_issues": validate_spec(spec, load_json(work_dir / "template-index.json", {}) or None),
        },
        "runtime": {
            "seconds": timing.get("seconds"),
            "tools": _tools_report(out_dir, work_dir),
            "generated_at": utc_stamp(),
        },
    }
    for k, v in contract_values.items():
        result[k] = v

    # short human summary for reviewers
    result["summary"] = ctx["summary_text"]

    save_json(out_dir / "result.json", result)
    return result


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_ingest(a) -> int:
    r = step_ingest(Path(a.input), Path(a.work))
    print(json.dumps({"ok": True, **r["summary"]}, ensure_ascii=False))
    return 0


def cmd_inspect(a) -> int:
    """Resolve the organizer template and index its masters/layouts (documented step 2)."""
    inp, work = Path(a.input), Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    case = load_json(inp / "case.json", {}) or {}
    materials = load_json(work / "materials.json", {}) or {}
    if a.template:
        ti = _index_one(Path(a.template))
    else:
        ti = step_template(inp, work, case, materials)
    if a.out and a.template:
        save_json(Path(a.out), ti)
    print(json.dumps({"ok": bool(ti.get("layouts")), "path": ti.get("resolved_from") or ti.get("path"),
                      "layouts": [l.get("name") for l in ti.get("layouts") or []],
                      "role_map": ti.get("role_map"), "warnings": ti.get("warnings") or []},
                     ensure_ascii=False))
    return 0 if ti.get("layouts") else 2


def _index_one(path: Path) -> dict:
    template = _mod("template")
    if not path.exists():
        return {"path": str(path), "layouts": [], "masters": [], "role_map": {},
                "warnings": [f"template not found: {path}"]}
    try:
        ti = template.inspect_template(path)
        ti["resolved_from"] = str(path)
        return ti
    except Exception as exc:
        return {"path": str(path), "layouts": [], "masters": [], "role_map": {},
                "warnings": [f"{type(exc).__name__}: {exc}"]}


def cmd_plan(a) -> int:
    work, inp = Path(a.work), Path(a.input)
    case = load_json(inp / "case.json", {}) or {}
    materials = load_json(work / "materials.json", {}) or {}
    if not materials:
        return _fail("materials.json missing; run `pt.py ingest` first")
    if a.template_index:
        ti = load_json(a.template_index, {}) or {}
        if not ti:
            return _fail(f"template-index unreadable: {a.template_index}")
        save_json(work / "template-index.json", ti)
    else:
        step_template(inp, work, case, materials)
    spec = step_plan(inp, work, case, Path(a.out))
    print(json.dumps({"ok": True, "out": a.out, "pages": len(spec["pages"]),
                      "evidence": len(spec["evidence"]), "qa": len(spec["qa"])}, ensure_ascii=False))
    return 0


def cmd_build(a) -> int:
    r = step_build(Path(a.spec), Path(a.work), Path(a.out))
    if r.get("exit") == 4:
        return 4
    print(json.dumps({"ok": bool(r.get("ok")), "slides": r.get("slides"),
                      "artifacts": list((r.get("artifacts") or {}).keys()),
                      "warnings": r.get("warnings") or []}, ensure_ascii=False))
    return 0 if r.get("ok") else 2


def cmd_render(a) -> int:
    r = step_render(Path(a.out), dpi=int(a.dpi))
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 2


def cmd_result(a) -> int:
    r = write_result(Path(a.out), Path(a.input), Path(a.work), status_override=a.status)
    print(json.dumps({"ok": True, "status": r["status"],
                      "contract_keys": [k for k in r if k not in
                                        ("case_id", "schema_version", "status", "artifacts", "sources",
                                         "evidence", "page_map", "qa", "uncertainty", "limitations",
                                         "change_records", "audit", "runtime", "summary",
                                         "deck_title", "audience", "budget")]}, ensure_ascii=False))
    return 0


def cmd_check(a) -> int:
    validate = _mod("validate")
    out_dir = Path(a.out)
    if not (out_dir / "deck.pptx").exists():
        return _fail("deck.pptx missing; run build first", 3)
    rep = validate.run_gates(out_dir, Path(a.spec), Path(a.work))
    print(json.dumps({"exit": rep.get("exit"), "gates": rep.get("gates")}, ensure_ascii=False))
    return int(rep.get("exit", 2))


def cmd_edit(a) -> int:
    spec = ensure_shape(load_json(a.spec, {}) or {})
    edits = load_json(a.edits, {}) or {}
    _merge(spec, edits)
    spec.setdefault("change_records", []).append({
        "at": utc_stamp(), "what": f"applied {len(edits)} edit key(s): {sorted(edits)[:8]}",
        "why": str((edits.get("_why") if isinstance(edits, dict) else "") or "spec revision"),
    })
    if isinstance(spec.get("change_records"), list) and spec["change_records"] and "_why" in edits:
        spec["change_records"][-1]["why"] = str(edits["_why"])
    save_json(a.out, spec)
    print(json.dumps({"ok": True, "out": a.out, "pages": len(spec.get("pages") or [])}, ensure_ascii=False))
    return 0


def _merge(dst: dict, patch: dict) -> None:
    for k, v in (patch or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def cmd_run(a) -> int:
    """Full chain: ingest → template → plan → build → render → result → check → result."""
    t0 = time.time()
    inp, work, out = Path(a.input), Path(a.work), Path(a.out)
    for d in (work, out):
        d.mkdir(parents=True, exist_ok=True)

    r = step_ingest(inp, work)
    case = r["case"]
    materials = load_json(work / "materials.json", {}) or {}

    ti = step_template(inp, work, case, materials)

    spec_path = work / "spec.json"
    if a.respec or not spec_path.exists():
        step_plan(inp, work, case, spec_path)
    spec = ensure_shape(load_json(spec_path, {}) or {})

    br = step_build(spec_path, work, out)
    if br.get("exit") == 4:
        write_result(out, inp, work, status_override="needs_human")
        return 4

    rr = step_render(out)

    # determinism evidence for gate G
    det = _determinism_probe(spec_path, work, out)
    save_json(work / "determinism.json", det)

    write_result(out, inp, work)
    vreport = {"gates": [], "exit": 1}
    try:
        validate = _mod("validate")
        vreport = validate.run_gates(out, spec_path, work)
    except MissingModule as exc:
        print(json.dumps({"warning": str(exc)}, ensure_ascii=False))
    res = write_result(out, inp, work)

    save_json(work / "run-timing.json", {"seconds": round(time.time() - t0, 2),
                                         "finished_at": utc_stamp()})
    res = write_result(out, inp, work)

    n_fail = sum(1 for g in (vreport.get("gates") or []) if g.get("status") == "fail")
    print(json.dumps({"ok": n_fail == 0, "exit": vreport.get("exit"),
                      "status": res["status"], "slides": len(spec.get("pages") or []),
                      "contract_unmapped": res["audit"]["unmapped_contract_keys"],
                      "render_ok": bool(rr.get("ok")),
                      "seconds": round(time.time() - t0, 2),
                      "gates": {g["id"]: g["status"] for g in (vreport.get("gates") or [])}},
                     ensure_ascii=False))
    return int(vreport.get("exit", 2))


def _determinism_probe(spec_path: Path, work: Path, out: Path) -> dict:
    """Rebuild once into a scratch dir and compare normalized sha256."""
    try:
        build = _mod("build")
        spec = ensure_shape(load_json(spec_path, {}) or {})
        ti = load_json(work / "template-index.json", {}) or {}
        scratch = work / "_determinism"
        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(parents=True, exist_ok=True)
        build.build(spec, work, scratch, template_index=ti or None)
        a = scratch / "deck.pptx"
        b = out / "deck.pptx"
        if not (a.exists() and b.exists()):
            return {"result": "unknown", "reason": "deck.pptx missing on one side"}
        ha, hb = sha256_file(a), sha256_file(b)
        return {"result": "byte-identical" if ha == hb else "DIFFERENT",
                "sha256_a": ha, "sha256_b": hb}
    except Exception as exc:
        return {"result": "unknown", "reason": f"{type(exc).__name__}: {exc}"}


def cmd_selftest(a) -> int:
    return int(_mod("selftest").main([]) if hasattr(_mod("selftest"), "main") else _mod("selftest").cli([]))


def cmd_contract_matrix(a) -> int:
    return int(_mod("contract_matrix").cli([]))


def cmd_gain_check(a) -> int:
    return int(_mod("gain_check").cli([]))


# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pt.py", description="academic deck organizer pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest"); p.add_argument("--input", required=True); p.add_argument("--work", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("--input", required=True); p.add_argument("--work", required=True)
    p.add_argument("--template", default=None); p.add_argument("--out", default=None)
    p = sub.add_parser("plan")
    p.add_argument("--work", required=True); p.add_argument("--input", required=True)
    p.add_argument("--out", required=True); p.add_argument("--template-index", default=None)
    p = sub.add_parser("build")
    p.add_argument("--spec", required=True); p.add_argument("--work", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("render")
    p.add_argument("--out", required=True); p.add_argument("--dpi", default="144")
    p = sub.add_parser("result")
    p.add_argument("--out", required=True); p.add_argument("--input", required=True)
    p.add_argument("--work", required=True); p.add_argument("--status", default=None)
    p = sub.add_parser("check")
    p.add_argument("--out", required=True); p.add_argument("--spec", required=True); p.add_argument("--work", required=True)
    p = sub.add_parser("edit")
    p.add_argument("--spec", required=True); p.add_argument("--edits", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("run")
    p.add_argument("--input", required=True); p.add_argument("--work", required=True)
    p.add_argument("--out", required=True); p.add_argument("--respec", action="store_true",
                                                           help="re-plan even if work/spec.json exists")
    p = sub.add_parser("selftest")
    p = sub.add_parser("contract_matrix")
    p = sub.add_parser("gain_check")

    a = ap.parse_args(argv)
    fn = {
        "ingest": cmd_ingest, "inspect": cmd_inspect, "plan": cmd_plan, "build": cmd_build,
        "render": cmd_render, "result": cmd_result, "check": cmd_check, "run": cmd_run,
        "edit": cmd_edit, "selftest": cmd_selftest, "contract_matrix": cmd_contract_matrix,
        "gain_check": cmd_gain_check,
    }[a.cmd]
    try:
        return int(fn(a))
    except MissingModule as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
