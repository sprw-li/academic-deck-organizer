#!/usr/bin/env python3
"""template.py — 读取真实 pptx 模板的 master / layout / 占位符，产出 template-index。

CONTRACT §3.3（template-index.json 字段名逐字一致）+ §4（`inspect_template` / `role_layout`）。

要点：

* 只用 python-pptx 暴露的**真实**对象（``Presentation.slide_masters`` /
  ``master.slide_layouts`` / ``layout.placeholders``），不从文件名猜结构；
* 主题字体/配色从 ``master.part`` 关联的 theme part XML 读（python-pptx 无公开 API）；
* ``role_map`` 是确定性贪心映射，``role_map_source`` ∈
  ``layout-names | heuristic | fallback``，并在 ``warnings`` 里写明降级原因；
* 一切异常都被收敛成 warning，不抛出（模板损坏时也要给出可诊断的部分结果）。

入口 ``cli(argv)``：``inspect --template X --out Y``（exit 0 / 2）。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
from pathlib import Path

from pptx import Presentation

try:  # 主题读取用得上，缺了也能降级
    from lxml import etree  # type: ignore
except Exception:  # pragma: no cover
    etree = None  # type: ignore

try:
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT  # type: ignore
except Exception:  # pragma: no cover
    RT = None  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))

from normalize import sha256_file  # noqa: E402

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
EMU_PER_INCH = 914400.0

ROLES = ("title", "section", "content", "two_col", "picture", "closing", "qa", "backup")

TITLE_PH_TYPES = {"title", "ctrtitle", "centertitle"}
BODY_PH_TYPES = {"body", "obj", "subtitle", "content", "text"}
PICTURE_PH_TYPES = {"picture", "pic", "bitmappicture"}

# 角色 → 布局名提示（子串匹配，分数越高越优先）
_ROLE_NAME_HINTS = {
    "title": [("title slide", 100), ("cover", 80), ("front page", 70), ("opening", 60), ("title", 55)],
    "section": [("section header", 100), ("section", 90), ("agenda", 85), ("divider", 80),
                ("chapter", 70), ("transition", 60), ("outline", 55)],
    "content": [("title and content", 100), ("content", 90), ("body", 60), ("key result", 60),
                ("bullets", 55), ("text", 40)],
    "two_col": [("two content", 100), ("two column", 100), ("2 content", 95), ("comparison", 85),
                ("side by side", 75), ("columns", 65), ("two", 40)],
    "picture": [("picture with caption", 100), ("content with caption", 90), ("picture", 80),
                ("image", 70), ("figure", 60), ("full image", 55)],
    "closing": [("closing", 100), ("thank", 90), ("end", 80), ("back cover", 75), ("blank", 70),
                ("conclusion", 55)],
}

# 角色 → 占位符类型加分（名字没命中时的次选判据）
_ROLE_PH_BONUS = {
    "title": {"ctrtitle": 45, "title": 25},
    "section": {"title": 20, "ctrtitle": 12, "body": 6},
    "content": {"body": 32, "obj": 26, "subtitle": 12},
    "two_col": {"body": 30, "obj": 24},
    "picture": {"picture": 45, "pic": 45, "body": 8},
    "closing": {"title": 12, "ctrtitle": 8},
}

# 未登记角色（rubric 里的 qa / backup 等）的兜底偏好：
# 先找面试用/备用页，再找空页与收尾页 —— 明确不要落到 index 0（那通常是 Title Slide）
_GENERIC_ROLE_HINTS = [("backup", 95), ("appendix", 90), ("annex", 90), ("qa", 85),
                       ("agenda", 75), ("blank", 65), ("closing", 60), ("section", 55),
                       ("end", 50), ("content", 45)]
_GENERIC_ROLE_PH_BONUS = {"body": 20, "obj": 18, "title": 5}

# 贪心分配顺序（固定 → 可复现）
_ASSIGN_ORDER = ("title", "content", "section", "two_col", "picture", "closing")

# CONTRACT §3.3 要求 role_map 也要覆盖 qa / backup（备用页与问答页）
_EXTRA_ROLES = ("qa", "backup")
_EXTRA_ROLE_HINTS = {
    "qa": [("qa", 95), ("backup", 80), ("agenda", 85), ("closing", 70), ("blank", 60), ("section", 55)],
    "backup": [("backup", 95), ("appendix", 90), ("annex", 90), ("closing", 85),
               ("blank", 80), ("agenda", 70), ("section", 55)],
}


def _hints_for(role: str) -> list:
    return (_ROLE_NAME_HINTS.get(role) or _EXTRA_ROLE_HINTS.get(role) or _GENERIC_ROLE_HINTS)


def _bonuses_for(role: str) -> dict:
    return (_ROLE_PH_BONUS.get(role) or _GENERIC_ROLE_PH_BONUS)


def _score_layout(lay: dict, role: str) -> tuple:
    """返回 (name_score, total_score)：name_score>0 表示布局名命中了角色提示。"""
    name_norm = _norm_name(lay.get("name", ""))
    best_name = 0
    for pattern, weight in _hints_for(role):
        if pattern in name_norm:
            best_name = max(best_name, weight)
    ph_types = {str(p.get("type", "")).lower() for p in lay.get("placeholders") or []}
    ph_bonus = 0
    for t, w in _bonuses_for(role).items():
        if t in ph_types:
            ph_bonus = max(ph_bonus, w)
    if role == "two_col":
        if len([t for t in ph_types if t in BODY_PH_TYPES]) >= 2:
            ph_bonus = max(ph_bonus, 35)
    if role not in _ROLE_NAME_HINTS and (ph_types & BODY_PH_TYPES):
        ph_bonus = max(ph_bonus, 15)
    return best_name, best_name + ph_bonus


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _ph_type(ph) -> str:
    pf = getattr(ph, "placeholder_format", None)
    t = getattr(pf, "type", None) if pf is not None else None
    if t is None:
        return "unknown"
    xml_value = getattr(t, "xml_value", None)
    if xml_value:
        return str(xml_value)
    return str(t).strip().lower()


def _ph_idx(ph):
    pf = getattr(ph, "placeholder_format", None)
    idx = getattr(pf, "idx", None) if pf is not None else None
    try:
        return int(idx)
    except (TypeError, ValueError):
        return None


def _geom(shape) -> dict:
    def one(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "left": one(getattr(shape, "left", None)),
        "top": one(getattr(shape, "top", None)),
        "width": one(getattr(shape, "width", None)),
        "height": one(getattr(shape, "height", None)),
    }


def _placeholders_of(layout) -> list:
    out = []
    try:
        phs = list(layout.placeholders)
    except Exception:
        return out
    for ph in phs:
        try:
            out.append({
                "idx": _ph_idx(ph),
                "type": _ph_type(ph),
                "name": str(getattr(ph, "name", "") or ""),
                "geom": _geom(ph),
            })
        except Exception:
            continue
    out.sort(key=lambda d: (d["idx"] is None, d["idx"] if d["idx"] is not None else 0, d["name"]))
    return out


def _theme_info(master) -> dict:
    fonts = {}
    colors = {}
    if etree is None or RT is None:
        return {"theme_fonts": fonts, "theme_colors": colors}
    try:
        theme_part = master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)
    except Exception:
        return {"theme_fonts": fonts, "theme_colors": colors}

    def _typeface(parent, tag):
        if parent is None:
            return None
        node = parent.find("{%s}%s" % (A_NS, tag))
        if node is None:
            return None
        latin = node.find("{%s}latin" % A_NS)
        if latin is None:
            return None
        return latin.get("typeface") or None

    try:
        scheme = root.find(".//{%s}themeElements/{%s}fontScheme" % (A_NS, A_NS))
        if scheme is not None:
            major = _typeface(scheme, "majorFont")
            minor = _typeface(scheme, "minorFont")
            if major:
                fonts["major"] = major
            if minor:
                fonts["minor"] = minor
    except Exception:
        pass

    try:
        clr = root.find(".//{%s}themeElements/{%s}clrScheme" % (A_NS, A_NS))
        if clr is not None:
            for child in clr:
                tag = etree.QName(child).localname
                val = None
                for sub in child:
                    sub_tag = etree.QName(sub).localname
                    if sub_tag == "srgbClr":
                        val = sub.get("val")
                    elif sub_tag == "sysClr":
                        # sysClr 的 val 是系统色名（windowText…），真实值在 lastClr
                        val = sub.get("lastClr") or val
                    if val:
                        break
                if val:
                    v = val.strip().upper()
                    colors[tag] = v if v.startswith("#") else "#" + v
    except Exception:
        pass
    return {"theme_fonts": fonts, "theme_colors": colors}


def _aspect(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    ratio = w / h
    for name, target in (("16:9", 16 / 9), ("16:10", 16 / 10), ("4:3", 4 / 3), ("3:2", 3 / 2)):
        if abs(ratio - target) < 0.02:
            return name
    return "%.3f:1" % ratio


def _collect_layouts(prs) -> tuple:
    """返回 (layouts_without_role, layout_objects, warnings)。index 为跨 master 的全局序号。"""
    layouts = []
    objects = []
    warnings = []
    try:
        masters = list(prs.slide_masters)
    except Exception as exc:
        return layouts, objects, ["cannot read slide_masters: %s" % exc]
    for mi, master in enumerate(masters):
        try:
            m_layouts = list(master.slide_layouts)
        except Exception as exc:
            warnings.append("master %d unreadable: %s" % (mi, exc))
            continue
        for lay in m_layouts:
            idx = len(layouts)
            name = ""
            try:
                name = str(getattr(lay, "name", "") or "")
            except Exception:
                pass
            phs = _placeholders_of(lay)
            layouts.append({
                "index": idx,
                "master": mi,
                "name": name,
                "placeholders": phs,
            })
            objects.append(lay)
            if not phs:
                warnings.append("layout %r (index %d) exposes no placeholders" % (name, idx))
    return layouts, objects, warnings


def compute_role_map(layouts: list) -> tuple:
    """确定性贪心：返回 (role_map, source, warnings)。"""
    warnings = []
    if not layouts:
        return {}, "fallback", ["template exposes no layouts"]

    used = {}
    assignment = {}
    scores = {}
    for role in _ASSIGN_ORDER:
        scored = []
        for i, lay in enumerate(layouts):
            best_name, total = _score_layout(lay, role)
            reuse_penalty = 25 if i in used else 0
            scored.append((total - reuse_penalty, best_name, -i, i))
        scored.sort(reverse=True)
        top = scored[0]
        if top[0] <= 0:
            continue
        i = top[3]
        assignment[role] = i
        used[i] = role
        scores[role] = (top[1], top[0])

    for role in _ASSIGN_ORDER:
        if role not in assignment:
            free = [i for i in range(len(layouts)) if i not in used]
            if free:
                # 兜底也带偏好：先看哪个 free 布局更像能放正文的，再退回最小 index
                i = max(free, key=lambda k: (_score_layout(layouts[k], role)[1], -k))
                assignment[role] = i
                used[i] = role
                warnings.append("role %r has no name/placeholder match; fell back to layout %r"
                                % (role, layouts[i].get("name")))
            else:
                warnings.append("role %r has no layout left; role_map omits it" % role)

    # qa / backup：CONTRACT §3.3 明确要求的 role_map 键；放在最后，避免抢占上面 6 个主角色的布局
    for role in _EXTRA_ROLES:
        scored = []
        for i, lay in enumerate(layouts):
            best_name, total = _score_layout(lay, role)
            reuse_penalty = 25 if i in used else 0
            scored.append((total - reuse_penalty, best_name, -i, i))
        scored.sort(reverse=True)
        top = scored[0]
        i = top[3]
        assignment[role] = i
        scores[role] = (top[1], top[0])
        if i not in used:
            used[i] = role
        if top[0] <= 0:
            warnings.append("role %r has no name/placeholder match; role_map points at layout %r"
                            % (role, layouts[i].get("name")))

    role_map = {r: layouts[i]["name"] for r, i in assignment.items()}
    name_only = all(scores.get(r, (0, 0))[0] > 0 for r in assignment if r in scores)
    any_scores = bool(scores)
    if name_only and any_scores and all(r in scores for r in assignment):
        source = "layout-names"
    elif any_scores:
        source = "heuristic"
    else:
        source = "fallback"
    return role_map, source, warnings


def inspect_template(path: str | Path) -> dict:
    """读真实模板 → template-index 结构（CONTRACT §3.3）。"""
    p = Path(path)
    info = {
        "path": str(p),
        "sha256": None,
        "slide_size": {"w_emu": None, "h_emu": None, "w_in": None, "h_in": None, "aspect": "unknown"},
        "masters": [],
        "layouts": [],
        "existing_slides": [],
        "role_map": {},
        "role_map_source": "fallback",
        "warnings": [],
    }
    if not p.is_file():
        info["warnings"].append("template file not found: %s" % p)
        return info
    try:
        info["sha256"] = sha256_file(p)
    except OSError as exc:
        info["warnings"].append("cannot hash template: %s" % exc)

    try:
        prs = Presentation(str(p))
    except Exception as exc:
        info["warnings"].append("python-pptx cannot open template: %r" % (exc,))
        return info

    try:
        w = int(prs.slide_width or 0)
        h = int(prs.slide_height or 0)
        info["slide_size"] = {
            "w_emu": w,
            "h_emu": h,
            "w_in": round(w / EMU_PER_INCH, 4) if w else None,
            "h_in": round(h / EMU_PER_INCH, 4) if h else None,
            "aspect": _aspect(w, h),
        }
    except Exception as exc:
        info["warnings"].append("cannot read slide size: %s" % exc)

    try:
        for mi, master in enumerate(prs.slide_masters):
            rec = {"index": mi, "name": str(getattr(master, "name", "") or ""),
                   "theme_fonts": {}, "theme_colors": {}}
            rec.update(_theme_info(master))
            info["masters"].append(rec)
    except Exception as exc:
        info["warnings"].append("cannot read masters: %s" % exc)

    layouts, _, layout_warnings = _collect_layouts(prs)
    info["layouts"] = layouts
    info["warnings"].extend(layout_warnings)

    try:
        for si, slide in enumerate(prs.slides):
            texts = []
            try:
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        txt = shape.text_frame.text
                        if txt and txt.strip():
                            texts.append(txt)
            except Exception:
                pass
            layout_name = ""
            try:
                layout_name = str(slide.slide_layout.name or "")
            except Exception:
                pass
            info["existing_slides"].append({"index": si, "layout": layout_name, "texts": texts})
    except Exception as exc:
        info["warnings"].append("cannot read existing slides: %s" % exc)

    role_map, source, role_warnings = compute_role_map(layouts)
    info["role_map"] = role_map
    info["role_map_source"] = source
    info["warnings"].extend(role_warnings)
    return info


def _layout_by_name(layouts: list, name: str):
    target = (name or "").strip()
    if not target:
        return None
    for lay in layouts:
        if lay.get("name") == target:
            return lay
    norm = _norm_name(target)
    for lay in layouts:
        if _norm_name(lay.get("name", "")) == norm:
            return lay
    return None


def _rank_for_role(layouts: list, role: str) -> list:
    hints = _ROLE_NAME_HINTS.get(role) or _GENERIC_ROLE_HINTS
    bonuses = _ROLE_PH_BONUS.get(role) or _GENERIC_ROLE_PH_BONUS
    scored = []
    for i, lay in enumerate(layouts):
        name_norm = _norm_name(lay.get("name", ""))
        best = 0
        for pattern, weight in hints:
            if pattern in name_norm:
                best = max(best, weight)
        ph_types = {str(p.get("type", "")).lower() for p in lay.get("placeholders") or []}
        for t, w in bonuses.items():
            if t in ph_types:
                best = max(best, w)
        if role not in _ROLE_NAME_HINTS and (ph_types & BODY_PH_TYPES):
            best = max(best, 15)
        scored.append((best, -i, lay))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored


def role_layout(role: str, info: dict, requested: str | None = None) -> dict:
    """角色 → 布局（CONTRACT §4）。source ∈ spec|role_map|heuristic|fallback。"""
    warnings: list = []
    layouts = (info or {}).get("layouts") or []

    def _result(lay, source):
        if lay is None:
            return {"layout": None, "index": -1, "placeholders": [], "source": source, "warnings": warnings}
        return {
            "layout": lay.get("name"),
            "index": lay.get("index"),
            "placeholders": list(lay.get("placeholders") or []),
            "source": source,
            "warnings": warnings,
        }

    if requested:
        hit = _layout_by_name(layouts, requested)
        if hit is not None:
            return _result(hit, "spec")
        warnings.append("requested layout %r not in template; falling back to role %r" % (requested, role))

    rm = (info or {}).get("role_map") or {}
    name = rm.get(role)
    if name:
        hit = _layout_by_name(layouts, name)
        if hit is not None:
            return _result(hit, "role_map")
        warnings.append("role_map[%r]=%r not found in layouts" % (role, name))

    ranked = _rank_for_role(layouts, role)
    if ranked and ranked[0][0] > 0:
        return _result(ranked[0][2], "heuristic")

    if layouts:
        # 最后兜底：宁可要一个能放正文的布局，也不要 index 0（通常是 Title Slide）
        with_body = next(
            (l for l in layouts
             if {str(p.get("type", "")).lower() for p in l.get("placeholders") or []} & BODY_PH_TYPES),
            None)
        pick = with_body if with_body is not None else layouts[0]
        warnings.append("no candidate layout for role %r; fell back to layout %r"
                        % (role, pick.get("name")))
        return _result(pick, "fallback")
    warnings.append("template has no layouts; role %r unresolved" % role)
    return _result(None, "fallback")


# ---- CLI ----------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="template.py", description="inspect a real pptx template")
    sub = ap.add_subparsers(dest="cmd")
    ins = sub.add_parser("inspect", help="write template-index.json")
    ins.add_argument("--template", required=True)
    ins.add_argument("--out", default=None, help="output json path; omit to print to stdout")
    ins.add_argument("--role", default=None, choices=list(ROLES), help="also resolve this role")
    return ap


def cli(argv) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv or []))
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        info = inspect_template(Path(args.template))
    except Exception as exc:  # 兜底：不让调用方拿到 traceback
        sys.stderr.write("template: IO error: %r\n" % (exc,))
        return 2
    if not Path(args.template).is_file():
        sys.stderr.write("template: file not found: %s\n" % args.template)
        return 2

    payload = info
    if args.role:
        payload = dict(info)
        payload["role_layout"] = role_layout(args.role, info)

    if args.out:
        try:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except OSError as exc:
            sys.stderr.write("template: cannot write %s: %s\n" % (args.out, exc))
            return 2

    summary = {
        "path": info["path"],
        "sha256": (info["sha256"] or "")[:16],
        "slide_size": info["slide_size"],
        "layouts": [{"index": l["index"], "master": l["master"], "name": l["name"],
                     "placeholders": [p["type"] for p in l["placeholders"]]} for l in info["layouts"]],
        "role_map": info["role_map"],
        "role_map_source": info["role_map_source"],
        "warnings": info["warnings"],
        "out": args.out,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
