# -*- coding: utf-8 -*-
"""침수·범람도 GeoJSON → 상황판 오버레이 레이어(assets/flood/).

폴리곤을 단순화해 assets/flood/<id>.json 으로 저장하고 index.json 에 등록한다.
등록된 레이어는 상황판 지도 위 토글 버튼으로 켜고 끌 수 있다.

자료 출처
  * 홍수위험지도정보시스템  https://www.floodmap.go.kr  (하천범람 = 외수)
  * 공공데이터포털 15141731  권역별 빈도별 도시침수지도   (내수, 30/50/80/100년)
  * 공공데이터포털 15141734  행정구역별 빈도별 도시침수지도 (내수, 30/50/80/100년)

주의 — 좌표계. 국내 자료는 EPSG:5179/5186 로 오는 경우가 많다. 이 도구는 변환을
하지 않으므로 **WGS84 경위도(EPSG:4326)로 내보낸 GeoJSON** 을 넣어야 한다.
(QGIS: 레이어 우클릭 → 내보내기 → 좌표계 EPSG:4326 지정)

사용 예:
  python tools/make_flood.py river_100.geojson --type 외수 --freq 100년
  python tools/make_flood.py urban_050.geojson --type 내수 --freq 50년 --depth "1.0~2.0m"
  python tools/make_flood.py --list
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOD_DIR = os.path.join(HERE, "assets", "flood")
INDEX = os.path.join(FLOOD_DIR, "index.json")

EPS = 0.00012      # 침수 경계는 하천보다 촘촘해야 한다 ≈ 12m
SEJONG_BBOX = (127.11, 36.39, 127.43, 36.75)


def _perp(point, start, end):
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(points, eps=EPS):
    if len(points) < 4:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst, index = -1.0, first
        for i in range(first + 1, last):
            distance = _perp(points[i], points[first], points[last])
            if distance > worst:
                worst, index = distance, i
        if worst > eps:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, flag in zip(points, keep) if flag]


def rings_of(raw):
    rings = []
    if isinstance(raw, dict):
        if raw.get("type") == "FeatureCollection":
            for feature in raw.get("features") or []:
                rings.extend(rings_of(feature))
            return rings
        if raw.get("type") == "Feature":
            return rings_of(raw.get("geometry") or {})
        geo_type, coords = raw.get("type"), raw.get("coordinates") or []
        if geo_type == "Polygon" and coords:
            return [[tuple(p[:2]) for p in coords[0]]]
        if geo_type == "MultiPolygon":
            return [[tuple(p[:2]) for p in polygon[0]] for polygon in coords if polygon]
    return rings


def looks_projected(rings) -> bool:
    """좌표가 경위도 범위를 한참 벗어나면 투영좌표계로 본다."""
    for ring in rings[:3]:
        for x, y in ring[:20]:
            if abs(x) > 180 or abs(y) > 90:
                return True
    return False


def load_index() -> dict:
    if not os.path.exists(INDEX):
        return {"layers": []}
    try:
        with open(INDEX, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (ValueError, OSError):
        return {"layers": []}
    payload.setdefault("layers", [])
    return payload


def save_index(payload):
    os.makedirs(FLOOD_DIR, exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="침수·범람도 레이어 등록")
    parser.add_argument("source", nargs="?", help="WGS84 GeoJSON 파일")
    parser.add_argument("--type", default="외수", help="외수 / 내수")
    parser.add_argument("--freq", default="", help="예: 100년")
    parser.add_argument("--depth", default="", help="예: 1.0~2.0m")
    parser.add_argument("--color", default="", help="예: #f0883e")
    parser.add_argument("--id", default="", help="레이어 id (생략하면 자동)")
    parser.add_argument("--list", action="store_true", help="등록된 레이어 보기")
    parser.add_argument("--remove", default="", help="레이어 id 를 목록에서 제거")
    args = parser.parse_args(argv)

    index = load_index()

    if args.list:
        if not index["layers"]:
            print("등록된 레이어가 없습니다.")
            return 0
        for layer in index["layers"]:
            print("  %-10s %s %s %s → %s"
                  % (layer.get("id"), layer.get("type"), layer.get("freq"),
                     layer.get("depth", ""), layer.get("file")))
        return 0

    if args.remove:
        before = len(index["layers"])
        index["layers"] = [x for x in index["layers"] if x.get("id") != args.remove]
        save_index(index)
        print("제거 %d건" % (before - len(index["layers"])))
        return 0

    if not args.source:
        parser.print_help()
        return 1

    with open(args.source, "r", encoding="utf-8") as fp:
        raw = json.load(fp)
    rings = rings_of(raw)
    if not rings:
        print("폴리곤을 찾지 못했습니다. Polygon/MultiPolygon GeoJSON 인지 확인해주세요.")
        return 1
    if looks_projected(rings):
        print("좌표가 경위도 범위를 벗어납니다. EPSG:4326(WGS84)으로 변환해 다시 넣어주세요.")
        return 1

    lon0, lat0, lon1, lat1 = SEJONG_BBOX
    kept, dropped = [], 0
    for ring in rings:
        inside = [p for p in ring if lon0 <= p[0] <= lon1 and lat0 <= p[1] <= lat1]
        if not inside:
            dropped += 1
            continue
        thin = simplify(ring)
        if len(thin) >= 4:
            kept.append([[round(x, 6), round(y, 6)] for x, y in thin])
    if not kept:
        print("세종시 범위 안에 들어오는 폴리곤이 없습니다.")
        return 1

    layer_id = args.id or ("%s_%s" % (
        "riv" if args.type.startswith("외") else "urb",
        (args.freq or "x").replace("년", "").zfill(3)))
    filename = "%s.json" % layer_id

    os.makedirs(FLOOD_DIR, exist_ok=True)
    with open(os.path.join(FLOOD_DIR, filename), "w", encoding="utf-8") as fp:
        json.dump({"rings": kept}, fp, ensure_ascii=False, separators=(",", ":"))

    index["layers"] = [x for x in index["layers"] if x.get("id") != layer_id]
    entry = {"id": layer_id, "type": args.type, "freq": args.freq, "file": filename}
    if args.depth:
        entry["depth"] = args.depth
    if args.color:
        entry["color"] = args.color
    index["layers"].append(entry)
    save_index(index)

    nodes = sum(len(r) for r in kept)
    print("폴리곤 %d개(좌표 %d점) 저장 - 범위 밖 %d개 제외" % (len(kept), nodes, dropped))
    print("→ assets/flood/%s, index.json 갱신" % filename)
    print("   python run.py --dashboard 로 상황판을 다시 만들면 토글이 생깁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
