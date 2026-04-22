import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { AgentTaskRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function AgentTasksPage() {
  const { session } = useSession();
  const [tasks, setTasks] = useState<AgentTaskRecord[]>([]);
  const [contractAddress, setContractAddress] = useState('anm1contractxxxxxxx');
  const [requester, setRequester] = useState('anm1requesterxxxx');
  const [payer, setPayer] = useState('anm1payerxxxx');
  const [modelId, setModelId] = useState('aicf-chat-1');
  const [budget, setBudget] = useState('4000000000');
  const [message, setMessage] = useState('');

  async function load() {
    if (!session) return;
    const data = await aicfApi.listAgentTasks(session);
    setTasks(data.tasks);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  async function create() {
    if (!session) return;
    try {
      await aicfApi.createAgentTask(session, {
        contractAddress,
        requester,
        payer,
        modelId,
        budgetAnmNanos: budget
      });
      setMessage('Agent task created');
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Agent Tasks" subtitle="Multi-step contract-driven agent workflows with deterministic settlement">
        <div className="grid two">
          <label>
            Contract
            <input value={contractAddress} onChange={(event) => setContractAddress(event.target.value)} />
          </label>
          <label>
            Model
            <input value={modelId} onChange={(event) => setModelId(event.target.value)} />
          </label>
          <label>
            Requester
            <input value={requester} onChange={(event) => setRequester(event.target.value)} />
          </label>
          <label>
            Payer
            <input value={payer} onChange={(event) => setPayer(event.target.value)} />
          </label>
          <label>
            Budget (ANM nanos)
            <input value={budget} onChange={(event) => setBudget(event.target.value)} />
          </label>
        </div>
        <button onClick={create}>Create Agent Task</button>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {tasks.length === 0 ? (
        <EmptyState title="No agent tasks" detail="Create an on-chain agent task to track multi-step commitments." />
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Contract</th>
              <th>State</th>
              <th>Step</th>
              <th>Remaining</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td>
                  <Link to={`/app/agent-tasks/${task.id}`}>{task.id}</Link>
                </td>
                <td>{task.contractAddress}</td>
                <td>{task.state}</td>
                <td>
                  {task.currentStep}/{task.stepCount}
                </td>
                <td>{task.remainingAnmNanos}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
