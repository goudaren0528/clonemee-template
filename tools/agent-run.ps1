# assistant 定时任务入口（Windows）：由 Orca 自动化（主）和任务计划程序（备份）调用，也可以手动运行。
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job nightly [-Date 2027-03-08]
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job morning [-Date 2027-03-09] [-Force]
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-run.ps1 -Job morning -Check   # Orca 的 precheck：0 = 该跑，1 = 已跑过 / 正在跑
# nightly：先跑 tools/nightly.py 做确定性采集，再由 claude -p 按 nightly-review skill 更新文档
# morning：claude -p 按 morning-brief skill 生成当天的建议（plans/daily/<日期>.md，待确认）
# 同一天同一个任务只跑一次：日志里已有「结束 <job> exit=0」就跳过（-Force 强制重跑）；同时只跑一个任务（锁文件，排队等）
# 不给 -Date 时：夜间巡检在 21 点前自动触发（-Trigger task / orca）算前一天的，手动的 12 点前算前一天
#   （补跑昨晚漏掉的那次，不会把当天的巡检提前跑掉）
# -Detach：转到后台跑、立即返回（Orca 自动化的启动器会话用）
# 跑完：确认仓库私有后推送，弹 Windows 通知（点开是产出；失败时是日志）
# 日志：inbox/logs/<job>-<日期>.log（不进 git）。说明见 playbooks/automation.md
param(
    [Parameter(Mandatory = $true)][ValidateSet('nightly', 'morning')][string]$Job,
    [string]$Date,
    [ValidateSet('manual', 'task', 'orca')][string]$Trigger = 'manual',
    [switch]$Check,
    [switch]$Force,
    [switch]$Detach
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'
# PowerShell 5.1 默认按本地代码页解码外部命令输出、用 UTF-16 写重定向，统一成 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Date) {
    $now = Get-Date
    # 自动触发的夜间巡检在 21 点前跑，一定是补跑前一天漏掉的那次（关机、Orca 没开）；手动的中午 12 点前算前一天
    $cutoff = if ($Trigger -eq 'manual') { 12 } else { 21 }
    $Date = if ($Job -eq 'nightly' -and $now.Hour -lt $cutoff) { $now.AddDays(-1).ToString('yyyy-MM-dd') } else { $now.ToString('yyyy-MM-dd') }
}
$logDir = Join-Path $root 'inbox\logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "$Job-$Date.log"
function Log($msg) { "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" | Out-File $log -Append -Encoding utf8 }

# 去重：同一天同一个任务成功跑过一次就不再跑（Orca 和任务计划程序都走这里）
function Test-Done {
    if (-not (Test-Path $log)) { return $false }
    return [bool](Select-String -Path $log -Pattern '结束 (\w+ )?exit=0\s*$' -Encoding utf8 -Quiet)
}
# 锁：同时只跑一个任务。进程退出时系统自动释放，不会残留；内容写着谁在跑，别的进程能读
$lockPath = Join-Path $logDir 'agent-run.lock'
function Open-Lock {
    try {
        $fs = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'Read')
        $fs.SetLength(0)
        $bytes = [Text.Encoding]::UTF8.GetBytes("$Job $Date $Trigger pid=$PID $(Get-Date -Format s)")
        $fs.Write($bytes, 0, $bytes.Length); $fs.Flush()
        return $fs
    } catch { return $null }
}
function Read-Lock {
    try {
        $fs = [IO.File]::Open($lockPath, 'Open', 'Read', 'ReadWrite')
        try { return (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
    } catch { return '' }
}

if ($Check) {
    if (Test-Done) { Write-Output "$Date 的 $Job 已经跑过，跳过"; exit 1 }
    $probe = Open-Lock
    if ($probe) { $probe.Close(); Write-Output "$Date 的 $Job 该跑"; exit 0 }
    if ((Read-Lock) -like "$Job $Date *") { Write-Output "$Date 的 $Job 正在跑，跳过"; exit 1 }
    Write-Output "$Date 的 $Job 该跑（另一个任务正在跑，到时排队）"; exit 0
}

# Orca 自动化的启动器会话：登记会话 ID，Stop hook（tools/stop_check.py）据此不要求它写会话记忆
$launchers = Join-Path $logDir 'launcher-sessions.txt'
if ($Trigger -eq 'orca' -and $env:CLAUDE_CODE_SESSION_ID -and
    -not ((Test-Path $launchers) -and (Select-String -Path $launchers -SimpleMatch $env:CLAUDE_CODE_SESSION_ID -Quiet))) {
    $env:CLAUDE_CODE_SESSION_ID | Out-File $launchers -Append -Encoding utf8
}
if (-not $Force -and (Test-Done)) {
    Log "跳过：$Date 的 $Job 已经跑过（$Trigger）"
    Write-Output "跳过：$Date 的 $Job 已经跑过。日志 $log"
    exit 0
}
if ($Detach) {
    $argLine = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Job $Job -Date $Date -Trigger $Trigger"
    if ($Force) { $argLine += ' -Force' }
    Start-Process conhost.exe -ArgumentList $argLine -WorkingDirectory $root -WindowStyle Hidden
    Log "转到后台运行（$Trigger）"
    Write-Output "已在后台启动 $Job $Date（$Trigger），日志 $log；跑完会弹 Windows 通知"
    exit 0
}

# 排队：另一个任务（或另一边的同一个任务）在跑就等，最多 45 分钟；拿到锁后再查一次是否已跑过
$lock = Open-Lock
if (-not $lock) {
    Log "排队：$(Read-Lock) 正在跑，等它结束"
    $deadline = (Get-Date).AddMinutes(45)
    while (-not $lock -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 30; $lock = Open-Lock }
    if (-not $lock) { Log "等了 45 分钟还没拿到锁，退出"; exit 1 }
    if (-not $Force -and (Test-Done)) { Log "跳过：排队期间另一边已经跑完 $Date 的 $Job（$Trigger）"; $lock.Close(); exit 0 }
}

# 从 Claude Code 会话里启动时（Orca 启动器），清掉会话级环境变量，否则 claude -p 会被当成嵌套会话
foreach ($v in 'CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_EFFORT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_CHILD_SESSION',
               'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_EXECPATH') {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}

function Find-Exe($name, $fallback) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($fallback -and (Test-Path $fallback)) { return $fallback }
    return $null
}
$python = Find-Exe 'python' $null
$claude = Find-Exe 'claude' (Join-Path $env:APPDATA 'npm\claude.cmd')
if (-not $python -or -not $claude) { Log "找不到 python 或 claude（python=$python claude=$claude），退出"; exit 1 }

Log "开始 $Job $Date（$Trigger）"
if ($Job -eq 'nightly') {
    & $python tools\nightly.py --date $Date 2>&1 | Out-File $log -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) { Log "nightly.py 失败 exit=$LASTEXITCODE"; exit 1 }
    $prompt = "按 .claude/skills/nightly-review/SKILL.md 执行 $Date 的夜间巡检。摘要在 inbox/nightly-$Date.md。无人值守，不要提问。"
} else {
    $prompt = "按 .claude/skills/morning-brief/SKILL.md 的「A. 生成」为 $Date 生成早间建议。无人值守，不要提问。"
}

# 只开放本仓库内的读写和少量命令；项目仓库的数据由 tools/*.py 采集，不给访问其他仓库的权限
$allowed = @('Read', 'Edit', 'Write', 'Glob', 'Grep',
             'Bash(python tools/*)', 'Bash(git add *)', 'Bash(git commit *)', 'Bash(git status*)', 'Bash(git diff*)', 'Bash(git log*)')
$denied = @('Bash(git push*)', 'WebFetch', 'WebSearch')
$week = '一二三四五六日'[([int]([datetime]$Date).DayOfWeek + 6) % 7]
$sessionName = if ($Job -eq 'nightly') { "[自动] $Date 周$week 夜间巡检" } else { "[自动] $Date 周$week 早间建议" }
& $claude -p $prompt -n $sessionName --permission-mode acceptEdits --allowedTools @allowed --disallowedTools @denied --output-format text 2>&1 |
    Out-File $log -Append -Encoding utf8
$code = $LASTEXITCODE
Log "结束 $Job exit=$code"

# 跑完：确认仓库仍是私有后推送（CLAUDE.md §4），再弹 Windows 通知，点通知打开产出（失败时打开日志）
function Push-Repo {
    $git = Find-Exe 'git' $null
    $gh = Find-Exe 'gh' $null
    if (-not $git) { return '没找到 git，未推送' }
    $ahead = & $git rev-list --count '@{u}..HEAD' 2>$null
    if (-not $ahead -or [int]$ahead -eq 0) { return '没有新提交要推送' }
    $vis = if ($gh) { & $gh repo view --json visibility -q .visibility 2>$null } else { $null }
    if ($vis -ne 'PRIVATE') { Log "仓库可见性=$vis，不推送"; return "未推送：确认不了仓库是私有的（$vis）" }
    & $git push -q origin HEAD 2>&1 | Out-File $log -Append -Encoding utf8
    if ($LASTEXITCODE -eq 0) { Log "已推送 $ahead 个提交"; return "已推送 $ahead 个提交" }
    Log "推送失败 exit=$LASTEXITCODE"; return '推送失败，看日志'
}

function Get-Summary($path) {
    if (-not (Test-Path $path)) { return $null }
    $m = Select-String -Path $path -Pattern '^summary:\s*"?(.*?)"?\s*$' -Encoding utf8 | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Notify($title, $body, $open) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        $e = { param($s) [Security.SecurityElement]::Escape([string]$s) }
        $launch = if ($open -and (Test-Path $open)) { " activationType=`"protocol`" launch=`"$(& $e ([Uri](Resolve-Path $open).Path).AbsoluteUri)`"" } else { '' }
        $texts = (@($title) + ($body -split "`n")) | Where-Object { $_ } | Select-Object -First 3 | ForEach-Object { "<text>$(& $e $_)</text>" }
        $xml = "<toast$launch><visual><binding template=`"ToastGeneric`">$($texts -join '')</binding></visual></toast>"
        $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
        $doc.LoadXml($xml)
        $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($doc))
        Log "已发通知：$title"
    } catch { Log "通知失败：$_" }
}

$label = if ($Job -eq 'nightly') { '夜间巡检' } else { '早间建议' }
if ($code -ne 0) {
    Notify "assistant $label 失败" "exit=$code，点开看日志" $log
    exit $code
}
$pushed = Push-Repo
if ($Job -eq 'morning') {
    $out = Join-Path $root "plans\daily\$Date.md"
    $todo = if (Test-Path $out) { @(Select-String -Path $out -Pattern '^- \[ \] T\d+' -Encoding utf8).Count } else { 0 }
    $body = "$(Get-Summary $out)`n$todo 项待确认，开新会话说「确认今日建议」 · $pushed"
} else {
    $out = Join-Path $root "reviews\nightly\$Date.md"
    $body = "$(Get-Summary $out)`n$pushed"
}
Notify "assistant $label 已生成（$Date）" $body $out
