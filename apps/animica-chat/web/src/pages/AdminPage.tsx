import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { SiteHeader } from '../components/SiteHeader';

export function AdminPage() {
  const [summary, setSummary] = useState<{ activeSubs: number; totalMrrCents: number } | null>(null);
  const [users, setUsers] = useState<any[]>([]);
  useEffect(() => {
    api.get<{ activeSubs: number; totalMrrCents: number }>('/api/admin/usage/summary').then(setSummary).catch(() => undefined);
    api.get<{ users: any[] }>('/api/admin/users').then((r) => setUsers(r.users)).catch(() => undefined);
  }, []);
  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-10 max-w-6xl px-4">
        <h1 className="text-2xl font-semibold">Admin</h1>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <Stat label="Active subs" value={summary?.activeSubs.toString() ?? '—'} />
          <Stat label="MRR" value={summary ? `$${(summary.totalMrrCents / 100).toFixed(2)}` : '—'} />
          <Stat label="Users" value={users.length.toString()} />
        </div>
        <h2 className="mt-8 mb-2 text-lg font-semibold">Users</h2>
        <div className="overflow-x-auto rounded-xl border border-white/8">
          <table className="w-full text-sm">
            <thead className="bg-white/[0.02] text-left text-ink-400">
              <tr>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Plan</th>
                <th className="px-3 py-2">Conv.</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-white/5">
                  <td className="px-3 py-2 font-mono">{u.email}</td>
                  <td className="px-3 py-2">{u.role}</td>
                  <td className="px-3 py-2">{u.subscription?.plan?.code ?? '—'} ({u.subscription?.status ?? '—'})</td>
                  <td className="px-3 py-2">{u._count?.conversations ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/[0.02] p-4">
      <div className="text-xs uppercase tracking-wide text-ink-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
