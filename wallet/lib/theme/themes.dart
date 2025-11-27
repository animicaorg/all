import 'package:flutter/material.dart';
import 'colors.dart';

/// Material 3 themes for Animica Wallet.
/// Uses Inter as the default font (declared in pubspec).
ThemeData buildLightTheme() {
  final colorScheme = ColorScheme(
    brightness: Brightness.light,
    primary: AppColors.brandDeep,
    onPrimary: AppColors.brandContrastFg,
    primaryContainer: AppColors.brandSoft,
    onPrimaryContainer: DarkColors.textPrimary,
    secondary: AppColors.info,
    onSecondary: DarkColors.textPrimary,
    secondaryContainer: LightColors.surfaceAlt,
    onSecondaryContainer: LightColors.textPrimary,
    tertiary: AppColors.quantum,
    onTertiary: DarkColors.textPrimary,
    tertiaryContainer: LightColors.surfaceAlt,
    onTertiaryContainer: LightColors.textPrimary,
    error: AppColors.error,
    onError: Colors.white,
    errorContainer: const Color(0xFFFEE2E2),
    onErrorContainer: const Color(0xFF7F1D1D),
    background: LightColors.background,
    onBackground: LightColors.textPrimary,
    surface: LightColors.surface,
    onSurface: LightColors.textPrimary,
    surfaceVariant: LightColors.surfaceAlt,
    onSurfaceVariant: LightColors.textSecondary,
    outline: LightColors.line,
    outlineVariant: LightColors.surfaceMuted,
    shadow: Colors.black.withOpacity(0.12),
    scrim: Colors.black.withOpacity(0.40),
    inverseSurface: DarkColors.surface,
    onInverseSurface: DarkColors.textPrimary,
    inversePrimary: AppColors.brand,
  );

  final base = ThemeData(
    useMaterial3: true,
    fontFamily: 'Inter',
    colorScheme: colorScheme,
    visualDensity: VisualDensity.standard,
    scaffoldBackgroundColor: colorScheme.background,
    canvasColor: colorScheme.background,
  );

  return base.copyWith(
    textTheme: _buildTextTheme(base.textTheme, isDark: false),
    appBarTheme: AppBarTheme(
      backgroundColor: colorScheme.surface,
      foregroundColor: colorScheme.onSurface,
      elevation: 0,
      centerTitle: false,
      surfaceTintColor: colorScheme.surface,
      titleTextStyle: _buildTextTheme(base.textTheme, isDark: false)
          .titleMedium
          ?.copyWith(fontWeight: FontWeight.w600),
    ),
    cardTheme: CardThemeData(
      color: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      elevation: 1,
      margin: const EdgeInsets.all(0),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
    ),
    dividerTheme: DividerThemeData(
      color: LightColors.line,
      thickness: 1,
      space: 1,
    ),
    inputDecorationTheme: _inputTheme(colorScheme, isDark: false),
    filledButtonTheme: FilledButtonThemeData(
      style: ButtonStyle(
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
        padding: MaterialStateProperty.all(
          const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ButtonStyle(
        backgroundColor: MaterialStateProperty.all(colorScheme.primary),
        foregroundColor: MaterialStateProperty.all(colorScheme.onPrimary),
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
        elevation: MaterialStateProperty.all(0),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: ButtonStyle(
        side: MaterialStatePropertyAll(BorderSide(color: colorScheme.outline)),
        foregroundColor: MaterialStateProperty.all(colorScheme.onSurface),
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
      ),
    ),
    chipTheme: _chipTheme(colorScheme, isDark: false),
    dialogTheme: DialogThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: colorScheme.surface,
      contentTextStyle: base.textTheme.bodyMedium?.copyWith(
        color: colorScheme.onSurface,
      ),
      behavior: SnackBarBehavior.floating,
      elevation: 2,
      actionTextColor: colorScheme.primary,
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      indicatorColor: AppColors.brand.withAlpha(32),
      elevation: 0,
      labelTextStyle: MaterialStateProperty.all(
        base.textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w600),
      ),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? colorScheme.primary
            : LightColors.surfaceMuted;
      }),
      trackColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? AppColors.brand.withAlpha(80)
            : LightColors.surfaceAlt;
      }),
    ),
    checkboxTheme: CheckboxThemeData(
      fillColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? colorScheme.primary
            : LightColors.surfaceMuted;
      }),
      side: BorderSide(color: colorScheme.outline),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: colorScheme.primary,
      linearMinHeight: 4,
    ),
    listTileTheme: ListTileThemeData(
      iconColor: AppColors.brandDeep,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: Colors.black87,
        borderRadius: BorderRadius.circular(8),
      ),
      textStyle: base.textTheme.labelSmall?.copyWith(color: Colors.white),
    ),
  );
}

ThemeData buildDarkTheme() {
  final colorScheme = ColorScheme(
    brightness: Brightness.dark,
    primary: AppColors.brand,
    onPrimary: DarkColors.textOnBrand,
    primaryContainer: AppColors.brandDeep.withAlpha(60),
    onPrimaryContainer: DarkColors.textPrimary,
    secondary: AppColors.info,
    onSecondary: Colors.black,
    secondaryContainer: DarkColors.surfaceMuted,
    onSecondaryContainer: DarkColors.textPrimary,
    tertiary: AppColors.quantum,
    onTertiary: Colors.black,
    tertiaryContainer: DarkColors.surfaceMuted,
    onTertiaryContainer: DarkColors.textPrimary,
    error: AppColors.error,
    onError: Colors.white,
    errorContainer: const Color(0xFF7F1D1D),
    onErrorContainer: const Color(0xFFFEE2E2),
    background: DarkColors.background,
    onBackground: DarkColors.textPrimary,
    surface: DarkColors.surface,
    onSurface: DarkColors.textPrimary,
    surfaceVariant: DarkColors.surfaceAlt,
    onSurfaceVariant: DarkColors.textSecondary,
    outline: DarkColors.line,
    outlineVariant: DarkColors.surfaceMuted,
    shadow: Colors.black.withOpacity(0.70),
    scrim: Colors.black.withOpacity(0.70),
    inverseSurface: LightColors.surface,
    onInverseSurface: LightColors.textPrimary,
    inversePrimary: AppColors.brandDeep,
  );

  final base = ThemeData(
    useMaterial3: true,
    fontFamily: 'Inter',
    colorScheme: colorScheme,
    visualDensity: VisualDensity.standard,
    scaffoldBackgroundColor: colorScheme.background,
    canvasColor: colorScheme.background,
  );

  return base.copyWith(
    textTheme: _buildTextTheme(base.textTheme, isDark: true),
    appBarTheme: AppBarTheme(
      backgroundColor: colorScheme.surface,
      foregroundColor: colorScheme.onSurface,
      elevation: 0,
      centerTitle: false,
      surfaceTintColor: colorScheme.surface,
      titleTextStyle: _buildTextTheme(base.textTheme, isDark: true)
          .titleMedium
          ?.copyWith(fontWeight: FontWeight.w600),
    ),
    cardTheme: CardThemeData(
      color: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      elevation: 0,
      margin: const EdgeInsets.all(0),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
    ),
    dividerTheme: DividerThemeData(
      color: DarkColors.line,
      thickness: 1,
      space: 1,
    ),
    inputDecorationTheme: _inputTheme(colorScheme, isDark: true),
    filledButtonTheme: FilledButtonThemeData(
      style: ButtonStyle(
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
        padding: MaterialStateProperty.all(
          const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ButtonStyle(
        backgroundColor: MaterialStateProperty.all(colorScheme.primary),
        foregroundColor: MaterialStateProperty.all(colorScheme.onPrimary),
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
        elevation: MaterialStateProperty.all(0),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: ButtonStyle(
        side: MaterialStatePropertyAll(BorderSide(color: colorScheme.outline)),
        foregroundColor: MaterialStateProperty.all(colorScheme.onSurface),
        minimumSize: MaterialStateProperty.all(const Size(48, 48)),
        shape: MaterialStateProperty.all(
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
      ),
    ),
    chipTheme: _chipTheme(colorScheme, isDark: true),
    dialogTheme: DialogThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: DarkColors.surfaceAlt,
      contentTextStyle: base.textTheme.bodyMedium?.copyWith(
        color: colorScheme.onSurface,
      ),
      behavior: SnackBarBehavior.floating,
      elevation: 2,
      actionTextColor: colorScheme.primary,
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: colorScheme.surface,
      surfaceTintColor: colorScheme.surface,
      indicatorColor: AppColors.brand.withAlpha(48),
      elevation: 0,
      labelTextStyle: MaterialStateProperty.all(
        base.textTheme.labelMedium?.copyWith(
          fontWeight: FontWeight.w600,
          color: colorScheme.onSurface,
        ),
      ),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? colorScheme.primary
            : DarkColors.surfaceMuted;
      }),
      trackColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? AppColors.brand.withAlpha(120)
            : DarkColors.surfaceAlt;
      }),
    ),
    checkboxTheme: CheckboxThemeData(
      fillColor: MaterialStateProperty.resolveWith((states) {
        return states.contains(MaterialState.selected)
            ? colorScheme.primary
            : DarkColors.surfaceMuted;
      }),
      side: BorderSide(color: colorScheme.outline),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: colorScheme.primary,
      linearMinHeight: 4,
    ),
    listTileTheme: ListTileThemeData(
      iconColor: AppColors.brand,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(
        color: const Color(0xFF111827),
        borderRadius: BorderRadius.circular(8),
      ),
      textStyle: base.textTheme.labelSmall?.copyWith(color: Colors.white),
    ),
  );
}

// ---- helpers ---------------------------------------------------------------

TextTheme _buildTextTheme(TextTheme base, {required bool isDark}) {
  // Inter reads best with slight tighter letter spacing on headings.
  final onBg = isDark ? DarkColors.textPrimary : LightColors.textPrimary;
  final onBgMuted = isDark ? DarkColors.textMuted : LightColors.textMuted;

  return base.copyWith(
    displayLarge:  base.displayLarge?.copyWith(color: onBg, fontWeight: FontWeight.w700),
    displayMedium: base.displayMedium?.copyWith(color: onBg, fontWeight: FontWeight.w700),
    displaySmall:  base.displaySmall?.copyWith(color: onBg, fontWeight: FontWeight.w700),
    headlineLarge: base.headlineLarge?.copyWith(color: onBg, fontWeight: FontWeight.w700),
    headlineMedium:base.headlineMedium?.copyWith(color: onBg, fontWeight: FontWeight.w700),
    headlineSmall: base.headlineSmall?.copyWith(color: onBg, fontWeight: FontWeight.w600),
    titleLarge:    base.titleLarge?.copyWith(color: onBg, fontWeight: FontWeight.w600),
    titleMedium:   base.titleMedium?.copyWith(color: onBg, fontWeight: FontWeight.w600),
    titleSmall:    base.titleSmall?.copyWith(color: onBg, fontWeight: FontWeight.w600),
    bodyLarge:     base.bodyLarge?.copyWith(color: onBg),
    bodyMedium:    base.bodyMedium?.copyWith(color: onBg),
    bodySmall:     base.bodySmall?.copyWith(color: onBgMuted),
    labelLarge:    base.labelLarge?.copyWith(color: onBg, fontWeight: FontWeight.w600),
    labelMedium:   base.labelMedium?.copyWith(color: onBgMuted, fontWeight: FontWeight.w600),
    labelSmall:    base.labelSmall?.copyWith(color: onBgMuted),
  );
}

InputDecorationTheme _inputTheme(ColorScheme scheme, {required bool isDark}) {
  final fill = isDark ? DarkColors.surfaceAlt : LightColors.surfaceAlt;
  OutlineInputBorder _border(Color c) => OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: c, width: 1),
      );

  return InputDecorationTheme(
    filled: true,
    fillColor: fill,
    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
    hintStyle: TextStyle(
      color: isDark ? DarkColors.textMuted : LightColors.textMuted,
    ),
    labelStyle: TextStyle(
      color: isDark ? DarkColors.textSecondary : LightColors.textSecondary,
    ),
    enabledBorder: _border(scheme.outline.withOpacity(0.40)),
    focusedBorder: _border(scheme.primary),
    errorBorder: _border(scheme.error),
    focusedErrorBorder: _border(scheme.error),
    prefixIconColor: scheme.onSurfaceVariant,
    suffixIconColor: scheme.onSurfaceVariant,
  );
}

ChipThemeData _chipTheme(ColorScheme scheme, {required bool isDark}) {
  return ChipThemeData(
    backgroundColor:
        isDark ? DarkColors.surfaceAlt : LightColors.surfaceAlt,
    selectedColor: scheme.primary.withAlpha(48),
    disabledColor: scheme.surfaceVariant,
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    labelStyle: TextStyle(
      color: scheme.onSurface,
      fontWeight: FontWeight.w600,
    ),
    secondaryLabelStyle: TextStyle(
      color: scheme.onSurfaceVariant,
    ),
    side: BorderSide(color: scheme.outline.withOpacity(0.4)),
  );
}
