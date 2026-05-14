/**
 * scripts/k6/image_mixed.js
 *
 * Purpose:
 *   Measure QR image serving performance under 50/50 cache hit / miss traffic.
 *   - Cache hit:  fixed spec (dimension=256&color=%23000000&border=4) — same Redis key every time.
 *   - Cache miss: randomised dimension (200-400) / color / border — each request produces a
 *     unique Redis key, forcing `qrcode` lib CPU + Redis SETEX.
 *   Tests GET /v1/qr_code_image/{token}
 *
 * Run:
 *   # 1. Seed tokens first (if not already done):
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js \
 *     2>/dev/null | grep "^TOKEN:" | sed 's/^TOKEN://' | jq -s '.' > scripts/k6/tokens.json
 *
 *   # 2. Run the image scenario:
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/image_mixed.js
 *
 *   # With docker-compose loadtest profile:
 *   docker-compose --profile loadtest run --rm k6 run /scripts/image_mixed.js
 *
 * Expected bottleneck:
 *   Cache miss path: `qrcode` Python library generates PNG in ~10-20ms CPU per image.
 *   At 100 VUs with ~50% miss rate the api container CPU approaches 80-100%.
 *   Monitor: `docker stats api --no-stream` during the run.
 *   Grafana panel: "Image cache hit rate" should read ~50% during steady state.
 *
 * Adjustable thresholds:
 *   p99Ms      - default 800ms; increase if local machine is slow or CPU-bound
 *   failedRate - default 1%
 *   Cache split - change Math.random() < 0.5 threshold to adjust hit/miss ratio
 */

import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { baseUrl, buildThresholds, pickToken, defaultOptions } from './lib/common.js';

const tokens = new SharedArray('image-tokens', function () {
  return JSON.parse(open('./tokens.json'));
});

// Fixed spec for cache-hit requests (always maps to the same Redis key).
const HIT_SPEC = 'dimension=256&color=%23000000&border=4';

export const options = {
  ...defaultOptions,
  scenarios: {
    image: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 30 },   // warm-up
        { duration: '2m', target: 100 },   // steady state (50% hit / 50% miss)
        { duration: '30s', target: 0 },    // ramp-down
      ],
    },
  },
  // p99Ms=800 accounts for cache-miss generation time; tighten after measurement.
  thresholds: buildThresholds({ p99Ms: 800, failedRate: 0.01 }),
};

/**
 * Returns a random hex color string like %23RRGGBB (URL-encoded #RRGGBB).
 */
function randomColor() {
  const hex = Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, '0');
  return `%23${hex}`;
}

export default function () {
  const token = pickToken(tokens);
  let queryString;

  if (Math.random() < 0.5) {
    // Cache hit: fixed spec, Redis key always the same.
    queryString = HIT_SPEC;
  } else {
    // Cache miss: randomised spec produces a unique Redis key each time.
    const dimension = Math.floor(Math.random() * 201) + 200; // 200-400
    const color = randomColor();
    const border = Math.floor(Math.random() * 5);            // 0-4
    queryString = `dimension=${dimension}&color=${color}&border=${border}`;
  }

  const res = http.get(
    `${baseUrl()}/v1/qr_code_image/${token}?${queryString}`,
    {
      tags: { scenario: 'image_mixed' },
      // Response bodies (PNG bytes) are discarded per defaultOptions.discardResponseBodies.
    }
  );

  check(res, {
    'status 200': (r) => r.status === 200,
    'content-type image/png': (r) => (r.headers['Content-Type'] || '').includes('image/png'),
  });
}
