// Tiny Dart RPC wrapper for Quantum Jobs (example)
// This file is an example showing how the wallet can call explorer RPCs for quantum jobs.

class QuantumRpcClient {
  final dynamic rpcClient; // expects an object with call(method, params)

  QuantumRpcClient(this.rpcClient);

  Future<List<dynamic>> listJobs({int limit = 50, int offset = 0}) async {
    final res = await rpcClient.call('explorer_list_quantum_jobs', {'limit': limit, 'offset': offset});
    return (res as List<dynamic>?) ?? [];
  }

  Future<Map<String, dynamic>> getJob(String jobId) async {
    final res = await rpcClient.call('explorer_get_quantum_job', {'job_id': jobId});
    return (res as Map<String, dynamic>?) ?? {};
  }

  // Submit job would be an on-chain transaction in practice; this is a helper that
  // constructs a job payload and returns it for signing/submission by wallet code.
  Map<String, dynamic> buildJobPayload({
    required String jobId,
    required String programId,
    required String inputCommitment,
    required int shots,
    required String paymentMax,
    required int deadlineBlock,
  }) {
    return {
      'job_id': jobId,
      'program_id': programId,
      'input_commitment': inputCommitment,
      'shots': shots,
      'payment_max': paymentMax,
      'deadline_block': deadlineBlock,
    };
  }
}
