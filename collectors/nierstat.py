# -*- coding: utf-8 -*-
"""국립환경과학원 시군구 통계 서비스 — 세종시 수질오염원 현황.

  End Point  https://apis.data.go.kr/1480523/SigunguStatService
  확인된 오퍼레이션 (2026-08-29 타진)
    getSigunguLvlhPpnSttusInfo   생활계 인구현황   -> 403(실재, 활용신청 필요)
    getSigunguLandInfo           토지계 토지이용   -> 403(실재, 활용신청 필요)
    그 밖의 이름 추측은 전부 400 이었다. 나머지 항목(축산·산업·양식·매립 등)은
    포털 상세기능 목록에서 이름을 확인해 config 의 operations 에 추가하면 된다.

  활용신청  https://www.data.go.kr/data/15058984/openapi.do

왜 하천별(RivrStatService)이 아니라 시군구인가 — 다시 시도하지 말 것

  하천별에는 애초에 '세종 구간' 이라는 항목이 없다. 같은 하천 이름에 코드가
  여러 개인 것은 집계 단위가 달라서가 아니라 **하천등급이 달라서**다.

  표준하천코드 7자리 = ①② 권역·수계 + ③ 하천등급 + ④~⑦ 하천 일련번호
    30 = 금강권역·금강수계
    0 / 1 / 2 = 국가하천 / (구)지방1급 / (구)지방2급
  그래서 미호천이 3001810(38만)·3011810(6.5만)·3021810(10.4만)로 갈린다.
  각각 국가하천 구간과 지방하천으로 남은 상류 구간(음성·진천 발원부)이며,
  셋 중 무엇도 세종 구간이 아니다. 어느 것을 골라도 오답이다.
  (2008년 하천법 개정으로 지방1·2급 구분은 폐지됐지만 코드는 그대로 남아 있다.)

  시군구 통계는 '세종특별자치시' 한 행으로 딱 떨어져 모호함이 없다. 대신 하천
  단위가 아니라 시 단위 집계다. 하천별을 병기하면 등급별 구간 인구가 지역 인구처럼
  읽힐 위험만 생긴다.

연 단위 통계다. 상황판의 실시간 값과 성격이 다르므로 화면에서도 기준연도를 밝힌다.
"""
from __future__ import annotations

import urllib.parse

from .common import CollectError, http_json, now_kst, to_float

BASE = "https://apis.data.go.kr/1480523/SigunguStatService"

DEFAULT_OPERATIONS = [
    {"id": "life", "label": "생활계 인구", "op": "getSigunguLvlhPpnSttusInfo"},
    {"id": "land", "label": "토지이용", "op": "getSigunguLandInfo"},
]

# 필드 뜻은 합계 검증으로 확정했다.
#   CHY* 합 328,613 + VICHY* 합 63,698 = POPUSUMCNT 392,311  → CHY=처리구역 내, VICHY=밖
#   LANDUTILZAREA* 합계 464,962,316㎡ = 464.96㎢ → 세종시 실제 면적과 일치
# 뜻이 확실하지 않은 하위 항목(합류식·정화조 구분 등)은 일부러 쓰지 않는다.
SEWER_IN = ("CHYSEWERSYSPOPU", "CHYCONFLUPOPU", "CHYDIRTYWNOTPOPU",
            "CHYWATTANKNOTPOPU", "CHYREMOVNOTPOPU")
SEWER_OUT = ("VICHYSEWERSYSPOPU", "VICHYCONFLUPOPU", "VICHYDIRTYWNOTPOPU",
             "VICHYWATTANKNOTPOPU", "VICHYREMOVNOTPOPU")

# 물환경에서 의미가 큰 지목 위주로. 나머지는 '기타'로 묶는다.
LAND_PARTS = [
    ("LANDUTILZAREAFORESTLAND", "임야"),
    ("LANDUTILZAREAPADDIES", "논"),
    ("LANDUTILZAREAFIELDS", "밭"),
    ("LANDUTILZAREAEARTH", "대지"),
    ("LANDUTILZAREARIVER", "하천"),
    ("LANDUTILZAREAROAD", "도로"),
    ("LANDUTILZAREAORCHARD", "과수원"),
]

def _service_key(key: str) -> str:
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _rows(payload) -> list:
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


def _matches(row: dict, keyword: str) -> bool:
    """시군구 필드명을 모르므로 문자열 값 어디든 지역명이 들어 있으면 그 지역으로 본다."""
    return any(keyword in str(v) for v in row.values() if isinstance(v, str))


def query(op: str, key: str, cfg: dict, year: str) -> list[dict]:
    params = {
        "serviceKey": _service_key(key),
        "pageNo": 1,
        "numOfRows": int(cfg.get("num_rows", 500)),
        "resultType": "json",
        "startYear": year,
        "endYear": year,
    }
    params.update(cfg.get("extra_params") or {})
    return _rows(http_json("%s/%s" % (BASE, op), params, timeout=40))


def probe_one(url: str, key: str, cfg: dict) -> dict:
    op = url.rsplit("/", 1)[-1]
    try:
        rows = query(op, key, cfg, str(cfg.get("year") or now_kst().year - 1))
        return {"url": url, "ok": bool(rows), "count": len(rows),
                "sample": rows[0] if rows else None, "error": None}
    except CollectError as exc:
        return {"url": url, "ok": False, "count": 0, "sample": None, "error": str(exc)}


def probe(key: str, cfg: dict) -> list[dict]:
    ops = cfg.get("operations") or DEFAULT_OPERATIONS
    return [probe_one("%s/%s" % (BASE, spec["op"]), key, cfg) for spec in ops]


def _sum(row: dict, fields) -> float:
    return sum(to_float(row.get(f)) or 0.0 for f in fields)


def _summarize(kind: str, row: dict) -> list:
    """항목별로 화면에 바로 쓸 수 있게 정리한다. 단위 환산과 비율까지 여기서."""
    if kind == "life":
        total = to_float(row.get("POPUSUMCNT"))
        inside, outside = _sum(row, SEWER_IN), _sum(row, SEWER_OUT)
        if not total:
            return []
        return [
            {"label": "총 인구", "value": total, "unit": "명", "digits": 0},
            {"label": "하수처리구역 내", "value": inside, "unit": "명", "digits": 0,
             "share": round(inside / total * 100, 1)},
            {"label": "하수처리구역 밖", "value": outside, "unit": "명", "digits": 0,
             "share": round(outside / total * 100, 1)},
        ]

    if kind == "land":
        total = to_float(row.get("LANDUTILZAREASUM"))
        if not total:
            return []
        rows = [{"label": "총 면적", "value": total / 1e6, "unit": "㎢", "digits": 1}]
        named = 0.0
        for field, label in LAND_PARTS:
            value = to_float(row.get(field)) or 0.0
            named += value
            rows.append({"label": label, "value": value / 1e6, "unit": "㎢",
                         "digits": 1, "share": round(value / total * 100, 1)})
        etc = max(0.0, total - named)
        rows.append({"label": "기타", "value": etc / 1e6, "unit": "㎢", "digits": 1,
                     "share": round(etc / total * 100, 1)})
        return rows
    return []


def collect(key: str, cfg: dict) -> dict:
    """세종시 한 행만 뽑아 항목별로 정리한다."""
    result = {"blocks": [], "year": None, "errors": [], "region": None}
    keyword = cfg.get("region_keyword") or "세종"
    result["region"] = keyword

    # 통계는 확정까지 1~2년 걸린다. 지정 연도부터 3년 거슬러 올라가며 찾는다.
    base_year = int(cfg.get("year") or (now_kst().year - 1))
    years = [str(base_year - offset) for offset in range(0, 3)]

    for spec in (cfg.get("operations") or DEFAULT_OPERATIONS):
        block = {"id": spec.get("id"), "label": spec.get("label") or spec["op"],
                 "op": spec["op"], "rows": [], "year": None}
        picked, used_year = None, None
        for year in years:
            try:
                rows = query(spec["op"], key, cfg, year)
            except CollectError as exc:
                block["error"] = str(exc)
                break
            hit = next((r for r in rows if _matches(r, keyword)), None)
            if hit:
                picked, used_year = hit, year
                break
        if picked is None and "error" not in block:
            block["error"] = "%s: '%s' 자료를 최근 %d개 연도에서 찾지 못했습니다." % (
                block["label"], keyword, len(years))

        if picked:
            block["year"] = used_year
            block["rows"] = _summarize(spec.get("id"), picked)
            if not block["rows"]:
                block["error"] = ("%s: 응답은 왔으나 아는 항목이 없습니다. "
                                  "필드명이 바뀌었을 수 있습니다." % block["label"])
            result["year"] = result["year"] or used_year
        result["blocks"].append(block)

    for block in result["blocks"]:
        if block.get("error"):
            result["errors"].append(block["error"])
    return result
