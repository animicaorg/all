#include "AppPaths.h"
#include <QStandardPaths>
#include <QCoreApplication>
#include <QDebug>

QString AppPaths::baseDir()
{
    // Use QStandardPaths to get OS-appropriate app data directory
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    
    // QStandardPaths::AppDataLocation returns:
    // - macOS: ~/Library/Application Support/<APPNAME>
    // - Windows: C:/Users/<USER>/AppData/Roaming/<APPNAME>
    // - Linux: ~/.local/share/<APPNAME>
    //
    // We want the app name to be "AnimicaWallet"
    // Note: Application name should be set in main() via QCoreApplication::setApplicationName()
    
    ensureDir(base);
    return base;
}

QString AppPaths::nodeDir()
{
    QString path = baseDir() + "/node";
    ensureDir(path);
    return path;
}

QString AppPaths::nodeChainDir(int chainId)
{
    QString path = nodeDir() + QString("/chain-%1").arg(chainId);
    ensureDir(path);
    return path;
}

QString AppPaths::walletDir()
{
    QString path = baseDir() + "/wallet";
    ensureDir(path);
    return path;
}

QString AppPaths::logsDir()
{
    QString path = baseDir() + "/logs";
    ensureDir(path);
    return path;
}

QString AppPaths::runDir()
{
    QString path = baseDir() + "/run";
    ensureDir(path);
    return path;
}

QString AppPaths::nodeLogFile(const QString& network)
{
    return logsDir() + QString("/node-%1.log").arg(network);
}

QString AppPaths::walletLogFile()
{
    return logsDir() + "/wallet.log";
}

QString AppPaths::nodePidFile()
{
    return runDir() + "/node.pid";
}

QString AppPaths::nodeLockFile()
{
    return runDir() + "/node.lock";
}

QString AppPaths::nodeInfoFile()
{
    return runDir() + "/node.json";
}

QString AppPaths::getBundledNodePath()
{
#ifdef BUNDLED_NODE_PATH
    return QString(BUNDLED_NODE_PATH);
#else
    // Fallback: assume node is in ../node relative to executable
    QDir appDir(QCoreApplication::applicationDirPath());
    return appDir.filePath("node");
#endif
}

bool AppPaths::ensureDirectoriesExist()
{
    bool success = true;
    success &= ensureDir(baseDir());
    success &= ensureDir(walletDir());
    success &= ensureDir(logsDir());
#if !WALLET_REMOTE_RPC_ONLY
    success &= ensureDir(runDir());
    success &= ensureDir(nodeDir());
#endif
    return success;
}

bool AppPaths::ensureDir(const QString& path)
{
    QDir dir(path);
    if (dir.exists()) {
        return true;
    }
    
    if (dir.mkpath(".")) {
        qDebug() << "Created directory:" << path;
        return true;
    } else {
        qWarning() << "Failed to create directory:" << path;
        return false;
    }
}
