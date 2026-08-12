'use client';

// Minimal line diff between two function versions (LCS-based, like `diff` without context
// collapsing). Self-contained — no library. Inputs are capped so a pathological pair of huge
// sources cannot lock the tab: past the cap we fall back to a plain "changed" notice with
// both byte sizes, which is honest rather than wrong.

import { useMemo } from 'react';

const MAX_LINES = 4000;

type Op = { kind: 'same' | 'add' | 'del'; a?: number; b?: number; text: string };

function diffLines(a: string[], b: string[]): Op[] {
  // Myers would be nicer; classic DP LCS is fine at our cap (4000×4000 worst case is too big,
  // so trim the common prefix/suffix first — real version-to-version edits are localized).
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }

  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const ops: Op[] = [];
  for (let i = 0; i < start; i++) ops.push({ kind: 'same', a: i + 1, b: i + 1, text: a[i] });

  if (midA.length * midB.length > 1_000_000) {
    // Middle too large for DP: report as a block replace (correct, just not minimal).
    for (let i = 0; i < midA.length; i++) ops.push({ kind: 'del', a: start + i + 1, text: midA[i] });
    for (let i = 0; i < midB.length; i++) ops.push({ kind: 'add', b: start + i + 1, text: midB[i] });
  } else {
    const n = midA.length;
    const m = midB.length;
    // LCS table (n+1 x m+1) of Uint16 lengths.
    const dp = new Uint16Array((n + 1) * (m + 1));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i * (m + 1) + j] = midA[i] === midB[j]
          ? dp[(i + 1) * (m + 1) + j + 1] + 1
          : Math.max(dp[(i + 1) * (m + 1) + j], dp[i * (m + 1) + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (midA[i] === midB[j]) {
        ops.push({ kind: 'same', a: start + i + 1, b: start + j + 1, text: midA[i] });
        i++; j++;
      } else if (dp[(i + 1) * (m + 1) + j] >= dp[i * (m + 1) + j + 1]) {
        ops.push({ kind: 'del', a: start + i + 1, text: midA[i] });
        i++;
      } else {
        ops.push({ kind: 'add', b: start + j + 1, text: midB[j] });
        j++;
      }
    }
    while (i < n) { ops.push({ kind: 'del', a: start + i + 1, text: midA[i] }); i++; }
    while (j < m) { ops.push({ kind: 'add', b: start + j + 1, text: midB[j] }); j++; }
  }

  for (let i = endA; i < a.length; i++) {
    const off = i - endA;
    ops.push({ kind: 'same', a: i + 1, b: endB + off + 1, text: a[i] });
  }
  return ops;
}

export default function DiffView({
  oldLabel, newLabel, oldSource, newSource,
}: {
  oldLabel: string;
  newLabel: string;
  oldSource: string;
  newSource: string;
}) {
  const a = useMemo(() => oldSource.split('\n'), [oldSource]);
  const b = useMemo(() => newSource.split('\n'), [newSource]);

  if (a.length > MAX_LINES || b.length > MAX_LINES) {
    return (
      <div className="empty" style={{ padding: 20, fontSize: 13 }}>
        These versions are too large to diff in the browser ({a.length.toLocaleString()} vs{' '}
        {b.length.toLocaleString()} lines). Open each version&apos;s source instead.
      </div>
    );
  }

  const ops = diffLines(a, b);
  const changed = ops.filter((o) => o.kind !== 'same').length;

  return (
    <div>
      <div className="muted" style={{ fontSize: 12.5, marginBottom: 8 }}>
        <b style={{ color: 'var(--bad)' }}>{oldLabel}</b> → <b style={{ color: 'var(--good)' }}>{newLabel}</b>
        {' · '}
        {changed === 0 ? 'sources are identical' : `${changed.toLocaleString()} changed line${changed === 1 ? '' : 's'}`}
      </div>
      {changed === 0 ? null : (
        <div className="cl-scroll" style={{ border: '1px solid var(--border)', borderRadius: 10, maxHeight: 420, overflowY: 'auto' }}>
          <pre style={{ margin: 0, fontFamily: 'var(--mono)', fontSize: 12, lineHeight: '19px', minWidth: 'max-content', padding: '8px 0' }}>
            {ops.map((o, idx) => (
              <div
                key={idx}
                style={{
                  padding: '0 12px 0 0',
                  display: 'flex',
                  background: o.kind === 'add' ? 'rgba(36,209,139,0.10)' : o.kind === 'del' ? 'rgba(255,92,114,0.10)' : undefined,
                  color: o.kind === 'add' ? 'var(--good)' : o.kind === 'del' ? 'var(--bad)' : 'var(--text-dim)',
                }}
              >
                <span style={{ width: 44, flexShrink: 0, textAlign: 'right', paddingRight: 8, color: 'var(--text-faint)', userSelect: 'none' }}>
                  {o.kind === 'add' ? o.b : o.a}
                </span>
                <span style={{ width: 16, flexShrink: 0, userSelect: 'none' }}>
                  {o.kind === 'add' ? '+' : o.kind === 'del' ? '−' : ' '}
                </span>
                <span style={{ whiteSpace: 'pre' }}>{o.text || ' '}</span>
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}
