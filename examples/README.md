# Examples

This directory demonstrates the Skill end to end on a **synthetic** fixture.

> ## ⚠️ SYNTHETIC EXAMPLE — NOT AN OFFICIAL CASE
> `synthetic-001` is invented material: a fictional damp-heat perovskite stability study whose
> numbers were made up to exercise the pipeline (9 pages, 18 evidence items, 4 Q&A items). It must
> never be presented or cited as a real measurement, and nothing here is an official challenge case,
> an official result, or a benchmark score.

---

## What is in here

```
examples/
├── README.md                     <- you are reading it
└── synthetic-001/
    ├── input/                    <- the case as the runtime would receive it
    │   ├── case.json             <- case_id + output_contract + the frozen brief
    │   ├── task.md               <- the workorder, labelled SYNTHETIC
    │   └── materials/
    │       ├── paper-excerpt.md  <- the "paper": sections and line numbers (#L locators)
    │       ├── results.csv       <- the cycling data (header on line 1)
    │       └── figure-notes.json <- Figure 1 / Table 1 captions (#fig.1, #tab.1)
    └── expected/
        ├── spec.json             <- the authored DeckSpec v1 (what the LLM step produces)
        ├── deck_plan.json        <- per-page resolution: slide -> layout -> placeholders -> claim -> evidence
        └── result.json           <- the shape result.json must take (ILLUSTRATIVE, see below)
```

Only **UTF-8 text** ships in this bundle, so the three files the runtime needs that are *binary* —
`materials/slides-template.pptx`, `materials/paper.pdf` and `materials/fig1.png` — are not stored
here. They are generated on the spot by `scripts/make_fixture.py`.

---

## Generate the fixture, then run it

```bash
# 1. generate a complete /input-shaped directory (text materials + real pptx + real pdf + real png)
python3 scripts/make_fixture.py --out /tmp/deck-fixture

# 2. run the whole chain against it
python3 scripts/pt.py run --input /tmp/deck-fixture --work /tmp/deck-work --out /tmp/deck-out
```

`make_fixture.py` writes `case.json` and `task.md` too, so `/tmp/deck-fixture` is self-contained and
its text materials are the same files as `examples/synthetic-001/input/**`. Step by step, the same
run is:

```bash
python3 scripts/pt.py ingest --input /tmp/deck-fixture --work /tmp/deck-work
python3 scripts/pt.py inspect --input /tmp/deck-fixture --work /tmp/deck-work
cp examples/synthetic-001/expected/spec.json /tmp/deck-work/spec.json   # the authored spec
python3 scripts/pt.py build  --spec /tmp/deck-work/spec.json --work /tmp/deck-work --out /tmp/deck-out
python3 scripts/pt.py render --out /tmp/deck-out --dpi 144
python3 scripts/pt.py result --input /tmp/deck-fixture --work /tmp/deck-work --out /tmp/deck-out
python3 scripts/pt.py check  --out /tmp/deck-out --spec /tmp/deck-work/spec.json --work /tmp/deck-work
python3 scripts/pt.py result --input /tmp/deck-fixture --work /tmp/deck-work --out /tmp/deck-out
```

Skipping the `cp` makes `plan` supply the deterministic fallback spec instead of the authored one:
`build` still produces a deck, but it is the floor, not the example.

> **Why not run `pt.py run --input examples/synthetic-001/input` directly?**
> `CONTRACT.md` §7.1 phrases the definition of done that way, but §0 forbids shipping a binary
> template and PDF, and `examples/synthetic-001/input/` is text-only. `ingest` runs against it
> happily; a full run needs the generated binaries, which is what step 1 above provides. The
> discrepancy is reported to the Lead as a contract gap (see
> [`../references/rubric-mapping.md`](../references/rubric-mapping.md) §"Command-surface audit").
>
> Also note: the flags shown for `pt.py result` here (and for `pt.py edit` in the revision flow) are
> **not frozen** in `CONTRACT.md` §4 — only `run --input --work --out` is. They follow the
> `result.json` and edits semantics of §3.6; confirm the exact surface with `--help` before relying
> on a particular flag. `ingest`, `build`, `render` and `check` above use the frozen signatures
> verbatim.

---

## What the fixture is designed to exercise

* a **real template** whose slide master and all 11 layouts carry deck-specific names
  (`Deck Title`, `Deck Content`, `Deck Section`, `Deck Two Column`, `Deck Figure`, `Deck Closing`, …)
  and a deck-specific theme colour set (`accent1 #1F3864`, `accent2 #C00000`, `dk2 #595959`), so the
  role→layout mapping and gate C have something real to resolve;
* a **real 3-page PDF** whose text carries the same numbers as the markdown, so `paper.pdf#p.N`
  locators resolve;
* an **800×600 PNG** referenced by page P6, so the figure-binding rules of gate E are exercised
  against a path that actually exists — bound as an **absolute** `media_path`
  (`/input/materials/fig1.png`) with `media_rel` (`materials/fig1.png`) for humans, per
  `CONTRACT.md` §3.1;
* **all four locator families that the grammar defines**: line ranges in markdown
  (`paper-excerpt.md#L12-L12`), line ranges in CSV (`results.csv#L4-L4`), semantic figure/table
  locators (`figure-notes.json#fig.1`, `#tab.1`) and page locators in the PDF;
* a **complete authored spec**: 9 pages covering `title`, `hook`, `problem`, `gap`, `method`,
  `results` (×2), `limitation`, `conclusion`; a per-page claim; per-page `strength`; 18 evidence items
  with verbatim quotes; 4 Q&A items including two `high` risk ones; 690 s of speaking time against a
  12-minute budget (4.17 % deviation).

The case brief is deliberately constraining (12 minutes, 9 slides, a fixed template, high
prior-knowledge audience), because that is the situation the topic is about: the hard part is not
making slides, it is deciding what does **not** go on them.

---

## How to read `expected/`

| File | What to do with it |
|---|---|
| `spec.json` | the template to copy and rewrite for your own case. It satisfies the `CONTRACT.md` §3.4 rules mechanically: unique page/evidence ids, every `evidence_ids` entry resolves, every content page carries evidence, every on-page number is traceable to a cited quote, every layout exists in the generated template, every claim ≤ 160 chars, `Σ timing_s` within ±15 % of the budget, notes/bullets similarity < 0.8, `story.arc` complete |
| `deck_plan.json` | the page-by-page resolution a reviewer checks against `deck.pptx`: slide → layout → which placeholder carries what → claim → evidence → figure → notes. **Real output** of the run below (paths shown as placeholders) |
| `result.json` | the structured result: `case_id`, `status`, `artifacts` (real bytes/sha256), `sources`, `evidence`, `page_map`, `qa`, `uncertainty`, `limitations`, `change_records`, `audit` (the real ten gate verdicts), `runtime`, plus every `output_contract` key |

### `expected/result.json` is a real run, and is labelled synthetic

These two files are **not hand-written**. They are the output of an actual pipeline run on this
fixture, executed 2026-11-23 ~13:47 +08:00:

```
python3 scripts/make_fixture.py --out <scratch-in>
python3 scripts/pt.py ingest  --input <scratch-in> --work <scratch-work>
python3 scripts/pt.py inspect --input <scratch-in> --work <scratch-work>
python3 scripts/pt.py run     --input <scratch-in> --work <scratch-work> --out <scratch-out>
python3 scripts/pt.py check   --out <scratch-out> --spec <scratch-work>/spec.json --work <scratch-work>
python3 scripts/pt.py result  --input <scratch-in> --work <scratch-work> --out <scratch-out>
```

Every step exited `0`. Measured facts recorded in `result.json`:

| Fact | Value |
|---|---|
| `status` | **`ok`** |
| gates A–J | **10/10 `pass`**, `checks_failed: 0`, `checks_warned: 0` |
| `artifacts` | **18** files, each with a real `bytes` + `sha256` |
| `deck.pptx` | 84,165 B, sha256 `8ed411b6818e58df941a443208f1d7bc96a71eb895f8dc530c25d157c8b4aec0` |
| `audit.determinism` | `byte-identical` |
| `runtime.seconds` | 32.34 |
| `output_contract` keys | 6/6 non-empty, `unmapped_contract_keys: []` |

Two disclosures travel with it, so nothing measured is misread:

1. **It is synthetic.** `"synthetic_example": true` plus a `disclaimer` field state that this is the
   invented damp-heat fixture, not an official case and not an official score.
2. **One field was substituted for the local run.** `pages[].figure.media_path` is stored in
   `spec.json` as the container path `/input/materials/fig1.png`; the run rewrote it to the local
   fixture file because `/input` exists only inside the runtime container. The file bytes — and the
   sha256 that gate E verified (`acfd665a…`) — are the same. This is spelled out in
   `result.json.measurement.spec_note`, and no other field differs from the shipped `spec.json`.
   Author-machine absolute paths were replaced with `<placeholders>`; no measured value was altered.

Re-run it yourself with `work/verify/docs-examples/measure_example.py` (author-side tool, not
shipped) or with the command sequence above, and compare the gate verdicts.

---

## The generator is reproducible

```bash
python3 scripts/make_fixture.py --out /tmp/deck-fixture
```

| Generated file | Byte-stable across runs? | Why |
|---|---|---|
| `materials/slides-template.pptx` | **yes** | re-zipped with a fixed 1980-01-01 stamp and sorted entries; `d4d7e8e5c9fd6c674c0e222a6163729f000264891dfd43cc2cc118f8d576a6b2` on two independent runs (python-pptx 1.0.2). Without that step, python-pptx stamps entries with the wall clock |
| `materials/paper.pdf` | no | matplotlib embeds a creation timestamp |
| `materials/fig1.png` | no | same reason |

`FIXTURE-MANIFEST.json` records every generated file with its `sha256` and `bytes`, plus the template's
layout list and which theme patches applied — so a mismatch can be diagnosed without guessing.

Add `--with-starter-slide` to put one slide inside the template: it is the quickest way to check that
a build **drops inherited slides** instead of appending to them (otherwise the deck has one page more
than the spec and gate C fails).

---

## Rendered exports are described, not stored

`deck.pdf` and `preview/page-0001.png …` are produced by `pt.py render` into the output directory and
measured in `render_manifest.json` (page count, dpi, per-file sha256, missing fonts). They are not in
this bundle because every bundle file must be UTF-8 text; reproduce them with the command sequence
above and compare against the manifest.

If no renderer can be found, `render` records `renderer-unavailable`, gate F warns, and `status`
becomes `partial` — the editable `.pptx` is still delivered. That is the designed behaviour, not a
failure of this example.

---

## Run status

What has actually been executed, and what has not — so nothing here is claimed without evidence.

| Claim | Status |
|---|---|
| `make_fixture.py` generates the template, PDF, PNG and text materials | **executed** — exit `0`; 11 custom layouts, master `Deck Master`, theme `Deck Theme`, 3-page PDF, 800×600 PNG |
| the template is byte-stable | **executed** — two independent runs, identical sha256 `d4d7e8e5…` |
| `examples/synthetic-001/**` is generated from the fixture, not hand-copied | **executed** — the derivation tool re-reads the fixture and asserts the §3.4 rules plus number traceability; result: `problems: []` |
| every `source.quote` in `expected/spec.json` equals the exact text of its cited span | **executed** — asserted during derivation (a non-equal span aborts with exit `1`) |
| every numeric `value` occurs inside its own quote, and every on-page number is traceable | **executed** — asserted during derivation, and again by gate A in the real run below |
| the worked example passes a real `ingest`/`run`/`check`/`result` | **executed** — all steps exit `0`; gates **A–J 10/10 pass**; `status: "ok"` (2026-11-23 ~13:47 +08:00) |
| `expected/{result,deck_plan}.json` are a real run's output | **yes** — real 18-artefact set with real bytes/sha256 and the real gate verdicts, labelled `synthetic_example: true` and accompanied by a `measurement` block |

> Never trust a number in this directory that is not accompanied by the command that produced it.
> The measured facts here are the template's sha256, the derived example's rule checks, the gate
> verdicts and the artefact hashes in `expected/result.json` — each with the command that produced it
> in `references/rubric-mapping.md` §"Evidence of execution".
