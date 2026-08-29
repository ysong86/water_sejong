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
