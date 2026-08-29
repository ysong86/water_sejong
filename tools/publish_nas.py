# -*- coding: utf-8 -*-
"""시놀로지 NAS 등 리눅스 환경용 수집·배포기 — git 없이 GitHub API 로 올린다.

왜 NAS 인가
  한강홍수통제소 API 는 해외 IP 에 응답하지 않는다. GitHub Actions 로는 수위·강수를
  받을 수 없어서 국내 IP 에서 수집해야 한다. 집 PC 는 절전에 들어가면 멈추므로
  24시간 켜져 있는 NAS 가 그 자리에 맞다.

왜 git 을 안 쓰나
  NAS 에 git 이 없을 수 있고, 있어도 자격증명 관리가 번거롭다. GitHub Contents API
  로 파일 하나만 PUT 하면 되므로 표준 라이브러리만으로 끝난다. 브랜치에 커밋이
  쌓이지만 파일 한 개(index.html)뿐이라 저장소가 부풀지 않는다.

준비
  1. GitHub 토큰 발급 — https://github.com/settings/tokens?type=beta
     Repository access: water_sejong 하나만.  Permissions: Contents = Read and write.
  2. config.json 에 아래를 넣거나 환경변수 GITHUB_TOKEN 으로 준다.

       "deploy": {
         "repo": "ysong86/water_sejong",
         "branch": "gh-pages",
         "path": "index.html",
         "token": "github_pat_..."
       }

  3. DSM 제어판 → 작업 스케줄러 → 예약 작업 → 사용자 정의 스크립트, 10분 간격:
       cd /volume1/<경로>/sejong_water && python3 tools/publish_nas.py

동작 확인
  python3 tools/publish_nas.py --dry-run     수집·생성만 하고 올리지 않는다
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_dashboard  # noqa: E402
from collectors.common import CollectError, load_config, now_kst, save_json, stamp  # noqa: E402
from run import collect  # noqa: E402

API = "https://api.github.com"


def _request(method: str, url: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "sejong-water-dashboard",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def current_sha(repo: str, path: str, branch: str, token: str):
    """이미 올라간 파일의 sha. 없으면 None(첫 업로드)."""
    url = "%s/repos/%s/contents/%s?ref=%s" % (API, repo, path, branch)
    try:
        return _request("GET", url, token).get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def upload(repo: str, path: str, branch: str, token: str, content: bytes,
           message: str) -> str:
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    sha = current_sha(repo, path, branch, token)
    if sha:
        payload["sha"] = sha
    url = "%s/repos/%s/contents/%s" % (API, repo, path)
    result = _request("PUT", url, token, payload)
    return (result.get("commit") or {}).get("sha", "")[:7]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NAS 수집·배포")
    parser.add_argument("--dry-run", action="store_true", help="수집·생성만, 업로드 안 함")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    print("수집 시작 %s" % stamp(now_kst()))
    data = collect(cfg)
    data["site"] = cfg.get("site") or {}
    save_json("latest.json", data)

    html = build_dashboard.render(data).encode("utf-8")
    print("상황판 생성 %d KB" % (len(html) // 1024))

    if args.dry_run:
        out = os.path.join(os.path.dirname(build_dashboard.OUT_PATH), "site")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "index.html"), "wb") as fp:
            fp.write(html)
        print("--dry-run: site/index.html 로만 저장했습니다.")
        return 0

    deploy = cfg.get("deploy") or {}
    token = (os.environ.get("GITHUB_TOKEN") or deploy.get("token") or "").strip()
    repo = deploy.get("repo") or ""
    if not token or not repo:
        print("deploy.repo 와 토큰이 필요합니다. 파일 첫머리 주석을 보세요.")
        return 1

    branch = deploy.get("branch") or "gh-pages"
    path = deploy.get("path") or "index.html"
    try:
        # Jekyll 처리를 끄는 빈 파일. 이미 있으면 그대로 둔다.
        if current_sha(repo, ".nojekyll", branch, token) is None:
            upload(repo, ".nojekyll", branch, token, b"", "Jekyll 처리 끄기")
        short = upload(repo, path, branch, token, html,
                       "상황판 갱신 %s" % stamp(now_kst()))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print("업로드 실패 HTTP %s — %s" % (exc.code, detail))
        return 1
    except urllib.error.URLError as exc:
        print("업로드 실패: %s" % exc.reason)
        return 1

    print("배포 완료 (%s) — https://%s.github.io/%s/"
          % (short, repo.split("/")[0], repo.split("/")[-1]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CollectError as exc:
        print(exc)
        sys.exit(1)
