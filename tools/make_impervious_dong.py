# -*- coding: utf-8 -*-
"""읍면동별 불투수면적을 직접 산정해 assets/sejong_impervious_dong.json 으로 굳힌다.

이 값은 공식 통계가 아니다 — 꼭 그렇게 표기할 것
  기관이 내놓는 불투수면적률은 시군구까지다. 세종은 단층제라 '세종시 12.99%'
  한 줄이 전부이고, 읍면동으로 나눈 공식 숫자는 존재하지 않는다. 그래서 원본
  지도에서 직접 계산한다. 방법은 기관과 같게 맞췄지만 결과는 우리 산정값이다.

어떻게 계산하나
  1. 환경공간정보 WFS 에서 세분류 토지피복 폴리곤을 받는다(EGIS:lv3_2025y).
     불투수로 분류된 16개 항목만 CQL 로 걸러 받는다.
  2. 행정동 경계(assets/sejong_admin.json)와 겹치는 면적을 shapely 로 구한다.
  3. 읍면동별로 불투수면적 / 전체면적 을 낸다.

  투수/불투수 구분은 우리가 정하지 않았다. assets/impervious_table.json
  (별표 '불투수면적률 및 물순환율 산정방법')을 그대로 쓴다.

좌표를 왜 이렇게 다루나
  WFS 는 EPSG:3857(웹 메르카토르)로 준다. 그대로 면적을 재면 위도 36.5도에서
  1.55배 부풀려진다. EPSG:4326 으로 달라고 하면 축 순서가 서버마다 달라 조용히
  뒤집힐 수 있다. 그래서 3857 을 받아 우리가 경위도로 되돌린 뒤, 세종만 한
  넓이에서 오차가 무시할 만한 국소 평면으로 옮긴다. 이 평면으로 잰 행정동
  합계가 463.45km2, 기관 공표값이 465.22km2 로 0.4% 차이다.

격자로 나눠 받는 이유
  WFS 페이징(startIndex)은 정렬 기준이 없으면 같은 것을 두 번 주거나 빠뜨린다.
  대신 범위를 격자로 쪼개 각각 통째로 받는다. 격자 경계에 걸친 폴리곤은 양쪽에
  다 나오므로 feature id 로 중복을 거른다.

사용
  python tools/make_impervious_dong.py              전부 받아 계산
  python tools/make_impervious_dong.py --grid 6     처음 격자를 6x6 으로
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from shapely.geometry import Polygon
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from collectors.common import now_kst, stamp  # noqa: E402

WFS = ("https://api.mcee.go.kr/geoserver/wfs?service=WFS&version=2.0.0"
       "&request=GetFeature&typeNames=EGIS:lv3_2025y")
LAYER_YEAR = 2025
R = 20037508.34                      # 웹 메르카토르 반바퀴(m)
TILE_LIMIT = 40000                   # 한 격자에서 이보다 많으면 더 쪼갠다


def merc(lon, lat):
    return (lon * R / 180.0,
            math.log(math.tan((90 + lat) * math.pi / 360.0))
            / (math.pi / 180.0) * R / 180.0)


def load_dongs():
    with open(os.path.join(ROOT, "assets", "sejong_admin.json"), encoding="utf-8") as fp:
        admin = json.load(fp)
    dongs = admin.get("dongs") or []
    if not dongs:
        raise SystemExit("행정동 경계가 없습니다.")
    pts = [p for g in dongs for ring in g["rings"] for p in ring]
    lon0 = min(p[0] for p in pts)
    lat0 = min(p[1] for p in pts)
    latm = sum(p[1] for p in pts) / len(pts)
    kx = math.cos(math.radians(latm)) * 111320.0
    ky = 110574.0

    shapes = []
    for g in dongs:
        rings = [[((p[0] - lon0) * kx, (p[1] - lat0) * ky) for p in ring]
                 for ring in g["rings"]]
        poly = Polygon(rings[0], rings[1:] if len(rings) > 1 else None)
        if not poly.is_valid:
            poly = poly.buffer(0)
        shapes.append({"name": g["name"], "poly": poly, "area": poly.area / 1e6})
    return shapes, (lon0, lat0, kx, ky)


def fetch(cql, retries=3):
    url = (WFS + "&outputFormat=application/json&srsName=EPSG:3857"
           # geom 을 빼면 형상이 null 로 온다. 코드만 받아도 면적은 못 낸다.
           "&count=1000000&propertyName=l3_code,geom&CQL_FILTER="
           + urllib.parse.quote(cql))
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sejong-water-dashboard"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            if attempt == retries - 1:
                raise
            print("    재시도 %d (%r)" % (attempt + 1, exc), flush=True)
            time.sleep(4)
    return None


def hits(cql):
    url = WFS + "&resultType=hits&CQL_FILTER=" + urllib.parse.quote(cql)
    req = urllib.request.Request(url, headers={"User-Agent": "sejong-water-dashboard"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        text = resp.read().decode("utf-8", "replace")
    m = re.search(r'numberMatched="(\d+)"', text)
    return int(m.group(1)) if m else 0


def plan_tiles(x0, y0, x1, y1, codes, n):
    """격자를 만들되, 한 칸이 너무 빽빽하면 4등분해서 다시 센다."""
    todo = []
    for i in range(n):
        for j in range(n):
            todo.append((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * j / n,
                         x0 + (x1 - x0) * (i + 1) / n, y0 + (y1 - y0) * (j + 1) / n))
    out = []
    while todo:
        a, b, c, d = todo.pop()
        cql = "BBOX(geom,%f,%f,%f,%f) AND l3_code IN (%s)" % (a, b, c, d, codes)
        here = hits(cql)
        if here == 0:
            continue
        if here > TILE_LIMIT:
            mx, my = (a + c) / 2, (b + d) / 2
            todo += [(a, b, mx, my), (mx, b, c, my), (a, my, mx, d), (mx, my, c, d)]
            continue
        out.append(((a, b, c, d), here))
    return out


def to_local(ring, lon0, lat0, kx, ky):
    out = []
    for x, y in ring:
        lon = x * 180.0 / R
        lat = math.degrees(2 * math.atan(math.exp(y * math.pi / R)) - math.pi / 2)
        out.append(((lon - lon0) * kx, (lat - lat0) * ky))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="읍면동별 불투수면적 산정")
    parser.add_argument("--grid", type=int, default=5, help="처음 격자 분할 수")
    args = parser.parse_args(argv)

    with open(os.path.join(ROOT, "assets", "impervious_table.json"), encoding="utf-8") as fp:
        table = json.load(fp)
    names = {c["code"]: c["name"] for c in table["classes"]}
    imp_codes = sorted(c["code"] for c in table["classes"] if c["impervious"])
    codes_sql = ",".join("'%d'" % c for c in imp_codes)

    shapes, (lon0, lat0, kx, ky) = load_dongs()
    tree = STRtree([s["poly"] for s in shapes])
    total_area = sum(s["area"] for s in shapes)
    print("행정동 %d개, 합계 %.2f km2" % (len(shapes), total_area), flush=True)

    xs = [p[0] for s in shapes for p in s["poly"].exterior.coords]
    ys = [p[1] for s in shapes for p in s["poly"].exterior.coords]
    lon_min = lon0 + min(xs) / kx - 0.01
    lon_max = lon0 + max(xs) / kx + 0.01
    lat_min = lat0 + min(ys) / ky - 0.01
    lat_max = lat0 + max(ys) / ky + 0.01
    x0, y0 = merc(lon_min, lat_min)
    x1, y1 = merc(lon_max, lat_max)

    print("격자 나누는 중...", flush=True)
    grid = plan_tiles(x0, y0, x1, y1, codes_sql, args.grid)
    print("격자 %d칸, 폴리곤 %d개(중복 포함)"
          % (len(grid), sum(n for _, n in grid)), flush=True)

    acc = [dict() for _ in shapes]
    seen = set()
    for step, ((a, b, c, d), here) in enumerate(grid, 1):
        cql = "BBOX(geom,%f,%f,%f,%f) AND l3_code IN (%s)" % (a, b, c, d, codes_sql)
        body = fetch(cql)
        for f in (body.get("features") or []):
            fid = f.get("id")
            if fid in seen:
                continue
            seen.add(fid)
            code = int((f.get("properties") or {}).get("l3_code") or 0)
            geom = f.get("geometry") or {}
            if geom.get("type") == "Polygon":
                parts = [geom["coordinates"]]
            elif geom.get("type") == "MultiPolygon":
                parts = geom["coordinates"]
            else:
                continue
            for rings in parts:
                local = [to_local(r, lon0, lat0, kx, ky) for r in rings]
                try:
                    poly = Polygon(local[0], local[1:] if len(local) > 1 else None)
                except (ValueError, TypeError):
                    continue
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
                for idx in tree.query(poly):
                    part = poly.intersection(shapes[idx]["poly"])
                    if not part.is_empty:
                        acc[idx][code] = acc[idx].get(code, 0.0) + part.area
        print("  %d/%d 칸 · 누적 %d개" % (step, len(grid), len(seen)), flush=True)

    rows = []
    grand = 0.0
    for s, bycode in zip(shapes, acc):
        imp = sum(bycode.values()) / 1e6
        grand += imp
        rows.append({
            "name": s["name"],
            "area": round(s["area"], 3),
            "impervious": round(imp, 3),
            "ratio": round(imp / s["area"] * 100, 2) if s["area"] else 0,
            "parts": [{"code": c, "name": names.get(c, str(c)), "area": round(a / 1e6, 3)}
                      for c, a in sorted(bycode.items(), key=lambda kv: -kv[1])
                      if a / 1e6 >= 0.005],
        })
    rows.sort(key=lambda r: -r["ratio"])

    payload = {
        "region": "세종특별자치시",
        "unit": "km2",
        "layer_year": LAYER_YEAR,
        "dongs": rows,
        "total": {"area": round(total_area, 2), "impervious": round(grand, 2),
                  "ratio": round(grand / total_area * 100, 2)},
        "polygons": len(seen),
        "basis": table["_source_status"],
        "caveat": ("읍면동별 값은 공식 통계가 아닙니다. 기관이 공표하는 불투수면적률은 "
                   "시군구까지이며 세종은 시 전체 한 값입니다. 여기 값은 같은 기준표로 "
                   "세분류 토지피복지도에서 직접 산정한 것입니다."),
        "source": "기후에너지환경부 환경공간정보 세분류 토지피복지도 %d" % LAYER_YEAR,
        "fetched_at": stamp(now_kst()),
    }
    path = os.path.join(ROOT, "assets", "sejong_impervious_dong.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=1)

    print("", flush=True)
    print("우리 산정 %.2f km2 / %.2f km2 = %.2f%%"
          % (grand, total_area, grand / total_area * 100), flush=True)
    print("기관 공표  60.42 km2 / 465.22 km2 = 12.99%  <- 이것과 가까워야 한다", flush=True)
    print("저장 %s" % path, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
