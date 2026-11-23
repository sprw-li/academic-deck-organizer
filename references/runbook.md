# Runbook — the exact command sequence, exit codes, budget and recovery

This file is the operational script for a single case run. Commands are written as they are run in
the runtime container (`/input`, `/app/work`, `/app/output`); the CLI surface is the one frozen in
`CONTRACT.md` §4. Where a flag is **not** frozen there, the text says so rather than inventing one.

```
PY=python3        # Windows local development: use the `py` / `python` launcher instead
```

---

## 1. The sequence

```bash
# 0. read the real inputs
cat /input/case.json            # case_id + output_contract keys
cat /input/task.md              # what this case asks for
ls  /input/materials            # what actually exists

# 1. ingest materials -> materials.json + evidence-ledger.json
python3 scripts/pt.py ingest --input /input --work /app/work

# 2. template index -> role/layout mapping
python3 scripts/pt.py inspect --input /input --work /app/work

# 3. deterministic fallback spec (then AUTHOR OVER IT)
python3 scripts/pt.py plan --work /app/work --input /input --out /app/work/spec.json

# 4. build the deck (deterministic)
python3 scripts/pt.py build --spec /app/work/spec.json --work /app/work --out /app/output

# 5. render deck.pdf + preview/*.png + render_manifest.json (tolerant of a missing renderer)
python3 scripts/pt.py render --out /app/output --dpi 144

# 6. result.json  -- BEFORE the gates: gate H reads it
python3 scripts/pt.py result --input /input --work /app/work --out /app/output

# 7. gates A-J
python3 scripts/pt.py check --out /app/output --spec /app/work/spec.json --work /app/work

# 8. result.json again -- so status/audit reflect the verdicts
python3 scripts/pt.py result --input /input --work /app/work --out /app/output
```

**The order is `build → render → result → check → result`.** Running `check` before the first
`result` makes gate **H** report a missing `result.json` and exit `1` even when every other gate
passed. The second `result` is what folds the gate verdicts into `status`/`audit`; a green `check`
followed by `result` is what yields `status: "ok"`.

The one-shot form does exactly this and returns the **check** exit code:

```bash
python3 scripts/pt.py run --input /input --work /app/work --out /app/output
```

`run` uses `/app/work/spec.json` when it exists, otherwise the deterministic plan. It guarantees
artefacts, not quality.

> **Contract vs shipped CLI — five differences, stated rather than hidden.** (1) `pt.py render` takes
> only `--out`/`--dpi`; the deck comes from `<out>/deck.pptx`, while `CONTRACT.md` §4's module-level
> signature is `render --pptx P --out O [--dpi 144]` (use `scripts/render.py render --pptx …` when the
> deck is elsewhere). (2) `pt.py inspect` exists although §4's `pt.py` subcommand list omits it;
> `scripts/template.py inspect --template X --out Y` is the §4-frozen equivalent. (3) `pt.py plan`
> does **not** accept `--template-index` (the index is discovered from `--work`), while §4's
> `deckplan.py plan` signature includes it — use the module form to pin an index file. (4) the flags
> of `pt.py result` and `pt.py edit` are **not frozen** in §4 — the ones used above are what the
> shipped `pt.py` accepts. (5) `scripts/evidence.py` and `scripts/measure.py` do have CLIs, but §4
> defines no argv shape for them, so no command line for either is documented in this bundle.
>
> Every command above was checked against `python3 scripts/pt.py <cmd> --help` on 2026-11-23 and
> against §4; see [`rubric-mapping.md`](rubric-mapping.md) §"Command-surface audit" for the table.

---

## 2. Exit codes

| Code | Meaning | Emitted by |
|---|---|---|
| `0` | success / all gates passed (warns allowed) | every command |
| `1` | at least one validation gate failed | `check`, `run` |
| `2` | usage or IO error (bad flags, missing input dir, unwritable work dir) | all commands |
| `3` | a required artefact is missing (e.g. `check` before `build`) | `check`, `run` |
| `4` | `spec.json` is structurally illegal | `build` (and `run` when an authored spec is rejected) |

Codes are the judgement surface (workspace rule: a claim that cannot produce an exit code is not a
claim). `pt.py run` and `pt.py check` share the 0/1/3 meaning; `4` means "fix the spec", not "the
deck is wrong".

**Renderer-unavailable is not an exit code.** `render_deck` returns
`{"ok": false, "reason": "renderer-unavailable"}` without raising (`CONTRACT.md` §6); gate F turns
that into a **warn**, which drags `result.json.status` to `partial`. The editable `.pptx` is still
delivered.

---

## 3. Budget plan — 64 requests, 900 s, 16384 tokens per response

| Step | Requests | Why |
|---|---|---|
| read `case.json` / `task.md` | 1 | once, at the start |
| ingest | 1 | scripts do the parsing; no model calls |
| template inspect + plan | 1 | the index is deterministic |
| author `spec.json` | 4–8 | this is the step worth spending on |
| repair loop 1 | 3–6 | usually gate A (provenance) or B (budget) |
| repair loop 2 | 3–6 | the last loop you get |
| build + render + result + check (×2) | 2–4 | commands, not reasoning |
| **reserve** | ≥ 10 | leave this much to write a truthful partial |

Wall clock: ingestion and rendering dominate. A large PDF can take tens of seconds to parse and a
deck render is seconds per page; the 900 s budget is mostly spent there, not on tokens.

Two habits that keep a run inside budget:

* `materials.json` is the cache. Re-reading a PDF you already ingested is the single most expensive
  mistake available.
* Repair from the **gate's measured detail**, not by re-reading the whole spec. Each failing gate
  names the field or the count that is wrong.

---

## 4. Degenerate probes (run these once on a new machine, not per case)

```bash
python3 scripts/pt.py ingest --input /input --work /app/work                    # 0 ok / 2 missing input dir
python3 scripts/pt.py check  --out /app/output \
    --spec /app/work/spec.json --work /app/work                                 # 3 while nothing was built
python3 scripts/pt.py build  --spec /app/work/bad-spec.json \
    --work /app/work --out /app/output                                          # 4 illegal spec
python3 scripts/pt.py check  --out /app/output \
    --spec /app/work/spec.json --work /app/work                                 # 0 after a real build
```

Expected: an ingest into an empty directory exits `2`; a check with no artefacts exits `3`; a
structurally broken spec exits `4` with the failing field paths. If any of those returns `0` on a
degenerate input, the gate is not load-bearing and the run cannot be trusted.

---

## 5. Failure recovery map

| What you see | Most likely cause | Action |
|---|---|---|
| `check` exits `1` with gate A failing | a quote that is not verbatim, or a value outside its quote | copy the sentence exactly; if the number is genuinely absent, drop the claim and record it under `limitations` |
| `check` exits `1` with gate B failing | page count drifted from `budget.slide_count`, or timing moved out of ±15 % | rebalance `timing_s`, update the count, cut the page rather than padding the timer |
| `check` exits `1` with gate C failing | a layout name not in the index, or body text in a hand-made textbox | use an indexed layout; put text in the inherited placeholder |
| `check` exits `1` with gate D failing | notes restate the bullets | rewrite `notes.say` to add what the bullets cannot say |
| `check` exits `1` with gate E failing | figure path missing, figure id not cited on that page, caption differs from the source | bind the real `media_path`, add the id to `evidence_ids`, reuse the caption verbatim |
| gate F warn | no renderer, or a missing font | still deliver the `.pptx`; declare the fallback in `limitations`; `status` becomes `partial` |
| gate G fail | a non-deterministic value reached the build | remove it (clock, dict order, RNG, zip stamp) and report it |
| gate H fail | `output_contract` key empty in `result.json` | re-run `result`; do not leave a key empty — fill it with the run summary |
| gate J fail | gates failed while `status: ok`, or a `parsed: false` material undeclared | make `status` match reality; copy the uncertainty across |
| `render` reports `renderer-unavailable` | §6 renderer discovery found nothing | check `DSH_OFFICE_NODE`/`DSH_OFFICE_CLI`, then the bundled `libreoffice-kit/lib/cli.js` + `node`; record the outcome either way |

**Two repair loops, then ship honestly.** After the second loop: `status: partial`, the exact
failing gate id in `uncertainty`, and a sentence to the user about what is unresolved. A partial
result that names its limits scores; a silent fabrication does not.

---

## 6. Verifying the Skill itself

```bash
python3 scripts/pt.py selftest          # >= 18 checks, >= 8 injected-defect negatives
python3 scripts/pt.py contract_matrix   # >= 5 adversarial output_contract key sets
python3 scripts/pt.py gain_check        # controlled A/B: naive baseline vs this Skill
```

All three must exit `0` on the shipped bundle before a run is trusted. `selftest` is the regression
suite: it includes injected defects, so a change that only keeps the happy path working is caught.
`contract_matrix` covers the unseen-case risk (the evaluator may name its output keys anything).
`gain_check` measures defect interception, not model quality — see
[`measured-gain.md`](measured-gain.md).

---

## 7. Local (author-machine) notes

* Windows: use the `py` / `python` launcher; the pure-Python parts are portable and no absolute path
  is baked into any delivered script.
* Set `PYTHONIOENCODING=utf-8` (CJK in gate output raises `UnicodeEncodeError` otherwise) and
  `PYTHONDONTWRITEBYTECODE=1` (so no `__pycache__` reaches the delivery tree).
* `python-pptx` stamps zip entries with the wall clock, so two saves in the same 2-second DOS window
  happen to compare equal. That is coincidence, not determinism — `normalize_pptx` is what makes gate
  G meaningful.
* The renderer verified on the author machine is `node` + `libreoffice-kit/lib/cli.js`; the discovery
  order in `CONTRACT.md` §6 is the authority, never a hard-coded path.

---

## 8. Evidence of execution

Recorded so that nothing in this bundle is claimed without evidence. The pipeline scripts are being
written in parallel by the other teammates named in `CONTRACT.md` §2, so at documentation-freeze
time only the fixture tooling had been executed.

| Command | Exit | Result |
|---|---|---|
| `python3 scripts/make_fixture.py --out <tmp>` | `0` | writes `case.json`, `task.md`, 3 text materials, `slides-template.pptx` (11 custom layouts, master `Deck Master`), a 3-page `paper.pdf`, an 800×600 `fig1.png`, plus `FIXTURE-MANIFEST.json` |
| fixture generator, second run into another directory, `sha256` compared | `0` | `slides-template.pptx` **byte-identical** across runs (`d4d7e8e5c9fd6c674c0e222a6163729f000264891dfd43cc2cc118f8d576a6b2`); `paper.pdf`/`fig1.png` are not byte-stable (matplotlib timestamp) |
| `python3 work/verify/docs-examples/derive_examples.py …` | `0` | regenerates `examples/synthetic-001/**` from the fixture and asserts the CONTRACT §3.4 rules: 9 pages, 17 evidence items, 4 Q&A, timing 690 s (4.17 % deviation) |
| `python3 scripts/pt.py ingest/build/render/result/check …` | **not run** | the scripts were not present at documentation freeze; the exit codes above are the frozen contract, not measurements |
| `python3 _shared/tools/validate_skill_submission.py <submission-dir>` | `0` | whole tree: `{"status":"ok","files":33,"bytes":603322,"name":"academic-deck-organizer","errors":[]}` (2026-11-23 ~12:35) |
| `python3 work/verify/docs-examples/audit_commands.py --submission <dir>` | `0` | **79 documented command forms, 0 problems** against `CONTRACT.md` §4 + the shipped `pt.py --help` |

> The last row is the honest state: **the pipeline commands in this runbook are documented, not
> measured.** Re-run this table after integration and replace the note with real exit codes; the
> numbers in `examples/expected/result.json` are likewise illustrative and marked
> `synthetic_example: true`.
