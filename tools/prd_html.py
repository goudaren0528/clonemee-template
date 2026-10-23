r"""
PRD 评审页生成器 —— 把 PRD（Markdown）和原型（HTML）合成一个图文并茂、可以评论的评审页。

    python tools/prd_html.py plans/prd/<slug>/prd.md --prototype plans/prd/<slug>/prototype/*.html
    python tools/prd_html.py <prd.md> --prototype a.html b.html -o review.html --title "…"

PRD 里能用的写法（产品 Agent 的 pm-prd skill 有完整说明，示例见 tools/samples/prd-demo/）：
- 流程图：```mermaid 代码块（流程图、时序图、状态图、用户旅程），上一行写「图：xxx」作图题
- 界面说明：```screen <原型文件名> 代码块，每行「编号 | 名称 | 说明 | 对应 AC」；原型里的元素加 data-note="编号"
- 关键状态：原型 <body data-states="默认,空状态,出错">，并提供 window.setState(名称)
- 提示块：> [!决策] / > [!风险] / > [!注意]
- 待确认：> [!待确认] q01 问题，下面每个选项一行「- 选项：理由」，推荐的标「（推荐）」；评审页上可以直接选，页首汇成清单
- 验收标准：「验收标准」一节逐条写 - **AC-n** …；正文里的 AC-n 自动变成可点的标签

页面功能：每节、每张图、每屏有「通过 / 有疑问 / 要改」和评论（能改能删）；说明行、原型里的任意元素也能评论；
待确认每条是单选（推荐项带理由、按推荐、其余按推荐、我自己写）；页首有概览卡、待确认清单、评审进度和「只看没过的」；
评审存在浏览器本地，没保存的评论存成草稿，页面被重新加载也不丢；
「导出评审」生成 Markdown（先结论汇总表，再待确认的选择，再逐条意见，带小节 / 图 / 屏 / 编号定位），粘贴回产品 Agent 的会话。
外部脚本只用 cdnjs 的 marked 和 mermaid（固定版本）；断网时 PRD 用简易渲染、图显示源码。
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import re
from pathlib import Path

from common import setup_stdio, ui_tokens

MERMAID_JS = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<style>
__TOKENS__
header { position: sticky; top: 0; z-index: 10; background: var(--bg); border-bottom: 1px solid var(--line); }
.bar { max-width: 2400px; margin: 0 auto; padding: var(--sp-12) var(--sp-16); display: flex; gap: var(--sp-12); align-items: center; flex-wrap: wrap; }
.bar h1 { font-size: var(--fs-20); margin: 0; flex: 1 1 260px; min-width: 0; }
.prog { display: flex; align-items: center; gap: var(--sp-8); color: var(--muted); font-size: var(--fs-13); flex: 1 1 280px; }
.track { flex: 0 0 96px; height: 6px; background: var(--panel-2); border-radius: var(--r-pill); overflow: hidden; border: 1px solid var(--line); }
.fill { height: 100%; width: 0; background: var(--brand); transition: width .2s; }
.n-pass { color: var(--brand); } .n-ask { color: var(--orange); } .n-fix { color: var(--red); }
button { font: inherit; font-size: var(--fs-14); cursor: pointer; border-radius: var(--r-sm); border: 1px solid var(--line); background: var(--panel); color: var(--text); padding: var(--sp-4) var(--sp-12); }
button:hover { border-color: var(--brand); } button.primary { background: var(--brand); color: var(--brand-text); border-color: var(--brand); }
button.on { background: var(--orange-soft); border-color: var(--orange); }
.layout { max-width: 2400px; margin: 0 auto; padding: var(--sp-16); display: grid; grid-template-columns: 220px 1fr; gap: var(--sp-24); }
.layout > * { min-width: 0; }
nav { position: sticky; top: 72px; align-self: start; font-size: var(--fs-13); max-height: calc(100vh - 88px); overflow: auto; }
nav a { display: flex; gap: var(--sp-8); align-items: baseline; padding: var(--sp-4) var(--sp-8); border-radius: var(--r-sm); color: var(--text); text-decoration: none; }
nav a:hover { background: var(--panel-2); } nav a.sub { padding-left: var(--sp-24); color: var(--muted); }
nav a .lb { flex: 1; min-width: 0; } nav .badge { color: var(--orange); font-size: var(--fs-12); font-weight: 600; }
.dot { flex: none; width: 8px; height: 8px; border-radius: var(--r-pill); border: 1px solid var(--faint); align-self: center; }
.dot.v-pass { background: var(--brand); border-color: var(--brand); } .dot.v-ask { background: var(--orange); border-color: var(--orange); } .dot.v-fix { background: var(--red); border-color: var(--red); }
.cardx { background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-16); margin-bottom: var(--sp-16); box-shadow: var(--shadow); }
.cardx h2 { font-size: var(--fs-16); margin: 0 0 var(--sp-8); }
.ov { display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-12) var(--sp-24); }
.ov .goal { grid-column: 1 / -1; font-size: var(--fs-16); font-weight: 600; }
.ov .wide { grid-column: 1 / -1; }
.lbl { display: block; font-size: var(--fs-12); color: var(--muted); font-weight: 400; margin-bottom: var(--sp-4); }
.ov ul { margin: 0; padding-left: 20px; } .ov .do li::marker { color: var(--brand); } .ov .dont li::marker { color: var(--red); }
.pending ol { margin: 0; padding-left: 20px; } .pending li { margin-bottom: var(--sp-4); } .pending .where { color: var(--muted); font-size: var(--fs-12); margin-left: var(--sp-8); }
.usage { color: var(--muted); font-size: var(--fs-13); margin: 0 0 var(--sp-16); }
.doc { background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-8) var(--sp-24) var(--sp-24); overflow-wrap: anywhere; }
.doc h1 { margin: var(--sp-16) 0; } .doc h2 { margin: var(--sp-24) 0 var(--sp-12); border-top: 1px solid var(--line); padding-top: var(--sp-16); }
.doc h3 { margin: var(--sp-16) 0 var(--sp-8); } .doc [id] { scroll-margin-top: 88px; }
.doc table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; font-size: var(--fs-13); }
.doc th, .doc td { border: 1px solid var(--line); padding: var(--sp-4) var(--sp-8); vertical-align: top; } .doc th { background: var(--panel-2); text-align: left; }
.doc pre { background: var(--panel-2); padding: var(--sp-12); border-radius: var(--r-sm); overflow-x: auto; }
.doc blockquote { margin: var(--sp-12) 0; padding: var(--sp-8) var(--sp-12); border-left: 3px solid var(--line); color: var(--muted); }
.doc blockquote.callout { border: 1px solid var(--line); border-left-width: 4px; border-radius: var(--r-sm); color: var(--text); }
.callout > :nth-child(2) { margin-top: 0; } .callout > :last-child { margin-bottom: 0; }
.callout .ct { display: block; font-size: var(--fs-12); font-weight: 600; margin-bottom: var(--sp-4); }
.doc blockquote.k-decision { background: var(--brand-soft); border-color: var(--brand); } .k-decision .ct { color: var(--brand); }
.doc blockquote.k-confirm { background: var(--orange-soft); border-color: var(--orange); } .k-confirm .ct { color: var(--orange); }
.doc blockquote.k-risk { background: var(--red-soft); border-color: var(--red); } .k-risk .ct { color: var(--red); }
.doc blockquote.k-note { background: var(--blue-soft); border-color: var(--blue); } .k-note .ct { color: var(--blue); }
.vd { display: inline-flex; gap: var(--sp-4); flex-wrap: wrap; vertical-align: 3px; margin-left: var(--sp-8); font-weight: 400; }
.blk-head button, .tools button { font-size: var(--fs-12); padding: 0 var(--sp-8); border-radius: var(--r-pill); color: var(--muted); line-height: 1.8; }
.vd button { font-size: var(--fs-13); padding: 0 var(--sp-12); border-radius: var(--r-pill); color: var(--text); line-height: 1.9; font-weight: 400; }
.vd button[data-v="pass"] { border-color: var(--brand); } .vd button[data-v="ask"] { border-color: var(--orange); } .vd button[data-v="fix"] { border-color: var(--red); }
.vd button[data-v].on { color: var(--panel); font-weight: 600; }
.vd button[data-v="pass"].on { background: var(--brand); color: var(--brand-text); }
.vd button[data-v="ask"].on { background: var(--orange); } .vd button[data-v="fix"].on { background: var(--red); }
.vd button[data-c] { color: var(--muted); }
.thread { margin: var(--sp-8) 0 var(--sp-12); padding: var(--sp-8) var(--sp-12); border-left: 3px solid var(--orange); background: var(--orange-soft); border-radius: 0 var(--r-sm) var(--r-sm) 0; font-size: var(--fs-14); }
.thread .c { margin: 0 0 var(--sp-4); } .thread .meta { color: var(--muted); font-size: var(--fs-12); }
.thread .meta button { font-size: var(--fs-12); padding: 0 var(--sp-4); margin-left: var(--sp-4); }
textarea { width: 100%; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: var(--r-sm); padding: var(--sp-8); }
.row { display: flex; gap: var(--sp-8); margin-top: var(--sp-8); flex-wrap: wrap; align-items: center; }
.blk { background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-12); margin: var(--sp-16) 0; }
.blk-head { display: flex; gap: var(--sp-8); align-items: center; flex-wrap: wrap; margin-bottom: var(--sp-8); }
.blk-t { font-weight: 600; flex: 1 1 200px; min-width: 0; }
.blk-head .vd { margin-left: 0; vertical-align: 0; }
.kind { display: inline-block; font-size: var(--fs-12); font-weight: 600; padding: 0 var(--sp-8); border-radius: var(--r-pill); background: var(--violet-soft); color: var(--violet); margin-right: var(--sp-8); }
.fig-body { overflow: auto; text-align: center; padding: var(--sp-8) 0; }
.fig-body svg { max-width: 100%; height: auto; }
.fig-body pre { text-align: left; }
.tools { display: flex; gap: var(--sp-8); align-items: center; flex-wrap: wrap; margin-bottom: var(--sp-8); font-size: var(--fs-13); color: var(--muted); }
.tools .states { display: inline-flex; gap: var(--sp-4); flex-wrap: wrap; align-items: center; }
.tools .states button.on { background: var(--blue-soft); border-color: var(--blue); color: var(--blue); }
.tools .sp { flex: 1; }
.scr-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(240px, 1fr); gap: var(--sp-12); align-items: start; }
.scr-grid.solo { grid-template-columns: minmax(0, 1fr); }
.stage { min-width: 0; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--bg); overflow: auto; position: sticky; top: 80px; max-height: calc(100vh - 96px); }
.zoom .stage { position: static; max-height: none; }
.vp { position: relative; overflow: hidden; }
.vp iframe { border: 0; display: block; transform-origin: 0 0; background: var(--panel); }
.notes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-8); font-size: var(--fs-13); }
.note { border: 1px solid var(--line); border-radius: var(--r-sm); padding: var(--sp-8) var(--sp-12); background: var(--panel); transition: background .15s, border-color .15s; }
.note.hl { border-color: var(--orange); background: var(--orange-soft); }
.note-h { display: flex; gap: var(--sp-8); align-items: center; }
.note-h .nm { flex: 1; min-width: 0; font-weight: 600; font-size: var(--fs-14); }
.note-h .vd { margin-left: 0; vertical-align: 0; }
.no { flex: none; display: inline-grid; place-items: center; min-width: 20px; height: 20px; padding: 0 var(--sp-4); border-radius: var(--r-pill); background: var(--brand); color: var(--brand-text); font-size: var(--fs-12); font-weight: 600; cursor: pointer; }
.note .d { margin-top: var(--sp-4); } .note .acs { margin-top: var(--sp-4); }
.ac { display: inline-block; font-size: var(--fs-12); font-family: var(--mono); padding: 0 var(--sp-8); border-radius: var(--r-pill); background: var(--blue-soft); color: var(--blue); text-decoration: none; line-height: 1.7; white-space: nowrap; }
.ac.undef { background: var(--gray-soft); color: var(--muted); }
.where { display: block; color: var(--muted); font-size: var(--fs-12); margin-top: var(--sp-4); }
.where a { color: var(--brand); margin-right: var(--sp-8); }
.where.none { color: var(--orange); }
.flash { animation: flash 1.4s ease-out; } @keyframes flash { 0%, 40% { background: var(--orange-soft); } 100% { background: transparent; } }
.zoom { position: fixed !important; inset: 0; z-index: 40; margin: 0 !important; border-radius: 0 !important; overflow: auto; padding: var(--sp-16) !important; background: var(--bg) !important; }
.fig.zoom .fig-body svg { max-width: none !important; width: 100%; }
body.noscroll { overflow: hidden; }
section.proto > h2 { margin: var(--sp-32) 0 var(--sp-12); }
.hint { color: var(--muted); font-size: var(--fs-13); }
.only-open .sec.v-pass > :not(.fig):not(.screen):not(.pend) { display: none; }
.only-open .pend.answered { display: none; }
.pend { border: 1px solid var(--orange); border-left-width: 4px; background: var(--panel); }
.kind.k-q { background: var(--orange-soft); color: var(--orange); }
.st { flex: none; font-size: var(--fs-12); font-weight: 600; padding: 0 var(--sp-8); border-radius: var(--r-pill); line-height: 1.8; }
.st.todo { background: var(--orange-soft); color: var(--orange); } .st.done { background: var(--brand-soft); color: var(--brand); }
.pend-body > :first-child { margin-top: 0; } .pend-body > :last-child { margin-bottom: 0; } .pend-body { color: var(--muted); font-size: var(--fs-13); }
.opts { list-style: none; margin: var(--sp-12) 0 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-8); }
.opt { display: flex; gap: var(--sp-8); align-items: flex-start; border: 1px solid var(--line); border-radius: var(--r-sm); padding: var(--sp-8) var(--sp-12); cursor: pointer; background: var(--panel); }
.opt:hover { border-color: var(--orange); } .opt.sel { border-color: var(--brand); background: var(--brand-soft); }
.opt input { flex: none; width: 16px; height: 16px; margin: 3px 0 0; accent-color: var(--brand); cursor: pointer; }
.opt .ob { flex: 1; min-width: 0; } .opt .ol { font-weight: 600; } .opt .why { color: var(--muted); font-size: var(--fs-13); margin-top: var(--sp-4); }
.opt .ab { color: var(--muted); font-weight: 600; margin-right: var(--sp-4); }
.rec { display: inline-block; font-size: var(--fs-12); font-weight: 600; padding: 0 var(--sp-8); border-radius: var(--r-pill); background: var(--brand); color: var(--brand-text); margin-left: var(--sp-8); line-height: 1.7; vertical-align: 1px; }
.opt textarea { margin-top: var(--sp-8); }
.pending ol li { display: flex; gap: var(--sp-8); align-items: baseline; flex-wrap: wrap; } .pending ol li a { flex: 1 1 240px; min-width: 0; }
.only-open .blk.v-pass, .only-open nav a.v-pass { display: none; }
dialog { width: min(760px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: var(--r-md); background: var(--panel); color: var(--text); padding: var(--sp-16); }
dialog h3 { margin: 0 0 var(--sp-8); }
dialog::backdrop { background: rgba(0,0,0,.4); } dialog textarea.out { height: 50vh; font-family: var(--mono); font-size: var(--fs-13); }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; gap: var(--sp-12); padding: var(--sp-12); }
  nav { position: static; max-height: 220px; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--panel); padding: var(--sp-4); }
  .doc { padding: var(--sp-8) var(--sp-12) var(--sp-16); }
  .ov { grid-template-columns: 1fr; }
  .scr-grid { grid-template-columns: minmax(0, 1fr); }
  .stage { position: static; max-height: none; }
  .prog { flex-basis: 100%; flex-wrap: wrap; }
}
</style>
</head>
<body>
<header><div class="bar">
  <h1>__TITLE__</h1>
  <div class="prog"><div class="track"><div class="fill" id="fill"></div></div><span id="ptext"></span></div>
  <button id="btnOpen" title="隐藏已经点了「通过」的小节、图和屏">只看没过的</button>
  <button class="primary" id="btnExport">导出评审</button><button id="btnTheme">主题</button>
</div></header>
<div class="layout">
  <nav id="nav"></nav>
  <main>
    <div id="top"></div>
    <p class="usage">每节、每张图、每屏的标题旁有「通过 / 有疑问 / 要改」，点了就算看过，再点一次取消、点别的就改；评论能改能删。「待确认」每条直接选一个答案（推荐的标了理由），不同意就选别的或自己写。说明行和原型里的元素也能评论（点「点选元素评论」再点原型）。看完点「导出评审」。</p>
    <article class="doc" id="doc"></article>
    <section class="proto" id="proto"></section>
  </main>
</div>
<dialog id="dlg">
  <h3>导出评审（Markdown）</h3>
  <textarea class="out" id="out" readonly></textarea>
  <div class="row"><button class="primary" id="btnCopy">复制</button><button id="btnDl">下载 .md</button>
    <span style="flex:1"></span><button id="btnClear">清空全部评审</button><button id="btnClose">关闭</button></div>
  <div class="hint">复制后__REPLY__。</div>
</dialog>
<script type="text/plain" id="prd-src">__PRD__</script>
<script>
const SLUG = __SLUG__, SCREENS = __SCREENS__, FIGS = __FIGS__, BLOCKS = __BLOCKS__, PENDS = __PENDS__, OVERVIEW = __OVERVIEW__, SOURCE = __SOURCE__;
const MERMAID_JS = __MERMAID__;
const KEY = "assistant-prd-" + SLUG;
const VERDICTS = { pass: "通过", ask: "有疑问", fix: "要改" };
let state = { comments: [], verdicts: {}, answers: {}, drafts: {}, theme: "", onlyOpen: false };
try { const raw = localStorage.getItem(KEY); if (raw) state = Object.assign(state, JSON.parse(raw)); } catch (e) {}
state.verdicts = state.verdicts || {}; state.answers = state.answers || {}; state.drafts = state.drafts || {};
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const inline = s => window.marked ? marked.parseInline(String(s || "")) : esc(s || "");
const when = iso => { const d = new Date(iso), p = n => String(n).padStart(2, "0"); return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };  // 本地时间
const flash = el => { if (!el) return; el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); };

// ---------- 评审对象：小节、图、屏、说明行 ----------
const targets = [], byKey = {}, used = {};
function addTarget(base, kind, title, extra) {
  used[base] = (used[base] || 0) + 1;
  const key = used[base] > 1 ? base + "#" + used[base] : base;
  const t = Object.assign({ key, kind, title, dom: "t" + targets.length, verdictable: kind !== "note" }, extra);
  targets.push(t); byKey[key] = t; return t;
}
const vd = (key, full) => `<span class="vd" data-k="${esc(key)}">` +
  (full ? Object.entries(VERDICTS).map(([v, l]) => `<button type="button" data-v="${v}">${l}</button>`).join("") : "") +
  `<button type="button" data-c>评论</button></span>`;

// ---------- PRD 渲染 ----------
function fallbackMd(md) {  // 离线时的简易渲染：标题、列表、引用、代码块、段落；占位块原样保留
  const out = []; let code = false, list = false, quote = false;
  const close = () => { if (list) { out.push("</ul>"); list = false; } if (quote) { out.push("</blockquote>"); quote = false; } };
  for (const line of md.split("\n")) {
    if (line.startsWith("```")) { close(); out.push(code ? "</code></pre>" : "<pre><code>"); code = !code; continue; }
    if (code) { out.push(esc(line)); continue; }
    if (line.startsWith('<div class="nz-slot"')) { close(); out.push(line); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/), li = line.match(/^\s*[-*]\s+(.*)$/), q = line.match(/^>\s?(.*)$/);
    if (list && !li) { out.push("</ul>"); list = false; }
    if (quote && !q) { out.push("</blockquote>"); quote = false; }
    if (h) out.push(`<h${h[1].length}>${esc(h[2])}</h${h[1].length}>`);
    else if (q) { if (!quote) { out.push("<blockquote>"); quote = true; } if (q[1].trim()) out.push(`<p>${esc(q[1])}</p>`); }
    else if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${esc(li[1])}</li>`); }
    else if (line.trim()) out.push(`<p>${esc(line)}</p>`);
  }
  close();
  return out.join("\n");
}
const md = $("prd-src").textContent.replace(/<\\\/script/g, "</script");
const doc = $("doc");
doc.innerHTML = window.marked ? marked.parse(md) : fallbackMd(md);

// 提示块：> [!决策] / [!待确认] / [!风险] / [!注意]（也认 GitHub 的 NOTE / IMPORTANT / WARNING 等）
const CALLOUT = { "决策": "decision", "待确认": "confirm", "风险": "risk", "注意": "note",
  IMPORTANT: "decision", WARNING: "risk", CAUTION: "risk", NOTE: "note", TIP: "note" };
const CALLOUT_NAME = { decision: "决策", confirm: "待确认", risk: "风险", note: "注意" };
doc.querySelectorAll("blockquote").forEach(bq => {
  const p = bq.firstElementChild; if (!p) return;
  const m = p.innerHTML.match(/^\s*\[!(决策|待确认|风险|注意|IMPORTANT|WARNING|CAUTION|NOTE|TIP)\]\s*(?:<br>)?\s*/i);
  if (!m) return;
  const k = CALLOUT[m[1]] || CALLOUT[m[1].toUpperCase()];
  p.innerHTML = p.innerHTML.slice(m[0].length);
  if (!p.textContent.trim() && !p.querySelector("img")) p.remove();
  bq.classList.add("callout", "k-" + k);
  bq.insertAdjacentHTML("afterbegin", `<span class="ct">${CALLOUT_NAME[k]}</span>`);
});

// 待确认：每条一张卡片，选项可以直接选（推荐的标出来、带理由），也能「按推荐」或自己写
const pendsRT = [], LETTER = i => String.fromCharCode(65 + i);
doc.querySelectorAll(".nz-slot[data-pend]").forEach(slot => {
  const i = +slot.dataset.pend, q = PENDS[i], no = q.id || "Q" + (i + 1);
  const d = document.createElement("div"); d.className = "pend blk";
  const t = addTarget("q:" + q.title, "pend", `待确认「${q.title}」`, { node: d, pend: q, no, verdictable: false });
  d.id = t.dom;
  const name = "pq" + i, recI = q.options.findIndex(o => o.rec);
  const opt = (o, j) => `<li><label class="opt" data-j="${j}"><input type="radio" name="${name}" value="${j}"><span class="ob">` +
    `<span class="ol"><span class="ab">${LETTER(j)}</span>${inline(o.label)}${o.rec ? '<span class="rec">推荐</span>' : ""}</span>` +
    (o.desc ? `<div class="why">${o.rec ? "推荐理由：" : ""}${inline(o.desc)}</div>` : "") + `</span></label></li>`;
  d.innerHTML = `<div class="blk-head"><span class="blk-t"><span class="kind k-q">待确认</span>${esc(no)} ${inline(q.title)}</span><span class="st"></span>${vd(t.key, false)}</div>
    ${q.body ? `<div class="pend-body">${window.marked ? marked.parse(q.body) : fallbackMd(q.body)}</div>` : ""}
    <ul class="opts">${q.options.map(opt).join("")}
      <li><label class="opt" data-j="other"><input type="radio" name="${name}" value="other"><span class="ob"><span class="ol"><span class="ab">${LETTER(q.options.length)}</span>都不是，我自己写</span>
        <textarea rows="2" data-other placeholder="写下你的答案…" hidden></textarea></span></label></li></ul>
    <div class="row">${recI >= 0 ? '<button type="button" data-rec>按推荐</button>' : ""}<button type="button" data-unpick>清除选择</button>
      ${q.structured ? "" : '<span class="hint">这条 PRD 里没写选项，上面是按正文整理的推荐和备选</span>'}</div><div class="th"></div>`;
  t.thread = d.querySelector(":scope > .th");
  pendsRT.push({ t, q, recI, node: d });
  slot.replaceWith(d);
});
function pickLabel(p, a) {  // 导出和清单里显示的选择
  if (!a) return "";
  if (a.pick === "other") return "自己写：" + (a.other || "（还没写）");
  const o = p.q.options[a.pick]; if (!o) return "";
  return o.rec ? `${LETTER(a.pick)}，按推荐` : `${LETTER(a.pick)}. ${o.label}` + (p.recI >= 0 ? "（没按推荐）" : "");
}

// 图：```mermaid（上一行「图：xxx」作图题）
doc.querySelectorAll(".nz-slot[data-fig]").forEach(slot => {
  const i = +slot.dataset.fig, f = FIGS[i], cap = f.caption || "图 " + (i + 1);
  const d = document.createElement("div"); d.className = "fig blk"; d.dataset.fi = i;
  const t = addTarget("f:" + cap, "fig", `图「${cap}」`, { node: d, nav: "图 · " + cap });
  d.id = t.dom;
  d.innerHTML = `<div class="blk-head"><span class="blk-t"><span class="kind">图</span>${esc(cap)}</span>${vd(t.key, true)}<button type="button" data-zoom>放大</button></div>
    <div class="fig-body"><div class="hint">图加载中…</div></div><div class="th"></div>`;
  t.thread = d.querySelector(".th");
  slot.replaceWith(d);
});

// 界面说明：```screen <原型>（左边原型、右边编号说明）
const screensRT = [];
function screenEl(si, block) {
  const sc = SCREENS[si], name = sc ? sc.name : (block ? block.ref : "?");
  const d = document.createElement("div"); d.className = "screen blk";
  const t = addTarget("p:" + (sc ? sc.file : name), "screen", `界面「${name}」`, { node: d, nav: "界面 · " + name });
  d.id = t.dom;
  const rows = block ? block.rows : [];
  d.innerHTML = `<div class="blk-head"><span class="blk-t"><span class="kind">界面</span>${esc(name)}</span>${vd(t.key, true)}${sc ? '<button type="button" data-zoom>放大</button>' : ""}</div>
    ${sc ? `<div class="tools"><span class="states"></span><span class="sp"></span><button type="button" data-pick>点选元素评论</button></div>` : `<div class="hint">没找到原型「${esc(name)}」，只显示说明。</div>`}
    <div class="scr-grid${rows.length && sc ? "" : " solo"}">
      ${sc ? `<div class="stage"><div class="vp"><iframe sandbox="allow-same-origin allow-scripts allow-forms" height="600" title="${esc(name)}"></iframe></div></div>` : ""}
      ${rows.length ? '<ol class="notes"></ol>' : ""}
    </div><div class="th"></div>`;
  t.thread = d.querySelector(":scope > .th");
  const notesEl = d.querySelector(".notes");
  for (const r of rows) {
    const li = document.createElement("li"); li.className = "note"; li.dataset.no = r.no;
    const nt = addTarget(`n:${sc ? sc.file : name}:${r.no}`, "note", `界面「${name}」· 编号 ${r.no}「${r.name}」`, { node: li, parent: t.key, no: r.no, acs: r.acs, screen: name });
    li.id = nt.dom;
    li.innerHTML = `<div class="note-h"><span class="no" data-no="${esc(r.no)}">${esc(r.no)}</span><span class="nm">${inline(r.name)}</span>${vd(nt.key, false)}</div>` +
      (r.desc ? `<div class="d">${inline(r.desc)}</div>` : "") + (r.acs.length ? `<div class="acs">${r.acs.join(" ")}</div>` : "") + `<div class="th"></div>`;
    nt.thread = li.querySelector(".th");
    notesEl.appendChild(li);
  }
  if (sc) {
    const scr = { t, sc, frame: d.querySelector("iframe"), stage: d.querySelector(".stage"), vp: d.querySelector(".vp"), statesEl: d.querySelector(".states"), notesEl, W: 1280, s: 1, hl: null, cur: "" };
    t.rt = scr; screensRT.push(scr);
    scr.frame.addEventListener("load", () => wireFrame(scr));
    scr.frame.srcdoc = sc.html;
    if (notesEl) {
      notesEl.addEventListener("mouseover", e => { const li = e.target.closest(".note"); hl(scr, li ? li.dataset.no : null); });
      notesEl.addEventListener("mouseleave", () => hl(scr, null));
    }
  }
  return d;
}
const shown = new Set();
doc.querySelectorAll(".nz-slot[data-scr]").forEach(slot => {
  const b = BLOCKS[+slot.dataset.scr]; if (b.screen >= 0) shown.add(b.screen);
  slot.replaceWith(screenEl(b.screen, b));
});
const rest = SCREENS.map((_, i) => i).filter(i => !shown.has(i));
if (rest.length) {
  $("proto").insertAdjacentHTML("beforeend", `<h2>${BLOCKS.length ? `其他界面（还没写说明的 ${rest.length} 屏）` : `原型（${rest.length} 屏）`}</h2>`);
  rest.forEach(i => $("proto").appendChild(screenEl(i, null)));
}

// 按 h2 / h3 切小节，挂「通过 / 有疑问 / 要改」和评论
let cur = null;
for (const n of [...doc.children]) {
  if (/^H[23]$/.test(n.tagName)) { cur = document.createElement("div"); cur.className = "sec"; doc.insertBefore(cur, n); }
  if (cur) cur.appendChild(n);
}
doc.querySelectorAll(".sec").forEach(sec => {
  const h = sec.firstElementChild, title = h.textContent.trim(), sub = h.tagName === "H3";
  const t = addTarget("s:" + title, "sec", `小节「${title}」`, { node: sec, heading: h, nav: title, sub });
  sec.dataset.title = title; h.id = t.dom;
  h.insertAdjacentHTML("beforeend", vd(t.key, true));
  const th = document.createElement("div"); th.className = "th"; h.after(th); t.thread = th;
});
targets.sort((a, b) => (a.node.compareDocumentPosition(b.node) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);

// ---------- 验收标准联动 ----------
const acDefs = {};
let inAc = false;
doc.querySelectorAll(".sec").forEach(sec => {
  const h = sec.firstElementChild;
  if (h.tagName === "H2") inAc = !Object.keys(acDefs).length && /验收/.test(sec.dataset.title) && !/对照/.test(sec.dataset.title);
  if (!inAc) return;
  sec.querySelectorAll("li, tr").forEach(el => {
    const m = el.textContent.trim().match(/^AC-(\d+)\b/);
    if (m && !acDefs[m[1]]) { acDefs[m[1]] = el; el.id = "ac-" + m[1]; }
  });
});
function linkifyAC(root) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: n =>
    /AC-\d/.test(n.nodeValue) && !n.parentElement.closest("pre, code, a, button, textarea, .thread") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
  const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
  for (const n of nodes) {
    const s = n.nodeValue, frag = document.createDocumentFragment(); let last = 0;
    s.replace(/\bAC-(\d+)\b/g, (m, num, off) => {
      frag.append(s.slice(last, off));
      const a = document.createElement("a"); a.className = "ac" + (acDefs[num] ? "" : " undef"); a.textContent = m; a.dataset.ac = num;
      if (acDefs[num]) a.href = "#ac-" + num; else a.title = "验收标准里没有这一条";
      frag.append(a); last = off + m.length; return m;
    });
    frag.append(s.slice(last)); n.replaceWith(frag);
  }
}
linkifyAC(doc);
const noteTargets = targets.filter(t => t.kind === "note");
for (const [num, el] of Object.entries(acDefs)) {
  const hits = noteTargets.filter(t => t.acs.includes("AC-" + num));
  const w = document.createElement("span"); w.className = "where";
  if (hits.length) w.innerHTML = "出现在：" + hits.map(t => `<a href="#${t.dom}" data-jump="${t.dom}">${esc(t.screen)} #${esc(t.no)}</a>`).join("");
  else if (noteTargets.length && !el.textContent.includes("非界面")) { w.classList.add("none"); w.textContent = "没有出现在任何屏的说明里"; }
  if (w.innerHTML) (el.tagName === "TR" ? el.lastElementChild : el).appendChild(w);
}

// ---------- 页首：概览卡、待确认清单 ----------
(function top() {
  const o = OVERVIEW, parts = [];
  const list = (xs, cls) => `<ul class="${cls}">${xs.map(x => `<li>${inline(x)}</li>`).join("")}</ul>`;
  if (o.goal) parts.push(`<div class="goal"><span class="lbl">目标</span>${inline(o.goal)}</div>`);
  if (o.success) parts.push(`<div class="wide"><span class="lbl">成功标准</span>${list(o.success, "")}</div>`);
  if (o.do) parts.push(`<div><span class="lbl">做</span>${list(o.do, "do")}</div>`);
  if (o.dont) parts.push(`<div><span class="lbl">不做</span>${list(o.dont, "dont")}</div>`);
  let h = parts.length ? `<div class="cardx"><h2>一页看懂</h2><div class="ov">${parts.join("")}</div></div>` : "";
  if (pendsRT.length) h += `<div class="cardx pending"><div class="blk-head"><h2 class="blk-t" style="margin:0">待确认（${pendsRT.length}）<span class="hint" id="pendCount"></span></h2>` +
    (pendsRT.some(p => p.recI >= 0) ? '<button type="button" id="btnRecAll" title="只填还没选的；填完还能改">其余按推荐</button>' : "") + `</div>
    <p class="hint" style="margin:0 0 var(--sp-8)">每条都能直接选：推荐的标了「推荐」和理由；不同意就选别的，或者选「我自己写」。选择会跟评审一起导出。</p><ol>` +
    pendsRT.map(p => { const sec = p.node.closest(".sec");
      return `<li><a href="#${p.t.dom}" data-jump="${p.t.dom}">${esc(p.t.no)} ${esc(p.q.title)}</a>` +
        `${sec ? `<span class="where" style="display:inline">${esc(sec.dataset.title)}</span>` : ""}<span class="st" data-pst="${p.t.dom}"></span></li>`; }).join("") + "</ol></div>";
  $("top").innerHTML = h;
})();

// ---------- 流程图（mermaid，加载失败显示源码） ----------
let mermaidP = null, seq = 0;
function loadMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (!mermaidP) mermaidP = new Promise((ok, no) => {
    const s = document.createElement("script"); s.src = MERMAID_JS;
    s.onload = () => window.mermaid ? ok(window.mermaid) : no(new Error("没加载到"));
    s.onerror = () => no(new Error("加载失败")); document.head.appendChild(s);
    setTimeout(() => no(new Error("加载超时")), 12000);
  });
  return mermaidP;
}
function isDark() { return state.theme ? state.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
function mermaidConf() {
  const cs = getComputedStyle(document.documentElement), v = n => cs.getPropertyValue(n).trim();
  return { startOnLoad: false, securityLevel: "strict", theme: "base", themeVariables: {
    darkMode: isDark(), background: v("--panel"), fontFamily: v("--font"), fontSize: "14px",
    primaryColor: v("--brand-soft"), primaryTextColor: v("--text"), primaryBorderColor: v("--brand"), lineColor: v("--muted"),
    secondaryColor: v("--blue-soft"), secondaryBorderColor: v("--blue"), secondaryTextColor: v("--text"),
    tertiaryColor: v("--panel-2"), tertiaryBorderColor: v("--line"), tertiaryTextColor: v("--text"), textColor: v("--text"),
    mainBkg: v("--brand-soft"), nodeBorder: v("--brand"), clusterBkg: v("--panel-2"), clusterBorder: v("--line"), titleColor: v("--text"),
    edgeLabelBackground: v("--panel"), noteBkgColor: v("--orange-soft"), noteTextColor: v("--text"), noteBorderColor: v("--orange"),
    actorBkg: v("--brand-soft"), actorBorder: v("--brand"), actorTextColor: v("--text"), actorLineColor: v("--muted"),
    signalColor: v("--text"), signalTextColor: v("--text"), labelBoxBkgColor: v("--panel-2"), labelBoxBorderColor: v("--line"),
    labelTextColor: v("--text"), loopTextColor: v("--text"), activationBkgColor: v("--blue-soft"), activationBorderColor: v("--blue"),
    labelColor: v("--text"), altBackground: v("--panel-2") } };
}
const srcBox = (src, why) => `<div class="hint" style="text-align:left">${esc(why)}，下面是源码：</div><pre><code>${esc(src)}</code></pre>`;
async function drawFigs() {
  const figs = [...document.querySelectorAll(".fig[data-fi]")]; if (!figs.length) return;
  let m = null, err = "";
  try { m = await loadMermaid(); m.initialize(mermaidConf()); } catch (e) { err = e.message; }
  for (const f of figs) {
    const body = f.querySelector(".fig-body"), src = FIGS[+f.dataset.fi].src;
    if (!m) { body.innerHTML = srcBox(src, "图没加载出来（" + err + "，可能断网了）"); continue; }
    const id = "nzm" + (++seq);
    try { body.innerHTML = (await m.render(id, src)).svg; }
    catch (e) { document.getElementById("d" + id)?.remove(); document.getElementById(id)?.remove(); body.innerHTML = srcBox(src, "图的写法有误（" + String(e.message || e).split("\n")[0] + "）"); }
  }
}

// ---------- 原型：缩放、编号角标、状态、点选 ----------
const FRAME_CSS = `.nz-hover{outline:2px dashed #d97706!important;cursor:crosshair!important}
.nz-marked{outline:2px solid #d97706!important}
.nz-hl{outline:3px solid #d97706!important;outline-offset:2px!important}
#nz-badges{position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647}
.nz-badge{position:absolute;display:grid;place-items:center;border-radius:999px;background:#2f6f5e;color:#fff;font-weight:600;font-family:system-ui,sans-serif;line-height:1;cursor:pointer;pointer-events:auto}
.nz-badge.on{background:#d97706}`;
let picking = null;
function wireFrame(scr) {
  const fd = scr.frame.contentDocument; if (!fd || !fd.body) return;
  const st = fd.createElement("style"); st.textContent = FRAME_CSS; fd.head.appendChild(st);
  frameTheme(scr);
  scr.W = parseInt(fd.body.dataset.width, 10) || 1280;
  const states = (fd.body.dataset.states || "").split(/[,，]/).map(s => s.trim()).filter(Boolean);
  if (states.length > 1) scr.statesEl.innerHTML = "状态：" + states.map(s => `<button type="button" data-st="${esc(s)}">${esc(s)}</button>`).join("");
  const box = fd.createElement("div"); box.id = "nz-badges"; fd.documentElement.appendChild(box); scr.badges = box;
  let last = null;
  fd.addEventListener("mouseover", e => {
    if (picking === scr.t.key && !e.target.closest(".nz-badge")) { last && last.classList.remove("nz-hover"); last = e.target; last.classList.add("nz-hover"); }
    const n = e.target.closest("[data-note], .nz-badge");
    hl(scr, n ? (n.dataset.note || n.dataset.no) : null);
  });
  fd.documentElement.addEventListener("mouseleave", () => hl(scr, null));
  fd.addEventListener("click", e => {
    const b = e.target.closest(".nz-badge");
    if (picking === scr.t.key) {
      e.preventDefault(); e.stopPropagation();
      if (b) return openBox(noteKey(scr, b.dataset.no));
      e.target.classList.remove("nz-hover");
      const n = e.target.closest("[data-note]");
      openBox(scr.t.key, { path: pathOf(e.target), text: (e.target.innerText || e.target.value || e.target.tagName).replace(/\s+/g, " ").trim().slice(0, 60),
        note: n ? n.dataset.note : "", state: scr.cur });
    } else if (b) { e.preventDefault(); e.stopPropagation(); focusNote(scr, b.dataset.no); }
  }, true);
  new MutationObserver(() => schedule(scr)).observe(fd.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style", "hidden", "open", "data-state"] });
  // 打开时不调用原型的 setState：原型自己会进默认状态。之前打开就切到 data-states 的第一个名字，
  // 名字和原型内部的状态键对不上时整屏被隐藏、显示空白
  if (states.length > 1) { scr.cur = states[0]; scr.statesEl.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", i === 0)); }
  fit(scr);
  markFrame(scr);
}
// 用 setTimeout 不用 rAF：页面没在绘制时（比如 Orca 的浏览器标签在后台）rAF 不触发，角标会停在旧位置
function schedule(scr) { if (scr.timer) return; scr.timer = setTimeout(() => { scr.timer = 0; fit(scr); }, 30); }
function fit(scr) {
  const fd = scr.frame.contentDocument; if (!fd || !fd.body) return;
  const zoom = scr.t.node.classList.contains("zoom"), W = scr.W;
  const s = Math.min(1, (scr.stage.clientWidth || 600) / W);
  scr.frame.style.width = W + "px"; scr.frame.style.height = "480px";
  const H = Math.max(480, Math.min(fd.documentElement.scrollHeight, zoom ? 6000 : 1400));
  scr.frame.style.height = H + "px"; scr.frame.style.transform = `scale(${s})`;
  scr.vp.style.width = Math.floor(W * s) + "px"; scr.vp.style.height = Math.floor(H * s) + "px";
  scr.s = s; placeBadges(scr);
}
function placeBadges(scr) {
  const fd = scr.frame.contentDocument, box = scr.badges; if (!fd || !box) return;
  const win = fd.defaultView, s = scr.s || 1, size = Math.round(20 / s), ring = Math.max(1, Math.round(2 / s));
  box.textContent = "";
  fd.querySelectorAll("[data-note]").forEach(el => {
    const r = el.getBoundingClientRect(); if (!r.width && !r.height) return;
    const b = fd.createElement("div"); b.className = "nz-badge" + (el.dataset.note === scr.hl ? " on" : ""); b.dataset.no = el.dataset.note; b.textContent = el.dataset.note;
    Object.assign(b.style, { left: Math.max(0, r.left + win.scrollX - size / 2) + "px", top: Math.max(0, r.top + win.scrollY - size / 2) + "px",
      width: size + "px", height: size + "px", fontSize: Math.round(12 / s) + "px", boxShadow: `0 0 0 ${ring}px #fff` });
    box.appendChild(b);
  });
}
function hl(scr, no) {  // 说明行、角标、原型元素互相高亮；只改变化了的，免得触发原型的变化监听
  no = no || null; if (scr.hl === no) return; scr.hl = no;
  const fd = scr.frame.contentDocument; if (!fd) return;
  fd.querySelectorAll(".nz-hl").forEach(x => { if (x.dataset.note !== no) x.classList.remove("nz-hl"); });
  if (no) fd.querySelectorAll("[data-note]").forEach(x => { if (x.dataset.note === no && !x.classList.contains("nz-hl")) x.classList.add("nz-hl"); });
  scr.badges && scr.badges.querySelectorAll(".nz-badge").forEach(b => b.classList.toggle("on", b.dataset.no === no));
  scr.notesEl && scr.notesEl.querySelectorAll(".note").forEach(li => li.classList.toggle("hl", li.dataset.no === no));
}
function noteKey(scr, no) { const t = targets.find(t => t.kind === "note" && t.parent === scr.t.key && t.no === no); return t ? t.key : scr.t.key; }
function focusNote(scr, no) {
  const li = scr.notesEl && [...scr.notesEl.children].find(x => x.dataset.no === no); if (!li) return;
  li.scrollIntoView({ block: "nearest", behavior: "smooth" }); flash(li);
}
function setFrameState(scr, name) {
  const fd = scr.frame.contentDocument, win = scr.frame.contentWindow; if (!fd) return;
  scr.cur = name; fd.body.dataset.state = name;
  try { if (typeof win.setState === "function") win.setState(name); } catch (e) {}
  scr.statesEl.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.st === name));
  schedule(scr);
}
function pathOf(el) {
  const parts = [];
  while (el && el.nodeType === 1 && el.tagName !== "BODY") {
    let i = 1, s = el; while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++;
    parts.unshift(el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + `:nth-of-type(${i})`); el = el.parentElement;
  }
  return parts.join(" > ");
}
function markFrame(scr) {
  const fd = scr.frame.contentDocument; if (!fd) return;
  const want = new Set();
  state.comments.filter(c => c.target === scr.t.key && c.el && c.el.path).forEach(c => { try { const x = fd.querySelector(c.el.path); x && want.add(x); } catch (e) {} });
  fd.querySelectorAll(".nz-marked").forEach(x => { if (!want.has(x)) x.classList.remove("nz-marked"); });
  want.forEach(x => { if (!x.classList.contains("nz-marked")) x.classList.add("nz-marked"); });
}
const ro = new ResizeObserver(es => es.forEach(e => { const scr = screensRT.find(s => s.stage === e.target); scr && schedule(scr); }));
screensRT.forEach(s => ro.observe(s.stage));

// ---------- 评论与结论 ----------
const commentsOf = key => state.comments.filter(c => c.target === key);
const countAll = t => state.comments.filter(c => c.target === t.key || (byKey[c.target] && byKey[c.target].parent === t.key)).length;
// opts.edit：改哪条评论；opts.quiet：不抢焦点（恢复草稿时）。没保存的内容存成草稿，页面刷新或被浏览器重新加载也不丢
function openBox(key, el, opts = {}) {
  const t = byKey[key]; if (!t) return;
  t.thread.querySelector(".newbox")?.remove();
  const old = opts.edit ? state.comments.find(c => c.id === opts.edit) : null, draft = state.drafts[key];
  if (old) el = old.el;
  const div = document.createElement("div"); div.className = "thread newbox";
  const v = state.verdicts[key];
  div.innerHTML = (el ? `<div class="meta">针对元素：「${esc(el.text)}」${el.note ? ` · 编号 ${esc(el.note)}` : ""}${el.state ? ` · 状态：${esc(el.state)}` : ""}</div>` : "") +
    (old ? '<div class="meta">改这条评论</div>' : v && v !== "pass" ? `<div class="meta">结论：${VERDICTS[v]}，写一句为什么</div>` : "") +
    `<textarea rows="3" placeholder="写下你的意见…（Ctrl+Enter 保存）"></textarea><div class="row"><button class="primary">保存</button><button>取消</button></div>`;
  t.thread.prepend(div); const ta = div.querySelector("textarea");
  ta.value = draft && (draft.edit || 0) === (opts.edit || 0) ? draft.text : old ? old.text : "";
  if (!opts.quiet) ta.focus();
  const [ok, cancel] = div.querySelectorAll("button");
  const drop = () => { delete state.drafts[key]; save(); div.remove(); };
  ta.addEventListener("input", () => { state.drafts[key] = { text: ta.value, el, edit: opts.edit || 0 }; save(); });
  ok.onclick = () => {
    const text = ta.value.trim(); if (!text) return;
    if (old) { old.text = text; old.t = new Date().toISOString(); }
    else state.comments.push({ id: Date.now(), target: key, title: t.title, el, text, t: new Date().toISOString() });
    drop(); render();
  };
  cancel.onclick = drop;
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ok.click(); });
}
function stats() {
  const vt = targets.filter(t => t.verdictable), c = { total: vt.length, seen: 0, pass: 0, ask: 0, fix: 0 };
  vt.forEach(t => { const v = state.verdicts[t.key]; if (v) { c.seen++; c[v]++; } });
  return c;
}
function render() {
  for (const t of targets) {
    if (!t.thread) continue;
    t.thread.querySelectorAll(".thread:not(.newbox)").forEach(x => x.remove());
    for (const c of commentsOf(t.key)) {
      const d = document.createElement("div"); d.className = "thread";
      d.innerHTML = (c.el ? `<div class="meta">元素：「${esc(c.el.text)}」${c.el.note ? ` · 编号 ${esc(c.el.note)}` : ""}${c.el.state ? ` · 状态：${esc(c.el.state)}` : ""}</div>` : "") +
        `<p class="c">${esc(c.text)}</p><div class="meta">${when(c.t)}<button data-edit="${c.id}">改</button><button data-del="${c.id}">删除</button></div>`;
      t.thread.appendChild(d);
    }
    if (t.verdictable) {
      const v = state.verdicts[t.key] || "";
      t.node.classList.remove("v-pass", "v-ask", "v-fix"); if (v) t.node.classList.add("v-" + v);
      t.node.querySelector(`.vd[data-k="${CSS.escape(t.key)}"]`)?.querySelectorAll("[data-v]").forEach(b => b.classList.toggle("on", b.dataset.v === v));
    }
  }
  for (const p of pendsRT) {
    const a = state.answers[p.t.key], ta = p.node.querySelector("[data-other]");
    p.node.querySelectorAll(".opt").forEach(l => { const on = !!a && String(a.pick) === l.dataset.j; l.classList.toggle("sel", on); l.querySelector("input").checked = on; });
    ta.hidden = !(a && a.pick === "other"); if (document.activeElement !== ta) ta.value = (a && a.other) || "";
    p.node.classList.toggle("answered", !!a);
    const label = a ? "已选 " + (a.pick === "other" ? "自己写" : LETTER(a.pick) + (p.q.options[a.pick] && p.q.options[a.pick].rec ? "（推荐）" : "")) : "还没选";
    for (const el of [p.node.querySelector(".blk-head .st"), document.querySelector(`[data-pst="${p.t.dom}"]`)]) if (el) { el.textContent = label; el.className = "st " + (a ? "done" : "todo"); }
  }
  const st = stats(), np = pendsRT.filter(p => state.answers[p.t.key]).length;
  if ($("pendCount")) $("pendCount").textContent = ` · 已选 ${np}`;
  $("fill").style.width = (st.total ? st.seen / st.total * 100 : 0) + "%";
  $("ptext").innerHTML = `已看 ${st.seen} / ${st.total}，<span class="n-pass">通过 ${st.pass}</span>、<span class="n-ask">有疑问 ${st.ask}</span>、<span class="n-fix">要改 ${st.fix}</span> · ${state.comments.length} 条评论` +
    (pendsRT.length ? ` · <span class="${np < pendsRT.length ? "n-ask" : "n-pass"}">待确认已选 ${np} / ${pendsRT.length}</span>` : "");
  $("nav").innerHTML = targets.filter(t => t.verdictable).map(t => {
    const v = state.verdicts[t.key] || "", n = countAll(t);
    return `<a href="#${t.dom}" data-jump="${t.dom}" class="${t.kind === "sec" && !t.sub ? "" : "sub"}${v ? " v-" + v : ""}"><i class="dot${v ? " v-" + v : ""}"></i><span class="lb">${esc(t.nav)}</span>${n ? `<span class="badge">${n}</span>` : ""}</a>`;
  }).join("");
  document.body.classList.toggle("only-open", !!state.onlyOpen);
  $("btnOpen").classList.toggle("on", !!state.onlyOpen);
  screensRT.forEach(markFrame);
}

document.addEventListener("click", e => {
  const x = e.target.closest("[data-v], [data-c], [data-zoom], [data-pick], [data-del], [data-edit], [data-rec], [data-unpick], #btnRecAll, [data-st], [data-jump], .ac[href], .note .no");
  if (!x) return;
  const pend = pendsRT.find(p => p.node.contains(x));
  if (x.matches("[data-v]")) {
    const key = x.closest(".vd").dataset.k, v = x.dataset.v;
    if (state.verdicts[key] === v) delete state.verdicts[key]; else state.verdicts[key] = v;
    save(); render();
    if (state.verdicts[key] && v !== "pass" && !commentsOf(key).length) openBox(key);
  } else if (x.matches("[data-c]")) openBox(x.closest(".vd").dataset.k);
  else if (x.matches("[data-zoom]")) {
    const blk = x.closest(".blk"), on = blk.classList.toggle("zoom");
    x.textContent = on ? "还原" : "放大"; document.body.classList.toggle("noscroll", !!document.querySelector(".zoom"));
    const scr = screensRT.find(s => s.t.node === blk); scr && schedule(scr);
  } else if (x.matches("[data-pick]")) {
    const key = targets.find(t => t.node === x.closest(".screen")).key;
    picking = picking === key ? null : key;
    document.querySelectorAll("[data-pick]").forEach(b => b.classList.toggle("on", picking && b.closest(".screen") === byKey[picking].node));
  } else if (x.matches("[data-del]")) { state.comments = state.comments.filter(c => String(c.id) !== x.dataset.del); save(); render(); }
  else if (x.matches("[data-edit]")) { const c = state.comments.find(c => String(c.id) === x.dataset.edit); c && openBox(c.target, null, { edit: c.id }); }
  else if (x.matches("[data-rec]")) { state.answers[pend.t.key] = { pick: pend.recI, other: "" }; save(); render(); }
  else if (x.matches("[data-unpick]")) { delete state.answers[pend.t.key]; save(); render(); }
  else if (x.matches("#btnRecAll")) { pendsRT.forEach(p => { if (p.recI >= 0 && !state.answers[p.t.key]) state.answers[p.t.key] = { pick: p.recI, other: "" }; }); save(); render(); }
  else if (x.matches("[data-st]")) { const scr = screensRT.find(s => s.t.node === x.closest(".screen")); scr && setFrameState(scr, x.dataset.st); }
  else if (x.matches(".note .no")) { const scr = screensRT.find(s => s.t.node === x.closest(".screen")); if (scr) { hl(scr, x.dataset.no); scr.stage.scrollIntoView({ block: "nearest", behavior: "smooth" }); } }
  else {
    const el = document.getElementById(x.dataset.jump || "ac-" + x.dataset.ac); if (!el) return;
    e.preventDefault(); el.scrollIntoView({ block: "center", behavior: "smooth" }); flash(el);
  }
});
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  document.querySelectorAll(".zoom").forEach(b => { b.classList.remove("zoom"); const z = b.querySelector("[data-zoom]"); if (z) z.textContent = "放大"; const scr = screensRT.find(s => s.t.node === b); scr && schedule(scr); });
  document.body.classList.remove("noscroll");
});
document.addEventListener("change", e => {  // 待确认：选了哪个
  const r = e.target.closest(".pend input[type=radio]"); if (!r) return;
  const p = pendsRT.find(p => p.node.contains(r)), prev = state.answers[p.t.key] || {};
  state.answers[p.t.key] = { pick: r.value === "other" ? "other" : +r.value, other: prev.other || "" };
  save(); render();
  if (r.value === "other") p.node.querySelector("[data-other]").focus();
});
document.addEventListener("input", e => {  // 待确认：自己写的答案
  if (!e.target.matches("[data-other]")) return;
  const p = pendsRT.find(p => p.node.contains(e.target));
  state.answers[p.t.key] = { pick: "other", other: e.target.value }; save(); render();
});
$("btnOpen").onclick = () => { state.onlyOpen = !state.onlyOpen; save(); render(); };
render();
for (const [k, d] of Object.entries(state.drafts)) if (byKey[k] && d && d.text) openBox(k, d.el, { edit: d.edit, quiet: true });
drawFigs();

// ---------- 导出：先结论汇总，再逐条意见 ----------
function where(c) {
  const t = byKey[c.target], e = c.el;
  return (t ? t.title : (c.title || c.target) + "（PRD 改过，位置可能变了）") +
    (e ? ` · 元素「${e.text}」\`${e.path}\`${e.note ? ` · 编号 ${e.note}` : ""}${e.state ? `（状态：${e.state}）` : ""}` : "");
}
function exportMd() {
  const st = stats(), vt = targets.filter(t => t.verdictable), cell = s => String(s).replace(/\|/g, "\\|");
  const L = [`# 评审意见 · ${document.title}`, "",
    `> 来自 ${SOURCE}。已看 ${st.seen} / ${st.total}：通过 ${st.pass}、有疑问 ${st.ask}、要改 ${st.fix}；评论 ${state.comments.length} 条` +
    (pendsRT.length ? `；待确认已选 ${pendsRT.filter(p => state.answers[p.t.key]).length} / ${pendsRT.length}` : "") + "。请产品 Agent 逐条处理后更新 PRD 和原型。", "",
    "## 结论汇总", ""];
  const rows = vt.filter(t => (state.verdicts[t.key] && state.verdicts[t.key] !== "pass") || countAll(t));
  if (rows.length) { L.push("| 位置 | 结论 | 评论 |", "|---|---|---|"); rows.forEach(t => L.push(`| ${cell(t.title)} | ${VERDICTS[state.verdicts[t.key]] || "没给结论"} | ${countAll(t)} |`)); }
  else L.push("_没有要改或有疑问的地方。_");
  const passed = vt.filter(t => state.verdicts[t.key] === "pass" && !countAll(t)), unseen = vt.filter(t => !state.verdicts[t.key]);
  if (passed.length) L.push("", `**通过（${passed.length}）**：${passed.map(t => t.title).join("、")}`);
  if (unseen.length) L.push("", `**没看（${unseen.length}）**：${unseen.map(t => t.title).join("、")}`);
  if (pendsRT.length) {
    L.push("", "## 待确认的选择", "", "| 编号 | 问题 | 选择 | 推荐 |", "|---|---|---|---|");
    for (const p of pendsRT) {
      const a = state.answers[p.t.key], rec = p.recI >= 0 ? `${LETTER(p.recI)}. ${p.q.options[p.recI].label}` : "（没写推荐）";
      L.push(`| ${cell(p.t.no)} | ${cell(p.q.title)} | ${cell(a ? pickLabel(p, a).replace(/\n/g, " / ") : "**没选**")} | ${cell(rec)} |`);
    }
  }
  L.push("", "## 逐条意见", "");
  const order = targets.map(t => t.key), orphan = state.comments.filter(c => !byKey[c.target]);
  const cs = state.comments.filter(c => byKey[c.target]).sort((a, b) => order.indexOf(a.target) - order.indexOf(b.target) || a.id - b.id);
  for (const c of cs.concat(orphan)) {
    const t = byKey[c.target], v = t && t.verdictable ? state.verdicts[t.key] : "";
    L.push(`- **[${where(c)}]**${v ? `（${VERDICTS[v]}）` : ""} ${c.text.replace(/\n/g, " / ")}`);
  }
  if (!state.comments.length) L.push("_没有评论。_");
  return L.join("\n");
}
$("btnExport").onclick = () => { $("out").value = exportMd(); $("dlg").showModal(); };
$("btnClose").onclick = () => $("dlg").close();
$("btnCopy").onclick = async () => { try { await navigator.clipboard.writeText($("out").value); } catch (e) { $("out").select(); document.execCommand("copy"); } };
$("btnDl").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([$("out").value], { type: "text/markdown" })); a.download = SLUG + "-comments.md"; a.click(); };
$("btnClear").onclick = () => { if (confirm("清空全部评论、结论和待确认的选择？")) { state.comments = []; state.verdicts = {}; state.answers = {}; state.drafts = {}; save(); document.querySelectorAll(".newbox").forEach(x => x.remove()); render(); $("dlg").close(); } };
function frameTheme(scr) { const r = scr.frame.contentDocument && scr.frame.contentDocument.documentElement; if (r) state.theme ? r.dataset.theme = state.theme : delete r.dataset.theme; }
function applyTheme() { state.theme ? document.documentElement.dataset.theme = state.theme : delete document.documentElement.dataset.theme; screensRT.forEach(frameTheme); }
$("btnTheme").onclick = () => { state.theme = isDark() ? "light" : "dark"; save(); applyTheme(); drawFigs(); };
applyTheme();
</script>
</body>
</html>
"""


# ---------- 预处理：把 ```mermaid、```screen 代码块换成占位，交给页面渲染 ----------
CAPTION = re.compile(r"^\s*\**图\s*\d*\s*[：:]\s*(.+?)\**\s*$")
FENCE = re.compile(r"^```\s*([\w-]*)\s*(.*?)\s*$")
FLOW_KINDS = ("flowchart", "graph", "sequencediagram", "statediagram", "statediagram-v2", "journey")


def parse_screen_rows(lines):
    """screen 块的每行「编号 | 名称 | 说明 | 对应 AC」；表头行、分隔行跳过；说明里多出来的竖线并回说明。"""
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.strip() or cells[0] in ("编号", "#", "No", "no") or all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        cells += [""] * (4 - len(cells))
        no, name = cells[0], cells[1]
        desc, ac = ("|".join(cells[2:-1]), cells[-1]) if len(cells) > 4 else (cells[2], cells[3])
        acs = re.findall(r"AC-\d+", ac) or re.findall(r"AC-\d+", desc)
        rows.append({"no": no, "name": name, "desc": desc, "acs": sorted(set(acs), key=lambda a: int(a[3:]))})
    return rows


def preprocess(md):
    """返回 (换成占位后的 md, 图列表, screen 块列表, 待确认列表)。只认顶格的代码块和提示块。"""
    lines, out, figs, blocks, pends, i = md.split("\n"), [], [], [], [], 0
    while i < len(lines):
        if PEND_HEAD.match(lines[i]):
            j = i
            while j < len(lines) and QUOTE.match(lines[j]):
                j += 1
            pends.append(parse_pending([QUOTE.match(x).group(1) for x in lines[i:j]], i + 1))
            out += ["", f'<div class="nz-slot" data-pend="{len(pends) - 1}"></div>', ""]
            i = j
            continue
        m = FENCE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].startswith("```"):
            j += 1
        lang, body = m.group(1).lower(), lines[i + 1:j]
        if lang == "mermaid":
            k, cap = len(out) - 1, ""
            while k >= 0 and not out[k].strip():
                k -= 1
            if k >= 0 and CAPTION.match(out[k]):
                cap = CAPTION.match(out[k]).group(1).strip()
                del out[k:]
            figs.append({"caption": cap, "src": "\n".join(body), "line": i + 1})
            out += ["", f'<div class="nz-slot" data-fig="{len(figs) - 1}"></div>', ""]
        elif lang == "screen":
            blocks.append({"ref": m.group(2).strip().strip("\"'"), "rows": parse_screen_rows(body), "line": i + 1})
            out += ["", f'<div class="nz-slot" data-scr="{len(blocks) - 1}"></div>', ""]
        else:
            out += lines[i:j + 1]
        i = j + 1
    return "\n".join(out), figs, blocks, pends


# ---------- 待确认：每条带选项（推荐的标「（推荐）」，冒号后写理由），评审页上能直接选 ----------
PEND_HEAD = re.compile(r"^ {0,3}>\s?\[!待确认\]")
QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
REC_MARK = re.compile(r"\s*(?:（推荐）|\(推荐\)|【推荐】|\[推荐\]|\*\*推荐\*\*)\s*")
PEND_ID = re.compile(r"^([A-Za-z]{1,3}\d{1,3})[.、]?\s+")


def plain(s):
    """去掉行内 Markdown 记号，用作标题和导出。"""
    return re.sub(r"\*\*|__|`", "", s).strip()


def split_opt(text):
    """「标签：理由」或「标签——理由」→ (标签, 理由)。"""
    parts = re.split(r"\s*(?:——|：|:\s)\s*", text, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text.strip(), "")


def pending_title(text, limit=60):
    """没写问题行时的标题：开头是「**小标题**：」就用小标题，否则用第一句。"""
    m = re.match(r"\s*\*\*([^*]+?)\*\*\s*(（[^）]*）)?\s*[：:]", text)
    if m:
        return (m.group(1) + (m.group(2) or "")).strip()
    t = plain(text).split("\n")[0]
    m = re.match(r"(.+?[？?。！])", t)
    t = m.group(1) if m else t
    return t if len(t) <= limit else t[:limit] + "…"


def guess_options(text):
    """没写选项的旧 PRD：从「推荐…」「现在按「…」画」「**…**（现在按这个画）」「备选：…」「理由…」里猜出推荐和备选。"""
    flat = plain(text.replace("\n", " "))
    m = re.search(r"理由\s*[：:]?\s*(.+)$", flat)
    reason, rec = (m.group(1).strip() if m else ""), ""
    m = re.search(r"推荐\s*[：:]?\s*([^。\n]+)", flat)
    if m:
        rec = m.group(1).strip()
    else:
        m = re.search(r"现在按「([^」]+)」画", flat) or re.search(r"\*\*([^*]+?)\*\*\s*（现在按这个画）", text)
        rec = plain(m.group(1)) if m else ""
    opts = [{"label": rec, "desc": reason, "rec": True}] if rec else [{"label": "按 PRD 现在写的", "desc": "", "rec": False}]
    m = re.search(r"备选\s*[：:]\s*(.+?)(?:。\s*(?=理由)|。?\s*$)", flat)
    for alt in (m.group(1).split("；") if m else []):
        if alt.strip():
            label, _, desc = alt.partition("——")
            opts.append({"label": label.strip(), "desc": desc.strip(), "rec": False})
    return opts


def parse_pending(lines, line_no):
    """一条待确认：[!待确认] 后面写问题；顶层列表有 2 个以上、其中一个标了「（推荐）」的，算选项，其余行是说明。"""
    head = re.sub(r"^\s*\[!待确认\]\s*", "", lines[0]).strip()
    rest, items, opt_lines = lines[1:], [], set()
    for i, line in enumerate(rest):
        m = LIST_ITEM.match(line)
        if m and not m.group(1):
            items.append([m.group(2)])
            opt_lines.add(i)
        elif items and i - 1 in opt_lines and line[:1] in (" ", "\t") and line.strip():
            items[-1].append(line.strip())  # 选项的续行
            opt_lines.add(i)
    texts = [" ".join(x) for x in items]
    structured = len(texts) >= 2 and any(REC_MARK.search(t) for t in texts)
    body = "\n".join(x for i, x in enumerate(rest) if not (structured and i in opt_lines)).strip()
    title = plain(head) or pending_title(body)
    m = PEND_ID.match(title)  # 问题前面的编号（p01、b02），单独显示
    title = title[m.end():] if m else title
    if structured:
        opts = []
        for t in texts:
            label, desc = split_opt(REC_MARK.sub(" ", t).strip())
            opts.append({"label": plain(label), "desc": desc, "rec": bool(REC_MARK.search(t))})
    else:
        opts = guess_options((head + "\n" + body).strip())
    return {"id": m.group(1) if m else "", "title": title, "body": body, "options": opts, "structured": structured, "line": line_no}


def check_pendings(pends):
    warns = []
    for p in pends:
        recs = [o for o in p["options"] if o["rec"]]
        if not p["structured"]:
            warns.append(f"第 {p['line']} 行的待确认没写选项：提示块里列 2 个以上选项（「- 选项：理由」），推荐的那个标「（推荐）」"
                         "（评审页先按正文猜了推荐和备选，可能不准）")
        elif len(recs) != 1:
            warns.append(f"第 {p['line']} 行的待确认要正好一个选项标「（推荐）」")
        elif not recs[0]["desc"]:
            warns.append(f"第 {p['line']} 行的待确认，推荐的选项没写理由（「选项：理由」）")
    return warns


def match_screen(ref, screens):
    """screen 块写的原型名 → 第几屏：文件名、去掉扩展名、<title>、编号前缀都认。"""
    r = ref.lower().removesuffix(".html")
    for test in (lambda s: s["file"].lower().removesuffix(".html") == r, lambda s: s["name"].lower() == r,
                 lambda s: re.sub(r"^\d+[-_ ]*", "", s["file"].lower().removesuffix(".html")) == r,
                 lambda s: s["file"].lower().startswith(r)):
        for i, s in enumerate(screens):
            if r and test(s):
                return i
    return -1


# ---------- 概览卡：从「背景与目标」「范围」抽一句话目标、成功标准、做 / 不做 ----------
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.+)$")


def sections_of(md):
    """按 ## / ### 切小节：[(级别, 标题, 行)]；代码块里的 # 不算。"""
    secs, fence = [(1, "", [])], False
    for line in md.splitlines():
        if line.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if m:
            secs.append((len(m.group(1)), m.group(2), []))
        else:
            secs[-1][2].append(line)
    return secs


def h2_block(secs, pred):
    """第一个标题满足 pred 的 ## 小节，连同它下面的 ### 小节。"""
    out = None
    for lv, title, lines in secs:
        if out is None:
            if lv == 2 and pred(title):
                out = [(lv, title, lines)]
        elif lv == 3:
            out.append((lv, title, lines))
        else:
            break
    return out or []


def list_items(lines, start=0, first_run=True):
    """顶层列表项；first_run 时只取紧跟着的一串。"""
    items, begun = [], False
    for line in lines[start:]:
        if not line.strip():
            if begun and first_run:
                break
            continue
        m = LIST_ITEM.match(line)
        if not m:
            if begun and first_run:
                break
            continue
        begun = True
        if not m.group(1):
            items.append(m.group(2).strip())
    return items


def labeled(block, label):
    """「**目标**：…」这类带标签的行：冒号后有内容就取内容，否则取紧跟的列表；也认标题就是标签的 ### 小节。"""
    pat = re.compile(rf"^\s*(?:[-*]\s+)?(?:\*\*(?:{label})[：:]\*\*|\**(?:{label})\**\s*[：:])\s*(.*)$")
    for lv, title, lines in block:
        if lv == 3 and re.fullmatch(rf"(?:[\d.]+\s*)?(?:{label})", title.strip()):
            items = list_items(lines, first_run=False)
            if items:
                return items
        for i, line in enumerate(lines):
            m = pat.match(line)
            if m:
                rest = m.group(1).strip()
                return [rest] if rest else list_items(lines, i + 1)
    return []


def overview(md):
    secs = sections_of(md)
    bg = h2_block(secs, lambda t: "背景" in t or "目标" in t)
    scope = h2_block(secs, lambda t: "范围" in t)
    o = {"goal": (labeled(bg, "一句话目标|产品目标|目标") or [""])[0],
         "success": labeled(bg, "成功标准|成功指标|衡量标准")[:6],
         "do": labeled(scope, "做什么|要做|做")[:8],
         "dont": labeled(scope, "不做什么|不做")[:8]}
    return {k: v for k, v in o.items() if v}


# ---------- 规范检查（只提醒，不拦）----------
FONT_SCALE = {12, 13, 14, 16, 20, 28}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕⌚-⏿]")
IMPL_HINT = re.compile(r"(?<![\w/])(?:tools|\.claude|src|app|lib)/[\w./-]+|\b[\w-]+\.(?:py|ps1|sh|ts|tsx|js|jsx|java|go|sql|yaml|yml|json)\b"
                       r"|(?:^|\s)(?:python|npm|pnpm|yarn|git|orca|claude|codex|opencode|curl|docker)\s+[a-z-]+")


def check_prd(md):
    """PRD 只写功能设计：找出疑似实现方式的行（文件路径、脚本名、命令）。代码块和「版本记录」一节跳过。"""
    hits, fence, skip = [], False, False
    for i, line in enumerate(md.splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
            continue
        if line.startswith("## "):
            skip = "版本" in line
        if fence or skip:
            continue
        m = IMPL_HINT.search(line)
        if m:
            hits.append(f"第 {i} 行「{m.group(0).strip()}」")
    return hits


def check_figures(md2, figs):
    """每个主流程要有图：PRD 里没有流程类的图、或者标题带「主流程」的小节里没有图，都提醒。"""
    warns = []
    kinds = {(f["src"].strip().split(None, 1) or [""])[0].lower() for f in figs}
    if not figs:
        warns.append("主流程没有图：PRD 里没有 ```mermaid 流程图（每个主流程一张；关键交互画时序图或状态图）")
    elif not kinds & set(FLOW_KINDS):
        warns.append("主流程没有图：图里没有流程图、时序图、状态图或用户旅程")
    secs = sections_of(md2)
    for idx, (lv, title, lines) in enumerate(secs):
        if "主流程" not in title:
            continue
        body = list(lines)
        if lv == 2:
            for lv2, _, more in secs[idx + 1:]:
                if lv2 == 2:
                    break
                body += more
        if not any("data-fig=" in line for line in body):
            warns.append(f"主流程没有图：小节「{title}」里没有 ```mermaid 图")
    for f in figs:
        if not f["caption"]:
            warns.append(f"第 {f['line']} 行的图没有图题（代码块上一行写「图：xxx」）")
    return warns


def check_screens(md, screens, blocks):
    """每屏一个 screen 说明块；说明的编号和原型的 data-note 对得上；每条 AC 至少出现在一屏的说明里。"""
    warns, referenced = [], set()
    for b in blocks:
        si = b["screen"]
        if si < 0:
            warns.append(f"第 {b['line']} 行的 screen 块找不到原型「{b['ref']}」（写原型文件名，例如 01-xxx.html）")
            continue
        referenced.add(si)
        sc, nos = screens[si], [r["no"] for r in b["rows"]]
        if not b["rows"]:
            warns.append(f"原型 {sc['file']} 的 screen 块是空的（每行「编号 | 名称 | 说明 | 对应 AC」）")
        missing = [n for n in nos if n not in sc["notes"]]
        if missing:
            warns.append(f"原型 {sc['file']} 里没有 data-note={'、'.join(missing)} 的元素（说明的编号要在原型上标出来）")
        extra = [n for n in sc["notes"] if n not in nos]
        if extra:
            warns.append(f"原型 {sc['file']} 里 data-note={'、'.join(extra)} 没有对应的说明行")
    for i, sc in enumerate(screens):
        if i not in referenced:
            warns.append(f"某屏没有 screen 说明块：原型 {sc['file']}（在 PRD 对应小节加 ```screen {sc['file']}）")
    acc = h2_block(sections_of(md), lambda t: "验收" in t and "对照" not in t)
    defined = []
    for _, _, lines in acc:
        for line in lines:
            m = re.match(r"^\s*(?:[-*+]\s+|\|\s*)\**(AC-\d+)\b", line)
            if m and "非界面" not in line and m.group(1) not in defined:
                defined.append(m.group(1))
    shown = {a for b in blocks for r in b["rows"] for a in r["acs"]}
    lost = [a for a in defined if a not in shown]
    if lost and screens:
        warns.append(f"AC 没有出现在任何屏的说明里：{'、'.join(lost[:12])}" + (f" 等 {len(lost)} 条" if len(lost) > 12 else "")
                     + "（加进相关屏的说明行；和界面无关的在那条 AC 末尾标「（非界面）」）")
    return warns


def check_prototype(name, text):
    """原型按视觉规范：字号只用六级、用规范的变量、导航标题按钮不用 emoji、不引用外部资源。"""
    warns = []
    style = " ".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I)) + " ".join(re.findall(r'style="([^"]*)"', text))
    bad = sorted({int(float(x)) for x in re.findall(r"font-size:\s*([\d.]+)px", style)} - FONT_SCALE)
    if bad:
        warns.append(f"字号不在六级里：{', '.join(f'{x}px' for x in bad)}")
    if "--fs-14" not in text:
        warns.append("没用视觉规范的变量（把 tools/ui-tokens.css 复制进 <style> 开头）")
    for tag in ("title", "nav", "h1", "h2", "h3", "button", "a"):
        for inner in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.S | re.I):
            if EMOJI.search(re.sub(r"<[^>]+>", "", inner)):
                warns.append(f"<{tag}> 里有 emoji：{re.sub(r'<[^>]+>', '', inner).strip()[:20]}")
                break
    if re.search(r"""(?:src|href)=["']https?://""", text):
        warns.append("引用了外部资源（原型要离线也能看）")
    states = re.search(r"<body[^>]*\bdata-states=[\"']([^\"']+)", text, re.I)
    if states and len(re.split(r"[,，]", states.group(1))) > 1 and "setState" not in text:
        warns.append("声明了 data-states 但没有 window.setState(名称)（评审页只能切 body 的 data-state，原型要用 CSS 跟着变）")
    return [f"原型 {name}：{w}" for w in warns]


WARNINGS = []


def js(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build(prd_path, protos, title, reply_to, out):
    md = Path(prd_path).read_text(encoding="utf-8").replace("\r\n", "\n")  # Windows 检出可能是 CRLF
    md = re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S)  # frontmatter 不进评审页（marked 会把它渲染成标题）
    if not title:
        m = re.search(r"^#\s+(.+)$", md, re.M)
        title = (m.group(1).strip() if m else Path(prd_path).stem) + " · 评审"
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", Path(prd_path).parent.name or Path(prd_path).stem)
    screens = []
    for p in protos:
        text = Path(p).read_text(encoding="utf-8")
        m = re.search(r"<title>(.*?)</title>", text, re.S)
        screens.append({"name": (m.group(1).strip() if m else Path(p).stem), "file": Path(p).name, "html": text,
                        "notes": list(dict.fromkeys(re.findall(r"""data-note=["']([^"']+)["']""", text)))})
        WARNINGS.extend(check_prototype(Path(p).name, text))
    md2, figs, blocks, pends = preprocess(md)
    for b in blocks:
        b["screen"] = match_screen(b["ref"], screens)
    impl = check_prd(md)
    if impl:
        WARNINGS.append(f"PRD 里可能写了实现方式（PRD 只写功能设计）：{'；'.join(impl[:8])}" + (f" 等 {len(impl)} 处" if len(impl) > 8 else ""))
    WARNINGS.extend(check_figures(md2, figs))
    WARNINGS.extend(check_screens(md, screens, blocks))
    WARNINGS.extend(check_pendings(pends))
    page = (PAGE.replace("__TOKENS__", ui_tokens())
                .replace("__TITLE__", html.escape(title))
                .replace("__REPLY__", html.escape(reply_to))
                .replace("__SLUG__", json.dumps(slug))
                .replace("__SCREENS__", js([{k: s[k] for k in ("name", "file", "html")} for s in screens]))
                .replace("__FIGS__", js([{"caption": f["caption"], "src": f["src"]} for f in figs]))
                .replace("__BLOCKS__", js([{k: b[k] for k in ("ref", "screen", "rows")} for b in blocks]))
                .replace("__PENDS__", js([{k: p[k] for k in ("id", "title", "body", "options", "structured")} for p in pends]))
                .replace("__OVERVIEW__", js(overview(md)))
                .replace("__MERMAID__", json.dumps(MERMAID_JS))
                .replace("__SOURCE__", js(Path(out).resolve().as_posix()))
                .replace("__PRD__", md2.replace("</script", "<\\/script")))
    Path(out).write_text(page, encoding="utf-8")
    return title, len(screens), len(figs), len(blocks), len(pends)


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="PRD 评审页生成器")
    ap.add_argument("prd")
    ap.add_argument("--prototype", nargs="*", default=[], help="原型 HTML，可以多个（支持通配）")
    ap.add_argument("-o", "--out")
    ap.add_argument("--title")
    ap.add_argument("--reply-to", default="粘贴回产品 Agent 的会话")
    args = ap.parse_args()
    protos = sorted({f for p in args.prototype for f in (glob.glob(p) or [p])})
    out = args.out or str(Path(args.prd).with_name("review.html"))
    title, n, nf, nb, npd = build(args.prd, protos, args.title, args.reply_to, out)
    print(f"已生成 {Path(out).resolve()}：{title}，原型 {n} 屏、图 {nf} 张、界面说明 {nb} 块、待确认 {npd} 条")
    print(f"浏览器打开：{Path(out).resolve().as_uri()}")
    for w in WARNINGS:
        print("⚠ " + w)


if __name__ == "__main__":
    main()
