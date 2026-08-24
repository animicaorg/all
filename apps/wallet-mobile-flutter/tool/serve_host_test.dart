// Standalone host harness for ServeWorkerCore — plain `dart run`, no Flutter.
//
// Wires the ported AICF worker loop to any OpenAI-compatible
// /v1/chat/completions endpoint (a local llama-server, the bundled Android
// engine over adb forward, ollama, …) and prints every event. Used to prove
// the PROTOCOL port against the real queue before the native engine lands.
//
//   dart run tool/serve_host_test.dart \
//     --address anim1… \
//     --openai-url http://127.0.0.1:8080/v1/chat/completions \
//     [--rpc-url https://rpc.animica.org/rpc] [--max-jobs 3] [--tier free]
//
// Exits after --max-jobs jobs have been answered (won or lost) or after
// 10 minutes, whichever comes first. NOTE: this registers the address as a
// live worker on the target network — run it deliberately.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:animica_wallet/services/emb1.dart';
import 'package:animica_wallet/services/serve_worker_core.dart';

String? _arg(List<String> args, String name) {
  final i = args.indexOf('--$name');
  if (i < 0 || i + 1 >= args.length) return null;
  return args[i + 1];
}

Future<void> main(List<String> args) async {
  final address = _arg(args, 'address');
  final openaiUrl = _arg(args, 'openai-url');
  final rpcUrl = _arg(args, 'rpc-url') ?? 'https://rpc.animica.org/rpc';
  final maxJobs = int.tryParse(_arg(args, 'max-jobs') ?? '3') ?? 3;
  final tier = _arg(args, 'tier') ?? 'free';
  // Optional: an OpenAI-compatible /v1/embeddings endpoint (e.g. llama-server
  // started with --embeddings on bge-small) — enables kind:"embed" jobs.
  final embedUrl = _arg(args, 'embed-url');

  if (address == null || openaiUrl == null) {
    stderr.writeln(
        'usage: dart run tool/serve_host_test.dart --address anim1… --openai-url http://…/v1/chat/completions [--rpc-url …] [--max-jobs 3] [--tier free]');
    exitCode = 2;
    return;
  }
  if (!ServeWorkerCore.isValidAnimAddress(address)) {
    stderr.writeln('refusing to start: "$address" is not a valid bech32m anim1… address');
    exitCode = 2;
    return;
  }

  final http = HttpClient()..connectionTimeout = const Duration(seconds: 15);
  var jobsDone = 0;
  final done = Completer<void>();
  late final ServeWorkerCore core;

  Future<String> generate(String prompt,
      {required int maxTokens,
      required double temperature,
      required double topP}) async {
    final req = await http.postUrl(Uri.parse(openaiUrl));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode({
      'model': 'native',
      'messages': [
        {'role': 'user', 'content': prompt}
      ],
      'max_tokens': maxTokens,
      'temperature': temperature,
      'top_p': topP,
      'stream': false,
    }));
    final res = await req.close().timeout(const Duration(minutes: 5));
    final body = await res.transform(utf8.decoder).join();
    if (res.statusCode != 200) {
      throw Exception('openai endpoint HTTP ${res.statusCode}: '
          '${body.length > 120 ? body.substring(0, 120) : body}');
    }
    final j = jsonDecode(body);
    final choices = j is Map ? j['choices'] : null;
    final msg = choices is List && choices.isNotEmpty
        ? (choices[0] as Map)['message']
        : null;
    return '${msg is Map ? (msg['content'] ?? '') : ''}';
  }

  Future<String> embed(List<String> inputs,
      {required String model, required int dims}) async {
    final req = await http.postUrl(Uri.parse(embedUrl!));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode({'model': 'embed', 'input': inputs}));
    final res = await req.close().timeout(const Duration(minutes: 2));
    final body = await res.transform(utf8.decoder).join();
    if (res.statusCode != 200) {
      throw Exception('embed endpoint HTTP ${res.statusCode}');
    }
    final data = (jsonDecode(body)['data'] as List)
      ..sort((a, b) => (a['index'] as int).compareTo(b['index'] as int));
    final vecs = [
      for (final d in data)
        l2norm([for (final x in (d['embedding'] as List)) (x as num).toDouble()])
    ];
    return encodeEmb1(model, vecs);
  }

  core = ServeWorkerCore(
    rpcUrl: rpcUrl,
    address: address,
    tiers: tier.split(','),
    hardware: {
      'engine': 'native-harness',
      'model': 'openai-endpoint',
      'ua': 'serve_host_test.dart',
      'platform': Platform.operatingSystem,
      'cores': Platform.numberOfProcessors,
      'device_memory_gb': 0,
      'kinds': embedUrl == null ? ['chat'] : ['chat', 'embed'],
      'concurrency': 1,
    },
    generate: generate,
    embed: embedUrl == null ? null : embed,
    onEvent: (e) {
      print('[${DateTime.now().toIso8601String().substring(11, 19)}] $e');
      if (e.type == 'job') {
        jobsDone++;
        if (jobsDone >= maxJobs && !done.isCompleted) done.complete();
      }
      if ((e.type == 'fatal' || e.type == 'stopped') && !done.isCompleted) {
        done.complete();
      }
    },
  );

  print('serving as $address (tier=$tier) → $rpcUrl');
  print('engine: $openaiUrl · stopping after $maxJobs jobs or 10 min');
  final loop = core.start();
  await done.future.timeout(const Duration(minutes: 10), onTimeout: () {});
  core.stop();
  await loop.timeout(const Duration(seconds: 20), onTimeout: () {});
  http.close(force: true);
  print('done: $jobsDone job(s) answered');
}
