# -*- coding: utf-8 -*-
"""국립환경과학원 시군구 통계 서비스 — 세종시 수질오염원 현황.

  End Point  https://apis.data.go.kr/1480523/SigunguStatService
  확인된 오퍼레이션 (2026-08-29 타진)
    getSigunguLvlhPpnSttusInfo   생활계 인구현황   -> 403(실재, 활용신청 필요)
    getSigunguLandInfo           토지계 토지이용   -> 403(실재, 활용신청 필요)
    그 밖의 이름 추측은 전부 400 이었다. 나머지 항목(축산·산업·양식·매립 등)은
    포털 상세기능 목록에서 이름을 확인해 config 의 operations 에 추가하면 된다.

  활용신청  https://www.data.go.kr/data/15058984/openapi.do

왜 하천별이 아니라 시군구인가
  하천별 통계(RivrStatService)는 응답에 시도·시군구 필드가 없고 하천명으로 거르면
  전국 동명 하천이 섞인다(금강만 코드 3개, 구룡천 8개). 시군구는 '세종'으로 딱
  떨어져 모호함이 없다. 대신 하천 단위가 아니라 시 단위 집계다.

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

# 확신할 수 있는 항목만 이름을 붙인다. 뜻이 애매한 필드에 한글 라벨을 붙이면
# 숫자는 맞는데 설명이 틀리는, 가장 나쁜 오류가 된다.
FIELD_LABELS = {
    "life": [("POPUSUMCNT", "총 인구", "명", 0)],
    "land": [
        ("LANDUTILZAREASUM", "총 토지이용면적", "㎡", 0),
        ("LANDUTILZAREAPADDIES", "논", "㎡", 0),
        ("LANDUTILZAREAFIELDS", "밭", "㎡", 0),
        ("LANDUTILZAREAORCHARD", "과수원", "㎡", 0),
    ],
}


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
            for field, label, unit, digits in FIELD_LABELS.get(spec.get("id"), []):
                value = to_float(picked.get(field))
                if value is not None:
                    block["rows"].append({"label": label, "value": value,
                                          "unit": unit, "digits": digits})
            if not block["rows"]:
                block["error"] = ("%s: 응답은 왔으나 아는 항목이 없습니다. "
                                  "필드명이 바뀌었을 수 있습니다." % block["label"])
            result["year"] = result["year"] or used_year
        result["blocks"].append(block)

    for block in result["blocks"]:
        if block.get("error"):
            result["errors"].append(block["error"])
    return result
