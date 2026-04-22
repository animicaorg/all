import { useEffect, useMemo, useState } from 'react';
import type { UsageRecord } from '@animica/aicf-shared';
import { EmptyState, Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function UsagePage() {
  const { session } = useSession();
  const [usage, setUsage] = useState<UsageRecord[]>([]);
  const [message, setMessage] = useState('');

  async function loadUsage() {
    if (!session) return;
    const payload = await aicfApi.listUsage(session, session.selectedProjectId);
    setUsage(payload.usage);
  }

  useEffect(() => {
    loadUsage().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, session?.selectedProjectId]);

  const totals = useMemo(() => {
    const totalCharged = usage.reduce((sum, row) => sum + BigInt(row.chargedAnmNanos), 0n);
    const totalProvider = usage.reduce((sum, row) => sum + BigInt(row.providerRewardAnmNanos), 0n);
    const totalTreasury = usage.reduce((sum, row) => sum + BigInt(row.treasuryCutAnmNanos), 0n);
    const totalSubsidy = usage.reduce((sum, row) => sum + BigInt(row.subsidyAnmNanos), 0n);
    return {
      totalCharged: totalCharged.toString(),
      totalProvider: totalProvider.toString(),
      totalTreasury: totalTreasury.toString(),
      totalSubsidy: totalSubsidy.toString()
    };
  }, [usage]);

  return (
    <div className="stack">
      <Panel title="Usage Meter" subtitle="Platform-side metering, never client-reported">
        <div className="stats-inline">
          <StatTile label="Charged ANM nanos" value={totals.totalCharged} />
          <StatTile label="Provider rewards" value={totals.totalProvider} />
          <StatTile label="Treasury cut" value={totals.totalTreasury} />
          <StatTile label="Subsidy" value={totals.totalSubsidy} />
        </div>
      </Panel>

      {usage.length ? (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Model</th>
              <th>Class</th>
              <th>Status</th>
              <th>Input</th>
              <th>Output</th>
              <th>Charged</th>
            </tr>
          </thead>
          <tbody>
            {usage.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.createdAt}</td>
                <td>{entry.model}</td>
                <td>{entry.class}</td>
                <td>{entry.status}</td>
                <td>{entry.inputTokens}</td>
                <td>{entry.outputTokens}</td>
                <td>{entry.chargedAnmNanos}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No usage yet" detail="Call models or run jobs to generate metered ANM usage records." />
      )}

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
