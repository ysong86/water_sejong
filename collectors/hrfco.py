# -*- coding: utf-8 -*-
"""한강홍수통제소(HRFCO) 오픈API — 수위관측소 / 강수량관측소.

  제원  https://api.hrfco.go.kr/{key}/waterlevel/info.json
  자료  https://api.hrfco.go.kr/{key}/waterlevel/list/10M/{코드}/{시작}/{종료}.json
        (10M = 10분 단위. HRFCO 가 실제로 갱신하는 주기이므로 이게 기본값이다.
         한강권역 7~8분, 금강권역 11분 이상 걸려 관측소별 최신시각이 조금씩 다르다.)
        (rainfall 도 동일 구조, 코드 필드만 rfobscd)

관측소는 코드를 하드코딩하지 않고 제원에서 주소로 걸러낸다(세종 구간은 관측소가
개편되는 일이 있어 코드를 박아두면 조용히 빈 값이 된다).
"""
from __future__ import annotations

import re
from datetime import timedelta

from .common import CollectError, http_json, now_kst, to_float

BASE = "https://api.hrfco.go.kr"
INFO_CACHE = "hrfco_stations.json"


def _rows(payload) -> list:
    """HRFCO 응답에서 레코드 목록을 꺼낸다. 래퍼 키가 문서마다 달라 관대하게 처리."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("content", "Content", "list", "WL", "RF", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            inner = _rows(value)
            if inner:
                return inner
    for value in payload.values():          # 마지막 수단: 중첩된 첫 레코드 리스트
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
        if isinstance(value, dict):
            inner = _rows(value)
            if inner:
                return inner
    return []


def _lower(row: dict) -> dict:
    return {str(k).lower(): v for k, v in row.items()}


def _coord(value):
    """'127-17-30' 같은 도분초 문자열과 십진수 둘 다 받는다."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    direct = to_float(text)
    if direct is not None and "-" not in text:
        return direct
    parts = [p for p in re.split(r"[-:\s]+", text) if p]
    try:
        deg = float(parts[0])
        minute = float(parts[1]) if len(parts) > 1 else 0.0
        second = float(parts[2]) if len(parts) > 2 else 0.0
    except (ValueError, IndexError):
        return None
    return deg + minute / 60.0 + second / 3600.0


def fetch_stations(key: str, hydro: str = "waterlevel") -> list[dict]:
    payload = http_json(f"{BASE}/{key}/{hydro}/info.json")
    out = []
    for raw in _rows(payload):
        row = _lower(raw)
        code = row.get("wlobscd") or row.get("rfobscd") or row.get("obscd")
        if not code:
            continue
        out.append({
            "code": str(code),
            "name": (row.get("obsnm") or "").strip(),
            "agency": (row.get("agcnm") or "").strip(),
            "addr": " ".join(x for x in [(row.get("addr") or "").strip(),
                                         (row.get("etcaddr") or "").strip()] if x),
            "lat": _coord(row.get("lat")),
            "lon": _coord(row.get("lon")),
            # 홍수 단계 수위(m). 수위관측소에만 존재.
            "attwl": to_float(row.get("attwl")),   # 관심
            "wrnwl": to_float(row.get("wrnwl")),   # 주의보
            "almwl": to_float(row.get("almwl")),   # 경보
            "srswl": to_float(row.get("srswl")),   # 심각
            "pfh": to_float(row.get("pfh")),       # 계획홍수위
        })
    if not out:
        raise CollectError(f"{hydro} 관측소 제원을 받지 못했습니다. 인증키를 확인해주세요.")
    return out


def filter_stations(stations: list[dict], include: list[str],
                    names: list[str] | None = None) -> list[dict]:
    """주소에 include 키워드가 있거나 이름이 names 에 있는 관측소만."""
    names = [n.strip() for n in (names or []) if n.strip()]
    picked, seen = [], set()
    for st in stations:
        hay = f"{st['addr']} {st['name']}"
        hit = any(kw and kw in hay for kw in include) or st["name"] in names
        if hit and st["code"] not in seen:
            seen.add(st["code"])
            picked.append(st)
    return picked


TIME_FMT = {"10M": "%Y%m%d%H%M", "1H": "%Y%m%d%H", "1D": "%Y%m%d"}


def fetch_series(key: str, code: str, hydro: str = "waterlevel",
                 hours: int = 24, time_type: str = "1H") -> list[dict]:
    """기간 시계열.

    주의 — 10M 로 기간을 지정하면 HRFCO 는 행은 주지만 값을 전부 공백으로 돌려준다
    (관측소·기간 무관하게 재현됨). 그래서 추이는 1H 로 받고, 10분 해상도의
    현재값은 fetch_latest() 로 따로 가져와 덮어쓴다.
    """
    end = now_kst()
    start = end - timedelta(hours=hours)
    fmt = TIME_FMT.get(time_type, "%Y%m%d%H")
    url = (f"{BASE}/{key}/{hydro}/list/{time_type}/{code}/"
           f"{start.strftime(fmt)}/{end.strftime(fmt)}.json")
    payload = http_json(url)
    series = []
    for raw in _rows(payload):
        row = _lower(raw)
        ymdhm = str(row.get("ymdhm") or "").strip()
        if not ymdhm:
            continue
        value = to_float(row.get("wl")) if hydro == "waterlevel" else to_float(row.get("rf"))
        point = {"t": ymdhm, "v": value}
        if hydro == "waterlevel":
            point["fw"] = to_float(row.get("fw"))
        series.append(point)
    series.sort(key=lambda p: p["t"])
    return series


def fetch_latest(key: str, code: str, hydro: str = "waterlevel") -> dict | None:
    """기간 없이 코드만 지정하면 10분 단위 최신 1건이 값과 함께 온다."""
    url = f"{BASE}/{key}/{hydro}/list/10M/{code}.json"
    for raw in _rows(http_json(url)):
        row = _lower(raw)
        ymdhm = str(row.get("ymdhm") or "").strip()
        value = to_float(row.get("wl")) if hydro == "waterlevel" else to_float(row.get("rf"))
        if not ymdhm or value is None:
            continue
        point = {"t": ymdhm, "v": value}
        if hydro == "waterlevel":
            point["fw"] = to_float(row.get("fw"))
        return point
    return None


def flood_stage(level, st: dict) -> tuple[str, str]:
    """현재 수위를 홍수 단계로 환산. (등급코드, 라벨)"""
    if level is None:
        return "unknown", "자료없음"
    steps = [("srswl", "serious", "심각"), ("almwl", "alert", "경보"),
             ("wrnwl", "warning", "주의보"), ("attwl", "watch", "관심")]
    for field, code, label in steps:
        threshold = st.get(field)
        if threshold is not None and level >= threshold:
            return code, label
    return "normal", "평상"


def collect(key: str, cfg: dict) -> dict:
    """세종 구간 수위·강수 관측소 현황을 한 덩어리로 반환."""
    include = cfg.get("address_keywords") or ["세종"]
    result = {"waterlevel": [], "rainfall": [], "errors": []}

    for hydro, name_key in (("waterlevel", "waterlevel_stations"),
                            ("rainfall", "rainfall_stations")):
        try:
            stations = filter_stations(fetch_stations(key, hydro), include,
                                       cfg.get(name_key))
        except CollectError as exc:
            result["errors"].append(f"{hydro} 제원: {exc}")
            continue
        if not stations:
            result["errors"].append(
                f"{hydro}: '{'/'.join(include)}' 에 해당하는 관측소를 찾지 못했습니다.")
            continue

        limit = int(cfg.get("max_stations", 8))
        for st in stations[:limit]:
            entry = dict(st)
            try:
                series = fetch_series(key, st["code"], hydro,
                                      hours=int(cfg.get("hours", 24)),
                                      time_type=cfg.get("time_type", "1H"))
            except CollectError as exc:
                entry.update(latest=None, series=[], error=str(exc))
                result[hydro].append(entry)
                continue

            latest = next((p for p in reversed(series) if p["v"] is not None), None)
            try:                       # 10분 해상도 현재값으로 갈아끼운다
                fresh = fetch_latest(key, st["code"], hydro)
            except CollectError:
                fresh = None
            if fresh and (not latest or fresh["t"] >= latest["t"][:len(fresh["t"])]):
                latest = fresh
            entry["series"] = series
            entry["latest"] = latest
            if hydro == "waterlevel":
                level = latest["v"] if latest else None
                entry["stage"], entry["stage_label"] = flood_stage(level, st)
                # 직전 관측과의 차가 아니라 "1시간 전 대비" 로 잡는다.
                # 10분 해상도에서 직전 값과 비교하면 변화량이 사실상 0으로 보인다.
                step = {"10M": 6, "1H": 1, "1D": 1}.get(cfg.get("time_type", "1H"), 1)
                prior = [p["v"] for p in series if p["v"] is not None]
                entry["delta_1h"] = (round(prior[-1] - prior[-1 - step], 3)
                                     if len(prior) > step else None)
            else:
                values = [p["v"] for p in series if p["v"] is not None]
                entry["sum_24h"] = round(sum(values), 1) if values else None
            result[hydro].append(entry)

    return result
