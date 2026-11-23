# Template and render — inherit the master, prove the export

Two scoring points live here: **template compliance** (the deck must be built on the organizer's
master/layout set, not restyled around it) and **export/render** (a real, openable, previewable
artefact). Both are checked mechanically by gates **C** and **F**.

Contract: `CONTRACT.md` §3.3 (template index), §3.5 (outputs), §4 (`template.py`, `render.py`),
§5 (gates C/F), §6 (tool discovery).

---

## 1. `template-index.json` — what the scripts read

```json
{ "path": "materials/slides-template.pptx", "sha256": "…",
  "slide_size": {"w_emu":12192000,"h_emu":6858000,"w_in":13.333,"h_in":7.5,"aspect":"16:9"},
  "masters": [{"index":0,"name":"Deck Master",
               "theme_fonts":{"major":"Calibri Light","minor":"Calibri"},
               "theme_colors":{"dk1":"#000000","lt1":"#FFFFFF","accent1":"#1F3864"}}],
  "layouts": [{"index":0,"master":0,"name":"Deck Title",
               "placeholders":[{"idx":0,"type":"ctrTitle","name":"Title 1",
                                "geom":{"left":0,"top":0,"width":0,"height":0}}]}],
  "existing_slides": [], "role_map": {"title":"Deck Title","section":"Deck Section",
    "content":"Deck Content","two_col":"Deck Two Column","picture":"Deck Figure",
    "closing":"Deck Closing"},
  "role_map_source": "layout-names|heuristic|fallback", "warnings": [] }
```

Produced by:

```bash
python3 scripts/template.py inspect --template /input/materials/slides-template.pptx \
    --out /app/work/template-index.json
```

Why it matters: `spec.pages[].layout` is checked against this index (gate **C**), and the theme
fonts/colours in `spec.theme` are checked against `masters[].theme_*`. **Do not copy the theme by
hand from a screenshot of the template** — read it from the index, which read it from the XML.

### `role_map` and how a layout is chosen

| Role | `role_map` key | Rationale |
|---|---|---|
| title page | `title` | a centred title with a subtitle |
| section / statement page | `section` | title + one body block, no bullet chrome |
| ordinary content page | `content` | title + one content placeholder |
| two-column comparison | `two_col` | title + two content placeholders |
| figure page | `picture` | the layout that can hold a picture **and** a caption |
| closing page | `closing` | the layout with no body placeholder |

Resolution order is `spec.pages[].layout` → `role_map` → heuristic on placeholder types →
fallback, and the choice is recorded as `role_map_source` so a reviewer can see whether the role was
matched by name or guessed.

**A page's `layout` is a deliberate choice, not a default.** Choosing `content` for every page is how
a deck ends up looking like a paper section rather than a talk: a figure page that uses the content
layout pushes its caption into a bullet, and a statement page that uses the content layout buries the
sentence among bullets.

### Placeholders, and why a text box fails gate C

Gate C fails when body text is written into a hand-made text box instead of the layout's inherited
placeholder. The reason is not aesthetic: a placeholder inherits position, font and theme from the
master, so the deck stays editable and stays inside the template. A text box is a private layout that
silently drifts from the template and cannot be restyled by swapping the master.

Practical consequences for the author:

* Put `claim` into the title placeholder, `bullets` into the content/body placeholder, and a figure's
  `caption` into the caption placeholder.
* Leave date/footer/slide-number placeholders alone; the template owns them.
* If a layout has no placeholder for something you need, choose a different layout — the fix is not
  to draw a box.

---

## 2. Slide size, aspect and theme

`meta.aspect` must match `slide_size.aspect` in the index. A 16:9 spec built on a 16:9 template is
the normal case; a mismatch is a template-fitting problem, not a ratio you can declare away.

`theme.colors` / `theme.fonts` come from `masters[]`. `theme.source: "template"` means "inherit";
`"override"` means you deliberately leave the theme and must justify it in `change_records`. The
default and the correct answer in almost every case is to inherit: the content changes between talks,
the template does not.

---

## 3. Rendering and export

```bash
python3 scripts/pt.py render --out /app/output --dpi 144
```

The `pt.py` pass-through takes the deck from `<out>/deck.pptx`; the module-level form frozen in
`CONTRACT.md` §4 (`python3 scripts/render.py render --pptx P --out O [--dpi 144]`) takes the deck as a
flag and is the one to use when the deck lives somewhere else.

Writes:

| File | Contents |
|---|---|
| `deck.pdf` | a real conversion of the built `.pptx` |
| `preview/page-0001.png …` | one raster per slide, at the requested dpi |
| `render_manifest.json` | the render **evidence**: page count, dpi actually produced, per-file sha256, missing fonts |
| `export_report.json` | the export chain: which converter/renderer was used and what it reported |

### Tool discovery (`CONTRACT.md` §6)

Renderer/converter, tried in order, all failing before "unavailable":

1. an `office-render-tool` / `office-renderer` executable on `PATH`;
2. `DSH_OFFICE_NODE` + `DSH_OFFICE_CLI` in the environment;
3. `libreoffice-kit/lib/cli.js` together with a `node` executable.

PDF text: `pdfplumber` → `pypdf` → a `pdftotext` / `pdf-text-tool` CLI.
PDF→PNG: the renderer's `render --pages` path, or `pdftoppm`.

**Any failure is reported, never raised.** `render_deck` returns
`{"ok": false, "reason": "renderer-unavailable"}`; gate F then reports a **warn**, which makes
`result.json.status` `partial`. The editable `.pptx` is still a deliverable, and the honest partial
is worth more than a fabricated preview.

Nothing in the delivered scripts hard-codes a machine path — the discovery order above is the
contract.

---

## 4. Gate C and gate F, precisely

| Gate | Fails when |
|---|---|
| **C** template compliance | a page `layout` is not in `template-index.json`; `deck.pptx` slide count ≠ spec page count; body text sits in a hand-made text box rather than the inherited placeholder; a font/colour is outside the template theme and `theme.source` is not `override` |
| **F** render and export | `deck.pdf` page count ≠ `.pptx` slide count; a preview page is missing; `render_manifest.json` disagrees with the files on disk (sha256/dpi/page count); renderer unavailable → **warn** + `status: partial` |

Two habits that keep both green:

* **Rebuild, never patch.** Editing `deck.pptx` or a PNG by hand breaks the sha256 chain that gate F
  checks and the determinism that gate G checks. Change `spec.json` and rebuild.
* **Freeze the dpi.** The preview dpi in `render_manifest.json` must match what was produced; a
  "close enough" dpi is a manifest mismatch.

---

## 5. Known schema gaps in template/render handling

Recorded because a reviewer should not have to rediscover them, and because they must be pinned down
before a case depends on them:

1. **Caption adaptation marker.** Gate E's fail condition mentions a caption that differs from the
   material caption "without being marked adapted", but `CONTRACT.md` §3.4 does not freeze an
   `adapted` field on `pages[].figure`. Until it does, the safe route is to reuse the source caption
   **verbatim** — which is what the bundled example does.
2. **Figure path form, resolved in §3.1 but not in §3.4.** §3.1 now records figures with an
   **absolute** `media_path` (what the renderer opens) plus a human-readable `media_rel`, and gate A's
   figure exception checks `sha256(media_path) == media_sha256`. §3.4's own DeckSpec snippet still
   shows the older relative form (`"media_path": "materials/fig1.png"`), so the two examples in
   `CONTRACT.md` disagree. This bundle follows §3.1 (absolute, and it is what gate A/E actually
   check); the §3.4 snippet should be corrected. Reported to the Lead.
3. **`render.py`'s CLI exit code.** §4 freezes the flags (`render --pptx P --out O [--dpi 144]`) but
   not the exit code for the renderer-unavailable case, which §6 defines as a returned dict rather
   than an exception. Treat the manifest as the authority, not the exit code.
4. **`pt.py render` vs §4.** The shipped `pt.py render` accepts only `--out`/`--dpi` and derives the
   deck from `<out>/deck.pptx`, while §4's `render.py` signature takes `--pptx`. Both forms are
   documented here so neither reading is hidden. Reported to the Lead.

---

## 6. The fixture template, concretely

`scripts/make_fixture.py` generates a working template from python-pptx's built-in default and then
rewrites it so that the deliverable has a **deck-specific master and layout vocabulary**:

* the slide master is renamed `Deck Master` and the theme `Deck Theme`;
* all 11 default layouts are renamed: `Deck Title`, `Deck Content`, `Deck Section`,
  `Deck Two Column`, `Deck Comparison`, `Deck Statement`, `Deck Closing`, `Deck Figure`,
  `Deck Picture`, `Deck Vertical`, `Deck Vertical Title`;
* the theme's `accent1`/`accent2`/`dk2` slots are set to `#1F3864`, `#C00000`, `#595959` so the
  namespace and identity are treated the same way; the mapping to `theme.colors`
  (`accent1`→primary, `accent2`→accent, `dk2`→muted) is the one the fixture's example spec uses;
* the package is re-zipped with a fixed 1980-01-01 stamp and sorted entry names, so the template is
  **byte-identical across runs** (measured: `d4d7e8e5c9fd6c674c0e222a6163729f000264891dfd43cc2cc118f8d576a6b2`).
  Without that step the template changes every run, because python-pptx stamps entries with the wall
  clock.

The generated template has **no slides** by default (a template, not a starter deck). Pass
`--with-starter-slide` to add one deliberately, to test that a build drops inherited slides instead of
appending to them.

```bash
python3 scripts/make_fixture.py --out /tmp/deck-fixture
```

The quiz for a new template — run it once and look at the output:

* does `slide_layouts` expose a layout for each role you need?
* does the theme colour that the organizer's brand actually uses appear in `theme_colors`?
* is `slide_size.aspect` what the case says?

If any answer is no, that is a fact to record in `uncertainty`, not a reason to restyle the deck.
