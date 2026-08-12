// Full-screen web-game player.
//
// A Game Lab game published to the store is a single self-contained, sandboxed
// HTML document (Listing.bundleCid). Playing it is just loading that document in
// a WebView — no server compute, no wallet-provider injection. The bytes carry
// their own locked-down CSP and are additionally served under a `sandbox
// allow-scripts …` response header (both the public content route and the
// play-token route), so the page runs on an opaque origin: no storage, no
// cookies, no network, no wallet access. This screen deliberately does NOT wire
// `window.animica` — playing a game is not a dapp session.
//
// Two ways in:
//   • a pre-resolved absolute [GamePlayScreen.url] (a FREE game whose public
//     content URL the caller already has), or
//   • a [GamePlayScreen.resolver] that runs on entry (mint a play-token for a
//     PAID+owned game, or look up the FREE content URL) — the screen shows a
//     spinner while it resolves and a friendly error (sign-in / purchase / gone)
//     if it fails.
//
// Use [playStoreGame] from a detail / library screen to launch by slug — it
// resolves FREE vs PAID for you.

import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../../services/marketplace_api.dart';

/// Resolve the absolute, loadable play URL for a game [slug]:
///   FREE  → the public content bundle URL (no sign-in).
///   PAID  → mint an entitlement-gated play-token and use its URL.
/// Throws a [MarketplaceApiException] (`no_bundle` / `not_entitled` /
/// `not_available` / `unauthorized`) the player turns into a friendly message.
Future<String> resolveGamePlayUrl(MarketplaceApi api, String slug) async {
  final info = await api.gameBundle(slug);
  if (!info.hasBundle) {
    throw MarketplaceApiException(
        404, 'no_bundle', 'This game has no playable bundle yet.');
  }
  if (info.free) {
    final url = MarketplaceApi.absoluteUrl(info.playUrl);
    if (url != null) return url;
    // Free but no direct URL exposed — fall through to the license-gated route.
  }
  final tok = await api.playToken(slug);
  final url = MarketplaceApi.absoluteUrl(tok.url);
  if (url == null) {
    throw MarketplaceApiException(
        500, 'no_play_url', 'Could not resolve the game URL.');
  }
  return url;
}

/// Push the full-screen player for game [slug]; the URL is resolved on entry
/// (FREE vs PAID handled by [resolveGamePlayUrl]).
void playStoreGame(
  BuildContext context, {
  required MarketplaceApi api,
  required String slug,
  required String name,
}) {
  Navigator.of(context).push(
    MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (_) => GamePlayScreen(
        title: name,
        resolver: () => resolveGamePlayUrl(api, slug),
      ),
    ),
  );
}

class GamePlayScreen extends StatefulWidget {
  final String title;

  /// A pre-resolved absolute play URL (used directly).
  final String? url;

  /// Resolves the absolute play URL on entry (e.g. mint a play-token). Exactly
  /// one of [url] / [resolver] must be provided.
  final Future<String> Function()? resolver;

  const GamePlayScreen({
    super.key,
    required this.title,
    this.url,
    this.resolver,
  }) : assert(url != null || resolver != null,
            'GamePlayScreen needs a url or a resolver');

  @override
  State<GamePlayScreen> createState() => _GamePlayScreenState();
}

enum _Phase { resolving, playing, error }

class _GamePlayScreenState extends State<GamePlayScreen> {
  _Phase _phase = _Phase.resolving;
  String? _url;
  String _error = '';
  bool _pageLoading = true;
  InAppWebViewController? _wv;

  @override
  void initState() {
    super.initState();
    final direct = widget.url;
    if (direct != null && direct.isNotEmpty) {
      _url = direct;
      _phase = _Phase.playing;
    } else {
      _resolve();
    }
  }

  Future<void> _resolve() async {
    setState(() {
      _phase = _Phase.resolving;
      _error = '';
    });
    try {
      final url = await widget.resolver!();
      if (!mounted) return;
      setState(() {
        _url = url;
        _pageLoading = true;
        _phase = _Phase.playing;
      });
    } on MarketplaceApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.error;
        _error = _friendlyPlayError(e);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.error;
        _error =
            'Could not start the game. Check your connection and try again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: Text(widget.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        actions: [
          if (_phase == _Phase.playing)
            IconButton(
              icon: const Icon(Icons.refresh),
              tooltip: 'Restart',
              onPressed: () => _wv?.reload(),
            ),
        ],
      ),
      body: switch (_phase) {
        _Phase.resolving => const _Resolving(),
        _Phase.error => _PlayError(
            message: _error,
            onRetry: widget.resolver == null ? null : _resolve,
            onClose: () => Navigator.of(context).maybePop(),
          ),
        _Phase.playing => _WebGame(
            url: _url!,
            loading: _pageLoading,
            onCreated: (c) => _wv = c,
            onLoadStop: () {
              if (mounted) setState(() => _pageLoading = false);
            },
          ),
      },
    );
  }
}

class _Resolving extends StatelessWidget {
  const _Resolving();
  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          CircularProgressIndicator(),
          SizedBox(height: 16),
          Text('Starting…', style: TextStyle(color: Colors.white70)),
        ],
      ),
    );
  }
}

class _WebGame extends StatelessWidget {
  final String url;
  final bool loading;
  final void Function(InAppWebViewController) onCreated;
  final VoidCallback onLoadStop;
  const _WebGame({
    required this.url,
    required this.loading,
    required this.onCreated,
    required this.onLoadStop,
  });

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        Positioned.fill(
          child: InAppWebView(
            initialUrlRequest: URLRequest(url: WebUri(url)),
            // The game document ships its own sandbox CSP; no provider injection.
            initialSettings: InAppWebViewSettings(
              transparentBackground: true,
              mediaPlaybackRequiresUserGesture: false,
              allowsInlineMediaPlayback: true,
              supportZoom: false,
            ),
            onWebViewCreated: onCreated,
            onLoadStop: (c, url) => onLoadStop(),
            onReceivedError: (c, req, err) => onLoadStop(),
          ),
        ),
        if (loading)
          const Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: LinearProgressIndicator(minHeight: 2),
          ),
      ],
    );
  }
}

class _PlayError extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;
  final VoidCallback onClose;
  const _PlayError({
    required this.message,
    required this.onRetry,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.sports_esports_outlined, size: 48, color: cs.outline),
            const SizedBox(height: 12),
            Text(message,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white)),
            const SizedBox(height: 20),
            if (onRetry != null) ...[
              FilledButton.tonalIcon(
                icon: const Icon(Icons.refresh),
                label: const Text('Try again'),
                onPressed: onRetry,
              ),
              const SizedBox(height: 8),
            ],
            TextButton(onPressed: onClose, child: const Text('Close')),
          ],
        ),
      ),
    );
  }
}

String _friendlyPlayError(MarketplaceApiException e) {
  if (e.isUnauthorized) return 'Sign in to the Store to play this game.';
  if (e.isForbidden) {
    return 'You need to buy this game before you can play it.';
  }
  if (e.isExpired) return 'This game is no longer available.';
  if (e.isNotFound || e.code == 'no_bundle') {
    return "This game isn't playable yet.";
  }
  return e.message;
}
