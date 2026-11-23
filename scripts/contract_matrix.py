#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract_matrix.py — adversarial output_contract key sets (gate H, unseen cases).

The organizer's hidden cases may name their outputs anything.  This script proves that
`spec.map_contract` never leaves a contract key empty, whatever the key names are, and
that unknown keys are disclosed instead of silently blanked.

    python3 scripts/contract_matrix.py            # exit 0 when every key set passes
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec import map_contract  # noqa: E402

MATRIX = {
    "canonical-english": {
        "slides_path": "deck.pptx", "speaker_notes": "notes for every slide",
        "evidence_mapping": "page-level provenance", "qa_prep": "anticipated questions",
        "preview_images": "rendered png previews", "pdf_export": "exported pdf",
    },
    "loose-pptx-names": {
        "presentation": "", "pptx": "", "editable_file": "", "render": "", "citations": "",
    },
    "cjk-keys": {
        "可编辑演示文件": "", "讲稿备注": "", "页级证据映射": "", "问答准备": "",
        "预览图": "", "导出件": "", "运行摘要": "",
    },
    "single-summary-key": {"answer": ""},
    "adversarial-unknown": {
        "zzz_unknown_key": "", "artifact_7": "", "X": "", "": "",
    },
    "mixed-family-collision": {
        "pdf_and_notes": "", "evidence_or_preview": "", "slides_preview": "", "qa_evidence": "",
    },
}

# Keys whose family is genuinely ambiguous may map to any real artefact; what matters is
# that the value is a non-empty, truthful string (a real path or an honest run summary).
EXPECT_NONEMPTY = True


def _ctx(tmp: Path) -> dict:
    art = {}
    for name in ("deck.pptx", "speaker-notes.md", "evidence-map.json", "qa-prep.md",
                 "deck_plan.json", "render_manifest.json", "deck.pdf"):
        p = tmp / name
        p.write_text("placeholder", encoding="utf-8")
        art[name] = {"path": f"output/{name}", "bytes": p.stat().st_size, "sha256": "0" * 8}
    return {
        "out_dir": tmp,
        "artifacts": art,
        "status": "ok",
        "slide_count": 14,
        "evidence_count": 22,
        "gates": [{"id": "A", "status": "pass"}],
        "summary_text": "case-001: 14 slides for 12 min; status=ok",
    }


def default_tmp() -> Path:
    """Scratch root — deliberately OUTSIDE the delivery tree (work/verify, not submission/)."""
    here = Path(__file__).resolve().parent          # <sub>/scripts
    sub = here.parent                                # <sub>
    work = sub.parent                                # work/
    if work.name == "work":
        return work / "verify" / "_contract_matrix_tmp"
    import os
    return Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or ".") / "deck_contract_matrix_tmp"


def run(tmp_root: Path | None = None) -> list[dict]:
    root = Path(tmp_root) if tmp_root else default_tmp()
    root.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(root)
    rows = []
    for name, contract in MATRIX.items():
        case = {"case_id": "case-001", "output_contract": contract}
        values, unmapped = map_contract(case, ctx)
        empties = [k for k, v in values.items() if not str(v or "").strip()]
        missing = [k for k in contract if k not in values]
        rows.append({
            "set": name, "keys": len(contract), "filled": len(values),
            "empty": empties, "missing": missing, "unmapped": unmapped,
            "status": "pass" if (not empties and not missing) else "fail",
            "sample": {k: values.get(k) for k in list(contract)[:3]},
        })
    return rows


def cli(argv: list[str]) -> int:
    import json
    root = None
    if argv:
        root = Path(argv[0])
    rows = run(root)
    bad = [r for r in rows if r["status"] != "pass"]
    print(json.dumps({"sets": len(rows), "passed": len(rows) - len(bad),
                      "failed": len(bad), "rows": rows}, ensure_ascii=False, indent=2))
    print(f"contract_matrix: {len(rows) - len(bad)}/{len(rows)} PASS")
    return 0 if not bad else 1

if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
