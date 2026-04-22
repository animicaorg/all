import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ContractDetailPage() {
  const { address = '' } = useParams();
  const { session } = useSession();
  const [contract, setContract] = useState<Record<string, unknown> | null>(null);
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const [message, setMessage] = useState('');

  async function load() {
    if (!session || !address) return;
    const [contractData, jobsData] = await Promise.all([
      aicfApi.getContract(session, address),
      aicfApi.listContractJobs(session, { contractAddress: address })
    ]);
    setContract(contractData.contract as Record<string, unknown>);
    setJobs(jobsData.jobs as Array<Record<string, unknown>>);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, address]);

  async function setPaused(paused: boolean) {
    if (!session) return;
    try {
      await aicfApi.setContractPaused(session, address, paused);
      await load();
      setMessage(paused ? 'Contract paused' : 'Contract resumed');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Contract Detail" subtitle={address}>
        {contract ? <pre>{JSON.stringify(contract, null, 2)}</pre> : <p className="muted">No contract loaded.</p>}
        <div className="row">
          <button onClick={() => setPaused(true)}>Pause</button>
          <button onClick={() => setPaused(false)}>Resume</button>
        </div>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      <Panel title="Linked Jobs" subtitle="Contract-emitted model calls and settlement states">
        {jobs.length ? <pre>{JSON.stringify(jobs, null, 2)}</pre> : <p className="muted">No jobs for this contract.</p>}
      </Panel>
    </div>
  );
}
