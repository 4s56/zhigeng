# 桌面气泡提示：不抢焦点，几秒自动消失
param([string]$Title = "知更", [string]$Msg = "")

Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(8000, $Title, $Msg, "Info")
Start-Sleep -Seconds 9
$n.Dispose()
