import 'dart:ui';
import 'package:flutter/widgets.dart';

/// Animica brand mark (custom painter).
///
/// A compact, dependency-free logo widget that draws:
///  • a soft teal glow orb
///  • a thin circular ring
///  • an upward triangle (core mark)
///  • a small rounded bar below the triangle
///
/// Tweak [size], [color], [drawRing], and [drawGlow] to fit your UI.
class AnimicaLogo extends StatelessWidget {
  final double size;
  final Color color;
  final bool drawRing;
  final bool drawGlow;

  const AnimicaLogo({
    super.key,
    this.size = 64,
    this.color = const Color(0xFF5EEAD4), // mint/teal
    this.drawRing = true,
    this.drawGlow = true,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Animica logo',
      image: true,
      child: CustomPaint(
        size: Size.square(size),
        painter: _AnimicaPainter(
          color: color,
          drawRing: drawRing,
          drawGlow: drawGlow,
        ),
      ),
    );
  }
}

class _AnimicaPainter extends CustomPainter {
  final Color color;
  final bool drawRing;
  final bool drawGlow;

  const _AnimicaPainter({
    required this.color,
    required this.drawRing,
    required this.drawGlow,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final shortest = size.shortestSide;

    // --- Soft glow orb ---
    if (drawGlow) {
      final glowRadius = shortest * 0.48;
      final rect = Rect.fromCircle(center: Offset(cx, cy), radius: glowRadius);
      final glowPaint = Paint()
        ..shader = RadialGradient(
          colors: [
            color.withOpacity(0.70),
            color.withOpacity(0.00),
          ],
          stops: const [0.0, 1.0],
        ).createShader(rect)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 28);
      canvas.drawCircle(Offset(cx, cy), glowRadius * 0.66, glowPaint);
    }

    // --- Thin circular ring ---
    if (drawRing) {
      final ringRadius = shortest * 0.36;
      final ringPaint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = shortest * 0.024
        ..color = color.withOpacity(1.0);
      canvas.drawCircle(Offset(cx, cy), ringRadius, ringPaint);
    }

    // --- Upward triangle mark ---
    final topY = shortest * 0.28;
    final baseY = shortest * 0.74;
    final leftX = shortest * 0.32;
    final rightX = shortest * 0.68;

    final tri = Path()
      ..moveTo(cx, topY)
      ..lineTo(rightX, baseY)
      ..lineTo(leftX, baseY)
      ..close();

    final triPaint = Paint()
      ..style = PaintingStyle.fill
      ..color = color;
    canvas.drawPath(tri, triPaint);

    // --- Small rounded bar under triangle ---
    final barWidth = shortest * 0.44;
    final barHeight = shortest * 0.06;
    final barRect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(cx, shortest * 0.57),
        width: barWidth,
        height: barHeight,
      ),
      Radius.circular(barHeight * 0.48),
    );
    final barPaint = Paint()
      ..style = PaintingStyle.fill
      ..color = color;
    canvas.drawRRect(barRect, barPaint);
  }

  @override
  bool shouldRepaint(covariant _AnimicaPainter oldDelegate) {
    return oldDelegate.color != color ||
        oldDelegate.drawRing != drawRing ||
        oldDelegate.drawGlow != drawGlow;
  }
}
