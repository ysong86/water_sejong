# -*- coding: utf-8 -*-
"""OSM Overpass 응답 → 상황판이 쓰는 하천수계도(assets/sejong_rivers.json).

원본 받는 법 (세종시 범위):

  curl -s -X POST --data-binary @query.txt https://overpass-api.de/api/interpreter \\
       -o osm_rivers.json

  query.txt:
    [out:json][timeout:90];
    (
      way["waterway"="river"](36.39,127.11,36.75,127.43);
      way["waterway"="stream"]["name"](36.39,127.11,36.75,127.43);
    );
    out geom;

사용:
  python tools/make_rivers.py <osm_rivers.json> [출력경로]
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(HERE, "assets", "sejong_rivers.json")

EPS = 0.00025          # 단순화 허용오차(도) ≈ 25m
MIN_LENGTH = 0.004     # 이보다 짧은 조각(도, ≈400m)은 버린다 — 점처럼 보인다

# 세종시 범위. Overpass 는 bbox 에 걸린 way 의 **전체** 형상을 돌려주므로
# (금강은 대청댐~공주까지 이어진다) 여기서 잘라내지 않으면 자산이 커지고
# 라벨 좌표가 화면 밖에 잡힌다.
BBOX = (127.11, 36.39, 127.43, 36.75)   # lon0, lat0, lon1, lat1

# 굵기 등급. 이름으로 판정한다 (OSM 태그만으로는 본류/지류가 안 갈린다).
MAIN = ("금강",)
MAJOR = ("미호강", "미호천", "조천", "대교천", "용수천")


def _perp(point, start, end):
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(points, eps=EPS):
    if len(points) < 3:
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


def in_bbox(point, slack=0.01):
    lon, lat = point
    lon0, lat0, lon1, lat1 = BBOX
    return (lon0 - slack <= lon <= lon1 + slack) and (lat0 - slack <= lat <= lat1 + slack)


def clip_to_bbox(line):
    """bbox 안에 든 구간들로 자른다. 경계에서 끊긴 티가 안 나게 바깥 점 하나씩을 붙인다."""
    segments, current = [], []
    for i, point in enumerate(line):
        if in_bbox(point):
            if not current and i > 0:
                current.append(line[i - 1])      # 들어오는 쪽 바깥 점
            current.append(point)
        elif current:
            current.append(point)                # 나가는 쪽 바깥 점
            segments.append(current)
            current = []
    if len(current) >= 2:
        segments.append(current)
    return [seg for seg in segments if len(seg) >= 2]


def length_of(points):
    return sum(math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
               for i in range(len(points) - 1))


def classify(name: str) -> str:
    if name in MAIN:
        return "main"
    if name in MAJOR:
        return "major"
    return "stream"


def build(src_path: str, out_path: str) -> dict:
    with open(src_path, "r", encoding="utf-8") as fp:
        raw = json.load(fp)

    grouped: dict[str, list] = {}
    for element in raw.get("elements") or []:
        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue
        name = ((element.get("tags") or {}).get("name") or "").strip()
        line = [(round(node["lon"], 6), round(node["lat"], 6)) for node in geometry]
        for segment in clip_to_bbox(line):
            if length_of(segment) < MIN_LENGTH:
                continue
            grouped.setdefault(name, []).append(simplify(segment))
        continue

    rivers = []
    for name, lines in grouped.items():
        # 이름 없는 조각은 본류의 지선인 경우가 많다. 짧으면 화면만 지저분해지니 거른다.
        if not name:
            lines = [ln for ln in lines if length_of(ln) >= MIN_LENGTH * 3]
            if not lines:
                continue
        kind = classify(name) if name else "stream"
        longest = max(lines, key=length_of)
        inside = [p for p in longest if in_bbox(p, slack=0.0)] or longest
        rivers.append({
            "name": name,
            "cls": kind,
            "len": round(sum(length_of(ln) for ln in lines), 5),
            "label": [round(v, 6) for v in inside[len(inside) // 2]],
            "lines": [[[x, y] for x, y in ln] for ln in lines],
        })

    order = {"main": 0, "major": 1, "stream": 2}
    rivers.sort(key=lambda r: (order[r["cls"]], -r["len"]))

    payload = {
        "_source": "OpenStreetMap (ODbL) waterway 데이터를 tools/make_rivers.py 로 단순화",
        "rivers": rivers,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    return payload


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    result = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT)
    target = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    nodes = sum(len(ln) for r in result["rivers"] for ln in r["lines"])
    named = [r["name"] for r in result["rivers"] if r["name"]][:12]
    print("하천 %d개(구간 %d, 좌표 %d점)" % (
        len(result["rivers"]),
        sum(len(r["lines"]) for r in result["rivers"]), nodes))
    print("주요:", ", ".join(named))
    print("→ %s (%.0f KB)" % (target, os.path.getsize(target) / 1024))
