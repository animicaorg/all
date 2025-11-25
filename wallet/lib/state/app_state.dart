/*
 * Animica Wallet — App Settings State (theme / locale / prefs)
 *
 * Ephemeral state powered by Riverpod StateNotifier.
 * Persistence hooks (to/from JSON Map) are provided so you can wire whatever
 * storage you prefer (SharedPreferences, files, secure storage, etc.) later.
 *
 * Usage (Flutter):
 *   final settings = ref.watch(appSettingsProvider);
 *   ref.read(appSettingsProvider.notifier).setTheme(AppThemeMode.dark);
 */

import 'package:riverpod/riverpod.dart';

/// Theme preference for the app. `system` defers to platform brightness.
enum AppThemeMode { system, light, dark }

/// Immutable settings bag.
class AppSettings {
  final AppThemeMode theme;
  /// BCP-47 code like 'en', 'es', 'en-US'. Null = follow system.
  final String? localeCode;
  /// Enable haptic feedback on supported devices.
  final bool haptics;
  /// Use biometrics for quick-unlock (where platform supports it).
  final bool useBiometrics;
  /// Telemetry / analytics opt-in (off by default).
  final bool analyticsOptIn;
  /// Show developer options (extra screens/logs).
  final bool devMode;

  const AppSettings({
    this.theme = AppThemeMode.system,
    this.localeCode,
    this.haptics = true,
    this.useBiometrics = true,
    this.analyticsOptIn = false,
    this.devMode = false,
  });

  AppSettings copyWith({
    AppThemeMode? theme,
    String? localeCode,
    bool? haptics,
    bool? useBiometrics,
    bool? analyticsOptIn,
    bool? devMode,
    bool localeNull = false, // set to true to clear locale (follow system)
  }) {
    return AppSettings(
      theme: theme ?? this.theme,
      localeCode: localeNull ? null : (localeCode ?? this.localeCode),
      haptics: haptics ?? this.haptics,
      useBiometrics: useBiometrics ?? this.useBiometrics,
      analyticsOptIn: analyticsOptIn ?? this.analyticsOptIn,
      devMode: devMode ?? this.devMode,
    );
  }

  Map<String, dynamic> toJson() => {
        'theme': theme.name,
        'localeCode': localeCode,
        'haptics': haptics,
        'useBiometrics': useBiometrics,
        'analyticsOptIn': analyticsOptIn,
        'devMode': devMode,
      };

  factory AppSettings.fromJson(Map<String, dynamic> m) {
    final themeStr = (m['theme'] ?? 'system').toString();
    final mode = AppThemeMode.values.firstWhere(
      (e) => e.name == themeStr,
      orElse: () => AppThemeMode.system,
    );
    return AppSettings(
      theme: mode,
      localeCode: m['localeCode'] == null || m['localeCode'] == '' ? null : m['localeCode'].toString(),
      haptics: _asBool(m['haptics'], true),
      useBiometrics: _asBool(m['useBiometrics'], true),
      analyticsOptIn: _asBool(m['analyticsOptIn'], false),
      devMode: _asBool(m['devMode'], false),
    );
  }

  @override
  String toString() =>
      'AppSettings(theme:$theme, locale:${localeCode ?? "system"}, haptics:$haptics, bio:$useBiometrics, analytics:$analyticsOptIn, dev:$devMode)';

  @override
  bool operator ==(Object other) {
    return other is AppSettings &&
        other.theme == theme &&
        other.localeCode == localeCode &&
        other.haptics == haptics &&
        other.useBiometrics == useBiometrics &&
        other.analyticsOptIn == analyticsOptIn &&
        other.devMode == devMode;
  }

  @override
  int get hashCode =>
      Object.hash(theme, localeCode, haptics, useBiometrics, analyticsOptIn, devMode);
}

/// Riverpod notifier managing [AppSettings].
class AppSettingsNotifier extends StateNotifier<AppSettings> {
  AppSettingsNotifier() : super(const AppSettings());

  // --- setters / toggles ---

  void setTheme(AppThemeMode mode) {
    state = state.copyWith(theme: mode);
  }

  void setLocale(String? code) {
    // null → follow system
    if (code == null || code.trim().isEmpty) {
      state = state.copyWith(localeNull: true);
    } else {
      state = state.copyWith(localeCode: code.trim());
    }
  }

  void toggleHaptics() {
    state = state.copyWith(haptics: !state.haptics);
  }

  void setBiometrics(bool enabled) {
    state = state.copyWith(useBiometrics: enabled);
  }

  void setAnalyticsOptIn(bool enabled) {
    state = state.copyWith(analyticsOptIn: enabled);
  }

  void setDevMode(bool enabled) {
    state = state.copyWith(devMode: enabled);
  }

  // --- (optional) persistence hooks ---

  /// Apply settings from JSON (e.g., loaded from disk). Returns current state.
  AppSettings hydrate(Map<String, dynamic>? json) {
    if (json == null) return state;
    final next = AppSettings.fromJson(json);
    state = next;
    return next;
  }

  /// Produce a JSON map suitable for persistence.
  Map<String, dynamic> dehydrate() => state.toJson();
}

/// Global provider for app settings.
final appSettingsProvider =
    StateNotifierProvider<AppSettingsNotifier, AppSettings>(
  (ref) => AppSettingsNotifier(),
);

// ===== helpers =====

bool _asBool(dynamic v, bool fallback) {
  if (v is bool) return v;
  if (v == null) return fallback;
  final s = v.toString().toLowerCase();
  if (s == 'true' || s == '1' || s == 'yes' || s == 'y') return true;
  if (s == 'false' || s == '0' || s == 'no' || s == 'n') return false;
  return fallback;
}
