r"""
PreToolUse hook：主会话越界执行的防线（CLAUDE.md §2：主会话碰项目仓库只做只读查询，执行一律派发）。

主会话要在 assistant 以外的仓库做写操作时拦下（deny），告诉模型先出派发方案；你同时会看到一行提示。
  - Bash / PowerShell：git commit / push / merge / rebase / pull / reset / checkout / switch / stash / tag / branch -d …；
    gh pr merge / create / close / review / comment …、gh issue / release / repo 的写操作、gh api 的 POST / PUT / PATCH / DELETE
  - Edit / Write / NotebookEdit：文件在 assistant 以外的 git 仓库里
目标仓库：命令里的 cd / Set-Location / pushd、git -C <路径>、gh --repo / -R <owner/repo>，都没有就是当前目录。
assistant 本身（含它的 Orca 工作区，同一个 .git）不拦。
放行：
  - 派发出来的会话（tools/prompt_hook.py 登记为 dispatched，或会话第一句话带派发标记）、claude -p 无人值守会话
  - 你在这次对话里明确说了「主会话直接做」：命令末尾加注释 `# assistant:direct` 再执行（只对命令有效；改文件一律派发）
任何一步出错都放行，不挡正常工作。自测：python tools/guard_hook.py --test
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit("tools", 1)[0] + "tools")

from common import ROOT, norm_remote, origin_of, setup_stdio  # noqa: E402

DIRECT = "# assistant:direct"
SHELLS = ("Bash", "PowerShell")
EDITS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
# 派发出去的会话第一句话里的标记（和 tools/notify.py 的 DISPATCH_MARKS 一致）
DISPATCH_MARKS = ("【角色设定", "【本次任务】", "派发出来的", "tools/notify.py send", "无人值守")

GIT_WRITE = re.compile(
    r"\bgit(?:\s+(?:-C|-c)\s+(?:\"[^\"]*\"|'[^']*'|\S+)|\s+--no-pager)*\s+"
    r"(?:(?:commit|push|merge|rebase|cherry-pick|revert|reset|pull|am|apply|checkout|switch|restore|clean|rm|mv"
    r"|update-ref|filter-branch|filter-repo)\b(?!-)|stash\b(?!\s+(?:list|show)\b)|tag\s+(?!-l\b|--list\b)[^\s|;&]"
    r"|branch\s+(?:-[dDmMcC]\b|--delete|--move|--force)|worktree\s+(?:add|remove|move|prune)\b)")
GH_WRITE = re.compile(
    r"\bgh\s+(?:pr\s+(?:merge|create|close|edit|review|comment|ready|reopen|checkout)"
    r"|issue\s+(?:create|close|edit|comment|reopen|delete|transfer|lock)"
    r"|release\s+(?:create|edit|delete|upload)|repo\s+(?:delete|edit|archive|rename|sync)"
    r"|workflow\s+(?:run|enable|disable)|run\s+(?:rerun|cancel|delete)|label\s+(?:create|edit|delete)"
    r"|secret\s+(?:set|delete)|variable\s+(?:set|delete))\b", re.I)
GH_API_WRITE = re.compile(r"\bgh\s+api\b(?=.*(?:-X|--method)[=\s]*(?:POST|PUT|PATCH|DELETE)\b)", re.I)
CD = re.compile(r"^\s*(?:cd|chdir|pushd|Set-Location|Push-Location|sl)\s+(?:-(?:Path|LiteralPath)\s+)?(\"[^\"]+\"|'[^']+'|\S+)", re.I)
GIT_C = re.compile(r"\bgit(?:\s+-c\s+\S+)*\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)")
GH_REPO = re.compile(r"(?:--repo|-R)(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)")


def unquote(s):
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def to_path(raw, cwd):
    """命令里的路径 → 绝对路径：去引号，Git Bash 的 /d/x 写法换成 D:/x，~ 展开，相对路径按当前目录。"""
    raw = unquote(raw.strip())
    m = re.match(r"^/([a-zA-Z])(/.*)?$", raw)
    if m and os.name == "nt":
        raw = f"{m.group(1).upper()}:{m.group(2) or '/'}"
    p = Path(os.path.expanduser(raw))
    return p if p.is_absolute() else Path(cwd) / p


def common_dir(path):
    """所在 git 仓库的公共 .git 目录（同一仓库的 Orca 工作区相同）；不在 git 仓库里返回 None。"""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    if p.is_file():
        p = p.parent
    try:
        r = subprocess.run(["git", "-C", str(p), "rev-parse", "--git-common-dir"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    d = Path(r.stdout.strip())
    return os.path.normcase(os.path.normpath(d if d.is_absolute() else p / d))


def outside(target):
    """target 是路径（Path）或 owner/repo（str）：在 assistant 以外的仓库返回 True；assistant 本身或不是仓库返回 False。"""
    if isinstance(target, str):
        own = (origin_of(ROOT) or "<your-github>/cloneme").lower()
        return norm_remote(target).lower() not in (own, own.split("/")[-1])
    try:
        Path(target).resolve().relative_to(ROOT.resolve())
        return False
    except (ValueError, OSError):
        pass
    cd = common_dir(target)
    return cd is not None and cd != common_dir(ROOT)


def shell_hits(command, cwd):
    """命令里在 assistant 以外仓库的写操作：[(操作, 目标)]。按 && ; || | 换行拆段，cd 会改变后面各段的当前目录。"""
    hits, here = [], Path(cwd)
    for seg in re.split(r"&&|\|\||[;\n|]", command):
        m = CD.match(seg)
        if m:
            here = to_path(m.group(1), here)
            continue
        op = GIT_WRITE.search(seg) or GH_WRITE.search(seg) or GH_API_WRITE.search(seg)
        if not op:
            continue
        g, r = GIT_C.search(seg), GH_REPO.search(seg)
        api = GH_API_WRITE.search(seg) and re.search(r"\brepos/([\w.-]+/[\w.-]+)", seg)
        if g:
            target = to_path(g.group(1), here)
        elif r and seg.lstrip().startswith("gh"):
            target = unquote(r.group(1))
        else:
            target = api.group(1) if api else here
        if outside(target):
            hits.append((re.sub(r"\s+", " ", op.group(0)).strip(), target if isinstance(target, str) else Path(target).as_posix()))
    return hits


def first_prompt(transcript):
    """会话第一句用户消息（transcript JSONL 里第一条 type=user 的文本）。"""
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                if d.get("type") != "user":
                    continue
                c = (d.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                if isinstance(c, str) and c.strip():
                    return c
    except (OSError, ValueError):
        pass
    return ""


def session_kind(data):
    """main / dispatched / auto。拿不准的按主会话算（宁可多拦一次，模型会改成出派发方案）。"""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "cli") != "cli":
        return "auto"
    try:
        state = json.loads((ROOT / "inbox" / "logs" / "main-sessions.json").read_text(encoding="utf-8"))
        kind = (state.get("sessions", {}).get(data.get("session_id") or "") or {}).get("kind")
        if kind:
            return kind
    except (OSError, ValueError):
        pass
    prompt = first_prompt(data.get("transcript_path") or "")
    return "dispatched" if any(m in prompt for m in DISPATCH_MARKS) else "main"


def check(data, kind=session_kind):
    """返回拦截原因；放行返回 None。"""
    tool, inp, cwd = data.get("tool_name", ""), data.get("tool_input") or {}, data.get("cwd") or os.getcwd()
    if tool in SHELLS:
        cmd = inp.get("command") or ""
        if DIRECT in cmd:
            return None
        hits = shell_hits(cmd, cwd)
        what = "；".join(f"`{op}`（{tgt}）" for op, tgt in hits)
    elif tool in EDITS:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        hits = [path] if path and outside(to_path(path, cwd)) else []
        what = f"改 {path}"
    else:
        return None
    if not hits or kind(data) != "main":
        return None
    return what


def reason(what, tool):
    direct = ("你在这次对话里明确说了「主会话直接做」才可以：命令末尾加注释 `# assistant:direct` 再执行一次，并在 journal 记一笔。"
              if tool in SHELLS else "改项目文件一律派发，没有直接做的口子。")
    return (f"助手主会话不在项目仓库执行写操作（CLAUDE.md §2）：{what}。"
            "你说「帮我处理 / 合 / 做」项目上的事 = 先出派发方案：用 dispatch skill（合 PR、解冲突这类小事用轻量派发），"
            "确认后派出去做；主会话只做只读查询（gh pr view / git log / git diff 之类）。" + direct)


def main():
    setup_stdio()
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except (ValueError, OSError):
        return
    what = check(data)
    if not what:
        return
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                               "permissionDecisionReason": reason(what, data.get("tool_name"))},
        "systemMessage": f"🛡 拦下主会话在项目仓库的写操作：{what}——先出派发方案",
    }, ensure_ascii=False))


def selftest():
    """离线自测：命令识别和目标仓库判断（会话类型固定当主会话），再看一眼会话类型判断。"""
    import tempfile
    other = Path(tempfile.mkdtemp(prefix="guard-"))
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    o, me = other.as_posix(), ROOT.as_posix()
    gb = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), o)  # Git Bash 写法 /c/Users/…
    cases = [
        (f"cd {o} && git commit -m 'x; y'", True), (f"git -C {o} push origin feat", True), (f"cd {gb} && git push", True),
        (f"cd {o} && git log --oneline -5", False), (f"git -C {o} status && git -C {o} diff", False),
        (f"cd {o} && git merge-base HEAD main", False), (f"cd {o}; git stash list", False), (f"cd {o}; git stash push -m x", True),
        (f"cd {o} && git tag", False), (f"cd {o} && git tag v1.0", True), (f"cd {o} && git branch -D old", True),
        (f"cd {me} && git commit -m 'assistant 自己'", False), (f"git -C {me} merge feat", False),
        (f"cd {o} && git log; cd {me} && git commit -m x", False),
        ("gh pr merge 12 --repo example-org/example-api --squash", True), ("gh pr view 12 --repo example-org/example-api", False),
        ("gh pr merge 3 --repo <your-github>/cloneme", False), (f"cd {o} && gh pr create --fill", True),
        ("gh api repos/example-org/example-app/pulls/1/merge -X PUT", True), ("gh api repos/example-org/example-app/pulls/1", False),
        (f"Set-Location '{o}'; git merge develop", True), (f"cd {o} && git push {DIRECT}", False),
    ]
    main_only = lambda data: "main"  # noqa: E731
    bad = 0
    for cmd, want in cases:
        got = bool(check({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": me}, main_only))
        bad += got != want
        print(("✅" if got == want else "❌") + f" {'拦' if got else '放'} {cmd}")
    for path, want in ((f"{o}/src/a.py", True), (f"{me}/journal/x.md", False), (str(Path.home() / ".claude" / "x.md"), False)):
        got = bool(check({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": me}, main_only))
        bad += got != want
        print(("✅" if got == want else "❌") + f" {'拦' if got else '放'} Edit {path}")
    got = bool(check({"tool_name": "Bash", "tool_input": {"command": f"git -C {o} push"}, "cwd": me}, lambda d: "dispatched"))
    bad += got
    print(("✅" if not got else "❌") + " 派发出来的会话：放")
    print(f"\n{'全部通过' if not bad else f'{bad} 条不对'}。当前会话判为："
          + session_kind({"session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", "")}))
    import shutil
    shutil.rmtree(other, ignore_errors=True)
    return bad


if __name__ == "__main__":
    if "--test" in sys.argv:
        setup_stdio()
        sys.exit(1 if selftest() else 0)
    try:
        main()
    except Exception:  # noqa: BLE001 —— hook 不能挡住正常工作
        pass
