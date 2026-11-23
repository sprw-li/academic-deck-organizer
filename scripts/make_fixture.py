#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_fixture.py -- generate the synthetic fixture for academic-deck-organizer.

The delivered bundle is limited to UTF-8 text (<= 50 files, <= 500 KB each, <= 2 MB
total, no symlinks), so the binary materials the runtime needs are **generated on the
spot** instead of being shipped:

  * ``materials/slides-template.pptx``  a real, openable template whose slide master and
    all 11 layouts carry deck-specific names and a deck-specific theme colour set;
  * ``materials/paper.pdf``             a real 3-page PDF (matplotlib) carrying the same
    numbers as the markdown excerpt, so ``paper.pdf#p.N`` locators resolve;
  * ``materials/fig1.png``              a real 800x600 PNG figure referenced by page P6.

    python3 scripts/make_fixture.py --out /tmp/deck-fixture

Everything is written under ``--out`` (created if missing).  ``case.json``, ``task.md``
and the three text materials are written by the same command, so the resulting directory
is a complete ``/input``-shaped case: point ``pt.py run`` at it directly.

The ``.pptx`` is byte-stable: the package is re-zipped with a fixed 1980-01-01 stamp and
sorted entry order, because python-pptx stamps entries with the wall clock (two saves in
the same 2-second DOS window happen to compare equal, which is *not* determinism).
``paper.pdf`` and ``fig1.png`` are not byte-stable (matplotlib embeds a creation date).

Exit 0 on success; exit 2 on usage/IO error.  ``sys.dont_write_bytecode`` is set before
any import so the bundle never grows a ``__pycache__``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile

sys.dont_write_bytecode = True

SYNTHETIC_BANNER = "SYNTHETIC EXAMPLE - NOT AN OFFICIAL CASE"
CASE_ID = "SYNTHETIC-DECK-001"

# --------------------------------------------------------------------------------------
# fixture text: paper excerpt (line numbers are stable and are cited by #L locators)
# --------------------------------------------------------------------------------------

PAPER_MD = "\n".join([
    "# Damp-heat phase stability of formamidinium-cesium mixed-halide perovskite absorbers",
    "",
    SYNTHETIC_BANNER + ". Invented material, written only to exercise the",
    "academic-deck-organizer interface end to end. It is not a measurement, it is not an",
    "official challenge case, and no number in it may be cited as a result.",
    "",
    "## Abstract",
    "Mixed-halide formamidinium-cesium lead iodide absorbers promise better thermal stability,",
    "but their phase behaviour under simultaneous damp heat is not settled. We cycled 24",
    "encapsulated devices per group at 85 C and 85 % relative humidity for 1000 h.",
    "The reference as-cast absorber retained 73.8 % of its initial power conversion efficiency,",
    "whereas the Cs-incorporated absorber retained 96.2 %.",
    "The retained fraction tracks the delta-phase fraction measured by X-ray diffraction: 31.5 %",
    "for the reference film and 4.2 % for the Cs-incorporated film.",
    "The activation energy for phase segregation rises from 0.78 eV to 1.12 eV, consistent with",
    "the observed T80 lifetime increase from 240 h to 1450 h.",
    "",
    "## 1. Introduction",
    "Phase segregation in mixed-halide absorbers is driven by light and heat, and its coupling",
    "to moisture ingress is the practical failure route in encapsulated modules.",
    "",
    "## 2. Methods",
    "Films were spun from a 1.2 M precursor solution and annealed at 150 C for 20 min.",
    "Devices were encapsulated with a glass-glass edge seal and cycled at 85 C and 85 %",
    "relative humidity. X-ray diffraction was collected every 100 h.",
    "",
    "## 3. Results",
    "### 3.1 Efficiency retention",
    "The reference group fell from 21.4 % to 15.8 % over 1000 h, while the Cs-incorporated",
    "group fell from 20.9 % to 20.1 % (Table 1).",
    "### 3.2 Phase fraction",
    "The delta-phase fraction after 1000 h was 31.5 % for the reference film and 4.2 % for the",
    "Cs-incorporated film (Figure 1).",
    "### 3.3 Activation energy",
    "Arrhenius analysis of the segregation rate gives 0.78 eV for the reference film and",
    "1.12 eV for the Cs-incorporated film.",
    "",
    "## 4. Discussion",
    "The Cs-incorporated film keeps its black phase, so the loss of open-circuit voltage is",
    "small (1.150 V to 1.130 V, Table 1).",
    "",
    "## 5. Limitations",
    "The study covers 1000 h only, one encapsulation stack and 24 devices per group; the",
    "Arrhenius fit uses three temperatures. Outdoor validation is not included.",
    "",
    "## 6. Data availability",
    "Raw cycling data are in results.csv; figure and table captions are in figure-notes.json.",
    "",
]) + "\n"

RESULTS_CSV = "\n".join([
    "sample_id,hours,PCE_pct,Voc_V,Jsc_mA_per_cm2,FF_pct,delta_phase_fraction_pct",
    "REF-0000h,0,21.40,1.150,24.10,77.3,0.0",
    "REF-0500h,500,18.90,1.120,23.50,71.8,14.6",
    "REF-1000h,1000,15.80,1.060,22.40,66.5,31.5",
    "CS-0000h,0,20.90,1.140,24.60,79.5,0.0",
    "CS-0500h,500,20.60,1.135,24.40,74.4,1.8",
    "CS-1000h,1000,20.10,1.130,24.30,73.2,4.2",
    "",
])

FIGURE_NOTES_JSON = {
    "note": SYNTHETIC_BANNER
    + ". Figure and table captions for the invented damp-heat cycling study.",
    "figures": [
        {
            "id": "fig1",
            "label": "Figure 1",
            "page": 2,
            "caption": (
                "Figure 1. Delta-phase fraction from X-ray diffraction for the reference and "
                "Cs-incorporated films during 1000 h of damp-heat cycling at 85 C and 85 % "
                "relative humidity (24 devices per group)."
            ),
            "media_rel": "materials/fig1.png",   # CONTRACT §3.1: the spec's media_path is absolute
        }
    ],
    "tables": [
        {
            "id": "tab1",
            "label": "Table 1",
            "page": 2,
            "caption": (
                "Table 1. Photovoltaic parameters of the reference and Cs-incorporated groups "
                "at 0 h and after 1000 h of damp-heat cycling (mean of 24 devices)."
            ),
        }
    ],
}


def _case_json(template_sha256: str) -> dict:
    return {
        "case_id": CASE_ID,
        "title": SYNTHETIC_BANNER + ": damp-heat phase stability of a mixed-halide "
        "perovskite absorber",
        "language": "en",
        "aspect": "16:9",
        "constraints": {
            "minutes": 12,
            "slide_count": 9,
            "qa_minutes": 3,
            "audience_id": "group-meeting",
            "prior_knowledge": "high",
            "template": "materials/slides-template.pptx",
            "template_sha256": template_sha256,
            "language": "en",
        },
        "output_contract": {
            "deck": "editable slide deck built on the supplied template master/layouts",
            "speaker_notes": "per-slide speaker notes, human readable",
            "evidence_map": "per-slide claim -> material locator -> verbatim quote",
            "qa_prep": "anticipated questions with answers, cited slides and risk level",
            "preview": "real rendered preview of the deck plus the render manifest",
            "summary": "one-paragraph run summary, honest about what is missing",
        },
        "materials": [
            "materials/paper-excerpt.md",
            "materials/results.csv",
            "materials/figure-notes.json",
            "materials/paper.pdf",
            "materials/fig1.png",
            "materials/slides-template.pptx",
        ],
        "notes": [
            SYNTHETIC_BANNER,
            "The fixture is regenerated by scripts/make_fixture.py; it is not an official case.",
        ],
    }


FIXTURE_TASK_MD = "\n".join([
    "# " + SYNTHETIC_BANNER,
    "",
    "## Group meeting: damp-heat phase stability of a mixed-halide perovskite absorber",
    "",
    "Prepare a 12-minute group-meeting presentation (9 slides, then ~3 minutes of questions)",
    "from the materials of an ongoing study. The audience already knows the field: skip the",
    "textbook background, rat the trade-off, and be explicit about what is not yet shown.",
    "",
    "Materials:",
    "",
    "* `materials/paper-excerpt.md` - the study write-up (sections and line numbers).",
    "* `materials/results.csv` - the cycling data behind every number (header on line 1).",
    "* `materials/figure-notes.json` - Figure 1 and Table 1 captions (`#fig.1`, `#tab.1`).",
    "* `materials/paper.pdf` - the same write-up as a real 3-page PDF (`#p.1` .. `#p.3`).",
    "* `materials/slides-template.pptx` - the frozen template; inherit its master/layouts.",
    "",
    "Requirements:",
    "",
    "1. Every number that appears on a slide must resolve to a locator in the materials,",
    "   with a verbatim quote, and the number must occur inside that quote.",
    "2. One claim per slide; speaker notes must add what the bullets cannot say, not repeat them.",
    "3. Total speaking time within +/-15 % of 12 minutes; slide count exactly 9.",
    "4. Keep the deck editable (real text, inherited placeholders, template theme) and export",
    "   a real PDF/PNG preview.",
    "5. Anticipate at least three questions on mechanism, data and limitations.",
    "6. Say what is missing instead of inventing it: the deck status may be `partial`.",
    "",
    "This is invented material. It is not an official case and it is not a result.",
    "",
])


# --------------------------------------------------------------------------------------
# generated binaries
# --------------------------------------------------------------------------------------

# python-pptx's built-in default template ships exactly these 11 layouts; each one is
# renamed so the template has a deck-specific layout vocabulary (>= 5 custom layouts).
DEFAULT_LAYOUT_NAMES = [
    "Title Slide",
    "Title and Content",
    "Section Header",
    "Two Content",
    "Comparison",
    "Title Only",
    "Blank",
    "Content with Caption",
    "Picture with Caption",
    "Title and Vertical Text",
    "Vertical Title and Text",
]

LAYOUT_RENAMES = {
    "Title Slide": "Deck Title",
    "Title and Content": "Deck Content",
    "Section Header": "Deck Section",
    "Two Content": "Deck Two Column",
    "Comparison": "Deck Comparison",
    "Title Only": "Deck Statement",
    "Blank": "Deck Closing",
    "Content with Caption": "Deck Figure",
    "Picture with Caption": "Deck Picture",
    "Title and Vertical Text": "Deck Vertical",
    "Vertical Title and Text": "Deck Vertical Title",
}

THEME_RENAMES = {
    "theme": "Deck Theme",
    "accent1": "1F3864",   # primary   (spec.theme.colors.primary)
    "accent2": "C00000",   # accent    (spec.theme.colors.accent)
    "dk2": "595959",       # muted     (spec.theme.colors.muted)
}


def _theme_patch(xml: str, applied: list) -> str:
    """Rewrite the theme name and three colour slots, recording what actually matched."""
    new_xml, n = re.subn(r'(<a:theme\b[^>]*\bname=")[^"]*(")',
                         r"\g<1>" + THEME_RENAMES["theme"] + r"\g<2>", xml, count=1)
    xml = new_xml
    applied.append("theme-name" if n else "theme-name:MISS")
    for slot in ("accent1", "accent2", "dk2"):
        pattern = r'(<a:%s>\s*<a:srgbClr val=")[0-9A-Fa-f]{6}(")' % slot
        xml, n = re.subn(pattern, r"\g<1>" + THEME_RENAMES[slot] + r"\g<2>", xml, count=1)
        applied.append("%s=%s" % (slot, slot if n else "MISS"))
    return xml


def _rezip(data: bytes, patch=None) -> bytes:
    """Re-write a pptx package with a fixed stamp, sorted names and fixed compression."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        for name in sorted(src.namelist()):
            blob = src.read(name)
            if patch is not None:
                blob = patch(name, blob)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            dst.writestr(info, blob)
    return out.getvalue()


def build_template(path: str, *, with_starter_slide: bool = False) -> dict:
    """Write a real .pptx whose master, 11 layouts and theme are deck-specific."""
    from pptx import Presentation
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width = Emu(12192000)   # 13.333 in
    prs.slide_height = Emu(6858000)   # 7.5 in  -> 16:9

    master = prs.slide_masters[0]
    try:
        master._element.cSld.set("name", "Deck Master")
        master_renamed = True
    except Exception:                                          # pragma: no cover
        master_renamed = False

    names = []
    for i, layout in enumerate(prs.slide_layouts):
        current = layout.name
        new_name = LAYOUT_RENAMES.get(current, "Deck Layout %02d" % (i + 1))
        layout._element.cSld.set("name", new_name)
        names.append(new_name)

    if with_starter_slide:
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = "Starter slide (template owner's own)"
        slide.placeholders[1].text = "Replace or delete this slide before presenting."

    raw = io.BytesIO()
    prs.save(raw)

    applied: list = []
    blob = _rezip(raw.getvalue(), lambda name, b: (
        _theme_patch(b.decode("utf-8"), applied).encode("utf-8")
        if name == "ppt/theme/theme1.xml" else b))

    with open(path, "wb") as fh:
        fh.write(blob)

    return {
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "slide_size_emu": [12192000, 6858000],
        "master_name": "Deck Master" if master_renamed else master.name,
        "layouts": names,
        "custom_layouts": len([n for n in names if n.startswith("Deck ")]),
        "existing_slides": 1 if with_starter_slide else 0,
        "theme_patches": applied,
        "python_pptx_default_layouts": DEFAULT_LAYOUT_NAMES,
    }


# the four panels of the generated PDF: (heading, [body lines])
PDF_PAGES = [
    (
        "Damp-heat phase stability of formamidinium-cesium mixed-halide perovskite absorbers",
        [
            "SYNTHETIC EXAMPLE - NOT AN OFFICIAL CASE (page 1 of 3)",
            "",
            "Abstract",
            "The reference as-cast absorber retained 73.8 % of its initial power conversion",
            "efficiency, whereas the Cs-incorporated absorber retained 96.2 % after 1000 h at",
            "85 C and 85 % relative humidity.",
            "The retained fraction tracks the delta-phase fraction measured by X-ray",
            "diffraction: 31.5 % for the reference film and 4.2 % for the Cs-incorporated film.",
            "The activation energy for phase segregation rises from 0.78 eV to 1.12 eV,",
            "consistent with the T80 lifetime increase from 240 h to 1450 h.",
            "",
            "1. Introduction",
            "Phase segregation in mixed-halide absorbers is driven by light and heat, and its",
            "coupling to moisture ingress is the practical failure route in encapsulated",
            "modules. Whether caesium incorporation removes that route is not settled.",
        ],
    ),
    (
        "2. Methods   |   3. Results",
        [
            "SYNTHETIC EXAMPLE - NOT AN OFFICIAL CASE (page 2 of 3)",
            "",
            "Methods: films spun from a 1.2 M precursor solution, annealed at 150 C for 20 min;",
            "devices encapsulated with a glass-glass edge seal; cycling at 85 C / 85 % RH;",
            "X-ray diffraction every 100 h; 24 devices per group.",
            "",
            "Results: the reference group fell from 21.4 % to 15.8 % over 1000 h, while the",
            "Cs-incorporated group fell from 20.9 % to 20.1 % (Table 1).",
            "The delta-phase fraction after 1000 h was 31.5 % for the reference film and 4.2 %",
            "for the Cs-incorporated film (Figure 1).",
            "",
            "Arrhenius analysis of the segregation rate gives 0.78 eV for the reference film",
            "and 1.12 eV for the Cs-incorporated film.",
        ],
    ),
    (
        "4. Discussion   |   5. Limitations",
        [
            "SYNTHETIC EXAMPLE - NOT AN OFFICIAL CASE (page 3 of 3)",
            "",
            "Discussion: the Cs-incorporated film keeps its black phase, so the loss of",
            "open-circuit voltage is small (1.150 V to 1.130 V, Table 1).",
            "",
            "Limitations: the study covers 1000 h only, one encapsulation stack and 24 devices",
            "per group; the Arrhenius fit uses three temperatures. Outdoor validation is not",
            "included. Table 1 reports 0 h and 1000 h means only.",
        ],
    ),
]


def build_pdf(path: str) -> dict:
    """Write a real 3-page PDF whose text carries the same numbers as the markdown."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.pyplot as plt

    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["font.family"] = "DejaVu Sans"

    with PdfPages(path) as pdf:
        for index, (heading, lines) in enumerate(PDF_PAGES):
            fig = plt.figure(figsize=(8.27, 11.69))          # A4 portrait
            fig.text(0.08, 0.94, heading, fontsize=12, weight="bold", va="top", wrap=True)
            fig.text(0.08, 0.90, "\n".join(lines), fontsize=8.5, va="top", linespacing=1.5)
            if index == 1:
                ax = fig.add_axes([0.12, 0.10, 0.78, 0.22])
                hours = [0, 500, 1000]
                ax.plot(hours, [0.0, 14.6, 31.5], marker="o", label="reference")
                ax.plot(hours, [0.0, 1.8, 4.2], marker="s", label="Cs-incorporated")
                ax.set_xlabel("damp-heat hours (85 C / 85 % RH)")
                ax.set_ylabel("delta-phase fraction (%)")
                ax.set_title("Figure 1 (synthetic example)")
                ax.legend(fontsize=8)
                ax.grid(alpha=0.3)
            pdf.savefig(fig)
            plt.close(fig)

    with open(path, "rb") as fh:
        blob = fh.read()
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "pages": len(PDF_PAGES)}


def build_figure(path: str) -> dict:
    """Write the 800x600 PNG referenced by page P6 (media_path materials/fig1.png)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.0, 6.0), dpi=100)
    hours = [0, 500, 1000]
    ax.plot(hours, [0.0, 14.6, 31.5], marker="o", label="reference")
    ax.plot(hours, [0.0, 1.8, 4.2], marker="s", label="Cs-incorporated")
    ax.set_xlabel("damp-heat hours (85 C / 85 % RH)")
    ax.set_ylabel("delta-phase fraction (%)")
    ax.set_title("Figure 1 (synthetic example)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)

    with open(path, "rb") as fh:
        blob = fh.read()
    return {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "pixels": [800, 600]}


# --------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------

def _write_text(path: str, text: str) -> dict:
    data = text.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "kind": "utf-8-text"}


def generate(out_dir: str, *, with_starter_slide: bool = False) -> dict:
    materials = os.path.join(out_dir, "materials")
    os.makedirs(materials, exist_ok=True)

    manifest: dict = {}

    # the template is generated first so its sha256 can be recorded in case.json
    template_path = os.path.join(materials, "slides-template.pptx")
    template_info = build_template(template_path, with_starter_slide=with_starter_slide)
    manifest["materials/slides-template.pptx"] = dict(template_info, kind="pptx",
                                                      deterministic=True)

    manifest["case.json"] = _write_text(
        os.path.join(out_dir, "case.json"),
        json.dumps(_case_json(template_info["sha256"]), indent=2, ensure_ascii=False,
                   sort_keys=False) + "\n")
    manifest["task.md"] = _write_text(os.path.join(out_dir, "task.md"), FIXTURE_TASK_MD)
    manifest["materials/paper-excerpt.md"] = _write_text(
        os.path.join(materials, "paper-excerpt.md"), PAPER_MD)
    manifest["materials/results.csv"] = _write_text(
        os.path.join(materials, "results.csv"), RESULTS_CSV)
    manifest["materials/figure-notes.json"] = _write_text(
        os.path.join(materials, "figure-notes.json"),
        json.dumps(FIGURE_NOTES_JSON, indent=2, ensure_ascii=False) + "\n")

    manifest["materials/paper.pdf"] = dict(build_pdf(os.path.join(materials, "paper.pdf")),
                                           kind="pdf", deterministic=False)
    manifest["materials/fig1.png"] = dict(build_figure(os.path.join(materials, "fig1.png")),
                                          kind="png", deterministic=False)

    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_fixture.py",
        description="Generate the synthetic fixture for academic-deck-organizer "
                    "(text materials + a real template pptx + a real PDF + a real PNG).")
    parser.add_argument("--out", required=True,
                        help="directory to write the fixture into (created if missing)")
    parser.add_argument("--with-starter-slide", action="store_true",
                        help="also put one slide in the template, to test that a build "
                             "drops inherited slides (default: template has no slides)")
    parser.add_argument("--quiet", action="store_true", help="print nothing on success")
    args = parser.parse_args(argv)

    if not args.out or not args.out.strip():
        print("[make_fixture] --out must not be empty", file=sys.stderr)
        return 2

    try:
        manifest = generate(os.path.abspath(args.out),
                            with_starter_slide=args.with_starter_slide)
        manifest_path = os.path.join(os.path.abspath(args.out), "FIXTURE-MANIFEST.json")
        report = {
            "fixture_root": os.path.abspath(args.out),
            "banner": SYNTHETIC_BANNER,
            "case_id": CASE_ID,
            "files": manifest,
            "notes": [
                "slides-template.pptx is byte-stable (fixed zip stamp, sorted entries).",
                "paper.pdf and fig1.png are not byte-stable; matplotlib embeds a date.",
                "No official case, no official result, no invented benchmark score.",
            ],
        }
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        report["manifest_path"] = manifest_path
    except Exception as exc:                                   # pragma: no cover - IO guard
        print("[make_fixture] failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    if not args.quiet:
        print(json.dumps({"ok": True, "fixture_root": report["fixture_root"],
                          "files": sorted(report["files"]),
                          "manifest": report["manifest_path"]},
                         indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
