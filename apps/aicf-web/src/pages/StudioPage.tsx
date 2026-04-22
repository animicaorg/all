import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ContractRecord, ModelDefinition, Project } from '@animica/aicf-shared';
import { Panel } from '../components/Ui';
import { clamp, estimateTokenCount, formatAnmNanos, shortAddress } from '../lib/anm';
import { aicfApi } from '../lib/api';
import { deriveNetworkDemand, estimateHelperCostNanos } from '../lib/gpuEconomics';
import { loadMonaco, type MonacoEditorInstance } from '../lib/monacoLoader';
import { useSession } from '../lib/session';
import { compileStudioSource } from '../lib/studioCompiler';
import { connectWallet, getAccounts, getChainId, sendTransaction } from '../lib/wallet';

type StudioExample = {
  id: string;
  name: string;
  description: string;
  contractType: 'model_call' | 'agent_task' | 'ai_escrow' | 'custom';
  code: string;
};

type HelperMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
  chargedNanos?: bigint;
  minerShareNanos?: bigint;
  demandMultiplier?: number;
  jobId?: string;
};

type HelperUsage = {
  id: string;
  chargedNanos: bigint;
  minerShareNanos: bigint;
  demandMultiplier: number;
  model: string;
  jobId: string;
  createdAt: string;
};

const STUDIO_EXAMPLES: StudioExample[] = [
  {
    id: 'escrow-guard',
    name: 'Escrow Guard + Callback',
    description: 'Reserves ANM, records provider commitments, and supports requester acceptance flow.',
    contractType: 'ai_escrow',
    code: `state = {
  "requester": "",
  "budget_anm_nanos": 0,
  "accepted_hash": "",
  "status": "idle"
}

def deploy(requester: str, budget_anm_nanos: int):
  state["requester"] = requester
  state["budget_anm_nanos"] = budget_anm_nanos
  state["status"] = "funded"


def submit_result(provider: str, result_hash: str):
  assert state["status"] in ["funded", "running"]
  state["status"] = "result_submitted"
  emit("RESULT_SUBMITTED", {
    "provider": provider,
    "result_hash": result_hash
  })


def accept_result(caller: str, result_hash: str):
  assert caller == state["requester"]
  assert state["status"] == "result_submitted"
  state["accepted_hash"] = result_hash
  state["status"] = "accepted"
  emit("RESULT_ACCEPTED", {"hash": result_hash})


def finalize(provider: str):
  assert state["status"] == "accepted"
  state["status"] = "paid"
  emit("ESCROW_FINALIZED", {
    "provider": provider,
    "budget_anm_nanos": state["budget_anm_nanos"]
  })
`
  },
  {
    id: 'agent-pipeline',
    name: 'Agent Pipeline Contract',
    description: 'Tracks multi-step agent execution with deterministic step commitments.',
    contractType: 'agent_task',
    code: `state = {
  "requester": "",
  "step": 0,
  "budget": 0,
  "final_hash": "",
  "completed": False
}


def deploy(requester: str, budget_anm_nanos: int):
  state["requester"] = requester
  state["budget"] = budget_anm_nanos


def append_step(provider: str, commitment_hash: str, trace_ref: str):
  assert not state["completed"]
  state["step"] += 1
  emit("AGENT_STEP", {
    "step": state["step"],
    "provider": provider,
    "commitment": commitment_hash,
    "trace_ref": trace_ref
  })


def finalize(caller: str, result_hash: str):
  assert caller == state["requester"]
  assert state["step"] > 0
  state["final_hash"] = result_hash
  state["completed"] = True
  emit("AGENT_FINAL", {
    "result_hash": result_hash,
    "total_steps": state["step"]
  })
`
  },
  {
    id: 'routing-policy',
    name: 'Provider Routing Policy',
    description: 'Enforces min benchmark and allowed region policy before execution.',
    contractType: 'model_call',
    code: `state = {
  "min_score": 90,
  "allowed_regions": ["eu-central", "us-east"],
  "latest_provider": "",
  "latest_hash": ""
}


def deploy(min_score: int):
  state["min_score"] = min_score


def authorize(provider_id: str, provider_score: int, region: str):
  assert provider_score >= state["min_score"]
  assert region in state["allowed_regions"]
  emit("PROVIDER_AUTHORIZED", {
    "provider": provider_id,
    "score": provider_score,
    "region": region
  })


def submit_hash(provider_id: str, result_hash: str):
  state["latest_provider"] = provider_id
  state["latest_hash"] = result_hash
  emit("RESULT_HASH", {
    "provider": provider_id,
    "hash": result_hash
  })


def get_latest() -> str:
  return state["latest_hash"]
`
  }
];

const GPU_HELPER_SYSTEM_PROMPT = `You are AICF GPU Code Helper.
You must provide production-quality guidance for Animica smart contracts and AICF jobs.
When returning code, use fenced python blocks and deterministic logic.
Always mention security checks, budget controls, and settlement/dispute implications.`;

function extractCodeBlock(text: string): string {
  const match = text.match(/```(?:python|py|vmpy)?\n([\s\S]*?)```/i);
  return match?.[1]?.trim() ?? '';
}

async function sha256Hex(value: string) {
  const encoded = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest('SHA-256', encoded);
  return `0x${Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')}`;
}

function MonacoContractEditor({
  value,
  onChange
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<MonacoEditorInstance | null>(null);
  const onChangeRef = useRef(onChange);
  const lastValueRef = useRef(value);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    let disposed = false;
    let changeDisposable: { dispose: () => void } | null = null;

    async function setup() {
      if (!containerRef.current) return;
      try {
        const monaco = await loadMonaco();
        if (disposed || !containerRef.current) return;

        monaco.editor.defineTheme('aicfNebula', {
          base: 'vs-dark',
          inherit: true,
          rules: [
            { token: 'comment', foreground: '7f9ba9' },
            { token: 'keyword', foreground: 'ffca7a' },
            { token: 'string', foreground: '8de6ce' },
            { token: 'number', foreground: 'f79ec2' }
          ],
          colors: {
            'editor.background': '#0a1220',
            'editorLineNumber.foreground': '#4d5f71',
            'editorCursor.foreground': '#8de6ce',
            'editor.selectionBackground': '#23456b66'
          }
        });
        monaco.editor.setTheme('aicfNebula');

        const editor = monaco.editor.create(containerRef.current, {
          value,
          language: 'python',
          minimap: { enabled: false },
          roundedSelection: true,
          automaticLayout: true,
          fontFamily: 'IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace',
          fontSize: 14,
          lineHeight: 21,
          scrollBeyondLastLine: false,
          tabSize: 2
        });

        editorRef.current = editor;
        lastValueRef.current = value;

        changeDisposable = editor.onDidChangeModelContent(() => {
          const next = editor.getValue();
          lastValueRef.current = next;
          onChangeRef.current(next);
        });

        const onResize = () => editor.layout();
        window.addEventListener('resize', onResize);

        return () => {
          window.removeEventListener('resize', onResize);
        };
      } catch (error) {
        setLoadError((error as Error).message);
      }
      return undefined;
    }

    let removeResize: (() => void) | undefined;
    setup()
      .then((cleanup) => {
        removeResize = cleanup;
      })
      .catch((error) => {
        setLoadError((error as Error).message);
      });

    return () => {
      disposed = true;
      changeDisposable?.dispose();
      removeResize?.();
      editorRef.current?.dispose();
      editorRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!editorRef.current) return;
    if (value === lastValueRef.current) return;
    editorRef.current.setValue(value);
    lastValueRef.current = value;
  }, [value]);

  if (loadError) {
    return (
      <div className="editor-fallback">
        <p className="muted">Monaco failed to load, using fallback textarea. {loadError}</p>
        <textarea rows={20} value={value} onChange={(event) => onChange(event.target.value)} />
      </div>
    );
  }

  return <div className="monaco-container" ref={containerRef} />;
}

export function StudioPage() {
  const { session, setSelectedProjectId } = useSession();
  const [searchParams] = useSearchParams();

  const [models, setModels] = useState<ModelDefinition[]>([]);
  const [contracts, setContracts] = useState<ContractRecord[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [statusPayload, setStatusPayload] = useState<Record<string, unknown> | null>(null);

  const [selectedExampleId, setSelectedExampleId] = useState(STUDIO_EXAMPLES[0].id);
  const [chatModel, setChatModel] = useState('aicf-chat-1');
  const [studioObjective, setStudioObjective] = useState(
    'Create a deterministic contract that limits provider selection to benchmark >= 92 and settles ANM via acceptance.'
  );
  const [generatedCode, setGeneratedCode] = useState(STUDIO_EXAMPLES[0].code);

  const [contractAddress, setContractAddress] = useState('anm1contractstudioxxxx');
  const [contractName, setContractName] = useState('studio_generated_contract');
  const [contractType, setContractType] = useState<'model_call' | 'agent_task' | 'ai_escrow' | 'custom'>(
    STUDIO_EXAMPLES[0].contractType
  );
  const [requester, setRequester] = useState('anm1requesterxxxx');
  const [payer, setPayer] = useState('anm1payerxxxx');
  const [taskBudget, setTaskBudget] = useState('4000000000');

  const [compiledCodeBytes, setCompiledCodeBytes] = useState<number[]>([]);
  const [compiledCodeHex, setCompiledCodeHex] = useState('');
  const [compiledAbiJson, setCompiledAbiJson] = useState('');
  const [compileDiagnostics, setCompileDiagnostics] = useState<string[]>([]);
  const [deployManifest, setDeployManifest] = useState('');
  const [deployTxHash, setDeployTxHash] = useState('');
  const [isDeploying, setIsDeploying] = useState(false);
  const [artifactRefs, setArtifactRefs] = useState<{ sourceRef?: string; abiRef?: string }>({});

  const [helperOpen, setHelperOpen] = useState(searchParams.get('panel') === 'helper');
  const [helperPrompt, setHelperPrompt] = useState('');
  const [helperBusy, setHelperBusy] = useState(false);
  const [helperMessages, setHelperMessages] = useState<HelperMessage[]>([
    {
      id: 'helper-init',
      role: 'assistant',
      content:
        'GPU Code Helper ready. Ask for contract snippets, security review, or gas/settlement flow improvements. Each request creates a metered ANM workload routed to provider miners.',
      createdAt: new Date().toISOString()
    }
  ]);
  const [helperUsage, setHelperUsage] = useState<HelperUsage[]>([]);
  const [helperApiKey, setHelperApiKey] = useState<{ projectId: string; token: string } | null>(null);
  const [message, setMessage] = useState('');

  const selectedExample = useMemo(
    () => STUDIO_EXAMPLES.find((example) => example.id === selectedExampleId) ?? STUDIO_EXAMPLES[0],
    [selectedExampleId]
  );
  const chatModels = useMemo(() => models.filter((model) => model.type === 'chat' && model.status === 'active'), [models]);
  const demand = useMemo(() => deriveNetworkDemand(statusPayload), [statusPayload]);
  const activeProjectId = session?.selectedProjectId ?? projects[0]?.id;
  const selectedModel = useMemo(
    () => chatModels.find((model) => model.name === chatModel) ?? chatModels[0],
    [chatModel, chatModels]
  );

  const totalHelperSpend = useMemo(
    () => helperUsage.reduce((sum, row) => sum + row.chargedNanos, 0n),
    [helperUsage]
  );
  const totalMinerShare = useMemo(
    () => helperUsage.reduce((sum, row) => sum + row.minerShareNanos, 0n),
    [helperUsage]
  );

  const nextPromptTokens = estimateTokenCount(helperPrompt);
  const nextEstimatedCost = estimateHelperCostNanos({
    promptTokens: nextPromptTokens,
    expectedCompletionTokens: clamp(380 + nextPromptTokens, 260, 1400),
    demandMultiplier: demand.demandMultiplier
  });
  const nextMinerShare = (nextEstimatedCost * BigInt(selectedModel?.pricing.providerShareBps ?? 7000)) / 10_000n;

  useEffect(() => {
    if (searchParams.get('panel') === 'helper') {
      setHelperOpen(true);
    }
  }, [searchParams]);

  useEffect(() => {
    if (session?.user.wallet?.address) {
      setRequester(session.user.wallet.address);
      setPayer(session.user.wallet.address);
    }
  }, [session?.user.wallet?.address]);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [statusResult, modelPayload] = await Promise.all([aicfApi.status(), aicfApi.listModels()]);
        if (!active) return;
        setStatusPayload(statusResult);
        setModels(modelPayload.data);

        const preferred = modelPayload.data.find((model) => model.type === 'chat' && model.status === 'active');
        if (preferred) {
          setChatModel((prev) => prev || preferred.name);
        }

        if (!session) {
          setContracts([]);
          setProjects([]);
          return;
        }

        const [contractsPayload, projectsPayload] = await Promise.all([
          aicfApi.listContracts(session),
          aicfApi.listProjects(session)
        ]);
        if (!active) return;
        setContracts(contractsPayload.contracts);
        setProjects(projectsPayload.projects);

        if (!session.selectedProjectId && projectsPayload.projects[0]) {
          setSelectedProjectId(projectsPayload.projects[0].id);
        }
      } catch (error) {
        if (active) {
          setMessage((error as Error).message);
        }
      }
    }

    load().catch(() => undefined);

    return () => {
      active = false;
    };
  }, [session, setSelectedProjectId]);

  useEffect(() => {
    const example = STUDIO_EXAMPLES.find((row) => row.id === selectedExampleId);
    if (!example) return;
    setContractType(example.contractType);
  }, [selectedExampleId]);

  function useExample() {
    setGeneratedCode(selectedExample.code);
    setContractName(`${selectedExample.id.replace(/-/g, '_')}_contract`);
    setContractType(selectedExample.contractType);
    setStudioObjective(
      `Build on ${selectedExample.name}: ${selectedExample.description}. Ensure deterministic execution and escrow-safe settlement.`
    );
    setMessage(`Loaded example: ${selectedExample.name}`);
  }

  async function compileDraft() {
    if (!generatedCode.trim()) {
      setMessage('Contract source is empty');
      return;
    }

    try {
      const output = await compileStudioSource(generatedCode, contractName.trim() || 'studio_contract');
      setCompiledCodeBytes(Array.from(output.codeBytes));
      setCompiledCodeHex(output.codeHex);
      setCompiledAbiJson(JSON.stringify(output.abi, null, 2));
      setDeployManifest(JSON.stringify(output.manifest, null, 2));
      setCompileDiagnostics(output.diagnostics);
      setMessage(`Compile complete: ${output.codeBytes.length} bytes`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function deployCompiledContract() {
    if (compiledCodeBytes.length === 0) {
      setMessage('Compile the contract first');
      return;
    }

    setIsDeploying(true);
    try {
      await connectWallet();
      const accounts = await getAccounts();
      if (!accounts[0]) {
        throw new Error('No wallet account connected');
      }
      const chainId = (await getChainId()) ?? 1337;
      const manifest = deployManifest.trim() ? JSON.parse(deployManifest) : {};

      const txHash = await sendTransaction({
        chainId,
        from: accounts[0],
        to: null,
        value: '0',
        data: {
          code: compiledCodeBytes,
          manifest
        },
        gasPrice: '1',
        gasLimit: 1_200_000
      });

      if (!txHash) {
        throw new Error('Wallet rejected deployment transaction');
      }
      setDeployTxHash(txHash);
      setMessage(`Deployment transaction submitted: ${txHash}`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setIsDeploying(false);
    }
  }

  async function registerContract() {
    if (!session) {
      setMessage('Wallet sign-in is required to register contracts');
      return;
    }

    try {
      const registerPayload = await aicfApi.registerContract(session, {
        address: contractAddress,
        type: contractType,
        metadata: {
          name: contractName,
          description: `Created in Studio from ${selectedExample.name}`,
          tags: ['studio', 'monaco', selectedExample.id]
        }
      });

      let refs: { sourceRef?: string; abiRef?: string } = {
        sourceRef: registerPayload.contract.metadata.sourceRef,
        abiRef: registerPayload.contract.metadata.abiRef
      };

      if (generatedCode.trim() || compiledAbiJson.trim()) {
        const artifactPayload = await aicfApi.upsertContractArtifacts(session, contractAddress, {
          sourceCode: generatedCode.trim() || undefined,
          sourceLanguage: 'vmpy',
          abiJson: compiledAbiJson.trim() || undefined
        });
        refs = {
          sourceRef: artifactPayload.contract.metadata.sourceRef,
          abiRef: artifactPayload.contract.metadata.abiRef
        };
      }

      setArtifactRefs(refs);

      const listPayload = await aicfApi.listContracts(session);
      setContracts(listPayload.contracts);
      setMessage(`Contract registered: ${contractAddress}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function createStarterAgentTask() {
    if (!session) {
      setMessage('Wallet sign-in is required to create agent tasks');
      return;
    }

    try {
      const created = await aicfApi.createAgentTask(session, {
        contractAddress,
        requester,
        payer,
        modelId: chatModel,
        budgetAnmNanos: taskBudget
      });

      const commitmentHash = await sha256Hex(`${generatedCode}\n${helperMessages.map((m) => m.content).join('\n')}`);
      await aicfApi.appendAgentTaskStep(session, created.task.id, {
        commitmentHash,
        traceRef: 'studio://gpu-helper-seed'
      });
      setMessage(`Created starter agent task ${created.task.id}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  async function ensureHelperApiToken(projectId: string): Promise<string> {
    if (!session) {
      throw new Error('Wallet sign-in is required to use GPU helper');
    }

    if (helperApiKey?.projectId === projectId) {
      return helperApiKey.token;
    }

    const keyPayload = await aicfApi.createApiKey(session, projectId, {
      name: `gpu-helper-${new Date().toISOString().replace(/[:.]/g, '-')}`,
      scopes: ['inference:chat', 'jobs:write', 'jobs:read']
    });

    setHelperApiKey({ projectId, token: keyPayload.token });
    return keyPayload.token;
  }

  async function submitHelperPrompt() {
    const prompt = helperPrompt.trim();
    if (!prompt) {
      return;
    }

    if (!session) {
      setMessage('Wallet sign-in is required before using GPU helper');
      return;
    }
    if (!activeProjectId) {
      setMessage('Create/select a project before using GPU helper');
      return;
    }

    setHelperBusy(true);
    setMessage('');

    const userMessage: HelperMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: prompt,
      createdAt: new Date().toISOString()
    };
    const nextMessages = [...helperMessages, userMessage];
    setHelperMessages(nextMessages);
    setHelperPrompt('');

    try {
      const promptTokens = estimateTokenCount(prompt);
      const expectedCompletionTokens = clamp(420 + promptTokens, 260, 1800);
      const estimatedCostNanos = estimateHelperCostNanos({
        promptTokens,
        expectedCompletionTokens,
        demandMultiplier: demand.demandMultiplier
      });
      const providerShareBps = selectedModel?.pricing.providerShareBps ?? 7000;
      const reserveBudgetNanos = estimatedCostNanos * 4n + 500_000n;

      const reservedJob = await aicfApi.createJob(session, {
        projectId: activeProjectId,
        maxBudgetAnmNanos: reserveBudgetNanos.toString(),
        request: {
          class: 'chat_inference',
          model: chatModel,
          input: {
            source: 'gpu_code_helper',
            prompt,
            demandMultiplier: demand.demandMultiplier,
            estimatedCostNanos: estimatedCostNanos.toString()
          },
          timeoutSeconds: 180,
          replication: 1,
          verificationMode: 'sampled',
          outputMode: 'private',
          challengeWindowSeconds: 90
        }
      });

      const helperToken = await ensureHelperApiToken(activeProjectId);
      const completion = await aicfApi.chatCompletions(
        {
          model: chatModel,
          max_tokens: expectedCompletionTokens,
          messages: [
            {
              role: 'system',
              content: GPU_HELPER_SYSTEM_PROMPT
            },
            ...nextMessages.slice(-10).map((entry) => ({
              role: entry.role,
              content: entry.content
            }))
          ],
          metadata: {
            source: 'aicf-web-gpu-helper',
            projectId: activeProjectId,
            reservedJobId: reservedJob.job.id,
            demandMultiplier: demand.demandMultiplier,
            estimatedCostNanos: estimatedCostNanos.toString()
          }
        },
        helperToken
      );

      const assistantText = completion.choices?.[0]?.message?.content?.trim() ?? 'No response content returned.';
      const aicfMeta = (completion.aicf ?? {}) as Record<string, unknown>;
      const chargedRaw = aicfMeta.charged_anm_nanos;
      const chargedNanos =
        typeof chargedRaw === 'string' && /^\d+$/.test(chargedRaw) ? BigInt(chargedRaw) : estimatedCostNanos;
      const minerShareNanos = (chargedNanos * BigInt(providerShareBps)) / 10_000n;

      const usageRow: HelperUsage = {
        id: `usage-${Date.now()}`,
        chargedNanos,
        minerShareNanos,
        demandMultiplier: demand.demandMultiplier,
        model: chatModel,
        jobId: reservedJob.job.id,
        createdAt: new Date().toISOString()
      };
      setHelperUsage((prev) => [usageRow, ...prev]);

      setHelperMessages((prev) => [
        ...prev,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: assistantText,
          createdAt: usageRow.createdAt,
          chargedNanos,
          minerShareNanos,
          demandMultiplier: demand.demandMultiplier,
          jobId: reservedJob.job.id
        }
      ]);
      setMessage(
        `GPU helper response received. Charged ${formatAnmNanos(chargedNanos, 6)} with ${formatAnmNanos(
          minerShareNanos,
          6
        )} routed to provider miners.`
      );
    } catch (error) {
      setHelperMessages((prev) => [
        ...prev,
        {
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          content: `Request failed: ${(error as Error).message}`,
          createdAt: new Date().toISOString()
        }
      ]);
      setMessage((error as Error).message);
    } finally {
      setHelperBusy(false);
    }
  }

  function applyLatestHelperCode() {
    const latestAssistant = [...helperMessages]
      .reverse()
      .find((entry) => entry.role === 'assistant' && extractCodeBlock(entry.content));

    if (!latestAssistant) {
      setMessage('No assistant code block found yet');
      return;
    }

    const code = extractCodeBlock(latestAssistant.content);
    setGeneratedCode(code);
    setMessage('Applied latest helper code block to Monaco editor');
  }

  function queueObjectiveForHelper() {
    const objective = studioObjective.trim();
    if (!objective) return;
    setHelperPrompt(objective);
    setHelperOpen(true);
  }

  return (
    <div className="stack studio-page">
      <Panel title="AICF Studio" subtitle="Monaco-first contract development with live GPU demand-aware coding assistance.">
        <div className="grid two">
          <label>
            Example contract
            <select value={selectedExampleId} onChange={(event) => setSelectedExampleId(event.target.value)}>
              {STUDIO_EXAMPLES.map((example) => (
                <option key={example.id} value={example.id}>
                  {example.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Chat model
            <select value={chatModel} onChange={(event) => setChatModel(event.target.value)}>
              {chatModels.length ? (
                chatModels.map((model) => (
                  <option key={model.name} value={model.name}>
                    {model.name}
                  </option>
                ))
              ) : (
                <option value="aicf-chat-1">aicf-chat-1</option>
              )}
            </select>
          </label>
        </div>

        <label>
          Project context for helper billing
          <select
            disabled={!projects.length}
            onChange={(event) => setSelectedProjectId(event.target.value || undefined)}
            value={activeProjectId ?? ''}
          >
            {projects.length ? (
              projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name} ({project.slug})
                </option>
              ))
            ) : (
              <option value="">No project found</option>
            )}
          </select>
        </label>

        <div className="stats-inline">
          <StatTileLike label="Demand" value={demand.demandLabel.toUpperCase()} hint={`x${demand.demandMultiplier.toFixed(2)} helper pricing`} />
          <StatTileLike label="Queue Units" value={String(demand.counts.jobs + demand.counts.contractJobs + demand.counts.agentTasks)} hint="network pressure" />
          <StatTileLike label="Helper Spend" value={formatAnmNanos(totalHelperSpend, 6)} hint={`${formatAnmNanos(totalMinerShare, 6)} to miners`} />
          <StatTileLike label="Active Project" value={activeProjectId ? shortAddress(activeProjectId) : 'none'} hint="required for helper" />
        </div>

        <p className="muted">{selectedExample.description}</p>
        <div className="row">
          <button onClick={useExample} type="button">
            Load example into editor
          </button>
          <button onClick={() => setHelperOpen((prev) => !prev)} type="button">
            {helperOpen ? 'Hide GPU helper' : 'Open GPU helper'}
          </button>
          <button onClick={applyLatestHelperCode} type="button">
            Apply latest helper code
          </button>
        </div>

        <label>
          Objective for helper
          <textarea
            rows={4}
            value={studioObjective}
            onChange={(event) => setStudioObjective(event.target.value)}
            placeholder="Describe what contract behavior you want and send it to GPU helper."
          />
        </label>
        <button onClick={queueObjectiveForHelper} type="button">
          Send objective to helper window
        </button>
      </Panel>

      <Panel title="Monaco Contract Editor" subtitle="Author Animica VM-PY contracts with syntax highlighting and immediate edits.">
        <MonacoContractEditor value={generatedCode} onChange={setGeneratedCode} />
      </Panel>

      <Panel title="Compile + Deploy" subtitle="Compile local source and submit deploy transaction from wallet.">
        <div className="row">
          <button onClick={compileDraft} type="button">
            Compile draft
          </button>
          <button disabled={isDeploying || compiledCodeBytes.length === 0} onClick={deployCompiledContract} type="button">
            {isDeploying ? 'Submitting deploy tx...' : 'Deploy from wallet'}
          </button>
        </div>

        {compiledCodeHex ? (
          <div className="stack">
            <label>
              Deploy manifest
              <textarea rows={8} value={deployManifest} onChange={(event) => setDeployManifest(event.target.value)} />
            </label>
            <label>
              Compiled ABI JSON
              <textarea rows={10} value={compiledAbiJson} onChange={(event) => setCompiledAbiJson(event.target.value)} />
            </label>
            <p className="muted">
              Code hex preview: <code>{compiledCodeHex.slice(0, 96)}...</code>
            </p>
            {compileDiagnostics.length ? <pre>{compileDiagnostics.join('\n')}</pre> : null}
          </div>
        ) : null}

        {deployTxHash ? (
          <p className="muted">
            Deployment tx hash: <code>{deployTxHash}</code>
          </p>
        ) : null}
      </Panel>

      <Panel title="Register + Launch" subtitle="Persist contract metadata/artifacts and create starter agent task.">
        <div className="grid two">
          <label>
            Deployed contract address
            <input value={contractAddress} onChange={(event) => setContractAddress(event.target.value)} />
          </label>
          <label>
            Contract name
            <input value={contractName} onChange={(event) => setContractName(event.target.value)} />
          </label>
          <label>
            Contract type
            <select value={contractType} onChange={(event) => setContractType(event.target.value as typeof contractType)}>
              <option value="model_call">model_call</option>
              <option value="agent_task">agent_task</option>
              <option value="ai_escrow">ai_escrow</option>
              <option value="custom">custom</option>
            </select>
          </label>
          <label>
            Agent task budget (ANM nanos)
            <input value={taskBudget} onChange={(event) => setTaskBudget(event.target.value)} />
          </label>
          <label>
            Requester wallet
            <input value={requester} onChange={(event) => setRequester(event.target.value)} />
          </label>
          <label>
            Payer wallet
            <input value={payer} onChange={(event) => setPayer(event.target.value)} />
          </label>
        </div>

        <div className="row">
          <button onClick={registerContract} type="button">
            Register contract + artifacts
          </button>
          <button onClick={createStarterAgentTask} type="button">
            Create starter agent task
          </button>
        </div>

        {artifactRefs.sourceRef || artifactRefs.abiRef ? (
          <p className="muted">
            Stored refs: source=<code>{artifactRefs.sourceRef ?? 'n/a'}</code>, abi=<code>{artifactRefs.abiRef ?? 'n/a'}</code>
          </p>
        ) : null}

        {contracts.length ? (
          <table>
            <thead>
              <tr>
                <th>Address</th>
                <th>Name</th>
                <th>Type</th>
                <th>Use</th>
              </tr>
            </thead>
            <tbody>
              {contracts.slice(0, 8).map((contract) => (
                <tr key={contract.id}>
                  <td>{contract.address}</td>
                  <td>{contract.metadata.name}</td>
                  <td>{contract.type}</td>
                  <td>
                    <button
                      onClick={() => {
                        setContractAddress(contract.address);
                        setContractName(contract.metadata.name);
                        setContractType(contract.type);
                        setArtifactRefs({
                          sourceRef: contract.metadata.sourceRef,
                          abiRef: contract.metadata.abiRef
                        });
                      }}
                      type="button"
                    >
                      Use
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No registered contracts yet for this account.</p>
        )}
      </Panel>

      {message ? <p className="muted">{message}</p> : null}

      <button className="helper-launcher" onClick={() => setHelperOpen((prev) => !prev)} type="button">
        {helperOpen ? 'Close GPU Helper' : 'GPU Code Helper'}
      </button>

      {helperOpen ? (
        <section className="helper-window" aria-label="GPU Code Helper window">
          <header>
            <h3>GPU Code Helper</h3>
            <div className="row">
              <span className="pill">Demand {demand.demandLabel.toUpperCase()}</span>
              <span className="pill">Next est. {formatAnmNanos(nextEstimatedCost, 6)}</span>
              <button onClick={() => setHelperOpen(false)} type="button">
                Close
              </button>
            </div>
          </header>

          <div className="helper-metrics">
            <div className="stat-tile">
              <span>Total Spend</span>
              <strong>{formatAnmNanos(totalHelperSpend, 6)}</strong>
              <small>from {helperUsage.length} helper calls</small>
            </div>
            <div className="stat-tile">
              <span>To Miners</span>
              <strong>{formatAnmNanos(totalMinerShare, 6)}</strong>
              <small>provider reward share</small>
            </div>
            <div className="stat-tile">
              <span>Next Miner Cut</span>
              <strong>{formatAnmNanos(nextMinerShare, 6)}</strong>
              <small>estimated for current prompt</small>
            </div>
          </div>

          <div className="helper-chat">
            {helperMessages.map((entry) => (
              <article key={entry.id} className={entry.role === 'assistant' ? 'helper-message assistant' : 'helper-message user'}>
                <h4>{entry.role === 'assistant' ? 'GPU Helper' : 'You'}</h4>
                <p>{entry.content}</p>
                {entry.chargedNanos ? (
                  <small>
                    Charged {formatAnmNanos(entry.chargedNanos, 6)} | miners {formatAnmNanos(entry.minerShareNanos ?? 0n, 6)} |
                    demand x{entry.demandMultiplier?.toFixed(2) ?? '1.00'}
                  </small>
                ) : null}
              </article>
            ))}
          </div>

          <label>
            Ask coding helper
            <textarea
              rows={4}
              value={helperPrompt}
              onChange={(event) => setHelperPrompt(event.target.value)}
              placeholder="Example: Add a challenge window and slash path to this contract, then provide tests."
            />
          </label>

          <div className="row">
            <button disabled={helperBusy} onClick={submitHelperPrompt} type="button">
              {helperBusy ? 'Routing paid GPU request...' : 'Send paid helper request'}
            </button>
            <button
              onClick={() => {
                setHelperMessages([
                  {
                    id: 'helper-reset',
                    role: 'assistant',
                    content: 'Context reset. Share your next contract question.',
                    createdAt: new Date().toISOString()
                  }
                ]);
              }}
              type="button"
            >
              Clear chat
            </button>
          </div>

          {helperUsage.length ? (
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Model</th>
                  <th>Charged</th>
                  <th>Miner Share</th>
                  <th>Demand</th>
                  <th>Job</th>
                </tr>
              </thead>
              <tbody>
                {helperUsage.slice(0, 6).map((row) => (
                  <tr key={row.id}>
                    <td>{row.createdAt.replace('T', ' ').slice(0, 19)}</td>
                    <td>{row.model}</td>
                    <td>{formatAnmNanos(row.chargedNanos, 6)}</td>
                    <td>{formatAnmNanos(row.minerShareNanos, 6)}</td>
                    <td>x{row.demandMultiplier.toFixed(2)}</td>
                    <td>{shortAddress(row.jobId)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function StatTileLike({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}
