/// Configuration persistence service using SharedPreferences
library;

import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:logging/logging.dart';

import '../models/miner_config.dart';

class ConfigService {
  static const String _configKey = 'animica_miner_config';
  final _log = Logger('ConfigService');
  
  SharedPreferences? _prefs;

  /// Initialize the service
  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    _log.info('ConfigService initialized');
  }

  /// Load configuration from persistent storage
  Future<MinerConfig> load() async {
    if (_prefs == null) {
      await init();
    }

    try {
      final jsonString = _prefs!.getString(_configKey);
      if (jsonString == null) {
        _log.info('No saved config found, using defaults');
        return MinerConfig.defaults();
      }

      final json = jsonDecode(jsonString) as Map<String, dynamic>;
      final config = MinerConfig.fromJson(json);
      _log.info('Config loaded successfully');
      return config;
    } catch (e, stackTrace) {
      _log.warning('Failed to load config, using defaults', e, stackTrace);
      return MinerConfig.defaults();
    }
  }

  /// Save configuration to persistent storage
  Future<bool> save(MinerConfig config) async {
    if (_prefs == null) {
      await init();
    }

    try {
      final json = config.toJson();
      final jsonString = jsonEncode(json);
      await _prefs!.setString(_configKey, jsonString);
      _log.info('Config saved successfully');
      return true;
    } catch (e, stackTrace) {
      _log.severe('Failed to save config', e, stackTrace);
      return false;
    }
  }

  /// Clear all saved configuration
  Future<bool> clear() async {
    if (_prefs == null) {
      await init();
    }

    try {
      await _prefs!.remove(_configKey);
      _log.info('Config cleared');
      return true;
    } catch (e, stackTrace) {
      _log.severe('Failed to clear config', e, stackTrace);
      return false;
    }
  }

  /// Check if configuration exists
  Future<bool> exists() async {
    if (_prefs == null) {
      await init();
    }

    return _prefs!.containsKey(_configKey);
  }
}
