// Games tab — featured Animica web games that open in the in-app dapp
// browser WITH the `window.animica` provider (unlike Game Lab store bundles,
// these are trusted first-party titles on whitelisted hosts, so wallet
// sign-in works in-game via the normal signed-challenge flow).
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

class GamesScreen extends StatelessWidget {
  const GamesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Games')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _FeaturedGameCard(
            title: 'Thronebound',
            tagline: 'Raise a ruined keep into a mighty fantasy kingdom.',
            bullets: const [
              'Idle kingdom — it keeps growing while you are away',
              'Heroes, expeditions, bosses and dynasties',
              'Earn Crown Points and share the daily 10,000 ANM Royal Rewards pool',
            ],
            gradient: const [Color(0xFF2b1d0e), Color(0xFF6b4a2f)],
            icon: Icons.castle,
            onPlay: () => context.push(
              '/games/play?url=${Uri.encodeComponent('https://animica.org/thronebound/')}',
            ),
          ),
          const SizedBox(height: 24),
          Center(
            child: Text(
              'More realms are on the way.',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
          ),
        ],
      ),
    );
  }
}

class _FeaturedGameCard extends StatelessWidget {
  const _FeaturedGameCard({
    required this.title,
    required this.tagline,
    required this.bullets,
    required this.gradient,
    required this.icon,
    required this.onPlay,
  });

  final String title;
  final String tagline;
  final List<String> bullets;
  final List<Color> gradient;
  final IconData icon;
  final VoidCallback onPlay;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            height: 148,
            decoration: BoxDecoration(
              gradient: LinearGradient(
                colors: gradient,
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
              ),
            ),
            child: Stack(
              children: [
                Positioned(
                  right: -12,
                  bottom: -18,
                  child: Icon(icon,
                      size: 148, color: Colors.white.withValues(alpha: 0.18)),
                ),
                Positioned(
                  left: 16,
                  bottom: 14,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          style: theme.textTheme.headlineSmall?.copyWith(
                              color: Colors.white,
                              fontWeight: FontWeight.bold)),
                      Text(tagline,
                          style: theme.textTheme.bodySmall
                              ?.copyWith(color: Colors.white70)),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final b in bullets)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Padding(
                          padding: EdgeInsets.only(top: 2, right: 8),
                          child: Icon(Icons.shield_outlined, size: 16),
                        ),
                        Expanded(child: Text(b)),
                      ],
                    ),
                  ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: onPlay,
                    icon: const Icon(Icons.play_arrow),
                    label: const Text('Play Thronebound'),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  'Opens in the wallet browser — sign in with one tap, no seed phrase ever requested.',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
