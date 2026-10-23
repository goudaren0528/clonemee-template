---
type: playbook
slug: device-setup
status: active
updated: 2026-10-19
tags: [meta, multi-device]
summary: "换到新设备时：克隆、装依赖、自动登记本地路径、private 不同步"
---

# 新设备上手 `v0`

本仓库不写死任何本地路径。换到新设备后按下面做，助手就能接着用。

## 步骤
1. 克隆到任意目录：`git clone https://github.com/<your-github>/cloneme.git`
2. 装依赖：`pip install -r requirements.txt`；GitHub CLI 登录：`gh auth login`
3. 在仓库目录打开 Claude Code。SessionStart hook 会自动：
   - 算出本设备指纹，新建 `registry/devices/<指纹>.yaml`
   - 扫描用户目录和非系统盘（深度 4），找到 tracked 仓库的本地克隆
   - 唯一匹配的自动确认；多个匹配的列为待确认
4. 处理待确认：`python tools/device.py show`，然后 `confirm <路径>` 或 `reject <路径或通配>`
5. 扫描根目录不合适时，直接改设备文件里的 `scan_roots`，再跑 `python tools/device.py --rescan`
6. 提交并推送设备文件（它只记这台设备的路径，不同设备之间不会冲突）

## 本设备没有克隆的仓库
不影响使用：`collect.py` 会从 GitHub API 按你的账号拉提交。

## private/ 也跟着走
`private/`（健康、财务明细这类敏感内容）可以选择进仓库随 git 同步；如果放进来，仓库必须保持 private。

## 命令前缀
hook 用 `python3`，失败时回退到 `python`，所以 Windows、macOS、Linux 都能跑。
