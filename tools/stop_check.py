r"""
Stop hook：保证每次会话都留下会话记忆（规范见 playbooks/memory.md）。

本次会话还没有 memory/sessions/*-<会话ID前8位>.md 时，阻止结束一次，提示先写会话记忆；
第二次触发（stop_hook_active）直接放行，不会死循环。
Orca 自动化的启动器会话（只负责把定时任务转到后台，由 tools/agent-run.ps1 登记在
inbox/logs/launcher-sessions.txt）不要求写：真正干活的无头会话会写自己的会话记忆。
"""
from __future__ import annotations

import json
import sys
from datetime import date

sys.path.insert(0, __file__.rsplit("tools", 1)[0] + "tools")

from common import ROOT, setup_stdio  # noqa: E402


def _is_launcher(session_id: str) -> bool:
    path = ROOT / "inbox" / "logs" / "launcher-sessions.txt"
    try:
        return session_id in path.read_text(encoding="utf-8-sig").split()
    except OSError:
        return False


def main():
    setup_stdio()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return
    if data.get("stop_hook_active"):
        return
    sid8 = (data.get("session_id") or "")[:8]
    if sid8 and _is_launcher(data.get("session_id")):
        return
    if not sid8 or list((ROOT / "memory" / "sessions").glob(f"*-{sid8}.md")):
        return
    target = f"memory/sessions/{date.today().isoformat()}-{sid8}.md"
    print(json.dumps({
        "decision": "block",
        "reason": (f"本次会话还没有会话记忆。按 playbooks/memory.md 用 templates/session.md 创建 {target}"
                   f"（session_id: {data.get('session_id')}），写清要点、状态信号、未结、我的承诺、写入的文件；"
                   "能沉淀为长期记忆的同步写入；然后跑 python tools/recall.py --reindex 再结束。"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
