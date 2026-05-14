/**
 * scripts/k6/redirect_hot.js
 *
 * Purpose:
 *   Measure the cache-hot redirect throughput ceiling. All tokens are pre-seeded
 *   and their URLs are already in Redis (qr:url:{token}), so every request hits
 *   the Redis URL cache path — no DB fallback occurs.
 *
 * Run:
 *   # 1. Seed tokens first (once):
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js \
 *     2>/dev/null | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' > scripts/k6/tokens.json
 *
 *   # 2. Run the hot scenario:
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/redirect_hot.js
 *
 *   # With docker-compose loadtest profile:
 *   docker-compose --profile loadtest run --rm k6 run /scripts/redirect_hot.js
 *
 * Expected bottleneck:
 *   Redis network round-trip (~0.5-2ms) + FastAPI middleware overhead.
 *   If p99 exceeds 100ms suspect event-loop saturation or Redis connection exhaustion.
 *   Jaeger trace: check app.handler span — Redis GET should be <5ms.
 *
 * Adjustable thresholds (edit buildThresholds call below):
 *   p99Ms     - default 500ms; tighten to 200ms on fast hardware
 *   failedRate - default 1%; 0% is achievable in a stable local environment
 */

import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { baseUrl, buildThresholds, pickToken, defaultOptions } from './lib/common.js';

// Load token list at init stage (shared across all VUs, zero-copy).
const tokens = new SharedArray('hot-tokens', function () {
  return JSON.parse(open('./tokens.json'));
});

export const options = {
  ...defaultOptions,
  scenarios: {
    hot: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 50 },   // warm-up
        { duration: '2m', target: 200 },   // steady state
        { duration: '30s', target: 500 },  // spike
        { duration: '30s', target: 0 },    // ramp-down
      ],
    },
  },
  // Adjustable: p99Ms=500 is conservative; tighten after baseline measurement.
  thresholds: buildThresholds({ p99Ms: 500, failedRate: 0.01 }),
};

export default function () {
  const token = pickToken(tokens);
  const res = http.get(
    `${baseUrl()}/r/${token}`,
    {
      redirects: 0,  // Do not follow redirect; we only measure the 302 response time.
      tags: { scenario: 'redirect_hot' },
    }
  );

  check(res, {
    'status 302': (r) => r.status === 302,
  });
}
