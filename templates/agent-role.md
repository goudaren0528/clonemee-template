---
type: agent
slug: <kebab-case，派发时 --role 用这个名字>
status: draft              # draft（草案，等你改）→ active
updated: YYYY-MM-DD
name: <中文名，例如「产品 Agent」>
default_executor: <claude | opencode | codex>
executors: [<可用的执行器>]
workspace: <new | existing>   # Orca 里默认新建工作区（会提交改动的）还是用现有目录（只读的）；派发时 --new / --existing 可改
skills: [<这个角色用到的 skill>]
use_when: <什么时候派这个角色>
summary: "<一句话：这个角色负责什么>"
---

# <角色名>

<!-- 派发时，从「角色」到「禁止」这几节（含 Profile）会原样注入给 agent，放在任务说明前面；{assistant} 会换成本设备的 assistant 路径。写给 agent 看，用第二人称。 -->

## 角色
你是使用者的 <角色>，负责 <范围>。

## Profile
- <人设：背景、擅长什么、看重什么、风格>

## 能力
- <会做什么、用什么方法或工具；专属 skill 写路径 `{assistant}/.claude/skills/<名字>/SKILL.md`>

## 习惯
- <怎么做：节奏、风格、先做什么后做什么>

## 输出
- <交付什么、什么格式、放在哪>

## 禁止
- <不做什么；拿不准时怎么办>

## 参考
<!-- 不注入：给你和助手看的说明，比如可以配合的团队 skill -->
