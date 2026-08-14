// Minimal, dependency-free markdown renderer for developer-authored app docs (CloudApp.docsMd).
// ALL input is HTML-escaped first, then a small whitelist of markdown constructs is re-applied,
// so arbitrary developer HTML/script can never reach the page. Server component.

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function inline(s: string): string {
  let t = esc(s);
  t = t.replace(/`([^`]+)`/g, '<code class="inline">$1</code>');
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // Links: http(s) or site-relative only. href is already escaped above.
  t = t.replace(
    /\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)\s]+)\)/g,
    '<a href="$2" rel="noopener nofollow">$1</a>',
  );
  return t;
}

export function mdToHtml(md: string): string {
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  const out: string[] = [];
  let i = 0;
  let list: 'ul' | 'ol' | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inline(para.join(' '))}</p>`);
      para = [];
    }
  };
  const closeList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      flushPara();
      closeList();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      i++; // closing fence
      out.push(`<pre class="codebox">${esc(buf.join('\n'))}</pre>`);
      continue;
    }

    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      closeList();
      const lvl = Math.min(h[1].length + 1, 5); // # -> h2 … never h1 (page owns h1)
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      i++;
      continue;
    }

    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      flushPara();
      const kind: 'ul' | 'ol' = ul ? 'ul' : 'ol';
      if (list !== kind) {
        closeList();
        out.push(`<${kind}>`);
        list = kind;
      }
      out.push(`<li>${inline((ul ?? ol)![1])}</li>`);
      i++;
      continue;
    }

    if (!line.trim()) {
      flushPara();
      closeList();
      i++;
      continue;
    }

    para.push(line.trim());
    i++;
  }
  flushPara();
  closeList();
  return out.join('\n');
}

export default function Md({ src }: { src: string }) {
  return <div className="md" dangerouslySetInnerHTML={{ __html: mdToHtml(src) }} />;
}
