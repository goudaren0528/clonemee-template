r"""飞书通知 —— agent 在本地发一张卡片到飞书群（做完 issue / PR / 合并 / 关闭 issue 之后）。

只用标准库（urllib），不依赖第三方包。卡片是飞书「卡片 JSON 2.0」：header 按动作变色，
正文 3–5 行要点，最后一个按钮打开 GitHub 链接。不经过 GitHub Actions，也不需要 secret。

    python tools/feishu_notify.py --kind merge --repo <your-github>/example-repo --number 7 --title "示例：修复导出按钮样式" --url https://github.com/<your-github>/example-repo/pull/7
    python tools/feishu_notify.py --kind issue --repo <your-github>/example-repo --number 12 --title "示例：列表页分页错位" --detail "示例说明：已复现，等排查" --dry-run
    python tools/feishu_notify.py --kind pr --repo <your-github>/example-repo --number 31 --title "…" --webhook-file private/feishu-webhook.md

webhook 按顺序找：--webhook-file → 环境变量 FEISHU_WEBHOOK → 本仓库 private/feishu-webhook.md。
webhook 当凭证：不写进代码、不打印到输出（失败原因会脱敏）；发送超时 10 秒，失败打印原因、退出码 1。
--dry-run 只打印卡片 JSON，不发，也不需要 webhook。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from common import ROOT, blocked, setup_stdio

DEFAULT_WEBHOOK_FILE = ROOT / "private" / "feishu-webhook.md"
DEFAULT_TIMEOUT = 10.0
MAX_TITLE = 120
MAX_DETAIL = 200

KINDS = ("issue", "pr", "merge")
KIND_LABEL = {"issue": "Issue", "pr": "PR", "merge": "PR"}
BUTTON_LABEL = {"issue": "查看 Issue", "pr": "查看 PR", "merge": "查看 PR"}
DEFAULT_ACTION = {"issue": "opened", "pr": "opened", "merge": "merged"}
ACTION_LABEL = {
    ("issue", "opened"): "已提出",
    ("issue", "closed"): "已关闭",
    ("issue", "reopened"): "重新打开",
    ("pr", "opened"): "待评审",
    ("pr", "closed"): "已关闭",
    ("pr", "reopened"): "重新打开",
    ("pr", "merged"): "已合并",
    ("merge", "merged"): "已合并",
}
# 颜色按 kind：issue 蓝、PR 青、合并绿；关闭红、重新打开橙
TEMPLATE = {
    ("issue", "opened"): "blue",
    ("issue", "closed"): "red",
    ("issue", "reopened"): "orange",
    ("pr", "opened"): "turquoise",
    ("pr", "closed"): "red",
    ("pr", "reopened"): "orange",
    ("pr", "merged"): "green",
    ("merge", "merged"): "green",
}
# 凭证文件里第一个形如 webhook 的地址；先认飞书机器人的标准地址，再退一步认任意 /hook/ 链接
WEBHOOK_PATTERNS = (
    re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+"),
    re.compile(r"https?://[^\s`'\"<>]+/hook/[^\s`'\"<>]+"),
)


def _text(value: Any, limit: int) -> str:
    """压成一行、去掉多余空白，超长截断。标题和详情里可能带换行。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def _action(kind: str, action: str) -> str:
    return (action or "").strip() or DEFAULT_ACTION.get(kind, "")


def _default_url(kind: str, repo: str, number: str) -> str:
    """没给 --url 时按仓库和编号拼 GitHub 链接；没有编号就给仓库首页。"""
    if not repo:
        return ""
    if not number:
        return f"https://github.com/{repo}"
    part = "issues" if kind == "issue" else "pull"
    return f"https://github.com/{repo}/{part}/{number}"


def card_title(kind: str, action: str, repo: str, number: str) -> str:
    """卡片标题，例如「[example-repo] PR #7 已合并」。"""
    short = repo.rsplit("/", 1)[-1] if repo else "仓库"
    subject = f"{KIND_LABEL.get(kind, kind)} #{number}" if number else KIND_LABEL.get(kind, kind)
    label = ACTION_LABEL.get((kind, action), "有更新")
    return _text(f"[{short}] {subject} {label}", MAX_TITLE)


def build_notification(
    kind: str,
    repo: str,
    title: str,
    number: str = "",
    detail: str = "",
    url: str = "",
    action: str = "",
) -> dict[str, Any]:
    """拼好卡片内容（header + 正文要点 + 按钮）；发不发由调用方决定。"""
    action = _action(kind, action)
    lines = [
        f"**仓库**：{_text(repo, 64)}",
        f"**标题**：{_text(title, MAX_TITLE) or '（无标题）'}",
    ]
    if number:
        lines.append(f"**编号**：#{_text(number, 32)}")
    if detail:
        lines.append(f"**详情**：{_text(detail, MAX_DETAIL)}")
    lines.append(f"**时间**：{datetime.now():%Y-%m-%d %H:%M}")
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(lines)}]
    link = url or _default_url(kind, repo, number)
    if link:
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": BUTTON_LABEL.get(kind, "打开链接")},
                "type": "primary",
                "behaviors": [{"type": "open_url", "default_url": link}],
            }
        )
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": card_title(kind, action, repo, number)},
            "template": TEMPLATE.get((kind, action), "grey"),
        },
        "body": {"elements": elements},
    }


def build_payload(
    kind: str,
    repo: str,
    title: str,
    number: str = "",
    detail: str = "",
    url: str = "",
    action: str = "",
) -> dict[str, Any]:
    """webhook 的请求体：interactive 消息 + 卡片。"""
    card = build_notification(kind, repo, title, number, detail, url, action)
    return {"msg_type": "interactive", "card": card}


def extract_webhook(text: str) -> str:
    """从凭证文件里取出 webhook 地址；没有返回空串。"""
    for pattern in WEBHOOK_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(0).rstrip(".,;，。；")
    return ""


def resolve_webhook(webhook_file: str = "") -> tuple[str, str]:
    """返回 (webhook, 来源说明)；找不到返回 ("", 说明)。

    顺序：--webhook-file 指定的文件 → 环境变量 FEISHU_WEBHOOK → 本仓库 private/feishu-webhook.md。
    """
    if webhook_file:
        path = Path(webhook_file).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return "", f"读不到凭证文件 {path}（{exc.__class__.__name__}）"
        found = extract_webhook(text)
        return (found, str(path)) if found else ("", f"{path} 里没有找到 webhook 地址")

    env = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if env:
        return env, "环境变量 FEISHU_WEBHOOK"

    path = DEFAULT_WEBHOOK_FILE
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return "", f"读不到凭证文件 {path}（{exc.__class__.__name__}）"
        found = extract_webhook(text)
        if found:
            return found, str(path)
        return "", f"{path} 里没有找到 webhook 地址"

    return "", (
        "没有找到 webhook：设置环境变量 FEISHU_WEBHOOK，或用 --webhook-file 指定凭证文件"
        "（本仓库默认读 private/feishu-webhook.md）"
    )


def _redact(text: str, webhook: str) -> str:
    """输出里不留 webhook：整条地址和末尾 token 都替掉。"""
    text = str(text)
    if not webhook:
        return text
    text = text.replace(webhook, "***")
    token = webhook.rstrip("/").rsplit("/", 1)[-1]
    if token and token != webhook:
        text = text.replace(token, "***")
    return text


def _describe_error(exc: BaseException, webhook: str) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        text = f"HTTP {exc.code} {exc.reason}"
    elif isinstance(exc, urllib.error.URLError):
        text = f"连接失败：{exc.reason}"
    elif isinstance(exc, TimeoutError):
        text = "请求超时"
    else:
        text = f"{type(exc).__name__}: {exc}"
    return _redact(text, webhook)


def send(webhook: str, payload: dict[str, Any], timeout: float = DEFAULT_TIMEOUT) -> bool:
    """POST 到飞书机器人。失败只打印一行原因（脱敏），返回 False，不抛异常。
    ASSISTANT_NO_SEND=1 时只打印「已拦截外发」、返回 True，不发网络请求（全仓飞书外发的唯一出口）。"""
    if blocked("飞书通知"):
        return True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        request = urllib.request.Request(
            webhook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8192)
    except (OSError, ValueError, http.client.HTTPException) as exc:  # 网络错误、超时、非 2xx、地址不合法
        print(f"飞书通知发送失败：{_describe_error(exc, webhook)}", file=sys.stderr)
        return False

    try:
        reply = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        print("飞书通知发送失败：返回内容不是 JSON", file=sys.stderr)
        return False
    if isinstance(reply, dict):
        code = reply.get("code")
        if code is None:
            code = reply.get("StatusCode", 0)
        message = reply.get("msg") or reply.get("StatusMessage") or ""
    else:
        code, message = 0, ""
    if str(code) not in ("0", "None"):
        print(f"飞书通知发送失败：code={code} msg={_redact(_text(message, 200), webhook)}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="在本地发一张飞书卡片（issue / PR / 合并这类对外状态变化之后）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--kind", required=True, choices=KINDS, help="动作类型：issue / pr / merge")
    parser.add_argument("--repo", required=True, help="GitHub 仓库 owner/name")
    parser.add_argument("--number", default="", help="issue 或 PR 编号")
    parser.add_argument("--title", required=True, help="标题（issue 标题、PR 标题）")
    parser.add_argument("--detail", default="", help="可选：一句话补充（要点、影响）")
    parser.add_argument("--url", default="", help="可选：按钮打开的链接，默认按 repo / number 拼 GitHub 链接")
    parser.add_argument("--action", default="", help="可选：opened / closed / reopened / merged，默认按 kind")
    parser.add_argument("--webhook-file", default="", help="凭证文件，默认读 private/feishu-webhook.md 或环境变量")
    parser.add_argument("--dry-run", action="store_true", help="只打印卡片 JSON，不发")
    args = parser.parse_args(argv)

    repo = args.repo.strip()
    if repo.count("/") != 1 or not all(part.strip() for part in repo.split("/")):
        print(f"飞书通知未发送：--repo 要写成 owner/name，收到的是 {repo!r}", file=sys.stderr)
        return 2

    payload = build_payload(
        kind=args.kind,
        repo=repo,
        title=args.title,
        number=str(args.number or "").strip().lstrip("#"),
        detail=args.detail,
        url=args.url.strip(),
        action=args.action,
    )
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    webhook, source = resolve_webhook(args.webhook_file)
    if not webhook:
        print(f"飞书通知未发送：{source}", file=sys.stderr)
        return 1

    if not send(webhook, payload):
        return 1
    print(f"已发送：{payload['card']['header']['title']['content']}（webhook 来自{source}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
