// AICF serve worker loop — pure-Dart port of the browser worker core
// (animica-pool/apps/web/src/app/serve/ServeWorker.tsx, CORE_SOURCE).
//
// This is the PROTOCOL half only: registration → claim → generate → submit →
// earnings, with the same RPC method names, param shapes, retry/backoff
// timing, best-of-N resubmit rules, and sign-off semantics as the page. The
// inference engine is supplied by the host through [ServeWorkerCore.generate]
// (native llama.cpp, an OpenAI-compatible endpoint, anything that turns a
// prompt into text).
//
// Protocol (JSON-RPC 2.0, POST rpcUrl):
//   aicf.workerRegister     {address, tiers, hardware}
//   aicf.workerClaimNextJob {address, tiers} -> null | {job_id, prompt, tier,
//                            max_output_tokens, temperature, top_p,
//                            claim_expires_at}
//   aicf.workerSubmitResult {address, job_id, text<=32000} ->
//                            {accepted, reason?, state?, score?, candidates?,
//                             retry_suggested?, settles_in_s?, won?}
//   aicf.workerEarnings     {address} -> {jobs_completed,
//                            earnings_pending_animica, earnings_paid_animica,
//                            earnings_unpaid_animica?}
//   aicf.workerSignOff      {address}   (best-effort, on stop)
//
// Pure Dart on purpose: dart:async/convert/io/math only — runs unchanged in a
// background isolate, a foreground-service callback, or a desktop `dart run`
// harness (tool/serve_host_test.dart).

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// Text generation supplied by the host. Must honor [maxTokens],
/// [temperature] and [topP]; return the full answer text.
typedef GenerateFn = Future<String> Function(
  String prompt, {
  required int maxTokens,
  required double temperature,
  required double topP,
});

/// One event out of the worker loop — mirrors the page core's postMessage
/// stream so a UI can render the same story.
class ServeEvent {
  /// registered | status | log | claimed | job | earnings | fatal | stopped
  final String type;
  final String? text;

  // type == "job"
  final bool? won;
  final int? tokens;
  final double? tokS;

  // type == "earnings"
  final double? pending; // unpaid — next anchor's weight
  final double? paid; // settled on-chain by ANMSETL1 anchors
  final int? completed;

  const ServeEvent(
    this.type, {
    this.text,
    this.won,
    this.tokens,
    this.tokS,
    this.pending,
    this.paid,
    this.completed,
  });

  @override
  String toString() {
    switch (type) {
      case 'earnings':
        return 'earnings unpaid=$pending paid=$paid jobs=$completed';
      case 'job':
        return 'job won=$won tokens=$tokens tokS=${tokS?.toStringAsFixed(1)}';
      default:
        return '$type${text != null ? ': $text' : ''}';
    }
  }
}

class ServeWorkerCore {
  ServeWorkerCore({
    required this.rpcUrl,
    required this.address,
    required this.generate,
    required this.onEvent,
    this.tiers = const ['free', 'standard'],
    Map<String, Object?>? hardware,
    this.searchUrl = 'https://animica.dev/v1/web-search',
    this.promptBudgetChars = 7500, // the page's non-wllama (GPU) budget
    this.engineCap = 2048, // the page's GPU-engine output ceiling
    this.interrupt,
  }) : hardware = hardware ?? const {};

  final String rpcUrl;
  final String address;
  final GenerateFn generate;
  final void Function(ServeEvent) onEvent;
  final List<String> tiers;
  final Map<String, Object?> hardware;
  final String searchUrl;
  final int promptBudgetChars;
  final int engineCap;

  /// Optional generation abort, wired to the engine (the page calls
  /// interruptGenerate). Invoked by the per-job watchdog just before the
  /// claim deadline, and on stop().
  final void Function()? interrupt;

  bool _stopped = false;
  bool _paused = false;
  bool get running => _started && !_stopped;
  bool _started = false;

  final _rand = Random();
  final HttpClient _http = HttpClient()
    ..connectionTimeout = const Duration(seconds: 15);

  // ── lifecycle ──────────────────────────────────────────────────────────

  /// Starts the loop. Returns when the loop exits (stop() or fatal).
  Future<void> start() {
    if (_started) return Future.value();
    _started = true;
    return _run();
  }

  void pause() => _paused = true;
  void resume() => _paused = false;

  /// Stop serving and tell the queue immediately (best-effort sign-off —
  /// the page does the same on pagehide; the earnings ledger is kept
  /// server-side).
  void stop() {
    if (_stopped) return;
    _stopped = true;
    try {
      interrupt?.call();
    } catch (_) {}
    _rpc('aicf.workerSignOff', {'address': address})
        .catchError((_) => null)
        .whenComplete(() {
      try {
        _http.close(force: true);
      } catch (_) {}
    });
  }

  // ── JSON-RPC (same id scheme as the page core) ─────────────────────────

  Future<dynamic> _rpc(String method, Map<String, Object?> params) async {
    final req = await _http.postUrl(Uri.parse(rpcUrl));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode({
      'jsonrpc': '2.0',
      'id': (DateTime.now().millisecondsSinceEpoch % 1000000000) +
          _rand.nextInt(1000),
      'method': method,
      'params': params,
    }));
    // The browser's fetch has no explicit timeout; a phone radio can hang a
    // socket forever, so cap the exchange (the loop's own backoff handles
    // the failure exactly like a fetch rejection).
    final res = await req.close().timeout(const Duration(seconds: 30));
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw Exception('$method: HTTP ${res.statusCode}');
    }
    final body =
        await res.transform(utf8.decoder).join().timeout(const Duration(seconds: 30));
    final j = jsonDecode(body);
    if (j is Map && j['error'] != null) {
      final err = j['error'];
      throw Exception(
          '$method: ${err is Map ? (err['message'] ?? 'rpc error') : 'rpc error'}');
    }
    return (j as Map)['result'];
  }

  // ── ported helpers ─────────────────────────────────────────────────────

  static Future<void> _sleep(int ms) => Future.delayed(Duration(milliseconds: ms));

  /// Head + tail clamp, identical proportions to the page (30% head).
  static String clampPrompt(String p, int maxChars) {
    if (p.length <= maxChars) return p;
    final head = (maxChars * 0.3).floor();
    final tail = maxChars - head;
    return '${p.substring(0, head)}\n…\n${p.substring(p.length - tail)}';
  }

  void _refreshEarnings() {
    _rpc('aicf.workerEarnings', {'address': address}).then((e) {
      if (e is! Map) return;
      final pendingCum = _num(e['earnings_pending_animica']);
      final paid = _num(e['earnings_paid_animica']);
      final unpaid = e['earnings_unpaid_animica'] != null
          ? _num(e['earnings_unpaid_animica'])
          : max(0.0, pendingCum - paid);
      onEvent(ServeEvent('earnings',
          pending: unpaid,
          paid: paid,
          completed: _num(e['jobs_completed']).toInt()));
    }).catchError((_) => null);
  }

  static double _num(dynamic v) =>
      v == null ? 0 : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0);

  static final RegExp _webIntent = RegExp(
      r'\b(today|current|currently|latest|news|price|weather|version|release|score|happened|recent|who is|when did|look ?up|search the web|20(2[4-9]|3[0-9]))\b',
      caseSensitive: false);
  static final RegExp _userTail =
      RegExp(r'(?:^|\n)\s*User:\s*([\s\S]*?)$', caseSensitive: false);

  /// Optional web research through the network's free SSRF-hardened lookup —
  /// same triggers, caps and formatting as the page. Skipped when the bridge
  /// already injected grounding.
  Future<String> _webLookup(String rawPrompt) async {
    try {
      if (searchUrl.isEmpty) return '';
      if (rawPrompt.contains('=== WEB RESULTS ===') ||
          rawPrompt.contains('[fresh web findings]')) {
        return '';
      }
      final tail =
          rawPrompt.length <= 600 ? rawPrompt : rawPrompt.substring(rawPrompt.length - 600);
      if (!_webIntent.hasMatch(tail)) return '';
      final m = _userTail.firstMatch(rawPrompt);
      var q = (m != null ? (m.group(1) ?? '') : tail)
          .replaceAll(RegExp(r'\s+'), ' ')
          .trim();
      if (q.length > 180) q = q.substring(0, 180);
      if (q.length < 8) return '';
      onEvent(const ServeEvent('status', text: 'Researching the web for this job…'));
      final req = await _http
          .getUrl(Uri.parse('$searchUrl?q=${Uri.encodeQueryComponent(q)}'));
      final res = await req.close().timeout(const Duration(seconds: 20));
      if (res.statusCode != 200) {
        await res.drain<void>();
        return '';
      }
      final d = jsonDecode(await res
          .transform(utf8.decoder)
          .join()
          .timeout(const Duration(seconds: 20)));
      var ctx = d is Map ? '${d['context'] ?? ''}' : '';
      if (ctx.length > 1200) ctx = ctx.substring(0, 1200);
      if (ctx.length < 60) return '';
      onEvent(ServeEvent('log',
          text:
              'web lookup: ${ctx.length} chars of fresh findings folded into the prompt'));
      return '[fresh web findings]\n$ctx\n[end findings]\n\n';
    } catch (_) {
      return '';
    }
  }

  // ── the loop (run() in CORE_SOURCE, minus engine loading) ──────────────

  Future<void> _run() async {
    try {
      // Registration must ride out node restarts: 12 attempts, page backoff.
      var registered = false;
      for (var attempt = 0; attempt < 12 && !_stopped; attempt++) {
        try {
          await _rpc('aicf.workerRegister',
              {'address': address, 'tiers': tiers, 'hardware': hardware});
          registered = true;
          break;
        } catch (e) {
          onEvent(ServeEvent('status',
              text:
                  'Network restarting (${_msg(e, 50)}) — retrying registration…'));
          await _sleep(min(15000, 4000 + attempt * 2000));
        }
      }
      if (_stopped) return;
      if (!registered) {
        throw Exception(
            'could not register with the queue after 12 attempts — is $rpcUrl reachable?');
      }
      onEvent(ServeEvent('log',
          text:
              'registered ${address.substring(0, min(14, address.length))}… tiers=${tiers.join(",")}'));
      onEvent(const ServeEvent('status', text: 'Serving — waiting for jobs…'));

      var delay = 2500;
      var lastRegister = DateTime.now().millisecondsSinceEpoch;
      var lastEarnings = 0;
      while (!_stopped) {
        if (_paused) {
          await _sleep(1200);
          continue;
        }
        final nowMs = DateTime.now().millisecondsSinceEpoch;
        if (nowMs - lastRegister > 300000) {
          lastRegister = nowMs;
          _rpc('aicf.workerRegister', {
            'address': address,
            'tiers': tiers,
            'hardware': hardware
          }).catchError((_) => null);
        }
        if (nowMs - lastEarnings > 15000) {
          lastEarnings = nowMs;
          _refreshEarnings();
        }
        dynamic job;
        try {
          job = await _rpc(
              'aicf.workerClaimNextJob', {'address': address, 'tiers': tiers});
        } catch (e) {
          onEvent(ServeEvent('status',
              text: 'Queue unreachable (${_msg(e, 60)}) — retrying…'));
          delay = min(15000, (delay * 1.6).round());
          await _sleep(delay);
          continue;
        }
        if (job is! Map || job['job_id'] == null) {
          delay = min(15000, (delay * 1.35).round());
          await _sleep((delay * (0.7 + _rand.nextDouble() * 0.6)).round());
          continue;
        }
        delay = 2500;
        final jobId = '${job['job_id']}';
        final rawPrompt = '${job['prompt'] ?? ''}';
        var prompt = clampPrompt(rawPrompt, promptBudgetChars);
        final findings = await _webLookup(rawPrompt);
        if (findings.isNotEmpty) {
          prompt = clampPrompt(findings + prompt, promptBudgetChars + 600);
        }
        if (prompt.trim().isEmpty) {
          onEvent(ServeEvent('log',
              text:
                  'claimed ${_short(jobId)}… but it carried no prompt — skipped'));
          continue;
        }
        final maxTok =
            max(16, min(_num(job['max_output_tokens']).toInt() == 0 ? 2048 : _num(job['max_output_tokens']).toInt(), engineCap));
        final claimExp = _num(job['claim_expires_at']);
        final deadline = claimExp > 0
            ? (claimExp * 1000).toInt()
            : DateTime.now().millisecondsSinceEpoch + 120000;
        onEvent(ServeEvent('status',
            text:
                'Answering job ${_short(jobId)}… (${prompt.length} chars in, ≤$maxTok tokens out)'));
        onEvent(
            ServeEvent('log', text: 'claimed ${_short(jobId)}… tier=${job['tier']}'));

        // Best-of-N: submits are scored candidates; a low score or rejected
        // degenerate gets ONE more pass at higher temperature.
        var text = '';
        var tokens = 0;
        var anyAccepted = false;
        final t0 = DateTime.now().millisecondsSinceEpoch;
        final temp0 =
            _num(job['temperature'] ?? 0.3).clamp(0.0, 1.2).toDouble();
        final topP = _num(job['top_p'] ?? 0.9).clamp(0.05, 1.0).toDouble();
        for (var attempt = 0; attempt < 2 && !_stopped; attempt++) {
          tokens = 0;
          final msLeft = deadline - DateTime.now().millisecondsSinceEpoch;
          final watchdog = interrupt == null
              ? null
              : Timer(Duration(milliseconds: max(5000, msLeft - 4000)), () {
                  try {
                    interrupt!.call();
                  } catch (_) {}
                });
          try {
            text = await generate(prompt,
                maxTokens: maxTok,
                temperature:
                    attempt == 0 ? temp0 : min(1.0, temp0 + 0.3),
                topP: topP);
            // The page counts streamed tokens; a plain-text engine reports
            // none, so estimate (~4 chars/token) for the stats line only.
            tokens = (text.length / 4).ceil();
          } catch (e) {
            onEvent(ServeEvent('log', text: 'generation failed: ${_msg(e, 80)}'));
          } finally {
            watchdog?.cancel();
          }
          if (_stopped) break;
          if (text.trim().isEmpty) {
            onEvent(ServeEvent('log',
                text:
                    'no text produced for ${_short(jobId)}… — nothing submitted'));
            break;
          }
          dynamic r;
          try {
            r = await _rpc('aicf.workerSubmitResult', {
              'address': address,
              'job_id': jobId,
              'text': text.length > 32000 ? text.substring(0, 32000) : text,
            });
          } catch (e) {
            onEvent(ServeEvent('log', text: 'submit failed: ${_msg(e, 80)}'));
            break;
          }
          final timeLeft = deadline - DateTime.now().millisecondsSinceEpoch;
          final rm = r is Map ? r : const {};
          if (rm['accepted'] != false) {
            anyAccepted = true;
            if (rm['state'] == 'candidate') {
              final cand = _num(rm['candidates']).toInt();
              onEvent(ServeEvent('log',
                  text:
                      'answer in for ${_short(jobId)}… score ${rm['score'] ?? '?'} (${cand == 0 ? 1 : cand} candidate${(cand == 0 ? 1 : cand) > 1 ? 's' : ''}) — best answer wins at settle'));
              if (rm['retry_suggested'] == true &&
                  attempt == 0 &&
                  _num(rm['settles_in_s']) > 8 &&
                  timeLeft > 30000) {
                onEvent(const ServeEvent('log',
                    text: 'score is low — taking another pass at it'));
                continue;
              }
            } else {
              onEvent(ServeEvent('log',
                  text:
                      '${rm['won'] != false ? 'WON ' : 'settled '}${_short(jobId)}… · $tokens tok'));
            }
            break;
          }
          if ((rm['reason'] == 'degenerate_text' || rm['reason'] == 'stub_text') &&
              attempt == 0 &&
              timeLeft > 30000) {
            onEvent(ServeEvent('log',
                text:
                    'answer rejected (${rm['reason']}) — regenerating at higher temperature'));
            continue;
          }
          onEvent(ServeEvent('log',
              text:
                  'lost on ${_short(jobId)}… (${rm['reason'] ?? 'another answer was better'})'));
          break;
        }
        final dt = (DateTime.now().millisecondsSinceEpoch - t0) / 1000;
        final tokS = tokens > 0 && dt > 0 ? tokens / dt : null;
        onEvent(ServeEvent('job', won: anyAccepted, tokens: tokens, tokS: tokS));
        _refreshEarnings(); // pending/paid update the moment a race resolves
        lastEarnings = DateTime.now().millisecondsSinceEpoch;
        onEvent(const ServeEvent('status', text: 'Serving — waiting for jobs…'));
      }
    } catch (e) {
      onEvent(ServeEvent('fatal', text: _msg(e, 200)));
      return;
    }
    onEvent(const ServeEvent('stopped'));
  }

  static String _short(String id) => id.substring(0, min(10, id.length));

  static String _msg(Object e, int cap) {
    final s = '$e';
    return s.length > cap ? s.substring(0, cap) : s;
  }

  // ── bech32m address validation (BIP-350), same as the page ─────────────
  // Workers may register any string, but settlement anchors can only pay
  // valid anim1… addresses — a typo'd address accrues IOUs that can never
  // pay out, so UIs must refuse invalid ones before start.

  static const String _b32Charset = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';

  static int _bech32Polymod(List<int> values) {
    const gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    var chk = 1;
    for (final v in values) {
      final b = chk >>> 25;
      chk = ((chk & 0x1ffffff) << 5) ^ v;
      for (var i = 0; i < 5; i++) {
        if ((b >>> i) & 1 != 0) chk ^= gen[i];
      }
    }
    return chk;
  }

  static bool isValidAnimAddress(String addr) {
    final a = addr.trim();
    if (a != a.toLowerCase() && a != a.toUpperCase()) return false;
    final s = a.toLowerCase();
    final pos = s.lastIndexOf('1');
    if (!s.startsWith('anim1') || pos != 4 || s.length < pos + 7) return false;
    final hrp = s.substring(0, pos);
    final data = <int>[];
    for (final ch in s.substring(pos + 1).split('')) {
      final d = _b32Charset.indexOf(ch);
      if (d == -1) return false;
      data.add(d);
    }
    final hrpExpand = [
      ...hrp.codeUnits.map((c) => c >>> 5),
      0,
      ...hrp.codeUnits.map((c) => c & 31),
    ];
    return _bech32Polymod([...hrpExpand, ...data]) == 0x2bc830a3;
  }
}
