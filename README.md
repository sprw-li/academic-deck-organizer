# academic-deck-organizer — organize the pages, keep the evidence map

Turn existing research materials into a talk that can actually be given: **one claim per page**, every
number traceable to a locator plus a verbatim quote, an explicit audience/time/slide brief, speaker
notes that add what the bullets cannot say, a Q&A sheet for the questions that will be asked, and a
deck built on the organizer's own master/layouts with a real rendered preview.

* `SKILL.md` — what an Agent loads and follows (the procedure, the gate table, the failure table).
* `scripts/` — the deterministic pipeline (`pt.py` CLI + the module CLIs of `CONTRACT.md` §4).
* `references/` — the deep documentation (schema, runbook, provenance, template/render, failures, measured gain, rubric mapping).
* `examples/` — a worked **synthetic** example, clearly labelled as such.

The design rule: **the LLM authors the science into `spec.json`; the scripts own every layout,
export and check.** Same spec plus same template in → byte-identical `deck.pptx` out.

**What this Skill is not.** It is not a paper reader (论文精读), not a manuscript writer or polisher
(学术写作成稿) and not a figure factory (科研绘图素材生产). Those belong to neighbouring topics. It
starts from material that exists and ends at pages that can be presented, with the evidence map that
makes the pages reviewable.

---

## 1. Requirements and dependencies

| Package | Version verified on the author machine | Used for | If missing |
|---|---|---|---|
| Python | **3.13.x** | everything | — |
| `python-pptx` | **1.0.2** | building the editable deck on the template's master/layouts; reading the template index; normalising the package | the whole Skill is unavailable — this one is not optional |
| `pdfplumber` | **0.11.11** | PDF text first choice | the PDF extractor cascade is tried instead (`CONTRACT.md` §6) |
| `pypdf` | **7.1.1** | PDF text fallback, PDF page counting | a CLI extractor is tried; page counts come from parsing `/Count` |
| `Pillow` | **13.0.0** | resolving image sizes/dpi for extracted figures | figure metadata is recorded as unknown rather than invented |
| `matplotlib` | **3.11.2** | only used by `scripts/make_fixture.py` to generate the fixture PDF/PNG | the runtime chain does **not** need it; the fixture cannot be regenerated |
| `numpy`, `pandas`, `lxml`, `beautifulsoup4` | present on the author machine | convenience in parsing paths | each parsing path degrades to stdlib |
| Node.js + `libreoffice-kit` | `node` + `libreoffice-kit/lib/cli.js` | converting `.pptx` → `.pdf` and rendering the per-page previews | `render_deck` returns `renderer-unavailable` without raising; gate F warns and `status` becomes `partial` |

Install the optional extras:

```
python3 -m pip install python-pptx pdfplumber pypdf Pillow matplotlib
```

The renderer is discovered, never hard-coded: an `office-render-tool`/`office-renderer` executable on
`PATH` → `DSH_OFFICE_NODE` + `DSH_OFFICE_CLI` → the bundled `libreoffice-kit/lib/cli.js` plus `node`
(`CONTRACT.md` §6). There is no service, network call, credential or hand-editing step anywhere in the
pipeline.

## 2. Install

Copy the Skill directory to wherever the runtime mounts skills, e.g.:

```
/skills/weekly-skill/SKILL.md
/skills/weekly-skill/scripts/*.py
/skills/weekly-skill/references/*.md
/skills/weekly-skill/examples/**
```

No build step, no packaging, no compilation. From any shell:

```
python3 scripts/pt.py --help
```

### Local development aside (Windows)

The authors develop on Windows; the code is portable and **no absolute path is baked into any
script** — every path resolves from CLI arguments or from the input/output directories. On Windows use
the `py`/`python` launcher instead of `python3`, and export

```powershell
$env:PYTHONIOENCODING='utf-8'          # gate output contains CJK and would otherwise raise
$env:PYTHONDONTWRITEBYTECODE='1'       # keep __pycache__ out of the delivery tree
```

The generated `.pptx` is normalised (fixed zip timestamp, sorted entries), which is what makes gate G
meaningful: `python-pptx` on its own stamps entries with the wall clock, so two saves merely *near* in
time look identical by coincidence.

## 3. Quickstart

Given a case directory `/input` containing `case.json`, `task.md` and `materials/**`:

```
python3 scripts/pt.py ingest --input /input --work /app/work
#   ... author /app/work/spec.json from materials.json + evidence-ledger.json (this is the LLM step) ...
python3 scripts/pt.py inspect --input /input --work /app/work
python3 scripts/pt.py build  --spec /app/work/spec.json --work /app/work --out /app/output
python3 scripts/pt.py render --out /app/output --dpi 144
python3 scripts/pt.py result --input /input --work /app/work --out /app/output   # BEFORE check: gate H reads it
python3 scripts/pt.py check  --out /app/output --spec /app/work/spec.json --work /app/work
python3 scripts/pt.py result --input /input --work /app/work --out /app/output   # re-run so status/audit reflect the gates
```

**The order is `build → render → result → check → result`.** Gate **H** validates `result.json`, so
running `check` before `result` makes H report a missing result and exit `1` even when every other gate
passed. The second `result` folds the gate verdicts into `status`/`audit`; a green `check` followed by
`result` is what yields `status: "ok"`.

One-shot, with the deterministic no-LLM fallback spec:

```
python3 scripts/pt.py run --input /input --work /app/work --out /app/output
```

`run` uses `/app/work/spec.json` when it exists, otherwise derives one with `plan`, then builds,
renders, checks and writes `result.json` — and exits with the **check** exit code. It guarantees
artefacts but not quality; an authored spec is what the rubric rewards.

See [`examples/README.md`](examples/README.md) for the same sequence run against the bundled synthetic
fixture (which first needs `python3 scripts/make_fixture.py --out /tmp/deck-fixture`).

## 4. CLI reference

| Command | Arguments | Writes |
|---|---|---|
| `pt.py run` | `--input DIR --work DIR --out DIR` | the whole chain; returns the `check` exit code |
| `pt.py ingest` | `--input DIR --work DIR` | `work/materials.json`, `work/evidence-ledger.json` |
| `pt.py inspect` | `--input DIR --work DIR [--template T] [--out J]` | `work/template-index.json` (the template defaults to the one named in `case.json`) |
| `pt.py plan` | `--work DIR --input DIR --out SPEC [--template-index T]` | the deterministic fallback `spec.json`; the index is discovered from `--work` when the flag is omitted |
| `pt.py build` | `--spec SPEC --work DIR --out DIR` | `deck.pptx`, `deck_plan.json`, `speaker-notes.md`, `evidence-map.json`, `qa-prep.md` |
| `pt.py render` | `--out DIR [--dpi 144]` | `deck.pdf`, `preview/page-####.png`, `render_manifest.json`, `export_report.json`; the deck is taken from `<out>/deck.pptx` |
| `pt.py result` | `--out DIR --input DIR --work DIR [--status S]` | `result.json` with every `output_contract` key |
| `pt.py check` | `--out DIR --spec SPEC --work DIR` | `validation_report.json` (gates A–J) |
| `pt.py edit` | `--spec SPEC --edits EDITS.json --out SPEC2` | the edited spec, with the change appended to `change_records` |
| `pt.py selftest` | — | **24 checks, measured 24/24 PASS (exit `0`)** — incl. injected-defect negatives and a full-chain determinism check |
| `pt.py contract_matrix` | — | ≥5 adversarial `output_contract` key sets |
| `pt.py gain_check` | — | the controlled A/B report for 使用增益; **no arguments needed** (`gain-ab.json` lands in `$DECK_GAIN_WORK` or the system temp dir, not in the delivery tree) |
| `scripts/template.py inspect` | `--template X [--out Y]` | `--out` omitted: print the index; the module-level form of `pt.py inspect` |
| `scripts/ingest.py ingest` | `--input DIR --work DIR` | the module-level form of `pt.py ingest` |
| `scripts/build.py build` | `--spec S --work W --out O` | the module-level form of `pt.py build` |
| `scripts/render.py render` | `--pptx P --out O [--dpi 144]` | the module-level form of `pt.py render` — here the deck path **is** a flag |
| `scripts/validate.py check` | `--out O --spec S --work W` | the module-level form of `pt.py check` |
| `scripts/deckplan.py plan` | `--work W --input DIR [--template-index T] --out SPEC` | the module-level form of `pt.py plan`; here the index **can** be pinned |
| `scripts/make_fixture.py` | `--out DIR [--with-starter-slide] [--quiet]` | a complete synthetic `/input`-shaped fixture; **not** part of the runtime chain |

> **Where the shipped CLI and `CONTRACT.md` §4 differ** (both are stated here so neither reading is
> hidden):
> * `pt.py render` accepts only `--out`/`--dpi` and takes the deck from `<out>/deck.pptx`, whereas
>   §4's module-level signature is `render --pptx P --out O [--dpi 144]`. Use
>   `scripts/render.py render --pptx …` when the deck lives outside `--out`. §4 documents this
>   pass-through split explicitly.
> * the flags of `pt.py result` and `pt.py edit` are **not frozen** in §4 (only `run` is). The table
>   above lists what the shipped `pt.py` accepts; confirm with `--help`.
> * `scripts/evidence.py` / `scripts/measure.py` do have CLIs, but §4 leaves their argv shape
>   undefined, so **no command line for either is documented anywhere in this bundle**.
>
> `pt.py inspect` and `pt.py plan --template-index` are **not** differences: §4 lists them, and the
> shipped CLI implements them. Every command in this bundle was checked mechanically against §4 plus
> the shipped argparse surface — see
> [`references/rubric-mapping.md`](references/rubric-mapping.md) §"Command-surface audit".
>
> All four are reported to the Lead as contract-vs-implementation gaps, so that one surface can be
> frozen before packaging. Every command in this bundle was checked mechanically against §4 plus the
> shipped argparse surface — see [`references/rubric-mapping.md`](references/rubric-mapping.md)
> §"Command-surface audit".

### Exit codes

| Code | Meaning | Emitted by |
|---|---|---|
| `0` | success / all gates passed (warns allowed) | all commands |
| `1` | at least one validation gate failed | `check`, `run` |
| `2` | usage or IO error (bad flags, missing input dir, unwritable work dir) | all commands |
| `3` | a required artefact is missing (e.g. `check`/`result` before `build`) | `check`, `run` |
| `4` | `spec.json` is structurally illegal | `build` (and `run` on a rejected authored spec) |

A missing renderer is **not** an exit code: `render_deck` returns
`{"ok": false, "reason": "renderer-unavailable"}`, gate F warns, and `status` becomes `partial`. The
editable `.pptx` is still delivered — that is the honest partial, not a failure.

## 5. Inputs and outputs

**Input** (`/input`): `case.json` (its `output_contract` names the JSON keys to return, and its
constraints carry the fixed audience/time/slide count), `task.md`, and materials under
`/input/materials/**` — markdown, txt, csv, json, xlsx, **PDF** (parsed for real text, no pre-extracted
answer layer) and images, plus the `.pptx` template.

**Output** (`/app/output`):

| File | Kind | What it is |
|---|---|---|
| `deck.pptx` | editable deliverable | real template master/layouts, real text in inherited placeholders, notes in the notes slides |
| `deck_plan.json` | plan | per page: slide → layout → which placeholder carries what → claim → evidence → figure → notes |
| `speaker-notes.md` | notes | the per-page script, human readable |
| `evidence-map.json` | provenance | page-level claim → evidence ids → locator → quote → strength |
| `qa-prep.md` | Q&A | questions, answers, cited slides, risk, category |
| `deck.pdf` | export | a real conversion of the built `.pptx` |
| `preview/page-####.png` | export | one raster per slide |
| `render_manifest.json` | record | render evidence: page count, dpi, per-file sha256, missing fonts |
| `export_report.json` | record | which converter/renderer ran and what it reported |
| `validation_report.json` | record | gates A–J with the measured detail for each |
| `result.json` | record | the structured case result (below) |

### `result.json`

Carries `case_id`, `status` (`ok`/`partial`/`needs_human`/`failed`), `artifacts` (path/bytes/sha256),
`sources` (each material + sha256), `evidence` (per-claim source + resolution), `page_map`, `qa`,
`change_records`, `uncertainty`, `limitations`, `audit` (`gates`, `checks_passed/failed/warned`,
`unfilled_fields`, `unmapped_contract_keys`, `determinism`) and `runtime` (`seconds`, `tools`).

Every key named by the case's `output_contract` is also emitted at top level, **non-empty**: a key with
no derivable value is filled with a truthful run summary and listed in `audit.unmapped_contract_keys`,
so an indirect fill is disclosed rather than passed off as a direct mapping. `status` can be `ok` only
when no gate failed and no warn is outstanding.

## 6. Template and export parameters

| Parameter | Value |
|---|---|
| Template | the case's own `.pptx`; master and layouts are **inherited**, never restyled |
| Slide size / aspect | read from the template (the fixture is 13.333 × 7.5 in, 16:9) |
| Theme | `theme.source: "template"` by default; fonts and colours must come from the template theme or be declared as an `override` with a reason |
| Body text | written into the layout's inherited placeholders (`CONTRACT.md` §5 gate C fails a hand-made text box) |
| Render dpi | `144` for previews (`--dpi` overridable); the manifest must match what was produced |
| Export | `deck.pdf` page count must equal the `.pptx` slide count (gate F) |
| Determinism | `build` twice → identical `deck.pptx` sha256 (gate G); the fixture template is byte-stable at `d4d7e8e5…` |
| Notes | must not repeat the bullets: normalised similarity < 0.8 (gate D) |

Full detail, including the `template-index.json` shape, the role→layout map, placeholder rules and the
renderer discovery order: [`references/template-and-render.md`](references/template-and-render.md).

## 7. Worked example

See [`examples/README.md`](examples/README.md). `examples/synthetic-001/` is **synthetic and labelled
as such** — a fictional damp-heat perovskite study, 9 pages with 18 evidence items and 4 Q&A items. It
demonstrates the interface end to end and must never be presented as an official case result.
`expected/spec.json` is the authored DeckSpec, `expected/deck_plan.json` and `expected/result.json`
are the **measured output of a real pipeline run** on it (2026-11-23 ~13:47 +08:00: `status: "ok"`,
gates **A–J 10/10 pass**, `exit 0`, `deck.pptx` 84,165 B / sha256 `8ed411b6…`, `runtime.seconds`
32.34). They carry `synthetic_example: true` and a `measurement` block naming the run, so they are
never mistaken for an official result. The run used the container-path `media_path` rewritten to the
local fixture file (the same bytes) because `/input` exists only inside the runtime container — that
single substitution is disclosed in `result.json.measurement.spec_note`.

The binary materials (`slides-template.pptx`, `paper.pdf`, `fig1.png`) are generated, not shipped:

```
python3 scripts/make_fixture.py --out /tmp/deck-fixture
```

## 8. Reuse guide

To build a deck for your own case:

1. **Copy the example skeleton**: `cp -r examples/synthetic-001 /tmp/mycase`, replace
   `input/materials/**` and `input/case.json`. Keep `output_contract` — it is what the evaluator reads
   back. Generate the binary materials with `make_fixture.py` or supply the real ones.
2. **Run `ingest`** and read `materials.json` / `evidence-ledger.json`. Every locator you will quote
   lives there; do not invent line or page numbers.
3. **Copy `expected/spec.json` as your template** and rewrite it section by section:
   `meta.case_id`, `meta.audience`, `meta.budget`, `meta.story`, then each page's `role`, `claim`,
   `evidence_ids`, `bullets`, `notes`, `strength`. Replace the `evidence[]` list with your own, one
   item per fact. Change the theme only if the new template demands it.
4. **Rebuild and re-check** until `check` exits `0`, then re-run `result` so `status` reflects it.
5. **Reuse the gates**: `check` is case-independent, so it is also your regression suite. Add a case to
   `examples/` only if it is synthetic or fully publishable.

The narrative vocabulary (roles, arc, claim-per-page, `strength`, Q&A binding) and the gate list are
reusable as-is; the only case-specific things are `spec.json` and the materials it cites.

## 9. Resource budget

The platform allows **≤ 50 UTF-8 text files, ≤ 500 KB each, ≤ 2 MB total, no symlinks**
(`task/task.md:88`). This bundle ships **text only**; the generated binaries are produced into a
scratch directory by `make_fixture.py` and are never part of the submission.

Measured for the docs/examples write scope (excluding `__pycache__`) — the full-bundle figure is
re-measured at packaging, after the `scripts/` owners have integrated:

| Metric | Limit | Measured (2026-11-23 ~12:33 +08:00; these two files are part of the total, so any further wording edit shifts it by tens of bytes — re-measure at packaging) |
|---|---|---|
| text files | ≤ 50 | **19** (docs + examples write scope) |
| largest file | ≤ 500 KB | **24,631 B** (`references/rubric-mapping.md`) |
| total bytes | ≤ 2 MB | **≈ 220 KB** (219,923 B at measurement) |
| symlinks | 0 | 0 |
| binaries in the shipped tree | — | 0 |
| `__pycache__` / `.pyc` | 0 | 0 |

Re-measure with:

```
python3 -c "import os;fs=[os.path.join(d,f) for d,_,g in os.walk('.') for f in g if '__pycache__' not in d]; \
print('files',len(fs),'total_bytes',sum(os.path.getsize(f) for f in fs),'largest',max((os.path.getsize(f),f) for f in fs))"
```

The official structural check is
`python3 _shared/tools/validate_skill_submission.py <submission-dir>`, which must exit `0` and enforces
all four limits plus the frontmatter and marked-example rules. **Measured `0`** on the whole tree at
2026-11-23 ~13:5x: `{"status":"ok","files":33,"bytes":…,"name":"academic-deck-organizer","errors":[]}`.

### Submission-root files that are not part of the Skill tree

| File | Contents |
|---|---|
| `GITHUB_URL.txt` | **required by the platform** (`task.md` §"本题作品的 GitHub 公开链接"): line 1 = the public URL of this work, line 2 = the relative path of the work inside it. Shipped as `https://github.com/sprw-li/academic-deck-organizer` + `SKILL.md`. Verify it in a **logged-out** browser before submitting. |

## 10. Maintenance

* **Frozen interfaces.** The `spec.json` schema (DeckSpec v1, `CONTRACT.md` §3.4), the CLI surface of
  §4 and the artefact names of §3.5 are the contract. Changing one means updating
  [`references/spec-schema.md`](references/spec-schema.md) and
  [`references/runbook.md`](references/runbook.md) in the same commit.
* **Adding a gate** means adding a check to `validate.py` *and* a row to the gate table in `SKILL.md`
  and [`references/rubric-mapping.md`](references/rubric-mapping.md). Never add a gate that cannot
  produce a pass/fail for a concrete artefact.
* **Negative-test first.** `pt.py selftest` runs injected-defect cases, so a change that only keeps the
  happy path working is caught.
* **Determinism is a regression target**, not a hope: a build reports the `deck.pptx` sha256, and gate G
  compares two of them. If it goes false, stop and fix the cause.
* **Documentation discipline**: every command documented must be a command that was run, and every
  number quoted must have a command behind it. Anything unverified is labelled unverified — see the
  evidence tables in [`references/runbook.md`](references/runbook.md) §8 and
  [`references/rubric-mapping.md`](references/rubric-mapping.md) §"Evidence of execution".

## 11. Licence and attribution

* **Original work.** The Skill's scripts, schema, references and synthetic fixture were written for
  this submission. No third-party source code is vendored into the bundle.
* **Third-party use is by import only** (`python-pptx`, `pdfplumber`, `pypdf`, `Pillow`, `matplotlib`),
  under their own licences; they are dependencies and are not redistributed here.
* **The bundled template is generated, not borrowed.** `make_fixture.py` starts from python-pptx's
  built-in default presentation, renames the master and all 11 layouts and rewrites three theme colour
  slots. It is a fixture, not a branded template, and carries no third-party design.
* **The bundled example is synthetic.** `examples/synthetic-001` is invented material, labelled
  `SYNTHETIC EXAMPLE - NOT AN OFFICIAL CASE` in its `case.json`, `task.md`, paper excerpt, figure
  notes, spec, plan and result (only the bare data table `results.csv` has no in-file banner — CSV has
  no comment syntax, so it is labelled by the files around it). It is not a measurement and must never
  be cited as one.
* **No benchmark claim.** No score, runtime or comparison against a baseline Agent is claimed anywhere
  in this bundle; the platform performs that comparison. `gain_check` measures defect interception
  only — see [`references/measured-gain.md`](references/measured-gain.md) §4.

## 12. Documentation map

| File | Read it for |
|---|---|
| [`SKILL.md`](SKILL.md) | the Agent-facing procedure, gate table and failure table |
| `GITHUB_URL.txt` (submission root) | the public link to this work and its path inside the repository |
| [`references/spec-schema.md`](references/spec-schema.md) | every `spec.json` field, its rule and its allowed values |
| [`references/runbook.md`](references/runbook.md) | the exact command sequence, exit codes, budget plan, recovery |
| [`references/provenance-and-evidence.md`](references/provenance-and-evidence.md) | locator grammar, quote rules, `E901`–`E903`, the evidence ledger |
| [`references/template-and-render.md`](references/template-and-render.md) | template-index, role→layout mapping, placeholders, renderer discovery |
| [`references/failure-modes.md`](references/failure-modes.md) | 29 failure classes with exit code, gate and correct handling |
| [`references/measured-gain.md`](references/measured-gain.md) | the controlled A/B and what it does **not** measure |
| [`references/rubric-mapping.md`](references/rubric-mapping.md) | scoring point → artefact → proving command |
| [`examples/README.md`](examples/README.md) | the worked synthetic example and how to regenerate its fixture |
