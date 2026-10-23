# 在 assistant 工作区开一个带名字的 Claude Code 会话（Windows）。名字会显示在输入框、/resume 列表和终端标题上。
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\start.ps1                      # 日常会话，自动按上午 / 晚上命名
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\start.ps1 -Kind dev -Topic 派发能力   # 改进会话
# 命名规则：[类型] 日期 周X 主题。类型：日常（早上推进 / 晚上汇报）· 改进（改助手本身）；
# 自动（定时任务）和执行（派发到项目的任务）由 agent-run.ps1 等脚本自己命名。见 CLAUDE.md §2。
param(
    [ValidateSet('ops', 'dev')][string]$Kind = 'ops',
    [string]$Topic = ''
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$now = Get-Date
$week = '一二三四五六日'[([int]$now.DayOfWeek + 6) % 7]
$day = "$($now.ToString('yyyy-MM-dd')) 周$week"
if ($Kind -eq 'ops') {
    if (-not $Topic) { $Topic = if ($now.Hour -lt 15) { '早上推进' } else { '晚上汇报' } }
    $name = "[日常] $day $Topic"
} else {
    if (-not $Topic) { $Topic = '改进助手' }
    $name = "[改进] $day $Topic"
}

# 先把指挥台本地通道拉起来（不等结果；会话启动钩子会再确认一次并给链接）
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    try { & $python.Source -B (Join-Path $root 'tools\console_channel.py') ensure --wait 0 --quiet 2>$null | Out-Null } catch {}
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) { Write-Output '找不到 claude 命令'; exit 1 }
# 带一句开场白启动，助手会先打招呼并报运行环境和状态（CLAUDE.md §2）
& $claude.Source -n $name "开始（$name）"
