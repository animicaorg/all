import { estimateTokenCount } from '@animica/aicf-shared';

export function runTrainingAdapter(input: Record<string, unknown>) {
  const datasetUri = String(input.dataset_uri ?? input.datasetUri ?? 'unknown-dataset');
  const epochs = Number(input.epochs ?? 1);

  return {
    output: {
      status: 'training_completed',
      checkpoints: [`checkpoint://${datasetUri}/epoch-${epochs}`],
      metrics: {
        loss: 0.12,
        accuracy: 0.91
      },
      runtime: 'provider-training-scaffold'
    },
    usage: {
      inputTokens: estimateTokenCount(JSON.stringify(input)),
      outputTokens: 64,
      latencyMs: 1100,
      bytesIn: JSON.stringify(input).length,
      bytesOut: 320
    }
  };
}
