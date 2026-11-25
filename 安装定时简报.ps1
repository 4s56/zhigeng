# 今天 · 定时简报 —— 注册 / 移除 Windows 计划任务
#   -Task install  注册三个每日任务（简报 08:00 / 22:00，发现 09:00）
#   -Task remove   移除
#   -Task status   查看状态
# 用法： powershell -ExecutionPolicy Bypass -File 安装定时简报.ps1 -Task install

param(
    [ValidateSet('install', 'remove', 'status')][string]$Task = 'install',
    [string]$Morning = '08:00',
    [string]$Evening = '22:00',
    [string]$Inspire = '09:00'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$root   = $PSScriptRoot
$entry  = Join-Path $root 'brief.py'
$entryI = Join-Path $root 'inspire.py'
$nameM  = 'TodayAgent_Morning'
$nameE  = 'TodayAgent_Evening'
$nameI  = 'TodayAgent_Inspire'
$all    = @($nameM, $nameE, $nameI)

function Get-PyW {
    $cands = @()
    try {
        $out = & py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2>$null
        if ($out) { $cands += ("$out").Trim() }
    } catch {}
    $cands += @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
        'C:\Python312\pythonw.exe'
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return 'pythonw.exe'
}

switch ($Task) {
    'status' {
        foreach ($n in $all) {
            $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
            if (-not $t) { Write-Output "$n : 未安装"; continue }
            $i = Get-ScheduledTaskInfo -TaskName $n
            Write-Output "$n : $($t.State)    下次运行 $($i.NextRunTime)"
        }
    }
    'remove' {
        foreach ($n in $all) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue
        }
        Write-Output '已移除定时简报 / 发现任务'
    }
    default {
        if (-not (Test-Path $entry)) {
            Write-Output "找不到 $entry"
            exit 1
        }
        $pyw = Get-PyW
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

        $jobs = @(
            @{ Name = $nameM; Entry = $entry;  Args = 'morning'; At = $Morning; Desc = '早间简报' },
            @{ Name = $nameE; Entry = $entry;  Args = 'evening'; At = $Evening; Desc = '晚间收尾' },
            @{ Name = $nameI; Entry = $entryI; Args = 'run';     At = $Inspire; Desc = '今日发现 · 项目推送' }
        )

        foreach ($j in $jobs) {
            if (-not (Test-Path $j.Entry)) {
                Write-Output "跳过 $($j.Name)：找不到 $($j.Entry)"
                continue
            }
            $action = New-ScheduledTaskAction -Execute $pyw `
                -Argument "`"$($j.Entry)`" $($j.Args)" -WorkingDirectory $root
            $trigger = New-ScheduledTaskTrigger -Daily -At $j.At
            Register-ScheduledTask -TaskName $j.Name -Action $action -Trigger $trigger `
                -Settings $settings -Force `
                -Description "今天 · $($j.Desc)（每天 $($j.At)）" | Out-Null
            Write-Output "$($j.Name) 已安装：每天 $($j.At)（$($j.Desc)）"
        }
        Write-Output "解释器：$pyw"
    }
}
