import { useEffect, useState } from 'react';
import type { ContractDisputeRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function DisputesPage() {
  const { session } = useSession();
  const [disputes, setDisputes] = useState<ContractDisputeRecord[]>([]);
  const [status, setStatus] = useState<'open' | 'resolved' | 'dismissed' | ''>('');
  const [message, setMessage] = useState('');
  const [slashAmount, setSlashAmount] = useState('10000000');

  async function load() {
    if (!session) return;
    const data = await aicfApi.listDisputes(session, status || undefined);
    setDisputes(data.disputes);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, status]);

  async function resolve(disputeId: string, action: 'slash' | 'clear' | 'refund_requester') {
    if (!session) return;
    try {
      await aicfApi.resolveDispute(session, disputeId, {
        action,
        slashAmountAnmNanos: action === 'slash' ? slashAmount : undefined,
        note: 'resolved from disputes console'
      });
      await load();
      setMessage(`Resolved ${disputeId}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Dispute Console" subtitle="Open/resolve challenge windows, evidence, slash and refund outcomes">
        <div className="row">
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
              <option value="">all</option>
              <option value="open">open</option>
              <option value="resolved">resolved</option>
              <option value="dismissed">dismissed</option>
            </select>
          </label>
          <label>
            Slash amount (ANM nanos)
            <input value={slashAmount} onChange={(event) => setSlashAmount(event.target.value)} />
          </label>
        </div>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {disputes.length === 0 ? (
        <EmptyState title="No disputes" detail="Challenges will appear here when opened against contract job results." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Job</th>
              <th>Status</th>
              <th>Reason</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {disputes.map((dispute) => (
              <tr key={dispute.id}>
                <td>{dispute.id}</td>
                <td>{dispute.jobId}</td>
                <td>{dispute.status}</td>
                <td>{dispute.reasonCode}</td>
                <td>
                  <div className="row">
                    <button onClick={() => resolve(dispute.id, 'clear')}>Clear</button>
                    <button onClick={() => resolve(dispute.id, 'slash')}>Slash</button>
                    <button onClick={() => resolve(dispute.id, 'refund_requester')}>Refund</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
