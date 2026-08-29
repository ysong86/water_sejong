# -*- coding: utf-8 -*-
"""국립환경과학원 수질 DB — 수질측정망 운영결과.

  End Point  https://apis.data.go.kr/1480523/WaterQualityService
  오퍼레이션  getWaterMeasuringList   (수질측정망 정기측정 결과)
             getRealTimeWaterQualityList (실시간 수질지수 — 항목이 M01.. 코드라 별도 해석 필요)

세종 지점은 코드를 박아두지 않는다. 응답의 ADDR 에 '세종'이 들어가는 지점을
연 단위로 훑어 코드를 찾아내고(discover), 그 뒤로는 ptNoList 로 한 번에 조회한다.
찾아낸 코드는 config 의 nier.point_codes 에 적어두면 탐색을 건너뛴다.

좌표는 응답이 도분초로 준다(LAT_DGR/MIN/SEC). 그대로 십진수로 바꿔 지도에 쓴다.
수동으로 point_coords 를 적을 필요가 없다.

주기 — 정기측정망이라 지점별 월 1~2회다. 실시간이 아니다.
"""
from __future__ import annotations

import urllib.parse

from .common import CollectError, http_json, now_kst, to_float

BASE = "https://apis.data.go.kr/1480523/WaterQualityService"
DEFAULT_ENDPOINT = BASE + "/getWaterMeasuringList"

# 화면에 쓰는 항목만 골라 매핑한다(응답에는 60개가 넘는 항목이 있다).
ITEMS = {
    "temp": "ITEM_TEMP", "ph": "ITEM_PH", "do": "ITEM_DOC",
    "bod": "ITEM_BOD", "cod": "ITEM_COD", "toc": "ITEM_TOC",
    "ss": "ITEM_SS", "tn": "ITEM_TN", "tp": "ITEM_TP",
    "cond": "ITEM_EC", "chla": "ITEM_CLOA", "ecoli": "ITEM_ECOLI",
}

# 하천 생활환경기준(환경정책기본법 시행령) TOC 기준 등급 상한(mg/L)
TOC_GRADES = [(2.0, "Ia", "매우 좋음"), (3.0, "Ib", "좋음"), (4.0, "II", "약간 좋음"),
              (5.0, "III", "보통"), (6.0, "IV", "약간 나쁨"), (8.0, "V", "나쁨")]


def _service_key(key: str) -> str:
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _rows(payload) -> list:
    """{"getWaterMeasuringList": {"item": [...]}} 형태에서 레코드를 꺼낸다."""
    if not isinstance(payload, dict):
        return []
    for value in payload.values():
        if isinstance(value, dict):
            items = value.get("item")
            if isinstance(items, list):
                return items
            if isinstance(items, dict):
                return [items]
    return []


def _dms(row: dict, prefix: str):
    """도·분·초 세 칸을 십진 좌표로. 하나라도 비면 None."""
    parts = [to_float(row.get("%s_%s" % (prefix, unit)))
             for unit in ("DGR", "MIN", "SEC")]
    if parts[0] is None:
        return None
    return round(parts[0] + (parts[1] or 0) / 60.0 + (parts[2] or 0) / 3600.0, 6)


def grade_from_toc(toc):
    if toc is None:
        return None, None
    for limit, code, label in TOC_GRADES:
        if toc <= limit:
            return code, label
    return "VI", "매우 나쁨"


def _normalize(row: dict) -> dict:
    entry = {
        "code": (row.get("PT_NO") or "").strip() or None,
        "name": (row.get("PT_NM") or "").strip() or "(이름없음)",
        "addr": (row.get("ADDR") or "").strip(),
        # 2026.06.29 → 20260629. 화면의 시각 표기·신선도 계산이 이 형식을 읽는다.
        "time": str(row.get("WMCYMD") or "").replace(".", "").replace("-", "").strip(),
        "depth": to_float(row.get("WMDEP")),
        "lat": _dms(row, "LAT"),
        "lon": _dms(row, "LON"),
        "org": (row.get("ORG_NM") or "").strip(),
    }
    for field, source in ITEMS.items():
        entry[field] = to_float(row.get(source))
    entry["grade"], entry["grade_label"] = grade_from_toc(entry.get("toc"))
    return entry


def query(endpoint: str, key: str, params: dict, timeout: int = 45) -> list[dict]:
    merged = {"serviceKey": _service_key(key), "pageNo": 1, "numOfRows": 500,
              "resultType": "json"}
    merged.update(params)
    return [_normalize(r) for r in _rows(http_json(endpoint, merged, timeout=timeout))]


def probe_one(url: str, key: str, cfg: dict) -> dict:
    try:
        rows = query(url, key, {"numOfRows": 5}, timeout=25)
        return {"url": url, "ok": bool(rows), "count": len(rows),
                "sample": rows[0] if rows else None, "error": None}
    except CollectError as exc:
        return {"url": url, "ok": False, "count": 0, "sample": None, "error": str(exc)}


def probe(key: str, cfg: dict) -> list[dict]:
    urls = [cfg["endpoint"]] if cfg.get("endpoint") else []
    urls += [DEFAULT_ENDPOINT, BASE + "/getRealTimeWaterQualityList"]
    seen, report = set(), []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        report.append(probe_one(url, key, cfg))
    return report


def discover(endpoint: str, key: str, cfg: dict, year: str) -> list[str]:
    """해당 연도 전국 자료를 훑어 주소가 맞는 지점 코드를 찾아낸다.

    지점 목록 API 가 따로 없어 측정결과에서 역으로 뽑는다. 한 해 4만 건 남짓이라
    5천 건씩 몇 장이면 끝나고, 찾은 코드는 설정에 적어두면 다시 하지 않는다.
    """
    keywords = cfg.get("address_keywords") or ["세종"]
    codes: dict[str, None] = {}
    for page in range(1, 12):
        rows = query(endpoint, key,
                     {"pageNo": page, "numOfRows": 5000, "wmyrList": year}, timeout=90)
        for row in rows:
            if any(kw in row["addr"] for kw in keywords) and row["code"]:
                codes[row["code"]] = None
        if len(rows) < 5000:
            break
    return sorted(codes)


def collect(key: str, cfg: dict) -> dict:
    result = {"points": [], "endpoint": None, "errors": [], "discovered": False}
    endpoint = cfg.get("endpoint") or DEFAULT_ENDPOINT
    result["endpoint"] = endpoint

    year = str(cfg.get("year") or now_kst().year)
    codes = [str(c).strip() for c in (cfg.get("point_codes") or []) if str(c).strip()]

    if not codes:
        try:
            codes = discover(endpoint, key, cfg, year)
            result["discovered"] = True
        except CollectError as exc:
            result["errors"].append("지점 탐색 실패: %s" % exc)
            return result
        if not codes:
            result["errors"].append(
                "'%s' 주소의 측정지점을 %s년 자료에서 찾지 못했습니다."
                % ("/".join(cfg.get("address_keywords") or ["세종"]), year))
            return result
        result["codes"] = codes          # 설정에 적어두라고 화면·로그에 남긴다

    rows = []
    for attempt_year in (year, str(int(year) - 1)):
        try:
            rows = query(endpoint, key, {"ptNoList": ",".join(codes),
                                         "wmyrList": attempt_year, "numOfRows": 2000})
        except CollectError as exc:
            result["errors"].append("%s년 조회 실패: %s" % (attempt_year, exc))
            return result
        if rows:
            result["year"] = attempt_year
            break

    if not rows:
        result["errors"].append("지점 %d개에 대해 최근 2개 연도 자료가 비어 있습니다."
                                % len(codes))
        return result

    # 지점별 최신 1건만 남긴다(정기측정이라 한 해에 여러 건이 온다).
    latest: dict[str, dict] = {}
    for row in rows:
        code = row["code"]
        if not code:
            continue
        if code not in latest or str(row["time"]) > str(latest[code]["time"]):
            latest[code] = row

    coords = cfg.get("point_coords") or {}
    points = []
    for code in codes:
        point = latest.get(code)
        if not point:
            continue
        override = coords.get(point["name"]) or coords.get(code)
        if override and len(override) == 2:
            point["lon"], point["lat"] = float(override[0]), float(override[1])
        points.append(point)

    points.sort(key=lambda p: p["name"])
    result["points"] = points
    return result
