const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function cors(response, status = 200) {
  return new Response(response, { status, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' } });
}

const ACTIVE_ID_FILTER = `anonymous_id IN (
  SELECT anonymous_id
  FROM events
  WHERE tool <> 'test_tool'
  GROUP BY anonymous_id
  HAVING COUNT(*) >= 2
)`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // POST /api/telemetry — 遥测事件写入
    if (path === '/api/telemetry' && request.method === 'POST') {
      try {
        const body = await request.json();
        const events = Array.isArray(body) ? body : [body];

        if (events.some(e => e.tool === 'test_tool')) {
          return cors(JSON.stringify({ ok: false, error: 'test_tool is not accepted' }), 400);
        }

        const stmt = env.DB.prepare(
          `INSERT INTO events (ts, anonymous_id, platform, siyuan_ver, mcp_ver, session_id, tool, action, ok, error_type, dur_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
        );

        const batch = events.map(e =>
          stmt.bind(
            e.ts, e.anonymous_id, e.platform || null, e.siyuan_ver || null,
            e.mcp_ver || null, e.session_id || null, e.tool, e.action || null,
            e.ok ? 1 : 0, e.error_type || null, e.dur_ms || null
          )
        );

        await env.DB.batch(batch);
        return cors(JSON.stringify({ ok: true, count: events.length }));
      } catch (e) {
        return cors(JSON.stringify({ ok: false, error: 'invalid payload' }), 400);
      }
    }

    // POST /api/feedback — 用户反馈写入
    if (path === '/api/feedback' && request.method === 'POST') {
      try {
        const body = await request.json();
        if (!body.type || !body.title || !body.description) {
          return cors(JSON.stringify({ ok: false, error: 'missing required fields' }), 400);
        }

        await env.DB.prepare(
          `INSERT INTO feedbacks (ts, type, title, description, contact)
           VALUES (?, ?, ?, ?, ?)`
        ).bind(
          new Date().toISOString(), body.type, body.title, body.description, body.contact || null
        ).run();

        return cors(JSON.stringify({ ok: true }));
      } catch (e) {
        return cors(JSON.stringify({ ok: false, error: 'invalid payload' }), 400);
      }
    }

    // GET /api/notifications — 通知列表
    if (path === '/api/notifications' && request.method === 'GET') {
      try {
        const { results } = await env.DB.prepare(
          `SELECT id, title, url FROM notifications ORDER BY created_at DESC`
        ).all();

        return cors(JSON.stringify({ notifications: results }));
      } catch (e) {
        return cors(JSON.stringify({ notifications: [] }));
      }
    }

    // GET /api/dashboard — 遥测统计看板
    if (path === '/api/dashboard' && request.method === 'GET') {
      try {
        const days = Math.min(Math.max(parseInt(url.searchParams.get('days') || '30'), 1), 365);
        const since = new Date(Date.now() - days * 86400000).toISOString();

        const [summary, daily, byTool, byError] = await Promise.all([
          env.DB.prepare(
            `SELECT COUNT(DISTINCT anonymous_id) as active_users,
                    COUNT(*) as total_calls,
                    ROUND(100.0 * SUM(ok) / COUNT(*), 1) as success_rate,
                    ROUND(AVG(dur_ms), 0) as avg_dur_ms
             FROM events WHERE ts >= ? AND tool <> 'test_tool' AND ${ACTIVE_ID_FILTER}`
          ).bind(since).first(),

          env.DB.prepare(
            `SELECT DATE(ts) as date,
                    COUNT(*) as calls,
                    ROUND(100.0 * SUM(ok) / COUNT(*), 1) as success_rate,
                    ROUND(AVG(dur_ms), 0) as avg_dur_ms
             FROM events WHERE ts >= ? AND tool <> 'test_tool' AND ${ACTIVE_ID_FILTER}
             GROUP BY DATE(ts) ORDER BY date`
          ).bind(since).all(),

          env.DB.prepare(
            `SELECT tool,
                    COUNT(*) as calls,
                    ROUND(100.0 * SUM(ok) / COUNT(*), 1) as success_rate,
                    ROUND(AVG(dur_ms), 0) as avg_dur_ms
             FROM events WHERE ts >= ? AND tool <> 'test_tool' AND ${ACTIVE_ID_FILTER}
             GROUP BY tool ORDER BY calls DESC`
          ).bind(since).all(),

          env.DB.prepare(
            `SELECT error_type,
                    COUNT(*) as count
             FROM events WHERE ts >= ? AND tool <> 'test_tool' AND ${ACTIVE_ID_FILTER}
               AND ok = 0 AND error_type IS NOT NULL
             GROUP BY error_type ORDER BY count DESC LIMIT 20`
          ).bind(since).all(),
        ]);

        return cors(JSON.stringify({
          days,
          summary: summary || { active_users: 0, total_calls: 0, success_rate: 0, avg_dur_ms: 0 },
          daily: daily.results || [],
          by_tool: byTool.results || [],
          by_error: byError.results || [],
        }));
      } catch (e) {
        return cors(JSON.stringify({ error: 'query failed' }), 500);
      }
    }

    // GET /api/errors — 错误下钻明细
    if (path === '/api/errors' && request.method === 'GET') {
      try {
        const tool = url.searchParams.get('tool') || '';
        const days = Math.min(Math.max(parseInt(url.searchParams.get('days') || '30'), 1), 365);
        const since = new Date(Date.now() - days * 86400000).toISOString();

        let query = `SELECT tool, action, error_type, COUNT(*) as count
                     FROM events WHERE ts >= ? AND tool <> 'test_tool' AND ${ACTIVE_ID_FILTER}
                       AND ok = 0 AND error_type IS NOT NULL`;
        const params = [since];

        if (tool) {
          query += ` AND tool = ?`;
          params.push(tool);
        }

        query += ` GROUP BY tool, action, error_type ORDER BY tool, count DESC`;

        const { results } = await env.DB.prepare(query).bind(...params).all();
        return cors(JSON.stringify({ days, tool: tool || null, errors: results || [] }));
      } catch (e) {
        return cors(JSON.stringify({ error: 'query failed' }), 500);
      }
    }

    // POST /api/feedbacks/:id/status — 更新反馈状态（开发者用）
    const statusMatch = path.match(/^\/api\/feedbacks\/(\d+)\/status$/);
    if (statusMatch && request.method === 'POST') {
      try {
        const id = Number(statusMatch[1]);
        const body = await request.json();
        const allowedStatus = ['open', 'done', 'ignored'];
        if (!allowedStatus.includes(body.status)) {
          return cors(JSON.stringify({ ok: false, error: 'invalid status' }), 400);
        }
        const result = await env.DB.prepare(
          `UPDATE feedbacks SET status = ?, note = COALESCE(?, note) WHERE id = ?`
        ).bind(body.status, body.note ?? null, id).run();
        if (!result.meta || result.meta.changes === 0) {
          return cors(JSON.stringify({ ok: false, error: 'feedback not found' }), 404);
        }
        return cors(JSON.stringify({ ok: true }));
      } catch (e) {
        return cors(JSON.stringify({ ok: false, error: 'invalid payload' }), 400);
      }
    }

    // GET /api/feedbacks — 反馈列表（开发者用）
    if (path === '/api/feedbacks' && request.method === 'GET') {
      try {
        const days = Math.min(Math.max(parseInt(url.searchParams.get('days') || '90'), 1), 365);
        const since = new Date(Date.now() - days * 86400000).toISOString();

        const { results } = await env.DB.prepare(
          `SELECT id, ts, type, title, description, contact, status, note FROM feedbacks WHERE ts >= ? ORDER BY ts DESC`
        ).bind(since).all();

        return cors(JSON.stringify({ days, feedbacks: results || [] }));
      } catch (e) {
        return cors(JSON.stringify({ feedbacks: [] }));
      }
    }

    // 404
    return new Response('Not Found', { status: 404 });
  }
};

