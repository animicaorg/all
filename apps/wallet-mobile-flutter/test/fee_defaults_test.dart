// Pins the wallet's fee economics. 0.3.0/0.3.1 shipped kDefaultGasPrice =
// 1_000_000_000 nanos PER GAS UNIT (one whole ANM per gas), which burned
// 21,000 ANM of invisible fees on every mined transfer and let a near-full-
// balance send be admitted but never mined. These tests make sure neither
// the sane default nor the safety cap can silently regress.

import 'package:animica_wallet/services/signer.dart';
import 'package:flutter_test/flutter_test.dart';

const String kFrom =
    'anim1zqpszd0dlq4ukh682fvrpx2lv6wmwxnw0lq3rq3y9lppe07e05utuqsxjd23s';
const String kTo =
    'anim1zqpmxapvv6pf5qlrecetx5604k4u4gcf25vgyh4a9z4ucyxhhrvqe8cw58wa6';

void main() {
  test('default gas price is the network norm of 1 nano per gas unit', () {
    expect(kDefaultGasPrice, 1,
        reason: 'a plain transfer must cost 21_000 nanos (0.000021 ANM), '
            'not 21,000 ANM — never raise this without a fee oracle');
    final body = buildTransferBody(
      from: kFrom,
      to: kTo,
      amountNanos: BigInt.one,
      nonce: 0,
    );
    expect((body['gas'] as Map)['price'], 1);
    expect((body['gas'] as Map)['limit'], kDefaultTransferGasLimit);
  });

  test('worst-case fee above the 100 ANM cap is refused outright', () {
    expect(
      () => buildTransferBody(
        from: kFrom,
        to: kTo,
        amountNanos: BigInt.one,
        nonce: 0,
        maxFee: 1000000000, // the 0.3.0 bug value: 21,000 ANM fee
      ),
      throwsArgumentError,
    );
  });

  test('a sane explicit fee under the cap is accepted', () {
    final body = buildTransferBody(
      from: kFrom,
      to: kTo,
      amountNanos: BigInt.one,
      nonce: 0,
      maxFee: 1000, // 21_000_000 nanos = 0.021 ANM worst case
    );
    expect((body['gas'] as Map)['price'], 1000);
  });
}
