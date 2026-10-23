# 注册 / 移除 assistant 的定时任务（Windows 任务计划程序，当前用户，不需要管理员）。
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1                 # 两个都装
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Only nightly
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -NightlyAt 21:30 -MorningAt 09:50
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Disable       # 停用（任务留着当备份）；-Enable 恢复
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\agent-install.ps1 -Remove        # 两个都卸
# 主力是 Orca 自动化（10:00 / 22:00，见 playbooks/automation.md），这里是备份：默认晚 15 分钟，
# Orca 已经跑过的话 agent-run.ps1 会跳过，Orca 没开或跑失败时由它补上。
# assistant-nightly：每天 22:15 夜间巡检；assistant-morning：周一到周六 10:15 早间建议（周日留给事情盘点）。
# 到点时关机或睡眠的话，下次开机后补跑（StartWhenAvailable）。只在你登录时运行；用 conhost --headless 启动，不弹窗口。
param(
    [ValidateSet('all', 'nightly', 'morning')][string]$Only = 'all',
    [string]$NightlyAt = '22:15',
    [string]$MorningAt = '10:15',
    [switch]$Disable,
    [switch]$Enable,
    [switch]$Remove
)

$runner = Join-Path $PSScriptRoot 'agent-run.ps1'
$workdir = Split-Path -Parent $PSScriptRoot
$jobs = @(
    @{ Name = 'assistant-nightly'; Job = 'nightly'; Trigger = (New-ScheduledTaskTrigger -Daily -At $NightlyAt);
       Desc = "assistant 夜间巡检：每天 $NightlyAt 按当天提交更新项目进展和记忆" },
    @{ Name = 'assistant-morning'; Job = 'morning';
       Trigger = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday, Saturday -At $MorningAt);
       Desc = "assistant 早间建议：周一到周六 $MorningAt 生成今日事项建议，待确认" }
) | Where-Object { $Only -eq 'all' -or $_.Job -eq $Only }

foreach ($j in $jobs) {
    if ($Remove) {
        Unregister-ScheduledTask -TaskName $j.Name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output "已移除 $($j.Name)"
        continue
    }
    if ($Disable -or $Enable) {
        if ($Disable) { Disable-ScheduledTask -TaskName $j.Name | Out-Null } else { Enable-ScheduledTask -TaskName $j.Name | Out-Null }
        continue
    }
    # conhost --headless：不弹黑屏窗口（-WindowStyle Hidden 挡不住任务计划程序先建出来的控制台）
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $workdir `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Job $($j.Job) -Trigger task"
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $j.Trigger -Settings $settings `
        -Description $j.Desc -Force | Out-Null
    Write-Output "已注册 $($j.Name)：$($j.Desc)"
}
if (-not $Remove) { Get-ScheduledTask -TaskName 'assistant-*' | Select-Object TaskName, State | Format-Table -AutoSize }
