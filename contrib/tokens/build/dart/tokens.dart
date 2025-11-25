// Animica Tokens — Flutter ThemeData bridge
// Generated from contrib/tokens/tokens.json (+ dark overrides)
// SPDX-License-Identifier: MIT

import 'package:flutter/material.dart';

/// Version of the token bundle.
const String kAnimicaTokensVersion = '1.0.0';

/// -------- Colors (Light) --------
class AnmColorsLight {
  // Primary
  static const primary50  = Color(0xFFEEF3FF);
  static const primary100 = Color(0xFFDCE7FF);
  static const primary200 = Color(0xFFC3D6FF);
  static const primary300 = Color(0xFFA7C1FF);
  static const primary400 = Color(0xFF7FA2FF);
  static const primary500 = Color(0xFF4B7DFF);
  static const primary600 = Color(0xFF2E63FF);
  static const primary700 = Color(0xFF254FCC);
  static const primary800 = Color(0xFF1C3D99);
  static const primary900 = Color(0xFF132966);

  // Neutral
  static const neutral50  = Color(0xFFF6F8FF);
  static const neutral100 = Color(0xFFECEFFC);
  static const neutral200 = Color(0xFFE1E6F5);
  static const neutral300 = Color(0xFFCDD4E6);
  static const neutral400 = Color(0xFFB4BED3);
  static const neutral500 = Color(0xFF98A4BD);
  static const neutral600 = Color(0xFF7B88A3);
  static const neutral700 = Color(0xFF5E6B88);
  static const neutral800 = Color(0xFF39425A);
  static const neutral900 = Color(0xFF0E1222);

  // Surface
  static const surface0   = Color(0xFFFFFFFF);
  static const surface50  = Color(0xFFF8FAFF);
  static const surface100 = Color(0xFFF2F5FF);
  static const surface800 = Color(0xFF121623);
  static const surface900 = Color(0xFF0A0C14);

  // Success
  static const success600 = Color(0xFF22A06B);

  // Warning
  static const warning600 = Color(0xFFDFA71B);

  // Error
  static const error600   = Color(0xFFE45757);
}

/// -------- Colors (Dark Overrides) --------
class AnmColorsDark {
  // Surfaces
  static const surface0   = Color(0xFF0D0F18);
  static const surface50  = Color(0xFF0F1220);
  static const surface100 = Color(0xFF121623);
  static const surface800 = Color(0xFF0B0E17);
  static const surface900 = Color(0xFF070A11);

  // Neutral (inverted scale)
  static const neutral50  = Color(0xFFE8ECF8);
  static const neutral100 = Color(0xFFD5DBEF);
  static const neutral200 = Color(0xFFC2CBE3);
  static const neutral300 = Color(0xFFA7B2CF);
  static const neutral400 = Color(0xFF8E9BBD);
  static const neutral500 = Color(0xFF7C8AAE);
  static const neutral600 = Color(0xFFAEB8CF);
  static const neutral700 = Color(0xFFC7CFE0);
  static const neutral800 = Color(0xFFE1E6F5);
  static const neutral900 = Color(0xFFF6F8FF);

  // Primary (lighter on dark)
  static const primary400 = Color(0xFF8AA9FF);
  static const primary500 = Color(0xFF5E86FF);
  static const primary600 = Color(0xFF4B7DFF);
  static const primary700 = Color(0xFF2E63FF);
  static const primary800 = Color(0xFF254FCC);

  // Accents
  static const success600 = Color(0xFF2CAF7E);
  static const warning600 = Color(0xFFE3A822);
  static const error600   = Color(0xFFE45757);
}

/// -------- Spacing / Radii --------
class AnmSpace {
  static const s1 = 4.0;
  static const s2 = 8.0;
  static const s3 = 12.0;
  static const s4 = 16.0;
  static const s5 = 20.0;
  static const s6 = 24.0;
  static const s8 = 32.0;
  static const s10 = 40.0;
  static const s12 = 48.0;
  static const s16 = 64.0;
  static const s20 = 80.0;
  static const s24 = 96.0;
}

class AnmRadius {
  static const sm = 6.0;
  static const md = 10.0;
  static const lg = 14.0;
  static const xl = 18.0;
  static const x2l = 24.0;
  static const pill = 9999.0;
}

/// -------- Typography --------
class AnmTypography {
  static const fontFamilyBase = 'Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif';
  static const fontFamilyMono = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace';

  static const sizeXs = 12.0;
  static const sizeSm = 14.0;
  static const sizeBase = 16.0;
  static const sizeMd = 18.0;
  static const sizeLg = 20.0;
  static const sizeXl = 24.0;
  static const size2xl = 32.0;
  static const size3xl = 40.0;
  static const size4xl = 48.0;

  static const weightRegular = FontWeight.w400;
  static const weightMedium  = FontWeight.w500;
  static const weightSemibold= FontWeight.w600;
  static const weightBold    = FontWeight.w700;

  static TextTheme buildTextTheme(Color color) {
    return TextTheme(
      bodyLarge: TextStyle(
        fontFamily: 'Inter',
        fontSize: sizeBase,
        height: 1.5,
        color: color,
        fontWeight: weightRegular,
      ),
      bodyMedium: TextStyle(
        fontFamily: 'Inter',
        fontSize: sizeSm,
        height: 1.5,
        color: color.withOpacity(0.92),
        fontWeight: weightRegular,
      ),
      labelLarge: TextStyle(
        fontFamily: 'Inter',
        fontSize: sizeSm,
        height: 1.15,
        color: color,
        fontWeight: weightSemibold,
      ),
      titleMedium: TextStyle(
        fontFamily: 'Inter',
        fontSize: sizeLg,
        height: 1.3,
        color: color,
        fontWeight: weightSemibold,
      ),
      titleLarge: TextStyle(
        fontFamily: 'Inter',
        fontSize: sizeXl,
        height: 1.2,
        color: color,
        fontWeight: weightBold,
      ),
      headlineSmall: TextStyle(
        fontFamily: 'Inter',
        fontSize: size2xl,
        height: 1.15,
        color: color,
        fontWeight: weightBold,
      ),
      headlineMedium: TextStyle(
        fontFamily: 'Inter',
        fontSize: size3xl,
        height: 1.1,
        color: color,
        fontWeight: weightBold,
      ),
    );
  }
}

/// -------- Elevation (Shadows) --------
/// Note: Flutter's Material uses elevation rather than raw shadows; these helper
/// shadows are useful for custom containers where elevation is not applicable.
class AnmShadows {
  static const sm   = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.06), blurRadius: 2, offset: Offset(0, 1));
  static const md   = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.08), blurRadius: 8, offset: Offset(0, 2));
  static const lg   = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.10), blurRadius: 24, offset: Offset(0, 8));

  static const darkSm = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.35), blurRadius: 1, offset: Offset(0, 1));
  static const darkMd = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.40), blurRadius: 6, offset: Offset(0, 2));
  static const darkLg = BoxShadow(color: Color.fromRGBO(0, 0, 0, 0.44), blurRadius: 18, offset: Offset(0, 8));
}

/// -------- ThemeData Builders --------
ThemeData buildLightTheme() {
  final textColor = AnmColorsLight.neutral900;
  final textTheme = AnmTypography.buildTextTheme(textColor);

  final colorScheme = ColorScheme(
    brightness: Brightness.light,
    primary: AnmColorsLight.primary600,
    onPrimary: AnmColorsLight.surface0,
    secondary: AnmColorsLight.primary400,
    onSecondary: AnmColorsLight.surface0,
    error: AnmColorsLight.error600,
    onError: AnmColorsLight.surface0,
    surface: AnmColorsLight.surface0,
    onSurface: textColor,
    // material 3 extras
    primaryContainer: AnmColorsLight.primary100,
    onPrimaryContainer: AnmColorsLight.primary800,
    secondaryContainer: AnmColorsLight.neutral100,
    onSecondaryContainer: AnmColorsLight.neutral800,
    surfaceContainerHighest: AnmColorsLight.surface100,
    surfaceContainerLow: AnmColorsLight.surface50,
    tertiary: AnmColorsLight.success600,
    onTertiary: AnmColorsLight.surface0,
    outline: AnmColorsLight.neutral300,
    shadow: Colors.black.withOpacity(0.06),
    scrim: Colors.black.withOpacity(0.3),
    surfaceTint: AnmColorsLight.primary600,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.light,
    colorScheme: colorScheme,
    scaffoldBackgroundColor: AnmColorsLight.surface50,
    canvasColor: AnmColorsLight.surface0,
    textTheme: textTheme,
    primaryTextTheme: textTheme.apply(bodyColor: AnmColorsLight.surface0, displayColor: AnmColorsLight.surface0),
    appBarTheme: AppBarTheme(
      backgroundColor: AnmColorsLight.surface0,
      foregroundColor: textColor,
      elevation: 0,
      shadowColor: AnmShadows.sm.color,
      centerTitle: true,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AnmColorsLight.surface0,
      contentPadding: const EdgeInsets.symmetric(horizontal: AnmSpace.s4, vertical: AnmSpace.s3),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsLight.neutral300),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsLight.neutral300),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsLight.primary600, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsLight.error600),
      ),
      labelStyle: TextStyle(color: AnmColorsLight.neutral700),
      hintStyle: TextStyle(color: AnmColorsLight.neutral600),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AnmColorsLight.primary600,
        foregroundColor: AnmColorsLight.surface0,
        padding: const EdgeInsets.symmetric(horizontal: AnmSpace.s6, vertical: AnmSpace.s4),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AnmColorsLight.primary700,
        side: BorderSide(color: AnmColorsLight.primary600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
        padding: const EdgeInsets.symmetric(horizontal: AnmSpace.s6, vertical: AnmSpace.s4),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    cardTheme: CardTheme(
      color: AnmColorsLight.surface0,
      elevation: 0,
      shadowColor: AnmShadows.md.color,
      margin: const EdgeInsets.all(AnmSpace.s4),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
    ),
    dialogTheme: DialogTheme(
      backgroundColor: AnmColorsLight.surface0,
      elevation: 8,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.xl)),
    ),
    dividerTheme: DividerThemeData(
      color: AnmColorsLight.neutral300,
      thickness: 1,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AnmColorsLight.neutral900,
      contentTextStyle: textTheme.bodyMedium!.copyWith(color: AnmColorsLight.surface0),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
    ),
  );
}

ThemeData buildDarkTheme() {
  final textColor = AnmColorsDark.neutral900;
  final textTheme = AnmTypography.buildTextTheme(textColor);

  final colorScheme = ColorScheme(
    brightness: Brightness.dark,
    primary: AnmColorsDark.primary600,
    onPrimary: AnmColorsDark.surface0,
    secondary: AnmColorsDark.primary400,
    onSecondary: AnmColorsDark.surface0,
    error: AnmColorsDark.error600,
    onError: AnmColorsDark.surface0,
    surface: AnmColorsDark.surface900,
    onSurface: textColor,
    primaryContainer: AnmColorsDark.surface100,
    onPrimaryContainer: textColor,
    secondaryContainer: AnmColorsDark.surface100,
    onSecondaryContainer: textColor,
    surfaceContainerHighest: AnmColorsDark.surface100,
    surfaceContainerLow: AnmColorsDark.surface0,
    tertiary: AnmColorsDark.success600,
    onTertiary: AnmColorsDark.surface0,
    outline: AnmColorsDark.neutral300,
    shadow: Colors.black.withOpacity(0.4),
    scrim: Colors.black.withOpacity(0.6),
    surfaceTint: AnmColorsDark.primary600,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: colorScheme,
    scaffoldBackgroundColor: AnmColorsDark.surface0,
    canvasColor: AnmColorsDark.surface0,
    textTheme: textTheme,
    primaryTextTheme: textTheme.apply(bodyColor: AnmColorsDark.surface0, displayColor: AnmColorsDark.surface0),
    appBarTheme: AppBarTheme(
      backgroundColor: AnmColorsDark.surface0,
      foregroundColor: textColor,
      elevation: 0,
      shadowColor: AnmShadows.darkSm.color,
      centerTitle: true,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AnmColorsDark.surface100,
      contentPadding: const EdgeInsets.symmetric(horizontal: AnmSpace.s4, vertical: AnmSpace.s3),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsDark.neutral300),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsDark.neutral300),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsDark.primary600, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AnmRadius.md),
        borderSide: BorderSide(color: AnmColorsDark.error600),
      ),
      labelStyle: TextStyle(color: AnmColorsDark.neutral600),
      hintStyle: TextStyle(color: AnmColorsDark.neutral500),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AnmColorsDark.primary600,
        foregroundColor: AnmColorsDark.surface0,
        padding: const EdgeInsets.symmetric(horizontal: AnmSpace.s6, vertical: AnmSpace.s4),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AnmColorsDark.primary600,
        side: BorderSide(color: AnmColorsDark.primary600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
        padding: const EdgeInsets.symmetric(horizontal: AnmSpace.s6, vertical: AnmSpace.s4),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    cardTheme: CardTheme(
      color: AnmColorsDark.surface100,
      elevation: 0,
      shadowColor: AnmShadows.darkMd.color,
      margin: const EdgeInsets.all(AnmSpace.s4),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
    ),
    dialogTheme: DialogTheme(
      backgroundColor: AnmColorsDark.surface100,
      elevation: 8,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.xl)),
    ),
    dividerTheme: DividerThemeData(
      color: AnmColorsDark.neutral300,
      thickness: 1,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AnmColorsDark.surface900,
      contentTextStyle: textTheme.bodyMedium!.copyWith(color: AnmColorsDark.neutral900),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AnmRadius.lg)),
    ),
  );
}

/// Utility: choose theme by mode or platform brightness.
ThemeData buildTheme({Brightness? platformBrightness, bool? forceDark}) {
  if (forceDark == true) return buildDarkTheme();
  if (forceDark == false) return buildLightTheme();
  final b = platformBrightness ?? WidgetsBinding.instance.platformDispatcher.platformBrightness;
  return b == Brightness.dark ? buildDarkTheme() : buildLightTheme();
}

/// Optional helper container that applies card styling per tokens.
class AnmCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry? padding;
  final EdgeInsetsGeometry? margin;
  final Color? color;

  const AnmCard({super.key, required this.child, this.padding, this.margin, this.color});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final boxShadow = [
      if (isDark) AnmShadows.darkMd else AnmShadows.md,
    ];
    final bg = color ??
        (isDark ? AnmColorsDark.surface100 : AnmColorsLight.surface0);

    return Container(
      margin: margin ?? const EdgeInsets.all(AnmSpace.s4),
      padding: padding ?? const EdgeInsets.all(AnmSpace.s4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(AnmRadius.lg),
        boxShadow: boxShadow,
      ),
      child: child,
    );
  }
}
