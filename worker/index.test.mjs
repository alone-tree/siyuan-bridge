import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('./index.js', import.meta.url), 'utf8');
const worker = (await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default;

function jsonRequest(path, body) {
  return new Request(`https://example.test${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function event(overrides = {}) {
  return {
    ts: '2026-07-25T00:00:00.000Z',
    anonymous_id: 'user-1',
    platform: 'Windows',
    mcp_ver: '1.0.4',
    session_id: 'session-1',
    tool: 'siyuan_read',
    ok: 1,
    dur_ms: 10,
    ...overrides,
  };
}

test('telemetry ingestion rejects test_tool without writing it', async () => {
  let batchCalls = 0;
  const env = {
    DB: {
      prepare() {
        return { bind() { return {}; } };
      },
      async batch() {
        batchCalls += 1;
      },
    },
  };

  const response = await worker.fetch(jsonRequest('/api/telemetry', event({ tool: 'test_tool' })), env);
  const body = await response.json();

  assert.equal(response.status, 400);
  assert.equal(body.ok, false);
  assert.equal(body.error, 'test_tool is not accepted');
  assert.equal(batchCalls, 0);
});

test('dashboard filters by ids with at least two lifetime calls', async () => {
  const queries = [];
  const env = {
    DB: {
      prepare(sql) {
        queries.push(sql);
        return {
          bind() { return this; },
          async first() { return {}; },
          async all() { return { results: [] }; },
        };
      },
    },
  };

  const response = await worker.fetch(new Request('https://example.test/api/dashboard?days=30'), env);
  assert.equal(response.status, 200);
  assert.equal(queries.length, 4);
  for (const sql of queries) {
    assert.match(sql, /anonymous_id IN\s*\(\s*SELECT anonymous_id\s+FROM events\s+WHERE tool <> 'test_tool'\s+GROUP BY anonymous_id\s+HAVING COUNT\(\*\) >= 2\s*\)/s);
    assert.match(sql, /tool <> 'test_tool'/);
  }
});

test('error drilldown uses the same lifetime-id filter', async () => {
  let query = '';
  const env = {
    DB: {
      prepare(sql) {
        query = sql;
        return {
          bind() { return this; },
          async all() { return { results: [] }; },
        };
      },
    },
  };

  const response = await worker.fetch(new Request('https://example.test/api/errors?days=30'), env);
  assert.equal(response.status, 200);
  assert.match(query, /anonymous_id IN\s*\(\s*SELECT anonymous_id\s+FROM events\s+WHERE tool <> 'test_tool'\s+GROUP BY anonymous_id\s+HAVING COUNT\(\*\) >= 2\s*\)/s);
  assert.match(query, /tool <> 'test_tool'/);
});

test('feedback status update accepts valid status and passes note', async () => {
  let query = '';
  let params = null;
  const env = {
    DB: {
      prepare(sql) {
        query = sql;
        return {
          bind(...args) {
            params = args;
            return this;
          },
          async run() {
            return { meta: { changes: 1 } };
          },
        };
      },
    },
  };

  const response = await worker.fetch(
    jsonRequest('/api/feedbacks/11/status', { status: 'done', note: 'v1.0.5 修复' }),
    env
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.ok, true);
  assert.match(query, /UPDATE feedbacks SET status = \?, note = COALESCE\(\?, note\) WHERE id = \?/);
  assert.deepEqual(params, ['done', 'v1.0.5 修复', 11]);
});

test('feedback status update rejects invalid status', async () => {
  const env = {
    DB: {
      prepare() {
        return { bind() { return this; }, async run() { return { meta: { changes: 1 } }; } };
      },
    },
  };

  const response = await worker.fetch(
    jsonRequest('/api/feedbacks/11/status', { status: 'in_progress' }),
    env
  );
  const body = await response.json();

  assert.equal(response.status, 400);
  assert.equal(body.ok, false);
  assert.equal(body.error, 'invalid status');
});

test('feedback status update returns 404 for missing feedback', async () => {
  const env = {
    DB: {
      prepare() {
        return { bind() { return this; }, async run() { return { meta: { changes: 0 } }; } };
      },
    },
  };

  const response = await worker.fetch(
    jsonRequest('/api/feedbacks/999/status', { status: 'done' }),
    env
  );
  const body = await response.json();

  assert.equal(response.status, 404);
  assert.equal(body.ok, false);
  assert.equal(body.error, 'feedback not found');
});

test('feedback list includes status and note fields', async () => {
  let query = '';
  const env = {
    DB: {
      prepare(sql) {
        query = sql;
        return {
          bind() { return this; },
          async all() {
            return {
              results: [{
                id: 11, ts: '2026-07-25T00:00:00.000Z', type: 'bug',
                title: 't', description: 'd', contact: null, status: 'done', note: 'v1.0.5 修复',
              }],
            };
          },
        };
      },
    },
  };

  const response = await worker.fetch(new Request('https://example.test/api/feedbacks?days=90'), env);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.match(query, /SELECT id, ts, type, title, description, contact, status, note FROM feedbacks/);
  assert.equal(body.feedbacks[0].status, 'done');
  assert.equal(body.feedbacks[0].note, 'v1.0.5 修复');
});
