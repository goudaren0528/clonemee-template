r"""
运行环境识别 —— 判断当前会话跑在哪里、有哪些能力可用（Orca 里外都能识别）。

    python tools/env.py          # 一行摘要
    python tools/env.py --json   # 结构化结果（dispatch.py 等工具用）

识别依据：Orca 会给它启动的终端注入 TERM_PROGRAM=Orca 和 ORCA_* 环境变量；
不在 Orca 里时，再看 Windows Terminal / VS Code / 普通终端，以及本机装了哪些 agent CLI。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys

from common import run, setup_stdio

AGENTS = ("claude", "codex", "opencode")


def detect(probe=True):
    e = os.environ
    in_orca = e.get("TERM_PROGRAM") == "Orca" or bool(e.get("ORCA_WORKSPACE_ID"))
    orca_cli = shutil.which("orca")
    info = {
        "os": platform.system(),
        "host": "Orca" if in_orca else ("Windows Terminal" if e.get("WT_SESSION")
                                         else "VS Code" if e.get("TERM_PROGRAM") == "vscode"
                                         else e.get("TERM_PROGRAM") or "普通终端"),
        "in_orca": in_orca,
        "orca_version": e.get("ORCA_APP_VERSION", ""),
        "orca_workspace": e.get("ORCA_WORKSPACE_ID", ""),
        "orca_cli": orca_cli or "",
        "orca_reachable": False,
        "agent": e.get("AI_AGENT", ""),
        "agents_available": [a for a in AGENTS if shutil.which(a)],
    }
    # 不在 Orca 终端里，但本机装了 Orca 且正在运行时，也能用它的 CLI 派发
    if orca_cli and probe:
        out = run([orca_cli, "status", "--json"], timeout=20) or ""
        try:
            r = json.loads(out).get("result", {})
            info["orca_reachable"] = bool((r.get("runtime") or {}).get("reachable") or r.get("runtimeReachable"))
        except (ValueError, AttributeError):
            info["orca_reachable"] = "runtimeReachable: true" in out
    return info


def summary(info):
    if info["in_orca"]:
        head = f"Orca {info['orca_version']}（工作区 {info['orca_workspace'].split('::')[-1]}）"
    else:
        head = info["host"] + ("；本机 Orca 正在运行，可以通过它的 CLI 派发" if info["orca_reachable"] else "")
    dispatch = "Orca 工作区派发" if info["orca_reachable"] else "本地终端派发（没有 Orca 能力）"
    agents = "、".join(info["agents_available"]) or "无"
    return f"运行环境：{head}；派发方式：{dispatch}；可用 agent：{agents}"


def main():
    setup_stdio()
    info = detect()
    print(json.dumps(info, ensure_ascii=False, indent=2) if "--json" in sys.argv else summary(info))


if __name__ == "__main__":
    main()
