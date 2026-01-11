#pragma once

#include <QObject>
#include <QTimer>

#include "ProcessManager.h"
#include "RpcClient.h"

namespace animica::nodekit {

class HealthMonitor : public QObject {
    Q_OBJECT

public:
    explicit HealthMonitor(QObject *parent = nullptr);

    void setRpcClient(RpcClient *client);
    void setProcessManager(ProcessManager *manager);
    void start();
    void stop();

signals:
    void healthOk();
    void healthDegraded(const QString &reason);
    void restartSuggested(const QString &reason);

private slots:
    void checkHealth();
    void handleRpcResponse(const QString &method, const QJsonObject &result);

private:
    RpcClient *rpcClient_ = nullptr;
    ProcessManager *processManager_ = nullptr;
    QTimer timer_{};
    int consecutiveFailures_ = 0;
    qint64 lastHeadHeight_ = -1;
    int maxFailuresBeforeRestart_ = 3;
};

} // namespace animica::nodekit
