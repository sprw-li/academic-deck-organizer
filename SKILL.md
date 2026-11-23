---
name: academic-deck-organizer
description: Organize an academic talk from existing research materials into a template-compliant, editable slide deck with a per-page evidence map, speaker notes and Q&A preparation: decide which pages exist, the single claim on each page, which material locator and verbatim quote backs it, what the speaker says beyond the bullets, and what the audience will ask, then let deterministic scripts lay out, render, export and gate the deck. Use when the user asks for a conference / group-meeting / defense / progress-report deck, 学术汇报 / 组会汇报 / 答辩 PPT / 评审汇报, a slide plan or page-by-page storyboard from a paper or report, speaker notes or a Q&A brief for a talk, or to reorganize an existing deck against a fixed template, audience, time limit and slide count. Do NOT use for close reading or summarizing a paper for understanding alone (论文精读), for writing, translating or polishing the paper manuscript itself (学术写作成稿), or for producing the figures or media assets that would appear on the slides (科研绘图素材生产) - those belong to neighbouring topics; this Skill owns only what appears on the pages, their order, their provenance, their notes and their export quality. Also do NOT use it for designing experiments, for statistical analysis, or for inventing content when material is missing - it records the gap and lowers the status instead.
---

# academic-deck-organizer — organize the pages, keep the evidence map

Turn existing research materials into a talk that can actually be given: **one claim per page**,
every number traceable to a real locator plus a verbatim quote, an explicit audience/time/slide
budget, speaker notes that add what the bullets cannot say, and a Q&A sheet for the questions that
will be asked. The deck comes out editable, built on the supplied master/layouts, with a real
rendered preview and a gate report that can be re-run.

## What the LLM decides vs. what the scripts do

This split is the point of the Skill — do not blur it.

| Work | Who | Where |
|---|---|---|
| Which pages exist, their order and role; the single claim on each page; which evidence id backs it; what the speaker says; what will be asked | **You (the agent / a human)** | authored into `spec.json` (DeckSpec v1) |
| Layout resolution, placeholder filling, text measurement, notes rendering, deterministic `.pptx` writing, PDF/PNG export, gates A–J, `result.json` | **Deterministic scripts** | `scripts/*.py`, never hand-edited |

Corollary: you **never** hand-edit `deck.pptx`, `speaker-notes.md` or a preview. Change `spec.json`
(or apply `edit` and record it in `change_records`) and rebuild. The scripts guarantee that the same
`spec.json` plus the same template produces a byte-identical `deck.pptx` (decks are zip-normalised,
so no wall-clock timestamp leaks in).

The three things this Skill deliberately does **not** own: reading the paper for you
(论文精读), writing or polishing the manuscript (学术写作成稿), and producing the figures or
media assets themselves (科研绘图素材生产). It starts from material that exists and ends at pages
that can be presented.

## Budget

64 model requests, 900 s wall clock, 16384 tokens per response. Plan on **one** ingest, one authored
spec with at most two repair loops, one build, one check, one result. `materials.json` is the cache —
do not re-read material you have already ingested. Save honest partials early (`status: partial`) and
improve incrementally; the run is over long before a perfect deck is.

## Procedure

### 0. Verify the inputs before anything else

Read the real files. Never infer content from a filename, a case label or a domain guess.

```
/input/case.json      # case_id + output_contract (the JSON keys result.json must return)
/input/task.md        # what this case asks for
/input/materials/**   # the actual evidence: paper text/PDF, CSV, captions, the deck template
```

Check three things in particular, because they are the ones that silently ruin a deck:
the **template** (a real `.pptx` with a master/layout set to inherit), the **audience/time/slide
count** constraints from `case.json`, and whether a **PDF** is present at all (a PDF is parsed for
real text; a missing or unreadable one is a `parse_note`, not a licence to guess).

If a material is empty, unreadable or missing, record it in `limitations` and set `status: partial`.
Do **not** substitute a plausible-looking number.

### 1. Ingest

```
python3 scripts/pt.py ingest --input /input --work /app/work
```

Writes `/app/work/materials.json` (per-file text, `sha256`, locators, figures, tables) and
`/app/work/evidence-ledger.json` (candidate evidence items with resolvable spans). Exit `0` = ok,
`2` = usage/IO. Read both before authoring; the ledger is your menu of citable facts.

### 2. Inspect the template, then author `spec.json`

The template index must exist before `plan` can map roles to layouts:

```
python3 scripts/pt.py inspect --input /input --work /app/work
```

That writes `/app/work/template-index.json`. The equivalent module-level CLI — the form frozen in
`CONTRACT.md` §4 — is `python3 scripts/template.py inspect --template X --out Y`; use it when the
template lives outside `/input`.

```
python3 scripts/pt.py plan --work /app/work --input /input \
    --template-index /app/work/template-index.json --out /app/work/spec.json
```

`--template-index` is optional: `pt.py plan` discovers the index from `--work` when it is omitted.
The module-level form frozen in `CONTRACT.md` §4 is
`python3 scripts/deckplan.py plan --work W --input DIR [--template-index T] --out SPEC`.

`plan` is the deterministic floor: it writes a structurally valid spec from the materials when you
are out of budget or out of judgement. It is **not** the target. The target is an authored spec —
that is the only creative step, and it is where the quality points live. Full field reference:
[`references/spec-schema.md`](references/spec-schema.md). What the index contains, and which layouts
this Skill maps to which role, is in
[`references/template-and-render.md`](references/template-and-render.md).

Shape of the authored spec (abridged — the schema file lists every field and every rule):

```json
{ "schema_version": 1,
  "meta": { "case_id": "…", "language": "zh|en", "deck_title": "…", "aspect": "16:9",
            "template": {"path": "materials/slides-template.pptx", "sha256": "…"},
            "audience": {"id": "committee|group-meeting|collaborators|public",
                          "prior_knowledge": "high|medium|low",
                          "concerns": ["…"], "expected_challenges": ["…"]},
            "goal": {"statement": "…", "success_checks": ["…"]},
            "budget": {"minutes": 12, "slide_count": 9, "qa_minutes": 3},
            "story": {"arc": ["hook","problem","gap","method","results","limitation","conclusion"],
                       "rationale": "…"} },
  "theme": { "colors": {"primary": "#1F3864", "accent": "#C00000", "text": "#000000", "muted": "#595959"},
             "fonts": {"heading": "Calibri Light", "body": "Calibri"}, "source": "template" },
  "evidence": [ {"id":"E1","kind":"number","claim":"…","value":2.8,"unit":"fold",
                 "source":{"file":"paper.pdf","locator":"paper.pdf#p.4","quote":"…verbatim…"}} ],
  "pages": [ {"id":"P4","role":"results","layout":"Deck Figure","claim":"one sentence",
              "evidence_ids":["E1"],
              "bullets":["…"],
              "figure":{"evidence_id":"E2","media_path":"/input/materials/fig1.png",
                        "media_rel":"materials/fig1.png",
                        "placement":"right","caption":"…"},
              "notes":{"say":"…","timing_s":90,"checkpoint":"…"},
              "strength":"strong|moderate|preliminary|mixed"} ],
  "qa": [ {"id":"Q1","question":"…","answer":"…","evidence_ids":["E1"],"slide_ids":["P5"],
            "risk":"high|medium|low","category":"mechanism|data|control|limitation|other"} ],
  "uncertainty": [], "limitations": [], "change_records": [] }
```

Rules the validator enforces — grill on these, they are mechanical:

* `pages[].id` and `evidence[].id` globally unique; `evidence_ids` must resolve.
* every **content** page — `hook`/`problem`/`gap`/`method`/`results`/`limitation`/`conclusion` —
  needs at least one `evidence_id`; `title`, `qa` and `backup` pages are exempt because they do not
  carry a claim of their own.
* `figure.media_path` is **absolute** (the form the renderer can actually open, e.g.
  `/input/materials/fig1.png`); `media_rel` is the human-readable relative form.
* `layout` must exist in `template-index.json`; `role` must be one of the enum values.
* `claim` non-empty and ≤ 160 characters; **one claim per page**, not a paragraph.
* content pages (anything that is not `title`/`qa`/`backup`) need non-empty `notes.say`, and the
  normalised similarity between `notes.say` and the joined `bullets` must be **< 0.8** — notes that
  restate the bullets are a defect, not a style choice.
* `meta.budget.slide_count` must equal the number of pages; `Σ notes.timing_s` must be within
  ±15 % of `meta.budget.minutes`.
* `story.arc` must contain `problem`, `method`, `results` and `conclusion`.
* every `evidence[].source` must resolve through the locator grammar and the `quote` must match
  the material **verbatim** after whitespace normalisation; a numeric `value` must occur inside its
  own quote. See [`references/provenance-and-evidence.md`](references/provenance-and-evidence.md).

### 3. Build (deterministic)

```
python3 scripts/pt.py build --spec /app/work/spec.json --work /app/work --out /app/output
```

Emits `deck.pptx` (real template master/layout, real text in inherited placeholders, notes in the
notes slide), `deck_plan.json`, `speaker-notes.md`, `evidence-map.json`, `qa-prep.md`.
Exit `0` = ok, `2` = IO, `4` = the spec is structurally illegal (the failing field paths come back
in the error report). Rebuilding the same spec is byte-identical.

### 4. Render and export the preview

```
python3 scripts/pt.py render --out /app/output --dpi 144
```

The `pt.py` pass-through takes the deck from `<out>/deck.pptx`. The module-level form frozen in
`CONTRACT.md` §4 spells the input out — `python3 scripts/render.py render --pptx P --out O
[--dpi 144]` — and is the one to use when rendering a deck that lives elsewhere.

Writes `deck.pdf`, `preview/page-0001.png …`, `render_manifest.json` (real dpi/page count/missing
fonts/sha256) and `export_report.json`. If no renderer is found the Skill does **not** raise: it
records `renderer-unavailable`, keeps the editable `.pptx`, and gate F becomes a warn that lowers
`status` to `partial`. That is a correct, gradeable outcome — see
[`references/template-and-render.md`](references/template-and-render.md).

### 5. Write `result.json`

```
python3 scripts/pt.py result --input /input --work /app/work --out /app/output
```

Merges `case.json.output_contract` keys with the real run: `case_id`, `status`
(`ok`/`partial`/`needs_human`/`failed`), `artifacts` (path/bytes/sha256), `sources`, `evidence`,
`page_map`, `qa`, `uncertainty`, `limitations`, `change_records`, `audit`, `runtime`. Every
`output_contract` key must come back **non-empty**: unmappable keys are filled with a truthful run
summary and listed in `audit.unmapped_contract_keys`, never left empty.

Run this **before** the gates and again after them. Before the gates it honestly reports
`status: partial`; gate H reads `result.json`, so a `check` with no result present fails H.

> `result` and `edit` are `pt.py` subcommands whose accepted flags are **not frozen** in
> `CONTRACT.md` §4 (only `run --input --work --out` is). The flags above are the ones the shipped
> `pt.py` accepts today (`result --out --input --work [--status]`, `edit --spec --edits --out`), so
> they are the implementation's surface rather than the contract's; confirm with
> `python3 scripts/pt.py result --help` before relying on a flag.

### 6. Check the gates

```
python3 scripts/pt.py check --out /app/output --spec /app/work/spec.json --work /app/work
```

Runs gates **A–J** and writes `validation_report.json`. Exit `0` = all pass (warns allowed),
`1` = at least one gate failed, `3` = artefacts missing, `2` = usage/IO. Then re-run
`python3 scripts/pt.py result --input /input --work /app/work --out /app/output` so `status` and
`audit` reflect the verdicts; a green `check` followed by `result` is what yields `status: "ok"`.

| Gate | Proves | Fails when |
|---|---|---|
| **A** | evidence traceability | any **content** page (`hook`/`problem`/`gap`/`method`/`results`/`limitation`/`conclusion`) has no `evidence_ids` — `title`/`qa`/`backup` are exempt; any evidence locator fails to resolve (`E901`), its quote is not verbatim (`E902`), or its number is not inside the quote (`E903`). **figure exception:** a `kind: "figure"` item is judged by “locator resolves + `media_path` exists with a sha256 matching `media_sha256`”, and its `quote` **may be empty** when the source figure has no caption — never invent one — but the gap must be written into `uncertainty` |
| **B** | story and budget | page count ≠ `budget.slide_count`; `Σ notes.timing_s` off `budget.minutes` by more than ±15 % (`minutes` is **speaking time and excludes `qa_minutes`**); a content-page claim empty or > 160 chars; `story.arc` missing `problem`/`method`/`results`/`conclusion` |
| **C** | template compliance | a page layout is not in the template index; `deck.pptx` slide count ≠ spec page count; body text sits in a hand-made textbox instead of the inherited placeholder; fonts/colours are outside the template theme without `theme.source: override` |
| **D** | editability and notes | any page's body is a flat image instead of text; notes missing, or notes/bullets similarity ≥ 0.8 |
| **E** | figure binding | a referenced figure's `media_path` (which is **absolute**) does not exist; the figure's evidence id is not in that page's `evidence_ids`; the caption differs from the material caption while `caption_adapted` is `false`/absent |
| **F** | render and export | `deck.pdf` page count ≠ `.pptx` page count; a preview page is missing; `render_manifest.json` disagrees with the files on disk (sha256/dpi); renderer unavailable → **warn** + `status: partial` |
| **G** | determinism | two `build` runs of the same spec give different `deck.pptx` sha256 |
| **H** | contract completeness | any `output_contract` key missing/empty in `result.json`; an `artifacts` entry points at a file that does not exist |
| **I** | Q&A preparation | fewer than 3 `qa` items; a `qa` entry has a dangling `slide_ids`/`evidence_ids`; a `high` risk question has no answer |
| **J** | honesty | a gate failed while `status: ok`; `uncertainty`/`limitations` exist but are not in `result.json`; a material with `parsed: false` is not declared |

### 7. One-shot form

```
python3 scripts/pt.py run --input /input --work /app/work --out /app/output
```

Runs the whole chain (ingest → authored spec if present, else the deterministic plan → build →
render → result → check → result) and returns the **check** exit code. Use it when budget is tight:
it always yields a valid, honestly-limited deck. It is a floor, not the target.

### 8. Verify the Skill itself (before trusting a run)

```
python3 scripts/pt.py selftest          # 24 checks, measured 24/24 PASS
python3 scripts/pt.py contract_matrix   # >= 5 adversarial output_contract key sets
python3 scripts/pt.py gain_check        # controlled A/B; no arguments needed
```

All three exit `0` on the shipped bundle. `contract_matrix` matters most for an **unseen** case: the
evaluator may name its outputs anything, and an unknown key must be filled truthfully rather than
emptied. `gain_check` measures defect interception against a naive "summary + Results pasted page by
page" baseline — it is **not** a model-quality benchmark and claims no benchmark score. See
[`references/measured-gain.md`](references/measured-gain.md).

### Smoke run on the bundled synthetic fixture (definition of done)

`examples/synthetic-001/input/` is text-only, because the bundle ships no binaries (§0), so the
definition-of-done path of `CONTRACT.md` §7.1 cannot be run verbatim from the shipped tree. Generate
the binary materials first, then run against the scratch directory — **this two-command form is the
reproducible smoke run**:

```
python3 scripts/make_fixture.py --out /tmp/deck-fixture
python3 scripts/pt.py run --input /tmp/deck-fixture --work /tmp/deck-work --out /tmp/deck-out
```

`/tmp/deck-fixture` is a complete `/input`-shaped case whose text materials are the same files as
`examples/synthetic-001/input/**`; expected result: `run` exits `0` with `status: "ok"` and gates A–J
all pass (at most one declared warn). `python3 scripts/pt.py run --input examples/synthetic-001/input`
alone cannot work, because the template `.pptx` and the PDF it needs are not in the bundle.

## When a gate fails

Never "fix" a deck by editing an export. Read `validation_report.json` → the failing gate's measured
detail, then act on the cause.

| Symptom | Exit | Do this |
|---|---|---|
| Spec rejected structurally | `4` | Fix `spec.json` (the error lists the failing field paths), rebuild. |
| Gate **A** provenance | `1` | Three distinct causes: unresolved locator (`E901`) → re-run `ingest` and use a locator that exists; quote not verbatim (`E902`) → copy the sentence exactly as it appears; value not in quote (`E903`) → find material that really contains the number, or drop the number and record the gap in `limitations`/`uncertainty`. **Do not invent a quote, and do not rewrite the number to match the quote.** |
| Gate **B** budget/story | `1` | Cut or add a page so the count matches `budget.slide_count`; move `timing_s` between pages to bring the total within ±15 %; shorten any claim over 160 characters; restore the missing arc element. |
| Gate **C** template | `1` | Use a layout name from `template-index.json`; put body text in the inherited placeholder rather than a new textbox; if a colour or font really must leave the template theme, declare `theme.source: "override"` and say why in `change_records`. |
| Gate **D** notes | `1` | Rewrite `notes.say` so it adds what the bullets cannot say (what to emphasise, what to concede, what to check if asked). Do not shorten the notes to game the similarity score. |
| Gate **E** figure | `1` | Use the material's own figure path; make sure the figure's evidence id is in the page's `evidence_ids`; reuse the source caption verbatim or mark the adaptation. |
| Gate **F** render | `1`/warn | Check that a renderer exists (§6 of `CONTRACT.md`); if fonts are missing, install a capable font and record the fallback in `limitations`. The `.pptx` stays editable either way. |
| Gate **G** determinism | `1` | A non-deterministic input leaked into the build (system time, dict order, RNG, an un-normalised zip). Remove it and report it; do not ship a deck you cannot rebuild. |
| Gate **H** contract | `1` | Compare `case.json.output_contract` keys with `result.json`; re-run `result` without a `--status` override. |
| Gate **I** Q&A | `1` | Add questions until there are at least 3; repair dangling slide/evidence ids; answer every `high` risk question. |
| Gate **J** honesty | `1` | Make `status` match the gates; copy `uncertainty`/`limitations` into `result.json`; declare every `parsed: false` material. |
| Artefacts missing | `3` | Run `build` and `render` before `check`/`result`. |
| Module/usage error | `2` | Check the invocation against `python3 scripts/pt.py <cmd> --help`. |

If after two repair loops a gate still fails, **stop and ship honestly**: set `status: partial`, put
the exact failing gate id and reason into `uncertainty`, and tell the user what is unresolved. A
partial result that names its limits scores; a silent fabrication does not.

## Do not fabricate (hard rule)

The evidence map is the contract between the deck and reality.

* **Every number, figure and quotation that reaches a page carries a locator and a verbatim quote.**
  Gate A fails (`E901`/`E902`/`E903`) when a citation does not resolve, when the quote is not
  verbatim, or when the number is not inside its own quote — so a real citation cannot launder an
  invented number. The check is mechanical, not advisory.
* If the material does not contain the value you need, you may **not** supply one. Omit it, or state
  the gap in `limitations` / `uncertainty` and lower `status`.
* Never invent an n, a p-value, a unit, a time point, an author or a citation; never restate a number
  you did not read; never let a placeholder survive into the deck.
* Never present the bundled synthetic example as an official case result, and never claim a
  benchmark score or a runtime you did not measure.
* When material is missing, ambiguous or contradictory, say so in `uncertainty` and set
  `status: partial` (or `needs_human`). That is a correct outcome, not a failure.

## Revision without re-authoring

```
python3 scripts/pt.py edit --spec /app/work/spec.json --edits /app/work/edits.json \
    --out /app/work/spec2.json
python3 scripts/pt.py build --spec /app/work/spec2.json --work /app/work --out /app/output
```

`edit` applies spec-level changes and appends them to `change_records`, keeping stable ids and
provenance. Prefer it to retyping the spec: it preserves the edit history the verifiability point
asks for. (As noted in step 5, the `edit` flag surface is not frozen in `CONTRACT.md` §4 — confirm
with `--help`.)

## Reference files

| File | Read it for |
|---|---|
| [`references/spec-schema.md`](references/spec-schema.md) | every `spec.json` field, its rule and its allowed values |
| [`references/runbook.md`](references/runbook.md) | the exact command sequence, exit codes, budget plan, recovery |
| [`references/provenance-and-evidence.md`](references/provenance-and-evidence.md) | locator grammar, quote rules, `E901`–`E903`, the evidence ledger |
| [`references/template-and-render.md`](references/template-and-render.md) | template-index, role→layout mapping, placeholders, renderer discovery, export checks |
| [`references/failure-modes.md`](references/failure-modes.md) | observed behaviour per failure class (exit code, gate, `result.json`) |
| [`references/measured-gain.md`](references/measured-gain.md) | the measured A/B for 使用增益 and what it does **not** measure |
| [`references/rubric-mapping.md`](references/rubric-mapping.md) | which artefact + command proves each scoring point |
| [`README.md`](README.md) | dependencies, install, CLI reference, reuse, licence, resource budget |
| [`examples/README.md`](examples/README.md) | the worked **synthetic** example and how to regenerate its fixture |

## What this Skill is not

* It is not a paper reader: it does not perform close reading, literature review or critique
  (论文精读).
* It is not a writer: it does not draft, translate or polish the manuscript (学术写作成稿).
* It is not a figure factory: it does not create the charts or media that go on the slides
  (科研绘图素材生产); it binds material that exists to the pages that cite it.
* It does not invent content to fill a required page count; it cuts pages or declares the gap.
