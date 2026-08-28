# -*- coding: utf-8 -*-
"""행정동 GeoJSON → 상황판이 쓰는 경량 경계 파일(assets/sejong_admin.json).

하는 일
  1. 세종 행정동 폴리곤을 읽고
  2. 공유 변(邊)을 소거해 시 외곽선을 계산하고 (union 없이 위상만으로)
  3. Douglas-Peucker 로 단순화해 용량을 줄인다.

원본 예시:
  https://github.com/raqoon886/Local_HangJeongDong
  → hangjeongdong_세종특별자치시.geojson

사용:
  python tools/make_admin.py <원본.geojson> [출력경로]
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(HERE, "assets", "sejong_admin.json")

QUANT = 7          # 변 소거용 좌표 반올림 자리수
EPS_DONG = 0.00035  # 행정동 경계 단순화 허용오차(도) ≈ 35m
EPS_CITY = 0.00025  # 시 외곽선은 조금 더 촘촘히


# --------------------------------------------------------------------------- 기하

def _perp(point, start, end):
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(points, eps):
    """Douglas-Peucker. 반복 구현이라 긴 링에서도 재귀 한도에 걸리지 않는다."""
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


def ring_area(ring):
    """부호 있는 면적(도² 단위). 크기 비교와 최대 링 선별에만 쓴다."""
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def centroid(ring):
    area = ring_area(ring)
    if abs(area) < 1e-12:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx / (6 * area), cy / (6 * area)


# --------------------------------------------------------------------------- 외곽선

def _key(point):
    return (round(point[0], QUANT), round(point[1], QUANT))


def outer_rings(all_rings):
    """폴리곤들이 공유하는 변을 지우고 남은 변을 이어 붙여 외곽 링을 만든다."""
    counts = {}
    for ring in all_rings:
        for i in range(len(ring) - 1):
            a, b = _key(ring[i]), _key(ring[i + 1])
            if a == b:
                continue
            counts[frozenset((a, b))] = counts.get(frozenset((a, b)), 0) + 1

    adjacency = {}
    for edge, count in counts.items():
        if count != 1:                 # 두 번 이상 나온 변 = 내부 경계
            continue
        a, b = tuple(edge)
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    rings, visited = [], set()
    for start in list(adjacency):
        if start in visited or not adjacency.get(start):
            continue
        ring, current, previous = [start], start, None
        visited.add(start)
        while True:
            nexts = [n for n in adjacency.get(current, []) if n != previous]
            nexts = [n for n in nexts if n not in visited or n == start]
            if not nexts:
                break
            following = nexts[0]
            if following == start:
                ring.append(start)
                break
            ring.append(following)
            visited.add(following)
            previous, current = current, following
        if len(ring) >= 4:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append([list(p) for p in ring])
    rings.sort(key=lambda r: abs(ring_area(r)), reverse=True)
    return rings


# --------------------------------------------------------------------------- 변환

def polygons_of(geometry):
    geo_type, coords = geometry.get("type"), geometry.get("coordinates") or []
    if geo_type == "Polygon":
        return [coords]
    if geo_type == "MultiPolygon":
        return list(coords)
    return []


def short_name(adm_nm: str) -> str:
    """'세종특별자치시 세종시 조치원읍' → '조치원읍'"""
    return (adm_nm or "").split()[-1] if adm_nm else ""


def build(src_path: str, out_path: str) -> dict:
    with open(src_path, "r", encoding="utf-8") as fp:
        raw = json.load(fp)

    dongs, every_ring = [], []
    for feature in raw.get("features") or []:
        properties = feature.get("properties") or {}
        name = short_name(properties.get("adm_nm") or properties.get("adm_nm2") or "")
        rings = []
        for polygon in polygons_of(feature.get("geometry") or {}):
            if not polygon:
                continue
            exterior = [tuple(p[:2]) for p in polygon[0]]
            if exterior[0] != exterior[-1]:
                exterior.append(exterior[0])
            every_ring.append(exterior)
            rings.append(exterior)
        if not rings:
            continue
        biggest = max(rings, key=lambda r: abs(ring_area(r)))
        dongs.append({
            "name": name,
            "rings": [[[round(x, 6), round(y, 6)] for x, y in simplify(r, EPS_DONG)]
                      for r in rings],
            "label": [round(v, 6) for v in centroid(biggest)],
            "area": abs(ring_area(biggest)),
        })

    city = outer_rings(every_ring)
    city = [[[round(x, 6), round(y, 6)] for x, y in simplify([tuple(p) for p in ring], EPS_CITY)]
            for ring in city[:3]]

    payload = {
        "_source": "행정동 경계 GeoJSON 을 tools/make_admin.py 로 단순화한 결과",
        "outline": city,
        "dongs": sorted(dongs, key=lambda d: -d["area"]),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
    return payload


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    source = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    result = build(source, target)
    total = sum(len(r) for d in result["dongs"] for r in d["rings"])
    print("행정동 %d개, 외곽 링 %d개, 단순화 후 좌표 %d점"
          % (len(result["dongs"]), len(result["outline"]), total))
    print("→ %s (%.0f KB)" % (target, os.path.getsize(target) / 1024))
