// App theme — neutral dark-first Material design with an amber accent
// that matches the rest of the Animica brand (animica.xyz cards,
// founders gold).

import 'package:flutter/material.dart';

class AnimicaTheme {
  static const seed = Color(0xFFFCB400);

  static ThemeData light() => ThemeData(
        useMaterial3: true,
        brightness: Brightness.light,
        colorScheme: ColorScheme.fromSeed(seedColor: seed, brightness: Brightness.light),
        cardTheme: const CardThemeData(elevation: 0, margin: EdgeInsets.zero),
      );

  static ThemeData dark() => ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(seedColor: seed, brightness: Brightness.dark),
        cardTheme: const CardThemeData(elevation: 0, margin: EdgeInsets.zero),
      );
}
