import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ContractJobRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ProviderContractJobsPage() {
  const { session } = useSession();
  const [jobs, setJobs] = useState<ContractJobRecord[]>([]);
  const [message, setMessage] = useState('');

  async function load() {
    if (!session) return;
    const data = await aicfApi.listProviderContractJobs(session);
    setJobs(data.jobs);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  return (
    <div className="stack">
      <Panel title="Provider Contract Jobs" subtitle="Jobs claimed/submitted from on-chain model call and agent task contracts">
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {jobs.length === 0 ? (
        <EmptyState title="No provider contract jobs" detail="Once scheduler assigns contract jobs, they will appear here." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>On-chain ID</th>
              <th>Mode</th>
              <th>State</th>
              <th>Budget</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <Link to={`/app/provider-jobs/${job.id}`}>{job.id}</Link>
                </td>
                <td>{job.onchainJobId}</td>
                <td>{job.mode}</td>
                <td>{job.state}</td>
                <td>{job.budgetAnmNanos}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
