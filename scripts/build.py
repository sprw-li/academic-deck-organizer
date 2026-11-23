#!/usr/bin/env python3
"""build.py — 用真实模板 master/layout 生成可编辑 deck 与配套文本产物。

CONTRACT §4/§3.5 冻结签名::

    def build(spec, work_dir, out_dir, *, template_index=None) -> dict
    def cli(argv) -> int   # build --spec S --work W --out O ; exit 0 / 2 IO / 4 spec

硬约束（本模块的存在理由）：

1. **只用传入模板的真实 master/layout**：``Presentation(template_path)`` 后按
   ``prs.slide_layouts`` 选页；找不到合适占位符时才允许新建 textbox，并写进
   ``deck_plan.json`` 的 ``warnings`` 与逐页 ``textbox_fallbacks``。
2. 正文（bullets）写进 layout 继承的占位符（``<p:ph>``），不是自建 textbox；
   layout 没有正文占位符时会从 master 克隆一个同级占位符（仍带 ``<p:ph>``），
   克隆也失败才退到 textbox 并记 warning。
3. 结尾调用 ``normalize.normalize_pptx(deck_path)`` 并把它的 sha256 作为 deck.pptx 的
   artifact sha256 ⇒ 同 spec + 同模板两次 build 逐字节相同。

产物：``deck.pptx`` / ``deck_plan.json`` / ``speaker-notes.md`` / ``evidence-map.json`` /
``qa-prep.md``。所有文本文件 UTF-8 + LF，JSON 键序固定。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import copy
import difflib
import json
import re
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Emu

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import measure  # noqa: E402
import normalize  # noqa: E402
import template as template_mod  # noqa: E402

TITLE_PH_TYPES = {"title", "ctrtitle", "centertitle"}
BODY_PH_TYPES = {"body", "obj", "subtitle", "content", "text"}
PICTURE_PH_TYPES = {"picture", "pic", "bitmappicture"}
DEFAULT_BODY_FONT_PT = 18.0

CONTENT_ROLES = {"hook", "problem", "gap", "method", "results", "limitation", "conclusion"}


# ---------------------------------------------------------------- 小工具


def _ph_type(ph) -> str:
    pf = getattr(ph, "placeholder_format", None)
    t = getattr(pf, "type", None) if pf is not None else None
    if t is None:
        return "unknown"
    xml_value = getattr(t, "xml_value", None)
    return str(xml_value) if xml_value else str(t).strip().lower()


def _ph_idx(ph):
    pf = getattr(ph, "placeholder_format", None)
    try:
        return int(getattr(pf, "idx", None))
    except (TypeError, ValueError):
        return None


def _ph_name(ph) -> str:
    return str(getattr(ph, "name", "") or "")


def _geom(shape) -> dict:
    def one(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {"left": one(getattr(shape, "left", None)), "top": one(getattr(shape, "top", None)),
            "width": one(getattr(shape, "width", None)), "height": one(getattr(shape, "height", None))}


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm_ws(a).lower(), _norm_ws(b).lower()).ratio(), 4)


def _json_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _detect_input_dir(work_dir: Path, spec: dict):
    meta = (spec or {}).get("meta") or {}
    for key in ("input_dir", "input"):
        v = meta.get(key)
        if v and Path(str(v)).is_dir():
            return Path(str(v))
    mpath = Path(work_dir) / "materials.json"
    if mpath.is_file():
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
            v = data.get("input_dir")
            if v and Path(str(v)).is_dir():
                return Path(str(v))
        except (OSError, ValueError):
            pass
    for cand in (Path("/input"), Path(work_dir).parent / "input", Path(work_dir) / "input"):
        if cand.is_dir():
            return cand
    return None


def resolve_input_path(rel_or_abs, work_dir: Path, input_dir=None) -> Path | None:
    """把 spec 里写的相对路径（materials/xxx、fig1.png…）解析到真实文件。"""
    if not rel_or_abs:
        return None
    p = Path(str(rel_or_abs))
    cands = []
    if p.is_absolute():
        cands.append(p)
    else:
        if input_dir:
            cands.append(Path(input_dir) / p)
        cands.extend([
            Path(work_dir) / p,
            Path(work_dir).parent / p,
            Path("/input") / p,
            Path(work_dir) / "input" / p,
            Path(work_dir).parent / "input" / p,
        ])
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    if input_dir and p.name:
        try:
            hits = sorted(x for x in Path(input_dir).rglob(p.name) if x.is_file())
            if hits:
                return hits[0]
        except OSError:
            pass
    return None


# ------------------------------------------------------- 占位符/形状操作


def _next_shape_id(sp_tree) -> int:
    ids = []
    for el in sp_tree.iter():
        if el.tag == qn("p:cNvPr"):
            try:
                ids.append(int(el.get("id")))
            except (TypeError, ValueError):
                continue
    return (max(ids) if ids else 1) + 1


def _clone_master_placeholder(slide, master_ph):
    """layout 没有正文占位符时，从 master 克隆一个带 <p:ph> 的占位符（不退化成 textbox）。"""
    try:
        sp = copy.deepcopy(master_ph._element)
    except Exception:
        return None
    c_nv_pr = sp.find(".//" + qn("p:cNvPr"))
    sp_tree = slide.shapes._spTree
    if c_nv_pr is not None:
        c_nv_pr.set("id", str(_next_shape_id(sp_tree)))
        c_nv_pr.set("name", (_ph_name(master_ph) or "Placeholder") + " (from master)")
    try:
        sp_tree.append(sp)
        shapes = list(slide.shapes)
        return shapes[-1] if shapes else None
    except Exception:
        return None


def _set_bullets(shape, bullets) -> None:
    tf = shape.text_frame
    try:
        tf.word_wrap = True
    except Exception:
        pass
    tf.clear()
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = str(b)


def _set_title(shape, text: str) -> None:
    tf = shape.text_frame
    try:
        tf.word_wrap = True
    except Exception:
        pass
    tf.text = str(text)


def _add_textbox(slide, left, top, width, height, text):
    """仅用于 caption / 溢出兜底；字体字号不设，继承模板主題，避免违反闸门 C。"""
    box = slide.shapes.add_textbox(Emu(int(left)), Emu(int(top)), Emu(int(width)), Emu(int(height)))
    tf = box.text_frame
    try:
        tf.word_wrap = True
    except Exception:
        pass
    tf.text = str(text)
    return box


def _picture_rect(spec: dict, prs, placement: str) -> tuple:
    sw = int(prs.slide_width or 12192000)
    sh = int(prs.slide_height or 6858000)
    if placement == "full":
        return int(sw * 0.06), int(sh * 0.20), int(sw * 0.88)
    if placement == "left":
        return int(sw * 0.05), int(sh * 0.24), int(sw * 0.42)
    if placement == "right":
        return int(sw * 0.54), int(sh * 0.24), int(sw * 0.42)
    return int(sw * 0.25), int(sh * 0.26), int(sw * 0.50)


# --------------------------------------------------------------- 主体


def _load_template(spec: dict, work_dir: Path, template_index, warnings: list):
    meta = (spec or {}).get("meta") or {}
    tpl = meta.get("template") or {}
    raw = tpl.get("path") or tpl.get("abs")
    input_dir = _detect_input_dir(work_dir, spec)
    path = resolve_input_path(raw, work_dir, input_dir) if raw else None
    if path is None and input_dir:
        for name in ("slides-template.pptx", "template.pptx"):
            cand = Path(input_dir) / "materials" / name
            if cand.is_file():
                path = cand
                break
    if path is None:
        warnings.append("template not found (meta.template.path=%r); fell back to python-pptx default template"
                        % (raw,))
        return Presentation(), None
    try:
        prs = Presentation(str(path))
    except Exception as exc:
        warnings.append("template %s cannot be opened (%r); fell back to python-pptx default template"
                        % (path, exc))
        return Presentation(), path
    if template_index is None:
        try:
            template_index = template_mod.inspect_template(path)
        except Exception as exc:
            warnings.append("template-index inspection failed on real template: %r" % (exc,))
    return prs, path


def _select_layout(prs, layout_objs, by_name, page_layout, role, template_index, warnings: list):
    """返回 (layout_obj, layout_name, source)。只认真实打开文档里的 layout。"""
    requested = (page_layout or "").strip()
    if requested:
        hit = by_name.get(requested) or by_name.get(template_mod._norm_name(requested))
        if hit is not None:
            return hit[0], hit[1], "spec"
        warnings.append("page layout %r not present in real template; resolving by role %r" % (requested, role))

    rm = (template_index or {}).get("role_map") or {}
    name = rm.get(role)
    if name:
        hit = by_name.get(name) or by_name.get(template_mod._norm_name(name))
        if hit is not None:
            return hit[0], hit[1], "role_map"

    info = {"layouts": [{"index": i, "master": 0, "name": n, "placeholders": []}
                        for i, (_o, n) in enumerate(layout_objs)]}
    if template_index:
        # 用 template-index 的占位符信息做角色打分（名字以真实文档为准）
        real_names = {n for _o, n in layout_objs}
        info["layouts"] = [dict(l, placeholders=l.get("placeholders") or [])
                           for l in (template_index.get("layouts") or []) if l.get("name") in real_names]
    pick = template_mod.role_layout(role, info)
    if pick.get("layout"):
        hit = by_name.get(pick["layout"]) or by_name.get(template_mod._norm_name(pick["layout"]))
        if hit is not None:
            warnings.extend(pick.get("warnings") or [])
            return hit[0], hit[1], pick.get("source") or "heuristic"
    warnings.append("role %r cannot be matched to a real layout; using layout index 0" % role)
    if layout_objs:
        return layout_objs[0][0], layout_objs[0][1], "fallback"
    return None, None, "fallback"


def build(spec: dict, work_dir: str | Path, out_dir: str | Path, *, template_index: dict | None = None) -> dict:
    spec = spec or {}
    work_dir = Path(work_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    warnings: list = []
    slides_plan: list = []

    prs, template_path = _load_template(spec, work_dir, template_index, warnings)
    if template_index is None and template_path is not None:
        pass  # 已在 _load_template 里尝试过

    layout_objs = []
    for master in prs.slide_masters:
        try:
            for lay in master.slide_layouts:
                layout_objs.append((lay, str(getattr(lay, "name", "") or "")))
        except Exception as exc:
            warnings.append("cannot enumerate layouts of a master: %r" % (exc,))
    by_name = {}
    for obj, name in layout_objs:
        by_name.setdefault(name, (obj, name))
        by_name.setdefault(template_mod._norm_name(name), (obj, name))

    meta = spec.get("meta") or {}
    lang = str(meta.get("language") or "zh")
    deck_title = str(meta.get("deck_title") or "")
    budget = meta.get("budget") or {}
    evidence_by_id = {str(e.get("id")): e for e in (spec.get("evidence") or []) if e.get("id")}
    figure_media_cache: dict = {}

    input_dir = _detect_input_dir(work_dir, spec)

    pages = spec.get("pages") or []
    total_timing = 0.0
    for index, page in enumerate(pages):
        page = page or {}
        role = str(page.get("role") or "content")
        slide_id = str(page.get("id") or "P%d" % (index + 1))
        claim = str(page.get("claim") or "")
        bullets = [str(b) for b in (page.get("bullets") or [])]
        notes = page.get("notes") or {}
        say = str(notes.get("say") or "")
        timing = notes.get("timing_s")
        try:
            total_timing += float(timing)
        except (TypeError, ValueError):
            pass
        figure = page.get("figure") or None

        slide_warnings: list = []
        layout_obj, layout_name, layout_source = _select_layout(
            prs, layout_objs, by_name, page.get("layout"), role, template_index, slide_warnings)
        if layout_obj is None:
            slide_warnings.append("no layout available; page %s skipped" % slide_id)
            slides_plan.append({
                "index": index, "slide_id": slide_id, "role": role, "layout": None,
                "layout_source": "fallback", "skipped": True, "claim": claim,
                "warnings": slide_warnings,
            })
            warnings.extend("%s: %s" % (slide_id, w) for w in slide_warnings)
            continue

        slide = prs.slides.add_slide(layout_obj)

        # 注意：placeholder_format.type 的 xml_value 是驼峰（ctrTitle/subTitle），比对一律小写
        ph_list = []
        for ph in list(slide.placeholders):
            ph_list.append({"idx": _ph_idx(ph), "type": _ph_type(ph), "name": _ph_name(ph),
                            "geom": _geom(ph), "shape": ph})
        title_ph = next((p for p in ph_list if p["type"].lower() in TITLE_PH_TYPES), None)
        if title_ph is None:
            title_ph = next((p for p in ph_list if p["idx"] == 0), None)
        body_phs = [p for p in ph_list if p["type"].lower() in BODY_PH_TYPES and p is not title_ph]
        pic_phs = [p for p in ph_list if p["type"].lower() in PICTURE_PH_TYPES]

        # ---- 先算图的位置，并让正文占位符避开它（确定性几何，仍用 layout 继承的占位符）----
        fig_media = None
        fig_rect = None
        fig_placement = str((figure or {}).get("placement") or "none")
        if figure:
            rel = figure.get("media_path") or figure.get("path")
            if rel:
                if rel in figure_media_cache:
                    fig_media = figure_media_cache[rel]
                else:
                    fig_media = resolve_input_path(rel, work_dir, input_dir)
                    figure_media_cache[rel] = fig_media
            if fig_placement != "none" and fig_media is not None and not pic_phs:
                left, top, width = _picture_rect(spec, prs, fig_placement)
                height = int(width * 0.5625)
                try:
                    from PIL import Image as _PILImage  # 只为按原图比例定高
                    with _PILImage.open(str(fig_media)) as _im:
                        if _im.width and _im.height:
                            height = int(width * _im.height / _im.width)
                except Exception:
                    pass
                fig_rect = (left, top, width, height)
                gap = max(91440, int(int(prs.slide_width) * 0.012))
                for tgt in body_phs:
                    g = tgt["geom"] or {}
                    bl, bw, bt, bh = g.get("left"), g.get("width"), g.get("top"), g.get("height")
                    if not bl or not bw:
                        continue
                    # 水平不相交 → 不动
                    if (bl + bw) <= left or bl >= (left + width):
                        continue
                    shape = tgt["shape"]
                    if fig_placement == "right":
                        new_w = int(left - bl - gap)
                        if new_w > 914400:
                            shape.width = Emu(new_w)
                            tgt["geom"]["width"] = new_w
                            slide_warnings.append(
                                "body placeholder %s narrowed to %d EMU to avoid the figure (placement=right)"
                                % (tgt["name"], new_w))
                    elif fig_placement == "left":
                        new_left = int(left + width + gap)
                        slide_w = int(int(prs.slide_width) * 0.97) - new_left
                        if slide_w > 914400:
                            shape.left = Emu(new_left)
                            shape.width = Emu(slide_w)
                            tgt["geom"]["left"], tgt["geom"]["width"] = new_left, slide_w
                            slide_warnings.append(
                                "body placeholder %s moved right of the figure (placement=left)" % tgt["name"])
                    elif fig_placement == "full" and bt and bh:
                        new_h = int(top - bt - gap)
                        if new_h > 457200:
                            shape.height = Emu(new_h)
                            tgt["geom"]["height"] = new_h
                            slide_warnings.append(
                                "body placeholder %s shortened to %d EMU to avoid the figure (placement=full)"
                                % (tgt["name"], new_h))
                    break

        used_log: list = []
        for p in ph_list:
            entry = {"idx": p["idx"], "type": p["type"], "name": p["name"], "geom": p["geom"], "used_for": None}
            used_log.append(entry)
        log_by_shape = {id(p["shape"]): entry for p, entry in zip(ph_list, used_log)}

        textbox_fallbacks: list = []

        # --- 标题 ---
        title_text = deck_title if role == "title" and deck_title else claim
        if title_ph is not None and (title_text or bullets):
            _set_title(title_ph["shape"], title_text or deck_title)
            log_by_shape[id(title_ph["shape"])]["used_for"] = "title/claim"
        elif title_text:
            r = _picture_rect(spec, prs, "full")
            _add_textbox(slide, r[0], int(r[1] * 0.45), r[2], int(prs.slide_height * 0.18), title_text)
            slide_warnings.append("no title placeholder on layout %r; title placed in a textbox" % layout_name)
            textbox_fallbacks.append({"purpose": "title", "reason": "no title placeholder in layout"})

        # --- 正文 ---
        body_targets = list(body_phs)
        wanted_groups = [bullets]
        if role == "two_col" and len(body_targets) >= 2 and len(bullets) >= 2:
            half = (len(bullets) + 1) // 2
            wanted_groups = [bullets[:half], bullets[half:]]
        if bullets and not body_targets:
            master_body = None
            try:
                for mph in prs.slide_masters[0].placeholders:
                    if _ph_type(mph).lower() in BODY_PH_TYPES:
                        master_body = mph
                        break
            except Exception:
                master_body = None
            if master_body is not None:
                cloned = _clone_master_placeholder(slide, master_body)
                if cloned is not None:
                    body_targets = [{"idx": _ph_idx(cloned), "type": _ph_type(cloned), "name": _ph_name(cloned),
                                     "geom": _geom(cloned), "shape": cloned}]
                    used_log.append({"idx": body_targets[0]["idx"], "type": body_targets[0]["type"],
                                     "name": body_targets[0]["name"], "geom": body_targets[0]["geom"],
                                     "used_for": "bullets(from master clone)"})
                    log_by_shape[id(cloned)] = used_log[-1]
                    slide_warnings.append(
                        "layout %r has no body placeholder; cloned body placeholder from master "
                        "(kept <p:ph>, not a raw textbox)" % layout_name)
        for gi, group in enumerate(wanted_groups):
            if not group:
                continue
            if gi < len(body_targets):
                tgt = body_targets[gi]
                _set_bullets(tgt["shape"], group)
                entry = log_by_shape.get(id(tgt["shape"]))
                if entry is not None:
                    entry["used_for"] = "bullets" if len(wanted_groups) == 1 else "bullets[%d]" % gi
            else:
                # 没有任何正文占位符可放 ⇒ textbox（明确记 warning）
                r = _picture_rect(spec, prs, "full")
                _add_textbox(slide, r[0], int(prs.slide_height * 0.30), r[2], int(prs.slide_height * 0.5),
                             "\n".join(group))
                slide_warnings.append("layout %r has no body placeholder; %d bullets written to a textbox"
                                      % (layout_name, len(group)))
                textbox_fallbacks.append({"purpose": "bullets", "reason": "no body placeholder available",
                                          "bullets": len(group)})

        # --- 图 ---
        figure_plan = None
        if figure:
            media_rel = figure.get("media_path") or figure.get("path")
            media = fig_media
            placement = fig_placement
            figure_plan = {"evidence_id": figure.get("evidence_id"), "media_path": media_rel,
                           "placement": placement, "caption": figure.get("caption"),
                           "resolved": str(media) if media else None,
                           "inserted": False, "placement_mode": None}
            if placement == "none":
                figure_plan["note"] = "placement=none; figure intentionally not drawn"
            elif media is None:
                slide_warnings.append("figure media_path %r not found on disk; figure omitted" % (media_rel,))
                figure_plan["note"] = "media file not found"
            else:
                inserted = False
                if pic_phs:
                    try:
                        pic_phs[0]["shape"].insert_picture(str(media))
                        log_by_shape[id(pic_phs[0]["shape"])]["used_for"] = "figure"
                        inserted = True
                        figure_plan["placement_mode"] = "picture-placeholder"
                    except Exception as exc:
                        slide_warnings.append("insert into picture placeholder failed: %r" % (exc,))
                if not inserted:
                    if fig_rect:
                        left, top, width = fig_rect[0], fig_rect[1], fig_rect[2]
                    else:
                        left, top, width = _picture_rect(spec, prs, placement)
                    try:
                        pic = slide.shapes.add_picture(str(media), Emu(left), Emu(top), width=Emu(width))
                        inserted = True
                        figure_plan["placement_mode"] = "add_picture(placement=%s)" % placement
                        if figure.get("caption"):
                            cap_top = int(top + (pic.height or 0)) + int(int(prs.slide_height) * 0.01)
                            cap_h = max(400000, int(int(prs.slide_height) * 0.12))
                            _add_textbox(slide, left, cap_top, width, cap_h, str(figure.get("caption")))
                    except Exception as exc:
                        slide_warnings.append("add_picture failed for %r: %r" % (media, exc))
                figure_plan["inserted"] = inserted

        # --- 备注 ---
        notes_text = say
        checkpoint = notes.get("checkpoint")
        if checkpoint:
            notes_text = (say + "\n\n[Checkpoint] " + str(checkpoint)).strip()
        if notes_text:
            try:
                slide.notes_slide.notes_text_frame.text = notes_text
            except Exception as exc:
                slide_warnings.append("cannot write speaker notes: %r" % (exc,))
        if role in CONTENT_ROLES and not say:
            slide_warnings.append("content page %s has empty notes.say" % slide_id)

        # --- 预算/相似度预警（不改写 spec，只报告）---
        if role in CONTENT_ROLES:
            sim = _similarity(say, " ".join(bullets))
            if sim >= 0.8:
                slide_warnings.append("notes.say vs bullets normalized similarity=%.4f (>=0.8)" % sim)
        for tgt in body_targets[:max(1, len(wanted_groups))]:
            g = tgt.get("geom") or {}
            if g.get("width") and g.get("height"):
                fit = measure.text_fits("\n".join(bullets), g["width"], g["height"], DEFAULT_BODY_FONT_PT, lang=lang)
                if not fit["fits"]:
                    slide_warnings.append(
                        "bullet text may overflow placeholder %s: est_lines=%d overflow_ratio=%.3f"
                        % (tgt.get("name"), fit["est_lines"], fit["overflow_ratio"]))
                break

        if not (page.get("evidence_ids") or []) and role not in {"title", "qa", "backup"}:
            slide_warnings.append("content page %s has no evidence_ids" % slide_id)
        for eid in page.get("evidence_ids") or []:
            if str(eid) not in evidence_by_id:
                slide_warnings.append("evidence id %r referenced by %s is not defined in spec.evidence"
                                      % (eid, slide_id))

        slides_plan.append({
            "index": index,
            "slide_id": slide_id,
            "role": role,
            "layout": layout_name,
            "layout_source": layout_source,
            "claim": claim,
            "evidence_ids": list(page.get("evidence_ids") or []),
            "strength": page.get("strength"),
            "bullets": bullets,
            "figure": figure_plan,
            "notes": {"say": say, "timing_s": timing, "checkpoint": checkpoint,
                      "written": bool(notes_text)},
            "placeholders": used_log,
            "textbox_fallbacks": textbox_fallbacks,
            "warnings": slide_warnings,
        })
        warnings.extend("%s: %s" % (slide_id, w) for w in slide_warnings)

    # ---- 预算一致性 ----
    minutes = budget.get("minutes")
    try:
        if minutes:
            target = float(minutes) * 60.0
            if target > 0:
                dev = (total_timing - target) / target
                if abs(dev) > 0.15:
                    warnings.append("sum(notes.timing_s)=%.1fs deviates %.1f%% from budget.minutes*60=%.1fs"
                                    % (total_timing, dev * 100.0, target))
        declared = budget.get("slide_count")
        if declared is not None and int(declared) != len(pages):
            warnings.append("spec pages=%d != budget.slide_count=%s" % (len(pages), declared))
    except (TypeError, ValueError):
        pass

    # ---- 落盘 ----
    deck_path = out_dir / "deck.pptx"
    tmp_deck = out_dir / "deck.tmp.pptx"
    prs.save(str(tmp_deck))
    tmp_deck.replace(deck_path)
    deck_sha = normalize.normalize_pptx(deck_path)
    deck_bytes = deck_path.stat().st_size

    deck_plan = {
        "schema_version": 1,
        "deck_title": deck_title,
        "case_id": ((spec.get("meta") or {}).get("case_id")),
        "language": lang,
        "spec": {"case_id": ((spec.get("meta") or {}).get("case_id")),
                 "deck_title": deck_title, "language": lang,
                 "slide_count": len(pages), "budget": budget},
        "template": {"path": str(template_path) if template_path else None,
                     "index_path": ((spec.get("meta") or {}).get("template") or {}).get("path"),
                     "sha256": (template_index or {}).get("sha256")},
        "deck": {"path": deck_path.name, "bytes": deck_bytes, "sha256": deck_sha,
                 "slides": len(prs.slides)},
        "warnings": warnings,
        "slides": slides_plan,
    }
    artifacts = {"deck.pptx": {"path": str(deck_path), "bytes": deck_bytes, "sha256": deck_sha}}
    artifacts["deck_plan.json"] = normalize.write_text(out_dir / "deck_plan.json", _json_text(deck_plan))

    artifacts["speaker-notes.md"] = normalize.write_text(
        out_dir / "speaker-notes.md", _speaker_notes_md(slides_plan, deck_title, lang))
    artifacts["evidence-map.json"] = normalize.write_text(
        out_dir / "evidence-map.json", _json_text(_evidence_map(spec, slides_plan, deck_title)))
    artifacts["qa-prep.md"] = normalize.write_text(
        out_dir / "qa-prep.md", _qa_prep_md(spec, slides_plan))

    return {
        "ok": True,
        "slides": len(slides_plan),
        "artifacts": artifacts,
        "warnings": warnings,
        "deck_sha256": deck_sha,
        "deck_plan": deck_plan,
    }


# ------------------------------------------------------------- 文本产物


def _fmt_q(s: str) -> str:
    return _norm_ws(s)


def _speaker_notes_md(slides_plan, deck_title, lang) -> str:
    lines = ["# Speaker notes — %s" % (deck_title or "(untitled)"), ""]
    total = 0.0
    for s in slides_plan:
        if s.get("skipped"):
            continue
        n = s.get("notes") or {}
        try:
            total += float(n.get("timing_s") or 0)
        except (TypeError, ValueError):
            pass
        lines.append("## %s · %s · role=%s · ~%ss" % (s.get("slide_id"), s.get("layout"),
                                                      s.get("role"), n.get("timing_s")))
        lines.append("")
        if s.get("claim"):
            lines.append("**Claim:** %s" % s["claim"])
            lines.append("")
        for b in s.get("bullets") or []:
            lines.append("- %s" % b)
        if s.get("bullets"):
            lines.append("")
        if n.get("say"):
            lines.append("**Say:** %s" % n["say"])
            lines.append("")
        if n.get("checkpoint"):
            lines.append("**Checkpoint:** %s" % n["checkpoint"])
            lines.append("")
    lines.append("---")
    lines.append("Total planned speaking time: %.1fs" % total)
    lines.append("")
    return "\n".join(lines)


def _evidence_map(spec, slides_plan, deck_title) -> dict:
    evidence_by_id = {str(e.get("id")): e for e in (spec.get("evidence") or []) if e.get("id")}
    pages = []
    for s in slides_plan:
        if s.get("skipped"):
            continue
        items = []
        for eid in s.get("evidence_ids") or []:
            e = evidence_by_id.get(str(eid))
            if not e:
                items.append({"id": str(eid), "resolved": False, "reason": "not defined in spec.evidence"})
                continue
            src = e.get("source") or {}
            items.append({
                "id": str(eid),
                "resolved": True,
                "kind": e.get("kind"),
                "value": e.get("value"),
                "unit": e.get("unit"),
                "file": src.get("file"),
                "locator": src.get("locator"),
                "quote": src.get("quote"),
            })
        pages.append({
            "slide_id": s.get("slide_id"),
            "slide_number": int(s.get("index", 0)) + 1,
            "role": s.get("role"),
            "layout": s.get("layout"),
            "claim": s.get("claim"),
            "strength": s.get("strength"),
            "evidence_ids": list(s.get("evidence_ids") or []),
            "evidence": items,
        })
    return {"schema_version": 1, "deck_title": deck_title, "pages": pages}


def _qa_prep_md(spec, slides_plan) -> str:
    qa = spec.get("qa") or []
    lines = ["# Q&A preparation", ""]
    if not qa:
        lines.append("_No QA items declared in spec.json._")
        lines.append("")
    for i, q in enumerate(qa, start=1):
        q = q or {}
        lines.append("## %s · risk=%s · category=%s" % (q.get("id") or "Q%d" % i, q.get("risk"),
                                                       q.get("category")))
        lines.append("")
        lines.append("**Question:** %s" % (q.get("question") or ""))
        lines.append("")
        lines.append("**Answer:** %s" % (q.get("answer") or ""))
        lines.append("")
        slides = ", ".join(str(x) for x in (q.get("slide_ids") or [])) or "(none)"
        ev = ", ".join(str(x) for x in (q.get("evidence_ids") or [])) or "(none)"
        lines.append("**Slides:** %s" % slides)
        lines.append("")
        lines.append("**Evidence:** %s" % ev)
        lines.append("")
    backup = [s for s in slides_plan if (s.get("role") == "backup") or (s.get("role") == "qa")]
    if backup:
        lines.append("## Backup / QA pages")
        lines.append("")
        for s in backup:
            lines.append("- %s (%s): %s" % (s.get("slide_id"), s.get("layout"), s.get("claim")))
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="build.py", description="build a deck from spec.json using a real template")
    sub = ap.add_subparsers(dest="cmd")
    bd = sub.add_parser("build", help="build deck + companion text artifacts")
    bd.add_argument("--spec", required=True)
    bd.add_argument("--work", required=True)
    bd.add_argument("--out", required=True)
    bd.add_argument("--template-index", default=None, help="optional template-index.json")
    return ap


def cli(argv) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv or []))
    if not args.cmd:
        parser.print_help()
        return 2

    spec_path = Path(args.spec)
    if not spec_path.is_file():
        sys.stderr.write("build: spec not found: %s\n" % spec_path)
        return 2
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write("build: cannot read spec: %r\n" % (exc,))
        return 4
    if not isinstance(spec, dict) or not isinstance(spec.get("pages"), list):
        sys.stderr.write("build: spec must be an object with a 'pages' list\n")
        return 4

    template_index = None
    if args.template_index:
        try:
            template_index = json.loads(Path(args.template_index).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            sys.stderr.write("build: cannot read template-index: %r\n" % (exc,))
            return 2

    try:
        result = build(spec, Path(args.work), Path(args.out), template_index=template_index)
    except OSError as exc:
        sys.stderr.write("build: IO error: %r\n" % (exc,))
        return 2

    summary = {
        "ok": result["ok"],
        "slides": result["slides"],
        "deck_sha256": result["deck_sha256"],
        "artifacts": {k: {"path": v["path"], "bytes": v["bytes"], "sha256": v["sha256"]}
                      for k, v in result["artifacts"].items()},
        "warnings": result["warnings"],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
