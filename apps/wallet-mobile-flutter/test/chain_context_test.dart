// The chain identity is an input to every signature. These tests pin the
// three things that make it safe to depend on:
//   - it is fetched from the node, not guessed;
//   - a node on a different chain than this build is refused, not signed for;
//   - it is fetched ONCE per RpcClient, so the fix does not add a round trip
//     to every payment (and a failure is not cached forever).

import 'dart:convert';

import 'package:animica_wallet/constants.dart';
import 'package:animica_wallet/services/rpc.dart';
import 'package:animica_wallet/services/signer.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const String _genesisHex =
    '0x8f2c1d4b6a90e3f57c81b24d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f21';

/// An RpcClient whose transport answers `chain.getChainIdentity` from
/// [identity] and counts how many HTTP calls it served.
({RpcClient rpc, List<String> methods}) _client(
  Map<String, dynamic>? identity, {
  int status = 200,
}) {
  final methods = <String>[];
  final http.Client transport = MockClient((req) async {
    final body = jsonDecode(req.body) as Map<String, dynamic>;
    methods.add(body['method'] as String);
    if (status != 200) {
      return http.Response('{"error":"boom"}', status);
    }
    return http.Response(
      jsonEncode({'jsonrpc': '2.0', 'id': body['id'], 'result': identity}),
      200,
      headers: {'content-type': 'application/json'},
    );
  });
  return (
    rpc: RpcClient(
      endpoints: const ['https://node.invalid/rpc'],
      httpClient: transport,
    ),
    methods: methods,
  );
}

void main() {
  test('reads chainId / genesisHash / forkId from the node', () async {
    final c = _client({
      'chainId': 1,
      'genesisHash': _genesisHex,
      'forkId': 3,
    });
    final ctx = await fetchChainContext(c.rpc);
    expect(ctx.chainId, 1);
    expect(ctx.genesisHash.length, 32);
    expect(ctx.forkId, 3);
    // Mainnet's identity has no `network` key and the node falls back to
    // "unknown" — that literal is what gets signed.
    expect(ctx.network, 'unknown');
    expect(c.methods, ['chain.getChainIdentity']);
  });

  test('refuses a node on a different chain than this build', () async {
    final c = _client({
      'chainId': AnimicaConfig.chainId + 1,
      'genesisHash': _genesisHex,
    });
    await expectLater(
      fetchChainContext(c.rpc),
      throwsA(isA<RpcError>().having(
          (e) => e.message, 'message', contains('Network mismatch'))),
    );
  });

  test('refuses an identity with a bad genesis hash', () async {
    final c = _client({'chainId': 1, 'genesisHash': '0xdeadbeef'});
    await expectLater(
      fetchChainContext(c.rpc),
      throwsA(isA<RpcError>()
          .having((e) => e.message, 'message', contains('32 bytes'))),
    );
  });

  test('chainContextFor fetches once per client and reuses it', () async {
    final c = _client({'chainId': 1, 'genesisHash': _genesisHex});
    final a = await chainContextFor(c.rpc);
    final b = await chainContextFor(c.rpc);
    expect(identical(a, b), isTrue);
    expect(c.methods.length, 1, reason: 'second send must not re-fetch');
    clearChainContextCache(c.rpc);
    await chainContextFor(c.rpc);
    expect(c.methods.length, 2);
  });

  test('a failed fetch is not cached', () async {
    final c = _client(null, status: 500);
    await expectLater(chainContextFor(c.rpc), throwsA(isA<RpcError>()));
    final before = c.methods.length;
    await expectLater(chainContextFor(c.rpc), throwsA(isA<RpcError>()));
    expect(c.methods.length, greaterThan(before),
        reason: 'a transient outage must not wedge signing for the session');
  });
}
