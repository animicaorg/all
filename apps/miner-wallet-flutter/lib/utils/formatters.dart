import '../constants.dart';

/// Format ANM amount from base units to human-readable string
String formatAnm(int baseUnits) {
  final anm = baseUnits / AppConstants.anmBaseUnits;
  return '${anm.toStringAsFixed(6)} ANM';
}

/// Format hashrate with appropriate units (H/s, KH/s, MH/s, GH/s)
String formatHashrate(double hashrate) {
  if (hashrate < 1000) {
    return '${hashrate.toStringAsFixed(2)} H/s';
  } else if (hashrate < 1000000) {
    return '${(hashrate / 1000).toStringAsFixed(2)} KH/s';
  } else if (hashrate < 1000000000) {
    return '${(hashrate / 1000000).toStringAsFixed(2)} MH/s';
  } else {
    return '${(hashrate / 1000000000).toStringAsFixed(2)} GH/s';
  }
}

/// Format duration to human-readable string
String formatDuration(Duration duration) {
  if (duration.inDays > 0) {
    return '${duration.inDays}d ${duration.inHours % 24}h';
  } else if (duration.inHours > 0) {
    return '${duration.inHours}h ${duration.inMinutes % 60}m';
  } else if (duration.inMinutes > 0) {
    return '${duration.inMinutes}m ${duration.inSeconds % 60}s';
  } else {
    return '${duration.inSeconds}s';
  }
}

/// Truncate address for display (e.g., anim1...abc123)
String truncateAddress(String address, {int prefixLen = 10, int suffixLen = 6}) {
  if (address.length <= prefixLen + suffixLen) {
    return address;
  }
  return '${address.substring(0, prefixLen)}...${address.substring(address.length - suffixLen)}';
}

/// Validate Animica address format
bool isValidAddress(String address) {
  if (address.length < AppConstants.minAddressLength) {
    return false;
  }
  if (!address.startsWith(AppConstants.addressPrefix)) {
    return false;
  }
  // Additional validation could include bech32m checksum
  return true;
}
