"""dispatch.py 生成的「授权」文字的单测：三级判定（直接做 / 报主会话 / 红线）。

只测文字生成，不读仓库里真实的计划和 PRD，也不启动会话。
    python -B -m unittest tests/test_dispatch_auth.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import dispatch  # noqa: E402

TASK = {"id": "T9", "title": "改跟进 Agent 分级", "proj": "assistant"}
BLOCK = """- 目标：把上报分三级
- 验收：三级判定有例子，测试通过
"""
PLAN_FM = {"status": "active"}

EXEC_PLAN_BODY = """## 目标

把跟进 Agent 的决策和上报改三级。

## 验收

三级判定能落到例子；测试通过。

## 授权范围

- 可以替你定：例子的措辞
- 可以派的角色：research
- 必须升级：改任务目标
"""


def exec_plan(confirmed=True):
    return {
        "kind": "执行方案",
        "rel": "plans/exec/2026-10-22-test.md",
        "fm": {"status": "active" if confirmed else "draft", "confirmed": "2026-10-22"},
        "body": EXEC_PLAN_BODY,
        "confirmed": confirmed,
    }


class TierTextCase(unittest.TestCase):
    def test_three_tiers_in_constants(self):
        for mark, words in (
            ("① 直接做", ("推荐", "可回退", "决策记录")),
            ("② 报主会话", ("中等", "要拍板", "备选")),
            ("③ 红线", ("你本人", "授权里写了也不放行")),
        ):
            self.assertIn(mark, dispatch.AUTH_TIERS)
            for w in words:
                self.assertIn(w, dispatch.AUTH_TIERS)
        # 判定顺序：直接做 → 报主会话 → 红线，红线在最后、是兜底
        self.assertLess(dispatch.AUTH_TIERS.index("① 直接做"), dispatch.AUTH_TIERS.index("② 报主会话"))
        self.assertLess(dispatch.AUTH_TIERS.index("② 报主会话"), dispatch.AUTH_TIERS.index("③ 红线"))

    def test_red_line_keeps_你_bottom_line(self):
        for w in ("钱", "权益", "密钥", "账号权限", "生产", "不可逆", "对外发布", "改目标", "删数据"):
            self.assertIn(w, dispatch.AUTH_TIERS)
        self.assertIn("授权里写了也不放行", dispatch.AUTH_TIERS)
        self.assertIn("第 3 级", dispatch.AUTH_ESCALATE)
        self.assertIn("删除分支或文件", dispatch.AUTH_ESCALATE)

    def test_auth_default_uses_three_tiers(self):
        for w in ("第 1 级直接做", "主会话", "你本人", "不能派生子任务"):
            self.assertIn(w, dispatch.AUTH_DEFAULT)

    def test_no_confirmed_doc_returns_empty_auth(self):
        text, source, warn = dispatch.auth_of(TASK, "", "plan.md", {}, None)
        self.assertEqual((text, source, warn), ("", "", ""))
        self.assertIn("未单独授权", dispatch.AUTH_DEFAULT)


class AuthOfCase(unittest.TestCase):
    def test_exec_plan_gets_three_tiers_and_plan_escalate(self):
        text, source, warn = dispatch.auth_of(TASK, BLOCK, "plan.md", PLAN_FM, exec_plan())
        self.assertIn("执行方案 plans/exec/2026-10-22-test.md", source)
        self.assertEqual(warn, "")
        self.assertIn("- 三级判定（task-follow 第 5 节）：", text)
        self.assertIn("① 直接做", text)
        self.assertIn("② 报主会话", text)
        self.assertIn("③ 红线", text)
        self.assertIn("- 可以派的角色：research", text)
        self.assertIn("改任务目标；另外", text)
        self.assertIn(dispatch.AUTH_ESCALATE, text)

    def test_prd_gets_three_tiers(self):
        doc = {"kind": "PRD", "rel": "plans/prd/x/prd.md", "fm": {"status": "active"}, "body": "", "confirmed": True}
        text, source, _ = dispatch.auth_of(TASK, BLOCK, "plan.md", PLAN_FM, doc)
        self.assertIn("PRD", source)
        self.assertIn("① 直接做", text)
        self.assertIn("③ 红线", text)

    def test_confirmed_plan_markdown_gets_three_tiers(self):
        text, source, _ = dispatch.auth_of(TASK, BLOCK, "plan.md", PLAN_FM, None)
        self.assertIn("T9 的执行说明", source)
        self.assertIn("- 范围：把上报分三级", text)
        self.assertIn("② 报主会话", text)

    def test_unconfirmed_doc_warns_and_no_auth_text(self):
        # 方案没确认、计划也没确认：不当授权，返回空（调用方写 AUTH_DEFAULT）
        text, source, warn = dispatch.auth_of(TASK, "", "plan.md", {}, exec_plan(confirmed=False))
        self.assertIn("还没确认", warn)
        self.assertEqual(text, "")

    def test_told_auth_overrides_generated(self):
        block = BLOCK + "- 授权：可以推 fix 分支\n"
        text, source, _ = dispatch.auth_of(TASK, block, "plan.md", PLAN_FM, exec_plan())
        self.assertIn("你写明：可以推 fix 分支", text)

    def test_strip_auth_keeps_brief_clean(self):
        brief = "目标：x\n授权：随便\n回报：y"
        self.assertNotIn("授权", dispatch.strip_auth(brief))
        self.assertIn("回报：y", dispatch.strip_auth(brief))

    def test_follow_guide_text_has_both_levels(self):
        # follow() 的跟进说明直接拼在 guide 里；这里断言源码里的措辞，防止被改回去
        src = (REPO / "tools" / "dispatch.py").read_text(encoding="utf-8")
        self.assertIn("开头写「中等：」", src)
        self.assertIn("开头写「红线：」", src)


if __name__ == "__main__":
    unittest.main()
