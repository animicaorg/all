import 'package:flutter_test/flutter_test.dart';
import 'package:animica_miner_wallet/models/miner_config.dart';

void main() {
  group('MinerConfig', () {
    test('creates default configuration', () {
      final config = MinerConfig.defaults();
      
      expect(config.network.chainId, 1337);
      expect(config.network.rpcUrl, 'http://127.0.0.1:8545');
      expect(config.miner.autoStart, false);
      expect(config.miner.blocksPerBatch, 10);
      expect(config.cpu.enabled, true);
      expect(config.cpu.threads, 4);
    });

    test('serializes to and from JSON', () {
      final original = MinerConfig.defaults();
      final json = original.toJson();
      final restored = MinerConfig.fromJson(json);
      
      expect(restored.network.chainId, original.network.chainId);
      expect(restored.network.rpcUrl, original.network.rpcUrl);
      expect(restored.miner.autoStart, original.miner.autoStart);
      expect(restored.cpu.threads, original.cpu.threads);
    });

    test('copyWith creates modified copy', () {
      final original = MinerConfig.defaults();
      final modified = original.copyWith(
        miner: original.miner.copyWith(
          payoutAddress: 'anim1test1234567890abcdefghijklmnopqrstuvwxyz',
          autoStart: true,
        ),
      );
      
      expect(modified.miner.payoutAddress, contains('anim1test'));
      expect(modified.miner.autoStart, true);
      expect(modified.cpu.threads, original.cpu.threads); // Unchanged
    });
  });
}
