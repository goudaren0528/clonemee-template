r"""
pre-push 体积闸：这次推送带出去的新对象累计超过 10MB、单个文件超过 1MB，或新增文本里有密钥特征，就拒绝推送。

起因：长分支合并时把大体积的验收证据连同历史推上远端（文件树里删了，历史里还在）。
所以按「这次要传的对象」算，不按当前文件树算：长分支历史里出现过的大文件也会被拦下。
由 .git/hooks/pre-push 调用（tools/install_hooks.py 安装），git 从 stdin 每行传一个要推的 ref：
    <本地 ref> <本地 sha> <远端 ref> <远端 sha>
每个 ref 算 git rev-list --objects <远端 sha>..<本地 sha>（远端 sha 全 0 或本地没有时用 --not --remotes），
再用 git cat-file --batch-check 累计体积。删分支（本地 sha 全 0）不检查。
密钥特征：sk- 后 20 位以上、ghp_ 后 30 位以上、AKIA 后 16 位大写字母数字、带正文的 PEM 私钥文件头（只扫 1MB 以内的文本）；
测试夹具里的假密钥：文件里写一行 assistant-push-guard: fake-secrets 就不扫这个文件的密钥。
确实要推：ASSISTANT_PUSH_OK=1 git push …（PowerShell：$env:ASSISTANT_PUSH_OK='1'; git push …），放行时打印一行警告。
```
python tools/push_guard.py --rev HEAD    # 手动检查：HEAD 里还没到任何远端的对象（同步任务推送前也可以先跑这个）
```
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

MAX_FILE = 1 << 20    # 单个文件上限 1MB
MAX_TOTAL = 10 << 20  # 累计上限 10MB
TOP = 5               # 拦下时列出最大的几个文件
SCAN_PEEK = 8000      # 前这么多字节里有 NUL 就当二进制，不扫密钥
TAG = "[体积闸]"
OK_ENV = "ASSISTANT_PUSH_OK"

# 前面不能紧跟字母数字、下划线或连字符（避免 task-…、disk-… 这类 kebab 词误报）
SECRETS = [
    ("sk- 密钥", re.compile(rb"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{20,}")),
    ("GitHub 令牌 ghp_", re.compile(rb"(?<![A-Za-z0-9_\-])ghp_[A-Za-z0-9]{30,}")),
    ("AWS 访问密钥 AKIA", re.compile(rb"(?<![A-Za-z0-9_\-])AKIA[0-9A-Z]{16}(?![0-9A-Z])")),
    # 私钥要带正文（base64 行或加密头）才算：只提到文件头的文档和夹具不拦
    ("私钥文件头", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----(?:\s|\\n)*(?:[A-Za-z0-9+/=]{40,}|Proc-Type:)")),
]
# 文件里带这个标记就不扫密钥（只给测试夹具里的假密钥用；体积照样检查）
FAKE_MARK = b"assistant-push-guard: fake-secrets"


def _git(args, cwd=None, data=None):
    """跑 git，返回 (退出码, stdout 字节)。"""
    p = subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True)
    return p.returncode, p.stdout


def is_zero(sha):
    return not sha.strip("0")


def objects_for(local, remote, cwd=None):
    """这次推 local 要带出去的对象：{sha: 路径}。远端 sha 本地没有时退回 --not --remotes。"""
    attempts = []
    if remote and not is_zero(remote):
        attempts.append([local, "^" + remote])
    attempts.append([local, "--not", "--remotes"])
    for revs in attempts:
        code, out = _git(["rev-list", "--objects", *revs], cwd)
        if code == 0:
            objs = {}
            for line in out.decode("utf-8", "replace").splitlines():
                sha, _, path = line.partition(" ")
                if sha:
                    objs.setdefault(sha, path)
            return objs
    raise RuntimeError(f"git rev-list 算不出 {local[:12]} 要推的对象")


def sizes_of(shas, cwd=None):
    """{sha: (类型, 体积)}，体积是解压后的大小。"""
    if not shas:
        return {}
    data = "".join(s + "\n" for s in shas).encode()
    code, out = _git(["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], cwd, data)
    if code != 0:
        raise RuntimeError("git cat-file --batch-check 失败")
    info = {}
    for line in out.decode().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2].isdigit():
            info[parts[0]] = (parts[1], int(parts[2]))
    return info


def iter_blobs(shas, cwd=None):
    """逐个给出 (sha, 内容)：一次 git cat-file --batch 读完。"""
    if not shas:
        return
    data = "".join(s + "\n" for s in shas).encode()
    code, out = _git(["cat-file", "--batch"], cwd, data)
    if code != 0:
        raise RuntimeError("git cat-file --batch 失败")
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        head = out[pos:nl].split()
        pos = nl + 1
        if len(head) < 3:  # missing
            continue
        size = int(head[2])
        yield head[0].decode(), out[pos:pos + size]
        pos += size + 1


def find_secrets(content):
    """内容里命中的密钥特征：[(名称, 打码后的片段)]。"""
    if b"\0" in content[:SCAN_PEEK] or FAKE_MARK in content:
        return []
    hits = []
    for name, pat in SECRETS:
        m = pat.search(content)
        if m:
            s = m.group(0).decode("ascii", "replace")
            hits.append((name, s[:8] + "…" if len(s) > 12 else s))
    return hits


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def check(refs, cwd=None):
    """refs: [(本地 sha, 远端 sha)]。返回 (通过?, 要打印的行)。"""
    objs = {}
    for local, remote in refs:
        if is_zero(local):
            continue
        for sha, path in objects_for(local, remote, cwd).items():
            objs.setdefault(sha, path)
    info = sizes_of(list(objs), cwd)
    total = sum(size for _, size in info.values())
    blobs = sorted(((size, sha) for sha, (kind, size) in info.items() if kind == "blob"), reverse=True)
    big = [(size, sha) for size, sha in blobs if size > MAX_FILE]
    secrets = []
    small = [sha for size, sha in blobs if size <= MAX_FILE]
    for sha, content in iter_blobs(small, cwd):
        for name, snippet in find_secrets(content):
            secrets.append((objs.get(sha) or sha[:12], name, snippet))

    biggest = blobs[0][0] if blobs else 0
    if total <= MAX_TOTAL and not big and not secrets:
        return True, [f"{TAG} 通过：{len(objs)} 个新对象，共 {human(total)}，最大文件 {human(biggest)}"]

    reasons = []
    if total > MAX_TOTAL:
        reasons.append(f"新对象累计 {human(total)}（上限 {human(MAX_TOTAL)}）")
    if big:
        reasons.append(f"{len(big)} 个文件超过 {human(MAX_FILE)}")
    if secrets:
        reasons.append(f"{len(secrets)} 处疑似密钥")
    lines = [f"{TAG} 拦下这次推送：" + "；".join(reasons) + "。",
             f"  这次要传 {len(objs)} 个新对象，共 {human(total)}（按提交历史算，删掉的文件只要在历史里也算）。"]
    if blobs and (total > MAX_TOTAL or big):
        lines.append(f"  最大的 {min(TOP, len(blobs))} 个文件：")
        lines += [f"    {human(size):>9}  {objs.get(sha) or sha[:12]}" for size, sha in blobs[:TOP]]
    if secrets:
        lines.append("  疑似密钥：")
        lines += [f"    {path}：{name}（{snippet}）" for path, name, snippet in secrets[:10]]
    lines += ["  处理：机器生成的证据（大 json、日志、截图、gif）不进提交，放证据目录并加进 .gitignore；",
              "        长分支合进 main 用 git merge --squash，别把整段历史带过去；密钥从历史里去掉后再推。",
              f"  确实要推：{OK_ENV}=1 git push …（PowerShell：$env:{OK_ENV}='1'; git push …）"]
    return False, lines


def parse_stdin(text):
    """pre-push 的 stdin → [(本地 sha, 远端 sha)]。"""
    refs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4:
            refs.append((parts[1], parts[3]))
    return refs


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(description="pre-push 体积闸")
    ap.add_argument("remote", nargs="?", help="git 传进来的远端名（钩子模式）")
    ap.add_argument("url", nargs="?", help="git 传进来的远端地址（钩子模式）")
    ap.add_argument("--rev", help="手动检查：这个提交里还没到任何远端的对象")
    args = ap.parse_args(argv)

    if args.rev:
        code, out = _git(["rev-parse", "--verify", args.rev + "^{commit}"])
        if code != 0:
            print(f"{TAG} 找不到提交 {args.rev}", file=sys.stderr)
            return 2
        refs = [(out.decode().strip(), "")]
    else:
        refs = parse_stdin(sys.stdin.read())  # 放行也要读完 stdin，免得 git 写管道出错

    if os.environ.get(OK_ENV) == "1":
        print(f"{TAG} 警告：{OK_ENV}=1，跳过体积和密钥检查直接放行", file=sys.stderr)
        return 0
    try:
        ok, lines = check(refs)
    except RuntimeError as e:
        print(f"{TAG} 检查出错，拦下推送：{e}；确认没问题可用 {OK_ENV}=1 放行", file=sys.stderr)
        return 1
    print("\n".join(lines), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
