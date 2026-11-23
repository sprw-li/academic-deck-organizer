# Failure modes — observed behaviour per class

One row per failure class this Skill is expected to meet: what you see, which exit code, which gate
reports it, what `result.json` ends up saying, and what the honest handling is. The rubric's
异常处理 point asks about exactly this table, so the table is written so that each row names a
**checkable** behaviour rather than a good intention.

Contract: `CONTRACT.md` §5 (gates A–J), §6 (tool discovery, "no step raises"), §7 (definition of
done).

---

## 1. Failure classes the gates are responsible for

| # | Class | How it appears | Exit | Gate | `result.json` | Correct handling |
|---|---|---|---|---|---|---|
| 1 | Missing key value in a material | the number you need is not in the text | — | **A** if you invented it | `partial` | omit the number; put the gap in `uncertainty`; if the page no longer works, cut the page |
| 2 | Claim without evidence | a content page whose `evidence_ids` is empty | `1` | **A** | `partial` | cite the material, or drop the claim. `title`/`qa`/`backup` pages are exempt — only content pages are checked |
| 2 | Quote not verbatim | paraphrased or retyped quote | `1` | **A** / `E902` | `partial` | copy the sentence from the material; never rewrite the quote |
| 3 | Unresolvable locator | wrong file name, wrong page/line, stale locator | `1` | **A** / `E901` | `partial` | re-run `ingest`, cite a locator that exists |
| 4 | Value outside its quote | real citation, invented number | `1` | **A** / `E903` | `partial` | find a source that contains it, or drop the number |
| 5 | Slide count ≠ budget | pages added and `slide_count` not updated | `1` | **B** | `partial` | cut or add a page; the count is fixed by the brief |
| 6 | Timing off budget | `Σ timing_s` outside ±15 % of `minutes × 60` | `1` | **B** | `partial` | rebalance `timing_s` between pages; do not pad the total. Note the denominator: `minutes` is **speaking time and excludes `qa_minutes`** |
| 7 | Claim too long / empty | a paragraph in the claim field | `1` | **B** | `partial` | split the page or cut the sentence to ≤ 160 chars |
| 8 | Arc missing an element | no `method`/`results`/`problem`/`conclusion` | `1` | **B** | `partial` | restore the arc element; `hook`/`gap`/`limitation` are free choices |
| 9 | Unknown layout | a layout name not in the index | `1` | **C** | `partial` | use an indexed name; check `role_map` |
| 10 | Body in a text box | hand-made box instead of the placeholder | `1` | **C** | `partial` | move the text into the inherited placeholder |
| 11 | Off-theme font/colour | a colour outside the template theme | `1` | **C** | `partial` | inherit the theme, or declare `theme.source: override` and justify it |
| 12 | Notes missing / duplicating bullets | notes ≈ bullets (similarity ≥ 0.8) | `1` | **D** | `partial` | rewrite `notes.say` to add what the bullets cannot say |
| 13 | Page body is a flat image | a screenshot pasted as the page | `1` | **D** | `partial` | rebuild as real text in placeholders |
| 14 | Figure path missing | `figure.media_path` (absolute) points at nothing, or only `media_rel` was given | `1` | **E** | `partial` | bind the absolute `media_path` that `ingest` recorded; keep `media_rel` as the readable form |
| 15 | Figure not cited on its page | figure on the slide, id absent from `evidence_ids` | `1` | **E** | `partial` | add the id to that page's `evidence_ids` |
| 16 | Caption differs, unmarked | shortened caption presented as the source's | `1` | **E** | `partial` | reuse the source caption verbatim (the `adapted` marker is not yet frozen — see [`template-and-render.md`](template-and-render.md) §5) |
| 16b | Figure with no caption | the source figure genuinely has no caption | — | **A** (substituted criterion) | `partial` | leave the `quote` **empty** — never invent a caption — and make sure the figure is judged by “locator resolves + `media_path` exists with `sha256 == media_sha256`”; the gap **must** appear in `uncertainty` (`autoplan` adds a `U2` entry). An undeclared no-caption figure is a gate **J** problem |
| 17 | Page count mismatch after export | `deck.pdf` pages ≠ `.pptx` slides | `1` | **F** | `partial` | rebuild and re-render; never patch the PDF |
| 18 | Preview page missing | a slide has no PNG | `1` | **F** | `partial` | re-render; if the renderer is flaky, record it |
| 19 | Manifest disagrees with disk | sha256/dpi/page count mismatch | `1` | **F** | `partial` | re-render at a fixed dpi |
| 20 | No renderer available | `renderer-unavailable` | `0` | **F** | `partial` (warn) | deliver the editable `.pptx`; declare the fallback |
| 21 | Non-deterministic build | two builds, different `deck.pptx` sha256 | `1` | **G** | `partial` | find the leaked clock/order/RNG and remove it |
| 22 | `output_contract` key empty | a key with no value | `1` | **H** | `partial` | fill it (a truthful run summary is acceptable); never leave it empty |
| 23 | Artifact path dangling | `artifacts` points at a missing file | `1` | **H** | `partial` | rebuild; fix the recorded path |
| 24 | Fewer than 3 Q&A items | no Q&A preparation | `1` | **I** | `partial` | add questions covering mechanism/data/control/limitation |
| 25 | Dangling question ids | a `qa` entry cites a non-existent slide/evidence id | `1` | **I** | `partial` | fix the ids |
| 26 | `high` risk question unanswered | `risk: "high"`, empty answer | `1` | **I** | `partial` | answer it, or downgrade only if it truly is not high risk |
| 27 | Status dishonest | gates failed, `status: ok` | `1` | **J** | corrected | make `status` match the gate verdicts |
| 28 | Undeclared missing material | `parsed: false` but not mentioned | `1` | **J** | `partial` | copy it into `uncertainty`/`limitations` |
| 29 | Illegal spec | structural validation fails | `4` | — (pre-gate) | `failed`/`partial` | fix the field paths in the error report |

Note the exit codes: only gate **failures** produce `1`. A missing artefact produces `3`, a usage/IO
error `2`, and an illegal spec `4` before any gate runs. Renderer-unavailable exits `0` on purpose —
the run succeeded in producing the deliverable it could produce, and the *status* carries the
degradation.

---

## 2. What "honest degradation" looks like in `result.json`

For any of the warn/degraded classes:

```json
{ "status": "partial",
  "uncertainty": [ {"id":"U1","what":"no renderer was found, so no deck.pdf or preview exists",
                    "why":"renderer discovery (CONTRACT.md §6) found no office-render-tool",
                    "impact":"the deck can be opened and edited but not previewed in this run"} ],
  "limitations": ["deck.pdf and preview/*.png are absent from this run"],
  "audit": {"gates":[{"id":"F","status":"warn","detail":"renderer-unavailable (0 files)"}],
             "checks_passed": 9, "checks_failed": 0, "checks_warned": 1,
             "determinism": "byte-identical"} }
```

`status` may be `ok` only when no gate failed and no warn is outstanding. `needs_human` is for the
case where the material itself requires a decision you cannot make (an ambiguous figure ownership, a
contradiction between two materials).

---

## 3. Materials that cannot be used

| Situation | Recorded as | Never |
|---|---|---|
| PDF text extraction failed | `parsed: false`, `parse_note` set, file in `limitations` | quote it from the abstract on a website |
| a figure has no usable resolution | `dpi_estimate` reported; `uncertainty` entry | upscale it and present it as the source figure |
| a font is missing at export time | `render_manifest.json` `missingFonts`, `limitations` entry | claim the deck rendered as designed |
| two materials contradict each other | `uncertainty` entry naming both | pick the convenient one silently |
| a table is truncated in the material | `limitations` entry | complete the row from context |

The rule behind the table: **the deck may be weaker than the paper, but it may not be stronger.**
Every step down is declared.

---

## 4. Checks that do **not** exist (the boundary of the claim)

Stated explicitly so that nobody mistakes this Skill for something it is not:

* **No scientific-quality gate.** The gates verify provenance, budget, template compliance,
  editability, export and honesty. They cannot tell whether your framing of the science is good, and
  they do not try. A deck can pass A–J and still be a bad talk.
* **No completeness gate.** Nothing checks that you covered the paper's main line. A missing result
  is invisible unless it makes a claim unsupported.
* **No semantic matching of claim to quote.** `E902` proves the quote is verbatim; it does not prove
  the claim is what the quote means. A claim can be, in effect, an overclaim built from a real
  sentence — a human reviewer is the defence.
* **No baseline comparison inside the Skill.** The platform performs the with/without comparison;
  `gain_check` measures defect interception, not model quality (see [`measured-gain.md`](measured-gain.md)).
* **No model-request accounting.** The budget numbers in [`runbook.md`](runbook.md) §3 are a plan;
  the platform records the actual request count and native trace.

These are the honest edges. They are also the reason the Skill keeps the reasoning in `spec.json`:
what cannot be checked mechanically has to be visible to a human.
