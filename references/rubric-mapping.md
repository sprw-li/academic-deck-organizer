# Rubric mapping — every scoring point → artefact → proving command

The rubric is `task/task.md` §2. This file maps **each scoring point** to (a) the concrete artefact in
this submission that carries it and (b) the exact command or gate that proves it. A row is only
allowed to claim something a command can re-derive.

Quoted rubric text is from `task/task.md` §2 (`task.md:40-50`). Scoring is performed by the platform
running the 3 fixed cases and by judging the submitted material. **This bundle claims no benchmark
score** and no measured runtime advantage over a base agent — the platform performs that comparison
(`task.md:34`). What this file proves is that the *evidence* for each point exists and is
re-derivable.

Commands assume the Skill root as CWD; `PY=python3`.

---

## 实际效果 — 40 points

| # | Scoring point | 分值 | Artefact that carries it | Proving command / gate |
|---|---|---|---|---|
| 1 | **任务完成度** — "真正交付一份可直接上台的汇报材料：报告目标、听众定位、叙事顺序、逐页重点信息与问答准备是否形成闭环" (`task.md:42`) | 15 | `spec.json.meta` (`audience`/`goal`/`budget`/`story`) + `pages[]` (role, one claim each) + `qa[]`; produced as `deck.pptx`, `deck_plan.json`, `speaker-notes.md`, `qa-prep.md` | `$PY scripts/pt.py run --input /input --work /app/work --out /app/output` → exit `0`; gate **B** proves page count = `budget.slide_count`, timing within ±15 %, non-empty ≤160-char claims and a complete `story.arc`; gate **I** proves ≥3 Q&A items with resolvable slide/evidence ids; carrier check: `$PY -c "import json;s=json.load(open('spec.json'));print(s['meta']['budget'],len(s['pages']),len(s['qa']))"` |
| 2 | **结果正确性** — "页面上的科学内容、数字与图表引用是否与源材料一致，讲解备注是否与页面结论对应，结论强度是否与证据匹配" (`task.md:42`) | 15 | `evidence[]` (locator + verbatim quote + value/unit) + `pages[].evidence_ids` + `pages[].figure` + `pages[].strength`; `evidence-map.json` | gate **A**: every locator resolves (`E901`), every quote is verbatim (`E902`), every number sits inside its own quote (`E903`), every page has evidence. Gate **E**: figure `media_path` exists, the figure id is in that page's `evidence_ids`, the caption is not an unmarked rewrite. Gate **D**: notes are not a copy of the bullets |
| 3 | **结果完整性** — "是否覆盖论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染六个方面" (`task.md:42`) | 5 | `deck.pptx` (template master/layouts) + `deck_plan.json` + `evidence-map.json` + `speaker-notes.md` + `qa-prep.md` + `deck.pdf` + `preview/*.png` + `render_manifest.json` | `$PY scripts/pt.py check --out /app/output --spec /app/work/spec.json --work /app/work` → exit `0` with all ten gates reported; the six aspects map to gates C (template), A/E (figure+provenance), D (notes/logic), F (render); file-presence check: `$PY -c "import os;print(sorted(os.listdir('/app/output')))"` |
| 4 | **结果可核验性** — "从源材料到成稿的取舍、页级映射、导出链路能否被独立复核" (`task.md:42`) | 5 | `result.json` (`artifacts` path/bytes/sha256, `sources`, `evidence`, `page_map`, `audit`, `runtime`), `validation_report.json`, `render_manifest.json`, `export_report.json`, `change_records` | gate **G** (two builds → identical `deck.pptx` sha256) + gate **H** (`result.json` carries every `output_contract` key non-empty and every `artifacts` path exists with matching bytes/sha256) + gate **F** (manifest agrees with the files on disk). Re-running `$PY scripts/pt.py result --input /input --work /app/work --out /app/output` regenerates the same records |

`result.json` fields that make the audit possible:

```json
{"case_id":"…","status":"ok|partial|needs_human|failed",
 "artifacts":{"deck.pptx":{"path":"output/deck.pptx","bytes":…,"sha256":"…"}, "…":{}},
 "sources":[{"file":"materials/paper.pdf","sha256":"…","kind":"pdf","used":true}],
 "evidence":[{"id":"E1","claim":"…","source":{"file":"…","locator":"…#p.4","quote":"…"},
              "verified":true,"match":"verbatim"}],
 "page_map":[{"slide_id":"P6","layout":"Deck Figure","claim":"…","evidence_ids":["E3"],
              "strength":"strong"}],
 "qa":[{"id":"Q3","question":"…","slide_ids":["P7"]}],
 "uncertainty":[], "limitations":[], "change_records":[],
 "audit":{"gates":[{"id":"A","status":"pass","detail":"…"}],"checks_passed":10,
          "checks_failed":0,"checks_warned":0,"unfilled_fields":[],
          "unmapped_contract_keys":[],"determinism":"byte-identical"},
 "runtime":{"seconds":…,"tools":{"python_pptx":"…","renderer":"…"}}}
```

## 使用增益 — 25 points

| # | Scoring point | 分值 | Artefact that carries it | Proving command / gate |
|---|---|---|---|---|
| 5 | **质量增益** — "相同输入、相同听众与时长设定下，加载作品后相比基础 Agent 是否提升汇报内容组织质量" (`task.md:43`) | 15 | the controlled A/B: `scripts/gain_check.py` + [`references/measured-gain.md`](measured-gain.md); the deterministic gate suite doing the interception | `$PY scripts/pt.py gain_check` → **exit `0`, measured A 0/5 vs B 5/5** injected defects intercepted (D1 fabricated number → gate **A**, D2 claim without evidence → gate **A**, D3 page count ≠ budget → gate **B**, D4 notes duplicate the body → gate **D**, D5 caption mismatch → gate **E**), measured 2026-11-23. Every cell records **both** exit codes. The pass rule is `exit 0` iff `B ≥ 3 and B > A`, and the harness reports **counts only** — no score, no magnitude. **Not a model-quality benchmark** |
| 6 | **效率增益** — "是否减少从论文到可讲稿的人工修改量、返工页数与导出修正次数" (`task.md:43`) | 5 | one `spec.json` → all artefacts in one build; `deck_plan.json` as a single intermediate; byte-identical rebuild; `pt.py edit` for revisions | one `$PY scripts/pt.py build --spec /app/work/spec.json --work /app/work --out /app/output` writes deck + plan + notes + evidence map + Q&A together, so there is no cross-software round-trip; gate **G** measures a byte-identical rebuild, which is what removes "which version did I send?" rework; revisions go through `$PY scripts/pt.py edit …` + rebuild instead of re-authoring. **No runtime speedup is claimed** — no stopwatch measurement was taken |
| 7 | **增益可验证性** — "是否通过公平对照证明质量或效率改善" (`task.md:43`) | 5 | the fair-comparison protocol itself: identical fixture for both conditions, identical injected spec per defect cell, both exit codes recorded; `contract_matrix.py` (adversarial `output_contract` key sets); `selftest.py` (injected-defect negatives) | `$PY scripts/pt.py gain_check` → **exit `0`** (measured `A=0 B=5`); `$PY scripts/pt.py contract_matrix` → exit `0` (every declared key non-empty, `case_id` preserved); `$PY scripts/pt.py selftest` → **exit `0`, 24/24 checks** (incl. injected-defect negatives). All three are case-independent and can be re-run against another case |

## 创新增益 — 15 points

| # | Scoring point | 分值 | Artefact that carries it | Proving command / gate |
|---|---|---|---|---|
| 8 | **新增能力** — "是否形成基础 Agent 或现有方案不具备、或难以稳定实现的听众适配、页级规划与问答预演能力" (`task.md:44`) | 5 | DeckSpec v1 as an explicit **page-level** contract: `role` enum (10 values incl. `hook`/`gap`/`limitation`), one `claim` per page, per-page `strength`, `notes.checkpoint`, and a first-class `qa[]` with slide/evidence binding; audience object carrying `concerns` and `expected_challenges` | `references/spec-schema.md` declares the vocabulary; the code enforces it (`role` outside the enum and `strength` outside the enum are validation errors — see gate B's claim/arc rules); the Q&A binding is enforced by gate **I** (≥3 items, resolvable `slide_ids`/`evidence_ids`, every `high` risk answered). Carrier check: `$PY -c "import json;s=json.load(open('spec.json'));print(sorted({p['role'] for p in s['pages']}),len(s['qa']))"` |
| 9 | **方法改进** — "叙事模板、内容取舍规则、图表引用绑定、模板约束与导出处理等方法是否带来实质改进" (`task.md:44`) | 5 | the split of concerns: the LLM authors `spec.json`, the scripts own geometry/layout/notes/export/checks; the template-index → `role_map` resolution; the renderer-discovery cascade that degrades instead of crashing | `$PY scripts/pt.py selftest` → exit `0`, including injected-defect negatives (a defect that does not trip a gate is itself a failure) and a determinism check; gates C/D/F *measure* the deck (slide count, placeholder use, notes similarity, page counts, dpi/manifest agreement) instead of asserting it; every gate `detail` carries a measured number — see [`failure-modes.md`](failure-modes.md) §1 |
| 10 | **创新证据** — "是否证明创新点相对已有方案的边界和价值" (`task.md:44`) | 5 | [`references/measured-gain.md`](measured-gain.md) (the A/B **and its explicit non-claims**), [`references/failure-modes.md`](failure-modes.md) §4 (checks that do not exist), [`references/template-and-render.md`](template-and-render.md) §5 and [`references/provenance-and-evidence.md`](provenance-and-evidence.md) §2 (grammar gaps stated rather than papered over), [`references/runbook.md`](runbook.md) §8 (documented-not-measured commands labelled) | the boundary is falsifiable, not rhetorical: the gain harness **can fail** (exit non-zero when interception < 3) and reports an un-intercepted defect as un-intercepted; `contract_matrix` can fail on an unknown key; `selftest` includes negatives. The declared gaps (no scientific-quality gate, no completeness gate, no claim↔quote semantic matching, `#r.<n>` numbering ambiguity, unfrozen `result`/`edit` flags) are the honest edge of the claim |

## 可靠体验 — 10 points

| # | Scoring point | 分值 | Artefact that carries it | Proving command / gate |
|---|---|---|---|---|
| 11 | **调用与易用性** — "Agent 是否能在合适时机触发，用户能否按说明完成一次从源材料到导出文件的完整生成" (`task.md:45`) | 3 | `SKILL.md` (frontmatter `name` + `description` with explicit triggers **and** non-triggers; procedure in 8 steps; per-gate failure table), `README.md` (§3 quickstart, §4 CLI reference, exit codes), `references/runbook.md` | structural gate: `$PY _shared/tools/validate_skill_submission.py <submission-dir>` → exit `0` (asserts the frontmatter, the kebab-case name, the runtime tokens `/input/case.json`, `/app/output`, `result.json`, `case_id`, `output_contract`, a `README.md` and a marked example); the documented CLI is the frozen surface of `CONTRACT.md` §4, and every command in the docs was cross-checked against it (see "Command-surface audit") |
| 12 | **运行稳定性** — "在不同汇报类型、不同源材料长度与重复运行下能否持续产出可用页面" (`task.md:45`) | 4 | `render_manifest.json` + `export_report.json` (`determinism`) and gate **G**'s byte-identical rebuild; the deterministic fixture template (fixed zip stamp); the tolerant renderer cascade | gate **G** (`build` twice → identical `deck.pptx` sha256) + gate **F** (manifest matches disk) + `$PY scripts/pt.py selftest` (full-chain determinism). The fixture template is byte-stable across runs — measured: `d4d7e8e5c9fd6c674c0e222a6163729f000264891dfd43cc2cc118f8d576a6b2` from two independent `make_fixture.py` runs |
| 13 | **异常处理** — "对论文缺图、图表分辨率不足、公式或符号丢失、模板与内容冲突、字体缺失、导出失败、超时等情况的识别与处置能力" (`task.md:45`) | 3 | [`references/failure-modes.md`](failure-modes.md) (30 classes: exit code, gate, `result.json` status, correct handling) + the `uncertainty`/`limitations` discipline + gate **J** | reproduce each class and read the recorded handling: illegal spec → `$PY scripts/pt.py build --spec /app/work/bad-spec.json --work /app/work --out /app/output` → exit `4`; missing artefacts → `$PY scripts/pt.py check --out /app/output --spec /app/work/spec.json --work /app/work` → exit `3`; renderer unavailable → `render_deck` returns `renderer-unavailable` without raising and gate F warns (status `partial`), per `CONTRACT.md` §6; a `parsed: false` material must appear in `uncertainty` or gate **J** fails |

## 生态贡献 — 10 points

| # | Scoring point | 分值 | Artefact that carries it | Proving command / gate |
|---|---|---|---|---|
| 14 | **文档完整性** — "使用说明、依赖、模板、导出设置、示例、适用范围与维护信息是否齐全" (`task.md:46`) | 3 | `README.md` (§1 dependencies, §2 install, §3 quickstart, §4 CLI + exit codes, §5 inputs/outputs, §6 template and export parameters, §8 reuse, §9 budget, §10 maintenance, §11 licence) + seven `references/*.md` + `examples/README.md` | `$PY _shared/tools/validate_skill_submission.py <dir>` → exit `0`; completeness is checkable by listing: `$PY -c "import os;print(sorted(os.listdir('references')))"` → the seven referenced files; every machine-checkable claim in `README.md` names the command that produced it |
| 15 | **资源可复用性** — "题集、页级金标准、叙事模板、模板文件、导出脚本等资源能否被他人复用" (`task.md:46`) | 4 | `examples/synthetic-001/expected/spec.json` (a complete authored DeckSpec: 9 pages, 18 evidence items, 4 Q&A) as a copy-and-rewrite template; `examples/synthetic-001/input/**` as a case skeleton; `scripts/make_fixture.py` as a template **and** fixture generator; the case-independent gate suite as a regression check | the fixture regenerates with `$PY scripts/make_fixture.py --out D` and yields a real template (11 custom layouts, `Deck Master`) plus a real 3-page PDF; the example is validated by the real pipeline — **`pt.py run`/`check` → exit `0`, gates A–J 10/10 pass, `status: "ok"`** on 2026-11-23; reuse procedure in `README.md` §8 and `examples/README.md`. The gate suite is pointed at any new case unchanged |
| 16 | **分享推广价值** — "是否沉淀可分享的汇报案例、页级组织方法、问答准备经验与真实应用价值" (`task.md:46`) | 3 | `examples/` (worked synthetic case: input + expected + prose), [`references/measured-gain.md`](measured-gain.md) (a **reusable A/B harness**), [`references/failure-modes.md`](failure-modes.md) §4 (transferable boundary lessons), [`references/runbook.md`](runbook.md) §3 (budget plan), `GITHUB_URL.txt` at the submission root | the example is complete and reproducible from one generator + one command (`examples/README.md`); the gain harness and contract matrix are case-independent; the synthetic labelling is checkable — `$PY -c "import glob,os;fs=[f for f in glob.glob('examples/**/*',recursive=True) if os.path.isfile(f)];print(sorted(f for f in fs if 'SYNTHETIC' not in open(f,encoding='utf-8',errors='replace').read().upper()))"` → the **only** entry is the bare data table `examples/synthetic-001/input/materials/results.csv` (CSV has no comment syntax, so it is labelled by its neighbours); the case brief, the task file, the paper excerpt, the figure notes, and all three files in `expected/` carry the banner in-file |

## GitHub 公开作品链接 — +5 points

| # | Scoring point | 分值 | Artefact | Proving command |
|---|---|---|---|---|
| 17 | "提交本题作品的 GitHub 公开链接。评审无需登录即可打开链接并找到本次提交的作品" (`task.md:50`) | +5 | `GITHUB_URL.txt` at the submission root; first line = public URL, optional second line = relative path of this work | `$PY -c "print(open('GITHUB_URL.txt',encoding='utf-8').read().splitlines()[:2])"`; then open it in a **logged-out** browser window — `task.md:77` requires exactly that. **Owner: Lead** — this file is not in the docs write scope; listed here because it is a scoring point |

---

## Bundle budget

The platform allows **≤ 50 UTF-8 text files, ≤ 500 KB each, ≤ 2 MB total, no symlinks**
(`task/task.md:88`). This bundle contains only UTF-8 text: the generated binaries (template `.pptx`,
`paper.pdf`, `fig1.png`) are produced on the spot by `scripts/make_fixture.py` and are never shipped.

Measured with (excluding `__pycache__`):

```
$PY -c "import os;root='.';fs=[os.path.join(d,f) for d,_,g in os.walk(root) for f in g if '__pycache__' not in d];\
print('files',len(fs),'total_bytes',sum(os.path.getsize(f) for f in fs),'largest',max((os.path.getsize(f),f) for f in fs))"
```

| Metric | Limit | Measured for the docs/examples write scope (2026-11-23 ~12:33 +08:00) |
|---|---|---|
| text files | ≤ 50 | **19** |
| largest file | ≤ 500 KB | **24,631 B** (`references/rubric-mapping.md`) |
| total bytes | ≤ 2 MB | **≈ 220 KB** (219,923 B at measurement; both files containing this table are inside it, so a wording edit shifts it by tens of bytes) |
| symlinks | 0 | 0 (none created anywhere) |
| binaries | — | 0 in the shipped tree; 3 generated by `make_fixture.py` into a scratch directory |
| `__pycache__` / `.pyc` | 0 | 0 |
| decoding as UTF-8 | 100 % | 19/19 files decode as UTF-8 |

Structural gate: `$PY _shared/tools/validate_skill_submission.py <submission-dir>` →
**exit `0`** (2026-11-23 ~12:35, whole submission tree):
`{"status":"ok","files":33,"bytes":603322,"name":"academic-deck-organizer","errors":[]}`. It enforces
the four limits above plus the frontmatter and marked-example rules.

> The `scripts/` directory is owned by the other teammates in `CONTRACT.md` §2 and its byte total
> changes during integration, which is why the numbers above cover only this write scope and are
> re-measured at packaging time. The **file count** and the gate's `status: ok` are the stable facts.

---

## Evidence of execution

Only commands actually run are listed with their exit codes. Anything else in this document is a
**description of what the command checks**, not a claim that it was run on a finished pipeline.

| Command (Skill root as CWD) | Exit | Note |
|---|---|---|
| `$PY scripts/make_fixture.py --out work/verify/docs-examples/fixture` | `0` | writes `case.json`, `task.md`, 3 text materials, `slides-template.pptx` (11 custom layouts, master `Deck Master`, theme `Deck Theme`), 3-page `paper.pdf`, 800×600 `fig1.png`, `FIXTURE-MANIFEST.json` |
| `$PY scripts/make_fixture.py --out <second dir>` + sha256 comparison | `0` | `slides-template.pptx` **byte-identical** across runs (`d4d7e8e5…`); `paper.pdf`/`fig1.png` are not byte-stable (matplotlib timestamp) |
| `$PY work/verify/docs-examples/derive_examples.py --fixture … --examples …` (author-side tool, not shipped) | `0` | regenerates `examples/synthetic-001/**` from the fixture and asserts the §3.4 rules **plus** gate-A-style number traceability and the absolute-`media_path` rule → **9 pages, 18 evidence items, 4 Q&A, timing 690 s (4.17 % deviation), 0 problems** |
| `$PY work/verify/docs-examples/measure_example.py --submission … --scratch …` (author-side tool, not shipped) | `0` | **the real run behind `examples/expected/`**: `make_fixture → ingest → inspect → pt.py run → check → result` all exit `0`; **gates A–J 10/10 pass**, `status: "ok"`, 18 artefacts, `deck.pptx` 84,165 B / sha256 `8ed411b6818e58df…`, `runtime.seconds` 32.34, `audit.determinism: byte-identical`; author-machine paths replaced with placeholders |
| `$PY scripts/pt.py gain_check` and `$PY scripts/gain_check.py` | **`0`** | measured A **0/5** vs B **5/5** intercepted (D1→A, D2→A, D3→B, D4→D, D5→E); report in `$DECK_GAIN_WORK`/tempdir `gain-ab.json` |
| `$PY work/verify/docs-examples/audit_commands.py --submission <dir>` (author-side tool, not shipped) | `0` | every documented command form checked against `CONTRACT.md` §4 **and** the shipped `pt.py --help` signatures: **0 problems** |
| `$PY -c "…"` quote/locator check over `examples/…/spec.json` | `0` | every `source.quote` equals the exact text of its cited span; every numeric `value` occurs inside its own quote; every on-page number is traceable to a cited quote |
| `$PY _shared/tools/validate_skill_submission.py <submission-dir>` | **`0`** | `{"status":"ok","files":…,"name":"academic-deck-organizer","errors":[]}` (whole tree, 2026-11-23 ~13:5x) |
| `$PY scripts/pt.py selftest` / `contract_matrix` | **`0`** | `selftest` **24/24 PASS**; `contract_matrix` **5/5** |

> **The honest summary.** `examples/expected/result.json` and `deck_plan.json` are the output of the
> real run above, not hand-written illustrations: they carry `synthetic_example: true`, a
> `measurement` block naming the command sequence, real artefact bytes/sha256 and the real ten gate
> verdicts. The only substitution in that run is disclosed in
> `result.json.measurement.spec_note`: `figure.media_path` was rewritten from the container path
> `/input/materials/fig1.png` to the local fixture file (same bytes, same sha256) because `/input`
> exists only inside the runtime container. Author-machine absolute paths were replaced with
> `<placeholders>`; no measured value was altered.

---

## Command-surface audit

Every command line that appears in this bundle was checked mechanically (79 command forms) against
`CONTRACT.md` §4 **and** against the shipped argparse surface (`python3 scripts/pt.py <cmd> --help`,
run 2026-11-23). Where §4 does not freeze a flag, the doc says so instead of inventing one. Result:
**0 problems**.

| Documented form | Frozen where | Note |
|---|---|---|
| `pt.py run --input /input --work /app/work --out /app/output` | §4 (explicit) | the one-shot chain; returns the `check` exit code. Shipped CLI also accepts optional `--respec` (not documented) |
| `pt.py ingest --input /input --work /app/work` | §4 (`ingest.py` cli comment) | exit `0`/`2` |
| `pt.py inspect --input /input --work /app/work [--template T] [--out J]` | §4 (explicit) | writes `work/template-index.json` |
| `pt.py plan --work W --input /input --out spec.json [--template-index T]` | §4 (explicit) | `--template-index` optional; the index is discovered from `--work` when it is omitted |
| `pt.py build --spec S --work W --out O` | §4 (`build.py` cli comment) | exit `0`/`2`/`4` |
| `pt.py check --out O --spec S --work W` | §4 (`validate.py` cli comment) | exit `0`/`1`/`3`/`2` |
| `pt.py render --out O [--dpi 144]` | §4 (explicit) | the pass-through takes the deck from `<out>/deck.pptx`; renderer-unavailable is a returned dict, not an exit code |
| `pt.py result --out O --input /input --work W [--status S]` | **not frozen** in §4 | matches the shipped CLI; the docs say so |
| `pt.py edit --spec S --edits E --out S2` | **not frozen** in §4 | matches the shipped CLI; the docs say so |
| `pt.py selftest` / `contract_matrix` / `gain_check` | §4 (subcommand list) | **no flags**; `gain_check` now runs with no arguments at all |
| `scripts/template.py inspect --template X [--out Y]` | §4 (`template.py` cli comment) | the §4-frozen module form of `pt.py inspect` |
| `scripts/deckplan.py plan --work W --input DIR [--template-index T] --out SPEC` | §4 (`deckplan.py` cli comment) | the module form, used when an index must be pinned |
| `scripts/render.py render --pptx P --out O [--dpi 144]` | §4 (`render.py` cli comment) | the module form, used when the deck is not at `<out>/deck.pptx` |
| `scripts/ingest.py ingest` / `scripts/build.py build` / `scripts/validate.py check` | §4 (cli comments) | module-level equivalents, listed in `README.md` §4 |
| `scripts/evidence.py` / `scripts/measure.py` argv | **not frozen** | §4 defines the callables but leaves `cli(argv)` without a shape; **no** command line for either is documented anywhere in this bundle |
| `make_fixture.py --out D [--with-starter-slide] [--quiet]` | this bundle (`CONTRACT.md` §2 writes it to docs-examples) | an author/verification tool, not part of the runtime chain |

The auditor is `work/verify/docs-examples/audit_commands.py` (author-side, not shipped); it also
rejects an `evidence.py`/`measure.py` command line, an `pt.py` subcommand invoked with an extra flag,
and a required flag that is missing.

Known gaps in `CONTRACT.md` itself, re-checked item by item on 2026-11-23 after the R2 contract
updates. Only what is still true is listed:

1. §4 leaves the flags of `pt.py result` and `pt.py edit` unspecified, although §3.6 and §7 depend on
   `result`. (The shipped CLI accepts `result --out --input --work [--status]` and
   `edit --spec --edits --out`; the docs list them and mark them unfrozen.)
2. §7.1's definition-of-done path `examples/synthetic-001/input` cannot be run byte-for-byte from the
   shipped bundle, because §0 forbids shipping the template `.pptx` and the PDF that the run needs;
   the reproducible command is `make_fixture.py` into a scratch directory first (two-command smoke
   run in `SKILL.md` §8 and [`runbook.md`](runbook.md)).
3. §4's `pt.py render` pass-through takes only `--out`/`--dpi` while §4's own `render.py` module
   signature takes `--pptx`. Both forms are documented; this is now stated in §4 itself, so it is a
   documented split rather than a contradiction.

**Resolved in R2 (were listed as gaps in the previous revision, now removed):**

* `pt.py inspect` exists in §4 **and** in the shipped CLI (`--input --work [--template] [--out]`).
* `pt.py plan` accepts `--template-index` in both §4 and the shipped CLI.
* `#r.<n>` counts **data rows** (the CSV header is not record 1); JSON materials accept `#r.<n>` or
  `#L<a>-L<b>` — both are registered by `ingest`.
* Gate E's caption handling has a real field: `figure.caption_adapted` (bool), and a prefix-truncated
  caption counts as `一致(截断)`.
* §3.4's DeckSpec snippet now shows the **absolute** `media_path` that §3.1 requires, so the two
  sections agree.
* `pt.py selftest` is 24 checks (24/24 PASS, exit `0`), not the ≥18 floor; `gain_check` runs with no
  arguments.
