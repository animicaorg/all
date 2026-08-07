'use client';

// A self-contained browser Python editor for the /cloud console. No Monaco, no CDN, no
// dependencies: a <textarea> (input + native caret/undo/IME) layered over a highlighted
// <pre> and a line-number gutter that scroll in lockstep.
//
//   * line numbers + per-line error/warning markers fed by the REAL validator findings
//   * Python syntax highlighting (small stateful tokenizer, handles triple-quoted strings)
//   * Tab / Shift-Tab indentation (block-aware), Enter auto-indent
//   * Ctrl/Cmd-S -> onSave (validate), Ctrl/Cmd-F -> in-editor search with match count
//   * gotoLine() imperative handle so a findings list can jump the caret to the offending line
//
// Large sources degrade honestly: past ~150KB the color pass is skipped (plain text) so typing
// stays responsive; everything else keeps working.

import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react';

export interface EditorFinding {
  severity: 'error' | 'warning';
  code: string;
  message: string;
  line: number; // 1-based; 0 => whole-file
  col: number;
}

export interface PyEditorHandle {
  gotoLine: (line: number) => void;
  focus: () => void;
}

const LINE_H = 20; // px — every layer MUST agree on this
const FONT_SIZE = 13;
const PAD = 12;
const HIGHLIGHT_MAX_CHARS = 150_000;

// ── tokenizer ────────────────────────────────────────────────────────────────

const KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
  'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import',
  'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield', 'match', 'case',
]);
const BUILTINS = new Set([
  'print', 'len', 'range', 'int', 'float', 'str', 'bool', 'list', 'dict', 'set', 'tuple', 'bytes',
  'sum', 'min', 'max', 'abs', 'round', 'sorted', 'reversed', 'enumerate', 'zip', 'map', 'filter',
  'any', 'all', 'open', 'isinstance', 'issubclass', 'getattr', 'setattr', 'hasattr', 'repr',
  'type', 'super', 'iter', 'next', 'id', 'hash', 'hex', 'oct', 'bin', 'ord', 'chr', 'format',
  'divmod', 'pow', 'input', 'vars', 'dir', 'self',
]);

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const C = {
  kw: '#b39dff',
  str: '#8ed0a5',
  com: '#626880',
  num: '#ffb454',
  bi: '#14e0c8',
  def: '#7fc7ff',
  dec: '#ffd27f',
};

/** Tokenize `src` into per-line HTML strings. Stateful across lines for ''' / """ blocks. */
function highlightLines(src: string): string[] {
  if (src.length > HIGHLIGHT_MAX_CHARS) return src.split('\n').map(esc);

  const lines: string[] = [''];
  let cur = 0;
  const emit = (text: string, color: string | null, italic = false, bold = false) => {
    // A token may span lines (triple strings): split so each line stays a self-contained span.
    const parts = text.split('\n');
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) {
        lines.push('');
        cur++;
      }
      if (!parts[i]) continue;
      const e = esc(parts[i]);
      lines[cur] += color
        ? `<span style="color:${color}${italic ? ';font-style:italic' : ''}${bold ? ';font-weight:600' : ''}">${e}</span>`
        : e;
    }
  };

  let i = 0;
  const n = src.length;
  let prevWord = ''; // last identifier/keyword seen (for def/class name coloring)
  while (i < n) {
    const ch = src[i];

    // comment
    if (ch === '#') {
      let j = src.indexOf('\n', i);
      if (j === -1) j = n;
      emit(src.slice(i, j), C.com, true);
      i = j;
      continue;
    }

    // string (with optional prefix letters)
    if (ch === '"' || ch === "'" || /[rRbBuUfF]/.test(ch)) {
      const m = /^([rRbBuUfF]{1,2})?("""|'''|"|')/.exec(src.slice(i, i + 5));
      if (m && (ch === '"' || ch === "'" || m[1])) {
        const open = m[0];
        const q = m[2];
        let j = i + open.length;
        if (q.length === 3) {
          const close = src.indexOf(q, j);
          j = close === -1 ? n : close + 3;
        } else {
          while (j < n) {
            const c = src[j];
            if (c === '\\') { j += 2; continue; }
            if (c === q || c === '\n') { j += c === q ? 1 : 0; break; }
            j++;
          }
          if (j >= n) j = n;
        }
        emit(src.slice(i, j), C.str);
        i = j;
        prevWord = '';
        continue;
      }
    }

    // number
    if (/\d/.test(ch) || (ch === '.' && /\d/.test(src[i + 1] ?? ''))) {
      const m = /^\d[\w.]*|^\.\d[\w.]*/.exec(src.slice(i));
      if (m) {
        emit(m[0], C.num);
        i += m[0].length;
        prevWord = '';
        continue;
      }
    }

    // decorator
    if (ch === '@' && /[A-Za-z_]/.test(src[i + 1] ?? '')) {
      const m = /^@[\w.]+/.exec(src.slice(i))!;
      emit(m[0], C.dec);
      i += m[0].length;
      prevWord = '';
      continue;
    }

    // identifier / keyword
    if (/[A-Za-z_]/.test(ch)) {
      const m = /^[A-Za-z_]\w*/.exec(src.slice(i))!;
      const w = m[0];
      if (KEYWORDS.has(w)) emit(w, C.kw, false, true);
      else if (prevWord === 'def' || prevWord === 'class') emit(w, C.def, false, true);
      else if (BUILTINS.has(w)) emit(w, C.bi);
      else emit(w, null);
      prevWord = w;
      i += w.length;
      continue;
    }

    // newline / everything else
    if (ch === '\n') {
      lines.push('');
      cur++;
      i++;
      if (src[i - 2] !== '\\') prevWord = '';
      continue;
    }
    emit(ch, null);
    if (!/\s/.test(ch)) prevWord = '';
    i++;
  }
  return lines;
}

// ── the component ────────────────────────────────────────────────────────────

const PyEditor = forwardRef<PyEditorHandle, {
  value: string;
  onChange: (v: string) => void;
  findings?: EditorFinding[];
  onSave?: () => void;
  height?: number;
  readOnly?: boolean;
  ariaLabel?: string;
}>(function PyEditor({ value, onChange, findings = [], onSave, height = 460, readOnly = false, ariaLabel = 'Python source editor' }, ref) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [matchIdx, setMatchIdx] = useState(0);
  const [caretLine, setCaretLine] = useState(1);

  const lineCount = useMemo(() => value.split('\n').length, [value]);
  const htmlLines = useMemo(() => highlightLines(value), [value]);

  const byLine = useMemo(() => {
    const m = new Map<number, EditorFinding[]>();
    for (const f of findings) {
      const ln = f.line > 0 ? f.line : 1;
      const arr = m.get(ln) ?? [];
      arr.push(f);
      m.set(ln, arr);
    }
    return m;
  }, [findings]);

  const matches = useMemo(() => {
    if (!query) return [] as number[];
    const out: number[] = [];
    const hay = value.toLowerCase();
    const needle = query.toLowerCase();
    let at = 0;
    while (out.length < 500) {
      const idx = hay.indexOf(needle, at);
      if (idx === -1) break;
      out.push(idx);
      at = idx + Math.max(1, needle.length);
    }
    return out;
  }, [value, query]);

  const lineOfOffset = useCallback((offset: number) => {
    let line = 1;
    for (let i = 0; i < offset && i < value.length; i++) if (value[i] === '\n') line++;
    return line;
  }, [value]);

  const scrollToLine = useCallback((line: number) => {
    const ta = taRef.current;
    if (!ta) return;
    const target = Math.max(0, (line - 1) * LINE_H - ta.clientHeight / 2 + LINE_H);
    ta.scrollTop = target;
    syncScroll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const gotoLine = useCallback((line: number) => {
    const ta = taRef.current;
    if (!ta) return;
    const ls = value.split('\n');
    const ln = Math.min(Math.max(1, line), ls.length);
    let off = 0;
    for (let i = 0; i < ln - 1; i++) off += ls[i].length + 1;
    ta.focus();
    ta.setSelectionRange(off, off + ls[ln - 1].length);
    scrollToLine(ln);
    setCaretLine(ln);
  }, [value, scrollToLine]);

  useImperativeHandle(ref, () => ({ gotoLine, focus: () => taRef.current?.focus() }), [gotoLine]);

  const syncScroll = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    if (viewRef.current) {
      viewRef.current.scrollTop = ta.scrollTop;
      viewRef.current.scrollLeft = ta.scrollLeft;
    }
    if (gutterRef.current) gutterRef.current.scrollTop = ta.scrollTop;
  }, []);

  const updateCaret = useCallback(() => {
    const ta = taRef.current;
    if (ta) setCaretLine(lineOfOffset(ta.selectionStart ?? 0));
  }, [lineOfOffset]);

  const jumpToMatch = useCallback((idx: number) => {
    const ta = taRef.current;
    if (!ta || matches.length === 0) return;
    const i = ((idx % matches.length) + matches.length) % matches.length;
    setMatchIdx(i);
    const off = matches[i];
    ta.focus();
    ta.setSelectionRange(off, off + query.length);
    scrollToLine(lineOfOffset(off));
  }, [matches, query, lineOfOffset, scrollToLine]);

  // insertText keeps the native undo stack when the browser supports it (all Chromium/WebKit).
  const insertText = useCallback((text: string) => {
    const ta = taRef.current;
    if (!ta) return;
    ta.focus();
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch { ok = false; }
    if (!ok) {
      const { selectionStart: s, selectionEnd: e } = ta;
      ta.setRangeText(text, s, e, 'end');
      onChange(ta.value);
    }
  }, [onChange]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const ta = taRef.current;
    if (!ta) return;
    const mod = e.ctrlKey || e.metaKey;

    if (mod && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      onSave?.();
      return;
    }
    if (mod && (e.key === 'f' || e.key === 'F')) {
      e.preventDefault();
      setSearchOpen(true);
      setTimeout(() => searchInputRef.current?.focus(), 0);
      return;
    }
    if (readOnly) return;

    if (e.key === 'Tab') {
      e.preventDefault();
      const s = ta.selectionStart;
      const en = ta.selectionEnd;
      const multi = value.slice(s, en).includes('\n');
      if (!e.shiftKey && !multi) {
        insertText('    ');
        return;
      }
      // Block indent / dedent: operate on whole lines covering the selection.
      const startLine = value.lastIndexOf('\n', s - 1) + 1;
      let endLine = value.indexOf('\n', en);
      if (endLine === -1) endLine = value.length;
      const block = value.slice(startLine, endLine);
      const out = block
        .split('\n')
        .map((l) => (e.shiftKey ? l.replace(/^ {1,4}/, '') : '    ' + l))
        .join('\n');
      ta.setSelectionRange(startLine, endLine);
      let ok = false;
      try { ok = document.execCommand('insertText', false, out); } catch { ok = false; }
      if (!ok) {
        ta.setRangeText(out, startLine, endLine, 'select');
        onChange(ta.value);
      }
      ta.setSelectionRange(startLine, startLine + out.length);
      return;
    }

    if (e.key === 'Enter' && !e.shiftKey && !mod) {
      // Auto-indent: keep the current line's leading whitespace; +4 after a trailing ':'.
      const s = ta.selectionStart;
      const lineStart = value.lastIndexOf('\n', s - 1) + 1;
      const line = value.slice(lineStart, s);
      const indent = (/^[ \t]*/.exec(line) ?? [''])[0];
      const extra = /:\s*$/.test(line) ? '    ' : '';
      e.preventDefault();
      insertText('\n' + indent + extra);
      return;
    }

    if (e.key === 'Escape' && searchOpen) {
      setSearchOpen(false);
    }
  }, [value, onSave, insertText, onChange, readOnly, searchOpen]);

  useEffect(() => { syncScroll(); }, [value, syncScroll]);
  useEffect(() => { setMatchIdx(0); }, [query]);

  const gutterW = String(Math.max(lineCount, 1)).length * 8 + 26;
  const mono = 'ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace';
  const sharedMetrics: React.CSSProperties = {
    fontFamily: mono,
    fontSize: FONT_SIZE,
    lineHeight: `${LINE_H}px`,
    tabSize: 4,
    whiteSpace: 'pre',
    margin: 0,
  };

  return (
    <div style={{ position: 'relative', border: '1px solid var(--border-bright)', borderRadius: 10, background: 'var(--bg-elev)', overflow: 'hidden' }}>
      {/* search bar */}
      {searchOpen && (
        <div style={{
          position: 'absolute', top: 6, right: 6, zIndex: 5, display: 'flex', alignItems: 'center', gap: 6,
          background: 'var(--bg-card)', border: '1px solid var(--border-bright)', borderRadius: 8, padding: '4px 6px',
          boxShadow: '0 6px 20px rgba(0,0,0,0.4)',
        }}>
          <input
            ref={searchInputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); jumpToMatch(e.shiftKey ? matchIdx - 1 : matchIdx + (matches.length ? 1 : 0)); }
              if (e.key === 'Escape') { setSearchOpen(false); taRef.current?.focus(); }
            }}
            placeholder="Find… (Enter = next)"
            spellCheck={false}
            style={{ background: 'transparent', border: 0, outline: 0, color: 'var(--text)', fontSize: 12.5, width: 150, fontFamily: 'inherit' }}
          />
          <span className="muted" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>
            {query ? (matches.length ? `${(matchIdx % matches.length) + 1}/${matches.length}` : '0/0') : ''}
          </span>
          <button type="button" onClick={() => jumpToMatch(matchIdx - 1)} disabled={!matches.length} title="Previous (Shift+Enter)"
            style={{ background: 'none', border: 0, cursor: 'pointer', fontSize: 12, padding: '2px 4px' }}>↑</button>
          <button type="button" onClick={() => jumpToMatch(matchIdx + 1)} disabled={!matches.length} title="Next (Enter)"
            style={{ background: 'none', border: 0, cursor: 'pointer', fontSize: 12, padding: '2px 4px' }}>↓</button>
          <button type="button" onClick={() => { setSearchOpen(false); taRef.current?.focus(); }} title="Close (Esc)"
            style={{ background: 'none', border: 0, cursor: 'pointer', fontSize: 12, padding: '2px 4px' }}>✕</button>
        </div>
      )}

      <div style={{ display: 'flex', height }}>
        {/* gutter */}
        <div
          ref={gutterRef}
          aria-hidden
          style={{
            width: gutterW, flexShrink: 0, overflow: 'hidden', textAlign: 'right', userSelect: 'none',
            background: 'var(--bg-card)', borderRight: '1px solid var(--border)', color: 'var(--text-faint)',
            paddingTop: PAD, ...sharedMetrics, fontSize: 11.5,
          }}
        >
          <div style={{ paddingBottom: PAD + 200 }}>
            {Array.from({ length: lineCount }, (_, i) => {
              const ln = i + 1;
              const fs = byLine.get(ln);
              const worst = fs?.some((f) => f.severity === 'error') ? 'error' : fs?.length ? 'warning' : null;
              return (
                <div key={ln} style={{ height: LINE_H, paddingRight: 8, position: 'relative', color: ln === caretLine ? 'var(--text-dim)' : undefined }}
                  title={fs?.map((f) => `${f.severity}: ${f.message}`).join('\n')}>
                  {worst && (
                    <span style={{
                      position: 'absolute', left: 4, top: 7, width: 7, height: 7, borderRadius: 99,
                      background: worst === 'error' ? 'var(--bad)' : 'var(--warn)',
                    }} />
                  )}
                  {ln}
                </div>
              );
            })}
          </div>
        </div>

        {/* highlight layer + input layer */}
        <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
          <div
            ref={viewRef}
            aria-hidden
            style={{ position: 'absolute', inset: 0, overflow: 'hidden', padding: `${PAD}px 0 ${PAD}px 0` }}
          >
            <pre style={{ ...sharedMetrics, display: 'inline-block', minWidth: '100%', color: 'var(--text)', padding: `0 ${PAD}px 200px ${PAD}px` }}>
              {htmlLines.map((h, i) => {
                const fs = byLine.get(i + 1);
                const worst = fs?.some((f) => f.severity === 'error') ? 'error' : fs?.length ? 'warning' : null;
                return (
                  <span
                    key={i}
                    style={{
                      display: 'block', height: LINE_H, minWidth: '100%',
                      background: worst === 'error' ? 'rgba(255,92,114,0.10)' : worst === 'warning' ? 'rgba(255,180,84,0.07)' : undefined,
                    }}
                    dangerouslySetInnerHTML={{ __html: h || ' ' }}
                  />
                );
              })}
            </pre>
          </div>
          <textarea
            ref={taRef}
            value={value}
            onChange={(e) => { onChange(e.target.value); updateCaret(); }}
            onScroll={syncScroll}
            onKeyDown={onKeyDown}
            onKeyUp={updateCaret}
            onClick={updateCaret}
            readOnly={readOnly}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            wrap="off"
            aria-label={ariaLabel}
            style={{
              position: 'absolute', inset: 0, width: '100%', height: '100%', resize: 'none',
              background: 'transparent', color: 'transparent', caretColor: 'var(--text)',
              border: 0, outline: 'none', overflow: 'auto', padding: `${PAD}px ${PAD}px 200px ${PAD}px`,
              ...sharedMetrics,
            }}
          />
        </div>
      </div>

      {/* status bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '5px 10px', borderTop: '1px solid var(--border)',
        fontSize: 11.5, color: 'var(--text-faint)', flexWrap: 'wrap',
      }}>
        <span>Ln {caretLine}/{lineCount}</span>
        <span>{new TextEncoder().encode(value).length.toLocaleString()} bytes</span>
        {value.length > HIGHLIGHT_MAX_CHARS && <span style={{ color: 'var(--warn)' }}>large file — colors off</span>}
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ whiteSpace: 'nowrap' }}>⌘/Ctrl-S validate · ⌘/Ctrl-F find · Tab indent</span>
      </div>
    </div>
  );
});

export default PyEditor;
