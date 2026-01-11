#include "AppBackend.h"

#include <QDir>
#include <QStandardPaths>

using namespace animica::nodekit;

AppBackend::AppBackend(QObject *parent) : QObject(parent) {
    NodeKitConfig config;
    config.appId = QStringLiteral("wallet");
    config.chainId = QStringLiteral("main");
    const QString base = QStandardPaths::writableLocation(QStandardPaths::HomeLocation);
    config.dataDir = QDir(base).filePath(QStringLiteral(".animica/apps/wallet/chain-main"));
    QDir().mkpath(config.dataDir);
    config.rpcPort = 18400;
    nodekit_.configure(config);

    connect(nodekit_.rpcClient(), &RpcClient::rpcResponse, this, &AppBackend::handleRpcResponse);
    connect(nodekit_.processManager(), &ProcessManager::logUpdated, this, [this](const QString &line) {
        logs_.append(line);
        if (logs_.size() > 200) {
            logs_.removeFirst();
        }
        emit logsChanged();
    });

    connect(nodekit_.healthMonitor(), &HealthMonitor::healthOk, this, [this]() {
        nodeHealthy_ = true;
        emit nodeHealthyChanged();
    });
    connect(nodekit_.healthMonitor(), &HealthMonitor::healthDegraded, this, [this](const QString &) {
        nodeHealthy_ = false;
        emit nodeHealthyChanged();
    });

    nodekit_.start();

    refreshTimer_.setInterval(4000);
    connect(&refreshTimer_, &QTimer::timeout, this, &AppBackend::refreshStatus);
    refreshTimer_.start();
}

qint64 AppBackend::headHeight() const {
    return headHeight_;
}

qint64 AppBackend::safeHeadHeight() const {
    return safeHeadHeight_;
}

int AppBackend::peers() const {
    return peers_;
}

QString AppBackend::syncPhase() const {
    return syncPhase_;
}

bool AppBackend::nodeHealthy() const {
    return nodeHealthy_;
}

QStringList AppBackend::logs() const {
    return logs_;
}

bool AppBackend::unlockKeystore(const QString &passphrase) {
    return nodekit_.keystore()->unlock(passphrase);
}

bool AppBackend::createWallet(const QString &label) {
    return nodekit_.keystore()->createWallet(label);
}

void AppBackend::handleRpcResponse(const QString &method, const QJsonObject &result) {
    if (method == QStringLiteral("chain.getHead")) {
        headHeight_ = result.value("height").toVariant().toLongLong();
        emit headHeightChanged();
    } else if (method == QStringLiteral("chain.getSafeHead")) {
        safeHeadHeight_ = result.value("height").toVariant().toLongLong();
        emit safeHeadHeightChanged();
    } else if (method == QStringLiteral("peer.list")) {
        peers_ = result.value("peers").toArray().size();
        emit peersChanged();
    } else if (method == QStringLiteral("sync.status")) {
        syncPhase_ = result.value("phase").toString();
        emit syncPhaseChanged();
    }
}

void AppBackend::refreshStatus() {
    nodekit_.rpcClient()->chainGetHead();
    nodekit_.rpcClient()->chainGetSafeHead();
    nodekit_.rpcClient()->peerList();
    nodekit_.rpcClient()->syncStatus();
}
