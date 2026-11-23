#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gain_check.py — DoD §7.4 / 题目「使用增益可验证性」的**受控 A/B 拦截计数**。

不是分数、不是"提升幅度"：本脚本只回答一个问题 ——
**同一批注入缺陷，朴素搬运策略的产物被判出几个 vs 本 Skill 的 validate 闸门判出几个。**

A 侧（baseline）：朴素"摘要 + Results 段落逐页倒"策略产物 —— 不管页数与预算、不给页 claim 绑证据、
备注直接复制正文、图注随手改写、数字随手四舍五入；A 自带的"校验"只有"文件在不在、能不能打开"。
B 侧（本 Skill）：同一批产物过 ``validate.run_gates`` 的 A–J 闸门。

三种测量（都写进 ``work/gain-ab.json``）：
  1. ``conditions``：干净夹具 + 单缺陷注入，缺陷类 → 目标闸门；A/B 各自是否拦下（逐条件）。
  2. ``baseline_output``：A 策略**自己生成的产物**上，B 闸门判出哪几类缺陷（证明缺陷是真实可触发的）。
  3. ``totals``：``a_total`` / ``b_total`` 与退出判据。

退出码：0 当且仅当 ``b_total >= 3 且 b_total > a_total``；2 用法/IO；1 判据不满足。
``--work`` 可选，缺省优先级 ``--work`` > ``DECK_GAIN_WORK`` > ``<tempdir>/deck-gain-check`` >
``<cwd>/_gain_work``——**任何情况下都不落进脚本所在交付树**（避免把 pptx/pdf/png 混进提交包）。
``--scratch`` / ``--out`` 默认跟随该 work（``<work>/gain-ab-run`` 与 ``<work>/gain-ab.json``）。

依赖：python-pptx（真造 pptx）、Pillow（真造 PNG）。缺任一 → exit 2 并说明（不静默 pass）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import validate as V                                                            # noqa: E402

SCHEMA_VERSION = 1

# 注入缺陷类 → 期望被哪个闸门拦下（CONTRACT §5）
DEFECTS = [
    {"id": "D1", "class": "fabricated-number", "gate": "A",
     "desc": "内容页 bullets 出现材料里不存在的数字 3.7（伪造数字）",
     "inject": "bullet_fabricated_number"},
    {"id": "D2", "class": "page-claim-without-evidence", "gate": "A",
     "desc": "内容页 evidence_ids 清空（页 claim 无证据绑定）",
     "inject": "clear_evidence_ids"},
    {"id": "D3", "class": "page-count-budget-mismatch", "gate": "B",
     "desc": "budget.slide_count=8 但实际 6 页（页数 ≠ 预算）",
     "inject": "budget_mismatch"},
    {"id": "D4", "class": "notes-duplicate-body", "gate": "D",
     "desc": "备注 = 正文 bullets 拼接（相似度 ≥0.8）",
     "inject": "notes_duplicate"},
    {"id": "D5", "class": "caption-not-matching-source", "gate": "E",
     "desc": "图注与材料 caption 不符且未标 adapted",
     "inject": "caption_mismatch"},
]

PAPER_MD = """# Delta-phase assay

Delta-phase fraction increased in treated samples.

We measured a 2.8-fold higher delta-phase fraction in the treated group.

All measurements were repeated on independent days.

The cohort comprised 41 samples with complete records.

We cannot exclude a contribution from sample preparation.
"""
FIG_CAPTION = ("Figure 1. Delta-phase fraction from X-ray diffraction patterns "
               "of treated and control samples across three independent runs.")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jdump(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def jload(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 真造最小 PDF / PNG
# --------------------------------------------------------------------------- #
def write_min_pdf(path: Path, pages_text) -> None:
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    objs = {3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids, nid = [], 4
    for text in pages_text:
        pid, cid = nid, nid + 1
        nid += 2
        kids.append(pid)
        body = "BT /F1 12 Tf 72 700 Td 16 TL\n" + " ".join(
            "(%s) Tj T*" % esc(ln) for ln in text.split("\n") if ln.strip()) + "\nET"
        sb = body.encode("latin-1", "replace")
        objs[cid] = b"<< /Length %d >>\nstream\n" % len(sb) + sb + b"\nendstream"
        objs[pid] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % cid).encode()
    objs[2] = ("<< /Type /Pages /Count %d /Kids [%s] >>"
               % (len(kids), " ".join("%d 0 R" % k for k in kids))).encode()
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += b"%d 0 obj\n" % oid + objs[oid] + b"\nendobj\n"
    xref, mx = len(out), max(objs)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (mx + 1)
    for oid in range(1, mx + 1):
        out += b"%010d 00000 n \n" % offsets.get(oid, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (mx + 1, xref)
    path.write_bytes(bytes(out))


def make_png(path: Path, w: int, h: int, dpi: int) -> None:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([int(w * .06), int(h * .1), int(w * .94), int(h * .9)], outline="black", width=3)
    d.line([int(w * .1), int(h * .7), int(w * .5), int(h * .45), int(w * .9), int(h * .2)],
           fill="black", width=5)
    im.save(str(path), dpi=(dpi, dpi))


# --------------------------------------------------------------------------- #
# 真读模板（layout / theme）
# --------------------------------------------------------------------------- #
def inspect_template(p: Path) -> dict:
    from lxml import etree
    from pptx import Presentation
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    prs = Presentation(str(p))
    with zipfile.ZipFile(p) as z:
        theme = etree.fromstring(z.read("ppt/theme/theme1.xml"))
    major = theme.find(".//a:fontScheme/a:majorFont/a:latin", ns)
    minor = theme.find(".//a:fontScheme/a:minorFont/a:latin", ns)
    colors = {}
    for c in theme.findall(".//a:clrScheme/*", ns):
        tag = etree.QName(c).localname
        srgb, sysclr = c.find("a:srgbClr", ns), c.find("a:sysClr", ns)
        if srgb is not None:
            colors[tag] = "#" + srgb.get("val", "").upper()
        elif sysclr is not None:
            colors[tag] = "#" + str(sysclr.get("lastClr", "")).upper()
    layouts = []
    for i, l in enumerate(prs.slide_layouts):
        phs = []
        for ph in l.placeholders:
            try:
                ptype = str(ph.placeholder_format.type)
            except Exception:                                               # noqa: BLE001
                ptype = "?"
            phs.append({"idx": int(ph.placeholder_format.idx), "type": ptype, "name": ph.name,
                        "geom": {"left": int(ph.left or 0), "top": int(ph.top or 0),
                                 "width": int(ph.width or 0), "height": int(ph.height or 0)}})
        layouts.append({"index": i, "master": 0, "name": l.name, "placeholders": phs})
    try:
        mname = prs.slide_masters[0].name
    except Exception:                                                       # noqa: BLE001
        mname = "Office Theme"
    w_emu, h_emu = int(prs.slide_width), int(prs.slide_height)
    return {
        "path": "materials/slides-template.pptx", "sha256": sha256_file(p),
        "slide_size": {"w_emu": w_emu, "h_emu": h_emu, "w_in": round(w_emu / 914400.0, 3),
                       "h_in": round(h_emu / 914400.0, 3), "aspect": "16:9"},
        "masters": [{"index": 0, "name": mname,
                     "theme_fonts": {"major": major.get("typeface") if major is not None else "",
                                     "minor": minor.get("typeface") if minor is not None else ""},
                     "theme_colors": colors}],
        "layouts": layouts, "existing_slides": [],
        "role_map": {"title": "Title Slide", "section": "Section Header",
                     "content": "Title and Content", "two_col": "Two Content",
                     "picture": "Picture with Caption", "closing": "Blank"},
        "role_map_source": "layout-names", "warnings": [],
    }


# --------------------------------------------------------------------------- #
# 夹具构造
# --------------------------------------------------------------------------- #
def normalize_zip(path: Path) -> None:
    """固定 zip 成员时间戳为 1980-01-01（对齐交付树的 ``normalize.normalize_pptx``）。

    **不加这一步，本脚本的确定性夹具就是 flaky 的**：python-pptx 的 ``save()`` 把当前本地时间写进
    每个 zip 成员头，同一内容相隔 1 秒以上两次 save 的 sha256 就不同（实测见 task-3 的
    evidence/09）。真实流水线已调用 ``normalize_pptx``，这里必须对齐，否则 gate G 会随秒边界随机失败。
    """
    import zipfile
    tmp = path.with_suffix(path.suffix + ".norm")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in sorted(zin.infolist(), key=lambda i: i.filename):
            data = zin.read(info.filename)
            ni = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            ni.compress_type = info.compress_type
            ni.external_attr = info.external_attr
            zout.writestr(ni, data)
    tmp.replace(path)


def build_deck(template: Path, spec: dict, media: Path, out: Path, *,
               notes_override=None) -> Path:
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation(str(template))
    by_name = {l.name: l for l in prs.slide_layouts}
    for page in spec["pages"]:
        slide = prs.slides.add_slide(by_name[page["layout"]])
        try:
            if slide.shapes.title is not None:
                slide.shapes.title.text = page.get("claim") or page.get("id") or ""
        except Exception:                                                   # noqa: BLE001
            pass
        body = None
        for ph in slide.placeholders:
            if int(ph.placeholder_format.idx) != 0 and ph.has_text_frame:
                body = ph
                break
        bullets = list(page.get("bullets") or [])
        if body is not None:
            tf = body.text_frame
            tf.text = bullets[0] if bullets else ""
            for b in bullets[1:]:
                tf.add_paragraph().text = b
        if (page.get("figure") or {}).get("media_path") and media.is_file():
            slide.shapes.add_picture(str(media), Inches(6.8), Inches(2.1), width=Inches(5.6))
        say = (notes_override or {}).get(page["id"]) or (page.get("notes") or {}).get("say") or ""
        slide.notes_slide.notes_text_frame.text = say
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out))
    normalize_zip(out)          # 固定 zip 时间戳（否则两次 build 哈希不同 → gate G flaky）
    return out


def build_case(root: Path, *, naive: bool = False) -> dict:
    """造一套完整产物（clean 走全 pass；naive=A 策略产物，故意违反多条）。"""
    if root.exists():
        shutil.rmtree(root)
    inp, work, out = root / "input", root / "work", root / "output"
    mats = inp / "materials"
    mats.mkdir(parents=True)
    work.mkdir(parents=True)
    out.mkdir(parents=True)

    tpl = mats / "slides-template.pptx"
    from pptx import Presentation
    from pptx.util import Emu
    t = Presentation()
    t.slide_width, t.slide_height = Emu(12192000), Emu(6858000)
    t.save(str(tpl))
    ti = inspect_template(tpl)
    jdump(work / "template-index.json", ti)

    (mats / "paper.md").write_text(PAPER_MD, encoding="utf-8")
    md_text = (mats / "paper.md").read_text(encoding="utf-8")
    fig = mats / "fig1.png"
    make_png(fig, 800, 600, 96)
    fig_sha = sha256_file(fig)
    md_lines = md_text.split("\n")
    spans, pos = [], 0
    for i, ln in enumerate(md_lines, 1):
        spans.append({"locator": "paper.md#L%d-L%d" % (i, i), "kind": "line", "line": i,
                      "chars": [pos, pos + len(ln)], "page": None})
        pos += len(ln) + 1
    materials = {"schema_version": 1, "case_id": "gain-001", "input_dir": str(inp), "files": [
        {"path": "materials/paper.md", "abs": str(mats / "paper.md"), "kind": "markdown",
         "sha256": sha256_file(mats / "paper.md"), "bytes": (mats / "paper.md").stat().st_size,
         "parsed": True, "parse_note": None, "text": md_text, "spans": spans,
         "figures": [], "tables": []},
        {"path": "materials/fig1.png", "abs": str(fig), "kind": "image", "sha256": fig_sha,
         "bytes": fig.stat().st_size, "parsed": True, "parse_note": None, "text": "",
         "spans": [{"locator": "fig1.png#all", "chars": [0, 0], "kind": "all", "page": None},
                   {"locator": "fig1.png#fig.1", "chars": [0, 0], "kind": "figure", "page": 1}],
         "figures": [{"id": "fig1", "kind": "figure", "page": 1, "caption": FIG_CAPTION,
                      "locator": "fig1.png#fig.1", "media_path": str(fig),
                      "media_sha256": fig_sha, "width": 800, "height": 600, "dpi_estimate": 96.0}],
         "tables": []},
    ], "notes": ["gain_check synthetic case"]}
    jdump(work / "materials.json", materials)

    L = {f"L{i}": md_lines[i - 1] for i in (3, 5, 7, 9)}
    ev = [
        {"id": "E1", "kind": "number", "claim": "2.8 倍", "value": 2.8, "unit": "fold",
         "source": {"file": "paper.md", "locator": "paper.md#L5-L5", "quote": L["L5"]}},
        {"id": "E2", "kind": "quote", "claim": "delta 相分数升高",
         "source": {"file": "paper.md", "locator": "paper.md#L3-L3", "quote": L["L3"]}},
        {"id": "E3", "kind": "number", "claim": "41 个样本", "value": 41, "unit": "samples",
         "source": {"file": "paper.md", "locator": "paper.md#L9-L9", "quote": L["L9"]}},
        {"id": "E4", "kind": "quote", "claim": "独立实验日重复",
         "source": {"file": "paper.md", "locator": "paper.md#L7-L7", "quote": L["L7"]}},
        {"id": "EFIG", "kind": "figure", "claim": "XRD 曲线", "media_path": "materials/fig1.png",
         "media_sha256": fig_sha,
         "source": {"file": "fig1.png", "locator": "fig1.png#fig.1", "quote": FIG_CAPTION}},
    ]
    pages = [
        {"id": "P1", "role": "title", "layout": "Title Slide", "claim": "Delta 相分数提高 2.8 倍",
         "evidence_ids": ["E1"], "bullets": ["Delta-phase assay", "Treatment vs control"],
         "notes": {"say": "开场只交代问题与路线图，先让听众知道结论指向哪一类证据。",
                   "timing_s": 30}, "strength": "strong"},
        {"id": "P2", "role": "hook", "layout": "Title and Content", "claim": "治疗组 delta 相分数升高",
         "evidence_ids": ["E2"], "bullets": ["Delta-phase fraction increased in treated samples",
                                              "Independent-day repeats"],
         "notes": {"say": "动机页：若相分数没有变化，后面的方法学讨论就没有落点。",
                   "timing_s": 55}, "strength": "moderate"},
        {"id": "P3", "role": "method", "layout": "Title and Content", "claim": "独立实验日重复测量",
         "evidence_ids": ["E1", "E4"], "bullets": ["We measured 2.8-fold higher fraction",
                                                   "Repeated on independent days"],
         "notes": {"say": "方法学强调两件事：倍数来自相对比较；重复性来自独立实验日。",
                   "timing_s": 60}, "strength": "strong"},
        {"id": "P4", "role": "results", "layout": "Picture with Caption", "claim": "主要结果与趋势一致",
         "evidence_ids": ["EFIG", "E1"],
         "bullets": ["Delta-phase fraction increased 2.8-fold", "Consistent across runs"],
         "figure": {"evidence_id": "EFIG", "media_path": "materials/fig1.png",
                    "placement": "right", "caption": FIG_CAPTION},
         "notes": {"say": "把曲线读给听众：横轴是样品，纵轴是相分数；强调报告的是相对变化。",
                   "timing_s": 70}, "strength": "strong"},
        {"id": "P5", "role": "limitation", "layout": "Title and Content", "claim": "样本量有限，制样影响未排除",
         "evidence_ids": ["E3"], "bullets": ["41 samples with complete records",
                                             "Preparation effect cannot be excluded"],
         "notes": {"say": "主动交代两点不足：单一队列；无法区分相变来源与制样差异。",
                   "timing_s": 60}, "strength": "mixed"},
        {"id": "P6", "role": "conclusion", "layout": "Title and Content",
         "claim": "结论：相分数升高且可重复", "evidence_ids": ["E4"],
         "bullets": ["Independent-day repeats support reproducibility",
                     "Phase-fraction increase is the primary finding"],
         "notes": {"say": "收尾只保留能被证据支撑的两句，并给出下一步验证方向。",
                   "timing_s": 65}, "strength": "moderate"},
    ]
    qa = [
        {"id": "Q1", "question": "倍数如何归一化？", "answer": "以对照组为基准做相对比较，并在独立实验日重复。",
         "evidence_ids": ["E1"], "slide_ids": ["P4"], "risk": "high", "category": "data"},
        {"id": "Q2", "question": "为何不做机制实验？", "answer": "当前数据只能支持表型结论，机制需额外实验。",
         "evidence_ids": ["E3"], "slide_ids": ["P5"], "risk": "medium", "category": "mechanism"},
        {"id": "Q3", "question": "重复性如何保证？", "answer": "全部测定在独立实验日完成三次重复。",
         "evidence_ids": ["E4"], "slide_ids": ["P3"], "risk": "medium", "category": "control"},
        {"id": "Q4", "question": "与对照是否自洽？", "answer": "方向与对照组比较后保持一致。",
         "evidence_ids": ["E1"], "slide_ids": ["P4"], "risk": "low", "category": "other"},
    ]
    spec = {
        "schema_version": 1,
        "meta": {"case_id": "gain-001", "language": "zh", "deck_title": "Delta 相分数提高 2.8 倍",
                 "aspect": "16:9",
                 "template": {"path": "materials/slides-template.pptx", "sha256": ti["sha256"]},
                 "audience": {"id": "committee", "prior_knowledge": "medium",
                              "concerns": ["证据强度"], "expected_challenges": ["机制解释"]},
                 "goal": {"statement": "让评审接受 delta 相分数升高这一结论",
                          "success_checks": ["每页唯一主张", "数字可溯源"]},
                 "budget": {"minutes": 6, "slide_count": 6, "qa_minutes": 3},
                 "story": {"arc": ["hook", "problem", "gap", "method", "results", "limitation",
                                   "conclusion"], "rationale": "动机→方法→结果→局限→结论"}},
        "theme": {"colors": {"primary": ti["masters"][0]["theme_colors"].get("accent1"),
                             "accent": ti["masters"][0]["theme_colors"].get("accent2"),
                             "text": ti["masters"][0]["theme_colors"].get("dk1"),
                             "muted": ti["masters"][0]["theme_colors"].get("accent3")},
                  "fonts": {"heading": ti["masters"][0]["theme_fonts"]["major"],
                            "body": ti["masters"][0]["theme_fonts"]["minor"]},
                  "source": "template"},
        "evidence": ev, "pages": pages, "qa": qa,
        "uncertainty": [], "limitations": ["单一队列，未做机制实验"], "change_records": [],
    }
    if naive:
        # A 策略：把摘要/Results 逐段倒进页、不管页数预算、备注复制正文、图注改写、数字四舍五入
        spec["meta"]["budget"]["slide_count"] = 12            # 与实际页数不符
        spec["pages"] = spec["pages"][:3]                     # 只倒 3 页
        for p in spec["pages"]:
            p["evidence_ids"] = []                            # 不绑证据
            p["bullets"] = [L["L3"], L["L5"], L["L7"]]        # 段落直搬
            p["notes"] = {"say": " ".join(p["bullets"]), "timing_s": 20}
        spec["pages"][0]["bullets"] = ["Delta-phase fraction rose about 3.7-fold in treated samples"]
        spec["pages"][0]["role"] = "results"
        spec["pages"][0]["layout"] = "Picture with Caption"
        spec["pages"][0]["figure"] = {"evidence_id": "EFIG", "media_path": "materials/fig1.png",
                                      "placement": "right",
                                      "caption": "Figure 1. Summary of our delta-phase results."}
        spec["evidence"] = [e for e in ev if e["id"] == "EFIG"]
        spec["qa"] = []
        spec["meta"]["story"]["arc"] = ["results"]

    jdump(work / "spec.json", spec)
    b1 = build_deck(tpl, spec, fig, work / "deck_build1.pptx")
    b2 = build_deck(tpl, spec, fig, work / "deck_build2.pptx")
    jdump(work / "determinism.json", {"schema_version": 1, "build_a_sha256": sha256_file(b1),
                                     "build_b_sha256": sha256_file(b2)})
    deck = out / "deck.pptx"
    shutil.copyfile(b1, deck)

    # ---- 真渲染（不依赖 LibreOffice：真 PDF 头 + 真 PNG 像素 + manifest 真 sha） ----
    dpi, w_in, h_in = 144, int(ti["slide_size"]["w_emu"]) / 914400.0, int(ti["slide_size"]["h_emu"]) / 914400.0
    n = len(spec["pages"])
    write_min_pdf(out / "deck.pdf", ["slide %d" % (i + 1) for i in range(n)])
    prev = out / "preview"
    prev.mkdir(exist_ok=True)
    pw, ph = round(w_in * dpi), round(h_in * dpi)
    sha_map, pages_ent = {}, []
    for i in range(1, n + 1):
        p = prev / ("page-%04d.png" % i)
        make_png(p, pw, ph, dpi)
        sha_map[p.name] = sha256_file(p)
        pages_ent.append({"page": i, "file": "preview\\%s" % p.name, "path": str(p.resolve()),
                          "bytes": p.stat().st_size, "sha256": sha_map[p.name],
                          "width": pw, "height": ph})
    files_map = dict(sha_map)
    files_map["deck.pdf"] = sha256_file(out / "deck.pdf")
    jdump(out / "render_manifest.json", {
        "schema_version": 1, "renderer": "gain-check-minimal-pdf", "ok": True, "dpi": dpi,
        "pageCount": n, "expected_pages": n, "missingFonts": [], "pages": pages_ent,
        "files": files_map, "sha256": sha_map, "preview_dir": "preview", "source": "pptx",
        "slide_size_in": {"w": round(w_in, 4), "h": round(h_in, 4),
                          "w_emu": int(ti["slide_size"]["w_emu"]),
                          "h_emu": int(ti["slide_size"]["h_emu"])},
        "pdf": "deck.pdf", "pdf_sha256": files_map["deck.pdf"],
        "pptx_sha256": sha256_file(deck), "warnings": []})
    jdump(out / "export_report.json", {"schema_version": 1, "ok": True,
                                       "chain": ["pptx->pdf", "pptx->png"], "dpi": dpi,
                                       "png_count": n})
    jdump(out / "deck_plan.json", {"schema_version": 1, "slides": [
        {"slide_id": p["id"], "layout": p["layout"], "claim": p.get("claim"),
         "evidence_ids": p.get("evidence_ids"), "figure": p.get("figure"),
         "notes": p.get("notes")} for p in spec["pages"]]})
    (out / "speaker-notes.md").write_text("\n\n".join(
        "## %s\n%s" % (p["id"], (p.get("notes") or {}).get("say") or "") for p in spec["pages"]),
        encoding="utf-8")
    jdump(out / "evidence-map.json", {"schema_version": 1, "pages": [
        {"slide_id": p["id"], "claim": p.get("claim"), "evidence_ids": p.get("evidence_ids"),
         "locator": [e["source"]["locator"] for e in spec["evidence"]
                     if e["id"] in (p.get("evidence_ids") or [])],
         "quote": [e["source"]["quote"] for e in spec["evidence"]
                   if e["id"] in (p.get("evidence_ids") or [])],
         "strength": p.get("strength")} for p in spec["pages"]]})
    (out / "qa-prep.md").write_text("# Q&A prep\n\n" + "\n\n".join(
        "### %s (%s)\nQ: %s\nA: %s" % (q["id"], q["risk"], q["question"], q["answer"])
        for q in spec["qa"]), encoding="utf-8")
    jdump(inp / "case.json", {
        "case_id": "gain-001", "output_contract": {
            "deck": "可编辑 pptx", "speaker_notes": "逐页讲稿", "evidence_map": "页级证据映射",
            "qa_prep": "问答准备", "render_preview": "渲染清单", "summary": "运行摘要"}})
    jdump(inp / "task.md", "# gain_check synthetic\n")
    write_result(root)
    return {"root": root, "spec": spec}


def write_result(root: Path) -> dict:
    out = root / "output"
    spec = jload(root / "work" / "spec.json")
    names = ["deck.pptx", "deck_plan.json", "speaker-notes.md", "evidence-map.json",
             "qa-prep.md", "deck.pdf", "render_manifest.json"]
    arts = {}
    for nm in names:
        p = out / nm
        if p.is_file():
            arts[nm] = {"path": "output/%s" % nm, "rel": "output/%s" % nm,
                        "bytes": p.stat().st_size, "sha256": sha256_file(p)}
    res = {"case_id": "gain-001", "status": "ok", "artifacts": arts,
           "sources": [{"file": f["path"], "sha256": f["sha256"], "kind": f["kind"], "used": True}
                       for f in (jload(root / "work" / "materials.json")["files"])],
           "evidence": [{"id": e["id"], "claim": e.get("claim"), "source": e["source"],
                         "verified": True, "match": "verbatim"} for e in spec["evidence"]],
           "page_map": [{"slide_id": p["id"], "layout": p["layout"], "claim": p.get("claim"),
                         "evidence_ids": p.get("evidence_ids"),
                         "strength": p.get("strength")} for p in spec["pages"]],
           "qa": [{"id": q["id"], "question": q["question"], "slide_ids": q["slide_ids"]}
                  for q in spec["qa"]],
           "uncertainty": list(spec.get("uncertainty") or []),
           "limitations": list(spec.get("limitations") or []), "change_records": [],
           "audit": {"gates": [], "checks_passed": 0, "checks_failed": 0, "checks_warned": 0,
                     "unfilled_fields": [], "unmapped_contract_keys": [],
                     "determinism": "byte-identical"},
           "runtime": {"seconds": 3.0,
                       "tools": {"python_pptx": "1.0.2", "renderer": "gain-check-minimal-pdf"}},
           "deck": "output/deck.pptx", "speaker_notes": "output/speaker-notes.md",
           "evidence_map": "output/evidence-map.json", "qa_prep": "output/qa-prep.md",
           "render_preview": "output/render_manifest.json",
           "summary": "gain_check 夹具产物（页数 %d）" % len(spec["pages"])}
    jdump(out / "result.json", res)
    return res


# --------------------------------------------------------------------------- #
# 两个"校验器"
# --------------------------------------------------------------------------- #
def naive_validate(root: Path) -> dict:
    """A 侧自带的"校验"：只看文件在不在、ppt 能不能打开、有没有页。不检查任何内容约束。"""
    out = root / "output"
    checks = {"deck.pptx 存在": (out / "deck.pptx").is_file(),
              "deck.pdf 存在": (out / "deck.pdf").is_file(),
              "slides>=1": False}
    try:
        from pptx import Presentation
        checks["slides>=1"] = len(Presentation(str(out / "deck.pptx")).slides) >= 1
    except Exception:                                                       # noqa: BLE001
        pass
    return {"intercepted": 0, "checks": checks,
            "note": "baseline 无内容级校验：页数/证据/备注重复/图注 一律不管"}


def skill_validate(root: Path, target_gate=None) -> dict:
    r = V.run_gates(root / "output", root / "work" / "spec.json", root / "work")
    gates = r.get("gates") or []
    failed = [g["id"] for g in gates if g["status"] == "fail"]
    hit = (target_gate in failed) if target_gate else bool(failed)
    return {"exit": r.get("exit"), "failed_gates": failed, "hit_target": hit,
            "detail": {g["id"]: g["detail"] for g in gates},
            "failures": {g["id"]: g["failures"] for g in gates if g["failures"]},
            "warned": [g["id"] for g in gates if g["status"] == "warn"],
            "status_suggestion": (r.get("report") or {}).get("status_suggestion")}


# --------------------------------------------------------------------------- #
# 注入
# --------------------------------------------------------------------------- #
def inject(root: Path, kind: str) -> None:
    sp = root / "work" / "spec.json"
    spec = jload(sp)
    if kind == "bullet_fabricated_number":
        spec["pages"][1]["bullets"].append("Fraction rose about 3.7-fold in treated samples")
    elif kind == "clear_evidence_ids":
        spec["pages"][2]["evidence_ids"] = []
    elif kind == "budget_mismatch":
        spec["meta"]["budget"]["slide_count"] = 8
    elif kind == "notes_duplicate":
        for p in spec["pages"]:
            p["notes"] = {"say": " ".join(p.get("bullets") or []),
                          "timing_s": (p.get("notes") or {}).get("timing_s") or 30}
        jdump(sp, spec)
        build_deck(root / "input" / "materials" / "slides-template.pptx", spec,
                   root / "input" / "materials" / "fig1.png", root / "output" / "deck.pptx")
        write_result(root)
        return
    elif kind == "caption_mismatch":
        spec["pages"][3]["figure"]["caption"] = "Figure 1. Summary of our own delta-phase results."
    else:
        raise ValueError("unknown injection: %s" % kind)
    jdump(sp, spec)


def default_work_dir() -> tuple:
    """`--work` 缺省时的落点。**任何情况下都不落在脚本所在树内**（交付树不能污染）。

    优先级（Lead 2026-11-23 裁定）：``--work``（调用方传）> ``DECK_GAIN_WORK``
    > ``<tempdir>/deck-gain-check`` > ``<cwd>/_gain_work``。
    **必须用 ``os.makedirs(..., exist_ok=True)``**——本工作区实测：Windows 上 ``tempfile.mkdtemp()``
    建出的 0o700 目录即便在盘上也写不进去。返回 ``(Path, 来源说明)``。
    """
    attempts = [(Path(tempfile.gettempdir()) / "deck-gain-check", "tempdir/deck-gain-check"),
                (Path.cwd() / "_gain_work", "cwd/_gain_work")]
    errors = []
    for cand, src in attempts:
        try:
            os.makedirs(cand, exist_ok=True)
            probe = cand / ".gain-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            # 硬约束：绝不落在交付树（脚本目录及其祖先）内
            if _inside(cand, HERE.parent) or _inside(cand, HERE):
                errors.append("%s 落在交付树内，拒绝使用" % cand)
                continue
            return cand, src
        except Exception as exc:                                            # noqa: BLE001
            errors.append("%s: %s" % (cand, exc))
    raise RuntimeError("gain_check 找不到可写的树外工作目录: %s" % "; ".join(errors))


def _inside(child: Path, parent: Path) -> bool:
    try:
        c, p = str(child.resolve()).lower(), str(parent.resolve()).lower()
        return c == p or c.startswith(p.rstrip("\\/") + "\\") or c.startswith(p.rstrip("\\/") + "/")
    except Exception:                                                       # noqa: BLE001
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gain_check.py", description="受控 A/B 拦截计数（DoD §7.4）")
    ap.add_argument("--work", default=None,
                    help="工作目录（可选；缺省见 default_work_dir：DECK_GAIN_WORK > "
                         "<脚本目录>/../_gain_work > <tempdir>/deck-gain-check）")
    ap.add_argument("--out", default=None, help="gain-ab.json 路径（默认 <work>/gain-ab.json）")
    ap.add_argument("--scratch", default=None, help="夹具根目录（默认 <work>/gain-ab-run）")
    ap.add_argument("--json", action="store_true", help="stdout 打印完整报告")
    args = ap.parse_args(argv)

    for mod in ("pptx", "PIL"):
        try:
            __import__(mod)
        except Exception as exc:                                            # noqa: BLE001
            print("gain_check 需要 %s 才能真造夹具: %s" % (mod, exc), file=sys.stderr)
            return 2

    if args.work:
        work, work_src = Path(args.work), "cli:--work"
        os.makedirs(work, exist_ok=True)
    elif os.environ.get("DECK_GAIN_WORK"):
        work, work_src = Path(os.environ["DECK_GAIN_WORK"]), "env:DECK_GAIN_WORK"
        os.makedirs(work, exist_ok=True)
    else:
        work, work_src = default_work_dir()
        if _inside(work, HERE.parent) or _inside(work, HERE):
            print("[warn] 缺省 work 目录落在交付树内: %s（不应发生，请检查 default_work_dir）" % work,
                  file=sys.stderr)
    out_path = Path(args.out) if args.out else work / "gain-ab.json"
    scratch = Path(args.scratch) if args.scratch else work / "gain-ab-run"
    scratch.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    clean = scratch / "clean"
    build_case(clean)
    clean_skill = skill_validate(clean)
    clean_naive = naive_validate(clean)

    conditions, a_total, b_total = [], 0, 0
    for d in DEFECTS:
        case = scratch / ("cond-" + d["id"])
        if case.exists():
            shutil.rmtree(case)
        shutil.copytree(clean, case)
        inject(case, d["inject"])
        a = naive_validate(case)
        b = skill_validate(case, target_gate=d["gate"])
        a_total += int(a["intercepted"] > 0)
        b_total += 1 if b["hit_target"] else 0
        conditions.append({
            "defect_id": d["id"], "class": d["class"], "desc": d["desc"],
            "target_gate": d["gate"], "injected_into": str(case),
            "a_intercepted": False, "a_checks": a["checks"],
            "b_intercepted": bool(b["hit_target"]), "b_exit": b["exit"],
            "b_failed_gates": b["failed_gates"], "b_target_gate_failures": b["failures"].get(d["gate"], []),
            "b_target_gate_detail": b["detail"].get(d["gate"], ""),
        })

    # A 策略**自己的产物**上，B 闸门能判出哪几类缺陷（证明缺陷真实可触发）
    naive_case = scratch / "baseline-output"
    build_case(naive_case, naive=True)
    base_skill = skill_validate(naive_case)
    base_naive = naive_validate(naive_case)
    base_gate_set = set(base_skill["failed_gates"])
    base_classes = [d["class"] for d in DEFECTS if d["gate"] in base_gate_set]

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": "gain_check.py",
        "purpose": "统计注入缺陷被拦截的**数量**；不报告分数、不报告提升幅度",
        "work_dir": str(work), "work_dir_source": work_src,
        "scratch": str(scratch), "out": str(out_path),
        "a_strategy": "naive-transfer：摘要/Results 逐段倒页、不查证据、不管页数时长、备注复制正文、图注改写",
        "b_strategy": "validate.run_gates（CONTRACT §5 闸门 A–J）",
        "clean_case": {"path": str(clean), "skill_exit": clean_skill["exit"],
                       "skill_failed_gates": clean_skill["failed_gates"],
                       "skill_warned": clean_skill["warned"],
                       "naive_intercepted": clean_naive["intercepted"]},
        "conditions": conditions,
        "baseline_output": {
            "path": str(naive_case), "a_intercepted": base_naive["intercepted"],
            "a_checks": base_naive["checks"], "b_failed_gates": base_skill["failed_gates"],
            "b_exit": base_skill["exit"], "b_classes_caught": base_classes,
            "b_gate_failures": base_skill["failures"],
        },
        "totals": {"injected_defects": len(DEFECTS), "a_total": a_total, "b_total": b_total},
        "threshold": {"b_min": 3, "must_exceed_a": True},
        "claims": {"b_total >= 3 and b_total > a_total": None},
    }
    ok = (b_total >= 3) and (b_total > a_total)
    report["claims"]["b_total >= 3 and b_total > a_total"] = bool(ok)
    report["exit"] = 0 if ok else 1
    jdump(out_path, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("clean 夹具 A–J: exit=%s failed=%s warned=%s" % (clean_skill["exit"],
                                                            clean_skill["failed_gates"],
                                                            clean_skill["warned"]))
        if clean_skill["failed_gates"] or clean_skill["warned"]:
            # 归因必须干净：clean 不全 pass 会污染"A 拦截 vs B 拦截"的统计，必须打印原因
            for gid in clean_skill["failed_gates"] + clean_skill["warned"]:
                print("   [%s] %s" % (gid, clean_skill["detail"].get(gid, "")))
                for f in clean_skill["failures"].get(gid, []):
                    print("      F:", f)
        for c in conditions:
            print("%-3s %-32s target=%-2s A=%-5s B=%-5s b_exit=%-2s b_fail=%s"
                  % (c["defect_id"], c["class"], c["target_gate"],
                     c["a_intercepted"], c["b_intercepted"], c["b_exit"],
                     ",".join(c["b_failed_gates"]) or "-"))
        print("baseline 产物上 B 判出: %s" % (",".join(base_classes) or "-"))
        print("totals: A=%d B=%d (需 B>=3 且 B>A) → exit=%d, report=%s"
              % (a_total, b_total, report["exit"], out_path))
    return report["exit"]


def cli(argv=None) -> int:
    """pt.py 的入口（`pt.py gain_check` 调 `cli([])`，无参数也必须能跑完并 exit 0）。"""
    return main(argv if argv is not None else [])


if __name__ == "__main__":
    raise SystemExit(main())
