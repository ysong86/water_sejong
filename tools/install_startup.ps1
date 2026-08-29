# 자동 갱신 — 관리자 권한 없이 (시작프로그램 방식)
#
#   powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1
#   해제:  powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1 -Remove
#
# 작업 스케줄러(tools\register_task.ps1)가 관리자 권한을 요구해 실패할 때 쓴다.
# 로그인할 때 시작해 창 없이 돌면서 10분마다 publish.ps1 을 실행한다.
#
# 스케줄러 방식과의 차이
#   - 로그인해 있어야 돈다(스케줄러는 로그아웃 상태에서도 가능).
#   - 절전에서 깨어나면 다음 주기부터 다시 돈다(밀린 실행을 바로 따라잡지는 않는다).
# 관리자 권한을 쓸 수 있으면 register_task.ps1 쪽이 낫다.

param([switch]$Remove)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$root = Split-Path -Parent $PSScriptRoot
$loop = Join-Path $root "tools\_loop.ps1"
$startup = [Environment]::GetFolderPath('Startup')
$shortcut = Join-Path $startup "SejongWaterDashboard.lnk"

if ($Remove) {
    if (Test-Path $shortcut) { Remove-Item $shortcut -Force; Write-Host "시작프로그램에서 제거했습니다." }
    else { Write-Host "등록된 시작프로그램이 없습니다." }
    if (Test-Path $loop) { Remove-Item $loop -Force }
    Write-Host "실행 중인 것은 다음 로그인부터 사라집니다. 지금 멈추려면 작업 관리자에서 powershell 을 종료하세요."
    exit 0
}

# 10분마다 publish 를 돌리는 루프 스크립트
@'
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$publish = Join-Path $root "tools\publish.ps1"
$log = Join-Path $root "data\autorun.log"
while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $publish *>&1 |
            Select-Object -Last 3 | ForEach-Object { "$stamp  $_" } |
            Add-Content -Path $log -Encoding utf8
    } catch {
        "$stamp  실패: $_" | Add-Content -Path $log -Encoding utf8
    }
    # 로그가 무한정 자라지 않게 최근 400줄만 남긴다
    if ((Test-Path $log) -and ((Get-Content $log | Measure-Object -Line).Lines -gt 400)) {
        Get-Content $log -Tail 200 | Set-Content $log -Encoding utf8
    }
    Start-Sleep -Seconds 600
}
'@ | Set-Content -Path $loop -Encoding utf8

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = "powershell.exe"
$link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$loop`""
$link.WorkingDirectory = $root
$link.Description = "세종시 물환경 상황판 자동 갱신 (10분)"
$link.Save()

Write-Host "시작프로그램에 등록했습니다 - 다음 로그인부터 10분마다 자동 갱신됩니다."
Write-Host "지금 바로 시작하려면:"
Write-Host ('  Start-Process powershell -ArgumentList @(''-NoProfile'',''-ExecutionPolicy'',''Bypass'',''-WindowStyle'',''Hidden'',''-File'',''"' + $loop + '"'')')
Write-Host "  (경로에 공백이 있어 -File 값은 반드시 따옴표로 감싸야 합니다)"
Write-Host "실행 기록:  data\autorun.log"
Write-Host "해제:       tools\install_startup.ps1 -Remove"
