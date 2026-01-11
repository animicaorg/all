#include "AnimicaNodeKit/HealthMonitor.h"

#include <QJsonObject>

namespace animica::nodekit {

HealthMonitor::HealthMonitor(QObject *parent) : QObject(parent) {
    timer_.setInterval(5000);
    connect(&timer_, &QTimer::timeout, this, &HealthMonitor::checkHealth);
}

void HealthMonitor::setRpcClient(RpcClient *client) {
    rpcClient_ = client;
    if (rpcClient_) {
        connect(rpcClient_, &RpcClient::rpcResponse, this, &HealthMonitor::handleRpcResponse);
    }
}

void HealthMonitor::setProcessManager(ProcessManager *manager) {
    processManager_ = manager;
}

void HealthMonitor::start() {
    timer_.start();
}

void HealthMonitor::stop() {
    timer_.stop();
}

void HealthMonitor::checkHealth() {
    if (!rpcClient_ || !processManager_) {
        emit healthDegraded(QStringLiteral("NodeKit not configured"));
        return;
    }

    if (!rpcClient_->chainGetHead()) {
        consecutiveFailures_++;
        emit healthDegraded(QStringLiteral("RPC unavailable"));
        return;
    }
}

void HealthMonitor::handleRpcResponse(const QString &method, const QJsonObject &result) {
    if (method != QStringLiteral("chain.getHead")) {
        return;
    }

    const qint64 height = result.value("height").toVariant().toLongLong();
    if (height <= lastHeadHeight_) {
        consecutiveFailures_++;
        emit healthDegraded(QStringLiteral("Head not advancing"));
    } else {
        consecutiveFailures_ = 0;
        lastHeadHeight_ = height;
        emit healthOk();
    }

    if (consecutiveFailures_ >= maxFailuresBeforeRestart_) {
        emit restartSuggested(QStringLiteral("Repeated health failures"));
    }
}

} // namespace animica::nodekit
