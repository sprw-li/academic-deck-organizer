#!/usr/bin/env python3
"""render.py — 把 deck.pptx 真实导出为 deck.pdf + 逐页 PNG，并写渲染证据。

CONTRACT §4/§3.5/§6 冻结签名::

    def render_deck(pptx, out_dir, *, dpi=144) -> dict
        # 写 deck.pdf / preview/*.png / render_manifest.json / export_report.json
        # 找不到渲染器 -> {"ok": False, "reason": "renderer-unavailable"}（不抛异常）

工具发现顺序（§6 + work/RUNTIME-PATHS.md 实测）：

1. ``office-render-tool`` / ``office-renderer`` 可执行（``shutil.which``）；
2. 环境变量 ``DSH_OFFICE_NODE`` + ``DSH_OFFICE_CLI``；
3. RUNTIME-PATHS 里的硬路径（node + ``libreoffice-kit/lib/cli.js``）；
4. ``shutil.which('node')`` + 在若干根目录下有界搜索 ``libreoffice-kit/lib/cli.js``。

任何一步失败都不抛异常：写 ``render_manifest.json`` / ``export_report.json`` 并返回结构化失败。
PNG 一律重命名为 ``preview/page-0001.png``（按页号），sha256/宽高/dpi 逐张实测。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from normalize import sha256_file, write_text  # noqa: E402

TIMEOUT_S = 300

HARD_NODE = r"D:\App\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"
HARD_CLI = (r"D:\App\DeepSeek Harness\resources\app.asar.unpacked\dsh\node_modules"
            r"\@deepseek-ai\libreoffice-kit\lib\cli.js")

_MANIFEST_NAME = "render_manifest.json"
_REPORT_NAME = "export_report.json"


# ------------------------------------------------------------ 工具发现


def _search_cli(roots, max_depth=7):
    """在 roots 下做有界深度搜索，找 libreoffice-kit/lib/cli.js。"""
    rel = ("libreoffice-kit", "lib", "cli.js")
    for root in roots:
        try:
            root = Path(root)
        except (TypeError, ValueError):
            continue
        if not root.is_dir():
            continue
        try:
            for depth in range(max_depth):
                pattern = "/".join(["*"] * depth + list(rel))
                for hit in sorted(root.glob(pattern)):
                    if hit.is_file():
                        return hit
        except (OSError, ValueError):
            continue
    return None


def discover_renderer() -> dict | None:
    """返回 {"kind","node","cli","exe","source"} 或 None（不可用）。"""
    for exe in ("office-render-tool", "office-renderer", "office-render-tool.exe"):
        p = shutil.which(exe)
        if p:
            return {"kind": "exe", "exe": p, "node": None, "cli": None, "source": "PATH:%s" % exe}

    node = os.environ.get("DSH_OFFICE_NODE")
    cli = os.environ.get("DSH_OFFICE_CLI")
    if node and cli and Path(node).is_file() and Path(cli).is_file():
        return {"kind": "libreoffice-kit", "node": str(node), "cli": str(cli),
                "exe": None, "source": "env:DSH_OFFICE_NODE+DSH_OFFICE_CLI"}

    if Path(HARD_NODE).is_file() and Path(HARD_CLI).is_file():
        return {"kind": "libreoffice-kit", "node": HARD_NODE, "cli": HARD_CLI,
                "exe": None, "source": "runtime-paths:hardcoded"}

    node2 = shutil.which("node") or shutil.which("node.exe")
    if node2:
        roots = [
            Path(HARD_CLI).parents[5] if len(Path(HARD_CLI).parents) > 5 else Path(HARD_CLI).parent,
            Path("D:/App/DeepSeek Harness/resources/app.asar.unpacked"),
            Path("D:/App/DeepSeek Harness/resources"),
            Path("/usr/lib/node_modules"),
            Path("/usr/local/lib/node_modules"),
            Path.home() / ".npm-global/lib/node_modules",
        ]
        found = _search_cli(roots)
        if found:
            return {"kind": "libreoffice-kit", "node": node2, "cli": str(found),
                    "exe": None, "source": "which(node)+search"}
    return None


def _renderer_cmd(renderer: dict, subcmd: str, *args) -> list:
    if renderer.get("kind") == "exe":
        return [renderer["exe"], subcmd, *[str(a) for a in args]]
    return [renderer["node"], renderer["cli"], subcmd, *[str(a) for a in args]]


def _child_env() -> dict:
    """给 node 子进程用的环境：**完整继承** os.environ，并补回 Windows 必需项的缺失键。

    为什么需要"补回"：本机实测（2026-11-23）给 node 传只含
    ``PATH/PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE`` 的裁剪 env 时，node 在
    ``node::InitializeOncePerProcessInternal`` 里断言
    ``Assertion failed: ncrypto::CSPRNG(nullptr, 0)`` 直接 SIGABRT（exit 134）——
    缺 ``SystemRoot`` 会让 BCrypt/CSPRNG 初始化失败。平台沙箱若给裁剪 env，这里兜底。
    """
    env = dict(os.environ)
    repaired = []
    if os.name == "nt":
        if not env.get("SystemRoot"):
            env["SystemRoot"] = r"C:\Windows"
            repaired.append("SystemRoot")
        if not env.get("SystemDrive"):
            env["SystemDrive"] = env["SystemRoot"][:2]
            repaired.append("SystemDrive")
        if not env.get("windir"):
            env["windir"] = env["SystemRoot"]
            repaired.append("windir")
        if not (env.get("TEMP") or env.get("TMP")):
            try:
                tmp = tempfile.gettempdir()
            except Exception:
                tmp = env["SystemRoot"] + r"\Temp"
            env["TEMP"] = tmp
            env["TMP"] = tmp
            repaired.append("TEMP")
    else:
        if not (env.get("TMPDIR") or env.get("TMP") or env.get("TEMP")):
            try:
                tmp = tempfile.gettempdir()
            except Exception:
                tmp = "/tmp"
            env["TMPDIR"] = tmp
            repaired.append("TMPDIR")
    env["_RENDER_ENV_REPAIRED"] = ",".join(repaired)
    return env


def _run(argv, timeout=TIMEOUT_S) -> dict:
    t0 = time.monotonic()
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, env=_child_env())
        return {"argv": [str(a) for a in argv], "exit": int(cp.returncode),
                "stdout": cp.stdout or "", "stderr": cp.stderr or "",
                "seconds": round(time.monotonic() - t0, 3), "timeout": False}
    except subprocess.TimeoutExpired:
        return {"argv": [str(a) for a in argv], "exit": None, "stdout": "", "stderr": "timeout",
                "seconds": round(time.monotonic() - t0, 3), "timeout": True}
    except OSError as exc:
        return {"argv": [str(a) for a in argv], "exit": None, "stdout": "", "stderr": repr(exc),
                "seconds": round(time.monotonic() - t0, 3), "timeout": False}


def _stderr_digest(result: dict, *, max_lines: int = 6, max_chars: int = 700) -> str:
    """把子进程 stderr（或 stdout 里的诊断）压成一行摘要，用于 reason 与 warnings。"""
    text = (result or {}).get("stderr") or ""
    if not text.strip():
        text = (result or {}).get("stdout") or ""
    lines = []
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        lines.append(ln)
        if len(lines) >= max_lines:
            break
    digest = " | ".join(lines)
    return digest[:max_chars]


def _stdout_json(text: str):
    """从 CLI stdout 里抠出第一个完整 JSON 对象。"""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


# --------------------------------------------------------------- 度量


def _pptx_slide_count(pptx: Path) -> int:
    try:
        with zipfile.ZipFile(pptx, "r") as zf:
            names = [n for n in zf.namelist()
                     if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            return len(names)
    except (OSError, zipfile.BadZipFile):
        return 0


def _pdf_page_count(pdf: Path) -> int:
    try:
        from pypdf import PdfReader  # type: ignore
        try:
            return int(len(PdfReader(str(pdf)).pages))
        except Exception:
            pass
    except Exception:
        pass
    try:
        data = pdf.read_bytes()
    except OSError:
        return 0
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages[^>]*?/Count\s+(\d+)", data, re.S)]
    return max(counts) if counts else 0


def _pptx_slide_size(pptx: Path) -> dict:
    """读 deck 的画布尺寸（EMU → inch），给 gate F 做 "PNG 像素宽 ≈ w_in × dpi" 反推。"""
    w_emu = h_emu = 0
    try:
        from pptx import Presentation  # type: ignore
        prs = Presentation(str(pptx))
        w_emu, h_emu = int(prs.slide_width or 0), int(prs.slide_height or 0)
    except Exception:
        try:
            with zipfile.ZipFile(pptx, "r") as zf:
                xml = zf.read("ppt/presentation.xml").decode("utf-8", "replace")
            m = re.search(r"<p:sldSz[^>]*\bcx=\"(\d+)\"[^>]*\bcy=\"(\d+)\"", xml)
            if not m:
                m = re.search(r"<p:sldSz[^>]*\bcy=\"(\d+)\"[^>]*\bcx=\"(\d+)\"", xml)
                if m:
                    h_emu, w_emu = int(m.group(1)), int(m.group(2))
            else:
                w_emu, h_emu = int(m.group(1)), int(m.group(2))
        except Exception:
            return {"w": None, "h": None, "w_emu": None, "h_emu": None}
    return {"w": round(w_emu / 914400.0, 4) if w_emu else None,
            "h": round(h_emu / 914400.0, 4) if h_emu else None,
            "w_emu": w_emu or None, "h_emu": h_emu or None}


def _png_info(path: Path) -> dict:
    info = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    try:
        from PIL import Image  # type: ignore
        with Image.open(str(path)) as im:
            info["width"], info["height"] = int(im.width), int(im.height)
            dpi = im.info.get("dpi")
            if dpi:
                try:
                    info["dpi"] = round(float(dpi[0]), 3)
                except (TypeError, ValueError, IndexError):
                    pass
    except Exception:
        pass
    return info


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------- 主体


def _write_manifest(out_dir: Path, manifest: dict, report: dict) -> None:
    write_text(out_dir / _MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(out_dir / _REPORT_NAME, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def render_deck(pptx: str | Path, out_dir: str | Path, *, dpi: int = 144) -> dict:
    pptx = Path(pptx)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = out_dir / "preview"
    commands: list = []
    warnings: list = []

    renderer = discover_renderer()
    source_sha = sha256_file(pptx) if pptx.is_file() else None
    source_bytes = pptx.stat().st_size if pptx.is_file() else 0

    base_manifest = {
        "schema_version": 1,
        "ok": False,
        "reason": None,
        "dpi": int(dpi),
        "pageCount": 0,
        "missingFonts": [],
        "slide_size_in": None,
        "sha256": source_sha,
        "pptx_sha256": source_sha,
        "source": {"path": str(pptx), "bytes": source_bytes, "sha256": source_sha},
        "pages": [],
        "pdf": None,
        "preview_dir": "preview",
        "warnings": warnings,
    }

    if not pptx.is_file():
        base_manifest["reason"] = "pptx-not-found"
        report = {"schema_version": 1, "ok": False, "reason": "pptx-not-found",
                  "renderer": None, "commands": [], "warnings": [str(pptx)]}
        _write_manifest(out_dir, base_manifest, report)
        return {"ok": False, "reason": "pptx-not-found", "pageCount": 0, "dpi": int(dpi),
                "missingFonts": [], "artifacts": {}, "warnings": [str(pptx)]}

    if renderer is None:
        base_manifest["reason"] = "renderer-unavailable"
        base_manifest["renderer"] = {"available": False}
        report = {"schema_version": 1, "ok": False, "reason": "renderer-unavailable",
                  "renderer": {"available": False,
                               "probed": {"env": ["DSH_OFFICE_NODE", "DSH_OFFICE_CLI"],
                                          "hard_paths": [HARD_NODE, HARD_CLI],
                                          "which": ["office-render-tool", "office-renderer", "node"]}},
                  "commands": [], "warnings": warnings,
                  "pdf": None, "preview": {"pageCount": 0, "pages": []}}
        _write_manifest(out_dir, base_manifest, report)
        return {"ok": False, "reason": "renderer-unavailable", "pageCount": 0, "dpi": int(dpi),
                "missingFonts": [], "artifacts": {"render_manifest.json":
                                                  {"path": str(out_dir / _MANIFEST_NAME)}},
                "warnings": warnings}

    renderer_pub = {"available": True, "kind": renderer.get("kind"), "node": renderer.get("node"),
                    "cli": renderer.get("cli"), "exe": renderer.get("exe"),
                    "source": renderer.get("source")}
    base_manifest["renderer"] = renderer_pub

    expected = _pptx_slide_count(pptx)
    slide_size = _pptx_slide_size(pptx)

    # --- 1) convert → deck.pdf ---
    pdf_path = out_dir / "deck.pdf"
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError:
            pass
    conv = _run(_renderer_cmd(renderer, "convert", "--input", str(pptx), "--output", str(pdf_path)))
    commands.append(dict(conv, step="convert"))
    conv_json = _stdout_json(conv["stdout"]) or {}
    missing_fonts = list(conv_json.get("missingFonts") or [])
    pdf_ok = conv["exit"] == 0 and pdf_path.is_file()
    pdf_meta = None
    if pdf_ok:
        pdf_meta = {"path": str(pdf_path), "bytes": pdf_path.stat().st_size,
                    "sha256": sha256_file(pdf_path), "pageCount": _pdf_page_count(pdf_path)}
    else:
        warnings.append("convert failed (exit=%s): %s"
                        % (conv["exit"], _stderr_digest(conv) or "(no stderr)"))

    # --- 2) render → preview PNGs（多轮尝试，每次都留证据）---
    pages_argv = list(range(1, expected + 1)) if expected else []

    def _render_args(src: Path, with_pages: bool) -> list:
        args = ["--input", str(src), "--output-dir", str(preview_dir), "--dpi", str(int(dpi))]
        if with_pages and pages_argv:
            args += ["--pages", ",".join(str(p) for p in pages_argv)]
        return args

    def _publish(attempt: dict, src: Path, with_pages: bool) -> None:
        if preview_dir.exists():
            shutil.rmtree(preview_dir, ignore_errors=True)
        res = _run(_renderer_cmd(renderer, "render", *_render_args(src, with_pages)))
        attempt.clear()
        attempt.update(res)

    attempts = [{"step": "render", "src": pptx, "with_pages": True, "result": None},
                {"step": "render-nopages", "src": pptx, "with_pages": False, "result": None}]
    if pdf_ok:
        attempts.append({"step": "render-from-pdf", "src": pdf_path, "with_pages": True, "result": None})

    rnd_json = {}
    chosen = None
    for attempt in attempts:
        holder: dict = {}
        _publish(holder, attempt["src"], attempt["with_pages"])
        attempt["result"] = holder
        commands.append(dict(holder, step=attempt["step"]))
        parsed = _stdout_json(holder["stdout"]) or {}
        for f in parsed.get("missingFonts") or []:
            if f not in missing_fonts:
                missing_fonts.append(f)
        got_png = preview_dir.is_dir() and any(preview_dir.rglob("*.png"))
        if holder["exit"] == 0 and got_png:
            chosen = attempt
            rnd_json = parsed
            break
        warnings.append("%s failed (exit=%s): %s"
                        % (attempt["step"], holder["exit"], _stderr_digest(holder, max_lines=4, max_chars=400) or "(no stderr)"))
    if chosen is None and attempts:
        chosen = attempts[-1]
        rnd_json = _stdout_json(chosen["result"]["stdout"]) or {}
    rnd = chosen["result"] if chosen else {"exit": None, "stdout": "", "stderr": "", "argv": [], "seconds": 0.0}

    # --- 3) 收集 PNG，重命名为 page-0000N.png ---
    images = rnd_json.get("images") or []
    png_paths = []
    if preview_dir.is_dir():
        png_paths = sorted((p for p in preview_dir.rglob("*.png") if p.is_file()),
                           key=lambda p: _natural_key(p.name))

    order = []
    by_basename = {p.name: p for p in png_paths}
    used = set()
    if images:
        for k, im in enumerate(images):
            page_no = im.get("page")
            try:
                page_no = int(page_no)
            except (TypeError, ValueError):
                page_no = k + 1
            ref = im.get("file") or im.get("path") or im.get("filename") or ""
            src = by_basename.get(Path(str(ref)).name)
            if src is None and k < len(png_paths):
                src = png_paths[k]
            if src is not None and src not in used:
                used.add(src)
                order.append((page_no, src, im))
    for p in png_paths:
        if p not in used:
            used.add(p)
            order.append((len(order) + 1, p, {}))
    order.sort(key=lambda t: t[0])

    pages = []
    for i, (page_no, src, im) in enumerate(order, start=1):
        target = preview_dir / ("page-%04d.png" % i)
        if src != target:
            try:
                if target.exists():
                    target.unlink()
                src.replace(target)
            except OSError as exc:
                warnings.append("cannot rename %s -> %s: %r" % (src.name, target.name, exc))
                target = src
        rec = {"page": i, "file": str(Path("preview") / target.name),
               "path": str(target)}
        rec.update(_png_info(target))
        for key in ("dpi", "width", "height"):
            if im.get(key) is not None and key not in rec:
                rec[key] = im.get(key)
            elif im.get(key) is not None and key == "dpi":
                rec[key] = im.get(key)
        if im.get("width") and not rec.get("width"):
            rec["width"] = im.get("width")
        if im.get("height") and not rec.get("height"):
            rec["height"] = im.get("height")
        pages.append(rec)

    # 渲染器自带的 manifest.json 作为旁证保留（改名，避免与我们的 render_manifest.json 混淆）
    if preview_dir.is_dir():
        for extra in sorted(preview_dir.rglob("manifest.json")):
            try:
                extra.replace(out_dir / "renderer_manifest.json")
            except OSError:
                pass
            break

    page_count = len(pages)
    ok = pdf_ok and page_count > 0
    reason = "ok"
    if not ok:
        # 两次（或三次）都失败时绝不静默返回空页：reason 带 stderr 摘要，warnings 带逐次首行
        digest = _stderr_digest(rnd) or _stderr_digest(conv)
        if not pdf_ok and conv.get("exit") not in (0, None):
            reason = "renderer-failed: %s" % (digest or "convert exit=%s" % conv.get("exit"))
        elif page_count == 0:
            reason = "renderer-failed: %s" % (digest or "renderer produced no pages")
        else:
            reason = "render-failed: %s" % (digest or "deck.pdf missing")
        if conv.get("exit") not in (0, None):
            warnings.append("convert stderr: %s" % (_stderr_digest(conv) or "(empty)"))
    if ok and expected and page_count != expected:
        warnings.append("pptx slides=%d but rendered pages=%d" % (expected, page_count))
    if ok and pdf_meta and pdf_meta["pageCount"] and pdf_meta["pageCount"] != page_count:
        warnings.append("pdf pages=%d but preview pages=%d" % (pdf_meta["pageCount"], page_count))

    manifest = dict(base_manifest)
    manifest.update({
        "ok": ok,
        "reason": reason,
        "pageCount": page_count,
        "missingFonts": missing_fonts,
        "pages": pages,
        "pdf": pdf_meta,
        "pdf_sha256": (pdf_meta or {}).get("sha256"),
        "slide_size_in": slide_size,
        "expected_pages": expected,
        "warnings": warnings,
    })
    # 顶层"文件 → sha256"登记表：给 gate F 一个不用猜结构就能解析的哈希来源
    files_sha = {}
    if source_sha:
        files_sha["deck.pptx"] = source_sha
    if pdf_meta:
        files_sha["deck.pdf"] = pdf_meta.get("sha256")
    for p in pages:
        files_sha[p["file"].replace("\\", "/")] = p.get("sha256")
    manifest["files"] = files_sha
    report = {
        "schema_version": 1,
        "ok": ok,
        "reason": reason,
        "renderer": renderer_pub,
        "env": {"inherit_os_environ": True, "total_keys": len(_child_env()),
                "repaired_missing_keys": [k for k in (_child_env().get("_RENDER_ENV_REPAIRED") or "").split(",") if k]},
        "source": {"path": str(pptx), "bytes": source_bytes, "sha256": source_sha,
                   "slides": expected, "slide_size_in": slide_size},
        "commands": [{"step": c.get("step"), "argv": c.get("argv"), "exit": c.get("exit"),
                      "seconds": c.get("seconds"), "timeout": c.get("timeout"),
                      "stdout_head": (c.get("stdout") or "")[:600],
                      "stderr_head": (c.get("stderr") or "")[:600]} for c in commands],
        "json_from_cli": {"convert": conv_json, "render": rnd_json},
        "pdf": pdf_meta,
        "preview": {"dir": "preview", "pageCount": page_count, "dpi": int(dpi),
                    "pages": [{"page": p["page"], "file": p["file"], "sha256": p.get("sha256"),
                               "width": p.get("width"), "height": p.get("height")} for p in pages]},
        "missingFonts": missing_fonts,
        "warnings": warnings,
    }
    _write_manifest(out_dir, manifest, report)

    artifacts = {"render_manifest.json": {"path": str(out_dir / _MANIFEST_NAME)},
                 "export_report.json": {"path": str(out_dir / _REPORT_NAME)}}
    if pdf_meta:
        artifacts["deck.pdf"] = pdf_meta
    for p in pages:
        artifacts[p["file"]] = {"path": p["path"], "bytes": p.get("bytes"), "sha256": p.get("sha256")}

    return {"ok": ok, "reason": reason, "pageCount": page_count, "dpi": int(dpi),
            "missingFonts": missing_fonts, "artifacts": artifacts, "warnings": warnings,
            "pdf": pdf_meta, "pages": pages, "renderer": renderer_pub}


# ------------------------------------------------------------------ CLI


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="render.py", description="render pptx to pdf + per-page png")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("render", help="convert + render a deck")
    r.add_argument("--pptx", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--dpi", type=int, default=144)
    return ap


def cli(argv) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv or []))
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        res = render_deck(Path(args.pptx), Path(args.out), dpi=int(args.dpi))
    except OSError as exc:
        sys.stderr.write("render: IO error: %r\n" % (exc,))
        return 2
    summary = {"ok": res["ok"], "reason": res["reason"], "pageCount": res["pageCount"],
               "dpi": res["dpi"], "missingFonts": res["missingFonts"],
               "pages": [p.get("file") for p in res.get("pages") or []],
               "warnings": res.get("warnings") or []}
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    if res.get("ok"):
        return 0
    if res.get("reason") == "renderer-unavailable":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
