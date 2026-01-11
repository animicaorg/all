#include "AnimicaNodeKit/NodeKit.h"

#include <QDir>

namespace animica::nodekit {

NodeKit::NodeKit(QObject *parent) : QObject(parent) {
    healthMonitor_.setRpcClient(&rpcClient_);
    healthMonitor_.setProcessManager(&processManager_);
}

void NodeKit::configure(const NodeKitConfig &config) {
    config_ = config;
    processManager_.configure(config_);
    rpcClient_.setEndpoint(QUrl(QStringLiteral("http://127.0.0.1:%1").arg(config_.rpcPort)));
    snapshotManager_.setDataDir(config_.dataDir);
    const QString keystorePath = QDir(config_.dataDir).filePath("keystore.json.enc");
    keystore_.setStoragePath(keystorePath);
}

bool NodeKit::start() {
    if (!processManager_.start()) {
        return false;
    }
    healthMonitor_.start();
    emit started();
    return true;
}

void NodeKit::stop() {
    healthMonitor_.stop();
    processManager_.stop();
    emit stopped();
}

void NodeKit::restart() {
    processManager_.restart();
}

ProcessManager *NodeKit::processManager() {
    return &processManager_;
}

RpcClient *NodeKit::rpcClient() {
    return &rpcClient_;
}

HealthMonitor *NodeKit::healthMonitor() {
    return &healthMonitor_;
}

SnapshotManager *NodeKit::snapshotManager() {
    return &snapshotManager_;
}

Keystore *NodeKit::keystore() {
    return &keystore_;
}

} // namespace animica::nodekit
