#include "AnimicaNodeKit/SnapshotManager.h"

namespace animica::nodekit {

SnapshotManager::SnapshotManager(QObject *parent) : QObject(parent) {}

void SnapshotManager::setDataDir(const QString &dataDir) {
    dataDir_ = dataDir;
}

bool SnapshotManager::downloadSnapshot(const QUrl &manifestUrl) {
    Q_UNUSED(manifestUrl)
    emit recoveryAttempted(QStringLiteral("snapshot download stub"));
    return false;
}

bool SnapshotManager::verifySnapshot(const QString &hash) {
    Q_UNUSED(hash)
    return false;
}

bool SnapshotManager::applySnapshot() {
    emit recoveryAttempted(QStringLiteral("snapshot apply stub"));
    return false;
}

} // namespace animica::nodekit
