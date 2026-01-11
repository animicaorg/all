#pragma once

#include <QObject>

#include "HealthMonitor.h"
#include "Keystore.h"
#include "NodeKitConfig.h"
#include "ProcessManager.h"
#include "RpcClient.h"
#include "SnapshotManager.h"

namespace animica::nodekit {

class NodeKit : public QObject {
    Q_OBJECT

public:
    explicit NodeKit(QObject *parent = nullptr);

    void configure(const NodeKitConfig &config);
    bool start();
    void stop();
    void restart();

    ProcessManager *processManager();
    RpcClient *rpcClient();
    HealthMonitor *healthMonitor();
    SnapshotManager *snapshotManager();
    Keystore *keystore();

signals:
    void started();
    void stopped();

private:
    NodeKitConfig config_{};
    ProcessManager processManager_{};
    RpcClient rpcClient_{};
    HealthMonitor healthMonitor_{};
    SnapshotManager snapshotManager_{};
    Keystore keystore_{};
};

} // namespace animica::nodekit
