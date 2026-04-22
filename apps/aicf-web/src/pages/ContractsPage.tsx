import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ContractRecord } from '@animica/aicf-shared';
import { EmptyState, Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function ContractsPage() {
  const { session } = useSession();
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [address, setAddress] = useState('anm1contractxxxxxxx');
  const [name, setName] = useState('my_ai_contract');
  const [type, setType] = useState<'model_call' | 'agent_task' | 'ai_escrow' | 'custom'>('model_call');
  const [message, setMessage] = useState('');

  async function load() {
    if (!session) return;
    const data = await aicfApi.listContracts(session);
    setContracts(data.contracts);
  }

  useEffect(() => {
    load().catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  async function register() {
    if (!session) return;
    try {
      await aicfApi.registerContract(session, {
        address,
        type,
        metadata: {
          name
        }
      });
      setMessage('Contract registered');
      await load();
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="Contracts" subtitle="Register and manage VM-PY contracts that request AICF model/agent jobs">
        <div className="grid two">
          <label>
            Contract address
            <input value={address} onChange={(event) => setAddress(event.target.value)} />
          </label>
          <label>
            Contract name
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            Type
            <select value={type} onChange={(event) => setType(event.target.value as typeof type)}>
              <option value="model_call">model_call</option>
              <option value="agent_task">agent_task</option>
              <option value="ai_escrow">ai_escrow</option>
              <option value="custom">custom</option>
            </select>
          </label>
        </div>
        <button onClick={register}>Register Contract</button>
        {message ? <p className="muted">{message}</p> : null}
      </Panel>

      {contracts.length ? (
        <table>
          <thead>
            <tr>
              <th>Address</th>
              <th>Name</th>
              <th>Type</th>
              <th>Paused</th>
            </tr>
          </thead>
          <tbody>
            {contracts.map((contract) => (
              <tr key={contract.id}>
                <td>
                  <Link to={`/app/contracts/${contract.address}`}>{contract.address}</Link>
                </td>
                <td>{contract.metadata.name}</td>
                <td>{contract.type}</td>
                <td>{String(contract.paused)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No contracts" detail="Register a VM-PY contract to enable deterministic AI job requests." />
      )}
    </div>
  );
}
