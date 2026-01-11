#pragma once

#include <QObject>
#include <QStringList>

#include "AnimicaNodeKit/NodeKit.h"

class AppBackend : public QObject {
    Q_OBJECT
    Q_PROPERTY(qint64 headHeight READ headHeight NOTIFY headHeightChanged)
    Q_PROPERTY(qint64 safeHeadHeight READ safeHeadHeight NOTIFY safeHeadHeightChanged)
    Q_PROPERTY(int peers READ peers NOTIFY peersChanged)
    Q_PROPERTY(QString syncPhase READ syncPhase NOTIFY syncPhaseChanged)
    Q_PROPERTY(bool nodeHealthy READ nodeHealthy NOTIFY nodeHealthyChanged)
    Q_PROPERTY(QStringList logs READ logs NOTIFY logsChanged)

public:
    explicit AppBackend(QObject *parent = nullptr);

    qint64 headHeight() const;
    qint64 safeHeadHeight() const;
    int peers() const;
    QString syncPhase() const;
    bool nodeHealthy() const;
    QStringList logs() const;

    Q_INVOKABLE bool unlockKeystore(const QString &passphrase);
    Q_INVOKABLE bool createWallet(const QString &label);

signals:
    void headHeightChanged();
    void safeHeadHeightChanged();
    void peersChanged();
    void syncPhaseChanged();
    void nodeHealthyChanged();
    void logsChanged();

private slots:
    void handleRpcResponse(const QString &method, const QJsonObject &result);

private:
    void refreshStatus();

    animica::nodekit::NodeKit nodekit_{};
    qint64 headHeight_ = -1;
    qint64 safeHeadHeight_ = -1;
    int peers_ = 0;
    QString syncPhase_ = QStringLiteral("unknown");
    bool nodeHealthy_ = false;
    QStringList logs_{};
    QTimer refreshTimer_{};
};
