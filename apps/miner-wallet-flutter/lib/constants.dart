/// Application-wide constants
library;

class AppConstants {
  // Version
  static const String appVersion = '0.1.0';
  static const String appName = 'Animica Miner-Wallet';
  
  // Network defaults
  static const String defaultRpcUrl = 'http://127.0.0.1:8545';
  static const int defaultChainId = 1337;
  static const String defaultNetworkName = 'localnet';
  
  // Mining defaults
  static const int defaultBlocksPerBatch = 10;
  static const int minBlocksPerBatch = 1;
  static const int maxBlocksPerBatch = 100;
  static const int defaultCpuThreads = 4;
  static const double defaultShareTarget = 0.25;
  
  // UI constants
  static const int logRefreshIntervalMs = 1000;
  static const int statsRefreshIntervalMs = 5000;
  static const int walletRefreshIntervalMs = 10000;
  static const int maxLogLines = 1000;
  
  // Address validation
  static const int minAddressLength = 42;
  static const String addressPrefix = 'anim1';
  
  // ANM units
  static const int anmBaseUnits = 1000000000; // 1 ANM = 1e9 base units
  
  // File paths
  static const String configDirName = '.animica';
  static const String configFileName = 'miner-wallet-config.json';
  static const String walletsFileName = 'wallets.json';
  
  // Timeouts
  static const int rpcTimeoutSeconds = 30;
  static const int wsReconnectDelaySeconds = 5;
  
  // Device detection
  static const int minGpuMemoryMB = 2048;
  static const int minGpuComputeUnits = 4;
}
