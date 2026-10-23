r"""
交接包 —— 派发时把任务引用到的 assistant 文件（PRD、原型、文档）复制一份进工作区。

- 原件留在 assistant，工作区里是只读副本，开头注明来源提交；要改回 assistant 改，再重新派发或同步，不做双向同步
- 单个任务放 <工作目录>/.assistant/handoff/<计划>-<任务>/，并写进仓库的本地排除文件（.git/info/exclude），
  不改项目的 .gitignore，也不会被提交（团队仓库、自己的仓库都一样）
- 开发交付流水线照旧放 docs/delivery/<需求>/ 跟着代码提交（delivery.py）；团队仓库不走流水线，按团队流程
- 任务就在 assistant 本身时不复制（文件就在工作区里）
- private/ 下的文件永远不复制

    python tools/handoff.py plans/2026-01-01-example-plan.md T2     # 看这个任务会带哪些文件
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from common import ROOT, load_registry, setup_stdio


def own_owners():
    """你自己的 GitHub 账号（registry identity.github / github_alt）；其余算团队仓库。"""
    try:
        ident = (load_registry() or {}).get("identity") or {}
    except Exception:
        return set()
    names = {ident.get("github"), *(ident.get("github_alt") or [])}
    return {n for n in names if n}
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
BARE = re.compile(r"(?<![\w/.-])((?:plans|projects|decisions|playbooks|reviews|meetings|areas|templates|agents)/[\w./\-一-龥]+\.(?:md|html|yaml))")


def is_team(gh, repo_entry=None):
    if repo_entry and "team" in repo_entry:
        return bool(repo_entry["team"])
    return gh.split("/")[0] not in own_owners()


def refs(block, plan_file):
    """任务说明里引用到的 assistant 文件（相对 ROOT 的路径，去重、按出现顺序）。PRD 会顺带原型目录。"""
    base = Path(plan_file).parent
    out = []
    for raw in LINK.findall(block) + BARE.findall(block):
        if re.match(r"^[a-z]+://", raw):
            continue
        for cand in ((base / raw), (ROOT / raw)):
            try:
                p = cand.resolve()
                rel = p.relative_to(ROOT.resolve())
            except (ValueError, OSError):
                continue
            if p.exists() and rel.parts and rel.parts[0] != "private" and rel.as_posix() not in out:
                out.append(rel.as_posix())
                if p.name == "prd.md" and (p.parent / "prototype").is_dir():
                    out.append((rel.parent / "prototype").as_posix())
                break
    return out


def bundle_dir(workdir, plan_stem, tid):
    return Path(workdir) / ".assistant" / "handoff" / f"{plan_stem}-{tid}"


def exclude(workdir):
    """把 .assistant/ 写进仓库的本地排除文件（所有 worktree 共用），不改 .gitignore。"""
    r = subprocess.run(["git", "-C", str(workdir), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        return
    ex = Path(r.stdout.strip()) / "info" / "exclude"
    ex.parent.mkdir(parents=True, exist_ok=True)
    text = ex.read_text(encoding="utf-8") if ex.exists() else ""
    if ".assistant/" not in text.splitlines():
        ex.write_text(text.rstrip("\n") + ("\n" if text else "") + "# assistant 交接包（本地副本，不提交）\n.assistant/\n", encoding="utf-8")


def assistant_commit():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or "?"


def copy_bundle(files, dest, title):
    """复制到 dest，写 README（来源、清单、只读说明）。返回 dest。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    sha = assistant_commit()
    for rel in files:
        src, dst = ROOT / rel, dest / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (dest / "README.md").write_text("\n".join([
        f"# 交接包：{title}", "",
        f"> 只读副本：原件在 assistant（{ROOT.as_posix()}），复制于 {datetime.now():%Y-%m-%d %H:%M}，assistant 提交 {sha}。",
        "> 要改内容回 assistant 改，不在这里改；这个目录写在 .git/info/exclude 里，不会被提交。", "",
        "## 文件", *[f"- {f}" for f in files], ""]), encoding="utf-8")
    return dest


def main():
    setup_stdio()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    import dispatch
    plan_file = Path(sys.argv[1])
    plan_file = plan_file if plan_file.is_absolute() else ROOT / plan_file
    files = refs(dispatch.task_block(plan_file, sys.argv[2]), plan_file)
    print("\n".join(files) or "（没有引用 assistant 文件）")


if __name__ == "__main__":
    main()
