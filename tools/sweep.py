r"""
回收派出去的会话 —— 查每个还没结束的派发：会话现在怎样、做完了有没有回报主会话；做完没回报的，把结果收回 assistant。

    python tools/sweep.py                 # 总览：本机推进位 x/6 + 每个派发一行（设备、会话状态、最后一句、有没有回报、建议）+ 没登记的会话
    python tools/sweep.py reclaim <运行记录或看板>   # 回收：把会话最后的总结记进「验收」，在待处理清单里补一条「完成」（不推送）
    python tools/sweep.py reclaim <…> --close        # 同上，并关掉这个会话的终端（Orca 工作区和分支不动）
    python tools/sweep.py adopt [--handle term_…] [--dry]   # 收编：没登记的会话（不含团队 worker、主会话）补派发记录、收回总结
    python tools/sweep.py --snapshot [--auto-reclaim]   # 早间建议 / 夜间巡检用：写本设备快照 runs/status/<指纹>.md；夜间顺手回收

多设备：派发记录写 device（设备指纹）。会话只能在派发它的那台设备上查，别的设备的派发只列出来、读它的快照。
推进位（同时最多 6 件）按设备算：只数本机有会话开着的派发，别的设备上的占那台设备的位，不进本机的 x/6。

「回收」不改任务状态、不合并分支：结果收回来以后，由你在主会话里验收、决定合不合。
"""
from __future__ import annotations

import argparse
import functools
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import device
import env
import notify
import plan
from common import ROOT, setup_stdio
from dispatch import orca
from follow import append, fields, zombie_line

RUNS = ROOT / "runs"
STATUS = RUNS / "status"  # 每台设备一份派发快照（随 git 同步）
OPEN = ("dispatched", "planned", "")
MAX_SLOTS = 6  # 推进位上限：同时推进的派发件数，按设备算（CLAUDE.md §5 / dispatch skill「推进位」）
DONE_HINT = re.compile(r"做完|完成|可以审查|写好了|已交接|总结|通过|已提交|结论|处理好了|都已|定稿")


def ps_index():
    """Orca 里每个 agent 窗格：paneKey（tabId:leafId）→ (agent, 工作区)。"""
    idx = {}
    for w in (orca("worktree", "ps", "--limit", "300").get("result") or {}).get("worktrees", []):
        for a in w.get("agents") or []:
            idx[a.get("paneKey", "")] = (a, w)
    return idx


def session(handle, idx):
    """一个终端 handle 的现状：state / last / at / wait / gone。"""
    t = (orca("terminal", "show", "--terminal", handle).get("result") or {}).get("terminal") or {}
    if not t or not t.get("connected"):
        return {"state": "gone", "last": "", "at": "", "repo": ""}
    a, w = idx.get(f"{t.get('tabId')}:{t.get('leafId')}", ({}, {}))
    at = datetime.fromtimestamp(int(t["lastOutputAt"]) / 1000).strftime("%m-%d %H:%M") if t.get("lastOutputAt") else ""
    state = "等你" if t.get("agentWait") else (a.get("state") or "?")
    return {"state": state, "last": re.sub(r"\s+", " ", a.get("lastAssistantMessage") or ""), "at": at, "repo": w.get("repo", ""),
            "title": re.sub(r"^(OC \| )", "", re.sub(r"^[✳⠀-⣿·\s]+", "", t.get("title") or "")), "path": t.get("worktreePath") or "",
            "ts": int(t.get("lastOutputAt") or 0), "agent": a.get("agentType", "")}


def runs():
    for f in sorted(RUNS.glob("*.md")):
        if f.name in ("pending.md", "INDEX.md"):
            continue
        text = f.read_text(encoding="utf-8")
        fm = fields(text)
        if fm.get("status", "") in OPEN:
            yield f, text, fm


def handles_of(text, fm):
    """这个派发涉及的会话：执行会话、跟进 Agent、看板里登记的会话。"""
    hs = []
    if fm.get("handle", "").startswith("term_"):
        hs.append(("执行会话", fm["handle"]))
    for h in re.findall(r"跟进 Agent：Orca 终端 handle (term_[0-9a-f-]+)", text) + ([fm["follower"]] if fm.get("follower", "").startswith("term_") else []):
        hs.append(("跟进 Agent", h))
    for role, skill, at, h in re.findall(r"— (\w+)/([\w-]+) @(\S+) · (term_[0-9a-f-]+)", text):
        hs.append((f"{role}/{skill}@{at}", h))
    seen, out = set(), []
    for name, h in hs:
        if h not in seen:
            seen.add(h)
            out.append((name, h))
    return out


def reported(path):
    """待处理清单里有没有这个派发的「完成 / 交接」。"""
    _, rows = notify.read_rows()
    p = path.resolve().as_posix()
    return [r for r in rows if r["类型"] in ("完成", "交接") and r["详情"].replace("\\", "/") == p]


def verdict(main, rep, fm):
    if rep:
        return "已回报", f"清单 {rep[-1]['编号']}（{rep[-1]['状态'][:12]}）"
    if main["state"] == "gone":
        return "会话没了", "回收：看分支和提交收结果，或重新派一个会话接着做"
    if main["state"] == "等你":
        return "等你", "会话在等确认，去看一眼或交给跟进 Agent"
    if main["state"] == "working":
        return "在跑", "继续等"
    if main["state"] == "done" and main["last"] and DONE_HINT.search(main["last"]):
        return "做完没回报", f"回收：python tools/sweep.py reclaim {fm.get('slug', '')}"
    return "停着", "看一眼最后一句：做完了就回收，没做完就叫它继续"


def run_device(fm):
    """派发在哪台设备上：运行记录的 device（指纹）；旧记录没有设备字段，算本设备。"""
    return fm.get("device") or device.fingerprint()


def devices_in_use():
    """{设备指纹: 主机名}：本机 + 快照里、运行记录里见过的别的设备。只有一台设备时不显示设备列。"""
    dev = device.load_device()
    names = {dev["fingerprint"]: dev.get("hostname", "")}
    for f in sorted(STATUS.glob("*.md")) if STATUS.is_dir() else []:
        if f.name == "INDEX.md":
            continue
        fm = fields(f.read_text(encoding="utf-8"))
        names.setdefault(f.stem, fm.get("hostname") or f.stem)
    for f in RUNS.glob("*.md"):
        if f.name in ("pending.md", "INDEX.md"):
            continue
        fp = fields(f.read_text(encoding="utf-8")).get("device")
        if fp:
            names.setdefault(fp, fp)
    return names


def dev_cell(fp, names):
    """设备一栏：本机 / 别的设备（主机名）；没派发的不标。"""
    if not fp:
        return "—"
    if fp == device.fingerprint():
        return "本机"
    return "别的设备 " + (names.get(fp) or fp)


def slot_line(slot_rows, names=None, live_known=True):
    """推进位一行：本机 x/6 和占位的是哪几件；有别的设备时注明它们上的另算、不占本机位。"""
    me = device.fingerprint()
    names = devices_in_use() if names is None else names
    line = f"推进位：本机 {len(slot_rows)}/{MAX_SLOTS}"
    if slot_rows:
        line += "（" + "；".join(f"{r['project']}：{r['item']}" for r in slot_rows) + "）"
    others = [fp for fp in names if fp != me]
    if others:
        line += "；别的设备 " + "、".join(f"{names[fp]}（{fp}）" for fp in others) + "上的另算，不占本机位"
    if not live_known:
        line += "；Orca 没开，按运行记录里的会话数，没法确认还开着"
    return line


STATE_ZH = {"working": "在干活", "done": "停着（上一轮做完）", "等你": "等你回复", "gone": "会话已关", "Orca 没开": "Orca 没开", "?": "?"}


def who(s, name):
    """会话一格：名字 · 状态 · 最后输出。"""
    if not s:
        return "—"
    title = s.get("title") or name
    return f"{title[:26]} · {STATE_ZH.get(s['state'], s['state'])}" + (f" · {s['at'][6:]}" if s.get("at") else "")


@functools.lru_cache(maxsize=1)
def _proj_index():
    """[(本设备克隆路径, 项目)] 和 {仓库目录名: 项目}：注册表和设备登记只读一次（cleanup.py 一次要查上百个目录）。"""
    from common import load_registry, norm_remote
    paths, names = [], {}
    for e in load_registry().get("tracked") or []:
        for r in e.get("repos") or []:
            for lp in device.local_paths(norm_remote(r["github"])):
                lp = lp.replace("\\", "/").lower().rstrip("/")
                paths.append((lp, e["slug"]))
                names[lp.split("/")[-1]] = e["slug"]
            names.setdefault(norm_remote(r["github"]).split("/")[-1].lower(), e["slug"])
    return paths, names


def project_of_path(path):
    """工作目录 → 项目 slug：先比本设备登记的克隆路径，再看 Orca 工作区目录名（~/orca/workspaces/<仓库目录>/…）。"""
    p = (path or "").replace("\\", "/").lower()
    paths, names = _proj_index()
    hit = next((slug for lp, slug in paths if p == lp or p.startswith(lp + "/")), None)
    if hit:
        return hit
    m = re.search(r"/orca/workspaces/([^/]+)/", p + "/")
    return names.get(m.group(1)) if m else None


def plan_tasks():
    """进行中 / 受阻的计划任务，以及派发过、还没做完的任务：{run stem 或 plan#id: task}。"""
    out = {}
    for t in plan.all_tasks():
        if t["state"] in ("完成", "放弃"):
            continue
        if t["state"] in ("进行中", "受阻") or t.get("run"):
            out[t.get("run") or f"{t['plan']}#{t['id']}"] = t
    return out


def split_item(proj, item):
    """（项目, 事项）：事项开头重复写了项目（「assistant：…」「示例项目：…」）就去掉，项目单独一列。"""
    m = re.match(r"^([^：:]{1,16})[：:]\s*(.+)$", item or "")
    if proj and m and (m.group(1).lower() == proj.lower() or plan.project_of(m.group(1), "") == proj):
        item = m.group(2)
    return proj or "?", item or "—"


def local_slots(idx=None):
    """本机推进位：本机派发、会话还开着的顶层派发。一件里的子任务算在父任务里，别的设备上的不算。
    只在等你的会话已经关了，自然不占。返回 [{run, project, item, sessions}]。
    Orca 没开时按运行记录里有没有会话 handle 数，没法确认还开着。"""
    me = device.fingerprint()
    if idx is None:
        idx = ps_index() if env.detect()["orca_reachable"] else {}
    tasks, out = plan_tasks(), []
    for f, text, fm in runs():
        if run_device(fm) != me or fm.get("parent"):
            continue
        hs = handles_of(text, fm)
        live = [h for _, h in hs if idx and session(h, idx)["state"] not in ("gone", "?")]
        if idx and not live:
            continue
        if not idx and not hs:
            continue
        t = tasks.pop(f.stem, None)
        if t:
            project, item = split_item(plan.task_project(t), t["title"])
        elif fm.get("kind") == "adopted":
            project, item = fm.get("project", "?"), fm.get("summary", "").replace("收编：", "")
        else:
            project, item = split_item(fm.get("project", "?"), fm.get("req") or fm.get("task") or fm.get("summary", ""))
        out.append({"run": f.stem, "project": project, "item": short_text(item, 28),
                    "sessions": len(live) if idx else len(hs)})
    return out


def short_text(text, n=30):
    """一格里的短句：压掉换行，超长加省略号。"""
    text = re.sub(r"\s+", " ", text or "—").strip()
    return text if len(text) <= n else text[:n] + "…"


def board(auto_reclaim=False):
    """跟进总表：每件事一行——项目、事项、进度、派发了没有、执行会话、跟进会话、现在等谁、下一步。
    来源：进行中的计划任务 + 没结束的派发（运行记录、交付看板）+ Orca 里没登记的会话。"""
    idx = ps_index() if env.detect()["orca_reachable"] else {}
    me, rows, known = device.fingerprint(), [], set()
    tasks = plan_tasks()
    run_files = {f.stem: f for f in RUNS.glob("*.md") if f.name not in ("pending.md", "INDEX.md")}
    for f in run_files.values():
        known |= {h for _, h in handles_of(f.read_text(encoding="utf-8"), fields(f.read_text(encoding="utf-8")))}

    def live(h):
        return session(h, idx) if idx and h else None

    # 1. 派发过的：运行记录 / 交付看板（没结束的，或者对应的任务还没做完的）
    for stem, f in sorted(run_files.items()):
        text = f.read_text(encoding="utf-8")
        fm = fields(text)
        task = tasks.pop(stem, None)
        if fm.get("status", "") not in OPEN and not task:
            continue
        proj, item = split_item(*((plan.task_project(task), task["title"]) if task
                                  else (fm.get("project", ""), fm.get("summary", "").replace("收编：", "")) if fm.get("kind") == "adopted"
                                  else (fm.get("project", ""), fm.get("req") or fm.get("task", ""))))
        if run_device(fm) != me:
            rows.append({"proj": proj, "item": item, "device": run_device(fm), "progress": "在别的设备上派发的", "dispatch": f"已派发（{stem}）", "exec": "本机看不到",
                         "follow": "本机看不到", "wait": "看那台设备", "next": f"runs/status/{run_device(fm)}.md"})
            continue
        hs = dict((n, h) for n, h in handles_of(text, fm) if n in ("执行会话", "跟进 Agent"))
        ex, fo = live(hs.get("执行会话")), live(hs.get("跟进 Agent"))
        if fm.get("kind") == "delivery":  # 流水线：执行的是一组开发 / 验收会话，看板上有阶段和工作包
            stage = (re.search(r"^- 当前：(.+)$", text, re.M) or [None, "?"])[1]
            pk = re.findall(r"^\| (P\d+) \| (\S+) \|", text, re.M)
            merged = sum(1 for _, s in pk if s == "已合入")
            progress = f"{stage}；工作包 {merged}/{len(pk)} 已合入" if pk else stage
            busy = [s for n, h in handles_of(text, fm) if n not in ("执行会话", "跟进 Agent") for s in [live(h)] if s and s["state"] == "working"]
            ex_txt = f"流水线 {len(busy)} 个会话在干活" + (f"（{busy[0]['title'][:20]}…）" if busy else "")
        else:
            prog = (task or {}).get("progress") or ""
            progress = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", prog)[:60] or (ex or {}).get("last", "")[:60] or "—"
            if progress == "—":  # 收编的会话、读不到最后一句的：用回收时记的「验收」
                got = re.findall(r"（回收）会话最后的总结（[^）]*）：(.+)$", text, re.M)
                progress = got[-1][:60] if got else "—"
            ex_txt = who(ex, "执行会话") if hs.get("执行会话") else "—"
        rep = reported(f)
        lead = fo if hs.get("跟进 Agent") else ex
        v, tip = verdict(lead or {"state": "gone", "last": ""}, rep, fm) if fm.get("status", "") in OPEN else ("已结束", "")
        if v == "做完没回报" and auto_reclaim:
            cmd_reclaim(argparse.Namespace(run=str(f), close=False))
            v, tip = "已回收", "等你验收"
        wait = {"在跑": "在跑，不用管", "等你": "等你", "做完没回报": "要回收", "已回收": "等你验收", "会话没了": "我来处理",
                "停着": "我去看", "已回报": "等你验收" if any(r["状态"] == "待处理" for r in rep) else "已处理",
                "已结束": "等你验收"}.get(v, v)
        if fm.get("status", "") not in OPEN and task:
            wait, tip = "派发已结束、任务没收尾", "看进展：验收后打勾，或者还差什么"
        rows.append({"proj": proj, "item": item, "device": me, "progress": progress, "dispatch": ("收编" if fm.get("kind") == "adopted" else f"已派发 · {stem[11:]}")
                     + (f" · 派自 {(fm.get('dispatched_by') or fm.get('report_session')).split(' · ')[0]}" if (fm.get("dispatched_by") or fm.get("report_session")) and fm.get("kind") != "adopted" else ""), "exec": ex_txt,
                     "follow": who(fo, "跟进 Agent") if hs.get("跟进 Agent") else "没配", "wait": wait, "next": tip or "—"})

    # 2. 进行中、但没派发的计划任务（你或我自己在做的）
    for key, t in sorted(tasks.items()):
        if t.get("run"):
            continue
        proj, item = split_item(plan.task_project(t), t["title"])
        rows.append({"proj": proj, "item": item[:40], "device": "", "progress": (t.get("progress") or "—")[:60],
                     "dispatch": "没派发", "exec": f"@{t['owner'] or '?'}", "follow": "—",
                     "wait": "你在做" if t["owner"] == "me" else "我在做", "next": "—"})

    # 3. Orca 里没登记的会话（最近 24 小时有输出）：你自己开的、别的主会话、团队 Orca 编排的 worker
    others, team = [], {}
    if idx:
        mains = {s.get("handle") for s in notify.load_state()["sessions"].values() if s.get("kind") == "main"}
        cutoff = (datetime.now().timestamp() - 86400) * 1000
        for t in (orca("terminal", "list", "--limit", "300").get("result") or {}).get("terminals", []):
            h = t.get("handle", "")
            if not h or h in known or h == os.environ.get("ORCA_TERMINAL_HANDLE") or int(t.get("lastOutputAt") or 0) < cutoff:
                continue
            s = session(h, idx)
            if s["state"] in ("gone", "?"):
                continue
            proj = project_of_path(s["path"]) or s["repo"] or "?"
            if re.search(r"协调|worker_done", s["last"]) or s["title"].startswith("Report ") or re.match(r"#\d+", s["title"]):
                team.setdefault(proj, []).append(s)
                continue
            others.append({"proj": proj, "item": s["title"][:40] or "—", "device": me, "progress": s["last"][:60] or "—", "dispatch": "没登记（不是 assistant 派的）",
                           "exec": who(s, "") + (" · 主会话" if h in mains else ""), "follow": "没配",
                           "wait": "主会话" if h in mains else ("在跑" if s["state"] == "working" else "看你要不要收"),
                           "next": "要纳入跟进就说一声" if h not in mains else "—"})
        for proj, ss in team.items():
            busy = sum(1 for s in ss if s["state"] == "working")
            others.append({"proj": proj, "item": f"团队 Orca 编排的 worker × {len(ss)}", "device": me, "progress": f"{busy} 个在干活；它们向团队协调者汇报",
                           "dispatch": "团队流程", "exec": "—", "follow": "团队协调者", "wait": "按团队流程", "next": "—"})
    return rows, others


def render(rows, others, names=None):
    """跟进总表。有多台设备时加「设备」一列（本机 / 别的设备）；只有本机时保持原来的列。"""
    names = names or {}
    multi = len(names) > 1
    head = ["项目"] + (["设备"] if multi else []) + ["事项", "当前进度", "派发", "执行会话", "跟进会话", "现在", "下一步"]
    keys = ["proj"] + (["device"] if multi else []) + ["item", "progress", "dispatch", "exec", "follow", "wait", "next"]
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
    line = lambda r: "| " + " | ".join(cell(dev_cell(r.get("device", ""), names) if k == "device" else r.get(k, "")) for k in keys) + " |"
    out = ["| " + " | ".join(head) + " |", "|---" * len(head) + "|"]
    if rows:
        out += [line(r) for r in rows]
    else:
        out.append(line({"proj": "—", "item": "（没有在跟的事）"}))
    if others:
        out += ["", "**Orca 里没登记的会话**（最近 24 小时有输出，不是 assistant 派发的）", "",
                "| " + " | ".join(head) + " |", "|---" * len(head) + "|"]
        out += [line(r) for r in others]
    return "\n".join(out)


def snapshot(rows, others, tidy="", names=None, slots=None):
    """写本设备的快照 runs/status/<指纹>.md，随 git 同步，别的设备的主会话和早间建议读它。
    names / slots：设备表和本机推进位（cmd_overview 已经算过就传进来，不再查一遍）。
    tidy：cleanup.py 的一行「可以清理：…」，写进 cleanup 字段，开场卡片读它。"""
    dev = device.load_device()
    names = devices_in_use() if names is None else names
    slots = local_slots() if slots is None else slots
    STATUS.mkdir(parents=True, exist_ok=True)
    todo = sum(1 for r in rows if r["wait"] in ("等你", "要回收", "等你验收", "我来处理", "我去看"))
    f = STATUS / f"{dev['fingerprint']}.md"
    f.write_text("\n".join([
        "---", "type: status", f"slug: {dev['fingerprint']}", "status: active", f"updated: {datetime.now():%Y-%m-%d}",
        f"device: {dev['fingerprint']}", f"hostname: {dev.get('hostname', '')}", f"generated: {datetime.now():%Y-%m-%d %H:%M}",
        f'summary: "设备 {dev.get("hostname", "")} 的跟进总表：{len(rows)} 件在跟，{todo} 个要处理；本机推进位 {len(slots)}/{MAX_SLOTS}"',
        f'cleanup: "{tidy}"', "---", "",
        f"# 跟进总表 · {dev.get('hostname', '')}（{dev['fingerprint']}）", "",
        f"> 生成于 {datetime.now():%Y-%m-%d %H:%M}，由 `python tools/sweep.py --snapshot` 写（早间建议、夜间巡检会跑）。会话只能在这台设备上查，别的设备看这份快照。", "",
        f"**{slot_line(slots, names)}**", "",
        render(rows, others, names), *(["", f"**{tidy}**（清单：`python tools/cleanup.py`，cleanup skill）"] if tidy else []), ""]), encoding="utf-8")
    return f


def other_devices():
    """别的设备的快照：[(主机名, 生成时间, 一句话, 路径)]。"""
    me, out = device.fingerprint(), []
    for f in sorted(STATUS.glob("*.md")) if STATUS.is_dir() else []:
        if f.stem == me or f.name == "INDEX.md":
            continue
        fm = fields(f.read_text(encoding="utf-8"))
        out.append((fm.get("hostname", f.stem), fm.get("generated", ""), fm.get("summary", ""), f))
    return out


def cmd_overview(a):
    info = env.detect()
    rows, others = board(auto_reclaim=a.auto_reclaim)
    names, slots = devices_in_use(), local_slots()
    print(slot_line(slots, names, live_known=info["orca_reachable"]))
    print()
    print(render(rows, others, names))
    for host, at, summary, f in other_devices():
        print(f"\n- 设备 {host}（快照 {at}）：{summary} · {f.relative_to(ROOT).as_posix()}")
    tidy = ""
    if info["orca_reachable"]:  # 做完的会话和工作区：提醒清理（cleanup skill）
        import cleanup  # 放这里：cleanup 也 import 本模块
        tidy = cleanup.brief_line(cleanup.inventory()[0])
        print(f"\n{tidy}" + ("（清单：python tools/cleanup.py）" if tidy.startswith("可以清理") else ""))
    try:  # 台账（今日计划「推进位与排队」三栏）：漏网的、暂停没写复查日期、复查日期到了
        from board import brief as board_brief
        ledger = board_brief()
        if ledger:
            print(f"\n{ledger}（明细：python tools/board.py）")
    except Exception:  # noqa: BLE001 —— 台账扫不出来不能挡住总览
        pass
    try:  # 僵尸运行记录：已结束 / 已让位但 status 还是 dispatched
        zombies = zombie_line() if info["orca_reachable"] else zombie_line(live=None)
    except Exception:  # noqa: BLE001 —— 这一行扫不出来不能挡住总览
        zombies = ""
    if zombies:
        print(f"\n{zombies}（不加 --apply 只出清单，不会改文件）")
    if a.snapshot:
        print(f"\n已写本设备快照：{snapshot(rows, others, tidy, names, slots).relative_to(ROOT).as_posix()}")


def adopt_candidates():
    """可以收编的会话：Orca 里 24 小时内有输出、没登记、不是团队 worker、不是主会话。[(handle, 会话, 项目)]"""
    idx = ps_index()
    known = set()
    for f in RUNS.glob("*.md"):
        if f.name not in ("pending.md", "INDEX.md"):
            ft = f.read_text(encoding="utf-8")
            known |= {h for _, h in handles_of(ft, fields(ft))}
    mains = {s.get("handle") for s in notify.load_state()["sessions"].values() if s.get("kind") == "main"}
    cutoff = (datetime.now().timestamp() - 86400) * 1000
    out = []
    for t in (orca("terminal", "list", "--limit", "300").get("result") or {}).get("terminals", []):
        h = t.get("handle", "")
        if not h or h in known or h in mains or h == os.environ.get("ORCA_TERMINAL_HANDLE") or int(t.get("lastOutputAt") or 0) < cutoff:
            continue
        s = session(h, idx)
        if s["state"] in ("gone", "?") or re.search(r"协调|worker_done", s["last"]) or s["title"].startswith("Report ") or re.match(r"#\d+", s["title"]):
            continue
        out.append((h, s, project_of_path(s["path"]) or s["repo"] or "other"))
    return out


def cmd_adopt(a):
    """收编：不是 assistant 派发的会话，补一份派发记录（kind: adopted），把它最后的总结记进「验收」，待处理清单补一条「完成」。"""
    cands = adopt_candidates()
    if a.handle:
        cands = [c for c in cands if c[0] == a.handle]
    if not cands:
        print("没有可以收编的会话（团队 worker、主会话、已登记的不收）")
        return
    now = datetime.now()
    for h, s, proj in cands:
        f = RUNS / f"{now:%Y-%m-%d}-adopt-{proj}-{h[5:13]}.md"  # 文件名用终端 handle 的前 8 位，标题写在正文
        if a.dry:
            print(f"- 会收编：{proj}：{s['title']}（{s['agent']}，{STATE_ZH.get(s['state'], s['state'])}）→ {f.relative_to(ROOT).as_posix()}")
            continue
        f.write_text("\n".join([
            "---", "type: run", "kind: adopted", f"slug: {f.stem}", "status: dispatched", f"created: {now:%Y-%m-%d %H:%M}",
            f"updated: {now:%Y-%m-%d}", "task: ''", f"project: {proj}", f"agent: {s['agent']}", "mode: adopted",
            f"target: '{s['path']}'", f"workdir: '{s['path']}'", f"handle: '{h}'", f"device: {device.fingerprint()}",
            f"dispatched_by: '不是 assistant 派发的；{now:%m-%d} 收编（你定）'",
            f'summary: "收编：{proj}：{s["title"]}"', "---", "", f"# [收编] {proj} · {s['title']}", "",
            "## 任务说明", "", f"你自己开的会话（或别的工具派的），{now:%Y-%m-%d} 收进 assistant 跟踪。原始任务见会话本身：{s['title']}。", "",
            "## 派发", "", f"- {now:%Y-%m-%d %H:%M} — 收编：Orca 终端 {h}，工作目录 {s['path']}", "",
            "## 跟进说明", "", "## 决策记录", "", "## 监督", "", "## 验收", ""]), encoding="utf-8")
        print(f"- ✅ 收编 {proj}：{s['title']} → {f.relative_to(ROOT).as_posix()}")
        if s["last"]:
            cmd_reclaim(argparse.Namespace(run=str(f), close=False))


def cmd_reclaim(a):
    p = Path(a.run)
    if not p.exists():
        p = RUNS / (a.run if a.run.endswith(".md") else a.run + ".md")
    if not p.exists():
        sys.exit(f"找不到派发记录 {a.run}")
    p = p.resolve()
    text = p.read_text(encoding="utf-8")
    fm = fields(text)
    hs = handles_of(text, fm)
    main = next((h for n, h in hs if n == "跟进 Agent"), None) or next((h for n, h in hs if n == "执行会话"), None)
    if not main:
        sys.exit("这个派发没有记录会话 handle（本地模式），只能看分支和提交收结果")
    s = session(main, ps_index())
    if not s["last"]:  # 有的工具（比如 OpenCode）不报最后一句，就读终端当前一屏
        term = (orca("terminal", "read", "--terminal", main, "--screen").get("result") or {}).get("terminal") or {}
        lines = [l.strip(" ┃│") for l in term.get("tail") or [] if l.strip() and not set(l.strip()) <= set("─━-▀▄ ")]
        s["last"] = re.sub(r"\s+", " ", " / ".join(lines[-15:]))
    if not s["last"]:
        sys.exit(f"会话现在是「{s['state']}」，读不到它最后的总结；去看一眼终端再说")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    append(p, "验收", f"- {now} — （回收）会话最后的总结（{s['state']}，{s['at']}）：{s['last'][:800]}")
    item = (f"{fm.get('project', '')}：{fm.get('req') or fm.get('task')}" if (fm.get("req") or fm.get("task"))
            else fm.get("summary", "").replace("收编：", ""))
    notify.cmd_send(argparse.Namespace(type="完成", item=item, text=f"（回收）{s['last'][:120]}", detail=str(p.resolve()),
                                       run=str(p), source="主会话回收", no_push=True))
    print(f"✅ 已回收进 {p.relative_to(ROOT).as_posix()} 的「验收」，待处理清单补了一条「完成」；任务状态没改，等你验收")
    if a.close:
        res = orca("terminal", "close", "--terminal", main)
        print("✅ 终端已关" if res.get("ok") else f"⚠ 关终端失败：{str(res.get('error'))[:200]}")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="回收派出去的会话")
    ap.add_argument("--snapshot", action="store_true", help="把本设备的现状写进 runs/status/<指纹>.md")
    ap.add_argument("--auto-reclaim", action="store_true", help="做完没回报的自动回收（夜间巡检用，只记录不关终端）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("reclaim"); p.add_argument("run"); p.add_argument("--close", action="store_true")
    p = sub.add_parser("adopt"); p.add_argument("--handle", help="只收这一个会话"); p.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    {"reclaim": cmd_reclaim, "adopt": cmd_adopt}.get(a.cmd, cmd_overview)(a)


if __name__ == "__main__":
    main()
