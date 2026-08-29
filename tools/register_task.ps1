# 상황판 자동 갱신 — Windows 작업 스케줄러 등록
#
#   powershell -ExecutionPolicy Bypass -File tools\register_task.ps1
#   해제:  powershell -ExecutionPolicy Bypass -File tools\register_task.ps1 -Remove
#
# 10분마다 tools\publish.ps1 을 돌려 수집 → 화면 생성 → gh-pages 배포까지 한다.
# 이 PC 가 켜져 있어야 갱신된다. 한강홍수통제소 API 가 국내 IP 에서만 응답하기
# 때문에 클라우드로 옮길 수 없다.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$taskName = "SejongWaterDashboard"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "tools\publish.ps1"

if ($Remove) {
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "작업 '$taskName' 을 제거했습니다."
    } catch {
        Write-Host "등록된 작업이 없습니다."
    }
    exit 0
}

if (-not (Test-Path $script)) { throw "publish.ps1 을 찾을 수 없습니다: $script" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root

# 10분 간격, 무기한. 로그온 직후에도 한 번 돈다.
$daily = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes 10)
$logon = New-ScheduledTaskTrigger -AtLogOn

# 배터리로도 돌게 둔다. 노트북이라 AC 조건을 걸면 대부분 건너뛴다.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false } catch {}

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger @($daily, $logon) -Settings $settings `
    -Description "세종시 물환경 상황판 수집·생성·배포 (10분 간격)" | Out-Null

Write-Host "작업 '$taskName' 을 등록했습니다 — 10분 간격."
Write-Host "지금 한 번 돌려보려면:  Start-ScheduledTask -TaskName $taskName"
Write-Host "상태 확인:              Get-ScheduledTaskInfo -TaskName $taskName"
Write-Host "해제:                   tools\register_task.ps1 -Remove"
