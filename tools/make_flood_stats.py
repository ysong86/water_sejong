# -*- coding: utf-8 -*-
"""세종시 빈도별 침수면적 통계를 받아 assets/sejong_flood_stats.json 으로 굳힌다.

왜 수집기(collectors/)가 아니라 도구(tools/)인가
  홍수위험지도는 지도 자체를 다시 만들 때만 바뀐다(현재 자료 기준일 2025-06-13).
  10분마다 부를 이유가 없고, 부르면 공공 API 트래픽만 축낸다. 그래서 사람이
  가끔 손으로 돌리는 도구로 두고, 상황판은 결과 파일만 읽는다. 덕분에 이 패널은
  API 가 죽어 있어도 계속 보인다.

무엇을 받나 — 홍수위험지도 3종을 모두 받는다
  cty  도시침수지도        하수관망 용량 초과로 생기는 도심지 침수
  ntn  국가하천 하천범람지도  금강 본류 등 국가하천 제방 월류·붕괴
  rgn  지방하천 하천범람지도  시가 관리하는 지방하천
  각각 빈도(30~500년, 기왕최대)별로 침수심 5단계 면적(km2)을 준다.

요청주소를 왜 이걸 쓰나
  같은 자료가 공공데이터포털에도 있지만(apis.data.go.kr/1480964/InquireAdmCtyFLService_v2
  등) 지도 3종 x 지역구분 4종이 전부 별개 서비스라 쓰려면 활용신청을 따로
  해야 한다. 홍수위험지도 정보제공포털이 같은 자료를 인증 없이 공개하고 있어
  한 번 굳히는 용도로는 이쪽이 간단하다. 다만 **문서화된 주소가 아니다** —
  어느 날 이름이 바뀌면 이 도구만 실패하고, 상황판은 이미 굳어 있는 파일로
  계속 돈다. 그때 아래 SERVICES 의 주소만 고치면 된다.

  주소 규칙: /api/usr/inquire-{지역구분}-{지도종류}-fl-service/v2/get-list
    지역구분 nw 전국 / adm 행정구역 / rv 유역 / sa 권역

사용
  python tools/make_flood_stats.py            받아서 assets/ 에 저장
  python tools/make_flood_stats.py --print    저장하지 않고 화면에만
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from collectors.common import now_kst, stamp  # noqa: E402

BASE = ("https://data.floodmap.go.kr/api/usr/inquire-adm-%s-fl-service"
        "/v2/get-list?pageNo=1&numOfRows=5000&SG_APIM=temp")

# 세종특별자치시 = 법정동코드 36110 (시도 36, 시군구 110). 세종은 기초자치단체가
# 없는 단층제라 시군구 코드가 하나뿐이다 — 읍면동별로 쪼갠 값은 이 서비스에 없다.
CTPV, SGG = "36", "110"

SERVICES = [
    ("cty", "도시침수", "하수관망 용량을 넘는 집중호우로 생기는 도심지 침수"),
    ("ntn", "국가하천 범람", "금강 본류 등 국가하천의 제방 월류·붕괴"),
    ("rgn", "지방하천 범람", "세종시가 관리하는 지방하천의 범람"),
]

DEPTHS = [
    ("floodAreaDepthLe05", "0.5m 이하"),
    ("floodAreaDepth0510", "0.5~1.0m"),
    ("floodAreaDepth1020", "1.0~2.0m"),
    ("floodAreaDepth2050", "2.0~5.0m"),
    ("floodAreaDepthGt50", "5.0m 이상"),
]

FREQ_LABEL = {"030": "30년", "050": "50년", "080": "80년", "100": "100년",
              "200": "200년", "500": "500년", "max": "기왕최대"}


def fetch(slug: str) -> list:
    url = BASE % slug
    req = urllib.request.Request(url, headers={"User-Agent": "sejong-water-dashboard"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    items = (((body.get("response") or {}).get("body") or {})
             .get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def sejong_rows(items: list) -> list:
    """세종 행만 골라 빈도 오름차순으로. 면적이 전부 0 인 빈도는 버린다.

    0 은 '침수가 없다'가 아니라 '그 빈도 지도가 아직 없다'는 뜻이다(도시침수의
    기왕최대가 그렇다). 0.00 km2 로 찍어 두면 안전하다는 뜻으로 읽히므로 뺀다.
    """
    order = ["030", "050", "080", "100", "200", "500", "max"]
    out = []
    for row in items:
        if str(row.get("stdgCtpvCd")) != CTPV or str(row.get("stdgSggCd")) != SGG:
            continue
        areas = [round(float(row.get(key) or 0), 2) for key, _ in DEPTHS]
        if not any(areas):
            continue
        freq = str(row.get("fldlvFreq"))
        out.append({"freq": freq, "label": FREQ_LABEL.get(freq, freq),
                    "areas": areas, "total": round(sum(areas), 2)})
    out.sort(key=lambda r: order.index(r["freq"]) if r["freq"] in order else 99)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="세종시 빈도별 침수면적 통계 받기")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="저장하지 않고 화면에만 출력")
    args = parser.parse_args(argv)

    maps = []
    for slug, name, note in SERVICES:
        try:
            rows = sejong_rows(fetch(slug))
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            print("%s 실패 — %r" % (name, exc))
            return 1
        if not rows:
            print("%s — 세종 자료 없음(건너뜁니다)" % name)
            continue
        print("%s %d개 빈도, 최대 %.2f km2" % (name, len(rows), max(r["total"] for r in rows)))
        maps.append({"id": slug, "name": name, "note": note, "rows": rows})

    if not maps:
        print("받은 자료가 없습니다.")
        return 1

    payload = {
        "region": "세종특별자치시",
        "depths": [label for _, label in DEPTHS],
        "unit": "km2",
        "maps": maps,
        "fetched_at": stamp(now_kst()),
        "source": "환경부 한강홍수통제소 홍수위험지도 (행정구역별 빈도별 침수심 통계)",
        "license": "공공누리 제4유형 — 출처표시, 상업적 이용금지, 변경금지",
    }

    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.show:
        print(text)
        return 0
    path = os.path.join(ROOT, "assets", "sejong_flood_stats.json")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    print("저장 %s (%d KB)" % (path, len(text.encode("utf-8")) // 1024 + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
