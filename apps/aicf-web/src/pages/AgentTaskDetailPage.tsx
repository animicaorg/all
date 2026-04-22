import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function AgentTaskDetailPage() {
  const { id = '' } = useParams();
  const { session } = useSession();
  const [task, setTask] = useState<Record<string, unknown> | null>(null);
  const [commitmentHash, setCommitmentHash] = useState('0xstepcommitment');
  const [resultHash, setResultHash] = useState('0xfinalhash');
  const [resultRef, setResultRef] = useState('aicf://agent/result.json');
  const [message, setMessage] = useState('');

  async function load() {
    if (!session || !id) return;
    const data = await aicfApi.getAgentTask(session, id);
    setTask(data.task as Record<string, unknown>);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, id]);

  async function appendStep() {
    if (!session || !id) return;
    try {
      await aicfApi.appendAgentTaskStep(session, id, {
        commitmentHash
      });
      await load();
      setMessage('Step commitment appended');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function submitFinal() {
    if (!session || !id) return;
    try {
      await aicfApi.submitAgentTaskFinalResult(session, id, {
        resultHash,
        resultRef
      });
      await load();
      setMessage('Final result submitted');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Agent Task Detail" subtitle={`Task: ${id}`}>
        {task ? <pre>{JSON.stringify(task, null, 2)}</pre> : <p className="muted">No task found.</p>}
      </Panel>

      <Panel title="Append Step Commitment" subtitle="Record deterministic step hash + tool trace ref">
        <label>
          Commitment hash
          <input value={commitmentHash} onChange={(event) => setCommitmentHash(event.target.value)} />
        </label>
        <button onClick={appendStep}>Append Step</button>
      </Panel>

      <Panel title="Submit Final Result" subtitle="Finalize output commitment for settlement flow">
        <label>
          Final result hash
          <input value={resultHash} onChange={(event) => setResultHash(event.target.value)} />
        </label>
        <label>
          Result ref
          <input value={resultRef} onChange={(event) => setResultRef(event.target.value)} />
        </label>
        <button onClick={submitFinal}>Submit Final Result</button>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>
    </div>
  );
}
