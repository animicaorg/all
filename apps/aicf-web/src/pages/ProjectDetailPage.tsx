import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import type { Project } from '@animica/aicf-shared';
import { Panel, StatTile } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';
import { sendContractCall } from '../lib/wallet';

export function ProjectDetailPage() {
  const { id } = useParams();
  const { session, setSelectedProjectId } = useSession();
  const [project, setProject] = useState<Project | null>(null);
  const [fundAmount, setFundAmount] = useState('1000000000');
  const [withdrawAmount, setWithdrawAmount] = useState('100000000');
  const [message, setMessage] = useState('');

  const projectId = useMemo(() => id ?? session?.selectedProjectId, [id, session?.selectedProjectId]);

  async function loadProject() {
    if (!session || !projectId) return;
    const projects = await aicfApi.listProjects(session);
    const found = projects.projects.find((item) => item.id === projectId);
    if (!found) {
      setMessage('Project not found');
      return;
    }
    setProject(found);
    setSelectedProjectId(found.id);
  }

  useEffect(() => {
    loadProject().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token, projectId]);

  async function fundProject() {
    if (!session || !projectId) return;
    try {
      const funded = await aicfApi.fundProject(session, projectId, {
        amountAnm: fundAmount
      });
      if (session.user.wallet?.address) {
        const txHash = await sendContractCall({
          from: session.user.wallet.address,
          contractAddress: funded.contractCall.contractAddress,
          method: funded.contractCall.method,
          args: funded.contractCall.args
        });
        setMessage(txHash ? `Funding call submitted: ${txHash}` : 'Funding recorded (wallet call skipped)');
      } else {
        setMessage('Project funded in control-plane. Link wallet to submit chain tx.');
      }
      setProject(funded.project);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function withdrawProject() {
    if (!session || !projectId) return;
    try {
      const result = await aicfApi.withdrawProject(session, projectId, withdrawAmount);
      setProject(result.project);
      setMessage('Withdrawal booked. Submit withdrawal tx via wallet if required by policy.');
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  if (!projectId) {
    return (
      <Panel title="Project" subtitle="Select a project from /app/projects">
        <p className="muted">No active project selected.</p>
      </Panel>
    );
  }

  return (
    <div className="stack">
      <Panel title={project?.name ?? 'Project'} subtitle={project?.id ?? projectId}>
        <div className="stats-inline">
          <StatTile label="Available" value={project?.balance.availableAnm ?? '0'} />
          <StatTile label="Reserved" value={project?.balance.reservedAnm ?? '0'} />
          <StatTile label="Deposited" value={project?.balance.totalDepositedAnm ?? '0'} />
          <StatTile label="Spent" value={project?.balance.totalSpentAnm ?? '0'} />
          <StatTile label="Refunded" value={project?.balance.totalRefundedAnm ?? '0'} />
        </div>
      </Panel>

      <Panel title="Fund Project" subtitle="Approve and deposit ANM into AICF project balance contract">
        <div className="grid two">
          <label>
            Amount ANM nanos
            <input value={fundAmount} onChange={(event) => setFundAmount(event.target.value)} />
          </label>
          <button onClick={fundProject}>Fund Project</button>
        </div>
      </Panel>

      <Panel title="Withdraw" subtitle="Withdraw unused ANM subject to job locks">
        <div className="grid two">
          <label>
            Amount ANM nanos
            <input value={withdrawAmount} onChange={(event) => setWithdrawAmount(event.target.value)} />
          </label>
          <button onClick={withdrawProject}>Request Withdrawal</button>
        </div>
      </Panel>

      {message ? <p className="muted">{message}</p> : null}
    </div>
  );
}
