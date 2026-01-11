#pragma once

#include <QObject>
#include <QDateTime>
#include <QProcess>
#include <QStringList>

#include "NodeKitConfig.h"

namespace animica::nodekit {

struct NodeStatus {
    qint64 pid = -1;
    QDateTime startedAt;
    QDateTime lastHealthCheck;
    QString headHash;
    qint64 headHeight = -1;
    QDateTime headTime;
    int peers = 0;
    QString syncPhase;
    quint16 rpcPort = 0;
    quint16 wsPort = 0;
    quint16 metricsPort = 0;
};

class ProcessManager : public QObject {
    Q_OBJECT

public:
    explicit ProcessManager(QObject *parent = nullptr);

    void configure(const NodeKitConfig &config);
    bool start();
    void stop();
    void restart();
    QStringList tailLogs(int nLines) const;
    NodeStatus status() const;
    bool isRunning() const;

signals:
    void logUpdated(const QString &line);
    void nodeStarted();
    void nodeStopped();
    void nodeCrashed(const QString &reason);

private slots:
    void handleReadyRead();
    void handleProcessFinished(int exitCode, QProcess::ExitStatus status);

private:
    QStringList buildNodeArgs() const;
    QString resolveNodeBinary() const;
    QString buildComposePath() const;
    QStringList buildDockerArgs(const QString &composePath) const;
    void appendLogLine(const QString &line);

    NodeKitConfig config_{};
    QProcess process_{};
    QStringList logBuffer_{};
    NodeStatus status_{};
    int maxLogLines_ = 500;
    QString composePath_{};
};

} // namespace animica::nodekit
