# -*- coding: utf-8 -*-
"""세종시 행정구역도 + 하천수계 + 관측지점 + (선택)침수·범람도 오버레이 SVG.

겹 구성 — 아래에서 위로
  1. 행정동 면          assets/sejong_admin.json   (tools/make_admin.py)
  2. 침수·범람도         assets/flood/index.json    (없으면 통째로 빠짐)
  3. 하천수계           assets/sejong_rivers.json  (tools/make_rivers.py, OSM)
  4. 시 외곽선
  5. 행정동·하천 이름
  6. 관측지점 마커

확대/이동을 위해 1~6 은 `<g class="zoom">` 안에 넣는다. 글자와 마커는 class="cs"
(counter-scale) 를 달아 두어, JS 가 배율의 역수를 걸어 크기를 유지한다.
선 굵기는 vector-effect="non-scaling-stroke" 로 고정한다.
"""
from __future__ import annotations

import json
import math
import os

from collectors.common import BASE_DIR

ADMIN_FILE = os.path.join(BASE_DIR, "assets", "sejong_admin.json")
RIVERS_FILE = os.path.join(BASE_DIR, "assets", "sejong_rivers.json")
FLOOD_DIR = os.path.join(BASE_DIR, "assets", "flood")
FLOOD_INDEX = os.path.join(FLOOD_DIR, "index.json")

RIVER_STYLE = {
    "main":   {"w": 4.2, "hover": 6.4, "color": "#3b8ef0", "opacity": 0.95, "label": 17.5},
    "major":  {"w": 2.6, "hover": 4.6, "color": "#2f7fd8", "opacity": 0.9, "label": 15.0},
    "stream": {"w": 1.1, "hover": 3.0, "color": "#2a5f96", "opacity": 0.75, "label": 0},
}
HIT_WIDTH = 12.0        # 투명 히트영역 굵기(기준 폭 560 대비). 얇은 하천도 잡히게.

KIND_SHAPE = {"waterlevel": "triangle", "rainfall": "square",
              "quality": "circle", "groundwater": "diamond", "weir": "bar"}

# 라벨을 붙일 최소 면적(도²). 신도시 행정동은 너무 작아 글자가 겹친다.
LABEL_MIN_AREA = 0.0012

# 침수 유형 기본색 (index.json 에서 레이어별로 덮어쓸 수 있다)
FLOOD_COLORS = {"외수": "#f0883e", "내수": "#a371f7", "하천범람": "#f0883e",
                "도시침수": "#a371f7"}


# --------------------------------------------------------------------------- 자산

def _load(path, key):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (ValueError, OSError):
        return None
    return payload if payload.get(key) is not None else None


def load_admin():
    admin = _load(ADMIN_FILE, "dongs")
    if admin and not (admin.get("outline") or admin.get("dongs")):
        return None
    return admin


def load_rivers():
    payload = _load(RIVERS_FILE, "rivers")
    return (payload or {}).get("rivers") or []


def _rings_of(raw):
    """GeoJSON 또는 링 배열에서 폴리곤 외곽 링들을 모아 온다."""
    rings = []
    if isinstance(raw, dict):
        if raw.get("type") == "FeatureCollection":
            for feature in raw.get("features") or []:
                rings.extend(_rings_of(feature))
            return rings
        if raw.get("type") == "Feature":
            return _rings_of(raw.get("geometry") or {})
        geo_type, coords = raw.get("type"), raw.get("coordinates") or []
        if geo_type == "Polygon":
            return [[tuple(p[:2]) for p in coords[0]]] if coords else []
        if geo_type == "MultiPolygon":
            return [[tuple(p[:2]) for p in polygon[0]] for polygon in coords if polygon]
        for key in ("rings", "polygons", "coordinates", "features"):
            if key in raw:
                return _rings_of(raw[key])
        return rings
    if isinstance(raw, list):
        if raw and isinstance(raw[0], (list, tuple)) and raw[0] \
                and isinstance(raw[0][0], (int, float)):
            return [[tuple(p[:2]) for p in raw]]           # 링 하나
        for item in raw:
            rings.extend(_rings_of(item))
    return rings


def load_floods():
    """assets/flood/index.json 이 가리키는 침수·범람 레이어를 읽는다.

    index.json 예:
      {"layers":[
        {"id":"riv100","type":"외수","freq":"100년","file":"river_100.geojson"},
        {"id":"urb050","type":"내수","freq":"50년","file":"urban_050.geojson"}]}
    """
    index = _load(FLOOD_INDEX, "layers")
    layers = []
    for spec in (index or {}).get("layers") or []:
        filename = spec.get("file") or ""
        path = os.path.join(FLOOD_DIR, filename)
        if not filename or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fp:
                raw = json.load(fp)
        except (ValueError, OSError):
            continue
        rings = _rings_of(raw)
        if not rings:
            continue
        kind = spec.get("type") or "외수"
        layers.append({
            "id": spec.get("id") or filename,
            "type": kind,
            "freq": spec.get("freq") or "",
            "depth": spec.get("depth") or "",
            "color": spec.get("color") or FLOOD_COLORS.get(kind, "#f0883e"),
            "rings": rings,
        })
    return layers


# --------------------------------------------------------------------------- 투영

class Projection:
    """등장방형 + 위도 보정. 시 하나 크기에서는 왜곡이 눈에 띄지 않는다."""

    def __init__(self, lon0, lat0, lon1, lat1, width, height, pad=16):
        self.lon0, self.lon1 = lon0, lon1
        self.lat0, self.lat1 = lat0, lat1
        self.k = math.cos(math.radians((lat0 + lat1) / 2))

        span_x = (lon1 - lon0) * self.k or 1e-6
        span_y = (lat1 - lat0) or 1e-6
        self.scale = min((width - pad * 2) / span_x, (height - pad * 2) / span_y)
        self.off_x = (width - span_x * self.scale) / 2
        self.off_y = (height - span_y * self.scale) / 2

    def __call__(self, lon, lat):
        return (self.off_x + (lon - self.lon0) * self.k * self.scale,
                self.off_y + (self.lat1 - lat) * self.scale)

    def inside(self, lon, lat, slack=0.03):
        return (self.lon0 - slack <= lon <= self.lon1 + slack
                and self.lat0 - slack <= lat <= self.lat1 + slack)


def _bbox(admin, points):
    coords = [p for ring in (admin or {}).get("outline") or [] for p in ring]
    if not coords:
        coords = [p for dong in (admin or {}).get("dongs") or []
                  for ring in dong["rings"] for p in ring]
    if not coords:
        coords = [(p["lon"], p["lat"]) for p in points
                  if p.get("lon") is not None and p.get("lat") is not None]
    if not coords:
        return 127.13, 36.40, 127.41, 36.74
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return min(lons), min(lats), max(lons), max(lats)


def _path(projection, ring, close=True):
    parts = []
    for i, (lon, lat) in enumerate(ring):
        x, y = projection(lon, lat)
        parts.append("%s%.1f,%.1f" % ("M" if i == 0 else "L", x, y))
    if close:
        parts.append("Z")
    return "".join(parts)


def _in_rings(lon, lat, rings) -> bool:
    """레이 캐스팅. 시 경계 밖에 하천 이름이 떠 있는 걸 막는 데 쓴다."""
    inside = False
    for ring in rings:
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i + 1][0], ring[i + 1][1]
            if (y1 > lat) != (y2 > lat):
                cross = x1 + (lat - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
                if lon < cross:
                    inside = not inside
    return inside


def _shape(kind, color, size):
    """원점(0,0) 기준 마커. 위치는 바깥 <g> 의 transform 이 잡는다(확대 시 역보정)."""
    if kind == "triangle":
        return ('<polygon points="0,%.1f %.1f,%.1f %.1f,%.1f" style="fill:%s" '
                'stroke-width="1"/>'
                % (-size * 1.15, -size, size * 0.75, size, size * 0.75, color))
    if kind == "square":
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1.5" '
                'style="fill:%s" stroke-width="1"/>'
                % (-size * 0.8, -size * 0.8, size * 1.6, size * 1.6, color))
    if kind == "bar":
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1" style="fill:%s" '
                'stroke-width="1"/>'
                % (-size * 1.15, -size * 0.45, size * 2.3, size * 0.9, color))
    if kind == "diamond":
        return ('<polygon points="0,%.1f %.1f,0 0,%.1f %.1f,0" style="fill:%s" '
                'stroke-width="1"/>' % (-size, size, size, -size, color))
    return ('<circle cx="0" cy="0" r="%.1f" style="fill:%s" stroke-width="1"/>'
            % (size, color))


def _cs_text(x, y, text, cls="cs", extra=""):
    """확대해도 크기가 유지되는 글자 (JS 가 scale(1/k) 을 덧붙인다)."""
    return ('<text class="%s" data-x="%.1f" data-y="%.1f" '
            'transform="translate(%.1f,%.1f)"%s>%s</text>'
            % (cls, x, y, x, y, extra, text))


# --------------------------------------------------------------------------- 렌더

def render(points, width=560, height=None) -> str:
    admin = load_admin()
    lon0, lat0, lon1, lat1 = _bbox(admin, points)
    if height is None:
        # 자료 종횡비에 맞춰 높이를 잡는다. 고정하면 좌우에 빈 띠가 남는다.
        k = math.cos(math.radians((lat0 + lat1) / 2))
        ratio = (lat1 - lat0) / max((lon1 - lon0) * k, 1e-6)
        height = int(round(width * ratio)) + 42      # 하단 주석 줄 여유
    projection = Projection(lon0, lat0, lon1, lat1, width, height - 42, pad=16)
    scale = width / 560.0
    outline = (admin or {}).get("outline") or []

    defs, clip_ref = "", ""
    if outline:
        defs = ('<defs><clipPath id="cityclip">%s</clipPath></defs>'
                % "".join('<path d="%s"/>' % _path(projection, r) for r in outline))
        clip_ref = ' clip-path="url(#cityclip)"'

    layers = []

    # 1) 행정동 면
    dong_paths, dong_labels = [], []
    for dong in (admin or {}).get("dongs") or []:
        for ring in dong["rings"]:
            dong_paths.append('<path d="%s"/>' % _path(projection, ring))
        if dong.get("area", 0) >= LABEL_MIN_AREA and dong.get("label"):
            lx, ly = projection(*dong["label"])
            dong_labels.append(_cs_text(lx, ly, dong["name"],
                                        extra=' text-anchor="middle"'))
    if dong_paths:
        # 색은 CSS(.map .dongs)가 잡는다 — 라이트/다크에 따라 달라져야 하므로.
        layers.append('<g class="dongs" stroke-width="%.2f" stroke-linejoin="round" '
                      'vector-effect="non-scaling-stroke">%s</g>'
                      % (0.8 * scale, "".join(dong_paths)))

    # 2) 침수·범람 오버레이 — 기본 숨김. 지도 위 토글이 켠다.
    floods = load_floods()
    for layer in floods:
        paths = "".join('<path d="%s"/>' % _path(projection, ring)
                        for ring in layer["rings"])
        layers.append('<g class="flood" data-flood="%s" style="fill:%s;stroke:%s" '
                      'fill-opacity="0.28" stroke-opacity="0.75" stroke-width="1" '
                      'vector-effect="non-scaling-stroke"%s>%s</g>'
                      % (layer["id"], layer["color"], layer["color"], clip_ref, paths))

    # 3) 하천수계 — 호버하면 굵어지고 이름이 뜬다
    groups, river_labels = [], []
    for river in load_rivers():
        style = RIVER_STYLE.get(river.get("cls"), RIVER_STYLE["stream"])
        lines = river.get("lines") or []
        if not lines:
            continue
        paths = [_path(projection, line, close=False) for line in lines]
        hits = "".join('<path class="hit" d="%s"%s/>' % (d, clip_ref) for d in paths)
        strokes = "".join('<path class="ln" d="%s" stroke-opacity="%s"%s/>'
                          % (d, style["opacity"], clip_ref) for d in paths)

        named = bool(river.get("name")) and bool(river.get("label"))
        permanent = named and style["label"] and _in_rings(
            river["label"][0], river["label"][1], outline)
        hover_text = ""
        if named and not permanent:
            lx, ly = projection(*river["label"])
            hover_text = _cs_text(lx + 6 * scale, ly - 6 * scale,
                                  river["name"], cls="cs rvname")
        elif permanent:
            lx, ly = projection(*river["label"])
            river_labels.append(_cs_text(lx + 5 * scale, ly - 5 * scale, river["name"],
                                         extra=' font-size="%.1f"'
                                               % (style["label"] * scale)))

        groups.append('<g class="rv" data-cls="%s" '
                      'style="--w:%.2f;--wh:%.2f;--hit:%.1f">%s%s%s</g>'
                      % (river.get("cls", "stream"), style["w"] * scale,
                         style["hover"] * scale, HIT_WIDTH * scale,
                         hits, strokes, hover_text))
    if groups:
        layers.append('<g class="rivers">%s</g>' % "".join(groups))

    # 4) 시 외곽선
    for ring in outline:
        layers.append('<path class="cityline" d="%s" fill="none" '
                      'stroke-width="%.2f" stroke-linejoin="round" '
                      'vector-effect="non-scaling-stroke"/>'
                      % (_path(projection, ring), 1.8 * scale))

    # 5) 이름
    if river_labels:
        layers.append('<g class="rvlabel" style="pointer-events:none">%s</g>'
                      % "".join(river_labels))
    if dong_labels:
        layers.append('<g class="dongname" font-size="%.1f" font-weight="600" '
                      'style="pointer-events:none">%s</g>'
                      % (14.5 * scale, "".join(dong_labels)))

    # 6) 관측지점 마커
    markers, missing = [], 0
    for point in points:
        lat, lon = point.get("lat"), point.get("lon")
        if lat is None or lon is None or not projection.inside(lon, lat):
            missing += 1
            continue
        x, y = projection(lon, lat)
        # 이름표는 오른쪽에 붙이되, 오른쪽 끝 지점은 왼쪽으로 뒤집는다.
        flip = x > width * 0.72
        offset = (-1 if flip else 1) * 11 * scale
        anchor = "end" if flip else "start"
        label = ('<text class="mkname" x="%.1f" y="%.1f" text-anchor="%s">%s</text>'
                 % (offset, -8 * scale, anchor, point["name"]))
        markers.append(
            '<g class="mk cs" data-id="%s" data-x="%.1f" data-y="%.1f" '
            'transform="translate(%.1f,%.1f)" tabindex="0" role="button" aria-label="%s">'
            '<circle class="halo" cx="0" cy="0" r="%.1f" fill="%s" opacity="0"/>'
            '%s%s</g>'
            % (point["id"], x, y, x, y, point["name"].replace('"', "'"),
               14 * scale, point["color"],
               _shape(KIND_SHAPE.get(point.get("kind"), "circle"),
                      point["color"], 6.5 * scale),
               label))
    if markers:
        layers.append('<g class="markers">%s</g>' % "".join(markers))

    # 확대/이동 대상은 여기까지. 범례와 주석은 화면에 고정.
    zoomable = '<g class="zoom">%s</g>' % "".join(layers)

    # 범례 — 우측 상단, 배경 판을 깔아 지도 라벨과 겹치지 않게
    lg_w, lg_h = 78, 106
    lg_x, lg_y = width - lg_w - 14, 14
    legend = (
        '<g class="legend" transform="translate(%.1f,%.1f)" font-size="13.5" '
        'style="pointer-events:none">'
        '<rect class="lg-bg" x="-9" y="-9" width="%d" height="%d" rx="9"/>'
        '<polygon points="7,2 0,13 14,13"/><text x="22" y="13">수위</text>'
        '<rect x="1" y="23" width="12" height="12" rx="2"/><text x="22" y="34">강수</text>'
        '<circle cx="7" cy="51" r="6.5"/><text x="22" y="55">수질</text>'
        '<polygon points="7,66 14,73 7,80 0,73"/><text x="22" y="77">지하수</text>'
        '<rect x="0" y="90" width="14" height="6" rx="1"/><text x="22" y="97">보</text>'
        '</g>' % (lg_x, lg_y, lg_w, lg_h))

    # 축척 — 확대하면 JS 가 길이와 숫자를 다시 계산한다.
    # 1 SVG 단위 = (1/scale)도 위도 = 111320/scale 미터. 가로도 cos(위도)를 이미
    # 반영해 두어 두 축의 거리 척도가 같다.
    meters_per_unit = 111320.0 / projection.scale
    bar_x, bar_y = width - 178, height - 42
    scalebar = (
        '<g class="scalebar" data-mpu="%.6f" data-x="%.1f" data-y="%.1f" '
        'style="pointer-events:none">'
        '<rect class="sb-bg" x="%.1f" y="%.1f" width="150" height="26" rx="7"/>'
        '<text class="sb-label" x="%.1f" y="%.1f" font-size="11">—</text>'
        '<rect class="sb-bar" x="%.1f" y="%.1f" width="60" height="4" rx="2"/>'
        '<rect class="sb-tick" x="%.1f" y="%.1f" width="1.6" height="9" rx="0.8"/>'
        '<rect class="sb-tick sb-tick2" x="%.1f" y="%.1f" width="1.6" height="9" rx="0.8"/>'
        '</g>'
        % (meters_per_unit, bar_x, bar_y,
           bar_x - 8, bar_y - 8,                 # 배경
           bar_x, bar_y + 2,                     # 라벨
           bar_x, bar_y + 8,                     # 막대
           bar_x, bar_y + 5.5,                   # 왼쪽 눈금
           bar_x + 58.4, bar_y + 5.5))           # 오른쪽 눈금(JS 가 옮긴다)

    missing_layers = []
    if not admin:
        missing_layers.append("행정구역(assets/sejong_admin.json)")
    if not groups:
        missing_layers.append("하천수계(assets/sejong_rivers.json)")
    note = ("누락된 자산: " + ", ".join(missing_layers) if missing_layers
            else "행정구역·하천수계·관측지점 모두 실제 좌표 · 하천망 © OpenStreetMap")
    footnote = ('<text class="footnote" x="14" y="%d" font-size="%.1f">%s</text>'
                % (height - 13, 12.5 * scale, note))
    if missing:
        footnote += ('<text x="14" y="%d" fill="#8b949e" font-size="10">'
                     '좌표 없는 %d개소는 왼쪽 목록에만 표시</text>' % (height - 28, missing))

    return ('<svg class="map" viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">'
            '%s%s%s%s%s</svg>'
            % (width, height, defs, zoomable, legend, scalebar, footnote))
