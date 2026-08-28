# -*- coding: utf-8 -*-
"""국립환경과학원 물환경정보시스템 — 실시간 수질(수질자동측정망).

공공데이터포털의 상세 명세가 로그인 뒤에 있어 오퍼레이션명을 코드에 못박지 않았다.
config.json 의 `endpoint` 를 먼저 쓰고, 비어 있으면 CANDIDATES 를 차례로 타진한다.
`python run.py --probe` 가 발급받은 키로 어떤 후보가 응답하는지 찾아 config 에 적어준다.
"""
from __future__ import annotations

import urllib.parse

from .common import CollectError, http_json, to_float

# 타진 순서 — 위쪽이 실시간 자동측정망일 가능성이 높은 후보.
CANDIDATES = [
    "https://apis.data.go.kr/1480523/WaterQualityService/getRealTimeWaterQualityList",
    "https://apis.data.go.kr/1480523/RealTimeWaterQualityService/getRealTimeWaterQualityList",
    "https://apis.data.go.kr/1480523/WaterQualityService/getAutoWaterMeasuringList",
    "https://apis.data.go.kr/1480523/WaterQualityService/getWaterMeasuringList",
    "https://apis.data.go.kr/1480523/WaterQualityService/getTmsMeasuringList",
]

# 응답 필드명이 문서마다 다르다. 별칭을 넓게 잡고 첫 매칭을 쓴다.
FIELD_ALIASES = {
    "temp":  ("ITEM_TEMP", "WTRTMP", "TEMP", "ITEM_WTRTMP"),
    "ph":    ("ITEM_PH", "PH"),
    "do":    ("ITEM_DOC", "ITEM_DO", "DOC", "DO"),
    "toc":   ("ITEM_TOC", "TOC"),
    "bod":   ("ITEM_BOD", "BOD"),
    "cond":  ("ITEM_EC", "EC", "ITEM_COND", "COND"),
    "tn":    ("ITEM_TN", "TN"),
    "tp":    ("ITEM_TP", "TP"),
    "ss":    ("ITEM_SS", "SS"),
    "chla":  ("ITEM_CLOA", "CHLA", "ITEM_CHLA"),
}
NAME_ALIASES = ("PT_NM", "PTNM", "SITE_NM", "MSRSTE_NM", "OBSNM", "PT_NO_NM")
CODE_ALIASES = ("PT_NO", "PTNO", "SITE_ID", "MSRSTE_CODE", "PTNOLIST")
TIME_ALIASES = ("WMCYMD", "MSR_DATE", "WMYR", "OCCRRNC_DE", "MSRDT", "WMOD")

# 하천 생활환경기준(환경정책기본법 시행령) TOC 기준 등급 상한(mg/L)
TOC_GRADES = [(2.0, "Ia", "매우 좋음"), (3.0, "Ib", "좋음"), (4.0, "II", "약간 좋음"),
              (5.0, "III", "보통"), (6.0, "IV", "약간 나쁨"), (8.0, "V", "나쁨")]


def _service_key(key: str) -> str:
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _items(payload) -> list:
    """NIER 응답의 레코드 목록. 래퍼가 여러 형태라 재귀로 훑는다."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("item", "items", "list", "row"):
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
        if isinstance(value, dict):
            inner = _items(value)
            if inner:
                return inner
    for key in ("response", "body", "getRealTimeWaterQualityList", "result", "data"):
        if isinstance(payload.get(key), (dict, list)):
            inner = _items(payload[key])
            if inner:
                return inner
    for value in payload.values():
        if isinstance(value, (dict, list)):
            inner = _items(value)
            if inner:
                return inner
    return []


def _pick(row: dict, aliases) -> str | None:
    upper = {str(k).upper(): v for k, v in row.items()}
    for alias in aliases:
        if alias in upper and str(upper[alias]).strip() not in ("", "-"):
            return str(upper[alias]).strip()
    return None


def grade_from_toc(toc):
    if toc is None:
        return None, None
    for limit, code, label in TOC_GRADES:
        if toc <= limit:
            return code, label
    return "VI", "매우 나쁨"


def _normalize(row: dict) -> dict:
    entry = {
        "code": _pick(row, CODE_ALIASES),
        "name": _pick(row, NAME_ALIASES) or "(이름없음)",
        "time": _pick(row, TIME_ALIASES),
    }
    for field, aliases in FIELD_ALIASES.items():
        entry[field] = to_float(_pick(row, aliases))
    entry["grade"], entry["grade_label"] = grade_from_toc(entry.get("toc"))
    return entry


def query(endpoint: str, key: str, cfg: dict, num_rows: int = 100) -> list[dict]:
    params = {
        "serviceKey": _service_key(key),
        "pageNo": 1,
        "numOfRows": num_rows,
        "resultType": "json",
        "_returnType": "json",
    }
    points = [p for p in (cfg.get("point_codes") or []) if str(p).strip()]
    if points:
        params["ptNoList"] = ",".join(str(p) for p in points)
    params.update(cfg.get("extra_params") or {})
    payload = http_json(endpoint, params)
    return [_normalize(r) for r in _items(payload)]


def probe_one(url: str, key: str, cfg: dict) -> dict:
    """주소 하나만 시험한다. 포털에서 '요청주소' 를 복사해 넣었을 때 쓴다."""
    try:
        rows = query(url, key, cfg, num_rows=5)
        return {"url": url, "ok": bool(rows), "count": len(rows),
                "sample": rows[0] if rows else None, "error": None}
    except CollectError as exc:
        return {"url": url, "ok": False, "count": 0, "sample": None, "error": str(exc)}


def probe(key: str, cfg: dict) -> list[dict]:
    """후보 엔드포인트를 순서대로 호출해 결과를 보고한다(설정 자동 확정용)."""
    report = []
    for url in CANDIDATES:
        try:
            rows = query(url, key, cfg, num_rows=5)
            report.append({"url": url, "ok": bool(rows), "count": len(rows),
                           "sample": rows[0] if rows else None, "error": None})
        except CollectError as exc:
            report.append({"url": url, "ok": False, "count": 0,
                           "sample": None, "error": str(exc)})
    return report


def collect(key: str, cfg: dict) -> dict:
    result = {"points": [], "endpoint": None, "errors": []}
    endpoints = [cfg["endpoint"]] if cfg.get("endpoint") else list(CANDIDATES)

    for url in endpoints:
        try:
            rows = query(url, key, cfg)
        except CollectError as exc:
            result["errors"].append(f"{url.rsplit('/', 1)[-1]}: {exc}")
            continue
        if not rows:
            result["errors"].append(f"{url.rsplit('/', 1)[-1]}: 레코드 0건")
            continue
        result["endpoint"] = url
        result["errors"] = []          # 성공했으면 앞선 후보의 실패는 잡음이다
        break
    else:
        return result

    keywords = cfg.get("name_keywords") or ["세종", "금강", "미호", "대청"]
    if keywords:
        points = [r for r in rows if any(k in (r["name"] or "") for k in keywords)]
        if not points:
            # 예전엔 여기서 전국 지점을 그대로 내보냈다. 세종 상황판에 다른 유역 값이
            # 섞이면 화면은 멀쩡해 보이는데 내용이 틀리므로, 비우고 이유를 남긴다.
            result["endpoint"] = url
            result["received"] = len(rows)
            result["errors"].append(
                "'%s' 에 해당하는 지점이 응답 %d건 중 없습니다. "
                "nier.name_keywords 또는 point_codes 를 확인해주세요."
                % ("/".join(keywords), len(rows)))
            return result
    else:
        points = rows

    # 실시간 수질 API 는 좌표를 주지 않는 경우가 많다. 설정의 point_coords 로 보완한다.
    coords = cfg.get("point_coords") or {}
    for point in points:
        pair = coords.get(point.get("name")) or coords.get(point.get("code") or "")
        if pair and len(pair) == 2:
            point["lon"], point["lat"] = float(pair[0]), float(pair[1])

    result["points"] = points
    result["filtered"] = bool(keywords)
    result["received"] = len(rows)
    return result
