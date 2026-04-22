import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ContractJobRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ContractJobsPage() {
  const { session } = useSession();
  const [jobs, setJobs] = useState<ContractJobRecord[]>([]);
  const [stateFilter, setStateFilter] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    if (!session) return;
    const data = await aicfApi.listContractJobs(session, stateFilter ? { state: stateFilter } : undefined);
    setJobs(data.jobs);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, stateFilter]);

  return (
    <div className="stack">
      <Panel
        title="Contract Jobs"
        subtitle="Deterministic on-chain intent + off-chain AICF execution + deterministic ANM settlement"
      >
        <div className="row">
          <label>
            State filter
            <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
              <option value="">all</option>
              <option value="requested">requested</option>
              <option value="assigned">assigned</option>
              <option value="running">running</option>
              <option value="result_submitted">result_submitted</option>
              <option value="accepted">accepted</option>
              <option value="challenged">challenged</option>
              <option value="finalized_paid">finalized_paid</option>
              <option value="finalized_refunded">finalized_refunded</option>
              <option value="expired">expired</option>
            </select>
          </label>
          <Link className="ghost" to="/app/contract-jobs/new">
            New Contract Job
          </Link>
        </div>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {jobs.length === 0 ? (
        <EmptyState title="No contract jobs" detail="Create a contract-driven model call to start on-chain escrow + off-chain fulfillment." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Contract</th>
              <th>Model</th>
              <th>Mode</th>
              <th>State</th>
              <th>Budget</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <Link to={`/app/contract-jobs/${job.id}`}>{job.id}</Link>
                </td>
                <td>{job.contractAddress}</td>
                <td>{job.modelId}</td>
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
