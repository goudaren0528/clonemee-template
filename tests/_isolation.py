"""测试公共入口：隔离外发。每个 tests/test_*.py 顶部 `import _isolation  # noqa: F401`。

做三件事（import 时一次性做完）：
1. ASSISTANT_NO_SEND 默认设为 1（总开关，见 tools/common.py：飞书、orca 发消息 / 拉 agent、Windows 通知、gh 写入都只打印「已拦截外发」）；
2. FEISHU_WEBHOOK 强制指向本机不会监听的 127.0.0.1:9（不用 setdefault，开发者环境里的真 webhook 也盖掉）；
3. feishu_notify 读 private/ 下 webhook 文件的路径改到临时空目录，测试读不到真凭据。
想验证真实外发的单测（本机 http.server 收 POST）在自己的 setUp 里 patch.dict 把 ASSISTANT_NO_SEND 设成 "0"。
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

DEAD_WEBHOOK = "http://127.0.0.1:9/assistant-test-no-send"

os.environ.setdefault("ASSISTANT_NO_SEND", "1")
os.environ["FEISHU_WEBHOOK"] = DEAD_WEBHOOK

SANDBOX = Path(tempfile.mkdtemp(prefix="assistant-test-nosend-"))
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)

import feishu_notify as _fn  # noqa: E402

_fn.DEFAULT_WEBHOOK_FILE = SANDBOX / "private" / "feishu-webhook.md"  # 不存在 → 读不到真凭据
