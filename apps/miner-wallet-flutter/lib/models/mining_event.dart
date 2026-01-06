/// Mining event types and data structures
library;

enum MiningEventType {
  statusChange,
  hashrateUpdate,
  shareFound,
  blockFound,
  templateUpdate,
  error,
  log,
}

class MiningEvent {
  final MiningEventType type;
  final DateTime timestamp;
  final Map<String, dynamic> data;

  const MiningEvent({
    required this.type,
    required this.timestamp,
    required this.data,
  });

  factory MiningEvent.statusChange(String status) {
    return MiningEvent(
      type: MiningEventType.statusChange,
      timestamp: DateTime.now(),
      data: {'status': status},
    );
  }

  factory MiningEvent.hashrateUpdate(double hashrate) {
    return MiningEvent(
      type: MiningEventType.hashrateUpdate,
      timestamp: DateTime.now(),
      data: {'hashrate': hashrate},
    );
  }

  factory MiningEvent.shareFound(int height, String hash) {
    return MiningEvent(
      type: MiningEventType.shareFound,
      timestamp: DateTime.now(),
      data: {'height': height, 'hash': hash},
    );
  }

  factory MiningEvent.blockFound(int height, String hash) {
    return MiningEvent(
      type: MiningEventType.blockFound,
      timestamp: DateTime.now(),
      data: {'height': height, 'hash': hash},
    );
  }

  factory MiningEvent.templateUpdate(int height, int difficulty) {
    return MiningEvent(
      type: MiningEventType.templateUpdate,
      timestamp: DateTime.now(),
      data: {'height': height, 'difficulty': difficulty},
    );
  }

  factory MiningEvent.error(String message) {
    return MiningEvent(
      type: MiningEventType.error,
      timestamp: DateTime.now(),
      data: {'message': message},
    );
  }

  factory MiningEvent.log(String level, String message) {
    return MiningEvent(
      type: MiningEventType.log,
      timestamp: DateTime.now(),
      data: {'level': level, 'message': message},
    );
  }

  Map<String, dynamic> toJson() => {
        'type': type.name,
        'timestamp': timestamp.toIso8601String(),
        'data': data,
      };

  factory MiningEvent.fromJson(Map<String, dynamic> json) {
    return MiningEvent(
      type: MiningEventType.values.firstWhere(
        (e) => e.name == json['type'],
      ),
      timestamp: DateTime.parse(json['timestamp'] as String),
      data: json['data'] as Map<String, dynamic>,
    );
  }
}

/// Mining status
enum MiningStatus {
  stopped,
  starting,
  running,
  stopping,
  error,
}

extension MiningStatusExtension on MiningStatus {
  String get displayName {
    switch (this) {
      case MiningStatus.stopped:
        return 'Stopped';
      case MiningStatus.starting:
        return 'Starting...';
      case MiningStatus.running:
        return 'Running';
      case MiningStatus.stopping:
        return 'Stopping...';
      case MiningStatus.error:
        return 'Error';
    }
  }
}
