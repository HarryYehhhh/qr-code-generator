/**
 * scripts/k6/redirect_cold.js
 *
 * Purpose:
 *   Measure the cold-path redirect latency. Each VU iteration creates a brand-new
 *   token (POST /v1/qr_code) and immediately redirects through it (GET /r/{token}).
 *   This exercises:
 *     - Postgres INSERT (token creation)
 *     - SHA-256 token generation + Base62 encoding
 *     - Redis SETEX for URL cache population
 *     - First-ever redirect (cache miss → DB SELECT → Redis SETEX)
 *
 * Run:
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_cold.js
 *
 *   # With docker-compose loadtest profile:
 *   docker-compose --profile loadtest run --rm k6 run /scripts/redirect_cold.js
 *
 * Expected bottleneck:
 *   Postgres INSERT is the dominant cost (~5-20ms per row on db-f1-micro).
 *   Watch: Grafana panel "qr_db_pool_in_use" — if it saturates at pool_size+max_overflow (=3),
 *   requests queue and p99 spikes. Also check DB CPU via `docker stats db --no-stream`.
 *   SERVER_SECRET hashing (SHA-256) is negligible (<0.1ms).
 *
 * Adjustable thresholds:
 *   p99Ms      - default 1500ms; loosened vs hot path to account for DB write latency
 *   failedRate - default 1%
 *   vus / duration - reduce vus if local Postgres can't keep up
 */

import http from 'k6/http';
import { check } from 'k6';
import { baseUrl, buildThresholds, defaultOptions } from './lib/common.js';

export const options = {
  ...defaultOptions,
  scenarios: {
    cold: {
      executor: 'constant-vus',
      vus: 50,
      duration: '3m',
    },
  },
  // p99Ms=1500 accounts for Postgres INSERT latency; adjust after baseline measurement.
  thresholds: buildThresholds({ p99Ms: 1500, failedRate: 0.01 }),
};

export default function () {
  // Step 1: Create a new unique token.
  const url = `https://cold-test.example.com/${__VU}-${__ITER}-${Date.now()}`;
  const createRes = http.post(
    `${baseUrl()}/v1/qr_code`,
    JSON.stringify({ url }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { scenario: 'redirect_cold', step: 'create' },
      // Override the global `discardResponseBodies: true` (set in lib/common.js)
      // because we need to parse `qr_token` out of the response.
      responseType: 'text',
    }
  );

  const created = check(createRes, {
    'create status 201': (r) => r.status === 201,
    'has body': (r) => typeof r.body === 'string' && r.body.length > 0,
  });

  if (!created) {
    return;  // Skip redirect if creation failed to avoid 404 noise.
  }

  let token;
  try {
    token = JSON.parse(createRes.body).qr_token;
  } catch (_) {
    return;  // Defensive: skip if body is malformed (shouldn't happen now).
  }
  if (!token) return;

  // Step 2: Immediately redirect — exercises DB fallback + Redis SETEX path.
  // Note: POST /v1/qr_code pre-warms the URL cache, so this first redirect is
  // technically a Redis hit. To force true DB fallback, flush Redis between runs.
  const redirectRes = http.get(
    `${baseUrl()}/r/${token}`,
    {
      redirects: 0,
      tags: { scenario: 'redirect_cold', step: 'redirect' },
    }
  );

  check(redirectRes, {
    'redirect status 302': (r) => r.status === 302,
  });
}
