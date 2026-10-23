"""
安装 pre-push 体积闸：把仓库的 pre-push 钩子装成调用 tools/push_guard.py 的 sh 脚本（Windows 上 git 自带的 sh 跑）。

钩子目录按 git rev-parse --git-path hooks 找：认 core.hooksPath，Orca 工作区（worktree）和主目录共用一份。
钩子先找当前仓库自己的 tools/push_guard.py（只对 assistant），再找 assistant 主目录的、安装时这份的，都找不到就拦下推送。
已有别人写的 pre-push 不覆盖，要覆盖加 --force（原文件备份成 pre-push.assistant-bak）。
装完用 git push --dry-run 推到一个临时裸仓库做自检，确认钩子真的跑了。
```
python tools/install_hooks.py                 # 给 assistant 自己装（主目录和所有工作区共用）
python tools/install_hooks.py --repo <路径>   # 给别的仓库装
python tools/install_hooks.py --check         # 只检查装没装（0 = 装好了，1 = 没装）
```
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = "# assistant-push-guard"
TAG = "[体积闸]"


def git(args, cwd, env=None):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def toplevel(repo):
    code, out, err = git(["rev-parse", "--show-toplevel"], repo)
    if code != 0:
        raise SystemExit(f"{repo} 不是 git 仓库：{err}")
    return Path(out)


def hooks_dir(top):
    """钩子目录：认 core.hooksPath；worktree 返回共享的那份。相对路径按仓库顶层算。"""
    code, out, err = git(["rev-parse", "--git-path", "hooks"], top)
    if code != 0:
        raise SystemExit(f"找不到钩子目录：{err}")
    p = Path(out)
    return p if p.is_absolute() else (top / p).resolve()


def common_dir(top):
    code, out, _ = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], top)
    return Path(out).resolve() if code == 0 else None


def main_checkout():
    """assistant 主目录（git worktree list 的第一项）；拿不到就用这份文件所在的仓库。"""
    code, out, _ = git(["worktree", "list", "--porcelain"], ROOT)
    for line in out.splitlines() if code == 0 else []:
        if line.startswith("worktree "):
            return Path(line[len("worktree "):])
    return ROOT


def is_assistant(top):
    return common_dir(top) == common_dir(ROOT)


def guard_candidates(top, guards=None):
    """钩子依次尝试的 push_guard.py；第一项 None 表示「当前仓库自己的 tools/push_guard.py」。"""
    if guards:
        return [Path(g) for g in guards]
    found = [None] if is_assistant(top) else []
    for g in (main_checkout() / "tools" / "push_guard.py", ROOT / "tools" / "push_guard.py"):
        if g not in found:
            found.append(g)
    return found


def _sh(path):
    return str(path).replace("\\", "/")


def hook_script(candidates):
    lines = []
    for g in candidates:
        lines.append('"$top/tools/push_guard.py"' if g is None else f'"{_sh(g)}"')
    pys = [f'"{_sh(sys.executable)}"'] if sys.executable else []
    return f"""#!/bin/sh
{MARK}：由 assistant 的 tools/install_hooks.py 安装，重装会覆盖这个文件，别手改
# 推送前检查这次要传的新对象：累计超过 10MB、单个文件超过 1MB、新增文本里有密钥特征，就拒绝推送
# 确实要推：ASSISTANT_PUSH_OK=1 git push ...（PowerShell：$env:ASSISTANT_PUSH_OK='1'; git push ...）
top=$(git rev-parse --show-toplevel 2>/dev/null)
for guard in {' '.join(lines)}; do
  [ -f "$guard" ] || continue
  for py in {' '.join(pys)} python python3; do
    case "$py" in
      */*) [ -f "$py" ] || continue ;;
      *) command -v "$py" >/dev/null 2>&1 || continue ;;
    esac
    exec "$py" "$guard" "$@"
  done
  break
done
if [ "$ASSISTANT_PUSH_OK" = "1" ]; then
  echo "{TAG} 警告：找不到 push_guard.py 或 python，ASSISTANT_PUSH_OK=1 直接放行" >&2
  exit 0
fi
echo "{TAG} 找不到 push_guard.py 或 python，拦下推送；重装：python tools/install_hooks.py；确实要推：ASSISTANT_PUSH_OK=1 git push ..." >&2
exit 1
"""


def selftest(top):
    """git push --dry-run 推到临时裸仓库，看钩子有没有跑。返回 (钩子跑了?, 输出)。"""
    tmp = Path(tempfile.mkdtemp(prefix="assistant-push-selftest-"))
    try:
        bare = tmp / "remote.git"
        git(["init", "-q", "--bare", str(bare)], tmp)
        env = {k: v for k, v in os.environ.items() if k != "ASSISTANT_PUSH_OK"}
        code, out, err = git(["push", "--dry-run", str(bare), "HEAD:refs/heads/assistant-push-guard-selftest"], top, env)
        text = "\n".join(x for x in (out, err) if x)
        return TAG in text, code, text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check(top):
    hook = hooks_dir(top) / "pre-push"
    if not hook.is_file():
        return False, f"没装：{hook} 不存在"
    text = hook.read_text(encoding="utf-8", errors="replace")
    if MARK not in text:
        return False, f"没装：{hook} 是别的钩子"
    return True, f"已装：{hook}"


def install(repo=None, force=False, guards=None, run_selftest=True, out=print):
    """装钩子；返回退出码。guards 给测试用：指定钩子去找的 push_guard.py。"""
    top = toplevel(Path(repo) if repo else ROOT)
    hdir = hooks_dir(top)
    hook = hdir / "pre-push"
    if hook.is_file():
        old = hook.read_text(encoding="utf-8", errors="replace")
        if MARK not in old:
            if not force:
                out(f"{hook} 已有别的 pre-push 钩子，没覆盖；确认要换成体积闸加 --force（原文件会备份）")
                return 1
            shutil.copy2(hook, hook.with_name("pre-push.assistant-bak"))
            out(f"原钩子备份到 {hook.with_name('pre-push.assistant-bak')}")
    hdir.mkdir(parents=True, exist_ok=True)
    with open(hook, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_script(guard_candidates(top, guards)))
    os.chmod(hook, 0o755)
    out(f"已装：{hook}（仓库 {top}）")
    if not run_selftest:
        return 0
    ran, code, text = selftest(top)
    if not ran:
        out(f"自检失败：git push --dry-run 没有触发体积闸（退出码 {code}）\n{text}")
        return 1
    verdict = "通过" if code == 0 else "钩子生效，但当前 HEAD 里有没推过的内容被拦下（见下面）"
    out(f"自检：{verdict}")
    out("\n".join(line for line in text.splitlines() if TAG in line or line.startswith("    ")))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(description="安装 pre-push 体积闸")
    ap.add_argument("--repo", help="要装的仓库（默认 assistant 自己）")
    ap.add_argument("--check", action="store_true", help="只检查装没装")
    ap.add_argument("--force", action="store_true", help="覆盖已有的别的 pre-push 钩子（先备份）")
    ap.add_argument("--no-selftest", action="store_true", help="装完不做自检推送")
    args = ap.parse_args(argv)
    if args.check:
        ok, msg = check(toplevel(Path(args.repo) if args.repo else ROOT))
        print(msg)
        return 0 if ok else 1
    return install(args.repo, args.force, run_selftest=not args.no_selftest)


if __name__ == "__main__":
    sys.exit(main())
