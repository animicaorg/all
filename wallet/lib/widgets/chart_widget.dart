/// Shared UI Widgets for Marketplace
/// 
/// Components:
/// - Loading overlays
/// - Empty states
/// - Chart widgets
/// - Custom form inputs

import 'package:flutter/material.dart';

/// Full-screen loading overlay
class LoadingOverlay extends StatelessWidget {
  final String message;
  final bool dismissible;

  const LoadingOverlay({
    Key? key,
    this.message = 'Loading...',
    this.dismissible = false,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return WillPopScope(
      onWillPop: () async => dismissible,
      child: Container(
        color: Colors.black.withOpacity(0.3),
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const CircularProgressIndicator(),
              const SizedBox(height: 24),
              Text(
                message,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Empty state widget
class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final String? buttonText;
  final VoidCallback? onButtonTap;

  const EmptyState({
    Key? key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.buttonText,
    this.onButtonTap,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 64, color: Colors.grey[300]),
            const SizedBox(height: 16),
            Text(
              title,
              style: Theme.of(context).textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            if (subtitle != null) ...[
              const SizedBox(height: 8),
              Text(
                subtitle!,
                style: TextStyle(
                  color: Colors.grey[600],
                  fontSize: 14,
                ),
                textAlign: TextAlign.center,
              ),
            ],
            if (buttonText != null && onButtonTap != null) ...[
              const SizedBox(height: 24),
              ElevatedButton(
                onPressed: onButtonTap,
                child: Text(buttonText!),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Currency input field
class CurrencyInputField extends StatefulWidget {
  final String label;
  final String currency;
  final double value;
  final Function(double) onChanged;
  final double? maxValue;
  final String? errorText;

  const CurrencyInputField({
    Key? key,
    required this.label,
    required this.currency,
    required this.value,
    required this.onChanged,
    this.maxValue,
    this.errorText,
  }) : super(key: key);

  @override
  State<CurrencyInputField> createState() => _CurrencyInputFieldState();
}

class _CurrencyInputFieldState extends State<CurrencyInputField> {
  late TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(
      text: widget.value > 0 ? widget.value.toString() : '',
    );
  }

  @override
  void didUpdateWidget(CurrencyInputField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.value != widget.value && widget.value > 0) {
      _controller.text = widget.value.toString();
    }
  }

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: _controller,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      decoration: InputDecoration(
        labelText: widget.label,
        prefixText: '${widget.currency} ',
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
        ),
        errorText: widget.errorText,
        helperText: widget.maxValue != null
            ? 'Max: ${widget.currency} ${widget.maxValue}'
            : null,
      ),
      onChanged: (value) {
        final parsed = double.tryParse(value) ?? 0;
        widget.onChanged(parsed);
      },
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }
}

/// Payment method selector
class PaymentMethodSelector extends StatelessWidget {
  final String selectedId;
  final List<PaymentMethodOption> options;
  final Function(String) onSelected;

  const PaymentMethodSelector({
    Key? key,
    required this.selectedId,
    required this.options,
    required this.onSelected,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: options.map((option) {
        final isSelected = selectedId == option.id;
        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Card(
            color: isSelected ? const Color(0xFF5EEAD4) : null,
            child: ListTile(
              onTap: () => onSelected(option.id),
              leading: option.icon,
              title: Text(
                option.name,
                style: TextStyle(
                  fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                  color: isSelected ? Colors.white : null,
                ),
              ),
              subtitle: Text(
                option.description,
                style: TextStyle(
                  color: isSelected ? Colors.white70 : Colors.grey,
                ),
              ),
              trailing: isSelected
                  ? const Icon(Icons.check, color: Colors.white)
                  : null,
            ),
          ),
        );
      }).toList(),
    );
  }
}

class PaymentMethodOption {
  final String id;
  final String name;
  final String description;
  final Icon icon;

  PaymentMethodOption({
    required this.id,
    required this.name,
    required this.description,
    required this.icon,
  });
}

/// Stats card row
class StatsRow extends StatelessWidget {
  final List<StatsCardModel> stats;

  const StatsRow({
    Key? key,
    required this.stats,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        for (int i = 0; i < stats.length; i++) ...[
          Expanded(child: StatsCard.fromModel(stats[i])),
          if (i < stats.length - 1) const SizedBox(width: 12),
        ],
      ],
    );
  }
}

class StatsCard extends StatelessWidget {
  final String label;
  final String value;
  final IconData? icon;
  final Color? color;

  const StatsCard({
    Key? key,
    required this.label,
    required this.value,
    this.icon,
    this.color,
  }) : super(key: key);

  factory StatsCard.fromModel(StatsCardModel model) {
    return StatsCard(
      label: model.label,
      value: model.value,
      icon: model.icon,
      color: model.color,
    );
  }

  @override
  Widget build(BuildContext context) {
    final bgColor = color ?? const Color(0xFF5EEAD4);

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: bgColor.withOpacity(0.1),
        border: Border.all(color: bgColor.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Text(
                  label,
                  style: const TextStyle(
                    fontSize: 12,
                    color: Colors.grey,
                    fontWeight: FontWeight.w500,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (icon != null) Icon(icon, size: 16, color: bgColor),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            value,
            style: TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.bold,
              color: bgColor,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}

class StatsCardModel {
  final String label;
  final String value;
  final IconData? icon;
  final Color? color;

  StatsCardModel({
    required this.label,
    required this.value,
    this.icon,
    this.color,
  });
}

/// Chart widget base
abstract class ChartWidget extends StatelessWidget {
  const ChartWidget({Key? key}) : super(key: key);
}

/// Simple bar chart
class BarChart extends StatelessWidget {
  final List<BarData> data;
  final double height;
  final Color barColor;

  const BarChart({
    Key? key,
    required this.data,
    this.height = 200,
    this.barColor = const Color(0xFF5EEAD4),
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    if (data.isEmpty) return const SizedBox();

    final maxValue = data.map((d) => d.value).reduce((a, b) => a > b ? a : b);

    return Container(
      height: height,
      padding: const EdgeInsets.all(16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: data.map((d) {
          final barHeight = (d.value / maxValue) * (height - 40);
          return Column(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              Tooltip(
                message: '\$${d.value.toStringAsFixed(2)}',
                child: Container(
                  width: 30,
                  height: barHeight,
                  decoration: BoxDecoration(
                    color: barColor,
                    borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                d.label,
                style: const TextStyle(fontSize: 10),
                textAlign: TextAlign.center,
              ),
            ],
          );
        }).toList(),
      ),
    );
  }
}

class BarData {
  final String label;
  final double value;

  BarData({required this.label, required this.value});
}

/// Info banner
class InfoBanner extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Color backgroundColor;
  final Color textColor;
  final IconData? icon;
  final VoidCallback? onDismiss;

  const InfoBanner({
    Key? key,
    required this.title,
    this.subtitle,
    this.backgroundColor = const Color(0xFF5EEAD4),
    this.textColor = const Color(0xFF0F766E),
    this.icon,
    this.onDismiss,
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: backgroundColor.withOpacity(0.1),
        border: Border.all(color: backgroundColor.withOpacity(0.3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          if (icon != null) ...[
            Icon(icon, color: backgroundColor),
            const SizedBox(width: 12),
          ],
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    color: textColor,
                  ),
                ),
                if (subtitle != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    subtitle!,
                    style: TextStyle(
                      fontSize: 12,
                      color: textColor.withOpacity(0.7),
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (onDismiss != null)
            IconButton(
              onPressed: onDismiss,
              icon: const Icon(Icons.close),
              constraints: const BoxConstraints(),
              padding: EdgeInsets.zero,
            ),
        ],
      ),
    );
  }
}
