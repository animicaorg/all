// Serve & Earn — turn the phone into an AICF inference worker.
//
// Two lanes, best one wins at runtime:
//
//  NATIVE (default on arm64 Android): the bundled llama.cpp llama-server
//  (Vulkan) runs the model on the phone's GPU, and ServeWorkerCore — a
//  faithful Dart port of the /serve page's worker loop — claims, answers
//  and submits jobs against the same aicf.* RPC. GPU offload is verified
//  from the engine's own log, never assumed; devices where Vulkan fails
//  serve on native CPU threads (still far faster than WebView's
//  single-thread WASM). See services/native_engine.dart +
//  services/serve_worker_core.dart.
//
//  WEB (fallback + opt-in): the pool.animica.org/serve page in a WebView,
//  payout address injected via localStorage["anmServeAddress"] at
//  document-start. WebView has no WebGPU (2026), so this lane also offers
//  a "Serve with GPU" hand-off to Chrome carrying ?address=.
//
// Earnings settle on-chain: the treasury anchors each block's 75 ANM
// service carve as an ANMSETL1 tx paying serving workers pro-rata.

import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../constants.dart';
import '../services/native_engine.dart';
import '../services/serve_worker_core.dart';
import '../state/wallet_state.dart';

class ServeScreen extends ConsumerStatefulWidget {
  const ServeScreen({super.key});

  @override
  ConsumerState<ServeScreen> createState() => _ServeScreenState();
}

class _ServeScreenState extends ConsumerState<ServeScreen> {
  // ── shared ────────────────────────────────────────────────────────────
  Timer? _earningsTimer;
  double? _unpaidAnm;
  double? _paidAnm;
  int? _jobsCompleted;

  // ── native lane ───────────────────────────────────────────────────────
  final _engine = NativeEngine();
  bool? _nativeAvailable; // null while probing
  bool _forceWeb = false;
  ServeModel _model = ServeModel.qwen05;
  double? _downloadProgress;
  bool _cancelDownload = false;
  bool _busy = false;
  bool _serving = false;
  EngineStatus? _engineStatus;
  String? _error;
  ServeWorkerCore? _core;
  final List<String> _events = [];
  int _sessionWins = 0;
  HttpClientRequest? _inflight;

  // ── web lane ──────────────────────────────────────────────────────────
  bool? _webViewHasGpu;

  bool get _nativeMode => _nativeAvailable == true && !_forceWeb;

  @override
  void initState() {
    super.initState();
    WakelockPlus.enable();
    _engine.binary().then((f) {
      if (mounted) setState(() => _nativeAvailable = f != null);
    });
    _earningsTimer =
        Timer.periodic(const Duration(seconds: 20), (_) => _pollEarnings());
    WidgetsBinding.instance.addPostFrameCallback((_) => _pollEarnings());
  }

  @override
  void dispose() {
    _earningsTimer?.cancel();
    _core?.stop();
    _engine.stop();
    WakelockPlus.disable();
    super.dispose();
  }

  /// Direct RPC earnings poll — covers the web lane and the idle native
  /// screen; while the native core runs, its own 15 s earnings events win.
  Future<void> _pollEarnings() async {
    if (_core != null) return;
    final active = ref.read(activeAccountProvider);
    if (active == null) return;
    try {
      final r = await ref
          .read(rpcProvider)
          .call('aicf.workerEarnings', {'address': active.address});
      if (r is! Map || !mounted) return;
      final pendingCum = _num(r['earnings_pending_animica']);
      final paid = _num(r['earnings_paid_animica']);
      final unpaid = r['earnings_unpaid_animica'] != null
          ? _num(r['earnings_unpaid_animica'])
          : (pendingCum - paid).clamp(0, double.infinity).toDouble();
      setState(() {
        _unpaidAnm = unpaid;
        _paidAnm = paid;
        _jobsCompleted = _num(r['jobs_completed']).toInt();
      });
    } catch (_) {/* transient; keep last-known */}
  }

  static double _num(dynamic v) =>
      v == null ? 0 : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0);

  // ── native lane actions ───────────────────────────────────────────────

  Future<String> _generate(
    String base,
    String prompt, {
    required int maxTokens,
    required double temperature,
    required double topP,
  }) async {
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 10);
    try {
      final req =
          await client.postUrl(Uri.parse('$base/v1/chat/completions'));
      _inflight = req;
      req.headers.contentType = ContentType.json;
      req.add(utf8.encode(jsonEncode({
        'model': 'animica-serve',
        'messages': [
          {'role': 'user', 'content': prompt}
        ],
        'max_tokens': maxTokens,
        'temperature': temperature,
        'top_p': topP,
        // Mirrors the page's wllama penalties.
        'frequency_penalty': 0.1,
        'repeat_penalty': 1.15,
        'stream': false,
      })));
      final res = await req.close();
      final body = await res.transform(utf8.decoder).join();
      if (res.statusCode != 200) {
        throw HttpException('engine HTTP ${res.statusCode}');
      }
      final j = jsonDecode(body);
      return ((j['choices'] as List).first['message']['content'] ?? '')
          as String;
    } finally {
      _inflight = null;
      client.close(force: true);
    }
  }

  void _abortInflight() {
    try {
      _inflight?.abort();
    } catch (_) {}
  }

  void _onCoreEvent(ServeEvent e) {
    if (!mounted) return;
    setState(() {
      switch (e.type) {
        case 'earnings':
          if (e.pending != null) _unpaidAnm = e.pending;
          if (e.paid != null) _paidAnm = e.paid;
          if (e.completed != null) _jobsCompleted = e.completed;
          return; // not worth a log line every 15 s
        case 'job':
          if (e.won == true) _sessionWins++;
          _pushEvent(e.won == true
              ? 'Job won ✓ ${e.tokS == null ? '' : '(${e.tokS!.toStringAsFixed(1)} tok/s)'}'
              : 'Job answered — lost the race');
        case 'fatal':
          _error = e.text;
          _pushEvent('Error: ${e.text}');
        default:
          if (e.text != null && e.text!.isNotEmpty) _pushEvent(e.text!);
      }
    });
  }

  void _pushEvent(String s) {
    _events.add(s);
    if (_events.length > 8) _events.removeAt(0);
  }

  Future<void> _startNative(String address) async {
    setState(() {
      _busy = true;
      _error = null;
      _events.clear();
      _sessionWins = 0;
    });
    try {
      if (await _engine.modelFileIfReady(_model) == null) {
        _cancelDownload = false;
        setState(() => _downloadProgress = 0);
        await _engine.ensureModel(
          _model,
          (p) {
            if (mounted) setState(() => _downloadProgress = p);
          },
          cancelled: () => _cancelDownload,
        );
        if (mounted) setState(() => _downloadProgress = null);
      }
      final st = await _engine.start(_model);
      if (!st.running) throw Exception(st.detail);
      final core = ServeWorkerCore(
        rpcUrl: AnimicaConfig.rpcUrl,
        address: address,
        generate: (p,
                {required int maxTokens,
                required double temperature,
                required double topP}) =>
            _generate(st.baseUrl!, p,
                maxTokens: maxTokens,
                temperature: temperature,
                topP: topP),
        onEvent: _onCoreEvent,
        hardware: {
          'engine': st.gpu ? 'native-vulkan' : 'native-cpu',
          'model': _model.file,
          'platform': 'android',
          'cores': Platform.numberOfProcessors,
        },
        interrupt: _abortInflight,
      );
      _core = core;
      unawaited(core.start().whenComplete(() {
        if (mounted && identical(_core, core)) {
          setState(() => _serving = false);
        }
      }));
      setState(() {
        _engineStatus = st;
        _serving = true;
      });
    } catch (e) {
      _core?.stop();
      _core = null;
      await _engine.stop();
      if (mounted) {
        setState(() {
          _error = '$e';
          _downloadProgress = null;
          _serving = false;
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _stopNative() async {
    setState(() => _busy = true);
    _core?.stop();
    _core = null;
    _abortInflight();
    await _engine.stop();
    if (mounted) {
      setState(() {
        _serving = false;
        _engineStatus = null;
        _busy = false;
      });
    }
  }

  // ── UI ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final active = ref.watch(activeAccountProvider);
    final scheme = Theme.of(context).colorScheme;

    if (active == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Serve & Earn')),
        body: const Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'Create or unlock a wallet first — serving pays ANM '
              'directly to your wallet address.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: const Text('Serve & Earn'),
        actions: [
          if (_nativeAvailable == true)
            TextButton(
              onPressed: _serving || _busy
                  ? null
                  : () => setState(() => _forceWeb = !_forceWeb),
              child: Text(_nativeMode ? 'Web worker' : 'Native engine'),
            ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: Row(
              children: [
                _stat('Unpaid', _fmtAnm(_unpaidAnm), scheme),
                const SizedBox(width: 8),
                _stat('Paid on-chain', _fmtAnm(_paidAnm), scheme),
                const SizedBox(width: 8),
                _stat('Jobs', _jobsCompleted?.toString() ?? '—', scheme),
              ],
            ),
          ),
          Expanded(
            child: _nativeAvailable == null
                ? const Center(child: CircularProgressIndicator())
                : _nativeMode
                    ? _nativePanel(active.address, scheme)
                    : _webView(active.address),
          ),
          if (!_nativeMode && _webViewHasGpu == false)
            _chromeGpuBar(active.address, scheme),
          Container(
            width: double.infinity,
            color: scheme.surfaceContainerHighest,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Text(
              '${_engineStatus?.gpu == true ? 'Serving on this phone\'s GPU (${_engineStatus!.detail}). ' : ''}'
              'Keep the app open and the phone plugged in. First start '
              'downloads a ~0.5 GB model — use Wi-Fi. Earnings settle '
              'on-chain to ${_short(active.address)}.',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }

  Widget _nativePanel(String address, ColorScheme scheme) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        Card(
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.memory, color: scheme.primary),
                    const SizedBox(width: 8),
                    const Text('Native engine',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    const Spacer(),
                    if (_serving)
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: _engineStatus?.gpu == true
                              ? scheme.primaryContainer
                              : scheme.surfaceContainerHighest,
                          borderRadius: BorderRadius.circular(999),
                        ),
                        child: Text(
                          _engineStatus?.gpu == true
                              ? 'GPU · Vulkan'
                              : 'CPU · native',
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<ServeModel>(
                  initialValue: _model,
                  decoration: const InputDecoration(
                      labelText: 'Model', border: OutlineInputBorder()),
                  items: [
                    for (final m in ServeModel.all)
                      DropdownMenuItem(value: m, child: Text(m.label)),
                  ],
                  onChanged: _serving || _busy
                      ? null
                      : (m) => setState(() => _model = m ?? _model),
                ),
                if (_downloadProgress != null) ...[
                  const SizedBox(height: 12),
                  LinearProgressIndicator(value: _downloadProgress),
                  const SizedBox(height: 4),
                  Text(
                      'Downloading model — ${(100 * (_downloadProgress ?? 0)).toStringAsFixed(0)}%',
                      style: Theme.of(context).textTheme.bodySmall),
                ],
                if (_engineStatus != null && _serving) ...[
                  const SizedBox(height: 8),
                  Text(_engineStatus!.detail,
                      style: Theme.of(context).textTheme.bodySmall),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 8),
                  Text(_error!,
                      style: TextStyle(color: scheme.error, fontSize: 13)),
                ],
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy
                        ? (_downloadProgress != null
                            ? () => _cancelDownload = true
                            : null)
                        : _serving
                            ? _stopNative
                            : () => _startNative(address),
                    icon: Icon(_serving
                        ? Icons.stop
                        : _downloadProgress != null
                            ? Icons.close
                            : Icons.play_arrow),
                    label: Text(_serving
                        ? 'Stop serving'
                        : _downloadProgress != null
                            ? 'Cancel download'
                            : 'Start serving'),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        if (_serving || _events.isNotEmpty)
          Card(
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12)),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Session — $_sessionWins jobs won',
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 8),
                  for (final e in _events.reversed)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(e,
                          style: Theme.of(context).textTheme.bodySmall),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  Widget _webView(String address) {
    return InAppWebView(
      key: ValueKey('web-$address'),
      initialUrlRequest: URLRequest(url: WebUri(AnimicaConfig.serveUrl)),
      initialUserScripts: UnmodifiableListView([
        UserScript(
          source: '''
            try {
              if (location.host === 'pool.animica.org') {
                localStorage.setItem('anmServeAddress', '$address');
              }
            } catch (e) {}
          ''',
          injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START,
          forMainFrameOnly: true,
        ),
      ]),
      initialSettings: InAppWebViewSettings(
        mediaPlaybackRequiresUserGesture: false,
        allowsInlineMediaPlayback: true,
        javaScriptCanOpenWindowsAutomatically: false,
      ),
      onLoadStop: (ctrl, _) async {
        final r = await ctrl.evaluateJavascript(source: '!!navigator.gpu');
        if (mounted) setState(() => _webViewHasGpu = r == true);
      },
    );
  }

  Widget _chromeGpuBar(String address, ColorScheme scheme) {
    return Container(
      width: double.infinity,
      color: scheme.primaryContainer,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        children: [
          Icon(Icons.speed, size: 18, color: scheme.onPrimaryContainer),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'The web worker runs on CPU here. Chrome unlocks WebGPU — '
              'or switch to the native engine above.',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: scheme.onPrimaryContainer),
            ),
          ),
          TextButton(
            onPressed: () => launchUrl(
              Uri.parse('${AnimicaConfig.serveUrl}?address=$address'),
              mode: LaunchMode.externalApplication,
            ),
            child: const Text('Chrome'),
          ),
        ],
      ),
    );
  }

  Widget _stat(String label, String value, ColorScheme scheme) => Expanded(
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 8),
          decoration: BoxDecoration(
            color: scheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(
            children: [
              Text(value,
                  style: const TextStyle(
                      fontWeight: FontWeight.w600, fontSize: 15)),
              Text(label,
                  style: TextStyle(
                      fontSize: 11, color: scheme.onSurfaceVariant)),
            ],
          ),
        ),
      );

  static String _fmtAnm(double? v) =>
      v == null ? '—' : '${v.toStringAsFixed(v >= 100 ? 1 : 3)} ANM';

  static String _short(String a) =>
      a.length <= 16 ? a : '${a.substring(0, 10)}…${a.substring(a.length - 4)}';
}
