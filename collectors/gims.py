# -*- coding: utf-8 -*-
"""지하수 관측망 — 국가지하수관측망 + 지자체(보조)관측망.

두 갈래로 열려 있고, 어느 쪽이든 인증키가 필요하다.
  * 공공데이터포털 — 한국수자원공사_국가지하수측정자료조회서비스(시간자료) 등
      https://www.data.go.kr/data/15114511/openapi.do   (측정망번호·지하수위·수온·심도·EC)
      https://www.data.go.kr/data/15114508/openapi.do   (조사시설 조회)
  * 국가지하수정보센터(GIMS) 자체 오픈API
      https://www.gims.go.kr/opnApiList.do  → 신청 후 key 발급
      형식: http://www.gims.go.kr/{URI}?type=json&key={인증키}&{파라미터}

상세 명세가 로그인 뒤에 있어 오퍼레이션명을 코드에 못박지 않았다.
`config.json` 의 `gims.data_endpoint` 에 포털 상세페이지의 요청주소를 그대로 넣는 것이
가장 확실하고, 비워두면 CANDIDATES 를 타진한다(`run.py --probe`).

관측소 목록은 API 로 못 받는 경우가 많아 `gims.stations` 에 직접 적을 수 있게 했다.
지도에 찍으려면 어차피 좌표가 필요하다.
"""
from __future__ import annotations

import urllib.parse
from datetime import timedelta

from .common import CollectError, http_json, now_kst, to_float

# 타진 후보. 앞쪽이 공공데이터포털(한국수자원공사), 뒤쪽이 GIMS 자체 API.
CANDIDATES = [
    "https://apis.data.go.kr/B500001/GroundWaterMeasureInfoService/getGroundWaterMeasureInfo",
    "https://apis.data.go.kr/B500001/nationalGroundWaterMeasureInfo/getNationalGroundWaterMeasureInfo",
    "https://apis.data.go.kr/B500001/gwLevelService/getGwLevelList",
    "http://www.gims.go.kr/api/natnObsvHourData",
    "http://www.gims.go.kr/api/assiObsvHourData",
]

FIELD_ALIASES = {
    # 지하수위 — 표고(EL.m)로 주는 API 와 지표하 심도(GL.m)로 주는 API 가 섞여 있다.
    "level": ("ELEV", "ELE", "WTL", "GW_LEVEL", "GWLEVEL", "LEV", "WATER_LEVEL",
              "GRWTLV", "WL"),
    "depth": ("DEPTH", "DPT", "GL", "WATER_DEPTH", "DEPTHM"),
    "temp":  ("TEMP", "WTEMP", "WATER_TEMP", "TP", "WTRTMP"),
    "cond":  ("EC", "COND", "ELCTCDCT", "CONDUCTIVITY"),
}
NAME_ALIASES = ("OBSNM", "OBS_NM", "STNM", "STATION_NM", "GENNM", "NAME", "MSRSTE_NM")
CODE_ALIASES = ("GENNUM", "GENNO", "OBSCD", "OBS_CD", "STCD", "CODE", "NUM", "MSRSTE_CODE")
TIME_ALIASES = ("YMDHM", "OBSDT", "OBS_DATE", "MSRDT", "YMD", "DATE", "CHKDT", "OCCRRNC_DE")


def _service_key(key: str) -> str:
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _items(payload) -> list:
    """포털/GIMS 응답 어느 쪽이든 레코드 목록을 찾아낸다."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("item", "items", "list", "row", "data", "result", "body", "response"):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
        if isinstance(value, (dict, list)):
            inner = _items(value)
            if inner:
                return inner
    for value in payload.values():
        if isinstance(value, (dict, list)):
            inner = _items(value)
            if inner:
                return inner
    return []


def _pick(row: dict, aliases):
    upper = {str(k).upper(): v for k, v in row.items()}
    for alias in aliases:
        if alias in upper and str(upper[alias]).strip() not in ("", "-", "null"):
            return str(upper[alias]).strip()
    return None


def _normalize(row: dict) -> dict:
    entry = {
        "code": _pick(row, CODE_ALIASES),
        "name": _pick(row, NAME_ALIASES),
        "time": _pick(row, TIME_ALIASES),
    }
    for field, aliases in FIELD_ALIASES.items():
        entry[field] = to_float(_pick(row, aliases))
    return entry


def query(endpoint: str, key: str, cfg: dict, station: dict | None = None,
          num_rows: int = 200) -> list[dict]:
    """한 관측소(또는 전체)의 시간자료. 파라미터 이름은 설정으로 바꿀 수 있다."""
    end = now_kst()
    start = end - timedelta(hours=int(cfg.get("hours", 24)))
    names = {**{"code": "gennum", "begin": "begindate", "end": "enddate"},
             **(cfg.get("param_names") or {})}

    params = {
        "serviceKey" if "data.go.kr" in endpoint else "key": _service_key(key),
        "numOfRows": num_rows,
        "pageNo": 1,
        "dataType": "JSON",
        "type": "json",
        names["begin"]: start.strftime("%Y%m%d"),
        names["end"]: end.strftime("%Y%m%d"),
    }
    if station and station.get("code"):
        params[names["code"]] = station["code"]
    params.update(cfg.get("extra_params") or {})

    payload = http_json(endpoint, params)
    return [_normalize(r) for r in _items(payload)]


def probe_one(url: str, key: str, cfg: dict) -> dict:
    """주소 하나만 시험한다. 포털에서 '요청주소' 를 복사해 넣었을 때 쓴다."""
    stations = cfg.get("stations") or []
    try:
        rows = query(url, key, cfg, stations[0] if stations else None, num_rows=5)
        return {"url": url, "ok": bool(rows), "count": len(rows),
                "sample": rows[0] if rows else None, "error": None}
    except CollectError as exc:
        return {"url": url, "ok": False, "count": 0, "sample": None, "error": str(exc)}


def probe(key: str, cfg: dict) -> list[dict]:
    endpoints = list(CANDIDATES)
    if cfg.get("data_endpoint"):
        endpoints.insert(0, cfg["data_endpoint"])
    stations = cfg.get("stations") or []
    sample_station = stations[0] if stations else None

    report = []
    for url in endpoints:
        try:
            rows = query(url, key, cfg, sample_station, num_rows=5)
            report.append({"url": url, "ok": bool(rows), "count": len(rows),
                           "sample": rows[0] if rows else None, "error": None})
        except CollectError as exc:
            report.append({"url": url, "ok": False, "count": 0,
                           "sample": None, "error": str(exc)})
    return report


NETWORK_LABEL = {"national": "국가", "local": "지자체"}


def collect(key: str, cfg: dict) -> dict:
    result = {"stations": [], "endpoint": None, "errors": []}
    stations = cfg.get("stations") or []
    if not stations:
        result["errors"].append(
            "gims.stations 가 비어 있습니다. 관측소를 "
            '{"name","code","lat","lon","network"} 형태로 넣어주세요 '
            "(국가지하수정보센터 www.gims.go.kr 에서 세종 관측소 목록·좌표 확인).")
        return result

    endpoints = [cfg["data_endpoint"]] if cfg.get("data_endpoint") else list(CANDIDATES)

    # 첫 관측소로 살아 있는 엔드포인트를 하나 고른 뒤, 나머지는 그 주소로만 조회한다.
    chosen, first_rows = None, []
    for url in endpoints:
        try:
            rows = query(url, key, cfg, stations[0])
        except CollectError as exc:
            result["errors"].append("%s: %s" % (url.rsplit("/", 1)[-1], exc))
            continue
        if rows:
            chosen, first_rows = url, rows
            result["errors"] = []
            break
        result["errors"].append("%s: 레코드 0건" % url.rsplit("/", 1)[-1])
    if not chosen:
        return result

    result["endpoint"] = chosen
    for index, station in enumerate(stations):
        entry = {
            "code": station.get("code"),
            "name": station.get("name") or "(이름없음)",
            "network": station.get("network", "national"),
            "network_label": NETWORK_LABEL.get(station.get("network", "national"), "기타"),
            "lat": station.get("lat"), "lon": station.get("lon"),
            "addr": station.get("addr", ""),
        }
        try:
            rows = first_rows if index == 0 else query(chosen, key, cfg, station)
        except CollectError as exc:
            entry.update(latest=None, series=[], error=str(exc))
            result["stations"].append(entry)
            continue

        rows = [r for r in rows if r.get("level") is not None or r.get("depth") is not None]
        rows.sort(key=lambda r: str(r.get("time") or ""))
        series = [{"t": r.get("time"), "v": r.get("level")
                   if r.get("level") is not None else r.get("depth")} for r in rows]
        latest_row = rows[-1] if rows else None

        entry["series"] = series
        entry["latest"] = series[-1] if series else None
        entry["temp"] = latest_row.get("temp") if latest_row else None
        entry["cond"] = latest_row.get("cond") if latest_row else None
        entry["depth"] = latest_row.get("depth") if latest_row else None
        values = [p["v"] for p in series if p["v"] is not None]
        entry["delta_24h"] = (round(values[-1] - values[0], 3)
                              if len(values) >= 2 else None)
        result["stations"].append(entry)

    return result
