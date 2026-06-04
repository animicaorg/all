import 'dart:typed_data';
import 'package:animica_wallet/services/address.dart';

void main() {
  final pub = Uint8List(1952);
  for (var i = 0; i < pub.length; i++) {
    pub[i] = i % 32;
  }
  const expected =
      'anim1zqp6qkly84xk6jt6e0janskd9lkj95uzrumtln3crjvwkak0adxlyus3klghf';
  final got = addressFromPubkey(pub, 4099);
  print('expected: $expected');
  print('got:      $got');
  print('MATCH: ${got == expected}');

  // round-trip decode of the canonical bech32m address
  final dec = decodeAddress(expected);
  print('decode algId: ${dec.algId} (expect 4099)');
  print('isValid(canonical bech32m): ${isValidAnimAddress(expected)}');
  // an old bech32 (wrong) address must now be rejected
  print('isValid(old bech32 form): '
      '${isValidAnimAddress("anim1zqp6qkly84xk6jt6e0janskd9lkj95uzrumtln3crjvwkak0adxlyusy20yjt")}');
}
