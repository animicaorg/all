/// Device information models
library;

class DeviceInfo {
  final String id;
  final String name;
  final DeviceType type;
  final Map<String, dynamic> properties;

  const DeviceInfo({
    required this.id,
    required this.name,
    required this.type,
    this.properties = const {},
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'type': type.name,
        'properties': properties,
      };

  factory DeviceInfo.fromJson(Map<String, dynamic> json) {
    return DeviceInfo(
      id: json['id'] as String,
      name: json['name'] as String,
      type: DeviceType.values.firstWhere((e) => e.name == json['type']),
      properties: json['properties'] as Map<String, dynamic>? ?? {},
    );
  }
}

enum DeviceType {
  cpu,
  gpu,
  asic,
}

class CpuInfo extends DeviceInfo {
  final int coreCount;
  final int availableThreads;
  final bool supportsHugepages;

  CpuInfo({
    required super.id,
    required super.name,
    required this.coreCount,
    required this.availableThreads,
    required this.supportsHugepages,
  }) : super(
          type: DeviceType.cpu,
          properties: {
            'core_count': coreCount,
            'available_threads': availableThreads,
            'supports_hugepages': supportsHugepages,
          },
        );

  factory CpuInfo.fromJson(Map<String, dynamic> json) {
    final props = json['properties'] as Map<String, dynamic>;
    return CpuInfo(
      id: json['id'] as String,
      name: json['name'] as String,
      coreCount: props['core_count'] as int,
      availableThreads: props['available_threads'] as int,
      supportsHugepages: props['supports_hugepages'] as bool,
    );
  }
}

class GpuInfo extends DeviceInfo {
  final int computeUnits;
  final int memoryMB;
  final String driver;
  final bool isRecommended;

  GpuInfo({
    required super.id,
    required super.name,
    required this.computeUnits,
    required this.memoryMB,
    required this.driver,
    required this.isRecommended,
  }) : super(
          type: DeviceType.gpu,
          properties: {
            'compute_units': computeUnits,
            'memory_mb': memoryMB,
            'driver': driver,
            'is_recommended': isRecommended,
          },
        );

  factory GpuInfo.fromJson(Map<String, dynamic> json) {
    final props = json['properties'] as Map<String, dynamic>;
    return GpuInfo(
      id: json['id'] as String,
      name: json['name'] as String,
      computeUnits: props['compute_units'] as int,
      memoryMB: props['memory_mb'] as int,
      driver: props['driver'] as String,
      isRecommended: props['is_recommended'] as bool,
    );
  }
}
