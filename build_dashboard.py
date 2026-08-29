# -*- coding: utf-8 -*-
"""수집 결과(data/latest.json) → 단일 HTML 상황판.

차트는 외부 라이브러리 없이 파이썬에서 SVG 문자열로 직접 그린다.
결과 파일은 자기완결형이라 그냥 브라우저로 열면 된다.
"""
from __future__ import annotations

import html
import json
import os
import urllib.parse
from datetime import datetime

import sejong_map
from collectors.common import BASE_DIR, KST, now_kst, stamp

OUT_PATH = os.path.join(BASE_DIR, "dashboard.html")

# 색은 전부 assets/theme.css 의 토큰을 가리킨다. 라이트/다크 어느 쪽이든
# 같은 이름이 각 테마의 값으로 풀리므로 파이썬 쪽은 테마를 몰라도 된다.
STAGE_COLORS = {
    "normal": "var(--st-normal)", "watch": "var(--st-watch)",
    "warning": "var(--st-warning)", "alert": "var(--st-alert)",
    "serious": "var(--st-serious)", "unknown": "var(--st-unknown)",
}
GRADE_COLORS = {
    "Ia": "var(--g-ia)", "Ib": "var(--g-ib)", "II": "var(--g-ii)",
    "III": "var(--g-iii)", "IV": "var(--g-iv)", "V": "var(--g-v)",
    "VI": "var(--g-vi)",
}
GW_COLORS = {"national": "var(--gw-national)", "local": "var(--gw-local)"}
ACCENT = "var(--accent)"
WARN = "var(--st-warning)"
NEUTRAL = "var(--st-unknown)"
STAGE_ORDER = ["normal", "watch", "warning", "alert", "serious"]
GRADE_ORDER = ["Ia", "Ib", "II", "III", "IV", "V", "VI"]


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value, digits=2, unit="", dash="—"):
    if value is None:
        return dash
    return f"{value:,.{digits}f}{unit}"


def hhmm(ymdhm) -> str:
    """YYYYMMDDHHmm / YYYYMMDDHH / YYYYMMDD 를 사람이 읽는 꼴로.
    HRFCO 는 1시간 자료를 10자리(YYYYMMDDHH)로 준다."""
    text = str(ymdhm or "")
    if len(text) >= 12:
        return f"{text[4:6]}/{text[6:8]} {text[8:10]}:{text[10:12]}"
    if len(text) == 10:
        return f"{text[4:6]}/{text[6:8]} {text[8:10]}:00"
    if len(text) >= 8:
        return f"{text[4:6]}/{text[6:8]}"
    return text or "—"


# --------------------------------------------------------------------------- SVG

def sparkline(series, color=ACCENT, width=260, height=54, fill=True) -> str:
    points = [p for p in series if p.get("v") is not None]
    if len(points) < 2:
        return ('<svg class="spark" viewBox="0 0 %d %d"><text x="8" y="32" '
                'style="fill:var(--muted)" font-size="12">자료 부족</text></svg>' % (width, height))
    values = [p["v"] for p in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 6
    step = (width - pad * 2) / (len(points) - 1)

    coords = []
    for i, value in enumerate(values):
        x = pad + i * step
        y = height - pad - (value - lo) / span * (height - pad * 2)
        coords.append((x, y))
    line = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", x, y)
                    for i, (x, y) in enumerate(coords))
    area = ""
    if fill:
        area = ('<path d="%s L%.1f,%d L%.1f,%d Z" style="fill:%s" opacity="0.13"/>'
                % (line, coords[-1][0], height, coords[0][0], height, color))
    last_x, last_y = coords[-1]
    return ('<svg class="spark" viewBox="0 0 %d %d" preserveAspectRatio="none">%s'
            '<path d="%s" fill="none" style="stroke:%s" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
            '<circle cx="%.1f" cy="%.1f" r="3" style="fill:%s"/></svg>'
            % (width, height, area, line, color, last_x, last_y, color))


def level_gauge(entry) -> str:
    """현재 수위를 관심/주의보/경보/심각 눈금 위에 얹은 가로 게이지."""
    latest = entry.get("latest") or {}
    level = latest.get("v")
    thresholds = [(entry.get("attwl"), "관심", "var(--st-watch)"),
                  (entry.get("wrnwl"), "주의보", "var(--st-warning)"),
                  (entry.get("almwl"), "경보", "var(--st-alert)"),
                  (entry.get("srswl"), "심각", "var(--st-serious)")]
    known = [t[0] for t in thresholds if t[0] is not None]
    if not known:
        return '<div class="gauge-none">홍수단계 수위 미제공</div>'

    top = max(known) * 1.12
    bottom = min(0.0, (level or 0) * 0.9)
    span = (top - bottom) or 1.0
    width, height = 260, 34

    def to_x(value):
        return max(0.0, min(1.0, (value - bottom) / span)) * width

    ticks = []
    for value, label, color in thresholds:
        if value is None:
            continue
        x = to_x(value)
        ticks.append('<line x1="%.1f" y1="4" x2="%.1f" y2="20" style="stroke:%s" '
                     'stroke-width="2"/><text x="%.1f" y="31" style="fill:%s" '
                     'font-size="9" text-anchor="middle">%s</text>'
                     % (x, x, color, x, color, label))

    bar = ""
    if level is not None:
        color = STAGE_COLORS.get(entry.get("stage", "unknown"), NEUTRAL)
        bar = ('<rect x="0" y="8" width="%.1f" height="8" rx="4" style="fill:%s"/>'
               % (to_x(level), color))

    return ('<svg class="gauge" viewBox="0 0 %d %d">'
            '<rect x="0" y="8" width="%d" height="8" rx="4" style="fill:var(--panel-3)"/>%s%s</svg>'
            % (width, height, width, bar, "".join(ticks)))


def bar_chart(slots, key, color=ACCENT, width=560, height=96,
              unit="mm", max_hint=None) -> str:
    if not slots:
        return '<div class="empty">예보 자료 없음</div>'
    values = [(s.get(key) or 0) for s in slots]
    top = max(values + [max_hint or 0]) or 1.0
    pad_bottom = 18
    slot_w = width / len(slots)
    bars, labels = [], []
    for i, (slot, value) in enumerate(zip(slots, values)):
        bar_h = (value / top) * (height - pad_bottom - 4)
        x = i * slot_w + slot_w * 0.15
        w = slot_w * 0.7
        y = height - pad_bottom - bar_h
        opacity = 0.35 if value == 0 else 1.0
        bars.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s" '
                    'opacity="%s"><title>%s · %s%s</title></rect>'
                    % (x, y, w, max(bar_h, 1), color, opacity,
                       esc(hhmm(slot.get("t"))), value, unit))
        text = str(slot.get("t", ""))
        if len(text) >= 12 and int(text[8:10]) % 3 == 0:
            labels.append('<text x="%.1f" y="%d" style="fill:var(--muted)" font-size="9" '
                          'text-anchor="middle">%s</text>'
                          % (i * slot_w + slot_w / 2, height - 5, text[8:10]))
    # 균등 스케일 유지 — none 을 주면 좁은 칼럼에서 시각 라벨이 가로로 늘어난다.
    return ('<svg class="bars" viewBox="0 0 %d %d">%s%s</svg>'
            % (width, height, "".join(bars), "".join(labels)))


# --------------------------------------------------------------------------- 요약

def summarize(data: dict) -> list:
    hrfco = data.get("hrfco") or {}
    kma = data.get("kma") or {}
    nier = data.get("nier") or {}

    levels = hrfco.get("waterlevel") or []
    worst_stage, worst_name = "unknown", "—"
    for entry in levels:
        stage = entry.get("stage", "unknown")
        if stage not in STAGE_ORDER:
            continue
        if worst_stage not in STAGE_ORDER or \
                STAGE_ORDER.index(stage) > STAGE_ORDER.index(worst_stage):
            worst_stage, worst_name = stage, entry.get("name", "—")
    stage_label = next((e.get("stage_label") for e in levels
                        if e.get("stage") == worst_stage), "자료없음")

    rains = [e.get("sum_24h") for e in (hrfco.get("rainfall") or [])
             if e.get("sum_24h") is not None]
    rain_max = max(rains) if rains else None

    grades = [p.get("grade") for p in (nier.get("points") or []) if p.get("grade")]
    worst_grade = max(grades, key=lambda g: GRADE_ORDER.index(g)) if grades else None
    worst_point = next((p.get("name") for p in (nier.get("points") or [])
                        if p.get("grade") == worst_grade), "—") if worst_grade else "자료없음"

    forecast = kma.get("forecast") or {}

    return [
        {"label": "하천 최고 단계", "value": stage_label, "sub": worst_name,
         "color": STAGE_COLORS.get(worst_stage, NEUTRAL)},
        {"label": "24h 누적 강수(최대)", "value": fmt(rain_max, 1, " mm"),
         "sub": ("관측소 %d개소" % len(rains)) if rains else "자료없음",
         "color": ACCENT if (rain_max or 0) < 30 else WARN},
        {"label": "수질 최저 등급", "value": worst_grade or "—", "sub": worst_point,
         "color": GRADE_COLORS.get(worst_grade, NEUTRAL)},
        {"label": "24h 예보 강수", "value": fmt(forecast.get("rain_sum"), 1, " mm"),
         "sub": ("최대 강수확률 " + fmt(forecast.get("pop_max"), 0, "%")) if forecast else "자료없음",
         "color": ACCENT if (forecast.get("rain_sum") or 0) < 20 else WARN},
        _groundwater_kpi(data),
    ]


def _groundwater_kpi(data: dict) -> dict:
    stations = (data.get("gims") or {}).get("stations") or []
    deltas = [s.get("delta_24h") for s in stations if s.get("delta_24h") is not None]
    if not deltas:
        return {"label": "지하수위 24h 변화", "value": "—", "sub": "자료없음",
                "color": NEUTRAL}
    average = sum(deltas) / len(deltas)
    falling = sum(1 for d in deltas if d < 0)
    return {
        "label": "지하수위 24h 변화(평균)",
        "value": ("%+.2f m" % average),
        "sub": "관측정 %d개소 · 하강 %d" % (len(deltas), falling),
        "color": GW_COLORS["national"] if average >= -0.3 else WARN,
    }


# --------------------------------------------------------------------------- 렌더링

CSS_FILE = os.path.join(BASE_DIR, "assets", "theme.css")

# 폰트는 CDN 에서 받되, 없으면 맑은 고딕으로 떨어진다(오프라인에서도 깨지지 않는다).
FONT_LINK = ('<link rel="preconnect" href="https://cdn.jsdelivr.net">'
             '<link rel="stylesheet" as="style" crossorigin '
             'href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/'
             'variable/pretendardvariable-dynamic-subset.min.css">')

FALLBACK_CSS = """
body{margin:0;background:#f4f7fb;color:#0e2038;font-family:system-ui,sans-serif}
.wrap{max-width:1440px;margin:0 auto;padding:24px}
.panel{background:#fff;border:1px solid #dbe3ec;border-radius:12px;padding:16px}
"""


def load_css() -> str:
    """assets/theme.css 를 읽어 HTML 에 박는다. 결과물은 자기완결형으로 유지된다."""
    try:
        with open(CSS_FILE, "r", encoding="utf-8") as fp:
            return fp.read()
    except OSError:
        return FALLBACK_CSS


def _detail_head(name, sub, big, unit, color, tag=None) -> str:
    tag_html = ""
    if tag:
        tag_html = ('<span class="tag" style="background:color-mix(in srgb,%s 15%%,transparent);'
                    'color:%s;border:1px solid color-mix(in srgb,%s 38%%,transparent);'
                    'margin-left:8px">%s</span>' % (color, color, color, esc(tag)))
    return ('<div class="dt-name">%s%s</div><div class="dt-sub">%s</div>'
            '<div class="dt-big" style="color:%s">%s<span class="dt-unit"> %s</span></div>'
            % (esc(name), tag_html, esc(sub), color, big, esc(unit)))


def detail_waterlevel(entry: dict) -> str:
    latest = entry.get("latest") or {}
    color = STAGE_COLORS.get(entry.get("stage", "unknown"), NEUTRAL)
    delta = entry.get("delta_1h")
    delta_html = "—"
    if delta is not None:
        arrow = "▲ +" if delta > 0 else ("▼ " if delta < 0 else "· ")
        delta_html = ('<span class="delta %s">%s%s m/h</span>'
                      % ("up" if delta > 0 else "down", arrow, fmt(abs(delta), 2)))
    rows = [("관측시각", esc(hhmm(latest.get("t")))),
            ("1시간 변화", delta_html),
            ("유량", fmt(latest.get("fw"), 1, " ㎥/s")),
            ("관심 수위", fmt(entry.get("attwl"), 2, " m")),
            ("주의보 수위", fmt(entry.get("wrnwl"), 2, " m")),
            ("경보 수위", fmt(entry.get("almwl"), 2, " m")),
            ("심각 수위", fmt(entry.get("srswl"), 2, " m")),
            ("관측소 코드", esc(entry.get("code")))]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (label, value) for label, value in rows)
    return (_detail_head(entry.get("name"), entry.get("addr") or "세종특별자치시",
                         fmt(latest.get("v"), 2), "m", color, entry.get("stage_label"))
            + '<div class="sect"><h4>홍수단계 대비</h4>%s</div>' % level_gauge(entry)
            + '<div class="sect"><h4>최근 24시간 수위</h4>%s</div>'
              % sparkline(entry.get("series") or [], color, width=300, height=70)
            + "<dl>%s</dl>" % dl)


def bucket_hourly(series):
    """10분 자료를 시간별로 합산. 24시간 144개 막대는 좁은 칼럼에서 읽히지 않는다."""
    buckets = {}
    for point in series:
        stamp_text = str(point.get("t") or "")
        if len(stamp_text) < 12:
            continue
        hour = stamp_text[:10] + "00"
        buckets[hour] = buckets.get(hour, 0.0) + (point.get("v") or 0.0)
    return [{"t": key, "v": round(value, 1)} for key, value in sorted(buckets.items())]


def detail_rainfall(entry: dict) -> str:
    total = entry.get("sum_24h")
    color = ACCENT if (total or 0) < 30 else WARN
    latest = entry.get("latest") or {}
    rows = [("최근 관측", esc(hhmm(latest.get("t")))),
            ("최근 1시간", fmt(latest.get("v"), 1, " mm")),
            ("관측소 코드", esc(entry.get("code")))]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (label, value) for label, value in rows)
    return (_detail_head(entry.get("name"), entry.get("addr") or "세종특별자치시",
                         fmt(total, 1), "mm / 24h", color)
            + '<div class="sect"><h4>시간별 강수량</h4>%s</div>'
              % bar_chart(bucket_hourly(entry.get("series") or []), "v", color,
                          width=300, height=80, unit="mm")
            + "<dl>%s</dl>" % dl)


def detail_quality(point: dict) -> str:
    color = GRADE_COLORS.get(point.get("grade"), NEUTRAL)
    cells = "".join(
        '<div class="q"><div class="l">%s%s</div><div class="v">%s</div></div>'
        % (esc(label), (" (%s)" % unit) if unit else "", fmt(point.get(key), digits))
        for label, key, digits, unit in WQ_COLUMNS)
    extra = "".join(
        '<div class="q"><div class="l">%s</div><div class="v">%s</div></div>' % (label, value)
        for label, value in [("SS", fmt(point.get("ss"), 1)),
                             ("클로로필-a", fmt(point.get("chla"), 1))])
    return (_detail_head(point.get("name"), "측정 " + hhmm(point.get("time")),
                         esc(point.get("grade") or "—"),
                         point.get("grade_label") or "", color)
            + '<div class="sect"><h4>수질 항목</h4><div class="qgrid">%s%s</div></div>'
              % (cells, extra)
            + '<div class="sect"><h4>등급 기준</h4>'
              '<div style="color:var(--muted);font-size:11.5px">하천 생활환경기준 TOC 기준 '
              '참고값입니다. 법정 등급은 여러 항목을 함께 판정합니다.</div></div>')


def detail_groundwater(entry: dict) -> str:
    color = GW_COLORS.get(entry.get("network"), GW_COLORS["national"])
    latest = entry.get("latest") or {}
    delta = entry.get("delta_24h")
    delta_html = "—"
    if delta is not None:
        arrow = "▲ +" if delta > 0 else ("▼ " if delta < 0 else "· ")
        delta_html = ('<span class="delta %s">%s%s m</span>'
                      % ("up" if delta > 0 else "down", arrow, fmt(abs(delta), 3)))
    rows = [("관측시각", esc(hhmm(latest.get("t")))),
            ("24시간 변화", delta_html),
            ("수온", fmt(entry.get("temp"), 1, " ℃")),
            ("전기전도도", fmt(entry.get("cond"), 1, " µS/cm")),
            ("지표하 심도", fmt(entry.get("depth"), 2, " m")),
            ("관측망", esc(entry.get("network_label") or "—")),
            ("관측소 번호", esc(entry.get("code")))]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (label, value) for label, value in rows)
    error = entry.get("error")
    warn = ('<div class="sect" style="color:var(--st-warning);font-size:11.5px">%s</div>'
            % esc(error)) if error else ""
    return (_detail_head(entry.get("name"), entry.get("addr") or "세종특별자치시",
                         fmt(latest.get("v"), 2), "m", color,
                         entry.get("network_label"))
            + '<div class="sect"><h4>최근 24시간 지하수위</h4>%s</div>'
              % sparkline(entry.get("series") or [], color, width=300, height=70)
            + "<dl>%s</dl>" % dl + warn)


def build_points(data: dict, cfg_coords: dict | None = None) -> list:
    """지도·목록·상세가 공유하는 단일 지점 목록."""
    hrfco = data.get("hrfco") or {}
    nier = data.get("nier") or {}
    coords = cfg_coords or {}
    points = []

    for i, entry in enumerate(hrfco.get("waterlevel") or []):
        latest = entry.get("latest") or {}
        points.append({
            "id": "wl%d" % i, "kind": "waterlevel", "group": "하천 수위",
            "name": entry.get("name") or "(이름없음)",
            "lat": entry.get("lat"), "lon": entry.get("lon"),
            "color": STAGE_COLORS.get(entry.get("stage", "unknown"), NEUTRAL),
            "value": fmt(latest.get("v"), 2, " m"),
            "detail": detail_waterlevel(entry),
        })
    for i, entry in enumerate(hrfco.get("rainfall") or []):
        total = entry.get("sum_24h")
        points.append({
            "id": "rf%d" % i, "kind": "rainfall", "group": "강수량",
            "name": entry.get("name") or "(이름없음)",
            "lat": entry.get("lat"), "lon": entry.get("lon"),
            "color": ACCENT if (total or 0) < 30 else WARN,
            "value": fmt(total, 1, " mm"),
            "detail": detail_rainfall(entry),
        })
    for i, entry in enumerate((data.get("gims") or {}).get("stations") or []):
        latest = entry.get("latest") or {}
        points.append({
            "id": "gw%d" % i, "kind": "groundwater", "group": "지하수",
            "name": entry.get("name") or "(이름없음)",
            "lat": entry.get("lat"), "lon": entry.get("lon"),
            "color": GW_COLORS.get(entry.get("network"), GW_COLORS["national"]),
            "value": fmt(latest.get("v"), 2, " m"),
            "detail": detail_groundwater(entry),
        })
    for i, point in enumerate(nier.get("points") or []):
        lat, lon = point.get("lat"), point.get("lon")
        if lat is None or lon is None:
            fallback = coords.get(point.get("name")) or coords.get(point.get("code") or "")
            if fallback and len(fallback) == 2:
                lon, lat = float(fallback[0]), float(fallback[1])
        points.append({
            "id": "wq%d" % i, "kind": "quality", "group": "수질",
            "name": point.get("name") or "(이름없음)",
            "lat": lat, "lon": lon,
            "color": GRADE_COLORS.get(point.get("grade"), NEUTRAL),
            "value": point.get("grade") or "—",
            "detail": detail_quality(point),
        })
    return points


DOT_CLASS = {"waterlevel": "dot tri", "rainfall": "dot sq",
             "quality": "dot", "groundwater": "dot dia"}


def render_maptools(floods) -> str:
    zoom = ('<div class="zoombtns">'
            '<button type="button" data-zoom="in" title="확대">+</button>'
            '<button type="button" data-zoom="out" title="축소">−</button>'
            '<button type="button" data-zoom="reset" title="원래대로">초기화</button>'
            '</div>')
    if not floods:
        hint = ('<div class="ft-empty">침수·범람도 없음 — '
                '<code>assets/flood/</code> 에 넣으면 여기서 켤 수 있습니다</div>')
        return '<div class="maptools">%s%s</div>' % (zoom, hint)

    chips = []
    for layer in floods:
        label = " ".join(x for x in [layer.get("type"), layer.get("freq"),
                                     layer.get("depth")] if x)
        chips.append('<button type="button" class="ft" data-flood="%s" '
                     'style="--c:%s">%s</button>'
                     % (esc(layer["id"]), layer["color"], esc(label or layer["id"])))
    return ('<div class="maptools">%s<div class="fts">'
            '<span class="ft-label">침수·범람</span>%s</div></div>'
            % (zoom, "".join(chips)))


ZOOM_JS = r"""
(function(){
  var svg=document.querySelector('.map'); if(!svg) return;
  var g=svg.querySelector('.zoom'); if(!g) return;
  var vb=(svg.getAttribute('viewBox')||'0 0 560 620').split(/\s+/).map(Number);
  var W=vb[2], H=vb[3], MINK=1, MAXK=14;
  var k=1, tx=0, ty=0, drag=null, moved=0;

  function clamp(){ k=Math.min(MAXK,Math.max(MINK,k));
    tx=Math.min(0,Math.max(W*(1-k),tx)); ty=Math.min(0,Math.max(H*(1-k),ty)); }
  // 축척 — 확대 배율에 맞춰 '깔끔한 거리'(1/2/5 x 10^n)를 골라 막대 길이를 맞춘다.
  var sb = svg.querySelector('.scalebar');
  var sbBar = sb && sb.querySelector('.sb-bar');
  var sbTick2 = sb && sb.querySelector('.sb-tick2');
  var sbLabel = sb && sb.querySelector('.sb-label');
  var MPU = sb ? parseFloat(sb.dataset.mpu) : 0;
  var SB_MAX = 140;                      // 막대 최대 길이(SVG 단위)

  function updateScale(){
    if(!sb || !MPU) return;
    var perUnit = MPU / k;               // 현재 배율에서 1 단위가 몇 m 인가
    var nice = 1, best = 1;
    for(var e=0; e<8; e++){
      for(var i=0; i<3; i++){
        var m = [1,2,5][i] * Math.pow(10, e);
        if(m / perUnit <= SB_MAX) best = m;
      }
    }
    nice = best;
    var w = nice / perUnit;
    var x0 = parseFloat(sb.dataset.x);
    sbBar.setAttribute('width', w.toFixed(1));
    sbTick2.setAttribute('x', (x0 + w - 1.6).toFixed(1));
    sbLabel.textContent = nice >= 1000 ? (nice/1000) + ' km' : nice + ' m';
  }

  function apply(){ clamp();
    g.setAttribute('transform','translate('+tx.toFixed(2)+','+ty.toFixed(2)+') scale('+k.toFixed(4)+')');
    var inv=(1/k).toFixed(4);
    svg.querySelectorAll('.cs').forEach(function(e){
      e.setAttribute('transform','translate('+e.dataset.x+','+e.dataset.y+') scale('+inv+')'); });
    svg.classList.toggle('zoomed', k>1.01);
    updateScale(); }
  function toSvg(evt){ var m=svg.getScreenCTM(); if(!m) return null; m=m.inverse();
    return {x:evt.clientX*m.a+evt.clientY*m.c+m.e, y:evt.clientX*m.b+evt.clientY*m.d+m.f}; }
  function zoomAt(px,py,factor){ var nk=Math.min(MAXK,Math.max(MINK,k*factor));
    if(nk===k) return; tx=px-(px-tx)*(nk/k); ty=py-(py-ty)*(nk/k); k=nk; apply(); }
  svg.addEventListener('wheel', function(e){ e.preventDefault();
    var p=toSvg(e); if(!p) return; zoomAt(p.x,p.y,Math.exp(-e.deltaY*0.0016)); },
    {passive:false});
  svg.addEventListener('dblclick', function(e){ var p=toSvg(e); if(p) zoomAt(p.x,p.y,1.7); });

  svg.addEventListener('pointerdown', function(e){
    if(e.button!==0) return; drag={x:e.clientX,y:e.clientY,tx:tx,ty:ty}; moved=0;
    svg.setPointerCapture(e.pointerId); });
  svg.addEventListener('pointermove', function(e){ if(!drag) return;
    var rect=svg.getBoundingClientRect(); var per=W/(rect.width||1);
    var dx=(e.clientX-drag.x), dy=(e.clientY-drag.y);
    moved=Math.max(moved,Math.abs(dx)+Math.abs(dy));
    tx=drag.tx+dx*per; ty=drag.ty+dy*per; apply(); });
  function endDrag(e){ if(!drag) return; drag=null;
    try{ svg.releasePointerCapture(e.pointerId); }catch(_){} }
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('click', function(e){ if(moved>6){ e.stopPropagation(); moved=0; } }, true);

  document.querySelectorAll('.zoombtns button').forEach(function(b){
    b.addEventListener('click', function(){
      var mode=b.dataset.zoom;
      if(mode==='reset'){ k=1; tx=0; ty=0; apply(); return; }
      zoomAt(W/2, H/2, mode==='in'?1.5:1/1.5); }); });

  document.querySelectorAll('.ft').forEach(function(b){
    b.addEventListener('click', function(){
      var on=b.classList.toggle('on');
      var el=svg.querySelector('.flood[data-flood="'+b.dataset.flood+'"]');
      if(el) el.style.display = on ? 'block' : 'none'; }); });

  apply();
})();
"""


def render_explorer(points: list) -> str:
    floods = sejong_map.load_floods()
    if not points:
        # 관측지점이 없어도 행정구역·하천수계는 보여준다. 지도까지 사라지면
        # 화면이 통째로 비어 무엇이 잘못됐는지 알 수 없다.
        return ('<div class="explorer nodata" data-view="explorer">'
                '<section class="panel mapwrap"><h2>세종시 지도'
                '<span class="src">행정구역 · 하천수계</span></h2>%s%s'
                '<p class="note" style="margin:11px 0 0">관측지점이 없습니다. '
                '인증키를 넣으면 수위·강수·수질·지하수 지점이 이 지도에 표시됩니다.</p>'
                '</section></div>'
                % (render_maptools(floods), sejong_map.render([])))

    listing = []
    for group in ("하천 수위", "강수량", "수질", "지하수"):
        members = [p for p in points if p["group"] == group]
        if not members:
            continue
        listing.append("<h3>%s <span style=\"opacity:.6\">%d</span></h3>"
                       % (esc(group), len(members)))
        for point in members:
            listing.append(
                '<button class="pick" data-id="%s" type="button">'
                '<span class="%s" style="background:%s"></span>'
                '<span class="nm">%s</span><span class="vv">%s</span></button>'
                % (point["id"], DOT_CLASS[point["kind"]], point["color"],
                   esc(point["name"]), esc(point["value"])))

    details = {p["id"]: p["detail"] for p in points}
    payload = json.dumps(details, ensure_ascii=False).replace("</", "<\\/")

    return ('<div class="explorer" data-view="explorer">'
            '<section class="panel side"><h2>관측지점</h2>'
            '<p class="note">클릭하면 지도와 오른쪽 상세가 함께 바뀝니다.</p>%s</section>'
            '<section class="panel mapwrap"><h2>세종시 지도'
            '<span class="src">휠 확대 · 끌어서 이동 · 하천에 커서</span></h2>%s%s</section>'
            '<section class="panel detail" id="detail"></section>'
            '</div>'
            '<script>(function(){var D=%s;'
            'var first=%s;'
            'function sel(id){if(!D[id])return;'
            'document.getElementById("detail").innerHTML=D[id];'
            'document.querySelectorAll(".pick").forEach(function(b){'
            'b.classList.toggle("sel",b.dataset.id===id)});'
            'document.querySelectorAll(".map .mk").forEach(function(m){'
            'm.classList.toggle("sel",m.dataset.id===id)});}'
            'document.querySelectorAll(".pick").forEach(function(b){'
            'b.addEventListener("click",function(){sel(b.dataset.id)})});'
            'document.querySelectorAll(".map .mk").forEach(function(m){'
            'm.addEventListener("click",function(){sel(m.dataset.id)});'
            'm.addEventListener("keydown",function(e){'
            'if(e.key==="Enter"||e.key===" "){e.preventDefault();sel(m.dataset.id)}})});'
            'sel(first);})();</script><script>%s</script>'
            % ("".join(listing), render_maptools(floods),
               sejong_map.render(points), payload,
               json.dumps(points[0]["id"]), ZOOM_JS))

WQ_COLUMNS = [("수온", "temp", 1, "℃"), ("pH", "ph", 2, ""), ("DO", "do", 2, ""),
              ("TOC", "toc", 1, ""), ("BOD", "bod", 1, ""), ("전기전도도", "cond", 0, ""),
              ("T-N", "tn", 2, ""), ("T-P", "tp", 3, "")]


def render_quality(nier: dict) -> str:
    points = nier.get("points") or []
    if not points:
        return '<div class="empty">실시간 수질 자료가 없습니다. 아래 오류를 확인해주세요.</div>'
    head = "".join("<th>%s%s</th>" % (esc(label), (" (%s)" % unit) if unit else "")
                   for label, _, _, unit in WQ_COLUMNS)
    rows = []
    for point in points:
        grade = point.get("grade")
        color = GRADE_COLORS.get(grade, NEUTRAL)
        cells = "".join("<td>%s</td>" % fmt(point.get(key), digits)
                        for _, key, digits, _ in WQ_COLUMNS)
        rows.append(
            '<tr><td>%s<br><span style="color:var(--muted);font-size:10.5px">%s</span></td>'
            '<td class="g" style="color:%s">%s<br>'
            '<span style="font-weight:400;font-size:10.5px">%s</span></td>%s</tr>'
            % (esc(point.get("name")), esc(hhmm(point.get("time"))),
               color, esc(grade or "—"), esc(point.get("grade_label") or ""), cells))
    return ('<div class="tablewrap"><table class="wq">'
            '<thead><tr><th>지점</th><th>등급</th>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (head, "".join(rows)))


def render_weather(kma: dict) -> str:
    now = kma.get("now") or {}
    forecast = kma.get("forecast") or {}
    grid = kma.get("grid") or {}
    items = [("기온", fmt(now.get("temp"), 1, " ℃")),
             ("1시간 강수", fmt(now.get("rain_1h"), 1, " mm")),
             ("습도", fmt(now.get("humidity"), 0, " %")),
             ("풍속", fmt(now.get("wind"), 1, " m/s")),
             ("강수형태", esc(now.get("pty") or "—"))]
    head = "".join('<div class="item"><div class="l">%s</div><div class="v">%s</div></div>'
                   % (label, value) for label, value in items)
    slots = forecast.get("slots") or []
    chart = ('<div class="note" style="margin:12px 0 4px">시간별 강수량(mm) · 발표 %s · '
             '격자 nx %s, ny %s</div>%s'
             '<div class="note" style="margin:10px 0 4px">시간별 강수확률(%%)</div>%s'
             % (esc(forecast.get("base") or "—"), esc(grid.get("nx")), esc(grid.get("ny")),
                bar_chart(slots, "pcp", ACCENT, unit="mm"),
                bar_chart(slots, "pop", "var(--accent-2)", unit="%", max_hint=100)))
    base_note = '<div class="note">실황 기준 %s</div>' % esc(now.get("base") or "—")
    return base_note + '<div class="wx">%s</div>' % head + chart


# 소스별 원 제공주기(분)와, 이 정도 지나면 오래된 자료로 볼 기준(분).
# 기준은 제공주기의 3배 남짓 — 한두 번 걸러도 경고가 뜨지 않게.
SOURCE_CADENCE = {"waterlevel": 10, "rainfall": 10, "quality": 30 * 1440,
                  "groundwater": 60, "weather": 60}
STALE_LIMIT = {"waterlevel": 30, "rainfall": 30, "quality": 60 * 1440,
               "groundwater": 240, "weather": 120}


def _parse_stamp(text):
    """YYYYMMDDHHMM / YYYYMMDD / 'YYYY-MM-DD HH:MM' 을 datetime 으로."""
    text = str(text or "").strip()
    for fmt, size in (("%Y%m%d%H%M", 12), ("%Y-%m-%d %H:%M", 16),
                      ("%Y%m%d%H", 10), ("%Y%m%d", 8)):
        if len(text) >= size:
            try:
                return datetime.strptime(text[:size], fmt).replace(tzinfo=KST)
            except ValueError:
                continue
    return None


def _latest_stamp(values):
    stamps = [_parse_stamp(v) for v in values]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def freshness(data: dict) -> list:
    """소스별 최신 관측시각과 경과 시간. '언제 자료인지' 를 화면에 드러내기 위한 것."""
    hrfco = data.get("hrfco") or {}
    nier = data.get("nier") or {}
    gims = data.get("gims") or {}
    kma = data.get("kma") or {}

    rows = [
        ("하천 수위", "waterlevel", _latest_stamp(
            [(e.get("latest") or {}).get("t") for e in hrfco.get("waterlevel") or []]),
         len(hrfco.get("waterlevel") or [])),
        ("강수량", "rainfall", _latest_stamp(
            [(e.get("latest") or {}).get("t") for e in hrfco.get("rainfall") or []]),
         len(hrfco.get("rainfall") or [])),
        ("수질", "quality", _latest_stamp(
            [p.get("time") for p in nier.get("points") or []]),
         len(nier.get("points") or [])),
        ("지하수", "groundwater", _latest_stamp(
            [(e.get("latest") or {}).get("t") for e in gims.get("stations") or []]),
         len(gims.get("stations") or [])),
        ("기상", "weather", _parse_stamp((kma.get("now") or {}).get("base")),
         1 if kma.get("now") else 0),
    ]

    now = _parse_stamp(data.get("generated_at")) or now_kst()
    out = []
    for label, key, observed, count in rows:
        if not count:
            out.append({"label": label, "text": "자료없음", "age": "",
                        "color": NEUTRAL})
            continue
        if not observed:
            out.append({"label": label, "text": "시각미상", "age": "%d개소" % count,
                        "color": "#d29922"})
            continue
        minutes = max(0, int((now - observed).total_seconds() // 60))
        limit = STALE_LIMIT.get(key, 120)
        if minutes <= limit:
            color = "#3fb950"
        elif minutes <= limit * 3:
            color = "#d29922"
        else:
            color = WARN
        if minutes < 120:
            age = "%d분 전" % minutes
        elif minutes < 48 * 60:
            age = "%.1f시간 전" % (minutes / 60)
        else:
            age = "%d일 전" % (minutes // 1440)
        out.append({"label": label, "text": hhmm(observed.strftime("%Y%m%d%H%M")),
                    "age": "%s · %d개소" % (age, count), "color": color,
                    "cadence": SOURCE_CADENCE.get(key)})
    return out


def _cadence_text(minutes) -> str:
    if not minutes:
        return ""
    if minutes >= 1440:
        return "원 제공주기 약 %d일" % (minutes // 1440)
    if minutes >= 60:
        return "원 제공주기 %d시간" % (minutes // 60)
    return "원 제공주기 %d분" % minutes


# 화면에 켜고 끌 수 있는 블록. 선택은 브라우저에 기억된다.
VIEW_SECTIONS = [
    ("kpi", "요약 지표"),
    ("fresh", "관측시각"),
    ("explorer", "지점 탐색"),
    ("quality", "수질 전 지점"),
    ("weather", "기상"),
    ("pollution", "오염원 통계"),
]

VIEW_JS = r"""
(function(){
  var KEY='sw-views';
  var saved={};
  try{ saved=JSON.parse(localStorage.getItem(KEY)||'{}'); }catch(e){}

  function setOn(id,on){
    document.querySelectorAll('[data-view="'+id+'"]').forEach(function(el){
      el.style.display = on ? '' : 'none'; });
    document.querySelectorAll('.vw[data-view-toggle="'+id+'"]').forEach(function(b){
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on)); });
  }
  document.querySelectorAll('.vw').forEach(function(b){
    var id=b.dataset.viewToggle;
    var on = saved[id] !== false;          // 저장값이 없으면 켜둔다
    setOn(id, on);
    b.addEventListener('click', function(){
      var next = !b.classList.contains('on');
      setOn(id, next);
      saved[id]=next;
      try{ localStorage.setItem(KEY, JSON.stringify(saved)); }catch(e){}
      window.dispatchEvent(new Event('resize'));
    });
  });
})();
"""


def render_viewbar() -> str:
    chips = "".join(
        '<button type="button" class="vw on" data-view-toggle="%s" '
        'aria-pressed="true">%s</button>' % (key, esc(label))
        for key, label in VIEW_SECTIONS)
    return ('<div class="viewbar"><span class="vw-label">보기</span>%s</div>' % chips)


def render_freshness(data: dict) -> str:
    chips = "".join(
        '<div class="fr" style="--c:%s" title="%s"><span class="l">%s</span>'
        '<span class="t">%s</span><span class="a">%s</span></div>'
        % (row["color"],
           ("원 제공주기 %d분" % row["cadence"]) if row.get("cadence") else "",
           esc(row["label"]), esc(row["text"]), esc(row["age"]))
        for row in freshness(data))
    return ('<div class="freshbar" data-view="fresh">'
            '<span class="fresh-title">관측시각</span>%s'
            '<span class="fresh-note">API 응답에 담긴 관측시각입니다(수집 시각 아님). '
            '하천·강수 10분, 지하수·기상 1시간, 수질은 월 1~2회 정기측정입니다.'
            '</span></div>' % chips)


COUNTER_JS = r"""
(function(){
  var box=document.getElementById('visitors'); if(!box) return;
  var cfg=JSON.parse(box.dataset.counter||'{}');
  var today=document.getElementById('cnt-today'), total=document.getElementById('cnt-total');
  function put(el,v){ if(el) el.textContent = (v===null||v===undefined) ? '—'
      : Number(v).toLocaleString('ko-KR'); }
  function num(x){ if(x===null||x===undefined) return null;
    if(typeof x==='object') x = x.count!==undefined ? x.count : x.total;
    var n=parseInt(String(x).replace(/[^0-9]/g,''),10); return isNaN(n)?null:n; }
  function get(url){                    // 응답이 없으면 … 로 멈춰 있지 않게 끊는다
    var ctrl = (typeof AbortController!=='undefined') ? new AbortController() : null;
    if(ctrl) setTimeout(function(){ try{ctrl.abort();}catch(e){} }, 6000);
    return fetch(url,{mode:'cors', signal: ctrl?ctrl.signal:undefined})
      .then(function(r){ if(!r.ok) throw 0; return r.json(); }); }

  if(cfg.provider==='goatcounter' && cfg.code){
    var base='https://'+cfg.code+'.goatcounter.com/counter/'+encodeURIComponent(cfg.path||'/')+'.json';
    get(base).then(function(d){ put(total,num(d)); }).catch(function(){ put(total,null); });
    get(base+'?start=today').then(function(d){ put(today,num(d)); })
      .catch(function(){ put(today,null); });
  } else if(cfg.provider==='custom' && cfg.url){
    get(cfg.url).then(function(d){ put(today,num(d.today)); put(total,num(d.total)); })
      .catch(function(){ put(today,null); put(total,null); });
  }
})();
"""


DEFAULT_CONTACT = {"org": "세종연구원", "name": "송양호",
                   "title": "책임연구위원", "email": "ysong@sri.re.kr"}


def render_contact(site: dict) -> str:
    """제작자와 문의처. site.contact 로 바꿀 수 있다."""
    contact = {**DEFAULT_CONTACT, **((site or {}).get("contact") or {})}
    who = " ".join(x for x in [contact.get("org"), contact.get("name"),
                               contact.get("title")] if x)
    if not who and not contact.get("email"):
        return ""

    lines = []
    if who:
        role = contact.get("role") or "제작"
        lines.append('<div class="made"><span class="k">%s</span>%s</div>'
                     % (esc(role), esc(who)))
    if contact.get("email"):
        lines.append('<div class="ask"><span class="k">문의</span>'
                     '<a href="mailto:%s">%s</a> 로 연락 주시기 바랍니다.</div>'
                     % (esc(contact["email"]), esc(contact["email"])))
    return '<div class="contact">%s</div>' % "".join(lines)


def render_counter(site: dict) -> str:
    """하단 조회수.

    정적 페이지는 스스로 셀 수 없어 세어 주는 곳이 필요하다. provider 로 고른다.
      hits        — hits.sh 배지. 가입이 필요 없어 바로 뜬다. CORS 가 없어
                    숫자만 뽑아 우리 서체로 다시 그릴 수는 없고 이미지로 박는다.
                    새로고침마다 올라가므로 방문자 수가 아니라 조회수다.
      custom      — 직접 띄운 집계기(tools/counter-worker.js)가 {today,total} 반환.
                    투데이/토탈을 정확히 통제하고 싶을 때.
      goatcounter — GoatCounter 계정 사용.
      none        — 표시하지 않음.
    """
    counter = (site or {}).get("counter") or {}
    provider = counter.get("provider", "none")
    if provider in ("", "none"):
        return ""

    label = counter.get("label") or "조회수"

    if provider == "hits":
        target = (counter.get("target") or "").strip()
        if not target:
            return ""
        src = ("https://hits.sh/%s.svg?view=today-total&style=flat-square"
               "&label=%s&color=0ea5e9&labelColor=64748b"
               % (urllib.parse.quote(target, safe="/"),
                  urllib.parse.quote(label)))
        return ('<div class="visitors"><img class="hitsbadge" src="%s" '
                'alt="%s 오늘/전체" height="20" loading="lazy">'
                '<span class="vsub">오늘 / 전체</span></div>' % (esc(src), esc(label)))

    payload = json.dumps({k: v for k, v in counter.items() if k != "provider"}
                         | {"provider": provider}, ensure_ascii=False)
    return ('<div class="visitors" id="visitors" data-counter=%s>'
            '<span class="vk">%s</span>'
            '<span>오늘</span><b id="cnt-today">…</b>'
            '<span>누적</span><b id="cnt-total">…</b></div>'
            '<script>%s</script>'
            % (json.dumps(payload), esc(label), COUNTER_JS))


THEME_BOOT = ("<script>(function(){try{var t=localStorage.getItem('sw-theme');"
              "document.documentElement.setAttribute('data-theme',"
              "t==='dark'?'dark':'light');}catch(e){}})();</script>")

THEME_TOGGLE = ('<div class="theme-toggle" role="group" aria-label="화면 테마">'
                '<button type="button" data-set-theme="light" aria-pressed="true">'
                '라이트</button>'
                '<button type="button" data-set-theme="dark" aria-pressed="false">'
                '다크</button></div>')

# 시스템이 다크여도 자동으로 어두워지지 않는다. 기본은 라이트, 선택만 기억한다.
THEME_JS = r"""
(function(){
  var root=document.documentElement;
  function apply(t){
    root.setAttribute('data-theme', t);
    document.querySelectorAll('[data-set-theme]').forEach(function(b){
      b.setAttribute('aria-pressed', String(b.dataset.setTheme===t)); });
    try{ localStorage.setItem('sw-theme', t); }catch(e){}
  }
  document.querySelectorAll('[data-set-theme]').forEach(function(b){
    b.addEventListener('click', function(){ apply(b.dataset.setTheme); }); });
  apply(root.getAttribute('data-theme')==='dark' ? 'dark' : 'light');
})();
"""


def has_any_data(data: dict) -> bool:
    """어느 소스든 실제 값이 하나라도 들어왔는가."""
    hrfco = data.get("hrfco") or {}
    return bool((hrfco.get("waterlevel") or []) or (hrfco.get("rainfall") or [])
                or ((data.get("nier") or {}).get("points") or [])
                or ((data.get("gims") or {}).get("stations") or [])
                or ((data.get("kma") or {}).get("now")))


def render_sources(data: dict) -> str:
    """어떤 주소에서 받아온 값인지 남긴다. 수치를 검증할 때 이게 있어야 한다."""
    used = []
    for key, label in (("nier", "수질"), ("gims", "지하수")):
        endpoint = (data.get(key) or {}).get("endpoint")
        if endpoint:
            used.append("%s %s" % (label, esc(endpoint)))
    if not used:
        return ""
    return '<br>사용 엔드포인트 — ' + ' · '.join(used)


def render_pollution(data: dict) -> str:
    """세종시 수질오염원 현황(연 단위 통계). 실시간 값과 성격이 달라 기준연도를 밝힌다."""
    stat = data.get("nierstat") or {}
    blocks = [b for b in (stat.get("blocks") or []) if b.get("rows")]
    if not blocks:
        return ('<div class="empty">오염원 통계가 없습니다. 공공데이터포털에서 '
                '「국립환경과학원_시군구 통계 서비스」를 활용신청하면 채워집니다.</div>')

    cards = []
    for block in blocks:
        rows = "".join(
            '<div class="q"><div class="l">%s</div><div class="v">%s</div></div>'
            % (esc(row["label"]), fmt(row["value"], row["digits"], " " + row["unit"]))
            for row in block["rows"])
        cards.append('<div class="sect" style="margin-top:0;padding-top:0;border:0">'
                     '<h4>%s <span style="font-weight:400;text-transform:none">'
                     '%s년 기준</span></h4><div class="qgrid">%s</div></div>'
                     % (esc(block["label"]), esc(block.get("year") or "—"), rows))
    return "".join(cards)


def render_errors(data: dict) -> str:
    buckets = [("하천/강수(HRFCO)", (data.get("hrfco") or {}).get("errors") or []),
               ("실시간 수질(국립환경과학원)", (data.get("nier") or {}).get("errors") or []),
               ("기상(기상청)", (data.get("kma") or {}).get("errors") or [])]
    lines = []
    for source, errors in buckets:
        for error in errors:
            lines.append("<li><b>%s</b> — %s</li>" % (esc(source), esc(error)))
    if not lines:
        return ""
    return ('<div class="errs"><b>수집 경고</b><ul style="margin:6px 0 0;padding-left:18px">'
            '%s</ul></div>' % "".join(lines))


def render(data: dict) -> str:
    generated = data.get("generated_at") or stamp()
    demo = bool(data.get("demo"))
    kpis = "".join(
        '<div class="kpi" style="--c:%s"><div class="l">%s</div>'
        '<div class="v">%s</div><div class="s">%s</div></div>'
        % (k["color"], esc(k["label"]), esc(k["value"]), esc(k["sub"])) for k in summarize(data))

    warnings = (data.get("kma") or {}).get("warnings") or []
    alerts = ""
    if warnings:
        items = "".join("<li>%s <span style=\"opacity:.7\">(%s)</span></li>"
                        % (esc(w.get("title")), esc(w.get("time"))) for w in warnings)
        alerts = ('<div class="alerts"><h3>기상특보</h3>'
                  '<ul style="margin:0;padding-left:18px">%s</ul></div>' % items)

    if demo:
        banner = '<span class="badge demo">샘플 데이터</span>'
    elif has_any_data(data):
        banner = '<span class="badge live">실측 연동</span>'
    else:
        # 키가 없거나 전 소스가 실패한 상태. "실측 연동" 이라고 쓰면 거짓말이 된다.
        banner = '<span class="badge demo">자료 없음</span>'

    demo_note = ""
    if not demo and not has_any_data(data):
        demo_note = ('<div class="alerts warn"><h3>아직 수집된 자료가 없습니다</h3>'
                     '<div>API 인증키가 설정되지 않았거나 모든 소스가 실패했습니다. '
                     '아래 <b>수집 경고</b>에 소스별 사유가 적혀 있습니다. '
                     'GitHub 저장소의 Settings → Secrets and variables → Actions 에 '
                     '<code>HRFCO_KEY</code> <code>KMA_KEY</code> <code>NIER_KEY</code> '
                     '<code>GIMS_KEY</code> 를 넣고 워크플로를 다시 실행하십시오.</div></div>')
    if demo:
        demo_note = ('<div class="alerts warn">'
                     '<h3>이 화면의 수치는 실제 관측값이 아닙니다</h3>'
                     '<div>API 키를 <code>config.json</code> 에 넣고 '
                     '<code>python run.py --collect</code> 를 실행하면 실측으로 바뀝니다.'
                     '</div></div>')

    nier, kma = (data.get("nier") or {}, data.get("kma") or {})

    return """<!doctype html>
<html lang="ko" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>세종시 물환경 상황판</title>
%s
<style>%s</style>%s</head><body><div class="wrap">
<header class="top">
  <h1>세종시 물환경 상황판</h1>
  %s
  <span class="grow"></span>
  <span class="sub">갱신 %s</span>
  %s
</header>
%s%s
%s
<div class="kpis" data-view="kpi">%s</div>
%s
%s
<div class="grid">
  <section class="panel" data-view="quality"><h2>실시간 수질 전 지점<span class="src">물환경정보시스템</span></h2>
    <p class="note">등급은 하천 생활환경기준(TOC) 기준으로 산출한 참고값입니다.</p>
    %s</section>
  <section class="panel" data-view="weather"><h2>기상<span class="src">기상청 단기예보</span></h2>
    %s</section>
  <section class="panel" data-view="pollution"><h2>세종시 수질오염원
    <span class="src">국립환경과학원 시군구 통계</span></h2>
    <p class="note">연 단위 통계입니다. 실시간 값이 아닙니다.</p>
    %s</section>
</div>
%s
<footer>
  출처 — 한강홍수통제소 오픈API(수위·강수), 기상청 단기예보 조회서비스(실황·예보),
  국립환경과학원 물환경정보시스템(실시간 수질).<br>
  지도의 행정구역 경계·하천수계·관측지점은 모두 실제 좌표입니다.
  하천망 © OpenStreetMap 기여자 (ODbL).<br>
  수위·강수 자료는 보정 전 원시자료입니다. 실제 홍수 대응 판단은 한강홍수통제소 공식 발표를 따르십시오.<br>
  마지막 갱신 %s · 세종특별자치시 물환경 상황판%s
  %s
  %s
</footer>
</div><script>%s</script></body></html>""" % (
        THEME_BOOT, load_css(), FONT_LINK, banner, esc(generated), THEME_TOGGLE, demo_note, alerts, render_viewbar(), kpis,
        render_freshness(data),
        render_explorer(build_points(data)),
        render_quality(nier), render_weather(kma), render_pollution(data),
        render_errors(data), esc(generated), render_sources(data),
        render_contact(data.get("site")), render_counter(data.get("site")),
        THEME_JS + VIEW_JS)


def write(data: dict, path: str = OUT_PATH) -> str:
    data.setdefault("generated_at", stamp(now_kst()))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(render(data))
    return path
