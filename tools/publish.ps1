# 세종시 물환경 상황판 — 수집 → 생성 → 배포
#
# 왜 PC 에서 도는가
#   한강홍수통제소 API 는 해외 IP 에서 응답하지 않는다(거부가 아니라 타임아웃).
#   GitHub Actions 러너는 해외에 있어 수위·강수를 못 받는다. 그래서 수집과
#   화면 생성은 국내 IP 인 이 PC 에서 하고, GitHub 은 만들어진 페이지를
#   서빙만 한다.
#
# 어떻게 배포하는가
#   gh-pages 브랜치에 **커밋 하나만** 두고 매번 덮어쓴다(amend + force push).
#   매 실행마다 새 커밋을 쌓으면 37KB × 하루 144회 = 한 달 160MB 로 불어난다.
#
# 쓰는 법
#   powershell -ExecutionPolicy Bypass -File tools\publish.ps1
#   자동 실행 등록은 tools\register_task.ps1

# git 은 정상 흐름에서도 stderr 로 말을 건다(빈 브랜치의 rev-parse 등).
# Stop 으로 두면 그게 전부 치명적 오류가 되므로 Continue 로 두고
# 종료코드($LASTEXITCODE)만 보고 판단한다.
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$root = Split-Path -Parent $PSScriptRoot
$python = "C:\Python313\python.exe"
$worktree = Join-Path $root ".ghpages"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

Set-Location $root

# 정지 스위치 - data 폴더에 PAUSED 파일이 있으면 아무것도 하지 않는다.
# 수집을 NAS 로 옮긴 뒤에도 작업 스케줄러 항목이 남아 창이 뜨는 경우가 있는데,
# 그 항목을 지우려면 관리자 권한이 필요하다. 권한 없이 즉시 멈추기 위한 장치.
if (Test-Path (Join-Path $root "data\PAUSED")) {
    Write-Host "PAUSED 파일이 있어 실행하지 않습니다. (지우면 다시 동작)"
    exit 0
}

function Fail($message) {
    Write-Host "[실패] $message" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 1. 수집·생성
Write-Host "[1/3] 수집 및 상황판 생성"
& $python run.py --collect --out site
if ($LASTEXITCODE -ne 0) { Fail "run.py --collect 가 실패했습니다." }
if (-not (Test-Path (Join-Path $root "site\index.html"))) { Fail "site\index.html 이 없습니다." }

# GitHub Pages 의 Jekyll 처리를 끈다(밑줄로 시작하는 파일이 사라지는 것을 막는다)
$nojekyll = Join-Path $root "site\.nojekyll"
if (-not (Test-Path $nojekyll)) { New-Item -ItemType File $nojekyll | Out-Null }

# ---------------------------------------------------------------- 2. 워크트리
if (-not (Test-Path $worktree)) {
    Write-Host "[2/3] gh-pages 워크트리 생성"
    git worktree add --detach $worktree
    if ($LASTEXITCODE -ne 0) { Fail "워크트리를 만들지 못했습니다." }

    Push-Location $worktree
    git checkout --orphan gh-pages          # 소스 이력과 분리된 빈 가지
    git rm -rf . --quiet
    Pop-Location
} else {
    Write-Host "[2/3] 기존 워크트리 사용"
}

# ---------------------------------------------------------------- 3. 배포
Write-Host "[3/3] 배포"
Get-ChildItem $worktree -Force |
    Where-Object { $_.Name -ne ".git" } |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $root "site\*") $worktree -Recurse -Force

Push-Location $worktree
try {
    git add -A
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "     바뀐 내용이 없어 배포를 건너뜁니다."
    } else {
        # 첫 배포인지(=커밋이 아직 없는지) 종료코드로만 판단한다.
        git rev-parse --verify --quiet HEAD | Out-Null
        $hasCommit = ($LASTEXITCODE -eq 0)
        if ($hasCommit) {
            git commit --amend -m "상황판 갱신 $stamp" --quiet
        } else {
            git commit -m "상황판 갱신 $stamp" --quiet
        }
        if ($LASTEXITCODE -ne 0) { Fail "커밋에 실패했습니다." }

        git push -f origin gh-pages --quiet
        if ($LASTEXITCODE -ne 0) { Fail "푸시에 실패했습니다. 인증을 확인하세요." }
        Write-Host "     https://ysong86.github.io/water_sejong/ 갱신됨 ($stamp)"
    }
} finally {
    Pop-Location
}
