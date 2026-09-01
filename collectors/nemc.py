# -*- coding: utf-8 -*-
"""국립중앙의료원 응급의료정보 — '지금 세종에서 문 연 곳'.

  End Point  https://apis.data.go.kr/B552657
    ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire   응급실 실시간 가용병상
    ErmctInfoInqireService/getEgytListInfoInqire               응급의료기관 목록(좌표)
    ErmctInsttInfoInqireService/getParmacyListInfoInqire       약국 목록(진료시간)
    HsptlAsembySearchService/getHsptlMdcncListInfoInqire       병의원 목록(진료시간)

  활용신청  https://www.data.go.kr/data/15000563/openapi.do (응급의료기관 정보)
            https://www.data.go.kr/data/15000576/openapi.do (병의원·약국 정보)
            인증키는 포털 계정 공통이라 nier/gims 와 같은 값을 써도 된다.

왜 이 화면인가
  민선 9기 공약에 '세종 365-24 안심의료체계'가 있는데, 정작 365일 24시간 무엇이
  열려 있는지 보여주는 화면이 없다. 응급실 가용병상은 이 상황판에서 유일하게
  분 단위로 바뀌는 의료 자료다.

XML 이다
  B552657 계열은 기본이 XML 이고 서비스마다 json 지원이 들쭉날쭉하다. 응답을
  받아 보고 JSON 이면 JSON 으로, 아니면 ElementTree 로 읽는다. 둘 다 표준
  라이브러리다.

병상 필드
  hvec(응급실 일반 가용)/hvs01(기준) 짝만 확실하다. 나머지는 가용 수만 쓰고
  비율을 만들지 않는다. 기준 병상 필드명을 확인했다면 config 의 nemc.beds 에
  [가용필드, 기준필드, 라벨] 로 적으면 그때부터 비율이 나온다. --probe 가
  응답 한 건을 통째로 보여주므로 거기서 필드명을 확인하면 된다.
"""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

from .common import CollectError, http_get, now_kst, to_float

BASE = "https://apis.data.go.kr/B552657"

ER_BEDS = "%s/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire" % BASE
ER_LIST = "%s/ErmctInfoInqireService/getEgytListInfoInqire" % BASE
PHARMACY = "%s/ErmctInsttInfoInqireService/getParmacyListInfoInqire" % BASE
CLINIC = "%s/HsptlAsembySearchService/getHsptlMdcncListInfoInqire" % BASE

# (가용 필드, 기준 필드 또는 None, 라벨). 기준이 None 이면 가용 수만 보여준다.
DEFAULT_BEDS = [
    ["hvec", "hvs01", "응급실 일반"],
    ["hvgc", None, "입원실"],
    ["hvoc", None, "수술실"],
    ["hvicc", None, "일반 중환자"],
    ["hvncc", None, "신생아 중환자"],
]

# 응급실 혼잡도 — 가용/기준. 기준 병상을 모르면 색을 칠하지 않는다.
def bed_state(free, total):
    if free is None:
        return "unknown", "자료 없음"
    if not total:
        return "count", "가용 %d" % int(free)
    if free <= 0:
        return "full", "만실"
    ratio = free / total
    if ratio < 0.15:
        return "tight", "혼잡"
    if ratio < 0.4:
        return "fair", "보통"
    return "free", "여유"


def _service_key(key: str) -> str:
    key = key.strip()
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _rows(text: str) -> list[dict]:
    """JSON 이든 XML 이든 item 목록을 dict 리스트로."""
    stripped = (text or "").lstrip()
    if not stripped:
        raise CollectError("응답이 비어 있습니다.")

    if stripped[0] in "{[":
        import json
        payload = json.loads(stripped)
        node = payload
        for step in ("response", "body", "items"):
            if isinstance(node, dict) and step in node:
                node = node[step]
        if isinstance(node, dict):
            node = node.get("item", [])
        if isinstance(node, dict):
            return [node]
        return node if isinstance(node, list) else []

    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        raise CollectError("XML 파싱 실패: %s" % exc) from exc

    # 오류는 <cmmMsgHeader><returnAuthMsg>… 로 온다. 사람이 읽을 수 있게 옮긴다.
    reason = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg")
    if reason:
        detail = root.findtext(".//returnReasonCode") or ""
        raise CollectError("%s%s" % (reason, " (%s)" % detail if detail else ""))

    rows = []
    for item in root.iter("item"):
        row = {}
        for child in item:
            row[child.tag] = (child.text or "").strip()
        if row:
            rows.append(row)
    return rows


def query(url: str, key: str, params: dict, cfg: dict | None = None) -> list[dict]:
    merged = {"serviceKey": _service_key(key), "pageNo": 1,
              "numOfRows": int((cfg or {}).get("num_rows", 300))}
    merged.update(params)
    return _rows(http_get(url, merged, timeout=30))


# --------------------------------------------------------------------------- 진료시간

_WEEKDAY_FIELD = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7}   # 월=1 … 일=7, 공휴일=8


def _hhmm(text) -> int | None:
    """'0900' → 540(분). 자릿수가 모자라거나 숫자가 아니면 None."""
    digits = "".join(c for c in str(text or "") if c.isdigit())
    if len(digits) not in (3, 4):
        return None
    value = int(digits)
    hour, minute = divmod(value, 100)
    if minute > 59 or hour > 47:
        return None
    return hour * 60 + minute


def open_now(row: dict, when=None) -> bool | None:
    """지금 문을 열었나. 요일 진료시간이 없으면 None(모름)."""
    when = when or now_kst()
    slot = _WEEKDAY_FIELD[when.weekday()]
    start = _hhmm(row.get("dutyTime%ds" % slot))
    close = _hhmm(row.get("dutyTime%dc" % slot))
    if start is None or close is None:
        return None
    minutes = when.hour * 60 + when.minute
    if close <= start:                  # 자정을 넘겨 여는 곳
        return minutes >= start or minutes < (close % (24 * 60))
    return start <= minutes < close


def _place(row: dict, opened) -> dict:
    return {
        "name": row.get("dutyName") or "—",
        "addr": row.get("dutyAddr") or "",
        "tel": row.get("dutyTel1") or "",
        "lon": to_float(row.get("wgs84Lon")),
        "lat": to_float(row.get("wgs84Lat")),
        "open": opened,
    }


def _open_list(rows: list[dict], when=None) -> dict:
    """목록을 '지금 열림/닫힘/모름'으로 갈라 센다."""
    places = [_place(r, open_now(r, when)) for r in rows]
    opened = [p for p in places if p["open"] is True]
    unknown = [p for p in places if p["open"] is None]
    opened.sort(key=lambda p: p["name"])
    return {"total": len(places), "open": len(opened),
            "unknown": len(unknown), "places": opened}


# --------------------------------------------------------------------------- 응급실

def _er(rows: list[dict], listing: dict, beds_spec) -> list[dict]:
    out = []
    for row in rows:
        beds = []
        for spec in beds_spec:
            free_field, total_field, label = (list(spec) + [None, None, None])[:3]
            free = to_float(row.get(free_field))
            total = to_float(row.get(total_field)) if total_field else None
            if free is None and total is None:
                continue
            state, note = bed_state(free, total)
            beds.append({"label": label or free_field, "free": free,
                         "total": total, "state": state, "note": note})
        hpid = row.get("hpid") or ""
        info = listing.get(hpid) or {}
        out.append({
            "hpid": hpid,
            "name": row.get("dutyName") or info.get("dutyName") or "—",
            "tel": row.get("dutyTel3") or info.get("dutyTel1") or "",
            "addr": info.get("dutyAddr") or "",
            "lon": to_float(info.get("wgs84Lon")),
            "lat": to_float(info.get("wgs84Lat")),
            "observed": row.get("hvidate") or "",
            "beds": beds,
        })
    out.sort(key=lambda e: e["name"])
    return out


# --------------------------------------------------------------------------- 수집

def collect(key: str, cfg: dict) -> dict:
    """응급실 가용병상 + 지금 문 연 약국·소아과. 한 곳이 죽어도 나머지는 살린다."""
    sido = cfg.get("sido") or "세종특별자치시"
    sigungu = (cfg.get("sigungu") or "").strip()
    beds_spec = cfg.get("beds") or DEFAULT_BEDS
    when = now_kst()

    result = {"sido": sido, "er": [], "pharmacy": None, "pediatric": None,
              "observed": None, "errors": []}

    # 1) 응급의료기관 목록 — 좌표·주소를 붙이려고 먼저 받는다. 실패해도 진행한다.
    listing = {}
    try:
        params = {"Q0": sido}
        if sigungu:
            params["Q1"] = sigungu
        for row in query(ER_LIST, key, params, cfg):
            if row.get("hpid"):
                listing[row["hpid"]] = row
    except CollectError as exc:
        result["errors"].append("응급의료기관 목록: %s" % exc)

    # 2) 응급실 실시간 가용병상 — 이 화면의 핵심. 여기가 죽으면 남는 게 없다.
    try:
        params = {"STAGE1": sido}
        if sigungu:
            params["STAGE2"] = sigungu
        rows = query(ER_BEDS, key, params, cfg)
        result["er"] = _er(rows, listing, beds_spec)
        stamps = [e["observed"] for e in result["er"] if e.get("observed")]
        result["observed"] = max(stamps) if stamps else None
    except CollectError as exc:
        result["errors"].append("응급실 가용병상: %s" % exc)

    # 3) 약국 — '지금 문 연 곳'의 체감이 가장 큰 항목
    if cfg.get("pharmacy", True):
        try:
            params = {"Q0": sido}
            if sigungu:
                params["Q1"] = sigungu
            result["pharmacy"] = _open_list(query(PHARMACY, key, params, cfg), when)
        except CollectError as exc:
            result["errors"].append("약국: %s" % exc)

    # 4) 소아청소년과 — 출산율은 전국 최상위인데 소아 필수의료는 얇다.
    #    진료과목 필드명이 서비스마다 달라 문자열 어디든 걸리면 그 과로 본다.
    keywords = cfg.get("clinic_keywords") or ["소아"]
    if keywords:
        try:
            params = {"Q0": sido}
            if sigungu:
                params["Q1"] = sigungu
            rows = [r for r in query(CLINIC, key, params, cfg)
                    if any(k in str(v) for v in r.values() if isinstance(v, str)
                           for k in keywords)]
            picked = _open_list(rows, when)
            picked["keywords"] = keywords
            result["pediatric"] = picked
        except CollectError as exc:
            result["errors"].append("병의원(%s): %s" % ("·".join(keywords), exc))

    if not result["er"] and not result["errors"]:
        result["errors"].append(
            "%s 응급실 실시간 자료가 0건입니다. 시도명을 확인하십시오"
            "(nemc.sido, 기본 '세종특별자치시')." % sido)
    return result


# --------------------------------------------------------------------------- 타진

PROBE_URLS = [ER_BEDS, ER_LIST, PHARMACY, CLINIC]


def probe_one(url: str, key: str, cfg: dict) -> dict:
    sido = cfg.get("sido") or "세종특별자치시"
    params = {"STAGE1": sido} if "Sckbd" in url else {"Q0": sido}
    try:
        rows = query(url, key, params, cfg)
        return {"url": url, "ok": bool(rows), "count": len(rows),
                "sample": rows[0] if rows else None, "error": None}
    except CollectError as exc:
        return {"url": url, "ok": False, "count": 0, "sample": None, "error": str(exc)}


def probe(key: str, cfg: dict) -> list[dict]:
    return [probe_one(url, key, cfg) for url in PROBE_URLS]
