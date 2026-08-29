# -*- coding: utf-8 -*-
"""기상청 단기예보 조회서비스 — 초단기실황 + 단기예보(강수).

  실황  http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst
  예보  http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst
  특보  http://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnList  (있으면 표시)

격자(nx, ny)는 설정의 위경도에서 공식 LCC 식으로 계산한다(common.latlon_to_grid).
"""
from __future__ import annotations

import urllib.parse
from datetime import timedelta

from .common import CollectError, http_json, latlon_to_grid, now_kst, to_float

FCST_BASE = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
WARN_BASE = "https://apis.data.go.kr/1360000/WthrWrnInfoService"

PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기",
       "5": "빗방울", "6": "빗방울/눈날림", "7": "눈날림"}
SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}

# 단기예보 발표시각(정시 기준). 실제 배포는 +10분 뒤라 여유를 둔다.
_VILAGE_BASE_HOURS = [2, 5, 8, 11, 14, 17, 20, 23]


def _service_key(key: str) -> str:
    """포털은 인코딩/디코딩 두 형태의 키를 준다. 어느 쪽을 넣어도 동작하게 정규화."""
    key = key.strip()
    if "%" in key:                       # 이미 인코딩된 키
        return key
    return urllib.parse.quote(key, safe="")


def _items(payload) -> list:
    try:
        body = payload["response"]["body"]
    except (KeyError, TypeError):
        header = (payload or {}).get("response", {}).get("header", {})
        raise CollectError(f"응답 형식이 예상과 다릅니다: {header or str(payload)[:200]}")
    items = (body.get("items") or {}).get("item")
    if items is None:
        return []
    return items if isinstance(items, list) else [items]


def _check(payload):
    header = (payload or {}).get("response", {}).get("header", {})
    code = str(header.get("resultCode", "")).strip()
    if code and code not in ("00", "0"):
        raise CollectError(f"기상청 API 오류 {code}: {header.get('resultMsg', '')}")
    return payload


def _ncst_base(dt):
    """초단기실황은 매시 정시 자료가 +40분경 배포된다."""
    if dt.minute < 45:
        dt = dt - timedelta(hours=1)
    return dt.strftime("%Y%m%d"), dt.strftime("%H00")


def _vilage_base(dt):
    probe = dt - timedelta(minutes=15)          # 배포 지연 보정
    for hour in reversed(_VILAGE_BASE_HOURS):
        if probe.hour >= hour:
            return probe.strftime("%Y%m%d"), f"{hour:02d}00"
    prev = probe - timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


def _pcp(text):
    """'강수없음' / '1.0mm' / '30.0~50.0mm' 를 숫자와 표시문자열로."""
    text = (text or "").strip()
    if not text or text in ("강수없음", "-", "0", "적설없음"):
        return 0.0, "없음"
    number = to_float(text.replace("mm", "").replace("cm", "").split("~")[0])
    return (number if number is not None else 0.0), text


def fetch_now(key: str, nx: int, ny: int) -> dict:
    base_date, base_time = _ncst_base(now_kst())
    payload = _check(http_json(f"{FCST_BASE}/getUltraSrtNcst", {
        "serviceKey": _service_key(key), "pageNo": 1, "numOfRows": 100,
        "dataType": "JSON", "base_date": base_date, "base_time": base_time,
        "nx": nx, "ny": ny,
    }))
    values = {i.get("category"): i.get("obsrValue") for i in _items(payload)}
    return {
        # 상황판의 관측시각 파서가 읽는 형식으로 맞춘다("2026-08-29 11:00").
        "base": "%s-%s-%s %s:00" % (base_date[:4], base_date[4:6], base_date[6:8],
                                    base_time[:2]),
        "temp": to_float(values.get("T1H")),
        "rain_1h": to_float(values.get("RN1"), 0.0),
        "humidity": to_float(values.get("REH")),
        "wind": to_float(values.get("WSD")),
        "pty": PTY.get(str(values.get("PTY", "0")).strip(), "-"),
    }


def fetch_forecast(key: str, nx: int, ny: int, hours: int = 24) -> dict:
    base_date, base_time = _vilage_base(now_kst())
    payload = _check(http_json(f"{FCST_BASE}/getVilageFcst", {
        "serviceKey": _service_key(key), "pageNo": 1, "numOfRows": 1000,
        "dataType": "JSON", "base_date": base_date, "base_time": base_time,
        "nx": nx, "ny": ny,
    }))
    slots: dict[str, dict] = {}
    for item in _items(payload):
        stamp = f"{item.get('fcstDate', '')}{item.get('fcstTime', '')}"
        if len(stamp) != 12:
            continue
        slot = slots.setdefault(stamp, {"t": stamp})
        category, value = item.get("category"), item.get("fcstValue")
        if category == "POP":
            slot["pop"] = to_float(value)
        elif category == "PCP":
            slot["pcp"], slot["pcp_text"] = _pcp(value)
        elif category == "TMP":
            slot["temp"] = to_float(value)
        elif category == "SKY":
            slot["sky"] = SKY.get(str(value).strip(), "-")
        elif category == "PTY":
            slot["pty"] = PTY.get(str(value).strip(), "-")

    ordered = [slots[k] for k in sorted(slots)][:hours]
    rain_total = round(sum(s.get("pcp") or 0.0 for s in ordered), 1)
    return {
        # 상황판의 관측시각 파서가 읽는 형식으로 맞춘다("2026-08-29 11:00").
        "base": "%s-%s-%s %s:00" % (base_date[:4], base_date[4:6], base_date[6:8],
                                    base_time[:2]),
        "slots": ordered,
        "rain_sum": rain_total,
        "pop_max": max((s.get("pop") or 0 for s in ordered), default=0),
    }


def fetch_warnings(key: str, stn_id: str = "133") -> list[dict]:
    """기상특보 목록(선택). 실패해도 상황판 전체를 막지 않는다. 133 = 대전·세종."""
    today = now_kst()
    payload = _check(http_json(f"{WARN_BASE}/getWthrWrnList", {
        "serviceKey": _service_key(key), "pageNo": 1, "numOfRows": 20,
        "dataType": "JSON", "stnId": stn_id,
        "fromTmFc": (today - timedelta(days=2)).strftime("%Y%m%d"),
        "toTmFc": today.strftime("%Y%m%d"),
    }))
    out = []
    for item in _items(payload):
        out.append({
            "title": (item.get("title") or item.get("warnVar") or "").strip(),
            "time": str(item.get("tmFc") or "").strip(),
            "detail": (item.get("other") or item.get("command") or "").strip(),
        })
    return out


def collect(key: str, cfg: dict) -> dict:
    lat = float(cfg.get("lat", 36.4800))
    lon = float(cfg.get("lon", 127.2890))
    nx, ny = (cfg.get("nx"), cfg.get("ny"))
    if not nx or not ny:
        nx, ny = latlon_to_grid(lat, lon)

    result = {"grid": {"nx": nx, "ny": ny, "lat": lat, "lon": lon},
              "now": None, "forecast": None, "warnings": [], "errors": []}
    try:
        result["now"] = fetch_now(key, nx, ny)
    except CollectError as exc:
        result["errors"].append(f"초단기실황: {exc}")
    try:
        result["forecast"] = fetch_forecast(key, nx, ny, int(cfg.get("forecast_hours", 24)))
    except CollectError as exc:
        result["errors"].append(f"단기예보: {exc}")
    if cfg.get("warnings", True):
        try:
            result["warnings"] = fetch_warnings(key, str(cfg.get("stn_id", "133")))
        except CollectError as exc:
            # 특보는 단기예보와 다른 서비스라 활용신청이 따로 필요하다.
            # 403 이면 키가 틀린 게 아니라 그 서비스에 권한이 없다는 뜻이다.
            if "403" in str(exc):
                result["errors"].append(
                    "기상특보(선택): 이 인증키에 권한이 없습니다. 공공데이터포털에서 "
                    "'기상청_기상특보 조회서비스'를 따로 활용신청하거나, "
                    "config 의 kma.warnings 를 false 로 두면 이 줄이 사라집니다.")
            else:
                result["errors"].append("기상특보(선택): %s" % exc)
    return result
