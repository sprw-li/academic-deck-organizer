# Provenance and evidence — locators, quotes, numbers, and the ledger

The topic is judged on whether **every claim on a page can be traced back to a position in the
source material**. This file defines what "traced back" means mechanically, so you can predict gate
**A** instead of discovering it after a build.

The contract is `CONTRACT.md` §3.1 (locator grammar), §3.2 (evidence ledger) and §4
(`evidence.py` signatures: `resolve_locator`, `normalize_ws`, `quote_in_span`, `number_in_quote`,
`validate_evidence`).

---

## 1. The two artefacts

| Artefact | Written by | What it is |
|---|---|---|
| `/app/work/materials.json` | `ingest` | the parsed materials: per-file text, `sha256`, `bytes`, `parsed`/`parse_note`, resolved `spans`, extracted `figures` and `tables` |
| `/app/work/evidence-ledger.json` | `ingest` | the **candidate** evidence pool: numbers, figures, tables, sections and quotable sentences, each with a resolvable span. This is your menu — cite from it |

You author `spec.evidence[]` by picking from the ledger. The ledger is there so that "I could not
find a source" is a fact about the material rather than a guess about it.

`materials.json` is also the run's cache. Re-parsing a PDF that is already in it is the most
expensive avoidable mistake in the budget (see [`runbook.md`](runbook.md) §3).

---

## 2. Locator grammar (`CONTRACT.md` §3.1)

| Locator | Means | Applies to |
|---|---|---|
| `<basename>#p.<n>` | page *n* (1-based) | PDF |
| `<basename>#L<a>-L<b>` | lines *a* to *b* inclusive (1-based) | md, txt, csv |
| `<basename>#r.<n>` | record *n* | csv, json/JSONL-style materials |
| `<basename>#fig.<n>` / `<basename>#tab.<n>` | figure / table *n* | materials with extracted figures/tables |
| `<basename>#all` | the whole file | any |

Rules that follow from the grammar:

* `basename` is the **file name only**, no directory. It is matched against the basename of
  `files[].path`; two materials with the same basename resolve to the first and are reported as
  `ambiguous`.
* Comparison is by **whitespace normalisation**: runs of whitespace collapse to a single space and
  the ends are trimmed. Nothing else is normalised — punctuation, case and ordering are all part of
  the match.
* A locator that does not resolve is `E901`. It is never silently treated as `#all`.

> **Two ambiguities in the frozen grammar, flagged rather than guessed.** (1) `#r.<n>` does not say
> whether record 1 of a CSV with a header row is the header or the first data row. (2) `#L` is
> specified for md/txt/csv, so a JSON material has to be cited with `#r.<n>` or `#fig`/`#tab`.
> The bundled example sidesteps both: it cites CSV rows with unambiguous line locators
> (`results.csv#L4-L4`) and cites the JSON captions with semantic locators
> (`figure-notes.json#fig.1`). Both gaps are reported to the Lead — they must be pinned down in
> `ingest`/`evidence`, whichever way, *before* a case depends on them.

---

## 3. The three failure codes

`validate_evidence(evidence, materials)` returns a list of issues; gate **A** fails if the list is
non-empty.

| Code | Meaning | How to fix it properly |
|---|---|---|
| `E901` | the locator did not resolve to a span | re-run `ingest`, look up the real locator in `materials.json`/the ledger, and cite that. Never "fix" it by widening to `#all` unless you genuinely mean the whole file. |
| `E902` | the `quote` is not a verbatim (whitespace-normalised) substring of the resolved span | copy the sentence from the material text. Do **not** retype it from memory and do **not** paraphrase it into a quote. **Exception:** a `kind: "figure"` evidence whose source figure has no caption may carry an **empty** quote — see §6. |
| `E903` | a numeric `value` does not occur inside its own `quote` | find material that really contains the number, or remove the number and declare the gap. **Do not change the number to match the quote, and do not change the quote to match the number.** |

`E903` is the one that catches the most dangerous mistake in this whole topic: taking a real citation
and hanging an invented number on it. The check is mechanical on purpose.

### Number matching you may rely on

`number_in_quote` must handle the formatting differences that appear in real materials, so
`2.8` matches `2.80`, a value written in scientific notation is recognised, thousands separators and
a trailing `%` or unit do not break the match. What it must **not** do is match a number that occurs
in a *different* sentence of the same material: the comparison is scoped to the cited quote.

Practical consequence: **cite the sentence, not the paragraph.** A wider quote makes `E903` easier
to pass by accident and makes the claim harder to review — which is the opposite of the point.

> Gate A scopes the "every page needs evidence" requirement to **content pages**
> (`hook`/`problem`/`gap`/`method`/`results`/`limitation`/`conclusion`). `title`, `qa` and `backup`
> pages are exempt, because they do not carry a claim of their own — a title slide whose only
> "claim" is the talk's title does not need a citation, and a backup slide exists to be shown only
> if asked. The bundled example still cites an evidence id on every page, including the title, which
> is allowed (over-citing is not an error) but not required.

---

## 4. A worked example from the bundled fixture

One evidence item from `examples/synthetic-001/expected/spec.json`, and what each check does with it:

```json
{ "id": "E1", "kind": "number",
  "claim": "Cs-incorporated absorber retains 96.2 % of its initial PCE after 1000 h",
  "value": 96.2, "unit": "%",
  "source": { "file": "paper-excerpt.md",
              "locator": "paper-excerpt.md#L12-L12",
              "quote": "whereas the Cs-incorporated absorber retained 96.2 %." } }
```

| Check | Outcome |
|---|---|
| `E901` locator resolves | `paper-excerpt.md#L12-L12` resolves to line 12 of the ingested material |
| `E902` quote is verbatim | the quote **equals** the exact text of that line (the bundled example was generated so that a cited span *is* the quote — the author tool aborts if a quote merely sits inside a wider span) |
| `E903` number inside the quote | `96.2` occurs in `whereas the Cs-incorporated absorber retained 96.2 %.` |
| gate A page binding | `P2` and `P9` cite `E1`, so the claim is traceable from the page |

The example uses all four locator families, deliberately:

| Locator form | Example | Material |
|---|---|---|
| `#L<a>-L<b>` (single line) | `paper-excerpt.md#L12-L12` | markdown |
| `#L<a>-L<b>` (line range) | `paper-excerpt.md#L23-L25` | markdown, a protocol spanning three lines |
| `#L<a>-L<b>` on a table file | `results.csv#L4-L4` | CSV data row (see the `#r.<n>` ambiguity in §2) |
| `#fig.<n>` / `#tab.<n>` | `figure-notes.json#fig.1`, `#tab.1` | JSON captions |
| `#p.<n>` | `paper.pdf#p.2` | the generated 3-page PDF |

The bundled example deliberately prefers the unambiguous locator wherever two readings exist. Its
17 items resolve to **12 distinct line spans** across two text materials (`paper-excerpt.md`,
`results.csv`) plus **two semantic locators** (`#fig.1`, `#tab.1`); several items share a span because
one sentence legitimately supports two numbers (the two activation energies, the two T80 lifetimes).

## 5. Authoring order that avoids rewrites

1. `ingest` → read `evidence-ledger.json`. Write down the numbers you actually have, with their
   locators, *before* deciding the story.
2. Author the story (`meta.story`, the page roles, the claims) against the evidence you have.
3. Only then write `evidence[]` entries, one per fact, with the quote copied from the ledger.
4. `build`, then `check`. Gate A failures at this point are formatting, not structure.

Doing it the other way round — writing the story you want and then hunting for citations — is how a
deck acquires a page whose only support is a plausible-sounding sentence that is not in the
materials.

---

## 6. Figures and tables

`ingest` extracts figures (with `caption`, an **absolute** `media_path`, a human-readable `media_rel`,
`media_sha256`, pixel size, `dpi_estimate`) and tables (with `caption`, `rows`, `n_rows`, `n_cols`)
when the material supports it. Binding rules:

* `pages[].figure.media_path` must be **absolute** (the form the renderer can open, e.g.
  `/input/materials/fig1.png`) and must **exist** (gate **E**); `media_rel` (`materials/fig1.png`) is
  for humans and is never used to open anything.
* `pages[].figure.evidence_id` must also appear in that page's `evidence_ids` (gate **E**) — a figure
  cannot support a page it is not cited on.
* `figure.caption` should be the material's own caption. If you shorten or re-word it, mark the
  adaptation; gate E fails an unmarked mismatch. (The `adapted` marker itself is not frozen in the
  §3.4 schema — see [`template-and-render.md`](template-and-render.md) §5.)

### A figure with no caption — the one legitimate empty quote

Some figures have no caption at all (a composite panel, a slide the authors exported, a figure
mentioned only in the running text). The rule is *not* "invent a caption so the quote is non-empty".
For `kind: "figure"` gate A substitutes a different, checkable criterion:

| Judged by | How to satisfy it |
|---|---|
| locator resolves | cite `#fig.<n>` (or the page) that `ingest` actually recorded |
| `media_path` exists and is readable | the absolute path, not the relative one |
| `sha256(media_path) == media_sha256` | the bytes on disk are the bytes that were ingested, which is what stops a figure being swapped after the fact |
| the gap is declared | the empty-quote figure must appear in `uncertainty` (`autoplan` adds a `U2` entry for it automatically). An undeclared one is a gate **J** problem |

If the material *does* carry a caption, cite it verbatim — that is strictly stronger evidence, and the
bundled example does exactly that (its `E12` figure quote is the caption from `figure-notes.json`).

A figure whose resolution or extraction failed belongs in `uncertainty`, not on a page pretending to
carry visual evidence.

---

## 7. What a reviewer will do with this

The verifiability point is about the *process* being re-runnable, not about prose asserting that it
was. A reviewer can:

1. open `evidence-map.json` and see, per slide, the claim → evidence ids → locator → quote → strength;
2. take any quote and search the material for it;
3. re-run `check` and get the same gate verdicts;
4. re-run `build` twice and compare `deck.pptx` sha256 (gate **G**), which is what makes "same spec in,
   same deck out" checkable rather than asserted.

Everything this Skill adds to the process exists so those four steps are possible.

---

## 8. Related

* Field reference for `evidence[]` and `pages[].figure`: [`spec-schema.md`](spec-schema.md) §4–§5.
* Gate A's exact fail condition: `CONTRACT.md` §5.
* What happens when a material cannot be parsed at all: [`failure-modes.md`](failure-modes.md) §3.
