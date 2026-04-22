import { useEffect, useState } from 'react';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function BillingPage() {
  const { session } = useSession();
  const [settlements, setSettlements] = useState<Array<Record<string, unknown>>>([]);
  const [fundAmount, setFundAmount] = useState('1000000000');
  const [message, setMessage] = useState('');

  async function loadSettlements() {
    if (!session) return;
    const payload = await aicfApi.listSettlements(session, session.selectedProjectId);
    setSettlements(payload.settlements);
  }

  useEffect(() => {
    loadSettlements().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, session?.selectedProjectId]);

  async function fund() {
    if (!session?.selectedProjectId) {
      setMessage('Select a project first');
      return;
    }

    try {
      const result = await aicfApi.fundProject(session, session.selectedProjectId, {
        amountAnm: fundAmount
      });
      setMessage(`Project funded. Contract call: ${result.contractCall.method}`);
      await loadSettlements();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="ANM Billing" subtitle="Project balance, escrow releases, refunds, and settlements">
        <div className="grid two">
          <label>
            Add ANM nanos
            <input value={fundAmount} onChange={(event) => setFundAmount(event.target.value)} />
          </label>
          <button onClick={fund}>Fund selected project</button>
        </div>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      <Panel title="Settlement Ledger" subtitle="Escrow and payout receipts for compute jobs">
        {settlements.length ? (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Job</th>
                <th>Provider</th>
                <th>Status</th>
                <th>Charge</th>
                <th>Reward</th>
                <th>Treasury</th>
              </tr>
            </thead>
            <tbody>
              {settlements.map((row) => (
                <tr key={String(row.id)}>
                  <td>{String(row.id)}</td>
                  <td>{String(row.jobId)}</td>
                  <td>{String(row.providerId)}</td>
                  <td>{String(row.status)}</td>
                  <td>{String(row.chargeAnmNanos)}</td>
                  <td>{String(row.providerRewardAnmNanos)}</td>
                  <td>{String(row.treasuryCutAnmNanos)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No settlements yet.</p>
        )}
      </Panel>
    </div>
  );
}
