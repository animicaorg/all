import 'package:flutter_test/flutter_test.dart';
import 'package:animica_miner_wallet/utils/formatters.dart';
import 'package:animica_miner_wallet/constants.dart';

void main() {
  group('Formatters', () {
    test('formatAnm converts base units to ANM', () {
      expect(formatAnm(1000000000), '1.000000 ANM');
      expect(formatAnm(1500000000), '1.500000 ANM');
      expect(formatAnm(0), '0.000000 ANM');
    });

    test('formatHashrate uses appropriate units', () {
      expect(formatHashrate(100), '100.00 H/s');
      expect(formatHashrate(1500), '1.50 KH/s');
      expect(formatHashrate(2500000), '2.50 MH/s');
      expect(formatHashrate(3500000000), '3.50 GH/s');
    });

    test('formatDuration creates human-readable strings', () {
      expect(formatDuration(const Duration(seconds: 30)), '30s');
      expect(formatDuration(const Duration(minutes: 5, seconds: 30)), '5m 30s');
      expect(formatDuration(const Duration(hours: 2, minutes: 15)), '2h 15m');
      expect(formatDuration(const Duration(days: 1, hours: 3)), '1d 3h');
    });

    test('truncateAddress truncates long addresses', () {
      const address = 'anim1abcdefghijklmnopqrstuvwxyz1234567890';
      final truncated = truncateAddress(address);
      expect(truncated, contains('...'));
      expect(truncated.length, lessThan(address.length));
    });

    test('isValidAddress validates address format', () {
      expect(isValidAddress('anim1abcdefghijklmnopqrstuvwxyz1234567890'), true);
      expect(isValidAddress('invalid'), false);
      expect(isValidAddress('anim1short'), false);
      expect(isValidAddress('wrong1abcdefghijklmnopqrstuvwxyz1234567890'), false);
    });
  });
}
