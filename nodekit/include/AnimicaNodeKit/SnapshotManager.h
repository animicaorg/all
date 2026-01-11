#pragma once

#include <QObject>

namespace animica::nodekit {

class SnapshotManager : public QObject {
    Q_OBJECT

public:
    explicit SnapshotManager(QObject *parent = nullptr);

    void setDataDir(const QString &dataDir);
    bool downloadSnapshot(const QUrl &manifestUrl);
    bool verifySnapshot(const QString &hash);
    bool applySnapshot();

signals:
    void stallDetected();
    void recoveryAttempted(const QString &action);
    void recovered();
    void recoveryFailed(const QString &reason);

private:
    QString dataDir_{};
};

} // namespace animica::nodekit
