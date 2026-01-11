#pragma once

#include <QString>

namespace animica::nodekit {

struct NodeKitConfig {
    QString appId;
    QString chainId;
    QString dataDir;
    quint16 rpcPort = 0;
    quint16 wsPort = 0;
    quint16 metricsPort = 0;
    bool enableWebSocket = false;
    bool enableMetrics = false;
    bool useDockerMode = false;
};

} // namespace animica::nodekit
