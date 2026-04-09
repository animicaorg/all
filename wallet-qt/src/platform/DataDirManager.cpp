#include "DataDirManager.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>
#include <QDebug>
#include <QTextStream>
#include <QCoreApplication>

DataDirManager::DataDirManager(QObject* parent)
    : QObject(parent)
    , m_settings(new QSettings(this))
{
    // Ensure default data directory exists
    QString dataDir = getDataDir();
    QDir dir(dataDir);
    if (!dir.exists()) {
        dir.mkpath(".");
    }
}

QString DataDirManager::getDataDir() const
{
    // Priority 1: Environment variable
    QString envPath = qEnvironmentVariable(ENV_VAR_DATA_DIR);
    if (!envPath.isEmpty()) {
        QFileInfo info(envPath);
        return info.absoluteFilePath();
    }
    
    // Priority 2: User-chosen path from settings
    QString customPath = m_settings->value(SETTINGS_KEY_DATA_DIR).toString();
    if (!customPath.isEmpty()) {
        QFileInfo info(customPath);
        return info.absoluteFilePath();
    }
    
    // Priority 3: OS-specific default
    return getDefaultDataDir();
}

bool DataDirManager::setDataDir(const QString& path, bool validate)
{
    QString absPath = QFileInfo(path).absoluteFilePath();
    
    if (validate) {
        QString errorMsg;
        if (!validateDataDir(absPath, errorMsg)) {
            qWarning() << "Data directory validation failed:" << errorMsg;
            return false;
        }
    }
    
    // Store in settings
    m_settings->setValue(SETTINGS_KEY_DATA_DIR, absPath);
    m_settings->sync();
    
    qDebug() << "Data directory set to:" << absPath;
    emit dataDirChanged(absPath);
    
    return true;
}

QString DataDirManager::getDefaultDataDir()
{
#if defined(Q_OS_MACOS)
    // macOS: ~/Library/Application Support/Animica/
    QString baseDir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    // AppDataLocation includes organization and app name, so we need to adjust
    return QDir::home().filePath("Library/Application Support/Animica");
    
#elif defined(Q_OS_WIN)
    // Windows: %APPDATA%/Animica
    const QString appDataDir = qEnvironmentVariable("APPDATA");
    if (!appDataDir.isEmpty()) {
        return QDir(appDataDir).filePath("Animica");
    }
    return QDir::home().filePath("AppData/Roaming/Animica");
    
#else
    // Linux: ~/.animica/ (backward compatible with existing node behavior)
    return QDir::home().filePath(".animica");
#endif
}

QString DataDirManager::getWalletsFilePath() const
{
    return QDir(getDataDir()).filePath("wallets.json");
}

QString DataDirManager::getChainDataDir(int chainId) const
{
    return QDir(getDataDir()).filePath(QString("chain-%1").arg(chainId));
}

QString DataDirManager::getLogsDir() const
{
    return QDir(getDataDir()).filePath("logs");
}

QString DataDirManager::getSnapshotsDir() const
{
    return QDir(getDataDir()).filePath("snapshots");
}

bool DataDirManager::validateDataDir(const QString& path, QString& errorMsg) const
{
    QFileInfo info(path);
    
    // Check if path is absolute
    if (!info.isAbsolute()) {
        errorMsg = "Path must be absolute";
        return false;
    }
    
    // Check if directory exists or can be created
    QDir dir(path);
    if (!dir.exists()) {
        if (!dir.mkpath(".")) {
            errorMsg = "Cannot create directory";
            return false;
        }
    }
    
    // Check if directory is writable
    QFile testFile(dir.filePath(".write_test"));
    if (!testFile.open(QIODevice::WriteOnly)) {
        errorMsg = "Directory is not writable";
        return false;
    }
    testFile.close();
    testFile.remove();
    
    return true;
}

bool DataDirManager::ensureDirectoriesExist()
{
    QString baseDir = getDataDir();
    QDir dir(baseDir);
    
    bool success = true;
    
    // Create base directory
    if (!dir.exists()) {
        success &= dir.mkpath(".");
    }
    
    #if !WALLET_REMOTE_RPC_ONLY
    // Create chain directories for common networks
    QStringList chainDirs = {"chain-1", "chain-2", "chain-1337"};
    for (const QString& chainDir : chainDirs) {
        QString fullPath = dir.filePath(chainDir);
        if (!QDir(fullPath).exists()) {
            success &= dir.mkpath(chainDir);
        }
    }
    #endif
    
    // Create logs directory
    QString logsPath = getLogsDir();
    if (!QDir(logsPath).exists()) {
        success &= dir.mkpath("logs");
    }
    
    #if !WALLET_REMOTE_RPC_ONLY
    // Create snapshots directory
    QString snapshotsPath = getSnapshotsDir();
    if (!QDir(snapshotsPath).exists()) {
        success &= dir.mkpath("snapshots");
    }
    #endif
    
    if (success) {
        qDebug() << "All required directories exist in:" << baseDir;
    } else {
        qWarning() << "Failed to create some directories in:" << baseDir;
    }
    
    return success;
}

bool DataDirManager::isDataDirInitialized() const
{
    QString dataDir = getDataDir();
    QDir dir(dataDir);
    
    // Check for wallets.json
    if (QFile::exists(getWalletsFilePath())) {
        return true;
    }
    
    // Check for any chain directories
    QStringList chainDirs = dir.entryList(QStringList() << "chain-*", QDir::Dirs);
    if (!chainDirs.isEmpty()) {
        return true;
    }
    
    // Check for logs directory with content
    QDir logsDir(getLogsDir());
    if (logsDir.exists() && !logsDir.isEmpty()) {
        return true;
    }
    
    return false;
}

QString DataDirManager::getStoredNetworkId() const
{
    QString markerPath = QDir(getDataDir()).filePath(NETWORK_MARKER_FILE);
    QFile file(markerPath);
    
    if (!file.exists()) {
        return QString();
    }
    
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        qWarning() << "Failed to read network marker file:" << markerPath;
        return QString();
    }
    
    QString networkId = QString::fromUtf8(file.readAll()).trimmed();
    file.close();
    
    return networkId;
}

bool DataDirManager::setStoredNetworkId(const QString& networkId)
{
    QString markerPath = QDir(getDataDir()).filePath(NETWORK_MARKER_FILE);
    QFile file(markerPath);
    
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text | QIODevice::Truncate)) {
        qWarning() << "Failed to write network marker file:" << markerPath;
        return false;
    }
    
    QTextStream out(&file);
    out << networkId;
    file.flush();
    
#ifndef Q_OS_WIN
    // Set restrictive permissions on Unix-like systems
    file.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
#endif
    
    file.close();
    
    qDebug() << "Network ID marker set to:" << networkId << "in" << markerPath;
    return true;
}

bool DataDirManager::checkNetworkCompatibility(const QString& requestedNetwork, QString& errorMsg) const
{
    QString storedNetwork = getStoredNetworkId();
    
    // If no network is stored yet, any network is compatible (first run)
    if (storedNetwork.isEmpty()) {
        return true;
    }
    
    // Check if networks match
    if (storedNetwork != requestedNetwork) {
        errorMsg = QString(
            "Network mismatch detected!\n\n"
            "Data directory is configured for: %1\n"
            "You are trying to start: %2\n\n"
            "Using the wrong network could corrupt your chain data.\n"
            "Please choose a different data directory or switch to %1."
        ).arg(storedNetwork, requestedNetwork);
        return false;
    }
    
    return true;
}
