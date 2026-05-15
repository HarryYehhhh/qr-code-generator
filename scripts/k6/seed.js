/**
 * scripts/k6/seed.js
 *
 * Purpose:
 *   Pre-seed the database with N QR code tokens so that hot/cold redirect
 *   scenarios have a realistic token pool to work from.
 *   Outputs collected tokens to stdout as a JSON array.
 *
 * Run:
 *   # k6 v0.50+ wraps console.log output as `time=... level=info msg="TOKEN:xxx" source=console`
 *   # on STDERR, so we capture stderr and extract the token from the msg field.
 *   # NOTE: sed uses `|` as delimiter to avoid `*` followed by `/` (which closes JSDoc).
 *   docker compose --profile loadtest run --rm k6 run /scripts/seed.js 2>&1 \
 *     | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
 *     | jq -R . | jq -s . \
 *     > scripts/k6/tokens.json
 *
 *   # Standalone k6 (not via docker compose):
 *   k6 run --env BASE_URL=http://localhost:8000 scripts/k6/seed.js 2>&1 \
 *     | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p' \
 *     | jq -R . | jq -s . \
 *     > scripts/k6/tokens.json
 *
 * Expected bottleneck:
 *   Postgres INSERT + SHA-256 token generation. At SEED_COUNT=1000 with 10 VUs
 *   this completes in ~10-30s depending on DB performance.
 *
 * Adjustable parameters:
 *   SEED_COUNT (env) - Number of tokens to create (default: 1000)
 *   vus              - Concurrent VUs for seeding (default: 10)
 */

import http from 'k6/http';
import { check } from 'k6';
import { baseUrl } from './lib/common.js';

const SEED_COUNT = parseInt(__ENV.SEED_COUNT || '1000', 10);

export const options = {
  vus: 10,
  iterations: SEED_COUNT,
  // No thresholds for seed — it's a setup step, not a performance measurement.
};

// Collect tokens across VUs using a shared results array via handleSummary.
// k6 does not allow SharedArray writes from VU context; instead we log each token
// with a TOKEN: prefix so the caller can grep and redirect to tokens.json.
export default function () {
  const iter = __ITER;
  const url = `https://example.com/seed-${iter}-${Date.now()}`;
  const res = http.post(
    `${baseUrl()}/v1/qr_code`,
    JSON.stringify({ url }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  const ok = check(res, {
    'status 201': (r) => r.status === 201,
    'has qr_token': (r) => {
      try {
        return JSON.parse(r.body).qr_token !== undefined;
      } catch (_) {
        return false;
      }
    },
  });

  if (ok) {
    const token = JSON.parse(res.body).qr_token;
    // Print one token per line with a prefix so the caller can grep them out.
    console.log(`TOKEN:${token}`);
  }
}

/**
 * handleSummary just emits a reminder; token lines are captured from stderr via the
 * pipeline documented in the header. k6 console.log writes to stderr with format:
 *   time=... level=info msg="TOKEN:xxx" source=console
 * Use `2>&1 | sed -nE 's|^time=.*level=info msg="TOKEN:([^"]+)".*|\1|p'` to extract tokens.
 *
 * Note: k6's open() is read-only; writing files from k6 is not supported without
 * an external xk6 extension. Use the shell redirect approach above.
 */
export function handleSummary(data) {
  // Return standard stdout summary; token lines were already printed during the run.
  return {
    stdout: '\nSeed complete. Collect TOKEN: lines from stderr to build tokens.json.\n'
      + 'Example:\n'
      + '  k6 run seed.js 2>&1 | sed -nE \'s|^time=.*level=info msg="TOKEN:([^"]+)".*|\\1|p\' | jq -R . | jq -s . > scripts/k6/tokens.json\n',
  };
}
