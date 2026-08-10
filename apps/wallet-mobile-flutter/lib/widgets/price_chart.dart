// A lightweight line chart for token price history — no external chart
// dependency (CSP-free, tiny). Renders a filled area sparkline with a subtle
// grid and a last-value marker; shows an honest empty state when there's no
// data yet (on-chain-only mode or a brand-new token).

import 'package:flutter/material.dart';

import '../services/token_index.dart';

class PriceChart extends StatelessWidget {
  final List<PricePoint> points;
  final double height;

  /// Formats a price value for the axis labels (e.g. ANM or USD).
  final String Function(double) label;

  const PriceChart({
    super.key,
    required this.points,
    required this.label,
    this.height = 180,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    if (points.length < 2) {
      return Container(
        height: height,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: cs.surfaceContainerHighest.withOpacity(0.35),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.show_chart, color: cs.outline, size: 28),
            const SizedBox(height: 6),
            Text('No price history yet',
                style: TextStyle(color: cs.outline, fontSize: 13)),
          ],
        ),
      );
    }

    final sorted = [...points]..sort((a, b) => a.t.compareTo(b.t));
    final vals = sorted.map((p) => p.priceAnm).toList();
    var lo = vals.reduce((a, b) => a < b ? a : b);
    var hi = vals.reduce((a, b) => a > b ? a : b);
    if (hi == lo) {
      hi += hi.abs() * 0.01 + 1e-9;
      lo -= lo.abs() * 0.01 + 1e-9;
    }
    final up = vals.last >= vals.first;
    final lineColor = up ? const Color(0xFF16A34A) : const Color(0xFFDC2626);

    return SizedBox(
      height: height,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(label(hi), style: TextStyle(color: cs.outline, fontSize: 11)),
              Text('${(((vals.last - vals.first) / vals.first) * 100).toStringAsFixed(2)}%',
                  style: TextStyle(color: lineColor, fontSize: 11, fontWeight: FontWeight.w600)),
            ],
          ),
          Expanded(
            child: CustomPaint(
              painter: _SparkPainter(vals, lo, hi, lineColor, cs.outlineVariant),
              size: Size.infinite,
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(label(lo), style: TextStyle(color: cs.outline, fontSize: 11)),
              Text('now', style: TextStyle(color: cs.outline, fontSize: 11)),
            ],
          ),
        ],
      ),
    );
  }
}

class _SparkPainter extends CustomPainter {
  final List<double> values;
  final double lo;
  final double hi;
  final Color line;
  final Color grid;
  _SparkPainter(this.values, this.lo, this.hi, this.line, this.grid);

  @override
  void paint(Canvas canvas, Size size) {
    final gridPaint = Paint()
      ..color = grid.withOpacity(0.4)
      ..strokeWidth = 0.5;
    for (var i = 0; i <= 3; i++) {
      final y = size.height * i / 3;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    Offset at(int i) {
      final x = values.length == 1 ? 0.0 : size.width * i / (values.length - 1);
      final norm = (values[i] - lo) / (hi - lo);
      final y = size.height * (1 - norm);
      return Offset(x, y);
    }

    final path = Path()..moveTo(at(0).dx, at(0).dy);
    for (var i = 1; i < values.length; i++) {
      path.lineTo(at(i).dx, at(i).dy);
    }

    final fill = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [line.withOpacity(0.22), line.withOpacity(0.0)],
        ).createShader(Offset.zero & size),
    );

    canvas.drawPath(
      path,
      Paint()
        ..color = line
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeJoin = StrokeJoin.round
        ..strokeCap = StrokeCap.round,
    );

    final last = at(values.length - 1);
    canvas.drawCircle(last, 3.5, Paint()..color = line);
    canvas.drawCircle(last, 3.5, Paint()
      ..color = Colors.white.withOpacity(0.9)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1);
  }

  @override
  bool shouldRepaint(covariant _SparkPainter old) =>
      old.values != values || old.lo != lo || old.hi != hi || old.line != line;
}
