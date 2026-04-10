#include "DataDirManager.h"

#include "AppPaths.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>
#include <QTextStream>
#include <QDebug>

DataDirManager::DataDirManager(QObject* parent)
    : QObject(parent)
    , m_settings(new QSettings(this))
{
    QDir dir(getDataDir());
    if (!dir.exists()) {
        dir.mkpath(QStringLiteral("."));
    }
}

QString DataDirManager::getDataDir() const
{
    const QString envPath = qEnvironmentVariable(ENV_VAR_DATA_DIR);
    if (!envPath.isEmpty()) {
        return QFileInfo(envPath).absoluteFilePath();
    }

    const QString customPath = m_settings->value(SETTINGS_KEY_DATA_DIR).toString();
    if (!customPath.isEmpty()) {
        return QFileInfo(customPath).absoluteFilePath();
    }

    return getDefaultDataDir();
}

bool DataDirManager::setDataDir(const QString& path, bool validate)
{
    const QString absPath = QFileInfo(path).absoluteFilePath();
    if (validate) {
        QString errorMsg;
        if (!validateDataDir(absPath, errorMsg)) {
            qWarning() << "Data directory validation failed:" << errorMsg;
            return false;
        }
    }

    m_settings->setValue(SETTINGS_KEY_DATA_DIR, absPath);
    m_settings->sync();
    emit dataDirChanged(absPath);
    return true;
}

QString DataDirManager::getDefaultDataDir()
{
    return AppPaths::walletDir();
}

QString DataDirManager::getWalletsFilePath() const
{
    return QDir(getDataDir()).filePath(QStringLiteral("wallets.json"));
}

QString DataDirManager::getLogsDir() const
{
    return QDir(getDataDir()).filePath(QStringLiteral("logs"));
}

bool DataDirManager::validateDataDir(const QString& path, QString& errorMsg) const
{
    const QFileInfo info(path);
    if (!info.isAbsolute()) {
        errorMsg = QStringLiteral("Path must be absolute.");
        return false;
    }

    QDir dir(path);
    if (!dir.exists() && !dir.mkpath(QStringLiteral("."))) {
        errorMsg = QStringLiteral("Cannot create directory.");
        return false;
    }

    QFile testFile(dir.filePath(QStringLiteral(".write_test")));
    if (!testFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        errorMsg = QStringLiteral("Directory is not writable.");
        return false;
    }
    testFile.close();
    testFile.remove();

    return true;
}

bool DataDirManager::ensureDirectoriesExist()
{
    const QString baseDir = getDataDir();
    QDir dir(baseDir);
    bool success = true;

    if (!dir.exists()) {
        success &= dir.mkpath(QStringLiteral("."));
    }

    if (!QDir(getLogsDir()).exists()) {
        success &= dir.mkpath(QStringLiteral("logs"));
    }

    return success;
}

bool DataDirManager::isDataDirInitialized() const
{
    if (QFile::exists(getWalletsFilePath())) {
        return true;
    }

    const QStringList knownFiles{
        QStringLiteral("wallet.db"),
        QStringLiteral("address_book.json"),
    };
    const QDir dir(getDataDir());
    for (const QString& fileName : knownFiles) {
        if (dir.exists(fileName)) {
            return true;
        }
    }

    const QDir logsDir(getLogsDir());
    return logsDir.exists() && !logsDir.isEmpty();
}
