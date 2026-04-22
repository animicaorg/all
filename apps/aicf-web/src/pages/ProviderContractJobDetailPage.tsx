import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ProviderContractJobDetailPage() {
  const { id = '' } = useParams();
  const { session } = useSession();
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [message, setMessage] = useState('');

  async function load() {
    if (!session || !id) return;
    const detail = await aicfApi.getContractJob(session, id);
    setJob(detail.job as Record<string, unknown>);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, id]);

  return (
    <div className="stack">
      <Panel title="Provider Job Detail" subtitle={`contract-job ${id}`}>
        {job ? <pre>{JSON.stringify(job, null, 2)}</pre> : <p className="muted">No job</p>}
        {message ? <p className="muted">{message}</p> : null}
      </Panel>
    </div>
  );
}
