#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — the Skill's own regression battery.

Runs a synthetic case end-to-end and then injects real defects one at a time, asserting
that the matching gate catches each one.  A gate that cannot fail is not a gate.

    python3 scripts/selftest.py            # exit 0 when every check passes
    python3 scripts/selftest.py --keep     # keep the scratch tree for inspection

Scratch tree: <work>/verify/_selftest  (never inside the delivery tree).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})


def scratch_root() -> Path:
    """Always an ABSOLUTE path outside the delivery tree.

    The platform installs this Skill at /skills/weekly-skill, where the ancestor directory
    is not called "work" — an earlier version returned a CWD-relative path in that layout and
    the fixture/output halves landed in different directories (selftest then read empty dirs).
    """
    env = os.environ.get("DECK_SELFTEST_SCRATCH")
    if env:
        p = Path(env)
    else:
        p = Path(tempfile.gettempdir()) / "deck-selftest"
    p = p.resolve()
    here = Path(__file__).resolve()
    if here.is_relative_to(p) or p.is_relative_to(HERE.parent):
        # never let scratch live inside, or contain, the delivery tree
        p = Path(tempfile.gettempdir()) / "deck-selftest-fallback"
    return p


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #
MD = """# Tidal stress drives microbial methane release in shelf sediments

## Abstract
We measured methane flux across a shelf transect and found a 2.8 fold increase in
flux where tidal stress amplitude exceeded 0.42 kPa. The effect persisted after
correcting for temperature (p = 0.003, n = 46 cores).

## Methods
Cores were collected at 12 stations along a 140 km transect. Flux was measured with
closed-chamber incubations over 36 h. Tidal stress amplitude was modelled from a
regional harmonic model.

## Results
Flux increased 2.8 fold between low-stress and high-stress stations (95% CI 2.1-3.6).
Temperature explained 14% of the variance while tidal stress explained 51%.
The relationship weakened below 0.15 kPa stress amplitude.

## Limitations
The transect is single-season and single-region, so seasonal reversals cannot be
excluded. Four stations had incomplete chamber records.

Figure 1. Methane flux versus tidal stress amplitude across 12 stations.
Table 1. Station-level flux, temperature and modelled tidal stress.
"""

CSV = """station,flux_mmol_m2_d,stress_kpa,temperature_c
S01,1.20,0.08,4.1
S02,1.45,0.12,4.4
S03,3.36,0.55,4.2
S04,3.80,0.61,4.6
"""

NOTES_JSON = json.dumps({
    "figure_captions": {"fig_flux": "Figure 1. Methane flux versus tidal stress amplitude across 12 stations."},
    "key_numbers": [
        {"label": "fold increase", "value": 2.8, "unit": "fold"},
        {"label": "tidal stress threshold", "value": 0.42, "unit": "kPa"},
        {"label": "cores", "value": 46, "unit": "cores"},
    ],
}, ensure_ascii=False)

CASE = {
    "case_id": "synthetic-001",
    "audience": "thesis committee",
    "slide_count": 12,
    "minutes": 10,
    "qa_minutes": 3,
    "language": "en",
    "template": "slides-template.pptx",
    "goal": "convince the committee the tidal-stress effect is real and worth a second season",
    "expected_challenges": [
        "Could temperature explain the whole effect?",
        "Is n = 46 enough to support a 2.8 fold claim?",
    ],
    "output_contract": {
        "editable_slides": "the editable deck",
        "speaker_notes": "notes",
        "evidence_map": "page level provenance",
        "qa_prep": "prepared answers",
        "preview_images": "rendered previews",
        "pdf_export": "exported pdf",
        "run_summary": "one paragraph",
        "zzz_custom_key": "an unseen key the organizer invented",
    },
}


def build_fixture(root: Path) -> Path:
    inp = root / "input"
    mats = inp / "materials"
    mats.mkdir(parents=True, exist_ok=True)
    (inp / "case.json").write_text(json.dumps(CASE, ensure_ascii=False, indent=2), encoding="utf-8")
    (inp / "task.md").write_text("Synthetic self-test case. Not an official case.\n", encoding="utf-8")
    (mats / "paper.md").write_text(MD, encoding="utf-8")
    (mats / "table.csv").write_text(CSV, encoding="utf-8")
    (mats / "notes.json").write_text(NOTES_JSON, encoding="utf-8")

    from PIL import Image, ImageDraw
    for i in (1, 2):
        img = Image.new("RGB", (900, 600), "white")
        d = ImageDraw.Draw(img)
        for k in range(12):
            d.line((80 + k * 62, 560, 80 + k * 62, 560 - (k * 34) % 400), fill="black", width=6)
        d.text((20, 20), f"Figure {i}", fill="black")
        img.save(mats / f"fig{i}.png")

    _write_pdf(mats / "paper.pdf")
    _write_template(mats / "slides-template.pptx")
    return inp


def _write_pdf(path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.06, 0.94, "Tidal stress drives microbial methane release", fontsize=13)
    fig.text(0.06, 0.88, "We found a 2.8 fold increase in methane flux where tidal stress", fontsize=9)
    fig.text(0.06, 0.85, "amplitude exceeded 0.42 kPa (p = 0.003, n = 46 cores).", fontsize=9)
    fig.text(0.06, 0.80, "Methods: 12 stations along a 140 km transect; 36 h incubations.", fontsize=9)
    fig.text(0.06, 0.75, "Limitations: single season, single region; 4 stations incomplete.", fontsize=9)
    fig.text(0.06, 0.70, "Figure 1. Methane flux versus tidal stress amplitude.", fontsize=9)
    fig.savefig(path)
    plt.close(fig)


def _write_template(path: Path) -> None:
    """A real master/layout based template: rename the default layouts to the organizer's."""
    from pptx import Presentation
    prs = Presentation()
    prs.slide_width = 12192000
    prs.slide_height = 6858000
    wanted = ["Title Slide", "Agenda", "Two Column", "Key Result", "Limitation",
              "Closing", "Blank"]
    for i, layout in enumerate(prs.slide_layouts):
        if i < len(wanted):
            layout.name = wanted[i]
    prs.save(path)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run_pt(args: list[str], cwd: Path | None = None) -> tuple[int, dict | str]:
    proc = subprocess.run([sys.executable, str(HERE / "pt.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(cwd or HERE))
    out = (proc.stdout or "").strip()
    try:
        parsed = json.loads(out.splitlines()[-1]) if out else {}
    except Exception:
        parsed = out[-800:]
    return proc.returncode, parsed


def gate_status(report: dict, gid: str) -> str:
    for g in report.get("gates") or []:
        if g.get("id") == gid:
            return str(g.get("status"))
    return "missing"


def load(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, obj) -> None:
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# main battery
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    global RESULTS
    RESULTS = []

    root = scratch_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    inp = build_fixture(root)
    work = root / "work"
    out = root / "out"

    # ---- module availability
    pipeline_mods = ["spec", "deckplan", "ingest", "evidence", "template", "build", "render",
                     "measure", "normalize", "validate", "contract_matrix", "pt", "selftest"]
    extra_mods = ["gain_check"]
    missing = []
    for m in pipeline_mods + extra_mods:
        try:
            __import__(m)
        except Exception as exc:
            missing.append(f"{m}({type(exc).__name__})")
    blocking = [m for m in missing if m.split("(")[0] in pipeline_mods]
    record("imports-all-modules", not missing,
           f"missing: {missing}" if missing else f"{len(pipeline_mods) + len(extra_mods)} modules import")

    # ---- contract matrix
    try:
        import contract_matrix
        rows = contract_matrix.run()
        bad = [r for r in rows if r["status"] != "pass"]
        record("contract-matrix", not bad, f"{len(rows) - len(bad)}/{len(rows)} key sets pass")
    except Exception as exc:
        record("contract-matrix", False, f"{type(exc).__name__}: {exc}")

    # ---- autoplan geometry (pure, no peers needed)
    try:
        import deckplan
        materials = {"case_id": "synthetic-001", "input_dir": str(inp), "files": []}
        spec = deckplan.autoplan(materials, CASE, {"layouts": [{"name": n} for n in
                                                    ["Title Slide", "Agenda", "Two Column", "Key Result",
                                                     "Limitation", "Closing", "Blank"]],
                                                    "role_map": {"title": "Title Slide",
                                                                 "content": "Key Result",
                                                                 "closing": "Closing"}})
        rec = deckplan._role_sequence(12)
        record("autoplan-arc-complete",
               all(r in rec for r in ("problem", "method", "results", "conclusion")),
               f"arc={rec}")
        record("autoplan-slide-count", len(spec["pages"]) == CASE["slide_count"],
               f"{len(spec['pages'])} pages vs budget {CASE['slide_count']}")
        import spec as specmod
        issues = specmod.validate_spec(spec, None)
        hard = [i for i in issues if i["code"] in ("S014", "S015", "S023", "S024", "S026", "S027", "S029", "S030")]
        record("autoplan-spec-valid", not hard, f"{len(issues)} issue(s), hard={len(hard)}")
        total_t = sum(p["notes"]["timing_s"] for p in spec["pages"])
        want = CASE["minutes"] * 60
        record("autoplan-timing-fit", abs(total_t - want) <= 0.15 * want,
               f"sum={total_t}s vs {want:.0f}s budget")
    except Exception as exc:
        record("autoplan-arc-complete", False, f"{type(exc).__name__}: {exc}")
        record("autoplan-slide-count", False, "blocked by autoplan error")
        record("autoplan-spec-valid", False, "blocked by autoplan error")
        record("autoplan-timing-fit", False, "blocked by autoplan error")

    if blocking:
        record("end-to-end-run", False, f"skipped: missing modules {blocking}")
        record("gates-all-pass", False, "skipped")
        record("result-contract-filled", False, "skipped")
        record("render-artifacts", False, "skipped")
        _report(a)
        return 1

    # ---- end to end
    rc, summary = run_pt(["run", "--input", str(inp), "--work", str(work), "--out", str(out)])
    record("end-to-end-run", rc == 0, f"exit={rc} summary={summary}")
    repo = load(out / "validation_report.json", {}) or {}
    record("gates-all-pass", rc == 0 and not [g for g in repo.get("gates") or [] if g.get("status") == "fail"],
           "gates=" + json.dumps({g["id"]: g["status"] for g in repo.get("gates") or []}, ensure_ascii=False))
    res = load(out / "result.json", {}) or {}
    contract = CASE["output_contract"]
    empty = [k for k in contract if not str(res.get(k) or "").strip()]
    record("result-contract-filled", not empty, f"empty={empty}; unmapped={res.get('audit', {}).get('unmapped_contract_keys')}")
    record("render-artifacts",
           (out / "deck.pptx").exists() and (out / "render_manifest.json").exists(),
           f"pdf={ (out / 'deck.pdf').exists() } previews={len(list((out / 'preview').glob('*.png'))) if (out / 'preview').exists() else 0}")
    record("determinism-recorded", (load(work / "determinism.json", {}) or {}).get("result") == "byte-identical",
           str((load(work / "determinism.json", {}) or {}).get("result")))
    record("evidence-resolved", all(e.get("verified") for e in res.get("evidence") or []) if res.get("evidence") else False,
           f"{len(res.get('evidence') or [])} evidence items")
    record("notes-non-duplicating",
           all(p.get("notes", {}).get("say") for p in (load(out / "deck_plan.json", {}) or {}).get("pages") or []),
           "every slide carries notes")

    # ---- negative injections -------------------------------------------------
    if not (out / "deck.pptx").exists():
        record("negative-suite", False, "no deck to mutate")
        _report(a)
        return 1

    neg_root = root / "neg"
    neg_root.mkdir(exist_ok=True)
    base_spec = load(work / "spec.json", {}) or {}
    base_materials = load(work / "materials.json", {}) or {}
    import validate as vmod

    def neg(name: str, gid: str, mutate, rebuild: bool = True) -> None:
        """Rebuild the deck from the mutated spec so artefact-reading gates see the defect."""
        nw = neg_root / name
        nw.mkdir(parents=True, exist_ok=True)
        no = nw / "out"
        if no.exists():
            shutil.rmtree(no)
        shutil.copytree(out, no)
        spec = json.loads(json.dumps(base_spec))
        mutate(spec, no, nw)
        sp = nw / "spec.json"
        save(sp, spec)
        save(nw / "materials.json", base_materials)
        if (work / "template-index.json").exists():
            shutil.copy(work / "template-index.json", nw / "template-index.json")
        if rebuild:
            run_pt(["build", "--spec", str(sp), "--work", str(nw), "--out", str(no)])
        try:
            rep = vmod.run_gates(no, sp, nw)
        except Exception as exc:
            record(f"negative-{name}", False, f"gates crashed: {type(exc).__name__}: {exc}")
            return
        st = gate_status(rep, gid)
        record(f"negative-{name}", st == "fail", f"gate {gid}={st} (want fail); exit={rep.get('exit')}")

    def first_content(spec):
        for p in spec["pages"]:
            if p.get("role") not in ("title", "qa", "backup"):
                return p
        return spec["pages"][-1]

    def fabricated_number(spec, no, nw):
        ev = spec["evidence"][0] if spec["evidence"] else None
        if ev is None:
            ev = {"id": "E1", "kind": "number", "claim": "x",
                  "source": {"file": "paper.md", "locator": "paper.md#L1", "quote": "x"}}
            spec["evidence"].append(ev)
        ev["value"] = 999.0
        first_content(spec)["evidence_ids"] = [ev["id"]]

    def bad_quote(spec, no, nw):
        spec["evidence"][0]["source"]["quote"] = "this sentence is not in any material at all"

    def dangling_evidence_id(spec, no, nw):
        first_content(spec)["evidence_ids"] = ["E_DOES_NOT_EXIST"]

    def page_count_mismatch(spec, no, nw):
        spec["meta"]["budget"]["slide_count"] = len(spec["pages"]) + 3

    def notes_duplicate(spec, no, nw):
        p = first_content(spec)
        p["notes"]["say"] = " ".join(p["bullets"])

    def missing_notes(spec, no, nw):
        first_content(spec)["notes"] = {}

    def missing_figure_media(spec, no, nw):
        p = first_content(spec)
        p["figure"] = {"evidence_id": p["evidence_ids"][0] if p["evidence_ids"] else "",
                       "media_path": "materials/does-not-exist.png",
                       "placement": "right", "caption": "figure"}
        if p["figure"]["evidence_id"] and p["figure"]["evidence_id"] not in p["evidence_ids"]:
            p["evidence_ids"].append(p["figure"]["evidence_id"])

    def pdf_page_mismatch(spec, no, nw):
        # truncate the recorded page count so it disagrees with deck.pptx
        rm = load(no / "render_manifest.json", {}) or {}
        rm["pageCount"] = max(1, int(rm.get("pageCount") or 1) + 2)
        save(no / "render_manifest.json", rm)

    def contract_key_empty(spec, no, nw):
        r = load(no / "result.json", {}) or {}
        r["zzz_custom_key"] = ""
        save(no / "result.json", r)

    def too_few_qa(spec, no, nw):
        spec["qa"] = spec["qa"][:1]

    def dangling_qa_slide(spec, no, nw):
        if spec["qa"]:
            spec["qa"][0]["slide_ids"] = ["P_NOT_REAL"]

    neg("fabricated-number", "A", fabricated_number)
    neg("quote-not-verbatim", "A", bad_quote)
    neg("dangling-evidence-id", "A", dangling_evidence_id)
    neg("page-count-mismatch", "B", page_count_mismatch)
    neg("notes-duplicate-bullets", "D", notes_duplicate)
    neg("missing-notes", "D", missing_notes)
    neg("missing-figure-media", "E", missing_figure_media)
    neg("pdf-page-mismatch", "F", pdf_page_mismatch, rebuild=False)
    neg("contract-key-empty", "H", contract_key_empty, rebuild=False)
    neg("too-few-qa", "I", too_few_qa)
    neg("dangling-qa-slide", "I", dangling_qa_slide)

    _report(a)
    n_fail = sum(1 for r in RESULTS if r["status"] == "fail")
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 0 if n_fail == 0 else 1


def _report(a) -> None:
    n_pass = sum(1 for r in RESULTS if r["status"] == "pass")
    print(json.dumps({"checks": RESULTS, "passed": n_pass, "failed": len(RESULTS) - n_pass},
                     ensure_ascii=False, indent=2) if a.json else "")
    for r in RESULTS:
        mark = "PASS" if r["status"] == "pass" else "FAIL"
        print(f"[{mark}] {r['name']}: {r['detail']}")
    print(f"selftest: {n_pass}/{len(RESULTS)} PASS")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
