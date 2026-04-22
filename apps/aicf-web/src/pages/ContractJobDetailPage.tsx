import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ContractJobDetailPage() {
  const { id = '' } = useParams();
  const { session } = useSession();
  const [payload, setPayload] = useState<{
    job?: Record<string, unknown>;
    commitments?: Array<Record<string, unknown>>;
    assignments?: Array<Record<string, unknown>>;
    escrowEvents?: Array<Record<string, unknown>>;
  }>({});
  const [message, setMessage] = useState('');
  const [reasonCode, setReasonCode] = useState('INVALID_OUTPUT');

  async function load() {
    if (!session || !id) return;
    const detail = await aicfApi.getContractJob(session, id);
    setPayload(detail);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, id]);

  async function accept() {
    if (!session || !id) return;
    try {
      await aicfApi.acceptContractJob(session, id);
      await load();
      setMessage('Accepted result');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function challenge() {
    if (!session || !id) return;
    try {
      await aicfApi.challengeContractJob(session, id, reasonCode);
      await load();
      setMessage('Opened dispute');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function finalize() {
    if (!session || !id) return;
    try {
      await aicfApi.finalizeContractJob(session, id);
      await load();
      setMessage('Finalized payout/refund');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function refundExpired() {
    if (!session || !id) return;
    try {
      await aicfApi.refundContractJobIfExpired(session, id);
      await load();
      setMessage('Refunded expired job');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  const job = payload.job;

  return (
    <div className="stack">
      <Panel title="Contract Job Detail" subtitle={`ID: ${id}`}>
        {job ? (
          <pre>{JSON.stringify(job, null, 2)}</pre>
        ) : (
          <EmptyState title="No detail" detail="Job not found or no access." />
        )}
        <div className="row">
          <button onClick={accept}>Accept</button>
          <button onClick={finalize}>Finalize</button>
          <button onClick={refundExpired}>Refund If Expired</button>
        </div>
        <div className="row">
          <input value={reasonCode} onChange={(event) => setReasonCode(event.target.value)} />
          <button onClick={challenge}>Challenge</button>
        </div>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      <Panel title="Commitments" subtitle="Provider result commitments and references">
        {payload.commitments?.length ? <pre>{JSON.stringify(payload.commitments, null, 2)}</pre> : <p className="muted">none</p>}
      </Panel>

      <Panel title="Assignments" subtitle="Provider assignment and completion timeline">
        {payload.assignments?.length ? <pre>{JSON.stringify(payload.assignments, null, 2)}</pre> : <p className="muted">none</p>}
      </Panel>

      <Panel title="Escrow Events" subtitle="funded/reserved/paid/refunded/slashed lifecycle">
        {payload.escrowEvents?.length ? <pre>{JSON.stringify(payload.escrowEvents, null, 2)}</pre> : <p className="muted">none</p>}
      </Panel>
    </div>
  );
}
