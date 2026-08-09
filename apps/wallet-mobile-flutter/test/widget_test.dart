// Minimal smoke test for the Animica wallet app root.
//
// The previous file was leftover Flutter template boilerplate that referenced a
// non-existent `MyApp` counter widget and never compiled. The real app root is
// `AnimicaWalletApp` (wrapped in a Riverpod `ProviderScope` in main()). We keep
// this to a construct-only check so it doesn't boot RPC/vault side effects in a
// headless test environment.

import 'package:flutter_test/flutter_test.dart';

import 'package:animica_wallet/main.dart';

void main() {
  test('AnimicaWalletApp root is constructible', () {
    const app = AnimicaWalletApp();
    expect(app, isNotNull);
  });
}
