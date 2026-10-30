# -*- coding: utf-8 -*-
"""세종시 불투수면적률을 계산해 assets/sejong_impervious.json 으로 굳힌다.

무엇을 계산하나
  불투수면적률(%) = 불투수 항목 면적 합 / 총면적 x 100
  어느 항목이 불투수인지는 우리가 정하지 않는다. 별표 '불투수면적률 및
  물순환율 산정방법'의 세분류 41개 항목 구분을 그대로 쓴다
  (assets/impervious_table.json). 연구마다 기준이 달라지는 값이라
  출처가 분명한 표를 쓰는 것이 핵심이다.

왜 도구(tools/)인가
  연 1회 갱신되는 통계다. 10분 주기 수집기에 넣을 이유가 없다.
  침수면적 통계와 같은 방식으로, 한 번 받아 파일로 굳히고 상황판은 그 파일만 읽는다.

읍면동으로 못 쪼갠다
  이 통계 서비스는 시도·시군구까지만 준다. 세종은 단층제라 '세종시' 한 줄이
  전부다. 읍면동별 불투수 지도를 그리려면 세분류 토지피복지도(shapefile)를
  EGIS 에서 받아 행정동 경계와 교차시켜야 한다 — 이 도구로는 안 된다.

인증키
  config.json 의 landcover.key, 없으면 nier.key 를 쓴다. 환경변수
  LANDCOVER_KEY 가 있으면 그쪽이 우선이다. 포털 활용신청이 서비스마다
  별건이라 다른 서비스에 쓰던 키가 여기서는 안 먹을 수 있다.

사용
  python tools/make_impervious.py            받아서 assets/ 에 저장
  python tools/make_impervious.py --print    저장하지 않고 화면에만
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from collectors.common import load_config, now_kst, stamp  # noqa: E402

UDDI = "uddi:2f139805-fd32-4be1-9f21-bb6cab9a2333"
BASE = "https://api.odcloud.kr/api/15164500/v1/" + UDDI

# 통계 서비스의 칸 이름 -> 토지피복 세분류 코드.
# 왼쪽은 응답에 실제로 오는 철자 그대로다. '경기정리가 안된 밭'은 제공처의
# 오타지만 고치지 않는다 — 고치면 그 칸을 못 찾아 면적이 통째로 빠진다.
FIELD_TO_CODE = {
    "단독주거시설": 111, "공동주거시설": 112, "공업시설": 121, "상업 업무시설": 131,
    "혼합지역": 132, "문화 체육 휴양시설": 141, "공항": 151, "항만": 152,
    "철도": 153, "도로": 154, "기타 교통 통신시설": 155, "환경기초시설": 161,
    "교육 행정시설": 162, "기타 공공시설": 163, "경지정리가 된 논": 211,
    "경지정리가 안 된 논": 212, "경지정리가 된 밭": 221, "경기정리가 안된 밭": 222,
    "시설재배지": 231, "과수원": 241, "목장 양식장": 251, "기타재배지": 252,
    "활엽수림": 311, "침엽수림": 321, "혼효림": 331, "자연초지": 411,
    "골프장": 421, "묘지": 422, "기타 초지": 423, "내륙습지(수변식생)": 511,
    "갯벌": 521, "염전": 522, "해변": 611, "강기슭": 612, "암벽 바위": 613,
    "채광지역": 621, "운동장": 622, "기타 나지": 623, "하천": 711,
    "호소": 712, "해양수": 721,
}


# 환경공간정보 WMS 서버는 WFS 도 열어 둔다. 거기 '불투수면 비율' 레이어가
# 기관이 직접 산정한 값이라, 우리가 세분류 통계로 계산한 값과 맞춰볼 수 있다.
# adm_code 는 법정동코드가 아니라 자체 코드라(세종=29010) 코드로 찾지 않고
# 세종 중심점을 덮는 시군구를 공간질의로 찾는다.
OFFICIAL_WFS = ("https://api.mcee.go.kr/geoserver/me/wfs?service=WFS&version=2.0.0"
                "&request=GetFeature&typeNames=me:wa_plg_impermeable"
                "&outputFormat=application/json"
                "&propertyName=adm_code,adm_type,ratio,adm_area,imperm_area")
SEJONG_LONLAT = (127.25, 36.52)


def _mercator(lon: float, lat: float) -> tuple:
    import math
    return (lon * 20037508.34 / 180.0,
            math.log(math.tan((90 + lat) * math.pi / 360.0))
            / (math.pi / 180.0) * 20037508.34 / 180.0)


def fetch_official() -> dict:
    """기관이 산정한 불투수면 비율. 못 받으면 빈 dict — 대조만 빠진다."""
    x, y = _mercator(*SEJONG_LONLAT)
    # 전국을 한 번에 받는다(269행뿐이다). 세종 값만 받으면 순위·평균을
    # 2024년 통계 쪽에서 가져와야 해서, 큰 숫자와 그 옆 비교값의 연도가 어긋난다.
    try:
        req = urllib.request.Request(OFFICIAL_WFS + "&count=400",
                                     headers={"User-Agent": "sejong-water-dashboard"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError):
        return {}

    sgg, mine = [], None
    for feature in body.get("features") or []:
        prop = feature.get("properties") or {}
        if prop.get("adm_type") != "SGG":
            continue
        area = float(prop.get("adm_area") or 0)
        ratio = float(prop.get("ratio") or 0)
        sgg.append(ratio)
        # 세종은 면적으로 집어낸다. adm_code 가 법정동코드가 아니라 자체 코드라
        # (세종=29010) 코드로 찾으면 다른 시도를 집는다.
        if 440 < area < 490 and abs(area - 465.2) < 1.0:
            mine = (ratio, float(prop.get("imperm_area") or 0), area)
    if not mine or not sgg:
        return {}
    return {"ratio": round(mine[0], 2), "area": round(mine[1], 2),
            "total": round(mine[2], 2), "year": 2025,
            "count": len(sgg), "rank": sum(1 for v in sgg if v > mine[0]) + 1,
            "average": round(sum(sgg) / len(sgg), 2),
            "layer": "me:wa_plg_impermeable"}


def load_table() -> tuple:
    path = os.path.join(ROOT, "assets", "impervious_table.json")
    with open(path, encoding="utf-8") as fp:
        table = json.load(fp)
    names = {c["code"]: c["name"] for c in table["classes"]}
    impervious = {c["code"] for c in table["classes"] if c["impervious"]}
    if set(FIELD_TO_CODE.values()) != set(names):
        raise SystemExit("분류 대응이 어긋납니다 — 통계 칸과 별표 항목을 맞추세요.")
    return names, impervious, table


def fetch(key: str) -> list:
    rows, page = [], 1
    while page <= 20:
        url = "%s?page=%d&perPage=300&serviceKey=%s" % (BASE, page, key)
        req = urllib.request.Request(url, headers={"User-Agent": "sejong-water-dashboard"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        rows.extend(body.get("data") or [])
        if len(rows) >= (body.get("totalCount") or 0):
            break
        page += 1
    return rows


def split(row: dict, impervious: set) -> tuple:
    """(불투수면적, 총면적, 항목별 불투수면적). 단위 km2."""
    imp = total = 0.0
    parts = {}
    for field, code in FIELD_TO_CODE.items():
        area = float(row.get(field) or 0)
        total += area
        if code in impervious and area > 0:
            imp += area
            # 면적 합에는 넣되 내역 목록에서는 뺀다. 0.00 km2 로 찍히면
            # '공항이 있는데 면적이 0' 으로 읽힌다.
            if area >= 0.005:
                parts[code] = round(area, 3)
    return imp, total, parts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="세종시 불투수면적률 계산")
    parser.add_argument("--print", dest="show", action="store_true")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    names, impervious, table = load_table()
    cfg = load_config(args.config)
    key = (os.environ.get("LANDCOVER_KEY")
           or (cfg.get("landcover") or {}).get("key")
           or (cfg.get("nier") or {}).get("key") or "").strip()
    if not key:
        print("인증키가 없습니다. config.json 에 landcover.key 를 넣어주세요.")
        return 1
    if "%" not in key:                   # 디코딩된 키라면 다시 인코딩해서 보낸다
        key = urllib.parse.quote(key, safe="")

    try:
        rows = fetch(key)
    except urllib.error.HTTPError as exc:
        print("받기 실패 HTTP %s — 이 서비스에 활용신청이 돼 있는지 확인하세요." % exc.code)
        return 1
    except urllib.error.URLError as exc:
        print("받기 실패: %s" % exc.reason)
        return 1
    print("전국 %d행 받음" % len(rows))

    years = sorted({r.get("기준시기") for r in rows if r.get("기준시기")})
    series, parts, total_area, stamp_day = [], {}, 0.0, None
    for year in years:
        row = next((r for r in rows
                    if r.get("기준시기") == year and "세종" in str(r.get("시도"))), None)
        if not row:
            continue
        imp, total, part = split(row, impervious)
        series.append({"year": year, "ratio": round(imp / total * 100, 2),
                       "impervious": round(imp, 2), "total": round(total, 2)})
        parts, total_area = part, total
        stamp_day = row.get("데이터기준일") or stamp_day
    if not series:
        print("세종 자료가 없습니다.")
        return 1

    latest = years[-1]
    peers = []
    for row in rows:
        if row.get("기준시기") != latest:
            continue
        imp, total, _ = split(row, impervious)
        if total > 0:
            peers.append((imp / total * 100, row))
    peers.sort(key=lambda x: -x[0])
    mine = series[-1]["ratio"]
    # 순위는 반올림 전 값으로 센다. 12.73% 로 반올림한 값끼리 비교하면
    # 소수점 셋째 자리에서 갈리는 이웃 시군구와 순위가 뒤집힌다.
    exact = next(v for v, r in peers if "세종" in str(r.get("시도")))
    rank = sum(1 for v, _ in peers if v > exact) + 1

    payload = {
        "region": "세종특별자치시",
        "unit": "km2",
        "latest_year": latest,
        "ratio": mine,
        "total_area": round(total_area, 2),
        "impervious_area": series[-1]["impervious"],
        "series": series,
        "parts": [{"code": c, "name": names[c], "area": a}
                  for c, a in sorted(parts.items(), key=lambda kv: -kv[1])],
        "peers": {"count": len(peers), "rank": rank,
                  "average": round(sum(v for v, _ in peers) / len(peers), 2)},
        "basis": table["_source_status"],
        "formula": table["formula"],
        "official": fetch_official(),
        # 바로 아래에 읍면동 표가 붙으므로 '나눌 수 없다'고 쓰면 앞뒤가 안 맞는다.
        # 나눌 수 없는 것은 '기관이 공표하는 값'이지 자료 자체가 아니다.
        "granularity": ("여기까지는 기관이 공표한 값입니다. 공표 단위가 시군구까지라 "
                        "세종은 단층제인 시 전체 한 값입니다."),
        "source": "기후에너지환경부 환경공간정보 지역별 세분류 토지피복 면적 현황",
        "data_date": stamp_day,
        "fetched_at": stamp(now_kst()),
    }

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.show:
        print(text)
        return 0
    path = os.path.join(ROOT, "assets", "sejong_impervious.json")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    print("세종 %s년 %.2f%% (전국 %d개 중 %d위, 평균 %.2f%%) → %s"
          % (latest, mine, len(peers), rank, payload["peers"]["average"], path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
