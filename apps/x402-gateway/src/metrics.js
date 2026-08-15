'use strict';
/**
 * Minimal Prometheus text-format metrics registry (no dependency — the
 * exposition format is a few lines of text). Shared by the facilitator
 * (/metrics on the facilitator server) and the gateway (/metrics on the
 * gateway server); both bind loopback only.
 *
 * Money/gas metrics are BigInt internally and rendered digit-wise:
 *   - x402_gas_spent_wei renders as an exact decimal integer string;
 *   - x402_revenue_usdc accumulates ATOMIC units (6 decimals) and renders
 *     as an exact decimal USDC string (atomic / 1e6 done on strings).
 * Prometheus stores float64 — precision loss on scrape at extreme values is
 * the scraper's inherent limit, but this process never touches floats for
 * money.
 */

function esc(v) {
  return String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

function labelString(labels) {
  const keys = Object.keys(labels || {}).sort();
  if (keys.length === 0) return '';
  return '{' + keys.map((k) => `${k}="${esc(labels[k])}"`).join(',') + '}';
}

/** Exact atomic-units -> decimal string with `decimals` places (BigInt-safe). */
function atomicToDecimalString(atomic, decimals) {
  const neg = atomic < 0n;
  const abs = (neg ? -atomic : atomic).toString().padStart(decimals + 1, '0');
  const intPart = abs.slice(0, abs.length - decimals);
  const frac = abs.slice(abs.length - decimals).replace(/0+$/, '');
  return (neg ? '-' : '') + intPart + (frac ? '.' + frac : '');
}

class Counter {
  constructor(name, help) {
    this.name = name;
    this.help = help;
    this.series = new Map(); // labelString -> number
  }
  inc(labels = {}, by = 1) {
    const key = labelString(labels);
    this.series.set(key, (this.series.get(key) || 0) + by);
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} counter`];
    if (this.series.size === 0) lines.push(`${this.name} 0`);
    for (const [ls, v] of this.series) lines.push(`${this.name}${ls} ${v}`);
    return lines.join('\n');
  }
}

class BigCounter {
  constructor(name, help, { renderDecimals = 0 } = {}) {
    this.name = name;
    this.help = help;
    this.renderDecimals = renderDecimals;
    this.series = new Map(); // labelString -> BigInt
  }
  inc(labels = {}, by = 0n) {
    if (typeof by !== 'bigint') throw new Error(`${this.name}: BigCounter increments must be BigInt`);
    const key = labelString(labels);
    this.series.set(key, (this.series.get(key) || 0n) + by);
  }
  get(labels = {}) {
    return this.series.get(labelString(labels)) || 0n;
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} counter`];
    if (this.series.size === 0) lines.push(`${this.name} 0`);
    for (const [ls, v] of this.series) {
      lines.push(`${this.name}${ls} ${this.renderDecimals ? atomicToDecimalString(v, this.renderDecimals) : v.toString()}`);
    }
    return lines.join('\n');
  }
}

class Gauge {
  constructor(name, help) {
    this.name = name;
    this.help = help;
    this.series = new Map();
  }
  set(labels = {}, v) {
    if (v === undefined) { v = labels; labels = {}; }
    this.series.set(labelString(labels), v);
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} gauge`];
    if (this.series.size === 0) lines.push(`${this.name} 0`);
    for (const [ls, v] of this.series) lines.push(`${this.name}${ls} ${v}`);
    return lines.join('\n');
  }
}

class Histogram {
  constructor(name, help, buckets) {
    this.name = name;
    this.help = help;
    this.buckets = buckets.slice().sort((a, b) => a - b);
    this.series = new Map(); // labelString -> {counts: number[], sum, count}
  }
  observe(labels = {}, v) {
    if (v === undefined) { v = labels; labels = {}; }
    const key = labelString(labels);
    let s = this.series.get(key);
    if (!s) {
      s = { labels, counts: new Array(this.buckets.length).fill(0), sum: 0, count: 0 };
      this.series.set(key, s);
    }
    for (let i = 0; i < this.buckets.length; i++) if (v <= this.buckets[i]) s.counts[i]++;
    s.sum += v;
    s.count++;
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} histogram`];
    const renderSeries = (s) => {
      for (let i = 0; i < this.buckets.length; i++) {
        lines.push(`${this.name}_bucket${labelString({ ...s.labels, le: String(this.buckets[i]) })} ${s.counts[i]}`);
      }
      lines.push(`${this.name}_bucket${labelString({ ...s.labels, le: '+Inf' })} ${s.count}`);
      lines.push(`${this.name}_sum${labelString(s.labels)} ${s.sum}`);
      lines.push(`${this.name}_count${labelString(s.labels)} ${s.count}`);
    };
    if (this.series.size === 0) {
      renderSeries({ labels: {}, counts: new Array(this.buckets.length).fill(0), sum: 0, count: 0 });
    }
    for (const s of this.series.values()) renderSeries(s);
    return lines.join('\n');
  }
}

/** The x402 metric set (spec "Metrics" section) + a couple of operational extras. */
function createMetrics() {
  const m = {
    settlementsTotal: new Counter('x402_settlements_total', 'Successfully settled payments'),
    settlementFailuresTotal: new Counter('x402_settlement_failures_total', 'Settlement attempts that failed, by reason'),
    replaysRejectedTotal: new Counter('x402_replays_rejected_total', 'Payments rejected because the authorization was already used/claimed'),
    verificationsTotal: new Counter('x402_verifications_total', 'Verify calls by outcome'),
    gasSpentWei: new BigCounter('x402_gas_spent_wei', 'Total gas spent settling, in wei (L2 execution + L1 data fee)'),
    revenueUsdc: new BigCounter('x402_revenue_usdc', 'Settled revenue in USDC by product', { renderDecimals: 6 }),
    paymentLatencySeconds: new Histogram('x402_payment_latency_seconds', 'End-to-end settle latency in seconds',
      [0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60]),
    ready: new Gauge('x402_ready', '1 when readyz passes, 0 otherwise'),
    gasBalanceWei: new Gauge('x402_facilitator_gas_balance_wei', 'Facilitator account balance in wei (approximate, float rendering)'),
  };

  return {
    ...m,
    render() {
      return [
        m.settlementsTotal.render(),
        m.settlementFailuresTotal.render(),
        m.replaysRejectedTotal.render(),
        m.verificationsTotal.render(),
        m.gasSpentWei.render(),
        m.revenueUsdc.render(),
        m.paymentLatencySeconds.render(),
        m.ready.render(),
        m.gasBalanceWei.render(),
      ].join('\n') + '\n';
    },
  };
}

module.exports = { createMetrics, Counter, BigCounter, Gauge, Histogram, atomicToDecimalString };
