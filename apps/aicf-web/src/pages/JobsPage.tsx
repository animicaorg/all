import { useEffect, useState } from 'react';
import type { JobRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function JobsPage() {
  const { session } = useSession();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobClass, setJobClass] = useState<JobRecord['request']['class']>('chat_inference');
  const [model, setModel] = useState('aicf-chat-1');
  const [maxBudget, setMaxBudget] = useState('1200000000');
  const [payload, setPayload] = useState('{"messages":[{"role":"user","content":"run inference"}]}');
  const [message, setMessage] = useState('');

  async function loadJobs() {
    if (!session) return;
    const data = await aicfApi.listJobs(session, session.selectedProjectId);
    setJobs(data.jobs);
  }

  useEffect(() => {
    loadJobs().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, session?.selectedProjectId]);

  async function submitJob() {
    if (!session?.selectedProjectId) {
      setMessage('Select a project first');
      return;
    }
    try {
      const input = JSON.parse(payload) as Record<string, unknown>;
      await aicfApi.createJob(session, {
        projectId: session.selectedProjectId,
        maxBudgetAnmNanos: maxBudget,
        request: {
          class: jobClass,
          model,
          input,
          timeoutSeconds: 900,
          replication: 1,
          verificationMode: 'sampled',
          outputMode: 'private',
          challengeWindowSeconds: 900
        }
      });
      setMessage('Job queued for scheduler');
      await loadJobs();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Async Jobs" subtitle="Submit training, batch, retrieval, agent, or custom workloads">
        <div className="grid two">
          <label>
            Job class
            <select value={jobClass} onChange={(event) => setJobClass(event.target.value as JobRecord['request']['class'])}>
              <option value="chat_inference">chat_inference</option>
              <option value="embedding_generation">embedding_generation</option>
              <option value="batch_inference">batch_inference</option>
              <option value="fine_tuning_training">fine_tuning_training</option>
              <option value="evaluation">evaluation</option>
              <option value="retrieval_indexing">retrieval_indexing</option>
              <option value="agent_task">agent_task</option>
              <option value="custom_compute">custom_compute</option>
            </select>
          </label>
          <label>
            Model
            <input value={model} onChange={(event) => setModel(event.target.value)} />
          </label>
          <label>
            Max budget (ANM nanos)
            <input value={maxBudget} onChange={(event) => setMaxBudget(event.target.value)} />
          </label>
        </div>
        <label>
          Input payload (JSON)
          <textarea value={payload} onChange={(event) => setPayload(event.target.value)} rows={6} />
        </label>
        <button onClick={submitJob}>Queue job</button>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {jobs.length ? (
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Class</th>
              <th>Status</th>
              <th>Provider</th>
              <th>Charge (nanos)</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.id}</td>
                <td>{job.request.class}</td>
                <td>{job.status}</td>
                <td>{job.assignedProviderId ?? 'unassigned'}</td>
                <td>{job.settlement.chargedAnmNanos}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No jobs" detail="Queue a job to route compute through provider network and escrow settlement." />
      )}
    </div>
  );
}
