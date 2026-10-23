r"""
开发交付流水线 —— PRD 定稿、你说「确认派发」后，从规划到合回 develop 的派发和调度。

主会话用（先看方案，你确认后加 --run）：
    python tools/delivery.py start <项目> <需求slug> --prd plans/prd/<slug>/prd.md [--repo 仓库名] [--run]
        从 develop 切需求集成分支 req/<需求>（Orca 工作区），复制 PRD，建交付看板 runs/<日期>-<项目>-<需求>.md，
        启动：开发 Agent（dev-plan 写实现计划）、验收 Agent（accept-cases 写用例）、跟进 Agent（在 assistant 里）

跟进 Agent 用（直接执行，--dry 只看不做）：
    python tools/delivery.py status  <看板>                    # 全部任务线现状 + 可以派的包
    python tools/delivery.py approve <看板>                    # 实现计划和用例审过：提交到需求分支，工作包读进看板
    python tools/delivery.py pkg     <看板> P1                 # 开包：从需求分支切 pkg/<需求>-P1 工作区，启动开发（dev-build）
    python tools/delivery.py launch  <看板> --at P1 --skill accept-review [--note "…"]   # 在已有工作区启动一个会话
    python tools/delivery.py send    <看板> --to P1 "…"        # 给会话发话（--to：包号 / dev@req / accept@P1 / 终端 handle）
    python tools/delivery.py peek    <看板> --to P1            # 看会话当前一屏
    python tools/delivery.py allow   <看板> [--to P1]          # 处理授权框：不敏感的放行，敏感的列出来升级（不带 --to 扫全部会话）
    python tools/delivery.py set     <看板> P1 待审 [--note "…"]
    python tools/delivery.py merge   <看板> P1                 # 审查通过的包合进需求分支（--no-ff）
    python tools/delivery.py sync    <看板>                    # 把最新的 develop 合进需求分支（合回前，之后要重测）
    python tools/delivery.py finish  <看板>                    # 快进合回 develop（本地，不推送），看板结束
    python tools/delivery.py log     <看板> "…"   /   decide <看板> "问题 → 选择 → 理由（备选：…）"

工作包状态：待派 → 开发中 → 待审 → 审查中 → 待修 / 已通过 → 已合入（或 放弃）。同时最多 3 条线在 开发中~待修。
只有一个包的需求不切包分支，直接在需求分支上开发。
回报：start 时把当前会话（主会话）记为看板的 report_to；跟进 Agent 只在 完成 / 要拍板 / 卡住 时用 tools/notify.py 回报主会话
（先记待处理清单，再推一行消息；playbooks/comms.md）。开发、验收会话只和跟进 Agent 说话。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import clone
import device
import env
import handoff
import notify
from common import ROOT, load_registry, norm_remote, setup_stdio
from dispatch import NAMING, load_role, orca, orca_repo_id, resolve_repo
from follow import allow, append, fields, stamp_closed

RUNS = ROOT / "runs"
MAX_LINES = 3
ACTIVE = ("开发中", "待审", "审查中", "待修")
STATES = ("待派", "开发中", "待审", "审查中", "待修", "已通过", "已合入", "放弃")
COLS = ["包", "状态", "依赖", "分支", "工作区", "会话", "审查轮次", "更新"]
SKILL_ROLE = {"dev-plan": "dev", "dev-build": "dev", "dev-fix": "dev",
              "accept-cases": "accept", "accept-review": "accept", "accept-test": "accept"}
EXEC = {"dev": "opencode", "accept": "codex"}
ROLE_NAME = {"dev": "开发", "accept": "验收"}
ASSISTANT = ROOT.as_posix()


def now():
    return f"{datetime.now():%Y-%m-%d %H:%M}"


def git(path, *args):
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


# ---------- 看板 ----------

def board_path(arg):
    p = Path(arg)
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        sys.exit(f"找不到交付看板 {p}")
    return p


def set_field(text, key, value):
    return re.sub(rf"^{key}:.*$", f"{key}: '{value}'" if re.search(r"[:#]", str(value)) else f"{key}: {value}", text, count=1, flags=re.M)


def read_rows(text):
    m = re.search(r"^## 工作包\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    rows = []
    for l in (m.group(1) if m else "").splitlines():
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if l.startswith("|") and len(cells) == len(COLS) and cells[0] not in ("包",) and not set(cells[0]) <= set("-: "):
            rows.append(dict(zip(COLS, cells)))
    return rows


def write_rows(board, rows):
    text = board.read_text(encoding="utf-8")
    table = "| " + " | ".join(COLS) + " |\n|" + "---|" * len(COLS) + "\n" + "".join(
        "| " + " | ".join(r.get(c, "") or "" for c in COLS) + " |\n" for r in rows)
    m = re.search(r"^## 工作包\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    keep = "\n".join(l for l in m.group(1).splitlines() if l.startswith("<!--")) if m else ""
    keep = keep + "\n" if keep else ""
    new = "## 工作包\n" + keep + table + "\n"
    text = text[:m.start()] + new + text[m.end():] if m else text.rstrip() + "\n\n" + new
    text = re.sub(r"^updated: .*$", f"updated: {date.today()}", text, count=1, flags=re.M)
    board.write_text(text, encoding="utf-8")


def set_stage(board, stage):
    text = board.read_text(encoding="utf-8")
    board.write_text(re.sub(r"^- 当前：.*$", f"- 当前：{stage}", text, count=1, flags=re.M), encoding="utf-8")


def find_row(rows, pid):
    row = next((r for r in rows if r["包"].upper() == pid.upper()), None)
    if not row:
        sys.exit(f"看板里没有工作包 {pid}（先 approve 把实现计划的工作包读进来）")
    return row


def deps_of(row):
    return [d.strip().upper() for d in re.split(r"[,，、\s]+", row.get("依赖", "")) if d.strip() and d.strip() not in ("—", "-")]


def ready(rows):
    active = sum(1 for r in rows if r["状态"] in ACTIVE)
    merged = {r["包"].upper() for r in rows if r["状态"] in ("已合入", "放弃")}
    cand = [r["包"] for r in rows if r["状态"] == "待派" and all(d in merged for d in deps_of(r))]
    return cand[:max(0, MAX_LINES - active)], active


def ctx(board):
    f = fields(board.read_text(encoding="utf-8"))
    f["dir"] = f"docs/delivery/{f.get('req')}"
    return f


# ---------- Orca ----------

def need_orca():
    if not env.detect()["orca_reachable"]:
        sys.exit("开发交付流水线只在 Orca 里派：现在连不上 Orca")


def new_worktree(repo_path, name, base, branch, display, comment):
    """从 base 切一个 Orca 工作区，分支改名为 branch，返回本地路径。"""
    rid = orca_repo_id(repo_path)
    if not rid:
        orca("repo", "add", "--path", repo_path)
        rid = orca_repo_id(repo_path)
    res = orca("worktree", "create", "--repo", f"id:{rid}", "--name", name, "--base-branch", base,
               "--no-parent", "--setup", "skip", "--comment", comment)
    if not res.get("ok"):
        sys.exit(f"❌ 建工作区失败：{str(res.get('error'))[:300]}")
    wt = (res.get("result") or {}).get("worktree") or {}
    path = wt.get("path")
    if not path:
        rows = (orca("worktree", "list", "--repo", f"id:{rid}").get("result") or {})
        rows = rows.get("worktrees", rows) if isinstance(rows, dict) else rows
        path = next((w["path"] for w in rows if w.get("path", "").rstrip("/").endswith("/" + name)), "")
    if not path:
        sys.exit("❌ 工作区建好了但找不到路径，去 Orca 里看一下")
    code, out = git(path, "branch", "-m", branch)
    if code:
        print(f"  ⚠ 分支改名失败，沿用 Orca 的分支名：{out[:200]}")
        branch = git(path, "branch", "--show-current")[1]
    orca("worktree", "set", "--worktree", f"path:{path}", "--display-name", display)
    return path, branch


def launch_agent(agent, path, prompt, title):
    res = orca("agent", "launch", "--agent", agent, "--worktree", f"path:{path}", "--prompt", prompt)
    result = res.get("result") or {}
    handle = ((result.get("launch") or result).get("outcome") or {}).get("handle", "")
    if handle:
        orca("terminal", "rename", "--terminal", handle, "--title", title)
    if not res.get("ok"):
        print(f"  ❌ 启动 {agent} 失败：{str(res.get('error'))[:300]}")
    return handle


# ---------- 会话 ----------

def task_text(skill, f, at, row, note, board):
    d, req, rb = f["dir"], f.get("req"), f.get("req_branch")
    sk = f"{ASSISTANT}/.claude/skills/{skill}/SKILL.md"
    tpl = lambda n: f"{ASSISTANT}/templates/{n}"
    head = f"需求：{req}（{f.get('summary', '')}）。产物目录 {d}/，PRD 是 {d}/prd.md（唯一基准）。按 {skill} skill 做：{sk}"
    if skill == "dev-plan":
        body = f"写实现计划 {d}/impl-plan.md（模板 {tpl('impl-plan.md')}）。不要提交；写完在终端里说「实现计划写好了」，等跟进 Agent 审。"
    elif skill == "accept-cases":
        body = f"写验收用例 {d}/cases.md（模板 {tpl('cases.md')}）。只看 PRD，不看实现计划和代码。不要提交；写完在终端里说「用例写好了」。"
    elif skill == "dev-build":
        body = (f"做工作包 {at}：内容、对应 AC、依赖、文件归属见 {d}/impl-plan.md 的「工作包」表 {at} 那一行；"
                f"进度文件 {d}/packages/{at}.md（没有就按模板 {tpl('package.md')} 建）。在当前分支 {row.get('分支') or rb} 上小步提交；"
                f"自测完成后在终端里说「{at} 可以审查了」。")
    elif skill == "dev-fix":
        body = f"修复「范围内要修」的问题：{note or '见最新的审查报告 / 测试报告'}。修完自测、提交，在终端里说「修好了」。"
        note = ""
    elif skill == "accept-review":
        n = row.get("审查轮次") or "1"
        body = (f"审查工作包 {at} 第 {n} 轮：改动是 `git diff {rb}...HEAD`（当前分支 {row.get('分支')}），对应 AC 见 {d}/impl-plan.md。"
                f"报告写 {d}/reviews/{at}-r{n}.md（模板 {tpl('review-report.md')}）并提交；在终端里说结论（通过 / 要修 N 条）。")
        if row.get("分支") == rb:
            body = body.replace(f"`git diff {rb}...HEAD`", f"`git diff {f.get('dev_branch')}...HEAD`")
    elif skill == "accept-test":
        n = sum(1 for l in board.read_text(encoding="utf-8").splitlines() if "accept/accept-test" in l) + 1
        body = (f"在需求分支 {rb} 上按 {d}/cases.md 测试，第 {n} 轮：报告写 {d}/acceptance/report-r{n}.md（模板 {tpl('test-report.md')}），"
                f"截图放 {d}/acceptance/shots/；提交报告、截图和 e2e 脚本；在终端里说结论（通过 / 失败 N 条）。")
    else:
        sys.exit(f"不认识的 skill {skill}：可选 " + "、".join(SKILL_ROLE))
    tail = "约束：遵守角色设定里的「禁止」；拿不准的在终端里问，跟进 Agent 会回复你；不要关闭会话。\n" + NAMING
    return "\n".join(x for x in [head, body, f"补充：{note}" if note else "", tail] if x)


def launch(board, at, skill, note="", dry=False):
    f = ctx(board)
    role = SKILL_ROLE.get(skill) or sys.exit(f"不认识的 skill {skill}")
    rows = read_rows(board.read_text(encoding="utf-8"))
    row = {} if at == "req" else find_row(rows, at)
    path = f.get("req_worktree") if at == "req" else row.get("工作区")
    if not path:
        sys.exit(f"{at} 还没有工作区（先 pkg {at}）")
    if skill == "accept-review" and row:
        row["审查轮次"] = str(int(row.get("审查轮次") or 0) + 1)
    brief = load_role(role)[1] + "\n\n【本次任务】\n" + task_text(skill, f, at, row, note, board)
    title = f"[{ROLE_NAME[role]}·{skill}] {f.get('req')} · {at}"
    print(f"- 启动 {EXEC[role]}（{role} / {skill}）@ {at}：{path}")
    if dry:
        print("  （--dry，不执行）")
        return ""
    handle = launch_agent(EXEC[role], path, brief, title)
    append(board, "会话", f"- {now()} — {role}/{skill} @{at} · {handle or '?'}")
    if row:
        row["会话"] = handle or row.get("会话", "")
        row["状态"] = {"dev-build": "开发中", "dev-fix": "开发中", "accept-review": "审查中"}.get(skill, row["状态"])
        row["更新"] = str(date.today())
        write_rows(board, rows)
    print(f"  ✅ {handle}" if handle else "  ⚠ 没拿到终端 handle，去 Orca 里看一下")
    return handle


def resolve_handle(board, to):
    if to.startswith("term_"):
        return to
    text = board.read_text(encoding="utf-8")
    if "@" in to:
        role, at = to.split("@", 1)
        hits = re.findall(rf"— {re.escape(role)}/[\w-]+ @{re.escape(at)} · (\S+)", text)
        if hits:
            return hits[-1]
        sys.exit(f"看板「会话」里没有 {to}")
    h = find_row(read_rows(text), to).get("会话")
    return h or sys.exit(f"{to} 还没有会话")


# ---------- 命令 ----------

def cmd_start(a):
    need_orca()
    gh, path = resolve_repo(a.project, a.repo)
    if not path:
        sys.exit(f"❌ 本设备没有 {gh} 的克隆：说「克隆 {gh.split('/')[-1]}」，建议放 {clone.suggest(gh)}（python tools/clone.py {gh} --run）")
    entry = next(e for e in load_registry().get("tracked") or [] if e["slug"] == a.project)
    rep = next((r for r in entry.get("repos") or [] if norm_remote(r["github"]) == gh), {})
    if handoff.is_team(gh, rep) and not a.force:
        sys.exit(f"❌ {gh} 是团队仓库：团队项目按团队自己的流程走，不进开发交付流水线；确实要跑加 --force")
    br = {"main": "main", "dev": "develop", **(rep.get("branches") or {})}
    prd = Path(a.prd) if Path(a.prd).is_absolute() else ROOT / a.prd
    if not prd.exists():
        sys.exit(f"找不到 PRD {prd}")
    slug, today = a.req, date.today().isoformat()
    req_branch, d = f"req/{slug}", f"docs/delivery/{slug}"
    board = RUNS / f"{today}-{a.project}-{slug}.md"
    repo_name = gh.split("/")[-1]
    print(f"# 开发交付：{a.project} · {slug}（{'执行' if a.run else '只看方案，加 --run 才执行'}）")
    print(f"- 仓库 {gh}，本设备 {path}；分支约定 main={br['main']} dev={br['dev']}")
    print(f"- 从 {br['dev']} 切 Orca 工作区 req-{slug}，分支 {req_branch}；PRD 复制到 {d}/prd.md")
    print(f"- 交付看板 {board.relative_to(ROOT).as_posix()}")
    print("- 启动：开发 Agent（opencode · dev-plan）、验收 Agent（codex · accept-cases），都在需求工作区；跟进 Agent（claude）在 assistant")
    if git(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{br['dev']}")[0]:
        sys.exit(f"❌ 本地没有 {br['dev']} 分支：请你先建好并推送（git switch -c {br['dev']} {br['main']} && git push -u origin {br['dev']}）")
    if not git(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{req_branch}")[0]:
        sys.exit(f"❌ 分支 {req_branch} 已经存在：换一个需求 slug，或者先处理旧的")
    if board.exists():
        sys.exit(f"❌ 看板已存在：{board}")
    rt = notify.report_to()
    print(f"- 回报：{notify.report_line(rt)}")
    if not a.run:
        return
    wt, req_branch = new_worktree(path, f"req-{slug}", br["dev"], req_branch, f"[交付] {repo_name} · {slug}", f"assistant 交付 {slug} · 派自 {notify.dispatched_by()}")
    dst = Path(wt) / d
    dst.mkdir(parents=True, exist_ok=True)
    src_rel = prd.relative_to(ROOT).as_posix() if prd.is_relative_to(ROOT) else prd.as_posix()
    (dst / "prd.md").write_text(f"> 副本：原件在 assistant `{src_rel}`（assistant 提交 {handoff.assistant_commit()}）。要改 PRD 回 assistant 改，不在这里改。\n\n"
                                + prd.read_text(encoding="utf-8"), encoding="utf-8")
    if (prd.parent / "prototype").is_dir():
        shutil.copytree(prd.parent / "prototype", dst / "prototype", dirs_exist_ok=True)
    RUNS.mkdir(exist_ok=True)
    board.write_text("\n".join([
        "---", "type: run", "kind: delivery", f"slug: {board.stem}", "status: dispatched", f"created: {now()}",
        f"updated: {today}", f"project: {a.project}", f"repo: {gh}", f"req: {slug}", f"dev_branch: {br['dev']}",
        f"main_branch: {br['main']}", f"device: {device.fingerprint()}", f"req_branch: {req_branch}", f"req_worktree: '{Path(wt).as_posix()}'", "follower: ''", f"dispatched_by: '{notify.dispatched_by()}'",
        f"report_to: '{rt['handle']}'", f"report_session: '{rt['session']}'",
        f"device: '{notify.device_id()}'",  # v1.1：在哪台设备上跑（指挥台据此判断回复能不能转到）
        f'summary: "开发交付 {a.project} · {slug}（{a.title or prd.parent.name}）"', "---", "",
        f"# [交付] {repo_name} · {slug}", "",
        "<!-- 交付看板，tools/delivery.py 创建和更新，跟进 Agent 维护（模板 templates/run-delivery.md）。工作包表的列名不要改。 -->", "",
        "## 需求", f"- 回报：{notify.report_line(rt)}", f"- PRD：{d}/prd.md（来自 assistant {prd.relative_to(ROOT).as_posix() if prd.is_relative_to(ROOT) else prd}）",
        f"- 产物目录：{d}/（需求工作区 {Path(wt).as_posix()}）", "",
        "## 阶段", "<!-- 规划 → 开发 → 集成测试 → 合回 develop → 完成 -->", "- 当前：规划", "",
        "## 工作包", "| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS), "",
        "## 会话", "", "## 决策记录", "", "## 监督", "", "## 体验", "", "## 验收", ""]), encoding="utf-8")
    print(f"- ✅ 需求工作区 {wt}（{req_branch}）")
    launch(board, "req", "dev-plan")
    launch(board, "req", "accept-cases")
    rel = board.relative_to(ROOT).as_posix()
    guide = "\n".join([
        f"跟进对象：{a.project} · {slug} 的开发交付（你已说「确认派发」，到合回 develop 之前中间不再问你）",
        f"交付看板：{rel}（在 assistant {ASSISTANT} 里）；需求工作区 {Path(wt).as_posix()}，分支 {req_branch}，产物目录 {d}/",
        f"现在：开发 Agent 在写实现计划（dev-plan），验收 Agent 在写用例（accept-cases），两个会话都在需求工作区",
        f"怎么做：按 task-follow skill（{ASSISTANT}/.claude/skills/task-follow/SKILL.md），工具 python tools/delivery.py，先跑 status",
        "回报对象：" + notify.report_line(rt),
        notify.report_brief(board, ("完成", "要拍板", "卡住")),
    ])
    title = f"[跟进] {repo_name} · {slug}"
    handle = launch_agent("claude", ASSISTANT, load_role("pm")[1] + "\n\n【本次任务】\n" + guide, title)
    board.write_text(set_field(board.read_text(encoding="utf-8"), "follower", handle), encoding="utf-8")
    append(board, "会话", f"- {now()} — pm/task-follow @assistant · {handle or '?'}")
    print(f"- ✅ 跟进 Agent {handle or '（没拿到 handle）'}\n完成：看板 {rel}")


def cmd_status(a):
    board = board_path(a.board)
    text = board.read_text(encoding="utf-8")
    f = fields(text)
    rows = read_rows(text)
    stage = (re.search(r"^- 当前：(.*)$", text, re.M) or [None, "?"])[1]
    print(f"# {f.get('summary')}\n- 阶段：{stage}；看板 status {f.get('status')}；需求分支 {f.get('req_branch')}")
    if rows:
        print("\n| 包 | 状态 | 依赖 | 审查轮次 | 会话 |\n|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['包']} | {r['状态']} | {r['依赖']} | {r['审查轮次']} | {r['会话']} |")
        cand, active = ready(rows)
        print(f"\n- 进行中 {active} 条线（上限 {MAX_LINES}）；现在可以派：{'、'.join(cand) or '无'}")
    if env.detect()["orca_reachable"]:
        paths = {f.get("req_worktree"): "req"} | {r["工作区"]: r["包"] for r in rows if r.get("工作区")}
        print("\n## 会话现状")
        for w in (orca("worktree", "ps", "--limit", "200").get("result") or {}).get("worktrees", []):
            at = next((v for k, v in paths.items() if k and w.get("path", "").lower() == k.lower()), None)
            if not at:
                continue
            ts = max(w.get("lastOutputAt") or 0, w.get("lastActivityAt") or 0)
            last = datetime.fromtimestamp(ts / 1000).strftime("%H:%M") if ts else "?"
            print(f"- {at}：{w.get('status')}，最后活动 {last}，分支 {w.get('branch', '').replace('refs/heads/', '')}")
            for ag in w.get("agents") or []:
                msg = re.sub(r"\s+", " ", ag.get("lastAssistantMessage") or "")[:300]
                print(f"  - {ag.get('agentType')}：{ag.get('state')}" + (f"；最后一句：{msg}" if msg else ""))


def parse_impl(path):
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^## 工作包\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    lines = [l for l in (m.group(1) if m else "").splitlines() if l.startswith("|")]
    if len(lines) < 3:
        sys.exit(f"{path} 的「工作包」表是空的")
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    out = []
    for l in lines[2:]:
        cells = dict(zip(head, [c.strip() for c in l.strip().strip("|").split("|")]))
        if re.match(r"^P\d+$", cells.get("包", ""), re.I):
            out.append(cells)
    return out


def check_dag(pkgs):
    ids = {p["包"].upper() for p in pkgs}
    deps = {p["包"].upper(): deps_of(p) for p in pkgs}
    bad = [f"{k}→{d}" for k, v in deps.items() for d in v if d not in ids]
    if bad:
        sys.exit("实现计划里依赖了不存在的包：" + "、".join(bad))
    done, order = set(), []
    while len(done) < len(ids):
        nxt = [k for k in deps if k not in done and all(d in done for d in deps[k])]
        if not nxt:
            sys.exit("实现计划里的依赖有环：" + "、".join(sorted(ids - done)))
        order.append(nxt)
        done |= set(nxt)
    return order


def cmd_approve(a):
    board = board_path(a.board)
    f = ctx(board)
    wt = Path(f["req_worktree"])
    impl, cases = wt / f["dir"] / "impl-plan.md", wt / f["dir"] / "cases.md"
    for p in (impl, cases):
        if not p.exists():
            sys.exit(f"还没有 {p}")
    pkgs = parse_impl(impl)
    order = check_dag(pkgs)
    print(f"- 工作包 {len(pkgs)} 个，可并行的批次：" + " → ".join("、".join(b) for b in order))
    if a.dry:
        return
    for p in (impl, cases):
        p.write_text(re.sub(r"^status: draft\b.*$", "status: approved", p.read_text(encoding="utf-8"), count=1, flags=re.M), encoding="utf-8")
    git(wt, "add", f["dir"])
    code, out = git(wt, "commit", "-m", f"docs: {f.get('req')} PRD、实现计划和验收用例")
    print(("- ✅ 已提交到 " + f.get("req_branch")) if not code else f"- ⚠ 提交：{out[:200]}")
    single = len(pkgs) == 1
    rows = [{"包": p["包"].upper(), "状态": "待派", "依赖": ", ".join(deps_of(p)) or "—",
             "分支": f.get("req_branch") if single else "", "工作区": f.get("req_worktree") if single else "",
             "会话": "", "审查轮次": "0", "更新": str(date.today())} for p in pkgs]
    write_rows(board, rows)
    set_stage(board, "开发")
    append(board, "监督", f"- {now()} — 实现计划和用例已审过并提交；{len(pkgs)} 个工作包，批次 " + " → ".join("、".join(b) for b in order))
    print("- 单包需求：直接在需求分支上开发" if single else f"- 现在可以派：{'、'.join(ready(rows)[0])}")


def cmd_pkg(a):
    need_orca()
    board = board_path(a.board)
    f = ctx(board)
    rows = read_rows(board.read_text(encoding="utf-8"))
    row = find_row(rows, a.pkg)
    cand, active = ready(rows)
    if row["状态"] != "待派":
        sys.exit(f"{row['包']} 现在是「{row['状态']}」，不是待派")
    if row["包"] not in cand and not a.force:
        sys.exit(f"{row['包']} 还不能派：依赖没合入，或已有 {active} 条线在进行（上限 {MAX_LINES}）；确实要派加 --force")
    if not row.get("工作区"):
        branch = f"pkg/{f.get('req')}-{row['包']}"
        print(f"- 从 {f.get('req_branch')} 切 Orca 工作区，分支 {branch}")
        if a.dry:
            return
        repo_path = git(f["req_worktree"], "rev-parse", "--path-format=absolute", "--git-common-dir")[1]
        repo_path = str(Path(repo_path).parent)
        path, branch = new_worktree(repo_path, f"pkg-{f.get('req')}-{row['包'].lower()}", f.get("req_branch"), branch,
                                    f"[开发] {f.get('req')} · {row['包']}", f"assistant 交付 {f.get('req')} {row['包']}")
        row.update({"分支": branch, "工作区": Path(path).as_posix(), "更新": str(date.today())})
        write_rows(board, rows)
    launch(board, row["包"], "dev-build", a.note or "", a.dry)


def cmd_launch(a):
    need_orca()
    launch(board_path(a.board), a.at if a.at == "req" else a.at.upper(), a.skill, a.note or "", a.dry)


def cmd_send(a):
    board = board_path(a.board)
    handle = resolve_handle(board, a.to)
    res = orca("terminal", "send", "--terminal", handle, "--text", a.text, "--enter", "--wait-submit", "15")
    print("✅ 已发送" if res.get("ok") else f"❌ 发送失败：{str(res.get('error'))[:300]}")
    if res.get("ok"):
        append(board, "监督", f"- {now()} — 发给 {a.to}：{a.text}")


def cmd_peek(a):
    board = board_path(a.board)
    term = (orca("terminal", "read", "--terminal", resolve_handle(board, a.to), "--screen").get("result") or {}).get("terminal") or {}
    lines = [l.rstrip() for l in term.get("tail") or [] if l.strip() and not set(l.strip()) <= set("─━-")]
    print(f"（终端 {term.get('status', '连不上')}）\n" + "\n".join(lines[-40:]))


def cmd_allow(a):
    """处理授权框：--to 指定一个会话；不指定就扫看板里所有开发、验收会话。不敏感的放行，敏感的列出来要升级给你。"""
    board = board_path(a.board)
    text = board.read_text(encoding="utf-8")
    f = fields(text)
    if a.to:
        targets = [(a.to, resolve_handle(board, a.to))]
    else:
        seen = {}
        for role, at, h in re.findall(r"— (dev|accept)/[\w-]+ @(\S+) · (term_\S+)", text):
            seen[h] = f"{role}@{at}"
        targets = [(name, h) for h, name in seen.items()]
    roots = [f.get("req_worktree", "")] + [r["工作区"] for r in read_rows(text) if r.get("工作区")]
    found = False
    for name, h in targets:
        result, msg = allow(h, always=not a.once, allowed_roots=[r for r in roots if r])
        if result == "none":
            continue
        found = True
        print(("✅ " if result == "allowed" else "⚠ 敏感，没按：") + f"{name} · {msg}")
        append(board, "监督", f"- {now()} — {'替你放行授权框' if result == 'allowed' else '授权框敏感，没按，要升级'}：{name} · {msg}")
    if not found:
        print("没有会话在等授权")


def cmd_set(a):
    board = board_path(a.board)
    rows = read_rows(board.read_text(encoding="utf-8"))
    row = find_row(rows, a.pkg)
    if a.state not in STATES:
        sys.exit("状态只能是：" + "、".join(STATES))
    old, row["状态"], row["更新"] = row["状态"], a.state, str(date.today())
    write_rows(board, rows)
    append(board, "监督", f"- {now()} — {row['包']}：{old} → {a.state}" + (f"；{a.note}" if a.note else ""))
    print(f"{row['包']}：{old} → {a.state}")


def cmd_merge(a):
    board = board_path(a.board)
    f = ctx(board)
    rows = read_rows(board.read_text(encoding="utf-8"))
    row = find_row(rows, a.pkg)
    if row["状态"] != "已通过":
        sys.exit(f"{row['包']} 是「{row['状态']}」：审查通过（set {row['包']} 已通过）后才能合")
    wt = f["req_worktree"]
    if row["分支"] != f.get("req_branch"):
        if git(wt, "status", "--porcelain")[1]:
            sys.exit(f"需求工作区有未提交的改动，先处理：{wt}")
        print(f"- 在 {wt} 合入 {row['分支']}（--no-ff）")
        if a.dry:
            return
        code, out = git(wt, "merge", "--no-ff", "--no-edit", row["分支"])
        if code:
            files = git(wt, "diff", "--name-only", "--diff-filter=U")[1]
            git(wt, "merge", "--abort")
            append(board, "监督", f"- {now()} — 合入 {row['包']} 冲突，已放弃本次合并：{files.replace(chr(10), '、')}")
            sys.exit(f"❌ 冲突，已 merge --abort。冲突文件：\n{files}\n"
                     f"处理：launch {row['包']} --skill dev-fix --note \"把 {f.get('req_branch')} 合进当前分支并解决冲突\"，修好后重新审查再合")
    row["状态"], row["更新"] = "已合入", str(date.today())
    write_rows(board, rows)
    append(board, "监督", f"- {now()} — {row['包']} 已合入 {f.get('req_branch')}")
    cand, _ = ready(rows)
    left = [r["包"] for r in rows if r["状态"] not in ("已合入", "放弃")]
    print(f"✅ {row['包']} 已合入。" + (f"现在可以派：{'、'.join(cand)}" if cand else ("全部包已合入：进入集成测试（launch req --skill accept-test）" if not left else "")))
    if not left:
        set_stage(board, "集成测试")


def cmd_sync(a):
    board = board_path(a.board)
    f = ctx(board)
    wt = f["req_worktree"]
    if git(wt, "status", "--porcelain")[1]:
        sys.exit(f"需求工作区有未提交的改动，先处理：{wt}")
    print(f"- 把 {f.get('dev_branch')} 合进 {f.get('req_branch')}")
    if a.dry:
        return
    code, out = git(wt, "merge", "--no-edit", f.get("dev_branch"))
    if code:
        files = git(wt, "diff", "--name-only", "--diff-filter=U")[1]
        git(wt, "merge", "--abort")
        append(board, "监督", f"- {now()} — 同步 {f.get('dev_branch')} 冲突，已放弃：{files.replace(chr(10), '、')}")
        sys.exit(f"❌ 冲突，已 merge --abort：\n{files}\n处理：launch req --skill dev-fix --note \"把 {f.get('dev_branch')} 合进当前分支并解决冲突\"")
    changed = "Already up to date" not in out
    append(board, "监督", f"- {now()} — 已同步 {f.get('dev_branch')}" + ("（有新提交，要重跑单元测试和关键 e2e）" if changed else "（没有新提交）"))
    print("✅ 已同步" + ("；develop 有新提交：重跑测试（launch req --skill accept-test --note \"同步 develop 后回归：单元测试 + P0 用例\"）后再 finish" if changed else "，可以 finish"))


def cmd_finish(a):
    board = board_path(a.board)
    f = ctx(board)
    rows = read_rows(board.read_text(encoding="utf-8"))
    left = [r["包"] for r in rows if r["状态"] not in ("已合入", "放弃")]
    if left or not rows:
        sys.exit("还有包没合入：" + "、".join(left) if left else "看板里还没有工作包")
    wt, dev, rb = f["req_worktree"], f.get("dev_branch"), f.get("req_branch")
    if git(wt, "merge-base", "--is-ancestor", dev, rb)[0]:
        sys.exit(f"{dev} 有需求分支没包含的提交：先 sync，重测通过后再 finish")
    holder = None
    blocks = git(wt, "worktree", "list", "--porcelain")[1].split("\n\n")
    for b in blocks:
        if f"branch refs/heads/{dev}" in b:
            holder = re.search(r"^worktree (.*)$", b, re.M).group(1)
    print(f"- 快进合回 {dev}：{rb}" + (f"（{dev} 检出在 {holder}）" if holder else ""))
    if a.dry:
        return
    if holder:
        if git(holder, "status", "--porcelain")[1]:
            sys.exit(f"{holder} 检出了 {dev} 且有未提交的改动，先处理再 finish")
        code, out = git(holder, "merge", "--ff-only", rb)
    else:
        code, out = git(wt, "fetch", ".", f"{rb}:{dev}")
    if code:
        sys.exit(f"❌ 合回失败：{out[:300]}")
    sha = git(wt, "rev-parse", "--short", rb)[1]
    text = re.sub(r"^status: .*$", "status: done", board.read_text(encoding="utf-8"), count=1, flags=re.M)
    board.write_text(stamp_closed(text), encoding="utf-8")
    set_stage(board, "完成")
    append(board, "验收", f"- {now()} — 已快进合回 {dev}（{sha}，本地）。推送 {dev}、合到 {f.get('main_branch')}、上线由你决定")
    print(f"✅ {dev} 已到 {sha}（本地，没推送）。有界面的交付先派体验准备（launch req --skill accept-test --note \"体验准备\"），"
          "再回报主会话「完成」（带体验卡）；推送和上线由你决定")
    # 收尾提议清理（cleanup skill）：体验结束后这条需求的工作区、开发和验收会话都可以清；列清单写进「完成」汇报，你确认后由主会话执行
    print(f"收尾：「完成」汇报里附上可以清理的会话和工作区——python tools/cleanup.py --for {(board.relative_to(ROOT) if board.is_relative_to(ROOT) else board).as_posix()}"
          "（只列清单；体验验收结束后，你确认再由主会话加 --run）")


def cmd_note(a, section):
    board = board_path(a.board)
    append(board, section, f"- {now()} — {a.text}")
    print(f"已记到「{section}」")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="开发交付流水线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("project"); s.add_argument("req"); s.add_argument("--prd", required=True)
    s.add_argument("--repo"); s.add_argument("--title"); s.add_argument("--run", action="store_true")
    s.add_argument("--force", action="store_true", help="团队仓库也跑流水线")
    for name in ("status", "approve", "sync", "finish"):
        p = sub.add_parser(name); p.add_argument("board"); p.add_argument("--dry", action="store_true")
    p = sub.add_parser("pkg"); p.add_argument("board"); p.add_argument("pkg"); p.add_argument("--note")
    p.add_argument("--force", action="store_true"); p.add_argument("--dry", action="store_true")
    p = sub.add_parser("launch"); p.add_argument("board"); p.add_argument("--at", required=True)
    p.add_argument("--skill", required=True, choices=list(SKILL_ROLE)); p.add_argument("--note"); p.add_argument("--dry", action="store_true")
    p = sub.add_parser("send"); p.add_argument("board"); p.add_argument("--to", required=True); p.add_argument("text")
    p = sub.add_parser("peek"); p.add_argument("board"); p.add_argument("--to", required=True)
    p = sub.add_parser("allow"); p.add_argument("board"); p.add_argument("--to"); p.add_argument("--once", action="store_true", help="只放行这一次（默认选「以后不再问」）")
    p = sub.add_parser("set"); p.add_argument("board"); p.add_argument("pkg"); p.add_argument("state"); p.add_argument("--note")
    p = sub.add_parser("merge"); p.add_argument("board"); p.add_argument("pkg"); p.add_argument("--dry", action="store_true")
    for name in ("log", "decide"):
        p = sub.add_parser(name); p.add_argument("board"); p.add_argument("text")
    a = ap.parse_args()
    if a.cmd == "log":
        return cmd_note(a, "监督")
    if a.cmd == "decide":
        return cmd_note(a, "决策记录")
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
