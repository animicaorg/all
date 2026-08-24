// Serve & Earn — turn the phone into an AICF inference worker.
//
// Hosts the live pool.animica.org/serve worker page in a WebView rather
// than reimplementing the job loop natively: the page already handles
// worker registration, K-way job racing, the WebGPU (WebLLM) engine with
// a CPU/WASM (wllama) fallback — which is what actually runs inside
// Android WebView, where navigator.gpu is absent — model downloads from
// the pool's self-hosted GGUF mirror, charge-only gating, and the
// near-silent-audio keepalive.
//
// The wallet's contribution is what a browser can't do:
//   - the payout address is the wallet's ACTIVE account, injected at
//     document-start via localStorage["anmServeAddress"] (the page reads
//     that key on mount; there is no URL param). No typing, no typos.
//   - a native wakelock keeps the screen on while serving
//     (navigator.wakeLock is unavailable in WebView).
//   - a native earnings strip polls aicf.workerEarnings for the active
//     address so payouts are visible even before the page loads.
//
// Earnings settle on-chain: the treasury's settlement worker anchors each
// block's 75 ANM service carve as an ANMSETL1 tx paying serving workers
// pro-rata (settlement v2 debits pending into paid).

import 'dart:async';
import 'dart:collection';

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../constants.dart';
import '../state/wallet_state.dart';

class ServeScreen extends ConsumerStatefulWidget {
  const ServeScreen({super.key});

  @override
  ConsumerState<ServeScreen> createState() => _ServeScreenState();
}

class _ServeScreenState extends ConsumerState<ServeScreen> {
  Timer? _earningsTimer;
  double? _unpaidAnm;
  double? _paidAnm;
  int? _jobsCompleted;

  /// Whether the embedded WebView exposes navigator.gpu. Android WebView
  /// ships WITHOUT WebGPU (2026) even though Chrome has had it since 121,
  /// so this is almost always false today — but it's a runtime probe, not
  /// an assumption: the day WebView gains WebGPU the embedded worker uses
  /// the GPU lane automatically and the Chrome hand-off stops being offered.
  bool? _webViewHasGpu;

  @override
  void initState() {
    super.initState();
    // Serving is a leave-it-running activity; without this the screen
    // times out, Android pauses the WebView, and the worker misses jobs.
    WakelockPlus.enable();
    _earningsTimer = Timer.periodic(
        const Duration(seconds: 20), (_) => _pollEarnings());
    // First poll after the first frame so ref is safe to read.
    WidgetsBinding.instance.addPostFrameCallback((_) => _pollEarnings());
  }

  @override
  void dispose() {
    _earningsTimer?.cancel();
    WakelockPlus.disable();
    super.dispose();
  }

  Future<void> _pollEarnings() async {
    final active = ref.read(activeAccountProvider);
    if (active == null) return;
    try {
      final r = await ref
          .read(rpcProvider)
          .call('aicf.workerEarnings', {'address': active.address});
      if (r is! Map || !mounted) return;
      // Mirror the serve page's math: pending is CUMULATIVE credit;
      // settlement v2 reports earnings_unpaid_animica directly, older
      // nodes need pending - paid.
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
    } catch (_) {
      // Transient RPC failure — keep the last-known numbers on screen.
    }
  }

  static double _num(dynamic v) =>
      v == null ? 0 : (v is num ? v.toDouble() : double.tryParse('$v') ?? 0);

  /// Injected at document-start on pool.animica.org only: hands the page
  /// the wallet's payout address through the localStorage key it reads on
  /// mount. Unconditional set — the wallet's active account is
  /// authoritative for where this phone's earnings go.
  UserScript _addressScript(String address) => UserScript(
        source: '''
          try {
            if (location.host === 'pool.animica.org') {
              localStorage.setItem('anmServeAddress', '$address');
            }
          } catch (e) {}
        ''',
        injectionTime: UserScriptInjectionTime.AT_DOCUMENT_START,
        forMainFrameOnly: true,
      );

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
      appBar: AppBar(title: const Text('Serve & Earn')),
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
            child: InAppWebView(
              // Key by address: switching the active account rebuilds the
              // WebView so the new document-start script (and the page's
              // mount-time localStorage read) pick up the new payout
              // address.
              key: ValueKey(active.address),
              initialUrlRequest:
                  URLRequest(url: WebUri(AnimicaConfig.serveUrl)),
              initialUserScripts:
                  UnmodifiableListView([_addressScript(active.address)]),
              initialSettings: InAppWebViewSettings(
                // The page starts its near-silent keepalive audio inside
                // the Start button's click handler; don't make WebView
                // demand a second gesture for it.
                mediaPlaybackRequiresUserGesture: false,
                allowsInlineMediaPlayback: true,
                // The worker page has no use for popups.
                javaScriptCanOpenWindowsAutomatically: false,
              ),
              onLoadStop: (ctrl, _) async {
                final r = await ctrl.evaluateJavascript(
                    source: '!!navigator.gpu');
                if (mounted) setState(() => _webViewHasGpu = r == true);
              },
            ),
          ),
          if (_webViewHasGpu == false)
            Container(
              width: double.infinity,
              color: scheme.primaryContainer,
              padding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              child: Row(
                children: [
                  Icon(Icons.speed, size: 18, color: scheme.onPrimaryContainer),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'In-app serving runs on CPU. Chrome unlocks this '
                      "phone's GPU — 5–20× faster, same wallet address.",
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: scheme.onPrimaryContainer),
                    ),
                  ),
                  TextButton(
                    onPressed: () => launchUrl(
                      Uri.parse(
                          '${AnimicaConfig.serveUrl}?address=${active.address}'),
                      mode: LaunchMode.externalApplication,
                    ),
                    child: const Text('Serve with GPU'),
                  ),
                ],
              ),
            ),
          Container(
            width: double.infinity,
            color: scheme.surfaceContainerHighest,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Text(
              '${_webViewHasGpu == true ? 'Serving on this phone\'s GPU (WebGPU). ' : ''}'
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
