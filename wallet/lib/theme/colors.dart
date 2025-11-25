import 'package:flutter/material.dart';

/// Design tokens for Animica Wallet (light & dark).
/// These are semantic colors consumed by themes and widgets.
/// Keep raw hex values here; build ThemeData in `themes.dart`.
class AppColors {
  // Brand (mint/teal glow used across the suite)
  static const Color brand            = Color(0xFF5EEAD4); // mint
  static const Color brandDeep        = Color(0xFF14B8A6); // deeper mint
  static const Color brandSoft        = Color(0xFF99F6E4); // lighter mint
  static const Color brandContrastFg  = Color(0xFF002A25); // readable on brand bg

  // Status
  static const Color success = Color(0xFF22C55E); // green-500
  static const Color warning = Color(0xFFF59E0B); // amber-500
  static const Color error   = Color(0xFFEF4444); // red-500
  static const Color info    = Color(0xFF38BDF8); // sky-400

  // Charts (Γ, DA, mempool)—mirrors contrib/explorer/charts/palette.json
  static const Color gammaPos   = Color(0xFF60A5FA); // blue-400
  static const Color gammaNeg   = Color(0xFFF472B6); // pink-400
  static const Color mempoolTx  = Color(0xFFFBBF24); // amber-400
  static const Color daBlob     = Color(0xFFA78BFA); // violet-400
  static const Color quantum    = Color(0xFF34D399); // emerald-400
  static const Color ai         = Color(0xFFFCA5A5); // rose-300
  static const Color randomness = Color(0xFF93C5FD); // blue-300
}

class LightColors {
  // Backgrounds & surfaces
  static const Color background   = Color(0xFFF8FAFC); // slate-50
  static const Color surface      = Color(0xFFFFFFFF); // white
  static const Color surfaceAlt   = Color(0xFFF1F5F9); // slate-100
  static const Color surfaceMuted = Color(0xFFE2E8F0); // slate-200

  // Text
  static const Color textPrimary   = Color(0xFF0F172A); // slate-900
  static const Color textSecondary = Color(0xFF475569); // slate-600
  static const Color textMuted     = Color(0xFF64748B); // slate-500
  static const Color textOnBrand   = AppColors.brandContrastFg;

  // Lines / dividers
  static const Color line = Color(0xFFE2E8F0); // slate-200

  // Focus / selection
  static const Color focusRing = Color(0x6614B8A6); // brandDeep @ 40%
  static const Color selection = Color(0x335EEAD4); // brand @ 20%

  // Elevated overlays
  static const Color overlaySoft  = Color(0x0F000000); // 6%
  static const Color overlayStrong= Color(0x26000000); // 15%
}

class DarkColors {
  // Backgrounds & surfaces (custom + slate)
  static const Color background   = Color(0xFF0B0D12); // deep canvas (suite default)
  static const Color surface      = Color(0xFF0F172A); // slate-900 tone
  static const Color surfaceAlt   = Color(0xFF111827); // gray-900-ish
  static const Color surfaceMuted = Color(0xFF1F2937); // gray-800

  // Text
  static const Color textPrimary   = Color(0xFFE5E7EB); // gray-200
  static const Color textSecondary = Color(0xFFCBD5E1); // slate-300
  static const Color textMuted     = Color(0xFF94A3B8); // slate-400
  static const Color textOnBrand   = Colors.black;      // brand is bright on dark

  // Lines / dividers
  static const Color line = Color(0xFF24303F); // dim line on dark

  // Focus / selection
  static const Color focusRing = Color(0x6634D399); // emerald-ish @ 40%
  static const Color selection = Color(0x335EEAD4); // brand @ 20%

  // Elevated overlays
  static const Color overlaySoft  = Color(0x33FFFFFF); // 20% white
  static const Color overlayStrong= Color(0x59FFFFFF); // 35% white
}

/// Helpful gradients kept centralized so hero/splash keep the same look.
class Gradients {
  static const List<Color> brandGlow = [
    Color(0x0034D399), // transparent center for subtle glow
    Color(0x2234D399),
    Color(0x4434D399),
    Color(0x0034D399),
  ];

  static const List<Color> heroMint = [
    Color(0xFF0F172A), // deep blue
    Color(0xFF0B0D12), // near-black
  ];
}

/// Convenience helpers.
extension ColorUtils on Color {
  Color withAlphaPct(int pct) => withAlpha(((pct.clamp(0, 100)) * 255 ~/ 100));
}
