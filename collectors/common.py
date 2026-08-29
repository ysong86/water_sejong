# -*- coding: utf-8 -*-
"""공통 유틸 — HTTP 호출, 설정 로드, 기상청 격자 변환. 표준 라이브러리만 사용."""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
KST = timezone(timedelta(hours=9))

USER_AGENT = "sejong-water-dashboard/1.0 (+python-urllib)"
DEFAULT_TIMEOUT = 20


def safe_url(url) -> str:
    """오류 메시지에 쓸 주소. 인증키를 가린다.

    HRFCO 는 키를 경로에 넣고(api.hrfco.go.kr/{키}/...), 공공데이터포털·GIMS 는
    질의문자열에 넣는다. 이 문자열이 공개 저장소의 실행 로그에 남으면 키가
    새므로 양쪽 다 가린다. 정규식 대신 문자열 처리로 쓴 이유는 단순함 때문이다.
    """
    text = str(url or "")

    # 1) 경로에 든 키 — 호스트 바로 뒤 한 조각
    marker = "api.hrfco.go.kr/"
    at = text.find(marker)
    if at >= 0:
        head = text[:at + len(marker)]
        tail = text[at + len(marker):]
        cut = tail.find("/")
        text = head + "<KEY>" + (tail[cut:] if cut >= 0 else "")

    # 2) 질의문자열에 든 키
    if "?" in text:
        base, _, query = text.partition("?")
        parts = []
        for chunk in query.split("&"):
            name = chunk.split("=", 1)[0]
            if name.lower() in ("servicekey", "key", "authkey", "apikey"):
                parts.append(name + "=<KEY>")
            else:
                parts.append(chunk)
        text = base + "?" + "&".join(parts)
    return text


class CollectError(Exception):
    """수집 실패. 메시지는 대시보드에 그대로 노출되므로 사람이 읽을 수 있게 쓴다."""


# --------------------------------------------------------------------------- 시간

def now_kst() -> datetime:
    return datetime.now(KST)


def stamp(dt: datetime | None = None) -> str:
    return (dt or now_kst()).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- 설정

# 섹션별 인증키를 넣을 수 있는 환경변수. CI(깃허브 액션)에서 Secrets 를 이렇게 넘긴다.
KEY_ENV = {"hrfco": "HRFCO_KEY", "kma": "KMA_KEY",
           "nier": "NIER_KEY", "gims": "GIMS_KEY", "nierstat": "NIER_KEY"}


def load_config(path=None) -> dict:
    """config.json → 없으면 config.public.json(키 없는 공개 설정) 순으로 읽고,
    환경변수에 키가 있으면 그쪽으로 덮어쓴다.

    저장소에 키를 커밋하지 않고도 CI 에서 그대로 돌리기 위한 구조다.
    """
    candidates = [path] if path else [
        os.path.join(BASE_DIR, "config.json"),
        os.path.join(BASE_DIR, "config.public.json"),
    ]
    chosen = next((c for c in candidates if c and os.path.exists(c)), None)
    if not chosen:
        example = os.path.join(BASE_DIR, "config.example.json")
        raise CollectError(
            "config.json 이 없습니다. config.example.json 을 복사해 API 키를 채워주세요." + chr(10) +
            '  copy "%s" "%s"' % (example, os.path.join(BASE_DIR, "config.json")))
    with open(chosen, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)

    for section, env_name in KEY_ENV.items():
        value = (os.environ.get(env_name) or "").strip()
        if value and isinstance(cfg.get(section), dict):
            cfg[section]["key"] = value
    cfg["_config_path"] = chosen
    return cfg


def save_json(name: str, payload) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return path


def load_json(name: str, default=None):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (ValueError, OSError):
        return default


# --------------------------------------------------------------------------- HTTP

_SSL_CTX = ssl.create_default_context()


def http_get(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
             retries: int = 2, insecure: bool = False) -> str:
    """GET 후 본문을 문자열로. 공공 API 는 간헐적으로 죽으므로 짧게 재시도한다."""
    if params:
        # serviceKey 는 이미 URL 인코딩된 값을 받는 경우가 많아 quote_via 를 조심히 쓴다.
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="%")
    ctx = _SSL_CTX
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read()
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    detail = last
    if isinstance(last, urllib.error.HTTPError):
        detail = "HTTP %s %s" % (last.code, last.reason)
    elif isinstance(last, urllib.error.URLError):
        detail = "연결 실패 (%s)" % last.reason
    raise CollectError("요청 실패: %s — %s" % (safe_url(url.split("?")[0]), detail))


def http_json(url: str, params: dict | None = None, **kw):
    text = http_get(url, params, **kw)
    stripped = text.lstrip()
    if not stripped:
        raise CollectError("응답이 비어 있습니다.")
    if stripped[0] not in "{[":
        # 공공데이터포털은 오류를 XML/HTML 로 돌려준다. 앞부분을 그대로 보여주는 편이 진단에 낫다.
        raise CollectError("JSON 이 아닌 응답: %s" % safe_url(stripped[:300]))
    try:
        return json.loads(stripped)
    except ValueError as exc:
        raise CollectError(f"JSON 파싱 실패: {exc}") from exc


def to_float(value, default=None):
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if text in ("", "-", "null", "None"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- 기상청 격자

# 기상청 동네예보 Lambert Conformal Conic 파라미터 (5km 격자)
_LCC = dict(RE=6371.00877, GRID=5.0, SLAT1=30.0, SLAT2=60.0,
            OLON=126.0, OLAT=38.0, XO=43, YO=136)


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 동네예보 격자 (nx, ny). 공식 dfs_xy_conv 이식."""
    p = _LCC
    DEGRAD = math.pi / 180.0
    re = p["RE"] / p["GRID"]
    slat1 = p["SLAT1"] * DEGRAD
    slat2 = p["SLAT2"] * DEGRAD
    olon = p["OLON"] * DEGRAD
    olat = p["OLAT"] * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + p["XO"] + 0.5)
    ny = int(ro - ra * math.cos(theta) + p["YO"] + 0.5)
    return nx, ny
