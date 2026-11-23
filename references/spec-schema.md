# `spec.json` — DeckSpec v1, field by field

`spec.json` is the **only** thing you author. It carries every judgement the pages depend on; the
scripts turn it into a deck. The frozen shape is `CONTRACT.md` §3.4; this file is the working
reference, and it names the gate that checks each rule so you can predict `check` before running it.

Path: `/app/work/spec.json` (the `plan` subcommand writes a deterministic fallback there; you
overwrite it with the authored spec).

> **Rule of thumb.** If a change alters *what the audience is told*, it belongs in `spec.json`.
> If it alters *how something is drawn*, it does not — and a change you cannot express in the spec
> is a change you do not get to make.

---

## 1. Top level

| Field | Type | Required | Rule / gate |
|---|---|---|---|
| `schema_version` | int | yes | `1` for DeckSpec v1. |
| `meta` | object | yes | §2 below. |
| `theme` | object | yes | §3 below; gate **C**. |
| `evidence` | array | yes | §4 below; gates **A**/**E**. |
| `pages` | array | yes | §5 below; gates **A**–**E**. |
| `qa` | array | yes | §6 below; gate **I**. |
| `uncertainty` | array | yes (may be `[]`) | §7; gate **J**. |
| `limitations` | array of string | yes (may be `[]`) | §7; gate **J**. |
| `change_records` | array | yes (may be `[]`) | §8; 结果可核验性. |

An empty array is a **claim** that there is nothing to declare. Do not use `[]` to mean "I did not
check": gate J fails when `uncertainty`/`limitations` are non-empty in the run but missing from
`result.json`, and a material that failed to parse must be declared somewhere.

---

## 2. `meta` — the brief

| Field | Type | Rule |
|---|---|---|
| `case_id` | string | must be the `case_id` from `/input/case.json`; it is echoed into `result.json` and is what the evaluator reads back. |
| `language` | `"zh"` \| `"en"` | drives speaking-rate estimation (`speaking_seconds`) and text fitting (`text_fits`). Mixed-language decks: use the language of the majority of the body text. |
| `deck_title` | string | the title as it should appear, not the filename. |
| `aspect` | `"16:9"` | must match the template's slide size; a mismatch is a template issue, not a ratio you get to choose. |
| `template.path` | string | the template actually used, e.g. `materials/slides-template.pptx`. |
| `template.sha256` | string | hash of that template; it is what makes "same spec + same template" a checkable statement. |
| `audience.id` | `committee` \| `group-meeting` \| `collaborators` \| `public` | drives how much background the deck may skip. |
| `audience.prior_knowledge` | `high` \| `medium` \| `low` | |
| `audience.concerns` | array of string | what this audience is actually worried about; put it in the notes, not on the slides. |
| `audience.expected_challenges` | array of string | the objections that become `qa` items. |
| `goal.statement` | string | what the talk must achieve, in one sentence. |
| `goal.success_checks` | array of string | how you would know afterwards; keep them checkable. |
| `budget.minutes` | number | **speaking time, excluding `budget.qa_minutes`**. Gate **B** compares `Σ notes.timing_s` against `minutes × 60` (not `(minutes + qa_minutes) × 60`) with a ±15 % tolerance. |
| `budget.slide_count` | int | **must equal** `len(pages)`. Gate **B**. |
| `budget.qa_minutes` | number | Q&A time, counted separately from `minutes`; drives how many `qa` items you owe (≥ 3, gate **I**). |
| `story.arc` | array of role names | must contain `problem`, `method`, `results`, `conclusion` (gate **B**); the rest is your ordering. |
| `story.rationale` | string | why this order serves this audience. This is read by a human grader; write it as an argument, not a label. |

`budget.slide_count` is the most common self-inflicted gate B failure: pages get added while the
count is not updated. Decide the count first, then author to it — that is exactly what a fixed
organizer brief forces, and the gate enforces it.

---

## 3. `theme` — what the template already decided

```json
"theme": { "colors": {"primary": "#1F3864", "accent": "#C00000",
                       "text": "#000000", "muted": "#595959"},
           "fonts": {"heading": "Calibri Light", "body": "Calibri"},
           "source": "template" }
```

| Field | Rule / gate |
|---|---|
| `colors.*` | gate **C**: every colour must come from the template theme unless `theme.source` is `"override"`. |
| `fonts.*` | same rule as colours. Values are the template's theme fonts, not your preference. |
| `source` | `"template"` (default, inherit everything) or `"override"` (you deliberately leave the template's theme). |

An `override` is not a free pass: it must be justified in `change_records`, and the reviewer will read
the justification. The correct default is to inherit and to change the *content*, not the look.

---

## 4. `evidence` — the citable pool

```json
{ "id": "E1", "kind": "number|figure|table|section|quote",
  "claim": "what this item asserts, in one line",
  "value": 2.8, "unit": "fold",
  "source": { "file": "paper.pdf", "locator": "paper.pdf#p.4",
              "quote": "…verbatim text from the material…" } }
```

| Field | Rule / gate |
|---|---|
| `id` | globally unique across `evidence` (gate **A**). Referenced by `pages[].evidence_ids` and `qa[].evidence_ids`; a dangling id fails. |
| `kind` | one of the five enum values. `number` items should carry `value` + `unit`; `quote`/`section` items need neither. For `kind: "figure"` see the exception below. |
| `claim` | the one-line meaning you are attributing to the source. It must not assert more than the quote says. |
| `value`, `unit` | required for `number`; the number must occur **inside its own `quote`** (`E903`). |
| `source.file` | basename of the material as `ingest` recorded it (no directory). Duplicate basenames resolve to the first and are reported as `ambiguous`. |
| `source.locator` | the locator grammar of `CONTRACT.md` §3.1 — see [`provenance-and-evidence.md`](provenance-and-evidence.md). `E901` if it does not resolve. |
| `source.quote` | the material text, verbatim apart from whitespace normalisation. `E902` if it does not match. |

### The `kind: "figure"` exception

A figure sourced from a PDF may have **no caption at all**. Gate A then judges it by a different
rule: the locator must resolve **and** the figure's `media_path` must exist in the materials with a
`sha256` equal to its recorded `media_sha256` (both live in `materials.json`/`pages[].figure`).

* The `quote` **may be empty** in that case. An empty quote is the honest state; inventing a caption
  is not.
* The gap is not allowed to be silent: it must appear in `uncertainty` (`autoplan` adds a `U2` entry
  for it automatically when it derives the spec). A figure with no caption that is not declared is a
  gate **J** problem.
* If the material does carry a caption, cite it verbatim like any other quote — that is the stronger
  evidence and the bundled example does exactly that.

Three practical rules that save a rebuild:

1. **Author evidence first, pages second.** Every page cites ids; discovering mid-page that a number
   has no source is how you end up rewriting `bullets` under time pressure.
2. **One item per fact.** If two pages cite the same retention number, that is one `E` entry used
   twice — not two entries with slightly different quotes.
3. **Cite the narrowest span.** A whole-page locator resolves but makes the claim unreviewable;
   the point of the mapping is that a reviewer can jump to the sentence.

---

## 5. `pages` — the talk itself

```json
{ "id": "P6", "role": "results", "layout": "Deck Figure",
  "claim": "After 1000 h the delta-phase fraction is 31.5 % in the reference film and 4.2 % with Cs",
  "evidence_ids": ["E3","E4","E12","E14","E15"],
  "bullets": ["31.5 % vs 4.2 % delta phase at 1000 h", "Read-out agrees with the efficiency ranking"],
  "figure": {"evidence_id": "E12", "media_path": "/input/materials/fig1.png",
             "placement": "right", "caption": "Figure 1. …",
             "caption_adapted": false},
  "notes": {"say": "…", "timing_s": 115, "checkpoint": "plot read once, left to right"},
  "strength": "strong" }
```

| Field | Rule / gate |
|---|---|
| `id` | globally unique (gate **A**); referenced by `qa[].slide_ids`. |
| `role` | `title` \| `hook` \| `problem` \| `gap` \| `method` \| `results` \| `limitation` \| `conclusion` \| `qa` \| `backup`. Anything else is a validation error. |
| `layout` | must exist in `template-index.json` (gate **C**). |
| `claim` | non-empty, ≤ 160 characters, **one claim**. A claim with an "and" in the middle is usually two pages. |
| `evidence_ids` | non-empty for every **content** page (gate **A**); `title`, `qa` and `backup` pages are exempt because they carry no claim of their own. Each id must exist. |
| `bullets` | the on-screen lines. They are shown, so they must not carry a number that no evidence item covers. |
| `figure` | optional. `evidence_id` must also be in this page's `evidence_ids` (gate **E**); `media_path` must be **absolute** and must exist (gate **E**) — `media_rel` is a `materials.json` field (§3.1), not a DeckSpec field; `placement` ∈ `right` \| `left` \| `full` \| `none`. |
| `figure.caption` | reuse the material's caption verbatim, or shorten it deliberately and set `caption_adapted: true` (see below). |
| `figure.caption_adapted` | **bool** (default `false`). `true` declares that the on-screen caption is a deliberate rewrite of the source caption. Gate **E** then records it as `adapted` and does **not** fail it. Gate E also accepts a **prefix-truncated** caption (a screenshot or a shortened form that is a prefix of the source and at least 20 chars / 40 % of its length) as `一致(截断)` — truncation by the layout is a legitimate handling, not a defect. |
| `notes.say` | required and non-empty on content pages; normalised similarity to the joined `bullets` must be **< 0.8** (gate **D**). |
| `notes.timing_s` | seconds; `Σ` within ±15 % of `60 × budget.minutes` (gate **B**). |
| `notes.checkpoint` | what must be true before moving on. Not machine-checked; it is the field that keeps a 12-minute talk on the clock. |
| `strength` | `strong` \| `moderate` \| `preliminary` \| `mixed`. This is the 结论强度 the rubric asks about: a claim resting on three temperatures is `preliminary`, not `strong`. |
| `backup_for` | optional, on backup pages: which page ids this page can be pulled up to defend. |

### Roles are a vocabulary, not a checklist

`title` and `conclusion` are unavoidable; `hook`, `problem`, `gap`, `method`, `results`,
`limitation` and `qa` are how a research talk is actually built. A deck that uses `content` for
everything is a deck that was never organized. Two rules of thumb:

* **`hook` must be a result, not an agenda.** "Here is what we found" beats "outline"; an outline
  page costs 30 s of a 12-minute talk and buys nothing.
* **`limitation` before `conclusion`.** The boundary sentence lands as competence when you say it;
  it lands as a concession when they extract it.

---

## 6. `qa` — the pressure the talk will meet

```json
{ "id": "Q3", "question": "Can a three-temperature Arrhenius fit support 1.12 eV?",
  "answer": "Only weakly; three points is the minimum for a line fit and no error bars are given…",
  "evidence_ids": ["E5","E6","E17"], "slide_ids": ["P7","P8"],
  "risk": "high", "category": "limitation" }
```

| Field | Rule / gate |
|---|---|
| count | ≥ 3 items (gate **I**). Fewer than three is treated as no Q&A preparation at all. |
| `answer` | required; **every `high` risk question must have one** (gate **I**). |
| `slide_ids` | slide ids that answer the question on screen; must exist, no dangling ids (gate **I**). |
| `evidence_ids` | evidence that backs the answer; must exist. |
| `risk` | `high` \| `medium` \| `low` — how likely the question is and how badly a weak answer lands. |
| `category` | `mechanism` \| `data` \| `control` \| `limitation` \| `other`. Covering only `data` means the mechanism and limitation questions are unprepared. |

A good `qa` set covers four families: a **mechanism** question, a **data/control** question, a
**limitation** question, and the one question about the thing you are least sure of.

---

## 7. `uncertainty` and `limitations` — honest degradation

```json
"uncertainty": [ {"id":"U1","what":"device spread is not reported","why":"materials give means only",
                  "impact":"retention numbers are point estimates"} ],
"limitations": ["1000 h window only; no outdoor validation."]
```

`uncertainty` is for things you did **not** establish; `limitations` for the boundaries that are
known and should be said out loud. Both are copied into `result.json` (gate **J**). Any `warn` from
the gates also forces `status: partial` and must appear here.

Use `uncertainty` for, at minimum: a material that failed to parse, a figure with no usable
resolution, a font fallback during export, and anything the gates reported as `warn`.

---

## 8. `change_records` — the audit trail of your own edits

```json
"change_records": [ {"at":"2026-11-23T10:12:00+08:00","what":"dropped P7 to fit 9 slides",
                      "why":"budget.slide_count is fixed at 9"} ]
```

Append-only. `edit` appends automatically; if you rewrite `spec.json` by hand, append manually.
This is what lets a reviewer reconstruct the 取舍 (trade-off) process rather than only its output —
one of the three things the topic is explicitly judged on.

---

## 9. Minimal valid spec vs. a good spec

A *valid* spec passes gates A–J. A *good* spec additionally:

* has a `claim` per page that a listener could repeat back after the talk;
* has `notes.say` that contains the sentence you would say if the projector died;
* has `strength` that matches the evidence (not every page is `strong`);
* has a `limitation` page you wrote before the audience asked;
* has `qa` items whose answers would survive a follow-up question;
* has `change_records` that show what you cut and why.

The bundled worked example (`examples/synthetic-001/expected/spec.json`) is a small instance of
all of the above: 9 pages, 18 evidence items, 4 Q&A items, 690 s of speaking time against a
12-minute budget (4.17 % deviation). It is **synthetic** and must never be cited as a case result.
Its `deck_plan.json` and `result.json` are the output of a real pipeline run on it (gates A–J 10/10
pass, `status: "ok"`, 2026-11-23), so the page→layout→evidence→gate chain can be read end to end.

### What the deterministic fallback spec looks like

If you are out of budget, `pt.py plan` (or `pt.py run` with no `spec.json`) derives one. It is
deliberately plain, and three of its behaviours are worth knowing so they are not mistaken for bugs:

* **Markdown markers are stripped** — `#`/`##` heading text never reaches a slide;
* the claim template follows `meta.language` (`zh` or `en`);
* pure section headings are skipped when it builds `bullets`.

---

## 10. Related

* Locator grammar, quote matching and the `E901`–`E903` codes:
  [`provenance-and-evidence.md`](provenance-and-evidence.md).
* Which layout to use for which role, and what `template-index.json` contains:
  [`template-and-render.md`](template-and-render.md).
* Every gate's exact fail condition: `CONTRACT.md` §5, restated with observed behaviour in
  [`failure-modes.md`](failure-modes.md).
