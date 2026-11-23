# Examples

This directory demonstrates the Skill end to end on a **synthetic** fixture.

> ## ⚠️ SYNTHETIC EXAMPLE — NOT AN OFFICIAL CASE
> `synthetic-001` is invented material: a fictional damp-heat perovskite stability study whose
> numbers were made up to exercise the pipeline (9 pages, 17 evidence items, 4 Q&A items). It must
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
  `results` (×2), `limitation`, `conclusion`; a per-page claim; per-page `strength`; 17 evidence items
  with verbatim quotes; 4 Q&A items including two `high` risk ones; 690 s of speaking time against a
  12-minute budget (4.17 % deviation).

The case brief is deliberately constraining (12 minutes, 9 slides, a fixed template, high
prior-knowledge audience), because that is the situation the topic is about: the hard part is not
making slides, it is deciding what does **not** go on them.

---

## How to read `expected/`

| File | What to do with it |
|---|---|
| `spec.json` | the template to copy and rewrite for your own case. It satisfies the `CONTRACT.md` §3.4 rules mechanically: unique page/evidence ids, every `evidence_ids` entry resolves, every layout exists in the generated template, every claim ≤ 160 chars, `Σ timing_s` within ±15 % of the budget, notes/bullets similarity < 0.8, `story.arc` complete |
| `deck_plan.json` | the page-by-page resolution a reviewer checks against `deck.pptx`: slide → layout → which placeholder carries what → claim → evidence → figure → notes |
| `result.json` | the **shape** of the structured result: `case_id`, `status`, `artifacts`, `sources`, `evidence`, `page_map`, `qa`, `uncertainty`, `limitations`, `change_records`, `audit`, `runtime`, plus every `output_contract` key |

### `expected/result.json` is illustrative, and says so

At documentation-freeze time the pipeline scripts were still being written in parallel
(`CONTRACT.md` §2). `result.json` here was therefore **authored by hand**, and it carries:

* `"synthetic_example": true` and a `disclaimer` field;
* `"status": "partial"`;
* `artifacts` entries with `bytes`/`sha256` set to `null` and a note that they were not produced — a
  fabricated hash would be worse than an honest null;
* `evidence[].verified: false` and `"match": "unverified-example"`;
* `"audit": {"gates": [], "checks_passed": 0, …, "determinism": "unknown"}` — no gate verdict is
  claimed;
* an `uncertainty` entry (`U3`) stating exactly this.

When the pipeline is integrated, re-run the chain above and replace this file with the real output —
then it becomes a worked example of a passing run instead of an illustration. Until then, **read it
as a schema example, never as a result**.

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
| `examples/synthetic-001/**` is generated from the fixture, not hand-copied | **executed** — the derivation tool re-reads the fixture and asserts the §3.4 rules; result: `problems: []` |
| every `source.quote` in `expected/spec.json` equals the exact text of its cited span | **executed** — asserted during derivation (a non-equal span aborts with exit `1`) |
| every numeric `value` occurs inside its own quote | **executed** — asserted during derivation |
| the worked example passes a real `build`/`check` | **not yet run** — the scripts were not present at freeze; re-run and record the exit codes |
| `expected/result.json` is a real run's output | **no** — it is illustrative and labelled `synthetic_example: true` |

> Never trust a number in this directory that is not accompanied by the command that produced it.
> The two files that *are* measured facts are the template's sha256 and the derived example's rule
> checks; everything else waits for the pipeline.
