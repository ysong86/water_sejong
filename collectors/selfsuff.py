# -*- coding: utf-8 -*-
"""세종시 자족도시 지표 — 통근·일자리로 자족성을 재는 다섯 지표.

왜 이걸 재나
  민선 9기 1호 공약이 '자족도시'인데, 자족을 무엇으로 재는지 합의된 수치가 없다.
  국외에는 계보가 있다. 영국 신도시(밀턴킨스 등) 평가에 쓰인 자족률
  (self-containment ratio)과 미국 도시계획의 직주균형(jobs-housing balance)이다.
  세종은 계획신도시라 이 계보에 그대로 들어맞는다.

왜 API 가 아니라 파일인가
  통근통학은 인구주택총조사 5년 주기, 종사자수는 전국사업체조사 연 1회다.
  10분마다 부를 것이 없다. 그런데 KOSIS 통계표 ID 는 표가 개편될 때마다 바뀌어,
  코드에 박아두면 어느 해엔가 조용히 틀린 값을 그린다. 이 상황판이 가장 피하려는
  실패다(README '읽을 때 주의' 참고). 그래서 숫자는 사람이 확인해 파일에 적고,
  적은 사람이 출처와 연도를 함께 남긴다. 화면에도 그 출처가 그대로 뜬다.

  data/selfsuff.json 이 없으면 이 블록은 '업데이트 예정'으로 비어 있을 뿐,
  나머지 상황판은 정상 동작한다. 서식은 data/selfsuff.example.json 참고.

입력 여섯 개 (연도별)
  population            상주인구 (거주지 기준)
  employed_residents    관내 거주 취업자
  jobs                  관내 일자리 = 종사자수 (근무지 기준)
  internal_commuters    관내 거주 · 관내 근무
  in_commuters          타지 거주 · 관내 근무 (유입)
  out_commuters         관내 거주 · 타지 근무 (유출)

  주간인구는 따로 받지 않고 상주인구 - 유출 + 유입 로 계산한다(통계청 정의).
  값은 숫자로 바로 쓰거나 {"value": 숫자, "source": "출처"} 로 적는다.
"""
from __future__ import annotations

from .common import load_json, to_float

DATA_FILE = "selfsuff.json"
EXAMPLE_FILE = "selfsuff.example.json"

# 입력 항목. 화면에서 '무엇이 비었는지' 알려줄 때 이 라벨을 쓴다.
INPUTS = [
    ("population", "상주인구"),
    ("employed_residents", "거주 취업자"),
    ("jobs", "관내 일자리(종사자)"),
    ("internal_commuters", "관내거주·관내근무"),
    ("in_commuters", "유입 통근"),
    ("out_commuters", "유출 통근"),
]

# 지표 정의. bands 는 (상한, 라벨, 색토큰) 을 위에서부터 훑어 처음 걸리는 것.
# 상한 None 은 '그 위 전부'. 구간 경계는 국외 관행에서 가져온 참고선이지
# 법정 기준이 아니다. 화면에도 그렇게 밝힌다.
INDICATORS = [
    {
        "id": "daytime",
        "label": "주간인구지수",
        "unit": "",
        "digits": 1,
        "formula": "주간인구 ÷ 상주인구 × 100",
        "desc": "낮에 사람이 들어오는 도시인가. 100을 넘으면 유입이 유출보다 많다.",
        "bands": [(90, "유출 우위", "--ss-weak"),
                  (100, "약한 유출", "--ss-fair"),
                  (110, "균형", "--ss-good"),
                  (None, "유입 중심", "--ss-strong")],
    },
    {
        "id": "resident_sc",
        "label": "통근 자립률",
        "unit": "%",
        "digits": 1,
        "formula": "관내거주·관내근무 ÷ 거주 취업자 × 100",
        "desc": "여기 사는 사람이 여기서 일하는가. 신도시 자족성의 표준 지표다.",
        "bands": [(50, "낮음", "--ss-weak"),
                  (70, "보통", "--ss-fair"),
                  (85, "높음", "--ss-good"),
                  (None, "매우 높음", "--ss-strong")],
    },
    {
        "id": "job_sc",
        "label": "일자리 자족률",
        "unit": "%",
        "digits": 1,
        "formula": "관내거주·관내근무 ÷ 관내 일자리 × 100",
        "desc": "여기 일자리를 여기 사람이 채우는가. 낮으면 외부 통근에 기댄다.",
        "bands": [(50, "낮음", "--ss-weak"),
                  (70, "보통", "--ss-fair"),
                  (85, "높음", "--ss-good"),
                  (None, "매우 높음", "--ss-strong")],
    },
    {
        "id": "jh_balance",
        "label": "직주균형",
        "unit": "",
        "digits": 2,
        "formula": "관내 일자리 ÷ 거주 취업자",
        "desc": "일자리와 사는 사람의 수가 맞는가. 1.0 근처가 균형이다.",
        "bands": [(0.8, "주거 우위", "--ss-weak"),
                  (1.2, "균형", "--ss-strong"),
                  (None, "고용 우위", "--ss-good")],
    },
    {
        "id": "job_density",
        "label": "일자리 밀도",
        "unit": "개",
        "digits": 1,
        "formula": "관내 일자리 ÷ 상주인구 × 100",
        "desc": "인구 100명당 일자리 수. 자족 기반의 두께를 본다.",
        "bands": [(30, "얇음", "--ss-weak"),
                  (45, "보통", "--ss-fair"),
                  (60, "두꺼움", "--ss-good"),
                  (None, "매우 두꺼움", "--ss-strong")],
    },
]

# --------------------------------------------------------------------------- 입력

def _value(raw):
    """숫자로 바로 적었든 {"value":..,"source":..} 로 적었든 (값, 출처) 로."""
    if isinstance(raw, dict):
        return to_float(raw.get("value")), (raw.get("source") or "").strip()
    return to_float(raw), ""


def _base(year_block: dict) -> tuple[dict, dict, list]:
    """한 연도 블록에서 입력 여섯 개를 뽑는다. 없는 것은 이름으로 돌려준다."""
    values, sources, missing = {}, {}, []
    for key, label in INPUTS:
        value, source = _value((year_block or {}).get(key))
        if value is None:
            missing.append(label)
            continue
        values[key] = value
        if source:
            sources[key] = source
    return values, sources, missing


# --------------------------------------------------------------------------- 계산

def daytime_population(base: dict):
    """주간인구 = 상주인구 - 유출통근 + 유입통근 (통계청 정의)."""
    need = ("population", "out_commuters", "in_commuters")
    if any(k not in base for k in need):
        return None
    return base["population"] - base["out_commuters"] + base["in_commuters"]


def indices(base: dict) -> dict:
    """입력 여섯 개에서 지표 다섯 개로. 재료가 빠진 지표는 None 으로 남긴다."""
    out = {spec["id"]: None for spec in INDICATORS}

    pop = base.get("population")
    emp = base.get("employed_residents")
    jobs = base.get("jobs")
    inner = base.get("internal_commuters")

    daytime = daytime_population(base)
    if daytime is not None and pop:
        out["daytime"] = daytime / pop * 100.0
    if inner is not None and emp:
        out["resident_sc"] = inner / emp * 100.0
    if inner is not None and jobs:
        out["job_sc"] = inner / jobs * 100.0
    if jobs is not None and emp:
        out["jh_balance"] = jobs / emp
    if jobs is not None and pop:
        out["job_density"] = jobs / pop * 100.0
    return out


def band(spec: dict, value) -> tuple[str, str]:
    """지표값이 어느 구간인가. (라벨, 색토큰)."""
    if value is None:
        return "—", "--st-unknown"
    for upper, label, color in spec["bands"]:
        if upper is None or value < upper:
            return label, color
    return spec["bands"][-1][1], spec["bands"][-1][2]


# --------------------------------------------------------------------------- 수집

def _years(block: dict) -> list[str]:
    """연도 키를 오래된 것부터. 숫자로 안 읽히는 키는 버린다."""
    out = []
    for key in (block or {}):
        try:
            out.append((int(key), str(key)))
        except (TypeError, ValueError):
            continue
    return [text for _, text in sorted(out)]


def _series(block: dict) -> tuple[list, str | None, dict, dict, list]:
    """한 지역의 연도별 지표 시계열과, 가장 최근 연도의 재료·출처·결손."""
    rows = []
    latest_year = None
    latest_base, latest_sources, latest_missing = {}, {}, [l for _, l in INPUTS]

    for year in _years(block):
        base, sources, missing = _base(block.get(year))
        computed = indices(base)
        if all(v is None for v in computed.values()):
            continue
        rows.append({"year": year, "values": computed,
                     "daytime_population": daytime_population(base),
                     "base": base})
        latest_year, latest_base = year, base
        latest_sources, latest_missing = sources, missing
    return rows, latest_year, latest_base, latest_sources, latest_missing


def _region(name: str, block: dict) -> dict:
    rows, year, base, sources, missing = _series(block)
    latest = rows[-1]["values"] if rows else {sp["id"]: None for sp in INDICATORS}
    return {
        "name": name,
        "year": year,
        "values": latest,
        "base": base,
        "sources": sources,
        "missing": missing,
        "daytime_population": rows[-1]["daytime_population"] if rows else None,
        "trend": [{"year": r["year"], "values": r["values"]} for r in rows],
    }


def collect(cfg: dict | None = None) -> dict:
    """data/selfsuff.json 을 읽어 지표로 바꾼다. 인증키가 필요 없다.

    파일이 없으면 errors 에 사유만 남기고 빈 결과를 돌려준다. 표본으로 화면을
    보고 싶으면 config 의 selfsuff.use_example 을 켠다(샘플 배지가 붙는다).
    """
    cfg = cfg or {}
    result = {"region": None, "year": None, "trend": [], "peers": [],
              "values": {}, "base": {}, "sources": {}, "missing": [],
              "sample": False, "errors": []}

    name = cfg.get("file") or DATA_FILE
    raw = load_json(name)
    if raw is None and cfg.get("use_example"):
        raw = load_json(EXAMPLE_FILE)
        result["sample"] = bool(raw)
    if raw is None:
        result["errors"].append(
            "data/%s 이 없습니다. data/%s 을 복사해 KOSIS 수치를 채우면 채워집니다."
            % (name, EXAMPLE_FILE))
        return result

    region_name = raw.get("region") or (cfg.get("region") or "세종특별자치시")
    main = _region(region_name, raw.get("years") or {})
    result.update({k: main[k] for k in
                   ("year", "values", "base", "sources", "missing",
                    "daytime_population", "trend")})
    result["region"] = region_name
    result["note"] = raw.get("note") or ""

    for peer_name, peer_block in (raw.get("peers") or {}).items():
        peer = _region(peer_name, peer_block)
        if peer["year"]:
            result["peers"].append(peer)

    if not main["year"]:
        result["errors"].append(
            "%s: 지표를 만들 재료가 없습니다. 빠진 항목 — %s"
            % (region_name, ", ".join(main["missing"]) or "전부"))
    elif main["missing"]:
        result["errors"].append(
            "%s %s년: %s 이(가) 비어 일부 지표를 못 냅니다."
            % (region_name, main["year"], ", ".join(main["missing"])))
    return result
