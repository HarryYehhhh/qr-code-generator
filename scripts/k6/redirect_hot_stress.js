/**
 * scripts/k6/redirect_hot_stress.js
 *
 * Purpose:
 *   Stress test variant of redirect_hot. Pushes VU much higher to force
 *   the API into saturation, so we can compare HOW the two architectures
 *   degrade under overload (not who's faster at moderate load).
 *
 * Run:
 *   docker compose --profile loadtest run --rm --no-deps \
 *     -e BASE_URL=http://api:8080 \
 *     k6 run /scripts/redirect_hot_stress.js
 *
 *   For baseline override BASE_URL to baseline container.
 *
 * Load profile:
 *   - 30s warm-up to 500 VU
 *   - 2m constant at 1500 VU (target saturation)
 *   - 30s ramp-down
 *
 * Expected behaviour:
 *   - Single-uvicorn-worker FastAPI cannot serve 1500 concurrent VU:
 *     event loop saturates, accept queue backs up, p99 climbs into seconds,
 *     some requests time out or get connection reset.
 *   - Both baseline (sync HINCRBY) and current (XADD + worker) should hit
 *     similar throughput ceiling, but the architectural win to look for is:
 *     does the worker arch keep click counting LAG manageable while redirect
 *     is overloaded?
 */

import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { baseUrl, buildThresholds, pickToken, defaultOptions } from './lib/common.js';

const tokens = new SharedArray('hot-tokens', function () {
  return JSON.parse(open('./tokens.json'));
});

export const options = {
  ...defaultOptions,
  scenarios: {
    stress: {
      executor: 'ramping-vus',
      stages: [
        { duration: '30s', target: 500 },   // warm-up
        { duration: '2m', target: 1500 },   // saturation target
        { duration: '30s', target: 0 },     // ramp-down
      ],
    },
  },
  // Loose thresholds — this test is supposed to push past them.
  thresholds: buildThresholds({ p99Ms: 5000, failedRate: 0.20 }),
};

export default function () {
  const token = pickToken(tokens);
  const res = http.get(
    `${baseUrl()}/r/${token}`,
    {
      redirects: 0,
      timeout: '10s',
      tags: { scenario: 'redirect_hot_stress' },
    }
  );

  check(res, {
    'status 302': (r) => r.status === 302,
  });
}
