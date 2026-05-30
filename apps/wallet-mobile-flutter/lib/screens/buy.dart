// Buy ANM — deep-link to buy.animica.org with the active wallet's address
// prefilled as the deposit destination.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../constants.dart';
import '../state/wallet_state.dart';

class BuyScreen extends ConsumerWidget {
  const BuyScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final acc = ref.watch(activeAccountProvider);
    final addr = acc?.address ?? '';
    final url = Uri.parse(AnimicaConfig.buyGatewayUrl).replace(
      queryParameters: {if (addr.isNotEmpty) 'to': addr},
    );

    return Scaffold(
      appBar: AppBar(title: const Text('Buy ANM')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Card(
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Icon(Icons.add_card, color: Theme.of(context).colorScheme.primary),
                      const SizedBox(width: 8),
                      const Text('Buy through the gateway',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                    ]),
                    const SizedBox(height: 8),
                    Text(
                      'buy.animica.org runs the official Animica purchase gateway. You\'ll pay in card, USDT, BTC, ETH or other supported currencies via NOWPayments, and the ANM is delivered to your wallet here.',
                      style: TextStyle(color: Theme.of(context).colorScheme.outline, fontSize: 13),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (acc != null) ...[
              Text('Delivery address:',
                  style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: Theme.of(context).colorScheme.outline)),
              const SizedBox(height: 4),
              SelectableText(
                acc.address,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
              const SizedBox(height: 24),
            ],
            FilledButton.icon(
              icon: const Icon(Icons.open_in_new),
              label: const Text('Open buy.animica.org'),
              onPressed: () async {
                if (await canLaunchUrl(url)) {
                  await launchUrl(url, mode: LaunchMode.externalApplication);
                }
              },
            ),
            const SizedBox(height: 8),
            Text(
              'Opens in your default browser. The site validates your delivery address and walks you through payment.',
              style: TextStyle(
                  color: Theme.of(context).colorScheme.outline, fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }
}
