r"""
任务跟进 —— 跟进 Agent（agents/pm.md · task-follow skill）用的工具：看执行会话现在怎样，记进展和决定。

    python tools/follow.py runs/<运行记录>.md                       # 快照：运行记录 + Orca 会话状态 + 终端当前一屏 + 改动和提交
    python tools/follow.py runs/<运行记录>.md --log "看到了什么"      # 在「监督」加一行
    python tools/follow.py runs/<运行记录>.md --decide "问题 → 选择 → 理由（备选：…）"   # 在「决策记录」加一行
    python tools/follow.py runs/<运行记录>.md --send "给执行会话的话"  # 发给执行会话（只有 Orca 模式能发），并记进「监督」
    python tools/follow.py runs/<运行记录>.md --allow           # 执行会话弹了授权框：不敏感就放行，敏感的不按、升级给你
    python tools/follow.py runs/<运行记录>.md --close done --log "验收结论"   # 结束：status 改为 done / failed / abandoned
    python tools/follow.py runs/<运行记录>.md --spawn --role dev --repo example-repo --title "修 #513 审查问题 1" \
        --brief "目标：…\n背景：…\n验收：…" [--base develop] [--why "为什么派"]      # 按「授权」派生子任务：先出方案
    python tools/follow.py runs/<运行记录>.md --spawn … --run                       # 真正派发：建子运行记录 <父>-S<n>.md，回链父任务
    python tools/follow.py --reconcile                            # 扫僵尸运行记录（已结束 / 已让位但 status 还是 dispatched）：只出清单
    python tools/follow.py --reconcile --apply                    # 按清单改：建议关闭的改 done，建议转你等的补 next_check（status 不动）

派生子任务（跟进 Agent 用）：只在运行记录「授权」一节写明可以派的角色范围内（「可以派的角色」「你写明」两种行）；没写的工具会拒绝，按 task-follow 第 5 节升级你。
子任务不改计划文件、不另配跟进 Agent（派生它的跟进 Agent 自己跟），也不回报主会话；执行器和工作区按角色默认（dispatch.py 的规则）。

本地模式（没有 Orca）看不到执行会话的终端，也发不了消息：只能看执行目录里的改动和提交，要决定的事只能问你。

僵尸运行记录（--reconcile）：status 还是 dispatched / running、其实已经结束或已经让位的记录。
判定（满足任一）：a) 记录里登记的会话都不在 Orca 里，且最后一次监督超过 2 小时；b) 交付看板工作包全部已合入、
且集成测试已过；c) 「验收」一节已有内容。命中后：写了 waiting_on 的算让位 → 建议转等你（只补 next_check，
status 保持 dispatched）；其余 → 建议关闭。默认只出清单，主会话确认后加 --apply 才改文件。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import dispatch
import env
from common import ROOT, setup_stdio
from dispatch import orca


def fields(text):
    head = text.split("\n---", 1)[0]
    return {k: v.strip().strip("'\"") for k, v in re.findall(r"^(\w+):[ \t]*(.*)$", head, re.M)}


def append(run_file, section, line, status=None):
    """在运行记录的某一节末尾加一行；顺手更新 updated（和 status）。"""
    text = run_file.read_text(encoding="utf-8")
    m = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m:
        body = m.group(1).rstrip()
        text = text[:m.start(1)] + (body + "\n" if body else "\n") + line + "\n\n" + text[m.end(1):]
    else:
        text = text.rstrip() + f"\n\n## {section}\n\n{line}\n"
    text = re.sub(r"^updated: .*$", f"updated: {datetime.now():%Y-%m-%d}", text, count=1, flags=re.M)
    if status:
        text = re.sub(r"^status: .*$", f"status: {status}", text, count=1, flags=re.M)
        if status in ("done", "failed", "abandoned"):
            text = stamp_closed(text)
    run_file.write_text(text, encoding="utf-8")


def stamp_closed(text):
    """进入结束态时在 frontmatter 写 closed_at（已有就更新）：cleanup.py 的宽限期从这时算。"""
    head, sep, body = text.partition("\n---")
    at = f"closed_at: '{datetime.now():%Y-%m-%d %H:%M}'"
    if re.search(r"^closed_at:", head, re.M):
        head = re.sub(r"^closed_at:.*$", at, head, count=1, flags=re.M)
    else:
        head = re.sub(r"^(status: .*)$", lambda m: m.group(1) + "\n" + at, head, count=1, flags=re.M)
    return head + sep + body


# ---------- 授权框：Claude Code / OpenCode / Codex 弹出来的权限确认 ----------
# 你的授权：不太敏感的操作（文件读写、跑测试和构建、装依赖、在当前分支提交）默认接受；
# 敏感的不替你按，升级给你（要拍板）。

SENSITIVE = [
    (r"\bgit\s+push\b|push\s+--force|--force-with-lease", "推送"),
    (r"\b(merge|rebase)\b.*\b(main|master|develop)\b|\bgh\s+pr\s+merge\b", "合并到主干或开发分支"),
    (r"reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|worktree\s+remove", "丢弃改动或删除分支"),
    (r"\brm\s+-[a-z]*r|Remove-Item\b.*-Recurse|\brmdir\b|\bdel\s+/s", "递归删除"),
    (r"\b(DROP|TRUNCATE)\b|DELETE\s+FROM|prisma\s+migrate\s+deploy|db\s+push", "动数据库"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\bgh\s+release\b|vercel\s+(--prod|deploy)|\bdeploy\b", "发布或部署"),
    (r"\bssh\b|\bscp\b|curl\b.*-X\s*(POST|PUT|DELETE)|Invoke-RestMethod", "远程操作或对外发请求"),
    (r"private/|\.env\b|secret|token|password|credential|id_rsa|\.pem\b", "密钥、隐私文件"),
    (r"\bprod(uction)?\b", "生产环境"),
]


def prompt_kind(lines):
    """认出终端当前一屏上的授权框：返回 (工具, 框里的文字) 或 None。"""
    text = "\n".join(lines)
    if "Allow once" in text and "Always allow" in text and "Reject" in text:
        return "opencode", text
    if re.search(r"Do you want to (proceed|make this edit|create|run)|Do you trust the files", text) and re.search(r"\b1\.\s*Yes", text):
        return "claude", text
    if re.search(r"Allow command\?|Would you like to run the following command|approve this|Yes, proceed", text, re.I) and re.search(r"\(y\)|\by\b.*\bn\b", text):
        return "codex", text
    return None


def judge(text, allowed_roots=()):
    """不敏感返回 None；敏感返回原因。OpenCode 的外部目录请求，只放行 assistant、Orca 工作区和临时目录。"""
    for pat, why in SENSITIVE:
        if re.search(pat, text, re.I):
            return why
    roots = [ROOT.as_posix().lower(), Path.home().joinpath("orca", "workspaces").as_posix().lower(), "/tmp", "appdata/local/temp",
             *[str(r).replace("\\", "/").lower() for r in allowed_roots]]
    for p in re.findall(r"[A-Za-z]:[\\/][^\s\"'*]*", text):
        p = p.replace("\\", "/").lower()
        if not any(p.startswith(r) or r in p for r in roots):
            return f"访问工作区以外的路径 {p}"
    return None


# ---------- 派生子任务 ----------

def section(text, name):
    """运行记录某一节的正文（去掉注释）。"""
    m = re.search(rf"^## {name}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip() if m else ""


def auth_text(text):
    """父任务的授权：「授权」一节；旧运行记录没有这一节时，退回去找「任务说明」里以「授权」开头的行。"""
    auth = section(text, "授权")
    if not auth:
        auth = "\n".join(l.strip() for l in section(text, "任务说明").splitlines() if re.match(r"^\s*授权", l))
    return "" if auth.startswith("未单独授权") else auth


def authorized(auth, slug, name):
    """授权里有没有允许派这个角色，返回不行的原因，行就返回 None。
    新格式（dispatch.py 从执行方案 / PRD 生成）只看「可以派的角色」「你写明」两种行，生成的默认值不算授权；
    旧运行记录没有这两种行，看整节：要提到「派」，并点到角色（--role <slug>、slug 单词或「<角色名>」）。"""
    if slug == dispatch.FOLLOW_ROLE:
        return "跟进 Agent 不派跟进 Agent"
    if not auth:
        return "运行记录没有单独授权（「授权」一节为空或是默认边界）"
    roles = re.search(r"^\s*-\s*可以派的角色[：:]\s*(.*)$", auth, re.M)
    if roles:
        told = [roles.group(1)] if not roles.group(1).strip().startswith(("无", "见")) else []
        told += [l for l in re.findall(r"^\s*-\s*你写明[：:]\s*(.*)$", auth, re.M) if "派" in l]
        if not told:
            return "授权里没写可以派的角色（「可以派的角色」是无，你写明的授权里也没提派子任务）"
        auth = "\n".join(told)
    elif "派" not in auth:
        return "授权里没有说可以派子任务"
    if not (re.search(rf"(?<![A-Za-z]){re.escape(slug)}(?![A-Za-z])", auth) or name in auth or name.replace(" ", "") in auth):
        return f"授权里没有提到{name}（--role {slug}）"
    return None


def spawn(run_file, a):
    """跟进 Agent 按授权派生一个子任务：出方案；--run 时派发、写子运行记录、在父任务「子任务」回链。"""
    text = run_file.read_text(encoding="utf-8")
    f = fields(text)
    if f.get("status") not in ("dispatched", "planned"):
        raise SystemExit(f"父任务已经结束（status {f.get('status')}），不能再派生子任务")
    role_fm, role_text = dispatch.load_role(a.role)
    name = role_fm.get("name", a.role)
    why = authorized(auth_text(text), a.role, name)
    if why:
        raise SystemExit(f"❌ 不能派生{name}：{why}。超出授权 → 按 task-follow 第 5 节升级你（notify.py send --type 要拍板），不要自己派")
    brief = Path(a.brief_file).read_text(encoding="utf-8") if a.brief_file else (a.brief or "").replace("\\n", "\n")
    if not brief.strip():
        raise SystemExit("要给子任务写任务说明：--brief \"目标：…\\n背景：…\\n验收：…\" 或 --brief-file <文件>")
    if not re.search(r"^\s*目标", brief, re.M):
        brief = f"目标：{a.title}\n" + brief
    plan_stem, _, tid = (f.get("task") or f"{run_file.stem}#T0").partition("#")
    n = 1 + max([int(m.group(1)) for c in run_file.parent.glob(f"{run_file.stem}-S*.md")
                 if (m := re.search(r"-S(\d+)$", c.stem))] or [0])
    task = {"plan": plan_stem, "id": f"{tid}-S{n}", "title": a.title, "proj": f.get("project", ""),
            "repo": a.repo or f.get("repo", ""), "exec": ""}
    plan_file = next((ROOT / "plans").rglob(f"{plan_stem}.md"), ROOT / "plans" / f"{plan_stem}.md")
    mode = a.mode if a.mode != "auto" else ("orca" if env.detect()["orca_reachable"] else "local")
    agent, why_agent = dispatch.pick_agent(a.agent, role_fm, task)
    existing, why_ws = dispatch.pick_workspace(a.new, a.existing, role_fm, task)
    it = dispatch.prepare(task, "```text\n" + brief.strip() + "\n```", plan_file, agent, mode, existing, task["repo"],
                          (role_fm, role_text), a.base, {"agent": why_agent, "workspace": why_ws}, parent=run_file)
    print(f"# 派生子任务（父任务 {run_file.stem}；{'执行' if a.go else '只看方案，加 --run 才派发'}）")
    dispatch.print_plan([it])
    print(f"- 子运行记录：{it['run_file'].relative_to(ROOT).as_posix()}；不改计划文件、不配跟进 Agent，由你（父任务的跟进 Agent）跟")
    if not a.go:
        return
    if it["errors"]:
        raise SystemExit("检查不过，没有派发：" + "；".join(it["errors"]))
    record = dispatch.execute(it)
    if not record:
        raise SystemExit("派发失败，见上面的报错；子运行记录如果已经写了，status 还是 dispatched，可以 --close failed")
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    child = it["run_file"]
    append(run_file, "子任务", f"- {now} — [S{n}]({child.name}) {name} · {dispatch.AGENT_NAMES[agent]} · {it['gh']} · {a.title}"
           f"（{record['handle'] or record['mode']}，工作目录 {Path(record['workdir']).as_posix()}）")
    if a.why:
        append(run_file, "决策记录", f"- {now} — 派生子任务 S{n}（{name}）：{a.why}")
    print(f"✅ 已派生 S{n}：{child.relative_to(ROOT).as_posix()}；看它用 python tools/follow.py {child.relative_to(ROOT).as_posix()}")


def children(run_file, text):
    """父任务「子任务」一节里的子运行记录：[(编号, 文件)]。"""
    return [(m.group(1), run_file.parent / m.group(2)) for m in re.finditer(r"\[(S\d+)\]\(([^)]+\.md)\)", section(text, "子任务"))]


def screen(handle):
    term = (orca("terminal", "read", "--terminal", handle, "--screen").get("result") or {}).get("terminal") or {}
    return [l.rstrip() for l in term.get("tail") or [] if l.strip() and not set(l.strip()) <= set("─━-▀")]


def allow(handle, always=True, allowed_roots=()):
    """看一眼这个会话有没有授权框：不敏感就按「允许」，敏感就不按。返回 (结果, 说明)：
    none 没有授权框 / allowed 已放行 / sensitive 敏感、没按（要升级给你）。"""
    lines = screen(handle)
    hit = prompt_kind(lines[-30:])
    if not hit:
        return "none", ""
    tool, text = hit
    gist = re.sub(r"\s+", " ", " ".join(l.strip(" ┃│>❯›") for l in lines[-14:]))[:240]
    why = judge(text, allowed_roots)
    if why:
        return "sensitive", f"{tool} 的授权框涉及「{why}」，没有替你按：{gist}"
    if tool == "opencode":
        if always:
            orca("terminal", "send", "--terminal", handle, "--text", "\x1b[C")  # 默认选中 Allow once，右移一格是 Always allow
        orca("terminal", "send", "--terminal", handle, "--text", "", "--enter")
    elif tool == "claude":
        orca("terminal", "send", "--terminal", handle, "--text", "2" if always and re.search(r"\b2\.\s*Yes", text) else "1")
    else:
        orca("terminal", "send", "--terminal", handle, "--text", "a" if always and re.search(r"\(a\)", text) else "y")
    return "allowed", f"{tool} 的授权框已放行（{'以后同类不再问' if always else '只这一次'}）：{gist}"


def same_path(a, b):
    return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def snapshot(run_file):
    text = run_file.read_text(encoding="utf-8")
    f = fields(text)
    print(f"# {f.get('summary', run_file.stem)}")
    print(f"- 运行记录：status {f.get('status')}；执行器 {f.get('agent')}；方式 {f.get('mode')}；派发于 {f.get('created')}"
          + (f"；父任务 {f['parent']}" if f.get("parent") else ""))
    kids = children(run_file, text)
    if kids:
        print("- 子任务：" + "；".join(f"{sid} {fields(c.read_text(encoding='utf-8')).get('status', '?') if c.exists() else '记录不见了'}"
                                     f"（python tools/follow.py {c.relative_to(ROOT).as_posix()}）" for sid, c in kids))
    mode, target, handle = f.get("mode", ""), f.get("target", ""), f.get("handle", "")
    path = target if mode != "orca-new-worktree" else ""

    if mode.startswith("orca"):
        if not env.detect()["orca_reachable"]:
            print("- ⚠ Orca 现在连不上：只能看改动和提交")
        else:
            rows = (orca("worktree", "ps", "--limit", "200").get("result") or {}).get("worktrees", [])
            row = next((w for w in rows if w.get("worktreeId") == target or same_path(w.get("path"), target)), None)
            if not row:
                print("- ⚠ Orca 里找不到这个工作区（可能已经关掉或删掉了）")
            else:
                path = row.get("path") or path
                ts = max(row.get("lastOutputAt") or 0, row.get("lastActivityAt") or 0)
                last = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M") if ts else "?"
                print(f"- Orca 工作区：{row.get('displayName')} · 状态 {row.get('status')} · 最后活动 {last} · 分支 {row.get('branch', '').replace('refs/heads/', '')}")
                for a in row.get("agents") or []:
                    msg = re.sub(r"\s+", " ", a.get("lastAssistantMessage") or "")[:400]
                    print(f"  - {a.get('agentType')}：{a.get('state')}" + (f"；最后一句：{msg}" if msg else ""))
            if handle:
                term = ((orca("terminal", "read", "--terminal", handle, "--screen").get("result") or {}).get("terminal") or {})
                lines = [l.rstrip() for l in term.get("tail") or [] if l.strip() and not set(l.strip()) <= set("─━-")]
                print(f"\n## 执行会话当前一屏（终端 {term.get('status', '连不上')}）\n")
                print("\n".join(lines[-40:]) or "（读不到）")
    else:
        print("- 本地模式：看不到执行会话的终端，只看改动和提交；要决定的事问你")

    if path and Path(path).is_dir():
        since = (f.get("created") or "").strip() or "1 day ago"
        print(f"\n## 执行目录 {path}\n")
        print("- 分支：" + (git(path, "branch", "--show-current") or "?"))
        log = git(path, "log", "--oneline", f"--since={since}", "-n", "20")
        print("- 派发后的提交：\n" + ("\n".join("  " + l for l in log.splitlines()) if log else "  （还没有）"))
        st = git(path, "status", "--short").splitlines()
        print(f"- 未提交改动 {len(st)} 个" + ("：\n" + "\n".join("  " + l for l in st[:20]) if st else ""))


# ---------- 僵尸运行记录（--reconcile） ----------
# 问题：跟进 Agent 让位时按设计只关会话、不改 status，sweep reclaim 也不改状态——「已结束 / 已让位」的记录
# 一直挂着 dispatched，台账和推进位对不上（时间一长，「进行中」里会混进已经结束的僵尸记录）。
# 这里默认只出清单，主会话确认后加 --apply：建议关闭的改 status: done，建议转你等的只补 next_check
# （status 保持 dispatched，表示没结束）。

RUNS = ROOT / "runs"
ZOMBIE_STATUS = ("dispatched", "running")
STALE = timedelta(hours=2)      # 会话都不在了、最后一条监督还超过这么久，就当没人管了
WAIT_DAYS = 3                   # 建议转等你时没有 next_check：按 3 天后回来看
HIT_LABEL = {"a": "会话都没了、超 2 小时", "b": "交付看板全合入 + 集成测试过", "c": "「验收」已有内容"}
UNKNOWN = object()              # live 没传：自己去问 Orca；传 None 表示 Orca 连不上，判定条件 a 跳过


def _timestamps(text, *sections):
    """几节里「- YYYY-MM-DD HH:MM — …」行的时间。"""
    out = []
    for name in sections:
        for m in re.finditer(r"^-\s*(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})", section(text, name), re.M):
            try:
                out.append(datetime.fromisoformat(f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}"))
            except ValueError:
                pass
    return out


def last_supervised(fm, text):
    """最后一次有人管它的时间：监督 / 决策记录 / 子任务里最晚的时间戳；都没有就用 created（再没有 updated）。"""
    ts = _timestamps(text, "监督", "决策记录", "子任务")
    for key in ("created", "updated"):
        try:
            ts.append(datetime.fromisoformat((fm.get(key) or "").strip()))
        except ValueError:
            pass
    return max(ts) if ts else None


def record_handles(text, fm):
    """运行记录里登记过的会话 handle：执行会话、跟进 Agent、交付流水线各角色的会话。"""
    hs = [fm.get("handle", ""), fm.get("follower", "")]
    hs += re.findall(r"跟进 Agent：Orca 终端 handle (term_[0-9a-f-]+)", text)
    hs += [h for _, _, _, h in re.findall(r"— (\w+)/([\w-]+) @(\S+) · (term_[0-9a-f-]+)", text)]
    return [h for h in dict.fromkeys(hs) if h.startswith("term_")]


def delivery_progress(text):
    """交付看板：工作包是不是全部已合入、集成测试过没过。返回 (是不是看板, 说明, 全部合入, 集成测试已过)。"""
    pk = [(pid, st.strip()) for pid, st in re.findall(r"^\|\s*(P\d+)\s*\|\s*([^|]*?)\s*\|", text, re.M)]
    if not pk:
        return False, "", False, False
    merged = sum(1 for _, st in pk if st == "已合入")
    stage = (re.search(r"^- 当前：(.+)$", text, re.M) or [None, ""])[1].strip()
    body = "\n".join(section(text, n) for n in ("监督", "验收"))
    passed = stage in ("合回 develop", "完成") or any(
        re.search(r"集成测试", l) and re.search(r"通过|全过", l) and not re.search(r"要修|没通过|未通过|不通过|失败", l)
        for l in body.splitlines())
    why = f"工作包 {merged}/{len(pk)} 已合入；阶段 {stage or '?'}" + ("；集成测试已过" if passed else "")
    return True, why, merged == len(pk), passed


def live_handles():
    """Orca 里现在开着的终端 handle 集合；没装 Orca、连不上、返回不像样时给 None（判定条件 a 跳过）。"""
    import shutil
    if not shutil.which("orca"):
        return None
    try:
        res = orca("terminal", "list", "--limit", "300")
    except Exception:  # noqa: BLE001 —— 查不到就只按 b、c 判，不能挡住 reconcile
        return None
    terms = (res.get("result") or {}).get("terminals")
    if not isinstance(terms, list):
        return None
    return {t.get("handle") for t in terms if t.get("handle")}


def zombie_scan(runs_dir=None, *, now=None, live=UNKNOWN, me=None):
    """扫 status 为 dispatched / running 的运行记录，判哪些是僵尸。只读，不改文件。

    判定条件（满足任一）：a) 记录里登记的会话都不在 Orca 里，且最后一次监督超过 2 小时；
    b) 交付看板工作包全部已合入、且集成测试已过；c)「验收」一节已有内容。
    命中后：写了 waiting_on 的算「让位」→ 建议转等你（status 保持 dispatched）；
    其余算「已结束没改状态」→ 建议关闭（done）。
    a 只在派发它的这台设备上判（别的设备上的会话这边看不到）；b、c 看文件，哪台设备都判。
    """
    import device
    runs_dir = Path(runs_dir) if runs_dir else RUNS
    now = now or datetime.now()
    me = me or device.fingerprint()
    if live is UNKNOWN:
        live = live_handles()
    scanned, rows, elsewhere = 0, [], 0
    for f in sorted(runs_dir.glob("*.md")):
        if f.name in ("pending.md", "INDEX.md", "replies.md") or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        fm = fields(text)
        if fm.get("status", "") not in ZOMBIE_STATUS:
            continue
        scanned += 1
        hits, evidence = [], []
        last = last_supervised(fm, text)
        hs = record_handles(text, fm)
        if (fm.get("device") or me) != me:
            elsewhere += 1  # 别的设备上派发的：会话只能在派发它的设备上查（sweep.py 的多设备规则）
        elif hs and live is not None and not any(h in live for h in hs) and last and now - last > STALE:
            hits.append("a")
            evidence.append(f"会话都不在了，最后一条监督 {last:%m-%d %H:%M}")
        is_delivery, why, merged, passed = delivery_progress(text)
        if is_delivery and merged and passed:
            hits.append("b")
            evidence.append(why)
        accepted = section(text, "验收")
        acc_at = max(_timestamps(text, "验收"), default=None)
        sup_at = max(_timestamps(text, "监督", "决策记录", "子任务"), default=None)
        if accepted:
            hits.append("c")
            evidence.append("「验收」一节已有内容" + (f"（最后一条 {acc_at:%m-%d %H:%M}）" if acc_at else ""))
        if not hits:
            continue
        waiting_on = fm.get("waiting_on", "")
        next_check = fm.get("next_check", "")
        action = "close" if ("b" in hits or "c" in hits or not waiting_on) else "wait"
        hint = ""
        if action == "close":
            if not accepted:
                hint = "「验收」还是空的：关闭前先在「验收」记一句结论"
            if "a" in hits and "b" not in hits and "c" not in hits:
                hint = (hint + "；" if hint else "") + "如果其实在等人：先补 waiting_on / next_check，再跑一次会进「建议转等你」"
            if acc_at and sup_at and acc_at < sup_at:
                hint = (hint + "；" if hint else "") + "「验收」最后一条早于最后一次监督，确认一下是不是还有后续"
        item = (fm.get("req") or fm.get("task") or fm.get("summary", "").replace("收编：", "")).strip()
        due = True
        if action == "wait":
            due = _due(next_check, now)
            hint = ("复查日期到了（next_check 到了或没写）——回来看一眼；不是真在等就关掉"
                    if due else f"在等你，复查日期 {next_check}（没到，先不提醒）")
        rows.append({"file": f, "slug": fm.get("slug") or f.stem, "project": fm.get("project", "?"), "item": item,
                     "hits": hits, "evidence": "；".join(evidence), "action": action, "last": last, "due": due,
                     "waiting_on": waiting_on, "next_check": next_check,
                     "suggest_next": next_check or f"{(now + timedelta(days=WAIT_DAYS)):%Y-%m-%d}", "hint": hint})
    return {"scanned": scanned, "rows": rows, "live_known": live is not None, "elsewhere": elsewhere}


def _due(next_check, now):
    """转你等的记录该不该提醒：没写复查日期、或者日期到了 / 过了，都算到期。"""
    try:
        return datetime.fromisoformat(next_check.strip()).date() <= now.date()
    except (AttributeError, ValueError):
        return True


def zombie_count(runs_dir=None, *, live=UNKNOWN, now=None):
    """要提醒的条数：建议关闭的全部算，建议转你等的只看复查日期到了的（没到期的是正常在等）。"""
    return sum(1 for r in zombie_scan(runs_dir, live=live, now=now)["rows"] if r["action"] == "close" or r["due"])


def zombie_line(runs_dir=None, *, live=UNKNOWN, now=None):
    """一行提醒（sweep 末尾、开场卡片用）：有 N 条僵尸运行记录（疑似已结束 / 已让位）。没有返回空串。"""
    n = zombie_count(runs_dir, live=live, now=now)
    return f"有 {n} 条僵尸运行记录（疑似已结束 / 已让位），跑 python tools/follow.py --reconcile 看清单" if n else ""


def _cell(s, n=None):
    s = re.sub(r"\s+", " ", str(s or "—")).replace("|", "/").strip() or "—"
    return s if not n or len(s) <= n else s[:n] + "…"


def render_zombies(scan, changes=None):
    """两张表：建议关闭（done）、建议转等你（status 保持 dispatched，只补 next_check）。"""
    rows = scan["rows"]
    close = [r for r in rows if r["action"] == "close"]
    wait = [r for r in rows if r["action"] == "wait"]
    out = [f"# 僵尸运行记录（扫了 {scan['scanned']} 条 status 为 dispatched / running 的记录）", ""]
    if not scan["live_known"]:
        out += ["- ⚠ Orca 连不上：判定条件 a（会话都不在了）跳过，只看交付看板和「验收」", ""]
    else:
        out += ["- 判定条件 a（会话都不在了）只判本设备派发的记录"
                + (f"；另有 {scan['elsewhere']} 条在别的设备上派发，a 跳过" if scan.get("elsewhere") else "") + "", ""]
    if not rows:
        return "\n".join(out + ["没有僵尸运行记录：都还活着，或者在正常监督中。", ""])
    out += [f"## 建议关闭（status → done）· {len(close)} 条", ""]
    if close:
        out += ["| 运行记录 | 项目 · 事项 | 命中 | 理由 / 提示 |", "|---|---|---|---|"]
        out += [f"| {_cell(r['file'].name)} | {_cell(r['project'], 22)} · {_cell(r['item'], 32)} | "
                f"{_cell('、'.join(HIT_LABEL[h] for h in r['hits']), 28)} | {_cell('；'.join(x for x in (r['evidence'], r['hint']) if x))} |"
                for r in close]
    else:
        out.append("（没有）")
    out += ["", f"## 建议转等你（status 保持 dispatched，只补 next_check）· {len(wait)} 条"
            f"（到期 {sum(1 for r in wait if r['due'])} 条）", ""]
    if wait:
        out += ["| 运行记录 | 项目 · 事项 | 等谁 / 等什么 | 复查日期 | 说明 |", "|---|---|---|---|---|"]
        out += [f"| {_cell(r['file'].name)} | {_cell(r['project'], 22)} · {_cell(r['item'], 32)} | "
                f"{_cell(r['waiting_on'], 36)} | {_cell(r['suggest_next'])} | {_cell(r['hint'])} |" for r in wait]
    else:
        out.append("（没有）")
    out.append("")
    if changes is None:
        out += ["只出清单，没改任何文件。确认后加 `--apply`：建议关闭的改 status: done（并在「监督」记一行），"
                "建议转你等的只补 next_check。", ""]
    else:
        out += [f"## 已应用（--apply）· {len(changes)} 处", ""] + ([f"- {c}" for c in changes] or ["（没有要改的）"]) + [""]
    return "\n".join(out)


def stamp_waiting(text, next_check, now=None):
    """frontmatter 写 next_check（有就更新，没有就加在 waiting_on 或 status 后面），顺手更新 updated（用注入的 now）。"""
    now = now or datetime.now()
    head, sep, body = text.partition("\n---")
    line = f"next_check: {next_check}"
    if re.search(r"^next_check:", head, re.M):
        head = re.sub(r"^next_check:.*$", line, head, count=1, flags=re.M)
    elif re.search(r"^waiting_on:", head, re.M):
        head = re.sub(r"^(waiting_on:.*)$", lambda m: m.group(1) + "\n" + line, head, count=1, flags=re.M)
    else:
        head = re.sub(r"^(status: .*)$", lambda m: m.group(1) + "\n" + line, head, count=1, flags=re.M)
    head = re.sub(r"^updated: .*$", f"updated: {now:%Y-%m-%d}", head, count=1, flags=re.M)
    return head + sep + body


def apply_zombies(scan, now=None):
    """按清单改：建议关闭的 status → done（closed_at 由 append 写），建议转你等的只补 next_check。"""
    now = now or datetime.now()
    changes = []
    for r in scan["rows"]:
        f = r["file"]
        if r["action"] == "close":
            append(f, "监督", f"- {now:%Y-%m-%d %H:%M} — reconcile（--apply）：{r['evidence']}，按建议关闭（status → done）",
                   status="done")
            changes.append(f"{f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f.name} → done（{r['evidence']}）")
            continue
        nxt = r["suggest_next"]
        if r["next_check"] == nxt:
            continue
        f.write_text(stamp_waiting(f.read_text(encoding="utf-8"), nxt, now), encoding="utf-8")
        changes.append(f"{f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f.name} → next_check {nxt}（仍 dispatched）")
    return changes


def cmd_reconcile(a):
    now = datetime.now()
    scan = zombie_scan(now=now)
    changes = apply_zombies(scan, now=now) if a.apply else None
    print(render_zombies(scan, changes))


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="任务跟进")
    ap.add_argument("run", nargs="?", help="运行记录 runs/<...>.md（--reconcile 时不用）")
    ap.add_argument("--reconcile", action="store_true", help="扫僵尸运行记录（已结束 / 已让位但 status 还是 dispatched）：默认只出清单")
    ap.add_argument("--apply", action="store_true", help="和 --reconcile 一起用：按清单改（建议关闭的改 done，建议转你等的只补 next_check）")
    ap.add_argument("--log", help="在「监督」加一行")
    ap.add_argument("--decide", help="在「决策记录」加一行：问题 → 选择 → 理由（备选：…）")
    ap.add_argument("--send", help="发给执行会话（Orca 模式）")
    ap.add_argument("--allow", action="store_true", help="执行会话在等授权框：不敏感就放行，敏感的不按（Orca 模式）")
    ap.add_argument("--close", choices=["done", "failed", "abandoned"], help="结束跟进，改 status")
    sp = ap.add_argument_group("派生子任务（--spawn，只在运行记录「授权」范围内）")
    sp.add_argument("--spawn", action="store_true", help="按授权派生一个子任务：先出方案，加 --run 才派发")
    sp.add_argument("--role", help="子任务的角色（agents/<role>.md），如 research / investigate / dev")
    sp.add_argument("--title", help="子任务标题（动词开头）")
    sp.add_argument("--brief", help="任务说明：目标 / 背景 / 验收 / 约束 / 做完，换行写 \\n")
    sp.add_argument("--brief-file", help="任务说明从文件读（长说明用这个）")
    sp.add_argument("--repo", help="仓库名或 owner/repo，默认父任务的仓库")
    sp.add_argument("--agent", choices=dispatch.AGENTS, help="执行器，默认按角色")
    sp.add_argument("--base", help="新工作区从哪个分支切（比如 develop）")
    sp.add_argument("--new", action="store_true", help="新建 Orca 工作区（覆盖角色默认）")
    sp.add_argument("--existing", action="store_true", help="用仓库现有目录（覆盖角色默认）")
    sp.add_argument("--mode", choices=["auto", "orca", "local"], default="auto")
    sp.add_argument("--why", help="为什么派（同时记进父任务「决策记录」）")
    sp.add_argument("--run", dest="go", action="store_true", help="真正派发")
    args = ap.parse_args()

    if args.apply and not args.reconcile:
        raise SystemExit("--apply 只和 --reconcile 一起用（它按 reconcile 的清单改文件）")
    if args.reconcile:
        cmd_reconcile(args)
        return
    if not args.run:
        raise SystemExit("要指定运行记录 runs/<...>.md，或者用 --reconcile 扫僵尸运行记录")
    run_file = Path(args.run)
    if not run_file.is_absolute():
        run_file = ROOT / run_file
    if not run_file.exists():
        raise SystemExit(f"找不到运行记录 {run_file}")
    if args.spawn:
        if not (args.role and args.title):
            raise SystemExit("--spawn 要带 --role 和 --title（还有 --brief 或 --brief-file）")
        spawn(run_file, args)
        return
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    acted = False
    if args.allow:
        handle = fields(run_file.read_text(encoding="utf-8")).get("handle", "")
        if not handle:
            raise SystemExit("这个任务没有 Orca 终端 handle（本地模式），处理不了授权框")
        result, msg = allow(handle)
        print({"none": "执行会话没有在等授权", "allowed": "✅ " + msg, "sensitive": "⚠ " + msg + "——要升级给你"}[result])
        if result != "none":
            append(run_file, "监督", f"- {now} — {'替你放行授权框' if result == 'allowed' else '授权框敏感，没按，要升级'}：{msg}")
        acted = True
    if args.send:
        handle = fields(run_file.read_text(encoding="utf-8")).get("handle", "")
        if not handle:
            raise SystemExit("这个任务没有 Orca 终端 handle（本地模式），发不了消息；需要的话请你转达")
        res = orca("terminal", "send", "--terminal", handle, "--text", args.send, "--enter", "--wait-submit", "15")
        ok = res.get("ok")
        print(("✅ 已发给执行会话" if ok else "❌ 发送失败：") + ("" if ok else str(res.get("error"))[:300]))
        if ok:
            append(run_file, "监督", f"- {now} — 发给执行会话：{args.send}")
        acted = True
    if args.decide:
        append(run_file, "决策记录", f"- {now} — {args.decide}")
        acted = True
    if args.log or args.close:
        section = "验收" if args.close else "监督"
        append(run_file, section, f"- {now} — " + (args.log or f"结束跟进：{args.close}"), status=args.close)
        acted = True
    if acted:
        print(f"已记到 {run_file.relative_to(ROOT).as_posix()}")
        if args.close and env.detect()["orca_reachable"]:  # 收尾：提议清理这个派发的会话和工作区（cleanup skill），你确认后由主会话执行
            import cleanup
            items, notes = cleanup.inventory(ROOT, for_run=run_file)
            if items:
                print("\n收尾：这个派发可以清理的会话和工作区——写进「完成」汇报，你确认后由主会话执行下面的命令（你自己不要清）\n")
                print(cleanup.render(items, notes, f"python tools/cleanup.py --for {run_file.relative_to(ROOT).as_posix()}"))
    else:
        snapshot(run_file)


if __name__ == "__main__":
    main()
