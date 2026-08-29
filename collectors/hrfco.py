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
    """HRFCO 응답에서 레코드 목록을 꺼낸다. 래퍼 키가 문서마다 달라 관대하게 처리.

    dam/info 처럼 목록 안에 null 이 섞여 오는 응답이 있어 dict 만 남긴다.
    (예전에는 여기서 걸러내지 않아 댐 조회가 통째로 죽었다.)
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("content", "Content", "list", "WL", "RF", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            inner = _rows(value)
            if inner:
                return inner
    for value in payload.values():          # 마지막 수단: 중첩된 첫 레코드 리스트
        if isinstance(value, list) and any(isinstance(x, dict) for x in value):
            return [x for x in value if isinstance(x, dict)]
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
        code = (row.get("wlobscd") or row.get("rfobscd") or row.get("boobscd")
                or row.get("dmobscd") or row.get("obscd"))
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
            "spcwl": to_float(row.get("spcwl")),   # 보 관리수위
            "fldlmtwl": to_float(row.get("fldlmtwl")),  # 댐 홍수제한수위
            "gdt": to_float(row.get("gdt")),       # 영점표고(EL.m) — 단면도 기준면
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


def fetch_bo_series(key: str, code: str, hours: int = 24) -> list[dict]:
    """보는 상류(swl)·하류(owl) 수위와 유입·방류량을 함께 준다."""
    end = now_kst()
    start = end - timedelta(hours=hours)
    url = (f"{BASE}/{key}/bo/list/1H/{code}/"
           f"{start.strftime('%Y%m%d%H')}/{end.strftime('%Y%m%d%H')}.json")
    series = []
    for raw in _rows(http_json(url)):
        row = _lower(raw)
        ymdhm = str(row.get("ymdhm") or "").strip()
        if not ymdhm:
            continue
        series.append({"t": ymdhm, "v": to_float(row.get("swl")),
                       "owl": to_float(row.get("owl")),
                       "inf": to_float(row.get("inf")),
                       "tototf": to_float(row.get("tototf"))})
    series.sort(key=lambda p: p["t"])
    return series


def fetch_bo_latest(key: str, code: str) -> dict | None:
    for raw in _rows(http_json(f"{BASE}/{key}/bo/list/10M/{code}.json")):
        row = _lower(raw)
        if not str(row.get("ymdhm") or "").strip():
            continue
        return {"t": str(row["ymdhm"]).strip(), "v": to_float(row.get("swl")),
                "owl": to_float(row.get("owl")), "inf": to_float(row.get("inf")),
                "tototf": to_float(row.get("tototf"))}
    return None


def collect_bo(key: str, cfg: dict) -> list[dict]:
    """세종 구간 보(洑). 제원의 좌표가 실제와 어긋나 있어 지도에는 설정 좌표만 쓴다."""
    include = cfg.get("bo_names") or ["세종보"]
    try:
        stations = [st for st in fetch_stations(key, "bo")
                    if any(name in st["name"] for name in include)]
    except CollectError:
        return []

    coords = cfg.get("bo_coords") or {}
    out = []
    for st in stations:
        entry = dict(st)
        pair = coords.get(st["name"]) or coords.get(st["code"])
        # 제원 좌표는 신뢰하지 않는다(세종보가 부여 근처로 찍힌다).
        entry["lat"], entry["lon"] = (None, None)
        if pair and len(pair) == 2:
            entry["lon"], entry["lat"] = float(pair[0]), float(pair[1])
        try:
            entry["series"] = fetch_bo_series(key, st["code"],
                                              hours=int(cfg.get("hours", 24)))
        except CollectError as exc:
            entry["series"], entry["error"] = [], str(exc)
        try:
            entry["latest"] = fetch_bo_latest(key, st["code"])
        except CollectError:
            entry["latest"] = next((p for p in reversed(entry["series"])
                                    if p.get("v") is not None), None)
        out.append(entry)
    return out


def _facility_series(key: str, code: str, hydro: str, hours: int,
                     value_field: str) -> list[dict]:
    end = now_kst()
    start = end - timedelta(hours=hours)
    url = (f"{BASE}/{key}/{hydro}/list/1H/{code}/"
           f"{start.strftime('%Y%m%d%H')}/{end.strftime('%Y%m%d%H')}.json")
    series = []
    for raw in _rows(http_json(url)):
        row = _lower(raw)
        ymdhm = str(row.get("ymdhm") or "").strip()
        if not ymdhm:
            continue
        series.append({"t": ymdhm, "v": to_float(row.get(value_field)),
                       "owl": to_float(row.get("owl")),
                       "inf": to_float(row.get("inf")),
                       "tototf": to_float(row.get("tototf"))})
    series.sort(key=lambda p: p["t"])
    return series


def _facility_latest(key: str, code: str, hydro: str, value_field: str):
    for raw in _rows(http_json(f"{BASE}/{key}/{hydro}/list/10M/{code}.json")):
        row = _lower(raw)
        if not str(row.get("ymdhm") or "").strip():
            continue
        return {"t": str(row["ymdhm"]).strip(), "v": to_float(row.get(value_field)),
                "owl": to_float(row.get("owl")), "inf": to_float(row.get("inf")),
                "tototf": to_float(row.get("tototf"))}
    return None


def collect_dam(key: str, cfg: dict) -> list[dict]:
    """금강 상류 댐. 방류가 늘면 몇 시간 뒤 세종 수위가 오르는 선행지표다."""
    include = cfg.get("dam_names") or ["대청댐"]
    try:
        stations = [st for st in fetch_stations(key, "dam")
                    if any(name in st["name"] for name in include)]
    except CollectError:
        return []

    out = []
    for st in stations:
        entry = dict(st)
        entry["lat"], entry["lon"] = None, None      # 세종 밖이라 지도에는 안 찍는다
        try:
            entry["series"] = _facility_series(key, st["code"], "dam",
                                               int(cfg.get("hours", 24)), "swl")
        except CollectError as exc:
            entry["series"], entry["error"] = [], str(exc)
        try:
            entry["latest"] = _facility_latest(key, st["code"], "dam", "swl")
        except CollectError:
            entry["latest"] = next((p for p in reversed(entry["series"])
                                    if p.get("v") is not None), None)
        # 방류량 추이는 수위보다 이 화면에서 더 중요하다
        entry["outflow"] = [{"t": p["t"], "v": p.get("tototf")} for p in entry["series"]]
        out.append(entry)
    return out


def fetch_flood_forecast(key: str) -> list[dict]:
    """홍수예보 발령현황. 평시에는 code 990(자료 없음)이 온다."""
    payload = http_json(f"{BASE}/{key}/fldfct/list.json")
    if isinstance(payload, dict) and str(payload.get("code", "")) == "990":
        return []
    out = []
    for raw in _rows(payload):
        row = _lower(raw)
        out.append({
            "kind": (row.get("kind") or row.get("fcttp") or "").strip(),
            "area": (row.get("wrnaranm") or row.get("obsnm") or "").strip(),
            "time": str(row.get("fctdt") or row.get("ymdhm") or "").strip(),
            "note": (row.get("etc") or row.get("cn") or "").strip(),
        })
    return out


def flood_stage(level, st: dict) -> tuple[str, str]:
    """현재 수위를 홍수 단계로 환산. (등급코드, 라벨)

    HRFCO 가 주는 기준수위는 관심·주의보·경보·심각 넷뿐이고, 그 아래 구간에는
    이름이 없다. '평상시' 같은 일상어를 붙이면 수문학 용어처럼 읽히므로
    사실만 적는다 — 관심수위에 못 미친다는 뜻의 '관심 이하'.
    (갈수위·저수위·평수위 같은 유황 구분은 이 API 로는 알 수 없다.)
    """
    if level is None:
        return "unknown", "자료없음"
    steps = [("srswl", "serious", "심각"), ("almwl", "alert", "경보"),
             ("wrnwl", "warning", "주의보"), ("attwl", "watch", "관심")]
    for field, code, label in steps:
        threshold = st.get(field)
        if threshold is not None and level >= threshold:
            return code, label
    return "normal", "관심 이하"


def collect(key: str, cfg: dict) -> dict:
    """세종 구간 수위·강수 관측소 현황을 한 덩어리로 반환."""
    include = cfg.get("address_keywords") or ["세종"]
    result = {"waterlevel": [], "rainfall": [], "bo": [], "dam": [],
              "forecast": [], "errors": []}

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

        # 상한에 걸려 잘리면 화면엔 멀쩡해 보이는데 주요 지점이 빠진다.
        # (예전 기본값 8 때문에 금남교·세종보 상/하가 통째로 누락됐다)
        limit = int(cfg.get("max_stations", 40))
        if len(stations) > limit:
            result["errors"].append(
                "%s: 조건에 맞는 관측소 %d곳 중 %d곳만 표시합니다(max_stations)."
                % (hydro, len(stations), limit))
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
                # 기간 조회는 양 끝을 모두 포함해 25개(=25시간)가 온다. 그대로 더하면
                # 24시간 누적이 아니라 25시간 누적이 된다. 창을 정확히 24시간으로 자른다.
                cutoff = (now_kst() - timedelta(hours=24)).strftime("%Y%m%d%H")
                window = [p for p in series
                          if p["v"] is not None and str(p["t"])[:10] > cutoff]
                entry["window"] = ([window[0]["t"], window[-1]["t"]]
                                   if window else None)
                entry["sum_24h"] = (round(sum(p["v"] for p in window), 1)
                                    if window else None)
            result[hydro].append(entry)

    result["surge_threshold"] = float(cfg.get("surge_threshold", 0.3))

    if cfg.get("bo", True):
        result["bo"] = collect_bo(key, cfg)
    if cfg.get("dam", True):
        result["dam"] = collect_dam(key, cfg)
    if cfg.get("forecast", True):
        try:
            result["forecast"] = fetch_flood_forecast(key)
        except CollectError as exc:
            result["errors"].append("홍수예보(선택): %s" % exc)

    return result
