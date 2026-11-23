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
| 1 | a **fabricated number** attached to a real citation | **A** (`E903`) | the most dangerous failure mode of this topic: a real source laundering an invented value |
| 2 | a page claim with **no evidence id** | **A** | "everyone knows this" sentences that are not in the materials |
| 3 | slide count ≠ `budget.slide_count` / timing out of band | **B** | the classic "deck grew until it no longer fits the slot" |
| 4 | **notes duplicating the bullets** | **D** | notes that add nothing make the speaker read the slide aloud |
| 5 | figure caption **not matching the source caption** | **E** | a re-worded caption that quietly strengthens the claim |

The exact cell set is fixed in `scripts/gain_check.py`; each cell records **both exit codes**, so the
comparison can be re-run by anyone.

## 3. What is measured

* **Defect interception**: how many of the injected defects A ships versus how many B catches. A
  baseline that ships a defective deck with exit `0` is the interesting case — it is what "no
  validation layer" means in practice.
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
  Rounding "3/4" up to "4/4" in prose is exactly the kind of claim this Skill refuses to make.

## 5. How to re-run it

```bash
python3 scripts/pt.py gain_check
```

Exit `0` = the Skill intercepted at least three of the injected defects **and** more than the
baseline. The report is written next to the run (see `scripts/gain_check.py` for the exact
`work/`-relative path) and contains the per-cell exit codes for both conditions.

## 6. Measured results

> **Pending integration.** At documentation-freeze time the pipeline scripts were still being written
> by the other teammates in `CONTRACT.md` §2, so `gain_check` had not been executed. The numbers
> below are the **targets** from `CONTRACT.md` §7.4, not measurements — they are marked as such and
> must be replaced with real output before this file is quoted.

| Metric | Target (`CONTRACT.md` §7.4) | Measured |
|---|---|---|
| baseline (A) defects intercepted | 0 / 4 | **not yet run** |
| Skill (B) defects intercepted | ≥ 3 / 4 | **not yet run** |
| `gain_check` exit code | `0` | **not yet run** |

The A/B harness itself, the defect list and the acceptance rule are all fixed and reviewable now; only
the observed counts are outstanding. When they are filled in, they are pasted from the command output
with its exit code — never restated from memory.

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
| the Skill **detects** the injected defect classes that the naive baseline ships | **yes** | `pt.py gain_check` per-cell exit codes (measured value pending integration) |
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
