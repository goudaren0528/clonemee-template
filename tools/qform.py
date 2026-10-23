r"""
澄清问卷生成器 —— 把一份题目清单（YAML）生成可以直接在浏览器里回答的 HTML 问卷。

界面和逻辑复用 tools/questionnaire.html（单选 / 多选 / 文本、补充说明、暂不回答、浏览器本地保存、
导出 Markdown）。产品 Agent 的 pm-clarify skill 用它向你澄清需求。
样式按视觉规范 tools/ui-tokens.css（六级字号、统一的间距圆角配色、浅色深色），生成时内嵌最新的一份；
生成后输出问卷的绝对路径和 file:/// 链接，回复你时直接用。

    python tools/qform.py plans/prd/<slug>/clarify-01.yaml          # 生成同名 .html
    python tools/qform.py <spec.yaml> -o <输出.html>

题目清单格式：
    title: 需求澄清 · <主题> · 第 1 轮
    slug: <主题>-clarify-01          # 浏览器本地保存用的键，每份问卷唯一
    reply_to: 粘贴回产品 Agent 的会话  # 导出后交给谁（显示在导出说明里）
    sections:
      - { id: goal, title: 目标与用户, desc: 这件事为什么做、给谁用, target: PRD 背景与目标, priority: true }
    questions:
      - { id: g01, s: goal, type: single, q: 主要给谁用？, opts: [我自己, 团队成员], other: true, hint: …,
          recommend: 我自己, why: 第一版先服务自己，验证了再给团队 }
      - { id: g02, s: goal, type: multi, q: …, opts: [...], recommend: [A, B], required: true }
      - { id: g03, s: goal, type: text, q: …, hint: …, recommend: 建议的写法 }
      - id: l01
        s: goal
        type: single
        q: 列表页用哪种布局？
        opts: [布局 A：卡片, 布局 B：表格]
        previews:                        # 选项预览，点选项旁的「看图」展开；三种写法任选
          "布局 A：卡片": { html: "<div class='card'>…</div>" }     # 一小段 HTML（自动带视觉规范的变量）
          "布局 B：表格": { img: shots/table.png }                  # 图片，路径相对本 YAML，生成时内嵌成 base64
      - { id: f01, s: goal, type: single, q: 流程怎么走？, opts: [先审后发, 先发后审],
          previews: { 先审后发: { mermaid: "flowchart LR; 提交-->审核-->发布" } } }

    recommend（推荐答案）+ why（理由）：选项上标「推荐」，页首「其余按推荐填」只填还没回答的题，导出时标「（按推荐）」。
    分区 priority: true 排在前面、标「必答」，可以「只看必答」；题目 required: true 也算必答；
    每个分区自动估用时（单选 20 秒、多选 30 秒、文本 1 分钟、带预览加 20 秒），分区写 minutes: 可以覆盖。
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
from pathlib import Path

import yaml

from common import ROOT, setup_stdio, ui_tokens

BASE = ROOT / "tools" / "questionnaire.html"


def preview(qid, opt, p, base_dir):
    """选项预览：{mermaid: …} / {html: …} / {img: 路径}；图片读进来内嵌成 data URI，离线也能看。"""
    if not isinstance(p, dict) or len(p) != 1 or next(iter(p)) not in ("mermaid", "html", "img"):
        sys.exit(f"题目 {qid} 选项「{opt}」的 preview 要写成 {{mermaid: …}}、{{html: …}} 或 {{img: 路径}} 之一")
    if "img" not in p:
        return {k: str(v) for k, v in p.items()}
    f = (base_dir / p["img"]).resolve()
    if not f.is_file():
        sys.exit(f"题目 {qid} 选项「{opt}」的图片不存在：{f}")
    mime = mimetypes.guess_type(f.name)[0] or "image/png"
    return {"img": f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()}


def build(spec, out_name, base_dir=Path(".")):
    html = BASE.read_text(encoding="utf-8")
    title = spec["title"]
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", str(spec.get("slug") or Path(out_name).stem))
    reply_to = spec.get("reply_to", "粘贴回会话里")
    sections = [{"id": s["id"], "title": s["title"], "target": s.get("target", ""),
                 "priority": bool(s.get("priority")), "desc": s.get("desc", "")} for s in spec["sections"]]
    for s, raw in zip(sections, spec["sections"]):
        if raw.get("minutes"):
            s["minutes"] = raw["minutes"]
    ids = {s["id"] for s in sections}
    questions = []
    for q in spec["questions"]:
        if q["s"] not in ids:
            sys.exit(f"题目 {q['id']} 的分区 {q['s']} 不存在")
        item = {k: q[k] for k in ("id", "s", "type", "q") if k in q}
        for k in ("opts", "other", "hint", "why", "required"):
            if q.get(k):
                item[k] = q[k]
        if item["type"] in ("single", "multi") and not item.get("opts"):
            sys.exit(f"题目 {q['id']} 是选择题但没有选项")
        rec = q.get("recommend")
        if rec is not None:
            recs = rec if isinstance(rec, list) else [rec]
            if item["type"] == "single" and (len(recs) != 1 or recs[0] not in item["opts"]):
                sys.exit(f"题目 {q['id']} 的 recommend「{rec}」不是它的一个选项")
            if item["type"] == "multi" and any(r not in item["opts"] for r in recs):
                sys.exit(f"题目 {q['id']} 的 recommend 里有不是选项的：{[r for r in recs if r not in item['opts']]}")
            item["rec"] = recs if item["type"] == "multi" else str(recs[0])
        if q.get("previews"):
            bad = [o for o in q["previews"] if o not in item.get("opts", [])]
            if bad:
                sys.exit(f"题目 {q['id']} 的 previews 里有不是选项的：{bad}")
            item["previews"] = {o: preview(q["id"], o, p, base_dir) for o, p in q["previews"].items()}
        questions.append(item)

    a, b = html.index("/* ui-tokens:start"), html.index("/* ui-tokens:end */")
    html = html[:a] + "/* ui-tokens:start（tools/ui-tokens.css） */\n" + ui_tokens() + "\n" + html[b:]
    lines = html.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("const SECTIONS = ["))
    end = next(i for i, l in enumerate(lines) if l.startswith("// ---------- 状态"))
    data = ["const SECTIONS = " + json.dumps(sections, ensure_ascii=False, indent=2) + ";", "",
            "const Q = " + json.dumps(questions, ensure_ascii=False, indent=2).replace("</", "<\\/") + ";", ""]
    html = "\n".join(lines[:start] + data + lines[end:])

    def sub(old, new):
        nonlocal html
        assert old in html, old[:40]
        html = html.replace(old, new)
    sub("<title>个人偏好问卷</title>", f"<title>{title}</title>")
    sub("<h1>个人偏好问卷</h1>", f"<h1>{title}</h1>")
    sub('const STORE_KEY = "questionnaire-answers";', f'const KEY = "assistant-qform-{slug}";')
    sub('"> 来自 tools/questionnaire.html"', f'"> 来自 {out_name}"')
    sub('"> 请按 playbooks/profile-questionnaire.md 并入。"', f'"> {reply_to}。"')
    html = re.sub(r'<div class="hint" style="margin-top:8px">复制后粘贴到.*?</div>',
                  f'<div class="hint" style="margin-top:8px">复制后{reply_to}。</div>', html, count=1)
    return html, len(questions)


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="澄清问卷生成器")
    ap.add_argument("spec")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()
    spec_path = Path(args.spec)
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else spec_path.with_suffix(".html")
    html, n = build(spec, out.resolve().as_posix(), spec_path.parent)  # 导出说明里写问卷的绝对路径
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out.resolve()}：{len(spec['sections'])} 个分区、{n} 道题")
    print(f"浏览器打开：{out.resolve().as_uri()}")


if __name__ == "__main__":
    main()
