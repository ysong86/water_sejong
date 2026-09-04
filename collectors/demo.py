# -*- coding: utf-8 -*-
"""API 키 없이 화면을 확인하기 위한 샘플 데이터.

실제 관측값이 아니다. 생성물에는 항상 demo=True 가 붙고 대시보드 상단에 경고가 뜬다.
지점명·홍수단계 수위는 세종 구간의 실재 관측소를 본떴으나 수치는 합성값이다.
"""
from __future__ import annotations

import math
import random
from datetime import timedelta

from . import nemc, selfsuff
from .common import now_kst

SEED = 20260828

_WL_STATIONS = [
    # 이름, 코드, 관심/주의/경계/심각(m), 기준수위, 경도, 위도
    ("금남교(금강)", "3011665", 3.5, 5.0, 7.0, 8.5, 2.6, 127.2880, 36.4880),
    ("세종보(금강)", "3011660", 3.0, 4.5, 6.0, 7.5, 2.1, 127.2555, 36.5022),
    ("미호천교(미호강)", "3009665", 4.0, 6.0, 7.5, 9.0, 3.2, 127.3083, 36.5203),
    ("조치원교(조천)", "3009670", 2.5, 3.5, 4.5, 5.5, 1.4, 127.2966, 36.5975),
]
_RF_STATIONS = [("세종(연기)", "30096010", 12.5, 127.2596, 36.5184),
                ("조치원", "30096020", 8.0, 127.2966, 36.6006),
                ("전의", "30096030", 15.5, 127.1878, 36.6867)]
# 이름, 코드, 경도, 위도, 관측망, 기준 지하수위(EL.m)
_GW_STATIONS = [
    ("세종연기", "95508", 127.2650, 36.5320, "national", 24.8),
    ("세종금남", "95509", 127.2950, 36.4620, "national", 21.3),
    ("세종전의", "SJ-01", 127.1930, 36.6790, "local", 46.2),
    ("세종부강", "SJ-04", 127.3760, 36.5210, "local", 18.7),
]
_WQ_POINTS = [("금강_세종", "3008A40", 127.2870, 36.4980),
              ("금강_대교천합류후", "3008A45", 127.2450, 36.5060),
              ("미호강_합강", "3009A10", 127.3180, 36.5090)]


def _series(rng, hours, base, amp, step_minutes=60):
    """step_minutes 간격 시계열. 하천·강수는 실제와 같이 10분 간격으로 만든다."""
    now = now_kst()
    end = now.replace(minute=(now.minute // step_minutes) * step_minutes
                      if step_minutes < 60 else 0, second=0, microsecond=0)
    steps = max(1, hours * 60 // step_minutes)
    out = []
    for i in range(steps, -1, -1):
        ts = end - timedelta(minutes=i * step_minutes)
        wave = amp * math.sin((steps - i) / (steps / 4.8))             + rng.uniform(-amp * 0.15, amp * 0.15)
        out.append({"t": ts.strftime("%Y%m%d%H%M"), "v": round(base + wave, 2)})
    return out


def build(hours: int = 24) -> dict:
    rng = random.Random(SEED)
    now = now_kst()

    waterlevel = []
    for name, code, att, wrn, alm, srs, base, lon, lat in _WL_STATIONS:
        series = _series(rng, hours, base, 0.35, step_minutes=10)
        latest = series[-1]
        values = [p["v"] for p in series]
        stage = "normal"
        for threshold, label in ((srs, "serious"), (alm, "alert"),
                                 (wrn, "warning"), (att, "watch")):
            if latest["v"] >= threshold:
                stage = label
                break
        labels = {"normal": "관심 이하", "watch": "관심", "warning": "주의",
                  "alert": "경계", "serious": "심각"}
        waterlevel.append({
            "code": code, "name": name, "agency": "환경부",
            "addr": "세종특별자치시", "lat": lat, "lon": lon,
            "attwl": att, "wrnwl": wrn,
            "almwl": alm, "srswl": srs, "series": series, "latest": latest,
            "stage": stage, "stage_label": labels[stage],
            "delta_1h": round(values[-1] - values[-7], 3),   # 10분 간격 6칸 = 1시간
        })

    rainfall = []
    for name, code, total, lon, lat in _RF_STATIONS:
        # 10분 강수량이므로 시간당 값을 6등분한 규모로 만든다
        per_slot = total / (hours * 6)
        series = [{"t": p["t"], "v": max(0.0, round(p["v"], 1))}
                  for p in _series(rng, hours, per_slot, per_slot * 1.6,
                                   step_minutes=10)]
        rainfall.append({
            "code": code, "name": name, "addr": "세종특별자치시",
            "lat": lat, "lon": lon, "series": series, "latest": series[-1],
            "sum_24h": round(sum(p["v"] for p in series), 1),
        })

    points = []
    for name, code, lon, lat in _WQ_POINTS:
        toc = round(rng.uniform(2.2, 5.4), 1)
        grade = next((g for lim, g, _ in
                      [(2.0, "Ia", 0), (3.0, "Ib", 0), (4.0, "II", 0),
                       (5.0, "III", 0), (6.0, "IV", 0), (8.0, "V", 0)]
                      if toc <= lim), "VI")
        labels = {"Ia": "매우 좋음", "Ib": "좋음", "II": "약간 좋음",
                  "III": "보통", "IV": "약간 나쁨", "V": "나쁨", "VI": "매우 나쁨"}
        points.append({
            "code": code, "name": name, "lat": lat, "lon": lon,
            "time": now.strftime("%Y%m%d%H%M"),
            "temp": round(rng.uniform(22.0, 27.5), 1),
            "ph": round(rng.uniform(6.9, 8.4), 2),
            "do": round(rng.uniform(6.2, 9.8), 2),
            "toc": toc, "bod": round(rng.uniform(1.0, 3.4), 1),
            "cond": round(rng.uniform(180, 420), 1),
            "tn": round(rng.uniform(1.8, 4.6), 2),
            "tp": round(rng.uniform(0.02, 0.14), 3),
            "ss": round(rng.uniform(3.0, 22.0), 1),
            "chla": round(rng.uniform(5.0, 38.0), 1),
            "grade": grade, "grade_label": labels[grade],
        })

    groundwater = []
    for name, code, lon, lat, network, base in _GW_STATIONS:
        series = _series(rng, hours, base, 0.16)
        values = [p["v"] for p in series]
        groundwater.append({
            "code": code, "name": name, "network": network,
            "network_label": {"national": "국가", "local": "지자체"}[network],
            "lat": lat, "lon": lon, "addr": "세종특별자치시",
            "series": series, "latest": series[-1],
            "temp": round(rng.uniform(13.5, 16.5), 1),
            "cond": round(rng.uniform(120, 480), 1),
            "depth": round(rng.uniform(4.0, 12.0), 2),
            "delta_24h": round(values[-1] - values[0], 3),
        })

    slots = []
    cursor = now.replace(minute=0, second=0, microsecond=0)
    for i in range(24):
        ts = cursor + timedelta(hours=i + 1)
        pop = rng.choice([0, 0, 10, 20, 30, 60, 80])
        pcp = round(rng.uniform(0.5, 7.0), 1) if pop >= 60 else 0.0
        slots.append({
            "t": ts.strftime("%Y%m%d%H%M"), "pop": pop, "pcp": pcp,
            "pcp_text": f"{pcp}mm" if pcp else "없음",
            "temp": round(24 + 5 * math.sin(i / 4.0), 1),
            "sky": rng.choice(["맑음", "구름많음", "흐림"]),
            "pty": "비" if pcp else "없음",
        })

    return {
        "demo": True,
        "hrfco": {"waterlevel": waterlevel, "rainfall": rainfall, "errors": []},
        "nier": {"points": points, "endpoint": "(샘플)", "errors": [], "filtered": True},
        "gims": {"stations": groundwater, "endpoint": "(샘플)", "errors": []},
        "kma": {
            "grid": {"nx": 66, "ny": 103, "lat": 36.48, "lon": 127.289},
            "now": {"base": now.strftime("%Y-%m-%d %H:00"),
                    "temp": 26.4, "rain_1h": 0.0, "humidity": 74.0,
                    "wind": 1.8, "pty": "없음"},
            "forecast": {"base": now.strftime("%Y-%m-%d %H:00"), "slots": slots,
                         "rain_sum": round(sum(s["pcp"] for s in slots), 1),
                         "pop_max": max(s["pop"] for s in slots)},
            "warnings": [], "errors": [],
        },
        # 자족도시 지표는 표본 파일(data/selfsuff.example.json)로 화면만 보여준다.
        "selfsuff": selfsuff.collect({"use_example": True}),
        "nemc": _medical(now),
    }


# 응급의료 표본. 기관명은 세종 실재 기관을 본떴으나 병상 수는 합성값이다.
_ER_SAMPLE = [
    ("세종충남대학교병원", "044-995-4000", 14, 40, 62, 5, 9, 3),
    ("세종특별자치시 세종의료원", "044-260-1000", 6, 18, 31, 2, 4, None),
    ("엔케이세종병원", "044-865-1000", 1, 12, 8, 1, None, None),
]

_PHARMACY_SAMPLE = [
    ("보람온누리약국", "세종특별자치시 보람동", "0900", "2400"),
    ("도담365약국", "세종특별자치시 도담동", "0000", "2400"),
    ("아름메디컬약국", "세종특별자치시 아름동", "0900", "1900"),
    ("조치원행복약국", "세종특별자치시 조치원읍", "0830", "1830"),
    ("새롬중앙약국", "세종특별자치시 새롬동", "0900", "2200"),
    ("한솔온약국", "세종특별자치시 한솔동", "1000", "1700"),
]

_PEDIATRIC_SAMPLE = [
    ("세종아이사랑소아청소년과의원", "세종특별자치시 새롬동", "0900", "2100"),
    ("도담소아청소년과의원", "세종특별자치시 도담동", "0900", "1800"),
    ("고운튼튼소아청소년과의원", "세종특별자치시 고운동", "0930", "1830"),
    ("조치원소아청소년과의원", "세종특별자치시 조치원읍", "0900", "1730"),
]


def _hours(rows, when):
    """표본 목록을 API 응답과 같은 꼴(dutyTimeNs/Nc)로 바꾼다."""
    slot = nemc._WEEKDAY_FIELD[when.weekday()]
    out = []
    for name, addr, start, close in rows:
        out.append({"dutyName": name, "dutyAddr": addr,
                    "dutyTime%ds" % slot: start, "dutyTime%dc" % slot: close})
    return out


def _medical(now):
    beds = [("hvec", "hvs01", "응급실 일반"), ("hvgc", None, "입원실"),
            ("hvoc", None, "수술실"), ("hvicc", None, "일반 중환자"),
            ("hvncc", None, "신생아 중환자")]
    rows = []
    for name, tel, free, total, ward, surgery, icu, nicu in _ER_SAMPLE:
        row = {"hpid": name, "dutyName": name, "dutyTel3": tel,
               "hvidate": (now - timedelta(minutes=8)).strftime("%Y%m%d%H%M00"),
               "hvec": free, "hvs01": total, "hvgc": ward, "hvoc": surgery}
        if icu is not None:
            row["hvicc"] = icu
        if nicu is not None:
            row["hvncc"] = nicu
        rows.append(row)

    result = {"sido": "세종특별자치시", "errors": [],
              "er": nemc._er(rows, {}, beds)}
    result["observed"] = max(e["observed"] for e in result["er"])
    result["pharmacy"] = nemc._open_list(_hours(_PHARMACY_SAMPLE, now), now)
    result["pediatric"] = nemc._open_list(_hours(_PEDIATRIC_SAMPLE, now), now)
    result["pediatric"]["keywords"] = ["소아청소년"]
    return result
