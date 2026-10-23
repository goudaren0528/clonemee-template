r"""
任务派发 —— 把已确认的计划任务派给 agent 执行（dispatch skill 的执行部分）。

    python tools/dispatch.py                          # 今天的当日计划里可派发的任务：只出方案，不执行
    python tools/dispatch.py --task T2 --agent codex  # 指定任务和执行器（覆盖任务行的 exec:）
    python tools/dispatch.py --task T2 --run          # 真正派发
    python tools/dispatch.py --plan plans/2026-01-01-example-plan.md --task T1 --run
    python tools/dispatch.py --status                 # 看已派发的运行记录和 Orca 工作区状态
    python tools/dispatch.py --task T2 --role accept  # 按 agents/accept.md 的角色派发（注入角色设定，默认用它的执行器）

派发方式按运行环境自动选择（tools/env.py）：
  orca   本机 Orca 可用：在项目里新建 Orca 工作区（独立分支）+ 启动 agent + 发任务说明 + 命名；
         或者在项目现有目录里启动 agent，不新建工作区
  local  没有 Orca：在项目目录开一个新终端窗口，启动带名字的 agent 会话，让它先读运行记录里的任务说明
执行器（谁说了算：--agent > 角色的 default_executor > 任务行 exec: > 兜底）：兜底时改代码的用 OpenCode、其他用 Claude Code，方案里标「兜底」。
Orca 里在哪做（--new / --existing > 角色的 workspace > 兜底）：会提交改动的（开发、产品）新建工作区，只读的（排查、调研、
单独验收）用现有目录；没有角色时看任务标题，只读的用现有目录，其余新建。规则见 plans/prd/agent-dispatch/prd.md 5.2。
PRD 定稿的需求要走开发交付流水线（多 Agent 协同、并行任务线），用 tools/delivery.py，不用本工具。
出方案时先报本机推进位 x/6（同时最多 6 件，按设备算：只数本机有会话开着的派发，别的设备上的不计，dispatch skill「推进位」）。
每次派发都在 runs/ 留一份运行记录（模板 templates/run.md），并回填任务行的 run: 字段。
回报给谁：派发者自己（当前会话的 Orca 终端）就是派出去的会话要回报的主会话，写进运行记录的 report_to；任务说明里带上
tools/notify.py 的回报命令（会话间通信，playbooks/comms.md）。有跟进 Agent 时由跟进 Agent 回报，执行会话不发
（产品 Agent、规划 Agent 例外：定稿后自己发「交接」）。
所有派发默认同时派一个跟进 Agent（agents/pm.md，固定 Claude Code，在 assistant 里启动）盯到完成；--no-follow 不派。
授权 = 确认过的执行方案或 PRD：运行记录「授权」一节从任务引用的执行方案（plans/exec/，规划 Agent 出）、PRD（plans/prd/，产品 Agent 出）
或任务自己的执行说明生成，你在任务行「- 授权：」写的原样并进去；跟进 Agent 按三级判定处理：有推荐、可回退、
范围和验收不变直接做，中等报主会话，红线（钱、权益、密钥、账号权限、不可逆、对外发布、改范围或验收、删数据）才问你本人。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import clone
import common
import device
import env
import handoff
import notify
import plan
import recall
from common import ROOT, load_registry, norm_remote, repo_keys, setup_stdio

RUNS = ROOT / "runs"
AGENT_DIR = ROOT / "agents"
AGENTS = ("claude", "codex", "opencode")
AGENT_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
# 兜底（既没有角色、也没写执行器）：改代码的用 OpenCode，其他用 Claude Code
CODE_AGENT, OTHER_AGENT = "opencode", "claude"
READONLY_HINT = re.compile(r"只读|排查|调研|审查|评审|整理|讨论|文档|报告|review|investigate|research", re.I)  # 只读排查、文档整理
CODE_HINT = re.compile(r"改代码|写代码|实现|开发|修复|修 ?bug|重构|迁移|前端|后端|接口|\bfix\b|\bfeat\b|refactor|bug", re.I)
EXPERIENCE = (f"体验：交付有界面（网页、页面、能点的东西）时，做完先按 {ROOT.as_posix()}/.claude/skills/accept-test/SKILL.md "
              "的「体验准备」一节在本地启动好，总结里带上体验卡（链接、体验要点 3 条左右、关闭命令）；启动不了也要写清卡在哪、怎么手动启动；"
              "没有界面就写「没有界面，不需要体验环境」。")
CONSTRAINTS = ("不要 push、合并、发布，不要删除分支或文件；需要这些操作时先停下来问我。"
               "只改和本任务有关的文件。拿不准的地方先问，不要猜。"
               "跑测试必须带 ASSISTANT_NO_SEND=1（测试公共入口 tests/_isolation.py 已默认带上），不要读 private/ 下的 webhook 文件。")
DONE = "做完后用三五句话总结：改了什么、怎么验证的、还有什么没做完；不要自己关闭会话。"
# 「你」只在和你对话、assistant 内部用；对外内容写你的名字
NAMING = "称呼：写进 PR、issue、评论、提交信息或发给别人的内容里，不要用内部称呼「你」，写使用者的名字或 GitHub 账号。"
# 授权：你给跟进 Agent（没配跟进时是主会话）的边界，写进运行记录「授权」一节，不发给执行会话（执行会话的边界是「约束」）。
# 执行方案、PRD 定稿就是授权范围，auth_of 从它们生成；都没有、也没确认时才用 AUTH_DEFAULT
AUTH_DEFAULT = ("未单独授权：没有确认过的执行方案或 PRD，按 task-follow 第 5 节的默认边界——任务范围内的技术选择、常规操作（装依赖、跑测试和构建、在当前分支提交）"
                "按第 1 级直接做；中等的（推送分支、要判断偏好、顺序协调）报主会话；红线和对外发布、删除、合并到 main、动生产一律问你本人。不能派生子任务。")
AUTH_DECIDE = "范围内的实现方式和技术选择、步骤顺序、措辞和格式细节、复测几轮、常规操作（装依赖、跑测试和构建、在任务分支提交）"
AUTH_BASIS = "执行方案 / PRD + 你的做事偏好 profile/preferences.md；选最贴近验收、改动最小、可回退、和现有写法一致的"
AUTH_TIERS = ("① 直接做（默认）：有推荐方案、把握大、可回退、不改范围和验收，或授权明说可以（第 3 级的红线除外）——做完记「决策记录」，不发消息；"
              "② 报主会话：中等风险、要判断你的偏好但不敏感、影响跨任务 / 跨项目、时间或顺序要协调——发一条「要拍板」，正文开头「中等：」，一句话 + 推荐 + 备选；"
              "③ 红线，必须问你本人（授权里写了也不放行）：钱、权益、密钥、账号权限、生产环境或真实数据、不可逆、对外发布（合 PR、发布、发给别人、推送到共享或公开仓库）、"
              "改目标 / 范围 / 验收、删数据或历史——发「要拍板」，开头「红线：」，主会话出卡片")
AUTH_ESCALATE = ("task-follow 第 5 节第 3 级的红线（上一条已列，授权里写了也升级——钱、权益、密钥、账号权限、生产或真实数据、不可逆、对外发布、改目标 / 范围 / 验收、删数据或历史）"
                 "；删除分支或文件没被授权时也算红线")
HANDOFF_ROLES = ("product", "planner")  # 和你本人对话、出方案的角色：本身不需要先有执行方案；配了跟进也自己发「交接」
CHILD_REPORT = ("回报：你是跟进 Agent 按授权派生的子任务，不要回报 助手主会话。做完在会话里留三五句总结（改了什么、证据、还有什么没做完）"
                "就停下；要决定的事停下来在会话里问。跟进 Agent 会来读、来回复。")


# ---------- 角色 ----------

def load_role(slug):
    """读 agents/<slug>.md：返回 (frontmatter, 注入文本)。注入「角色」到「禁止」几节，不含「参考」。"""
    f = AGENT_DIR / f"{slug}.md"
    if not f.exists():
        sys.exit(f"没有角色 {slug}：可选 " + "、".join(x.stem for x in sorted(AGENT_DIR.glob("*.md")) if x.name != "INDEX.md"))
    fm, body = recall.parse(f)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    start = body.find("## 角色")
    end = body.find("## 参考")
    text = body[start:end if end != -1 else None].strip() if start != -1 else body.strip()
    text = text.replace("{assistant}", ROOT.as_posix())  # 角色里引用的 assistant skill 路径，换成本设备的
    return fm, f"【角色设定：{fm.get('name', slug)}】\n{text}"


# ---------- 读任务 ----------

def task_block(path, tid):
    """返回任务行之后、下一个任务之前的子条目文本。"""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out, inside = [], False
    for l in lines:
        if re.match(rf"^- \[.\] {tid} ", l):
            inside = True
            continue
        if inside and (re.match(r"^- \[.\] T\d+ ", l) or l.startswith("#")):
            break
        if inside:
            out.append(l)
    return "\n".join(out)


def brief_of(task, block, doc=None):
    """优先用「执行说明」里的开场说明；没有就用任务引用的执行方案，再没有就按任务字段拼一份。"""
    m = re.search(r"```text\s*\n(.*?)```", block, re.S)
    if m:
        text = "\n".join(l.strip() for l in m.group(1).strip().splitlines())
        if "约束" not in text:
            text += "\n约束：" + CONSTRAINTS
        return text
    get = lambda k: next((re.sub(rf"^\s*- {k}：", "", l).strip() for l in block.splitlines() if re.match(rf"^\s*- {k}：", l)), "")
    if doc and doc["kind"] == "执行方案" and doc["confirmed"]:
        sec = lambda k: flat(md_section(doc["body"], k))
        return "\n".join([
            f"目标：{sec('目标') or task['title']}",
            f"背景：按执行方案 {(ROOT / doc['rel']).as_posix()} 做（背景、步骤、谁做都在里面，先读它）",
            f"验收：{sec('验收') or get('验收') or '完成目标，并说明怎么验证的'}",
            f"约束：{sec('约束') or CONSTRAINTS}",
            f"做完：{DONE}",
        ])
    return "\n".join([
        f"目标：{task['title']}",
        f"背景：{get('说明') or get('为什么今天') or '见 assistant 计划 ' + task['plan'] + '#' + task['id']}",
        f"验收：{get('验收') or '完成目标，并说明怎么验证的'}",
        f"约束：{CONSTRAINTS}",
        f"做完：{DONE}",
    ])


def md_section(body, name):
    """Markdown 正文里「## <name>」一节的内容（去掉注释）。"""
    m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    return re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""


def flat(text):
    """多行（列表）压成一行，条目之间用「；」。"""
    items = [re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", l).strip() for l in text.splitlines()]
    return "；".join(i for i in items if i)


def sub_item(block, key):
    """任务的某个字段：开场粘贴里「<key>：」一行优先，其次任务行下的「- <key>：」子项。"""
    paste = re.search(r"```text\s*\n(.*?)```", block, re.S)
    m = paste and re.search(rf"^\s*{key}[：:]\s*(.+)$", paste.group(1), re.M)
    if m:
        return m.group(1).strip()
    return next((re.sub(rf"^\s*- {key}[：:]", "", l).strip() for l in block.splitlines() if re.match(rf"^\s*- {key}[：:]", l)), "")


def doc_of(block, plan_file):
    """任务引用的执行方案（plans/exec/*.md，规划 Agent 出）或 PRD（plans/prd/<slug>/prd.md，产品 Agent 出），执行方案优先。
    返回 {kind, rel, fm, body, confirmed}，都没引用返回 None。status 是 active（或 done）才算你确认过 / PRD 定稿。"""
    refs = handoff.refs(block, plan_file)
    for kind, hit in (("执行方案", lambda r: r.startswith("plans/exec/") and r.endswith(".md")), ("PRD", lambda r: r.endswith("/prd.md"))):
        rel = next((r for r in refs if hit(r)), None)
        if rel:
            fm, body = recall.parse(ROOT / rel)
            return {"kind": kind, "rel": rel, "fm": fm, "body": body, "confirmed": str(fm.get("status")) in ("active", "done")}
    return None


def explicit_auth(block):
    """你写明的授权原文：任务行下的「- 授权：」子项（连同缩进更深的续行），加上开场粘贴里以「授权」开头的那一行。"""
    out, lines = [], block.splitlines()
    for i, l in enumerate(lines):
        m = re.match(r"^(\s*)- 授权[：:]\s*(.*)$", l)
        if not m:
            continue
        part = [m.group(2).strip()]
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)):
                break
            part.append(nxt.strip())
        out.append(" ".join(x for x in part if x))
    paste = re.search(r"```text\s*\n(.*?)```", block, re.S)
    if paste:
        out += [l.strip() for l in paste.group(1).splitlines() if re.match(r"^\s*授权", l)]
    return "\n".join(out)


def auth_of(task, block, plan_file, plan_fm, doc):
    """运行记录「授权」一节：确认过的执行方案或 PRD 就是授权范围。返回 (授权文本, 来源, 警告)；
    没有任何确认过的方案时返回 ("", "", 警告)，运行记录写 AUTH_DEFAULT。
    来源：任务引用的执行方案（status active）> 引用的 PRD（定稿）> 任务自己的执行说明（计划已确认，或你在任务行写了授权）。
    你在任务行「- 授权：」、开场粘贴「授权」行写的，原样记成「你写明」，和生成的默认值冲突时以它为准。
    跟进 Agent 能不能派生子任务，只看「可以派的角色」「你写明」两种行（tools/follow.py authorized）。"""
    told, warn = explicit_auth(block), ""
    confirmed_plan = str(plan_fm.get("status")) == "active"
    decide, roles, escalate = AUTH_DECIDE, "", ""
    if doc and not doc["confirmed"]:
        warn = f"{doc['kind']} {doc['rel']} 还没确认（status {doc['fm'].get('status')}），不能当授权，先请你确认"
        doc = None
    if doc and doc["kind"] == "执行方案":
        a = md_section(doc["body"], "授权范围")
        pick = lambda k: (re.search(rf"^\s*-\s*{k}[：:]\s*(.+)$", a, re.M) or [None, ""])[1].strip()
        decide, roles, escalate = pick("可以替你定") or decide, pick("可以派的角色"), pick("必须升级")
        source = f"执行方案 {doc['rel']}（你 {doc['fm'].get('confirmed') or doc['fm'].get('updated') or '?'} 确认）"
        scope, accept = md_section(doc["body"], "目标") or task["title"], md_section(doc["body"], "验收")
    elif doc:
        source = f"PRD {doc['rel']}（定稿）"
        scope, accept = "PRD 里的功能，PRD 以外的不做", "PRD 的验收标准逐条通过"
    elif told or (confirmed_plan and (sub_item(block, "目标") or sub_item(block, "验收"))):
        source = (f"{task['id']} 的执行说明（{Path(plan_file).name}，你已确认）" if confirmed_plan
                  else f"{task['id']} 任务行的授权（你写明）")
        scope, accept = sub_item(block, "目标") or task["title"], sub_item(block, "验收")
    else:
        return "", "", warn
    told_roles = any("派" in t for t in told.splitlines())
    lines = [f"执行方案就是授权范围。来源：{source}。下面「你写明」的和生成的默认值冲突时，以你写明的为准",
             f"- 范围：{flat(scope)}",
             f"- 验收：{flat(accept) or '完成目标，并说明怎么验证的'}",
             f"- 可以替你定：{flat(decide)}；依据：{AUTH_BASIS}",
             f"- 可以派的角色：{flat(roles) if roles else ('见「你写明」' if told_roles else '无（要派子任务先升级你）')}",
             f"- 三级判定（task-follow 第 5 节）：{AUTH_TIERS}",
             f"- 必须升级：" + (f"{flat(escalate)}；另外 " if escalate else "") + AUTH_ESCALATE]
    lines += [f"- 你写明：{t}" for t in told.splitlines() if t.strip()]
    return "\n".join(lines), source, warn


def strip_auth(brief):
    """发给执行会话的说明里去掉授权行：授权是给跟进 Agent 的，执行会话按「约束」做。"""
    return "\n".join(l for l in brief.splitlines() if not re.match(r"^\s*授权", l))


def changes_code(task):
    """没有角色时按任务标题粗判会不会改代码：只读类的不算，其余看关键词。"""
    title = task["title"]
    return not READONLY_HINT.search(title) and bool(CODE_HINT.search(title))


def pick_agent(cli_agent, role_fm, task):
    """选执行器，返回 (执行器, 理由)。谁说了算：派发时指定 > 角色默认 > 任务行 exec: > 兜底。"""
    if cli_agent:
        return cli_agent, "派发时指定"
    if role_fm.get("default_executor"):
        return role_fm["default_executor"], f"{role_fm.get('name', '角色')} 的默认"
    if task["exec"] in AGENTS:
        return task["exec"], "任务行 exec:"
    if changes_code(task):
        return CODE_AGENT, "兜底：没有角色也没写执行器，看起来要改代码 → OpenCode"
    return OTHER_AGENT, "兜底：没有角色也没写执行器，不改代码 → Claude Code"


def pick_workspace(cli_new, cli_existing, role_fm, task):
    """Orca 里新建工作区还是用现有目录，返回 (existing, 理由)。会提交改动的新建，只读的用现有目录。"""
    if cli_existing:
        return True, "派发时指定 --existing"
    if cli_new:
        return False, "派发时指定 --new"
    ws, name = role_fm.get("workspace"), role_fm.get("name", "")
    if ws in ("existing", "assistant"):
        return True, f"{name} 只读、不提交代码；要新建工作区加 --new"
    if ws == "new":
        return False, f"{name} 会提交改动，新建工作区（独立分支）隔开更安全；要用现有目录加 --existing"
    if READONLY_HINT.search(task["title"]):
        return True, "兜底：没有角色，任务看起来是只读排查或文档整理 → 现有目录；要新建工作区加 --new"
    return False, "兜底：没有角色，可能会提交改动 → 新建工作区；要用现有目录加 --existing"


def resolve_repo(proj, repo_name=None):
    reg = load_registry()
    entry = next((e for e in reg.get("tracked") or [] if e["slug"] == proj), None)
    if not entry:
        sys.exit(f"registry 里没有工作面 {proj}")
    repos = entry.get("repos") or []
    pick = repos[0]
    if repo_name:  # 仓库名或 owner/repo，大小写不敏感，也认改名前的旧名
        want = norm_remote(repo_name).lower()
        pick = next((r for r in repos if want in repo_keys(r) | {k.split("/")[-1] for k in repo_keys(r)}), None)
        if not pick:
            sys.exit(f"工作面 {proj} 里没有仓库 {repo_name}：可选 " + "、".join(norm_remote(r["github"]) for r in repos))
    gh = norm_remote(pick["github"])
    paths = device.local_paths(gh)
    if len(repos) > 1 and not repo_name:
        print(f"  （{proj} 有 {len(repos)} 个仓库，默认用 {gh}；指定别的用任务行的 repo: 字段或 --repo）")
    return gh, (paths[0] if paths else "")


# ---------- 派发 ----------

def orca(*args):
    stub = common.orca_blocked(args)
    if stub is not None:
        return stub
    exe = shutil.which("orca")
    r = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        return json.loads(r.stdout, strict=False)  # 终端预览里可能带控制字符
    except ValueError:
        return {"ok": False, "error": (r.stderr or r.stdout)[:400]}


def orca_repo_id(path):
    want = os.path.normcase(os.path.normpath(path))
    res = orca("repo", "list").get("result") or {}
    for r in res.get("repos", []) if isinstance(res, dict) else res:
        if os.path.normcase(os.path.normpath(r.get("path", ""))) == want:
            return r["id"]
    return None


def report_types(role_fm):
    """这个角色回报主会话用哪几种消息：产品、规划 Agent 发交接，单个任务只报完成和要拍板。"""
    return ("交接", "要拍板") if role_fm.get("slug") in HANDOFF_ROLES else ("完成", "要拍板")


def followed_note(it):
    """有跟进 Agent 时谁回报：一般执行会话不发；产品、规划 Agent 只自己发交接。"""
    if not it["followed"]:
        return ""
    return "；执行会话只发交接，其余由跟进 Agent 回报" if it["role"][0].get("slug") in HANDOFF_ROLES else "；执行会话不发，由跟进 Agent 回报"


def git_out(path, *args):
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip()


def prepare(task, block, plan_file, agent, mode, existing, repo_name, role=None, base=None, why=None, followed=False, parent=None):
    """派发前把一个任务算清楚：派到哪个仓库、本设备哪个目录、新工作区会在哪、带哪些文件，并做检查。不执行任何操作。
    parent：跟进 Agent 派生子任务时是父运行记录（tools/follow.py --spawn），子运行记录叫 <父>-S<n>.md。"""
    gh, path = resolve_repo(task["proj"], repo_name or task.get("repo"))
    entry = next((r for e in load_registry().get("tracked") or [] if e["slug"] == task["proj"]
                  for r in e.get("repos") or [] if norm_remote(r["github"]) == gh), {})
    role_fm, role_text = role or ({}, "")
    title = re.sub(r"\s+", " ", task["title"])[:40]
    tag = "执行" + (f"·{role_fm.get('name')}" if role_fm else "")
    today = date.today().isoformat()
    it = {
        "task": task, "block": block, "plan_file": plan_file, "gh": gh, "path": path, "self": bool(entry.get("self")),
        "agent": agent, "mode": mode, "existing": existing, "base": base, "role": (role_fm, role_text), "why": why or {},
        "followed": followed, "name": f"[{tag}] {gh.split('/')[-1]} · {task['id']} {title}",
        "run_file": RUNS / (f"{parent.stem}-{task['id'].rsplit('-', 1)[-1]}.md" if parent else f"{today}-{Path(task['plan']).stem}-{task['id']}.md"),
        "parent": parent,
        "wt_name": re.sub(r"[^a-z0-9-]", "", f"{today[5:]}-{task['id']}-{agent}".lower()),
        "errors": [], "warnings": [],
    }
    # 授权 = 确认过的执行方案或 PRD（子任务沿用父任务的，不另算）
    it["doc"] = None if parent else doc_of(block, plan_file)
    it["auth"], it["auth_source"], warn = ("", "沿用父任务", "") if parent else auth_of(
        task, block, plan_file, plan.parse_plan(Path(plan_file))[0], it["doc"])
    if warn:
        it["warnings"].append(warn)
    if not it["auth_source"] and role_fm.get("slug") not in HANDOFF_ROLES:
        it["warnings"].append("没有确认过的执行方案或 PRD：非开发类先派规划 Agent（--role planner）出执行方案，"
                              "开发类先派产品 Agent 出 PRD（dispatch skill 第 0 节）")
    it["files"] = [] if it["self"] else handoff.refs(block, plan_file)
    it["bundle"] = "" if it["self"] else f".assistant/handoff/{Path(task['plan']).stem}-{task['id']}"
    if not path:
        it["errors"].append(f"本设备没有克隆：说「克隆 {gh.split('/')[-1]}」，建议放 {clone.suggest(gh)}")
    if agent not in env.detect(probe=False)["agents_available"]:
        it["errors"].append(f"本机没有 {AGENT_NAMES[agent]}")
    if path:
        if mode == "orca" and not existing:
            if base and git_out(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}")[0]:
                it["errors"].append(f"本地没有分支 {base}")
            it["workdir"] = f"{Path.home().joinpath('orca', 'workspaces', Path(path).name, it['wt_name'])}（预计）"
            it["branch"] = f"新分支，从 {base or 'Orca 默认分支'} 切出"
        else:
            it["workdir"] = path
            it["branch"] = "当前分支 " + (git_out(path, "branch", "--show-current")[1] or "?")
            dirty = len(git_out(path, "status", "--porcelain")[1].splitlines())
            if dirty:
                it["warnings"].append(f"主目录有 {dirty} 个未提交改动，agent 会在这些改动上面工作")
    return it


def build_brief(it, workdir):
    """发给 agent 的任务说明：先讲清在哪做、带了哪些文件，再接执行说明、角色、回报方式。"""
    task, (role_fm, role_text) = it["task"], it["role"]
    head = (f"工作目录：{workdir}（项目 {task['proj']} · 仓库 {it['gh']}）。只在这个目录里工作。"
            f"\n派发标识：assistant {task['plan']}#{task['id']}，派自主会话 {notify.dispatched_by()}（回报、提交信息里要提派发来源时用它）")
    if it["files"]:
        head += (f"\n相关文件的只读副本在 {Path(workdir).as_posix()}/{it['bundle']}/（README 里有来源；原件在 assistant，要改回 assistant 改，不在这里改）："
                 + "、".join(it["files"]))
    if re.match(r"^[\w.-]+/[\w.-]+#\d+$", task.get("issue", "")):
        repo, n = task["issue"].split("#")
        head += f"\n对应 GitHub issue：https://github.com/{repo}/issues/{n}（提交信息写 refs #{n}，PR 里写 Closes #{n}；不要在 issue 里评论或关闭它）"
    brief = head + "\n" + strip_auth(brief_of(task, it["block"], it["doc"])) + "\n" + NAMING
    if (role_fm.get("slug") == "dev" or (not role_fm and changes_code(task))) and "体验" not in brief:
        brief += "\n" + EXPERIENCE
    if role_text:
        brief = role_text + "\n\n【本次任务】\n" + brief
    if it["parent"]:  # 跟进 Agent 派生的子任务：由它来看、来回复，不打扰主会话
        brief += "\n" + CHILD_REPORT
    elif not it["followed"]:
        brief += "\n" + notify.report_brief(it["run_file"], report_types(role_fm))
    elif role_fm.get("slug") in HANDOFF_ROLES:  # 有跟进 Agent 时由它回报；产品、规划 Agent 定稿后的「交接」还是自己发
        brief += "\n" + notify.report_brief(it["run_file"], ("交接",))
    return brief


def short_files(it):
    if it["self"]:
        return "就在本仓库"
    if not it["files"]:
        return "—"
    names = [Path(f).name if Path(f).name not in ("prd.md", "prototype") else f"{Path(f).parent.name}/{Path(f).name}" for f in it["files"]]
    return "、".join(names[:3]) + (f" 等 {len(names)} 个" if len(names) > 3 else "")


def print_plan(items):
    print("\n| # | 任务 | 项目 · 仓库 | 本设备主目录 | 在哪做 | 分支 | 谁做 | 带过去的文件 | 检查 |\n|---|---|---|---|---|---|---|---|---|")
    for i, it in enumerate(items, 1):
        t, (role_fm, _) = it["task"], it["role"]
        where = ("新终端窗口（不在 Orca 里）" if it["mode"] != "orca" else "现有目录" if it["existing"] else "新 Orca 工作区 " + it.get("workdir", ""))
        who = (role_fm.get("name", "") + " · " if role_fm else "") + AGENT_NAMES[it["agent"]]
        check = "❌ " + "；".join(it["errors"]) if it["errors"] else ("⚠ " + "；".join(it["warnings"]) if it["warnings"] else "✅")
        print(f"| {i} | {t['id']} {short(t['title'])} | {t['proj']} · {it['gh']} | {it['path'] or '—'} | {where} | {it.get('branch', '—')} "
              f"| {who} | {short_files(it)} | {check} |")
    bad = [it for it in items if it["errors"]]
    if bad:
        print(f"\n❌ 有 {len(bad)} 个任务检查不过，不会派发（其余照常）：" + "；".join(f"{it['task']['id']} {it['errors'][0]}" for it in bad))
    print("\n说明：")
    for it in items:
        print(f"- {it['task']['id']}：执行器 {AGENT_NAMES[it['agent']]}（{it['why'].get('agent', '')}）；"
              + (f"在哪做（{it['why'].get('workspace', '')}）；" if it["mode"] == "orca" else "")
              + ("回报：派生它的跟进 Agent 来看，不回报主会话；授权：沿用父任务" if it["parent"] else
                 f"回报：{notify.report_line(notify.report_to())}" + followed_note(it)
                 + "；跟进：" + ("配跟进 Agent" if it["followed"] else "不配（--no-follow）")
                 + "；授权：" + (it["auth_source"] or "没有确认过的执行方案或 PRD（按 task-follow 第 5 节默认边界，不能派生子任务）")))


def short(text, n=24):
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[:n] + "…"


def slot_note(items):
    """本机推进位占用：只数本机有会话开着的派发，别的设备上的不占本机位（dispatch skill「推进位」）。
    items 是本次方案里的任务；已经在推进位里的不重复计。"""
    import sweep  # 延迟导入：sweep 依赖本模块的 orca
    rows = sweep.local_slots()
    line = sweep.slot_line(rows, sweep.devices_in_use(), live_known=env.detect()["orca_reachable"])
    running = {r["run"] for r in rows}
    new = [it for it in items if not it["errors"] and it["run_file"].stem not in running]
    line += f"；本方案新增 {len(new)} 件，派完 {len(rows) + len(new)}/{sweep.MAX_SLOTS}"
    if len(rows) + len(new) > sweep.MAX_SLOTS:
        line += f"。⚠ 超过 {sweep.MAX_SLOTS} 件：新事项进今日计划「推进位与排队」，本机满 {sweep.MAX_SLOTS} 件不派"
    return line


def write_run(it, record, brief):
    task, (role_fm, _) = it["task"], it["role"]
    rt, today, parent = notify.report_to(), date.today().isoformat(), it["parent"]
    if parent:
        report = f"- 回报：子任务，由派生它的跟进 Agent 看（父任务 [{parent.stem}]({parent.name})），不回报主会话"
        auth = f"子任务：沿用父任务 [{parent.stem}]({parent.name}) 的「授权」。由父任务的跟进 Agent 跟进，按三级判定处理，红线由它升级你。"
    else:
        report = f"- 回报：{notify.report_line(rt)}" + followed_note(it)
        auth = it["auth"] or AUTH_DEFAULT
    RUNS.mkdir(exist_ok=True)
    it["run_file"].write_text("\n".join([
        "---", "type: run", f"slug: {it['run_file'].stem}", "status: dispatched", f"created: {datetime.now():%Y-%m-%d %H:%M}",
        f"updated: {today}", f"task: {task['plan']}#{task['id']}", f"project: {task['proj']}", f"repo: {it['gh']}",
        f"agent: {it['agent']}", f"role: {role_fm.get('slug', '')}", f"mode: {record['mode']}", f"target: '{record['target']}'",
        f"workdir: '{Path(record['workdir']).as_posix()}'", f"handle: '{record['handle']}'", f"device: {device.fingerprint()}",
        f"report_to: '{rt['handle']}'", f"report_session: '{rt['session']}'", f"dispatched_by: '{notify.dispatched_by(rt)}'", *([f"parent: '{parent.stem}'"] if parent else []),
        f'summary: "{it["name"]}（{it["agent"]}，{record["mode"]}）"', "---", "", f"# {it['name']}", "",
        "## 任务说明", "", brief, "", "## 授权", "",
        "<!-- 你给跟进 Agent 的授权（没配跟进时给主会话）：从确认过的执行方案或 PRD 生成，你写明的优先。"
        "按三级判定处理（有推荐、可回退、范围和验收不变直接做并记「决策记录」；中等报主会话；红线问你本人），见 task-follow 第 5 节；"
        "能不能派生子任务看「可以派的角色」「你写明」。执行会话不看这一节，它按「约束」做 -->",
        auth, "", "## 派发", "",
        "| 项目 · 仓库 | 工作目录 | 分支 | 带过去的文件 |", "|---|---|---|---|",
        f"| {task['proj']} · {it['gh']} | {Path(record['workdir']).as_posix()} | {record['branch']} | "
        + ("就在本仓库" if it["self"] else ("、".join(it["files"]) + f"（副本在 {it['bundle']}/，assistant 提交 {handoff.assistant_commit()}）" if it["files"] else "—")) + " |", "",
        f"- {datetime.now():%Y-%m-%d %H:%M} — {env.summary(env.detect())}", f"- 命令：{record['command']}",
        report, "",
        "## 跟进说明", "", *([f"由父任务 [{parent.stem}]({parent.name}) 的跟进 Agent 跟进（Orca 终端 {rt['handle'] or '—'}），不另配跟进 Agent", ""] if parent else []),
        "## 决策记录",
        "<!-- 跟进 Agent 替你做的决定：- YYYY-MM-DD HH:MM — 问题 → 选择 → 理由（备选：…） -->", "",
        "## 子任务",
        "<!-- 跟进 Agent 按授权派生的子任务（tools/follow.py --spawn 自动写）：- YYYY-MM-DD HH:MM — [S<n>](<子运行记录>) 角色 · 执行器 · 仓库 · 标题 -->", "",
        "## 监督", "", "## 验收", ""]), encoding="utf-8")


def put_bundle(it, workdir):
    if it["files"]:
        handoff.copy_bundle(it["files"], Path(workdir) / it["bundle"], f"{it['task']['plan']}#{it['task']['id']} {it['task']['title']}")
        handoff.exclude(workdir)


def execute(it):
    """真正派发一个已检查通过的任务，返回 record（工作目录、handle 等）；失败返回 None。"""
    task, path, agent, name = it["task"], it["path"], it["agent"], it["name"]
    record = {"mode": "", "target": "", "handle": "", "command": "", "workdir": path, "branch": it.get("branch", "")}
    comment = f"assistant {task['plan']}#{task['id']} · 派自 {notify.dispatched_by()}"
    if it["mode"] == "orca":
        repo_id = orca_repo_id(path)
        if not repo_id:
            orca("repo", "add", "--path", path)
            repo_id = orca_repo_id(path)
        if it["existing"]:
            record.update(mode="orca-existing", target=path)
        else:
            res = orca("worktree", "create", "--repo", f"id:{repo_id}", "--name", it["wt_name"], "--no-parent",
                       *(["--base-branch", it["base"]] if it["base"] else []), "--setup", "skip", "--comment", comment)
            wt = (res.get("result") or {}).get("worktree") or {}
            wt_path = wt.get("path") or next((w.get("path") for w in (orca("worktree", "ps", "--limit", "200").get("result") or {}).get("worktrees", [])
                                              if w.get("worktreeId") == wt.get("id")), "")
            if not res.get("ok") or not wt_path:
                print(f"- ❌ {task['id']} 建工作区失败：{str(res.get('error'))[:300]}")
                return None
            orca("worktree", "set", "--worktree", f"id:{wt['id']}", "--display-name", name, "--comment", comment)
            record.update(mode="orca-new-worktree", target=wt.get("id", ""), workdir=wt_path,
                          branch=git_out(wt_path, "branch", "--show-current")[1] + f"（从 {it['base'] or 'Orca 默认分支'} 切出）")
        put_bundle(it, record["workdir"])
        brief = build_brief(it, Path(record["workdir"]).as_posix())
        record["command"] = f"orca agent launch --agent {agent} --worktree path:{Path(record['workdir']).as_posix()}"
        write_run(it, record, brief)
        res = orca("agent", "launch", "--agent", agent, "--worktree", f"path:{Path(record['workdir']).as_posix()}", "--prompt", brief)
        result = res.get("result") or {}
        record["handle"] = ((result.get("launch") or result).get("outcome") or {}).get("handle", "")
        if record["handle"]:
            orca("terminal", "rename", "--terminal", record["handle"], "--title", name)
        if not res.get("ok"):
            print(f"- ❌ {task['id']} 启动 {agent} 失败：{str(res.get('error'))[:300]}")
            return None
        text = it["run_file"].read_text(encoding="utf-8")
        it["run_file"].write_text(re.sub(r"^handle: .*$", f"handle: '{record['handle']}'", text, count=1, flags=re.M), encoding="utf-8")
        return record
    ask = f"请先读 {it['run_file'].as_posix()} 的「任务说明」一节，然后按它执行。"
    cmd = {"claude": f'claude -n "{name}" "{ask}"', "codex": f'codex "{ask}"'}.get(agent, f'opencode run --title "{name}" "{ask}"')
    record.update(mode="local-window", target=path, command=cmd)
    put_bundle(it, path)
    write_run(it, record, build_brief(it, Path(path).as_posix()))  # 本地模式 agent 去读运行记录，要先写
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", "powershell", "-NoExit", "-Command", cmd], cwd=path)
    else:
        print(f"  非 Windows：请在新终端里执行 {cmd}")
    return record


FOLLOW_ROLE = "pm"  # 跟进 Agent（agents/pm.md），固定用 Claude Code；所有派发默认配一个


def follow(task, run_file, mode, do_run, worker_name, role_slug=""):
    """给已派发的任务配一个跟进 Agent：在 assistant 工作区启动，盯执行会话、在授权（执行方案 / PRD）范围内替你决策、完成后汇报。"""
    fm, role_text = load_role(FOLLOW_ROLE)
    name = f"[跟进] {worker_name.split('] ', 1)[-1]}"
    print(f"- 跟进：{fm.get('name')}（Claude Code），名字：{name}" + ("（角色状态 draft，先看角色说明）" if fm.get("status") == "draft" else ""))
    if not do_run:
        print(f"  将执行：在 assistant（{ROOT}）启动 claude，读运行记录里的「跟进说明」和「授权」，每 5 分钟检查一次执行会话")
        return
    text = run_file.read_text(encoding="utf-8")
    g = lambda k: (re.search(rf"^{k}:\s*'?([^'\n]*)'?", text, re.M) or [None, ""])[1]
    rel = run_file.relative_to(ROOT).as_posix()
    guide = "\n".join([
        f"跟进对象：{task['plan']}#{task['id']} {task['title']}",
        f"运行记录：{rel}（先读其中的「任务说明」，那就是执行 agent 拿到的任务）",
        f"执行会话：{g('agent')}，方式 {g('mode')}，目标 {g('target') or '—'}，Orca 终端 handle {g('handle') or '（本地模式没有）'}",
        f"怎么跟进：按 task-follow skill 做（{ROOT.as_posix()}/.claude/skills/task-follow/SKILL.md），大约每 5 分钟检查一次；"
        f"看状态用 python tools/follow.py {rel}",
        "授权：运行记录「授权」一节是从你确认过的执行方案或 PRD 生成的边界，按三级判定处理（task-follow 第 5 节）——"
        f"有推荐、可回退、范围和验收不变直接做、记「决策记录」（偏好见 {ROOT.as_posix()}/profile/preferences.md，开始前读一遍）；"
        "中等（要判断偏好但不敏感、跨任务、顺序协调）发「要拍板」，正文开头写「中等：」，一句话 + 推荐 + 备选，主会话定；"
        "红线（钱、权益、密钥、账号权限、不可逆、对外发布、改目标 / 范围 / 验收、删数据）发「要拍板」，开头写「红线：」，主会话出卡片问你；"
        "「可以派的角色」「你写明」里有的角色才能派生子任务："
        f"python tools/follow.py {rel} --spawn --role <角色> --title … --brief …（先不加 --run 看方案，task-follow 第 7 节）",
        *(["执行会话在和你本人对话（产品 / 规划 Agent）：问卷、评审、方案确认是你本人的事，不替你答；"
           "定稿后它自己发「交接」，你不重复发，只在它卡住、跑偏、超出授权时处理"] if role_slug in HANDOFF_ROLES else []),
        "记录：进展记进「监督」，你替你做的决定记进「决策记录」，结束时写「验收」并改 status",
        "回报对象：" + notify.report_line({"handle": g("report_to"), "session": g("report_session")}),
        notify.report_brief(run_file, ("完成", "要拍板", "卡住")),
        "完成、失败（报「完成」并写清结果）、第 2 / 3 级（报「要拍板」，开头标「中等：」/「红线：」）、所有线 30 分钟以上叫不动（报「卡住」）时才发；"
        "你会话里照样留一份完整汇报（先结论，再进展表，再建议）。第 1 级的决定只记「决策记录」，不发消息",
    ])
    run_file.write_text(text.replace("## 跟进说明\n", "## 跟进说明\n\n" + guide + "\n", 1), encoding="utf-8")
    prompt = role_text + "\n\n【本次任务】\n" + guide
    if mode == "orca":
        res = orca("agent", "launch", "--agent", "claude", "--worktree", f"path:{ROOT.as_posix()}", "--prompt", prompt)
        result = res.get("result") or {}
        handle = ((result.get("launch") or result).get("outcome") or {}).get("handle", "")
        if handle:
            orca("terminal", "rename", "--terminal", handle, "--title", name)
        print(("  ✅ 跟进 Agent 已启动：" if res.get("ok") else "  ❌ 跟进 Agent 启动失败：") + (handle or json.dumps(res.get("error"), ensure_ascii=False)[:200]))
        run_file.write_text(run_file.read_text(encoding="utf-8").replace(
            "## 决策记录", f"- 跟进 Agent：Orca 终端 handle {handle or '?'}\n\n## 决策记录", 1), encoding="utf-8")
    else:
        cmd = f'claude -n "{name}" "请先读 {run_file.as_posix()} 的「跟进说明」和「任务说明」，然后按 {ROOT.as_posix()}/agents/pm.md 的角色和说明跟进。"'
        print(f"  将执行：在 {ROOT} 开新终端窗口运行 {cmd}")
        if os.name == "nt":
            subprocess.Popen(["cmd", "/c", "start", "", "powershell", "-NoExit", "-Command", cmd], cwd=str(ROOT))


def mark_task(plan_file, tid, run_file, workdir=""):
    """回填任务行的 run: 字段，并在任务下记一条进展（写上工作目录）。"""
    p = Path(plan_file)
    lines = p.read_text(encoding="utf-8").splitlines()
    rel = run_file.relative_to(ROOT).as_posix()
    for i, l in enumerate(lines):
        if re.match(rf"^- \[.\] {tid} ", l):
            l = re.sub(r" · run:\S+", "", l) + f" · run:{run_file.stem}"
            lines[i] = re.sub(r"^- \[ \]", "- [~]", l)
            where = f"，工作目录 `{Path(workdir).as_posix()}`" if workdir else ""
            lines.insert(i + 1, f"  - 进展：已派发{where}，见 [{run_file.stem}]({os.path.relpath(ROOT / rel, Path(plan_file).parent).replace(os.sep, '/')})（{date.today()}）")
            break
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def status():
    files = sorted((f for f in RUNS.glob("*.md") if f.name != "pending.md"), reverse=True) if RUNS.is_dir() else []
    if not files:
        print("还没有派发记录。")
        return
    ps = {}
    if env.detect()["orca_reachable"]:
        for w in (orca("worktree", "ps").get("result") or {}).get("worktrees", []):
            ps[w.get("worktreeId")] = w
    for f in files[:20]:
        text = f.read_text(encoding="utf-8")
        g = lambda k: (re.search(rf"^{k}:\s*'?([^'\n]*)'?", text, re.M) or [None, ""])[1]
        w = ps.get(g("target"))
        live = ""
        if w:
            live = "；Orca：" + ("有活动" if w.get("hasHostSidebarActivity") else "无活动") + f"，分支 {w.get('branch', '').replace('refs/heads/', '')}"
        print(f"- [{g('status')}] {g('task')} · {g('agent')} · {g('mode')}{live} —— {f.relative_to(ROOT).as_posix()}")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="任务派发")
    ap.add_argument("--plan", help="计划文件，默认今天的当日计划")
    ap.add_argument("--task", action="append", help="任务号，可重复；默认全部可派发的任务")
    ap.add_argument("--agent", choices=AGENTS, help="执行器，覆盖角色默认和任务行的 exec:")
    ap.add_argument("--role", help="派发的角色（agents/<role>.md），会注入角色设定")
    ap.add_argument("--mode", choices=["auto", "orca", "local"], default="auto")
    ap.add_argument("--existing", action="store_true", help="Orca 模式下在项目现有目录启动，不新建工作区（覆盖角色默认）")
    ap.add_argument("--new", action="store_true", help="Orca 模式下新建工作区（覆盖角色默认）")
    ap.add_argument("--repo", help="多仓库工作面里用哪个仓库（仓库名或 owner/repo；覆盖任务行的 repo:）")
    ap.add_argument("--base", help="Orca 新工作区从哪个分支切（默认由 Orca 决定；本地有未推送提交时用它指定本地分支）")
    ap.add_argument("--run", action="store_true", help="真正派发；不加只出方案")
    ap.add_argument("--follow", action="store_true", help="配跟进 Agent（现在所有派发默认就配，保留这个参数兼容旧用法）")
    ap.add_argument("--no-follow", action="store_true", help="不配跟进 Agent")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
        return
    plan_file = Path(args.plan) if args.plan else ROOT / "plans" / "daily" / f"{date.today()}.md"
    if not plan_file.is_absolute():
        plan_file = ROOT / plan_file
    if not plan_file.exists():
        sys.exit(f"找不到计划文件 {plan_file}")
    fm, tasks = plan.parse_plan(plan_file)
    if plan_file.parent.name == "daily" and str(fm.get("status")) != "active":
        print(f"⚠ {plan_file.name} 还是草案（status: {fm.get('status')}），派发前要先确认今日事项")
    info = env.detect()
    mode = args.mode if args.mode != "auto" else ("orca" if info["orca_reachable"] else "local")
    print(env.summary(info))
    picked = [t for t in tasks if t["state"] in ("待办", "进行中") and t["proj"]
              and (not args.task or t["id"] in args.task)]
    if not args.task:
        picked = [t for t in picked if t["exec"] in AGENTS]
    if not picked:
        print("没有可派发的任务：任务要有 proj:，并且 exec: 是 claude / codex / opencode（或者用 --task 加 --agent 指定）")
        return
    print(f"\n# 派发方案（{'执行' if args.run else '只看方案，加 --run 才派发'}）")
    role = load_role(args.role) if args.role else None
    if role and role[0].get("status") == "draft":
        print(f"⚠ 角色 {args.role} 的状态是 draft（未经复核）：拿不准时先看角色说明 agents/{args.role}.md")
    items = []
    for t in picked:
        role_fm = role[0] if role else {}
        agent, why_agent = pick_agent(args.agent, role_fm, t)
        existing, why_ws = pick_workspace(args.new, args.existing, role_fm, t)
        # 所有派发默认同时派一个跟进 Agent；--no-follow 不派；派跟进 Agent 本身不再配跟进
        wants = not args.no_follow and not (role and role[0].get("slug") == FOLLOW_ROLE)
        items.append(prepare(t, task_block(plan_file, t["id"]), plan_file, agent, mode, existing, args.repo, role, args.base,
                             {"agent": why_agent, "workspace": why_ws}, followed=wants))
    print()
    print(slot_note(items))
    print_plan(items)
    if not args.run:
        for it in items:
            if it["followed"]:
                follow(it["task"], None, mode, False, it["name"])
        return
    done = []
    for it in items:
        if it["errors"]:
            continue
        record = execute(it)
        if not record:
            continue
        mark_task(plan_file, it["task"]["id"], it["run_file"], record["workdir"])
        done.append((it, record))
        if it["followed"]:
            follow(it["task"], it["run_file"], mode, True, it["name"], it["role"][0].get("slug", ""))
    print("\n# 已派发\n\n| 任务 | 项目 · 仓库 | 工作目录 | 分支 | 会话 | 运行记录 |\n|---|---|---|---|---|---|")
    for it, r in done:
        print(f"| {it['task']['id']} {short(it['task']['title'])} | {it['task']['proj']} · {it['gh']} | {Path(r['workdir']).as_posix()} "
              f"| {r['branch']} | {r['handle'] or r['mode']} | {it['run_file'].relative_to(ROOT).as_posix()} |")
    skipped = [it["task"]["id"] for it in items if it not in [d[0] for d in done]]
    if skipped:
        print(f"\n没派的：{'、'.join(skipped)}（见上面的检查）")

if __name__ == "__main__":
    main()
