/**
 * scripts/k6/lib/common.js
 *
 * Purpose:
 *   Shared utilities for all k6 load-test scripts in this project.
 *   Exports baseUrl(), defaultOptions, buildThresholds(), pickToken(), and loadTokens().
 *
 * Usage:
 *   import { baseUrl, defaultOptions, buildThresholds, pickToken, loadTokens } from './lib/common.js';
 *
 * Environment variables:
 *   BASE_URL  - API base URL (default: http://api:8080)
 */

import { SharedArray } from 'k6/data';

/**
 * Returns the API base URL from the BASE_URL env var, defaulting to http://api:8080.
 * @returns {string}
 */
export function baseUrl() {
  return __ENV.BASE_URL || 'http://api:8080';
}

/**
 * Default k6 options applied to every scenario.
 * discardResponseBodies: true saves memory when body content is not needed.
 * summaryTrendStats controls which percentiles appear in the end-of-test summary.
 */
export const defaultOptions = {
  summaryTrendStats: ['min', 'med', 'avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
};

/**
 * Builds a k6 thresholds object for standard HTTP error rate and duration checks.
 *
 * @param {object} opts
 * @param {number} [opts.failedRate=0.01]  - Maximum allowed http_req_failed rate (default 1%)
 * @param {number} [opts.p99Ms=500]        - Maximum allowed p99 response time in ms (default 500ms)
 * @returns {object} k6 thresholds object
 *
 * Adjustable: increase p99Ms on slower machines or when testing the cold path.
 */
export function buildThresholds({ failedRate = 0.01, p99Ms = 500 } = {}) {
  return {
    http_req_failed: [`rate<${failedRate}`],
    http_req_duration: [`p(99)<${p99Ms}`],
  };
}

/**
 * Picks a random token from the given array.
 *
 * @param {string[]} tokens - Array of qr_token strings
 * @returns {string}
 */
export function pickToken(tokens) {
  return tokens[Math.floor(Math.random() * tokens.length)];
}

/**
 * Loads the pre-seeded token list from tokens.json (relative to the script directory).
 * Must be called at module init level (not inside VU function) because open() is only
 * available during init stage. Wraps result in SharedArray for memory efficiency.
 *
 * @returns {SharedArray} Array of qr_token strings
 */
export function loadTokens() {
  return new SharedArray('tokens', function () {
    return JSON.parse(open('./tokens.json'));
  });
}
