import 'package:logging/logging.dart';

/// Set up logging for the application
void setupLogger({Level level = Level.INFO}) {
  Logger.root.level = level;
  Logger.root.onRecord.listen((record) {
    // Format: [LEVEL] LoggerName: Message
    final levelStr = record.level.name.padRight(7);
    final timeStr = record.time.toIso8601String().substring(11, 23);
    print('$timeStr [$levelStr] ${record.loggerName}: ${record.message}');
    
    if (record.error != null) {
      print('  Error: ${record.error}');
    }
    if (record.stackTrace != null) {
      print('  Stack trace:\n${record.stackTrace}');
    }
  });
}
