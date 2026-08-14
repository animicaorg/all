import type { Metadata } from 'next';
import CloudDocsNav from './nav';
import { CLOUD_DOC_SECTIONS } from './doc';

// Animica Python Cloud documentation shell: sticky section sidebar on desktop, a horizontal
// scrolling rail at <=860px (no horizontal page scroll — the rail scrolls inside itself).
// Styling uses ONLY the app's dark design tokens from styles/globals.css.

export const metadata: Metadata = {
  title: 'Animica Python Cloud — documentation',
  description:
    'Write Python. Deploy to Animica. Get paid when people use it. Runtime ABI, capabilities, pricing, REST API and working examples.',
};

const CSS = `
.cdoc{display:grid;grid-template-columns:230px minmax(0,1fr);gap:36px;align-items:start;padding:30px 0 60px}
.cd-toc{position:sticky;top:80px;display:grid;gap:2px;font-size:13.5px}
.cd-toc a{display:block;padding:6px 10px;border-radius:8px;color:var(--text-dim);min-height:22px}
.cd-toc a:hover{background:var(--bg-card);color:var(--text)}
.cd-toc a.active{background:rgba(108,92,255,0.1);color:var(--text);border-left:2px solid var(--accent);border-radius:4px 8px 8px 4px}
.cd-toc-back{color:var(--text-faint) !important;margin-bottom:8px}
.cd-toc-grp{margin-top:12px;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--text-faint);padding:0 10px}
.cd-main{min-width:0}
.cd-main h1{font-size:32px;letter-spacing:-0.03em;margin:0 0 8px;font-weight:800}
.cd-main .cd-lead{font-size:16.5px;color:var(--text-dim);line-height:1.6;margin:0 0 24px;max-width:720px}
.cd-main h2{font-size:21px;letter-spacing:-0.02em;margin:34px 0 8px;scroll-margin-top:80px}
.cd-main h3{font-size:16px;margin:22px 0 6px}
.cd-main p,.cd-main li{color:var(--text-dim);font-size:14.5px;line-height:1.7}
.cd-main li{margin:4px 0}
.cd-main b,.cd-main strong{color:var(--text)}
.cd-main a{color:#a99bff}
.cd-main a:hover{text-decoration:underline}
.cd-codewrap{margin:12px 0;border:1px solid var(--border);border-radius:10px;overflow:hidden;background:var(--bg-elev)}
.cd-codetitle{font-size:11.5px;color:var(--text-faint);padding:7px 13px;border-bottom:1px solid var(--border)}
.cd-code{margin:0;padding:13px 14px;overflow-x:auto;font:12.5px/1.65 var(--mono);color:#cdd6ee;background:transparent}
.cd-k{font-family:var(--mono);background:var(--bg-elev);border:1px solid var(--border);padding:1px 6px;border-radius:6px;font-size:12.5px;color:#bfe9ff}
.cd-callout{border:1px solid var(--border-bright);border-left:3px solid var(--accent-2);border-radius:10px;padding:11px 14px;margin:14px 0;font-size:13.5px;color:var(--text-dim);line-height:1.6}
.cd-callout b{color:var(--text)}
.cd-tablewrap{overflow-x:auto;margin:12px 0;border:1px solid var(--border);border-radius:10px}
.cd-table{width:100%;border-collapse:collapse;font-size:13px;min-width:520px}
.cd-table th,.cd-table td{border-bottom:1px solid var(--border);padding:8px 12px;text-align:left;vertical-align:top;color:var(--text-dim)}
.cd-table th{color:var(--text);background:var(--bg-elev);font-size:12px;letter-spacing:.03em;white-space:nowrap}
.cd-table tr:last-child td{border-bottom:0}
.cd-table code,.cd-main td code{font-family:var(--mono);font-size:12px;color:#bfe9ff}
.cd-pagenav{display:flex;justify-content:space-between;gap:10px;margin-top:44px;border-top:1px solid var(--border);padding-top:20px}
@media (max-width:860px){
  .cdoc{grid-template-columns:1fr;gap:14px;padding-top:16px}
  .cd-toc{position:static;display:flex;overflow-x:auto;gap:6px;padding-bottom:10px;border-bottom:1px solid var(--border);-webkit-overflow-scrolling:touch}
  .cd-toc a{white-space:nowrap;padding:8px 12px;border:1px solid var(--border);border-radius:999px;min-height:40px;display:flex;align-items:center}
  .cd-toc a.active{border-color:var(--accent);border-left-width:1px;border-radius:999px}
  .cd-toc-grp{display:none}
  .cd-toc-back{margin-bottom:0}
  .cd-main h1{font-size:26px}
}
`;

export default function CloudDocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="wrap">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="cdoc">
        <CloudDocsNav sections={CLOUD_DOC_SECTIONS} />
        <main className="cd-main">{children}</main>
      </div>
    </div>
  );
}
