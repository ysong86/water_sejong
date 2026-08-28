/**
 * 방문자 수 카운터 (Cloudflare Workers + KV) — 투데이 / 토탈
 *
 * 상황판은 정적 HTML 이라 스스로 방문자를 셀 수 없다. 이 워커가 세어 주고,
 * 상황판은 config 의 site.counter = {"provider":"custom","url":"<워커주소>"} 로 읽어 온다.
 *
 * 세는 방식 — 환경변수 COUNT_MODE 로 고른다.
 *   hits (기본)   : 새로고침 포함 모든 방문을 센다. 곧 조회수이므로 화면 라벨도
 *                   "조회수" 로 맞춰 두었다 (config 의 site.counter.label).
 *   unique        : 같은 사람이 같은 날 여러 번 들어와도 1. IP + User-Agent 를 해시해
 *                   하루짜리 표시를 KV 에 남기고, 표시가 없을 때만 증가시킨다.
 *                   원본 IP 는 저장하지 않는다(해시만 남는다).
 *                   이쪽으로 바꾸면 라벨도 "방문자 수" 로 되돌릴 것.
 *
 * 배포 (5분)
 *   1) dash.cloudflare.com → Workers & Pages → Create → Worker → 이 파일 내용 붙여넣기
 *   2) Settings → Bindings → KV namespace 추가:  변수명 COUNTER
 *   3) Settings → Variables → ALLOW_ORIGIN 에 상황판 주소
 *      (예: https://<아이디>.github.io) — 비워두면 모든 출처 허용
 *   4) 배포된 주소를 config.json 의 site.counter.url 에 넣고 run.py --collect
 *
 * 응답:  {"today": 12, "total": 3456}
 */

const DAY_SECONDS = 90000; // 25시간 — 자정 경계에서 표시가 먼저 사라지지 않게 여유

function kstDate(now) {
  return new Date(now.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

async function visitorHash(request, day) {
  const ip = request.headers.get('CF-Connecting-IP') || '';
  const ua = request.headers.get('User-Agent') || '';
  const bytes = new TextEncoder().encode(`${day}|${ip}|${ua}`);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].slice(0, 12)
    .map((b) => b.toString(16).padStart(2, '0')).join('');
}

export default {
  async fetch(request, env) {
    const origin = env.ALLOW_ORIGIN || '*';
    const headers = {
      'Access-Control-Allow-Origin': origin,
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    };
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: { ...headers, 'Access-Control-Allow-Methods': 'GET, OPTIONS' },
      });
    }
    if (!env.COUNTER) {
      return new Response(JSON.stringify({ error: 'KV binding COUNTER 가 없습니다' }),
        { status: 500, headers });
    }

    const day = kstDate(new Date());
    const dayKey = `d:${day}`;
    const url = new URL(request.url);
    const peek = url.searchParams.get('peek') === '1'; // 증가 없이 조회만

    const mode = (env.COUNT_MODE || 'hits').toLowerCase();

    if (!peek) {
      let shouldCount = true;
      if (mode !== 'hits') {
        // 하루 한 번만. 표시가 이미 있으면 같은 사람의 재방문으로 본다.
        const mark = `v:${day}:${await visitorHash(request, day)}`;
        if (await env.COUNTER.get(mark)) {
          shouldCount = false;
        } else {
          await env.COUNTER.put(mark, '1', { expirationTtl: DAY_SECONDS });
        }
      }
      if (shouldCount) {
        const [todayRaw, totalRaw] = await Promise.all([
          env.COUNTER.get(dayKey),
          env.COUNTER.get('total'),
        ]);
        await Promise.all([
          env.COUNTER.put(dayKey, String((parseInt(todayRaw, 10) || 0) + 1),
            { expirationTtl: DAY_SECONDS * 4 }),
          env.COUNTER.put('total', String((parseInt(totalRaw, 10) || 0) + 1)),
        ]);
      }
    }

    const [todayRaw, totalRaw] = await Promise.all([
      env.COUNTER.get(dayKey),
      env.COUNTER.get('total'),
    ]);
    return new Response(JSON.stringify({
      today: parseInt(todayRaw, 10) || 0,
      total: parseInt(totalRaw, 10) || 0,
      date: day,
      mode,
    }), { headers });
  },
};
