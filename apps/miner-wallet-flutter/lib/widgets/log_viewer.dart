/// Log viewer widget for displaying mining logs
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class LogViewer extends StatefulWidget {
  final List<String> logs;
  final VoidCallback? onClear;
  final bool autoScroll;

  const LogViewer({
    super.key,
    required this.logs,
    this.onClear,
    this.autoScroll = true,
  });

  @override
  State<LogViewer> createState() => _LogViewerState();
}

class _LogViewerState extends State<LogViewer> {
  final _scrollController = ScrollController();
  bool _userScrolled = false;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (_scrollController.hasClients) {
      final maxScroll = _scrollController.position.maxScrollExtent;
      final currentScroll = _scrollController.position.pixels;
      // Consider "at bottom" if within 50 pixels of max
      _userScrolled = (maxScroll - currentScroll) > 50;
    }
  }

  @override
  void didUpdateWidget(LogViewer oldWidget) {
    super.didUpdateWidget(oldWidget);
    
    // Auto-scroll to bottom when new logs arrive (if user hasn't scrolled up)
    if (widget.autoScroll && !_userScrolled && widget.logs.length > oldWidget.logs.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  void _copyLogs() {
    final allLogs = widget.logs.join('\n');
    Clipboard.setData(ClipboardData(text: allLogs));
    
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Logs copied to clipboard')),
    );
  }

  void _scrollToBottom() {
    if (_scrollController.hasClients) {
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOut,
      );
      setState(() {
        _userScrolled = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    
    return Column(
      children: [
        // Toolbar
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceVariant,
            border: Border(
              bottom: BorderSide(color: theme.dividerColor),
            ),
          ),
          child: Row(
            children: [
              Text(
                '${widget.logs.length} log entries',
                style: theme.textTheme.bodySmall,
              ),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.content_copy, size: 20),
                onPressed: widget.logs.isNotEmpty ? _copyLogs : null,
                tooltip: 'Copy logs',
              ),
              if (_userScrolled)
                IconButton(
                  icon: const Icon(Icons.arrow_downward, size: 20),
                  onPressed: _scrollToBottom,
                  tooltip: 'Scroll to bottom',
                ),
              if (widget.onClear != null)
                IconButton(
                  icon: const Icon(Icons.clear_all, size: 20),
                  onPressed: widget.logs.isNotEmpty ? widget.onClear : null,
                  tooltip: 'Clear logs',
                ),
            ],
          ),
        ),
        
        // Log content
        Expanded(
          child: widget.logs.isEmpty
              ? Center(
                  child: Text(
                    'No logs yet',
                    style: theme.textTheme.bodyLarge?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                )
              : Container(
                  color: theme.colorScheme.surface,
                  child: ListView.builder(
                    controller: _scrollController,
                    itemCount: widget.logs.length,
                    itemBuilder: (context, index) {
                      final log = widget.logs[index];
                      final isError = log.contains('[ERROR]');
                      
                      return Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 2,
                        ),
                        child: Text(
                          log,
                          style: theme.textTheme.bodySmall?.copyWith(
                            fontFamily: 'monospace',
                            color: isError 
                                ? theme.colorScheme.error 
                                : theme.colorScheme.onSurface,
                          ),
                        ),
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }
}
