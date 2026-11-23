# Measured gain — the controlled A/B, and what it does **not** measure

The 使用增益 dimension (25 points) asks whether loading this Skill improves the result over a base
Agent on the same input, and whether that improvement is **verified by a fair comparison**. This file
describes the comparison this bundle ships and, just as importantly, its limits.

Contract: `CONTRACT.md` §7.4 — `gain_check.py` runs a controlled A/B on the same fixture, injecting
≥ 4 real defects, and exits `0` only when the Skill intercepts **≥ 3** of them while the naive
baseline intercepts fewer.

---

## 1. The two conditions

| | A — naive baseline | B — this Skill |
|---|---|---|
| Input | the same fixture, byte-identical | the same fixture, byte-identical |
| Story | "summary + Results pasted page by page" | authored `spec.json`: one claim per page, role-based order |
| Provenance | none — numbers are retyped from the text | each number carries a locator + verbatim quote |
| Budget | page count and timing ignored | `budget.slide_count` and ±15 % timing enforced |
| Notes | a copy of the bullets, or absent | `notes.say` distinct from bullets (< 0.8 similarity) |
| Gates | **not run** — the deck ships as-is | gates A–J run; fail/warn recorded in `result.json` |
| Output | a `.pptx` | `.pptx` + plan + notes + evidence map + Q&A + render manifest + `result.json` |

Both conditions use the **same injected spec** for every defect cell, so the comparison is
reproducible and falsifiable: the only thing that differs is whether the validation layer exists.

### Why the baseline is not a strawman

The baseline is the strategy the topic description names as the typical current failure: take the
abstract, paste the Results section page by page, and ship. It is not a deliberately broken program.
It produces a real, openable deck, and a naive reader would accept it — which is exactly the point.
The defects it ships are the ones a competent agent makes when nothing checks it: a number retyped
from memory, a claim nobody sourced, a slide count that grew past the time slot, notes that repeat the
bullets. A comparison against a baseline that cannot produce a deck would prove nothing.

### What the same input means

"Same input" is enforced at the file level: both conditions read the same case directory, and the
fixture for both is the one `scripts/make_fixture.py` generates. Per defect cell the two conditions
receive an **identical spec**; B additionally runs the gates over the artefacts that spec produces.
The cell passes only if B reports the defect and A does not, so neither condition can win by producing
more files.

## 2. The injected defects

| # | Injected defect | Which gate must catch it | Why it is a realistic failure |
|---|---|---|---|
| **D1** | a **fabricated number** (a value absent from the materials) placed in a content page's bullets | **A** (number traceability) | the most dangerous failure mode of this topic: a real-looking slide carrying a value that is not in any material |
| **D2** | a page claim with **no evidence id** | **A** | "everyone knows this" sentences that are not in the materials |
| **D3** | slide count ≠ `budget.slide_count` | **B** | the classic "deck grew until it no longer fits the slot" |
| **D4** | **notes duplicating the bullets** | **D** | notes that add nothing make the speaker read the slide aloud |
| **D5** | a figure caption **not matching the source caption** and not marked adapted | **E** | a re-worded caption that quietly strengthens the claim |

The exact cell set is fixed in `scripts/gain_check.py`; each cell records **both exit codes**, so the
comparison can be re-run by anyone.

## 3. What is measured

* **Defect interception**: how many of the injected defects A ships versus how many B catches —
  measured **0/5 vs 5/5**. A baseline that ships a defective deck with exit `0` is the interesting
  case: it is what "no validation layer" means in practice.
* **Delivered verification artefacts**: whether the run produced an evidence map, a gate report, a
  render manifest and a `result.json`, and whether those are internally consistent (sha256/bytes
  re-checked). A can only produce a `.pptx`.
* **Determinism**: B's build is re-run and the `deck.pptx` sha256 compared (gate G), which is what
  makes the deck reproducible rather than merely produced.

## 4. What is **not** measured — read this before quoting a number

* **Not a model-quality benchmark.** The A/B does not run a base LLM agent against a Skill-loaded
  agent; that comparison is performed by the platform on the fixed cases. Nothing here should be
  cited as a score, a ranking or a measured improvement over a competing agent.
* **Not a wall-clock speedup.** No runtime advantage is claimed. The efficiency point is argued from
  structure (one spec → all artefacts, byte-identical rebuild, `edit` instead of re-authoring), not
  from a stopwatch.
* **Not a completeness measure.** The defects are chosen to be *interceptable by the gates*. The
  cells are a falsifiable demonstration of the validation layer, not a sample of all the ways an
  academic talk can go wrong.
* **Not all defects are caught.** If a cell is not intercepted, it is reported as not intercepted.
  Rounding an interception count up in prose — the "3/4 becomes 4/4" move — is exactly the kind of
  claim this Skill refuses to make.

## 5. How to re-run it

```
python3 scripts/pt.py gain_check      # or: python3 scripts/gain_check.py
```

No arguments are needed: the harness builds its own synthetic fixture, runs both conditions and
passes or fails itself. Exit `0` = the Skill intercepted at least three of the injected defects
**and** more than the baseline. The report is written to `gain-ab.json` under `$DECK_GAIN_WORK` when
that environment variable is set, otherwise under the system temp directory — it is **not** written
into the delivery tree.

## 6. Measured results

Measured on 2026-11-23 ~13:48 +08:00 with `python3 scripts/pt.py gain_check` → **exit `0`**
(`python3 scripts/gain_check.py` → exit `0` as well). Both conditions read their own synthetic
fixture built by the harness; per defect cell the two conditions receive an identical spec.

| Metric | Measured | Target (`CONTRACT.md` §7.4) |
|---|---|---|
| baseline (A) defects intercepted | **0 / 5** | 0 expected |
| Skill (B) defects intercepted | **5 / 5** | ≥ 3 |
| `gain_check` exit code | **`0`** | `0` |
| per-cell gate that did the intercepting | D1→**A**, D2→**A**, D3→**B**, D4→**D**, D5→**E** | — |
| B-side exit code per defect cell | `1` (a gate failed, as it should) | — |

Verbatim console summary from that run:

```
D1  fabricated-number                target=A  A=False B=True  b_exit=1  b_fail=A,I,J
D2  page-claim-without-evidence      target=A  A=False B=True  b_exit=1  b_fail=A,I,J
D3  page-count-budget-mismatch       target=B  A=False B=True  b_exit=1  b_fail=B,I,J
D4  notes-duplicate-body             target=D  A=False B=True  b_exit=1  b_fail=D,G,I,J
D5  caption-not-matching-source      target=E  A=False B=True  b_exit=1  b_fail=E,I,J
totals: A=0 B=5 (需 B>=3 且 B>A) → exit=0
```

Read that honestly: the B-side `b_fail` lists the **target gate first** and then the collateral gates
that also complain about the deliberately broken cell (`I`/`J` follow because a defective spec has no
proper Q&A block and a status that no longer matches). Only the target gate is the interception; the
rest are consequences. The harness reports interception **counts**, not scores and not magnitudes.

One further measured detail, stated because it is visible in the same report: the **clean control
cell** (no injected defect) reports gates `I` and `J` as failing on the harness's *own* synthetic
fixture — that fixture's spec carries fewer than three Q&A items and a status/gate disagreement. It is
a property of the harness's throwaway fixture, not of the example in this bundle: the shipped
`examples/synthetic-001` run passes **A–J 10/10 with exit `0`** (see
[`rubric-mapping.md`](rubric-mapping.md) §"Evidence of execution"). It does not affect the
interception counts above, which compare A and B on the same defective cells.

## 7. Why a controlled A/B at all

The rubric asks for 增益可验证性 (5 points). A prose claim that "the Skill produces better decks" is
not verifiable by a reader. A fixed fixture, a fixed defect list, two conditions, both exit codes and
a pass rule that can fail — that is. It is also the reason the baseline is deliberately *not*
strawmanned: it is the actual strategy a competent agent uses when it has no validation layer
(summarise, paste the Results section, ship), which is the behaviour the topic description names as
the typical current failure.

## 8. Claim ledger — what this bundle does and does not claim

Written down so that no reader has to infer the boundary from the prose.

| Claim | Made? | Evidence |
|---|---|---|
| the Skill **detects** the injected defect classes that the naive baseline ships | **yes** | `pt.py gain_check` → exit `0`, **A 0/5 vs B 5/5** (D1→A, D2→A, D3→B, D4→D, D5→E), measured 2026-11-23 |
| the Skill **delivers verification artefacts** the baseline does not (evidence map, gate report, render manifest, `result.json`) | **yes** | the output listing of both conditions in the same report |
| the deck is **reproducible** from the same spec | **yes** | gate G's two-build sha256 comparison |
| an unknown `output_contract` key is filled truthfully instead of emptied | **yes** | `pt.py contract_matrix` |
| the Skill **outperforms a base LLM agent** | **no** | not measured here; that comparison is the platform's |
| the Skill is **faster** than manual authoring or than the baseline | **no** | no timing was taken |
| the Skill catches **every** defect class | **no** | an un-intercepted cell is reported as un-intercepted |
| the deck is **scientifically good** | **no** | no gate evaluates scientific quality; a deck can pass A–J and still be a bad talk |

The last four rows are the honest edge. They are also why this file says "defect interception"
everywhere instead of "quality improvement": the two are not the same claim, and only the first one is
measurable from inside the bundle.
