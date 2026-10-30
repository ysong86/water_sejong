# -*- coding: utf-8 -*-
"""NAS 의 코드를 GitHub 의 main 가지에 맞춘다. 수집·배포 전에 한 번 돌린다.

왜 필요한가
  수집은 국내 IP 에서만 되므로 NAS 가 돌린다. 그런데 코드는 PC 에서 고친다.
  그동안은 고칠 때마다 File Station 으로 파일을 손으로 올렸는데, 몇 개를
  빠뜨렸는지 알 길이 없고 실제로 화면이 옛날 것으로 남아 있던 적이 있다.
  저장소가 공개라 토큰 없이 받아올 수 있으니, NAS 가 스스로 맞추게 한다.

무엇을 건드리지 않나
  config.json (인증키), data/ (수집 결과), site/ 는 내려받지 않는다.
  저장소에 없는 파일이라 애초에 대상이 아니지만, 혹시 올라가더라도
  덮어쓰지 않도록 KEEP 에 못박아 둔다.

안전장치
  - .py 는 바꿔치기 전에 컴파일해 본다. 깨진 파일을 받아 두면 다음 수집부터
    통째로 멈춘다.
  - 바꾸기 전 파일은 .bak 로 남긴다.
  - blob sha 를 manifest 에 적어 두고, 바뀐 것만 받는다. 평소에는 API 한 번.

쓰는 법 (DSM 작업 스케줄러의 명령을 이렇게 바꾼다)
  cd /volume1/homes/withsyh/sejong_water && python3 tools/selfupdate.py && python3 tools/publish_nas.py

  처음 한 번만:
  python3 tools/selfupdate.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "ysong86/water_sejong"
BRANCH = "main"
TREE = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (REPO, BRANCH)
RAW = "https://raw.githubusercontent.com/%s/%s/%%s" % (REPO, BRANCH)
MANIFEST = os.path.join(ROOT, "data", "selfupdate.json")

# 내려받아도 되는 것만. 저장소에 뭐가 더 생기든 여기 없으면 건드리지 않는다.
ALLOW_DIRS = ("collectors/", "tools/", "assets/")
ALLOW_FILES = ("build_dashboard.py", "sejong_map.py", "run.py",
               "config.public.json")
# 절대 덮어쓰지 않는다.
KEEP = ("config.json", "data/", "site/", "dashboard.html")


def wanted(path: str) -> bool:
    if any(path == k or path.startswith(k) for k in KEEP):
        return False
    return path in ALLOW_FILES or path.startswith(ALLOW_DIRS)


def get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sejong-water-selfupdate"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_manifest() -> dict:
    try:
        with open(MANIFEST, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return {}


def save_manifest(data: dict) -> None:
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)


def install(path: str, blob: bytes) -> str:
    """내려받은 내용을 제자리에 놓는다. 성공하면 '', 아니면 까닭."""
    target = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(target), exist_ok=True)

    tmp = tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(target),
                                      suffix=".new")
    try:
        tmp.write(blob)
        tmp.close()
        if path.endswith(".py"):
            # 깨진 파이썬을 제자리에 놓으면 다음 수집부터 통째로 멈춘다.
            try:
                py_compile.compile(tmp.name, doraise=True, cfile=tmp.name + ".pyc")
            except py_compile.PyCompileError as exc:
                return "문법 오류: %s" % str(exc).splitlines()[-1][:90]
            finally:
                if os.path.exists(tmp.name + ".pyc"):
                    os.remove(tmp.name + ".pyc")
        if os.path.exists(target):
            shutil.copy2(target, target + ".bak")
        os.replace(tmp.name, target)
        return ""
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GitHub 의 main 에 맞춘다")
    parser.add_argument("--force", action="store_true",
                        help="manifest 를 무시하고 전부 다시 받는다")
    parser.add_argument("--dry-run", action="store_true", help="무엇이 바뀌는지만")
    args = parser.parse_args(argv)

    try:
        tree = json.loads(get(TREE).decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        # 받아오지 못해도 수집은 계속돼야 한다. 옛 코드로라도 도는 편이 낫다.
        print("목록을 받지 못했습니다(%r) — 지금 코드로 그대로 갑니다." % exc)
        return 0

    have = {} if args.force else load_manifest()
    todo = []
    for node in tree.get("tree") or []:
        if node.get("type") != "blob":
            continue
        path = node["path"]
        if not wanted(path):
            continue
        if have.get(path) != node["sha"]:
            todo.append((path, node["sha"]))

    if not todo:
        print("최신 상태입니다.")
        return 0
    print("%d개 바뀜" % len(todo))
    if args.dry_run:
        for path, _ in todo:
            print("  " + path)
        return 0

    fresh = dict(have)
    failed = 0
    for path, sha in todo:
        try:
            blob = get(RAW % path)
        except urllib.error.URLError as exc:
            print("  %s — 받기 실패 %r" % (path, exc))
            failed += 1
            continue
        why = install(path, blob)
        if why:
            print("  %s — 건너뜀 (%s)" % (path, why))
            failed += 1
            continue
        fresh[path] = sha
        print("  %s" % path)

    save_manifest(fresh)
    if failed:
        print("%d개 실패 — 그 파일은 예전 것 그대로입니다." % failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
