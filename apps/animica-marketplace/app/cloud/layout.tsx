import type { Metadata } from 'next';
import { cloudSession, cloudAccount } from '@/components/cloud/server';
import CloudNav from '@/components/cloud/CloudNav';

export const metadata: Metadata = {
  title: 'Animica Python Cloud — developer console',
  description: 'Write Python. Deploy to Animica. Get paid when people use it.',
};

export const dynamic = 'force-dynamic';

// The console shell: sub-nav + shared console styles. Pages gate themselves server-side
// (pricing stays public), so the shell renders for signed-in and signed-out visitors alike.
export default async function CloudLayout({ children }: { children: React.ReactNode }) {
  const sess = cloudSession();
  const account = sess ? await cloudAccount(sess.accountId) : null;

  return (
    <div className="wrap" style={{ paddingTop: 24, paddingBottom: 70 }}>
      {/* Console-wide styles: form inputs, tables, meters, mobile behavior. globals.css has no
          button/table reset, so UA styles would paint dark-on-dark without these. */}
      <style>{`
        .cloud-root button { font-family: var(--font); color: var(--text); }
        .cloud-root button:disabled { opacity: 0.55; cursor: default; }
        .cloud-root a.btn, .cloud-root button.btn { min-height: 40px; }
        .cl-input {
          width: 100%; background: var(--bg-elev); border: 1px solid var(--border-bright);
          border-radius: 8px; color: var(--text); padding: 9px 11px; font-size: 13.5px;
          outline: none; font-family: inherit; min-height: 40px; box-sizing: border-box;
        }
        .cl-input:focus { border-color: var(--accent); }
        select.cl-input { appearance: auto; }
        textarea.cl-input { min-height: 80px; resize: vertical; font-family: var(--mono); font-size: 12.5px; }
        .cl-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        .cl-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 640px; }
        .cl-table th { text-align: left; color: var(--text-faint); font-size: 11.5px; text-transform: uppercase;
          letter-spacing: 0.06em; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
        .cl-table td { padding: 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        .cl-table tr:last-child td { border-bottom: 0; }
        .cl-table tr.rowlink:hover td { background: rgba(108,92,255,0.05); }
        .cl-kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
        .cl-kpi { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
        .cl-kpi b { display: block; font-size: 22px; letter-spacing: -0.02em; overflow-wrap: anywhere; }
        .cl-kpi span { font-size: 12px; color: var(--text-faint); }
        .cl-meter { height: 6px; border-radius: 999px; background: var(--bg-elev); border: 1px solid var(--border); overflow: hidden; }
        .cl-meter > div { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
        .cl-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        @media (max-width: 860px) { .cl-grid2 { grid-template-columns: 1fr; } }
        .cl-code { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 10px;
          padding: 12px 14px; font-family: var(--mono); font-size: 12.5px; overflow-x: auto; white-space: pre; }
        .cl-loglines { font-family: var(--mono); font-size: 12px; line-height: 1.7; overflow-x: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
      `}</style>
      <div className="cloud-root">
        <CloudNav address={account?.address ?? null} />
        <div style={{ marginTop: 22 }}>{children}</div>
      </div>
    </div>
  );
}
