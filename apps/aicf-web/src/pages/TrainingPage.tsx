import { useState } from 'react';
import { Panel } from '../components/Ui';
import { aicfApi } from '../lib/api';
import { useSession } from '../lib/session';

export function TrainingPage() {
  const { session } = useSession();
  const [datasetUri, setDatasetUri] = useState('s3://aicf-datasets/sample.jsonl');
  const [baseModel, setBaseModel] = useState('aicf-chat-1');
  const [epochs, setEpochs] = useState(3);
  const [budget, setBudget] = useState('3500000000');
  const [message, setMessage] = useState('');

  async function submitTrainingJob() {
    if (!session?.selectedProjectId) {
      setMessage('Select a project first');
      return;
    }

    try {
      const created = await aicfApi.createJob(session, {
        projectId: session.selectedProjectId,
        maxBudgetAnmNanos: budget,
        request: {
          class: 'fine_tuning_training',
          model: baseModel,
          input: {
            dataset_uri: datasetUri,
            epochs,
            learning_rate: 0.00005,
            output_adapter: 'lora'
          },
          timeoutSeconds: 7200,
          replication: 1,
          verificationMode: 'sampled',
          outputMode: 'private',
          challengeWindowSeconds: 1800,
          requiredHardware: {
            minGpuMemoryGb: 24,
            minCpu: 12,
            minRamGb: 64
          }
        }
      });
      setMessage(`Training job queued: ${created.job.id}`);
    } catch (error) {
      setMessage((error as Error).message);
    }
  }

  return (
    <Panel title="Training & Fine-tuning" subtitle="Launch ANM-budgeted fine-tune jobs over provider network">
      <div className="grid two">
        <label>
          Dataset URI
          <input value={datasetUri} onChange={(event) => setDatasetUri(event.target.value)} />
        </label>
        <label>
          Base model
          <input value={baseModel} onChange={(event) => setBaseModel(event.target.value)} />
        </label>
        <label>
          Epochs
          <input type="number" value={epochs} onChange={(event) => setEpochs(Number(event.target.value))} />
        </label>
        <label>
          Max budget (ANM nanos)
          <input value={budget} onChange={(event) => setBudget(event.target.value)} />
        </label>
      </div>
      <button onClick={submitTrainingJob}>Submit training job</button>
      {message ? <p className="muted">{message}</p> : null}
    </Panel>
  );
}
