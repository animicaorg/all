import { useEffect, useState } from 'react';
import type { ContractRecord } from '@animica/aicf-shared';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';
import { connectWallet, getAccounts, sendContractCall } from '../lib/wallet';

export function NewContractJobPage() {
  const { session } = useSession();
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [contractAddress, setContractAddress] = useState('');
  const [requester, setRequester] = useState('anm1requesterxxxx');
  const [payer, setPayer] = useState('anm1payerxxxx');
  const [modelId, setModelId] = useState('aicf-chat-1');
  const [jobType, setJobType] = useState<'model_call' | 'embedding' | 'classification' | 'custom'>('model_call');
  const [inputRefHash, setInputRefHash] = useState('0xinputhash');
  const [budget, setBudget] = useState('2500000000');
  const [timeout, setTimeoutSeconds] = useState(600);
  const [mode, setMode] = useState<'SINGLE_PROVIDER' | 'QUORUM_MATCH' | 'VERIFIER_REVIEW' | 'CALLBACK_ACCEPT'>(
    'SINGLE_PROVIDER'
  );
  const [replication, setReplication] = useState(1);
  const [quorum, setQuorum] = useState(1);
  const [message, setMessage] = useState('');
  const [contractCalls, setContractCalls] = useState<{
    approve?: { contractAddress: string; method: string; args: Record<string, unknown> };
    fund?: { contractAddress: string; method: string; args: Record<string, unknown> };
    reserve?: { contractAddress: string; method: string; args: Record<string, unknown> };
  } | null>(null);

  useEffect(() => {
    if (!session) return;
    aicfApi
      .listContracts(session)
      .then((data) => {
        setContracts(data.contracts);
        if (!contractAddress && data.contracts[0]) {
          setContractAddress(data.contracts[0].address);
        }
      })
      .catch((error) => setMessage((error as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.token]);

  async function create() {
    if (!session) return;
    try {
      const created = await aicfApi.createContractJob(session, {
        contractAddress,
        requester,
        payer,
        modelId,
        jobType,
        inputRefHash,
        maxBudgetAnmNanos: budget,
        timeoutSeconds: timeout,
        replication,
        quorum,
        verificationMode: mode,
        challengeWindowSeconds: 900,
        providerPolicy: {
          mode: 'open',
          providerIds: []
        },
        privacy: 'private',
        callbackMode: mode === 'CALLBACK_ACCEPT' ? 'requester_accept' : 'none',
        resultType: jobType === 'embedding' ? 'embeddings_artifact' : 'json'
      });
      setMessage(`Created ${created.job.id}`);
      setContractCalls(created.contractCalls as typeof contractCalls);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function submitWalletCall(
    call: { contractAddress: string; method: string; args: Record<string, unknown> } | undefined,
    label: string
  ) {
    if (!call) {
      setMessage(`No ${label} call prepared`);
      return;
    }
    try {
      await connectWallet();
      const accounts = await getAccounts();
      if (!accounts[0]) {
        setMessage('Wallet account not found');
        return;
      }
      const txHash = await sendContractCall({
        from: accounts[0],
        contractAddress: call.contractAddress,
        method: call.method,
        args: call.args
      });
      setMessage(`${label} tx submitted: ${txHash ?? 'unknown hash'}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <div className="stack">
      <Panel title="New Contract Job" subtitle="Create deterministic model/agent job intent and escrow budget in ANM">
        <div className="grid two">
          <label>
            Contract
            <select value={contractAddress} onChange={(event) => setContractAddress(event.target.value)}>
              {contracts.map((contract) => (
                <option key={contract.id} value={contract.address}>
                  {contract.address} ({contract.metadata.name})
                </option>
              ))}
            </select>
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
            Job type
            <select value={jobType} onChange={(event) => setJobType(event.target.value as typeof jobType)}>
              <option value="model_call">model_call</option>
              <option value="embedding">embedding</option>
              <option value="classification">classification</option>
              <option value="custom">custom</option>
            </select>
          </label>
          <label>
            Verification mode
            <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
              <option value="SINGLE_PROVIDER">SINGLE_PROVIDER</option>
              <option value="QUORUM_MATCH">QUORUM_MATCH</option>
              <option value="VERIFIER_REVIEW">VERIFIER_REVIEW</option>
              <option value="CALLBACK_ACCEPT">CALLBACK_ACCEPT</option>
            </select>
          </label>
          <label>
            Replication
            <input type="number" value={replication} onChange={(event) => setReplication(Number(event.target.value))} />
          </label>
          <label>
            Quorum
            <input type="number" value={quorum} onChange={(event) => setQuorum(Number(event.target.value))} />
          </label>
          <label>
            Max budget (ANM nanos)
            <input value={budget} onChange={(event) => setBudget(event.target.value)} />
          </label>
          <label>
            Timeout (seconds)
            <input type="number" value={timeout} onChange={(event) => setTimeoutSeconds(Number(event.target.value))} />
          </label>
        </div>
        <label>
          Input reference hash
          <input value={inputRefHash} onChange={(event) => setInputRefHash(event.target.value)} />
        </label>
        <button onClick={create}>Create Contract Job</button>
        {contractCalls ? (
          <div className="row">
            <button onClick={() => submitWalletCall(contractCalls.approve, 'approve')}>Approve ANM</button>
            <button onClick={() => submitWalletCall(contractCalls.fund, 'fund')}>Fund Escrow</button>
            <button onClick={() => submitWalletCall(contractCalls.reserve, 'reserve')}>Reserve Budget</button>
          </div>
        ) : null}
        {message ? <p className="muted">{message}</p> : null}
      </Panel>
    </div>
  );
}
