'use strict';
// One-line JSON snapshot of every usage counter the watcher cares about.
const D = require('better-sqlite3');
const GW = '/root/animica/apps/x402-gateway/state';
const OURS = '0xa2145508a22215ee4ff4b728a9fee04e777bd02e'; // our own test payer
const SELF = '144.126.133.21';                              // this host
function q(file, sql, params = []) {
  try {
    const db = new D(file, { readonly: true });
    const r = db.prepare(sql).get(...params);
    db.close();
    return r ? Number(Object.values(r)[0]) : 0;
  } catch { return 0; }
}
const P = `${GW}/x402.db`, G = `${GW}/x402-gateway.db`;
console.log(JSON.stringify({
  extPay:  q(P, 'SELECT COUNT(*) n FROM payments WHERE status=? AND lower(payer)<>?', ['settled', OURS]),
  allPay:  q(P, 'SELECT COUNT(*) n FROM payments WHERE status=?', ['settled']),
  spends:  q(G, 'SELECT COALESCE(SUM(spend_count),0) n FROM credit_vouchers'),
  anm:     q(G, 'SELECT COUNT(*) n FROM anm_payments'),
  scanExt: q(G, 'SELECT COUNT(*) n FROM scan_services WHERE submitted_by NOT LIKE ?', [SELF + '%']),
  bounty:  q(G, 'SELECT COUNT(*) n FROM bounty_claims'),
  incid:   q(G, 'SELECT COUNT(*) n FROM incidents'),
}));
