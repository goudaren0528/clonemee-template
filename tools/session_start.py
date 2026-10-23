r"""
SessionStart hook：每次会话启动时检测运行环境和各项状态。

输出 JSON：
  systemMessage      直接显示给你的问候和状态（不经过模型，启动就能看到）
  additionalContext  注入模型上下文的完整状态 + memory/INDEX.md（L0）
内容：运行环境（是否在 Orca 里、派发方式、可用 agent）、本次会话记忆路径、设备路径检查、journal 断档、
待处理记忆、计划任务、今日建议、固定日程（今天 / 明天，来自 areas/work/index.md 的「固定日程」表，
支持「每周二 10:30」和「2027-03-18 14:00」两种写法，提前天数默认 1 天，解析见 tools/schedule.py）、
待处理消息（runs/pending.md）、指挥台通道（v1.1：主目录里确保在跑并给链接，
另列指挥台回复待转条数）、GTD 状态、git 状态。任何一步出错都只跳过，不让会话启动失败。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, __file__.rsplit("tools", 1)[0] + "tools")

from common import ROOT, run, setup_stdio  # noqa: E402


def session_file(session_id):
    sid8 = (session_id or "manual")[:8]
    hits = sorted((ROOT / "memory" / "sessions").glob(f"*-{sid8}.md"))
    rel = hits[0] if hits else ROOT / "memory" / "sessions" / f"{date.today().isoformat()}-{sid8}.md"
    return rel.relative_to(ROOT).as_posix(), bool(hits)


def safe(fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 —— hook 不能因为任何错误挂掉
        return default(e) if callable(default) else default


def short(text, n=34):
    text = re.sub(r"\s+", " ", re.split(r"[；;。]", text, 1)[0]).strip()
    return text if len(text) <= n else text[:n] + "…"


def brief_lines(daily, fm, tasks):
    """今日建议草案的展开：一句话结论、每项一行、要你拍板的问题。"""
    lines = []
    text = daily.read_text(encoding="utf-8")
    m = re.search(r'^summary:\s*"?(.*?)"?\s*$', text, re.M)  # 直接读原文：summary 里的 # 会被 YAML 当成注释
    if m:
        lines.append(f"📋 今日建议（草案）：{m.group(1)}")
    lines += [f"  {t['id']} {short(t['title'])}" for t in tasks]
    m = re.search(r"^## 需要你确认\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    asks = [re.sub(r"^\d+\.\s*", "", l).strip() for l in (m.group(1) if m else "").splitlines() if re.match(r"^\d+\.", l)]
    if asks:
        lines.append(f"❓ 需要你拍板（{len(asks)} 件）：")
        lines += [f"  {i}. {short(a, 46)}" for i, a in enumerate(asks, 1)]
    return lines


def channel_lines(ensure, waiting):
    """指挥台本地通道（v1.1）：确保在跑并给链接，列出还没送到主会话的指挥台回复。返回 (上下文行, 卡片行)。

    ensure()  -> console_channel.ensure 的结果 {ok, url, reused, reason}；任何异常都当作没起来
    waiting() -> notify.replies_waiting() 的列表
    工作区副本（派发出去的会话）不起通道，也不在卡片上提，免得打扰。
    """
    out, greet = [], []
    try:
        ch = ensure() or {}
    except Exception as e:  # noqa: BLE001 —— 通道起不来不能挡住会话启动
        ch = {"ok": False, "reason": f"通道检查失败：{type(e).__name__}"}
    if ch.get("ok"):
        out.append(f"- 指挥台：{ch['url']}（{'复用' if ch.get('reused') else '新起'}）——你说「打开指挥台」给这个链接；"
                   "「关掉指挥台」用 `python tools/console_channel.py stop`（下次在主目录启动会话会再起来）")
        greet.append(f"指挥台：{ch['url']}")
    else:
        reason = ch.get("reason") or "原因未知"
        out.append(f"- 指挥台通道：{reason}" + ("" if "副本" in reason else "；面板仍可用本机文件打开，回复改为复制指令"))
        if "副本" not in reason:
            greet.append(f"指挥台没起来：{short(reason, 30)}")
    try:
        n = len(waiting() or [])
    except Exception:  # noqa: BLE001
        n = 0
    if n:
        out.append(f"- 指挥台回复 {n} 条待转：你在面板上发的、还没送到主会话。`python tools/notify.py replies --waiting` 逐条看，"
                   "按 playbooks/comms.md「主会话收到回复」处理（forward / show + handled）")
        greet.append(f"指挥台回复 {n} 条待转")
    return out, greet


def next_step(greet, daily):
    """开场的下一步提示：按 CLAUDE.md §2 的优先级。"""
    gtd_line = next((g for g in greet if g.startswith("⚠ GTD")), "")
    if gtd_line:
        gtd = ("；还没做过 GTD 盘点，说「发起事情盘点」" if "还没做过" in gtd_line
               else "；GTD 盘点已超期，说「发起事情盘点」")
    else:
        gtd = ""
    pend = next((g for g in greet if g.startswith("待处理 ")), "")
    if pend:
        gtd += f"；{pend}消息，说「看看待处理」"
    if any("待你确认" in g for g in greet):
        return "回一句「确认今日建议」，我带你逐项过" + gtd
    if any(g.startswith("今日建议：还没生成") for g in greet):
        return "说「生成今日建议」，我派后台会话去做" + gtd
    return "说说今天要做什么，或者「汇报进展」" + gtd


def main():
    setup_stdio()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        data = {}
    now = datetime.now()
    week = "一二三四五六日"[now.weekday()]
    hi = "早上好" if now.hour < 11 else "中午好" if now.hour < 13 else "下午好" if now.hour < 18 else "晚上好"
    out = ["<assistant-context source=\"SessionStart hook\">", "## 会话启动上下文（自动注入）", "",
           f"- 时间：{now:%Y-%m-%d %H:%M} 周{week} · 触发：{data.get('source', 'startup')}"]
    greet = []  # 给你看的状态行

    import env
    info = safe(env.detect, {})
    env_line = safe(lambda: env.summary(info), "运行环境：检测失败")
    out.append("- " + env_line)
    greet.append("环境：" + (f"Orca {info.get('orca_version')}" if info.get("in_orca") else info.get("host", "?"))
                 + " · 派发：" + ("Orca 工作区" if info.get("orca_reachable") else "本地终端")
                 + " · agent：" + ("/".join(info.get("agents_available") or []) or "无"))

    rel, exists = session_file(data.get("session_id", ""))
    out.append(f"- 本次会话记忆：`{rel}`（" + ("已存在，继续更新" if exists else
               "尚未创建：本次会话第一轮有实质内容就按 templates/session.md 创建；Stop hook 会检查") + "）")

    import device
    lines = safe(lambda: device.summary(*device.check()), lambda e: [f"设备检查失败：{e}"])
    out.append("- " + lines[0])
    out += ["  " + l for l in lines[1:]]
    greet.append("设备：" + lines[0].split("：", 1)[0].replace("设备 ", "") + ("（路径有变化）" if len(lines) > 1 else ""))

    # 派出去的会话：读各设备的派发快照（早间建议、夜间巡检写的；现查会话太慢）
    for snap in sorted((ROOT / "runs" / "status").glob("*.md")) if (ROOT / "runs" / "status").is_dir() else []:
        if snap.name == "INDEX.md":
            continue
        head = snap.read_text(encoding="utf-8").split("\n---", 1)[0]
        g = lambda k: (re.search(rf"^{k}:\s*\"?(.*?)\"?\s*$", head, re.M) or [None, ""])[1]
        m = re.search(r"(\d+) 个要处理", g("summary"))
        if snap.stem != device.fingerprint():
            out.append(f"- 设备 {g('hostname')} 的派发快照（{g('generated')}）：{g('summary')}——那台设备上的会话只能在那台设备上处理")
            greet.append(f"设备 {g('hostname')}：{g('summary').split('：')[-1]}（{g('generated')[5:]}）")
        elif m and int(m.group(1)):
            out.append(f"- 本设备派发快照（{g('generated')}）：{g('summary')}——开场提醒你，现查用 `python tools/sweep.py`")
            greet.append(f"派出去的：{m.group(1)} 个要处理（{g('generated')[5:]} 的快照），说「看看派出去的」")
        if snap.stem == device.fingerprint() and g("cleanup").startswith("可以清理"):
            # 做完的会话和工作区（cleanup skill）：快照里 sweep.py 记的一行，开场提醒，你确认后再清
            out.append(f"- {g('cleanup')}（{g('generated')} 的快照）——开场提醒你，清单用 `python tools/cleanup.py`，确认后才清（cleanup skill）")
            greet.append(f"{g('cleanup').split('；')[0]}（{g('generated')[5:]} 的快照），说「清理一下」")

    # 换设备时：有未完成任务、但本设备没有克隆的项目（只提有任务的，免得刷屏）
    import clone
    gaps = [m for m in safe(clone.missing, []) if m[2]]
    if gaps:
        out.append("- ⚠ 本设备缺克隆、但有未完成任务：" + "；".join(f"{p}（{gh}，{n} 个任务，建议放 {where}）" for p, gh, n, where in gaps)
                   + "——开场提醒你；你说「克隆 xx」后用 `python tools/clone.py <仓库> --run`")
        greet.append("⚠ 本设备缺克隆：" + "、".join(f"{p}（{n} 个任务）" for p, gh, n, where in gaps) + "，说「克隆 <项目>」")

    journals = sorted(f for f in (ROOT / "journal").rglob("????-??-??.md") if f.stem <= date.today().isoformat())
    if journals:
        last = journals[-1].stem
        gap = (date.today() - date.fromisoformat(last)).days
        out.append(f"- journal：最近一篇 {last}（{gap} 天前）" + ("——超过 3 天，开场提一句断档" if gap > 3 else ""))
        if gap > 3:
            greet.append(f"⚠ journal 已断档 {gap} 天")

    import recall
    import build_index
    safe(build_index.build, None)  # 重建总地图、各目录索引和记忆索引（只有内容变化时才写）
    n_due = safe(lambda: len(recall.due()), 0)
    if n_due:
        out.append(f"- 记忆待巩固/复核 {n_due} 条：`python tools/recall.py --due`")

    import plan
    plan_line = safe(plan.brief, "")
    if plan_line:
        out.append("- " + plan_line)
        greet.append(plan_line.split("。")[0])

    # 固定日程（areas/work/index.md 的「固定日程」表）：今天 / 明天有就各一行，解析失败只提示一行
    import schedule
    sch = safe(lambda: schedule.card(date.today()),
               {"today": [], "tomorrow": [], "reminders": [], "error": ""})
    if sch.get("error"):
        out.append(f"- ⚠ 固定日程解析失败：{sch['error']}（表在 `areas/work/index.md`，`python tools/schedule.py` 看明细）")
        greet.append("⚠ 固定日程解析失败")
    for key, name in (("today", "今日固定日程"), ("tomorrow", "明日固定日程")):
        if sch.get(key):
            items = "；".join(schedule.label(e, date.today() + timedelta(days=1) if key == "tomorrow" else None)
                             for e in sch[key])
            out.append(f"- {name}：{items}")
            greet.append(f"{name}：{items}")

    daily = ROOT / "plans" / "daily" / f"{date.today().isoformat()}.md"
    detail = []  # 今日建议的展开内容，接在状态行后面直接给你看
    if daily.exists():
        fm, tasks = safe(lambda: plan.parse_plan(daily), ({}, []))
        done = sum(1 for t in tasks if t["state"] == "完成")
        if str(fm.get("status")) == "draft":
            out.append(f"- 今日建议已生成、**待你确认**：`{daily.relative_to(ROOT).as_posix()}`（{len(tasks)} 项）——开场先带你确认（morning-brief skill · B）")
            greet.append(f"今日建议：{len(tasks)} 项待你确认")
            detail = safe(lambda: brief_lines(daily, fm, tasks), [])
        else:
            out.append(f"- 今日事项已确认：{len(tasks)} 项，完成 {done} 项（`{daily.relative_to(ROOT).as_posix()}`）")
            greet.append(f"今日事项：{done}/{len(tasks)} 完成")
            detail = [f"  {'✅' if t['state'] == '完成' else '⬜'} {t['id']} {short(t['title'])}" for t in tasks]
    elif now.weekday() < 6 and now.hour >= 10:
        out.append("- 今天还没有早间建议：后台派发 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/agent-run.ps1 -Job morning` 生成，跑完只读产出（morning-brief skill · B）")
        greet.append("今日建议：还没生成")

    # 台账（今日计划「推进位与排队」三栏）：只在有问题时显示一行
    import board
    ledger = safe(board.brief, "")
    if ledger:
        out.append(f"- {ledger}——明细 `python tools/board.py`；到复查日期的按 report-cards 出卡片问你，"
                   "暂停缺日期的当场补齐")
        greet.append(ledger)

    # 僵尸运行记录（已结束 / 已让位但 status 还是 dispatched）：只报一行，清单用 follow.py --reconcile
    import follow
    n_zombie = safe(lambda: follow.zombie_count(live=None if not info.get("orca_reachable") else follow.UNKNOWN), 0)
    if n_zombie:
        out.append(f"- 有 {n_zombie} 条僵尸运行记录（疑似已结束 / 已让位）：`python tools/follow.py --reconcile` 看清单，"
                   "确认后加 `--apply`（建议关闭的改 done，建议转你等的只补 next_check）")
        greet.append(f"有 {n_zombie} 条僵尸运行记录（疑似已结束 / 已让位），跑 python tools/follow.py --reconcile 看清单")

    import notify
    n_pending = safe(notify.pending_count, 0)
    if n_pending:
        # 派出去的会话回报的消息（会话间通信）：只显示条数，不展开
        out.append(f"- 待处理 {n_pending} 条：派出去的会话回报的消息（完成 / 要拍板 / 卡住 / 交接），`python tools/notify.py list` 查看，按 playbooks/comms.md 处理")
        greet.append(f"待处理 {n_pending} 条")

    import console_channel
    ch_out, ch_greet = channel_lines(lambda: console_channel.ensure(ROOT, wait=1.5), notify.replies_waiting)
    out += ch_out
    greet += ch_greet

    import inventory
    need, msg = safe(inventory.gtd_status, (False, ""))
    if need:
        out.append("- ⚠ GTD：" + msg + "；" + safe(inventory.brief, ""))
        head = msg.split("。")[0].split("；")[0]
        greet.append("⚠ GTD：" + head + ("，需要你发起" if "还没做过" not in head else "，需要你发起"))

    dirty = len((run(["git", "status", "--porcelain"], cwd=str(ROOT)) or "").splitlines())
    ahead = run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=str(ROOT))
    ahead_txt = f"未推送 {ahead.strip()} 个提交" if ahead is not None else "没有上游远端"
    out.append(f"- git：未提交改动 {dirty} 个；{ahead_txt}")
    greet.append(f"git：未提交 {dirty} · {ahead_txt}")

    index = ROOT / "memory" / "INDEX.md"
    if index.exists():
        body = [l for l in index.read_text(encoding="utf-8").splitlines() if not l.startswith(">")]
        out += ["", "---", ""] + body
    out += ["", "开场要求：你的第一句话之后（或带开场白启动时），先打招呼，用一张小表报上面的运行环境和状态，再按 CLAUDE.md §2 提醒和建议。",
            "</assistant-context>"]

    message = f"{hi}！助手已就绪（{now:%m-%d} 周{week} {now:%H:%M}）\n" + "\n".join("· " + g for g in greet)
    if data.get("source") != "compact":
        # 启动时模型不会主动开口（要等你第一句话），所以把今天要处理的事直接展开给你看
        if detail:
            message += "\n\n" + "\n".join(detail)
        message += "\n\n→ " + next_step(greet, daily)
    print(json.dumps({"systemMessage": message,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(out)}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
