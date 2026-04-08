#include "AppPaths.h"
#include <QStandardPaths>
#include <QCoreApplication>
#include <QFileInfo>
#include <QDebug>

namespace {
#if defined(Q_OS_UNIX) && !defined(Q_OS_MACOS)
void appendLinuxInstallNodeCandidates(QStringList& candidates, const QDir& libDir)
{
    const QString preferred = libDir.filePath("x86_64-linux-gnu/animica-wallet/node");
    if (!candidates.contains(preferred)) {
        candidates << preferred;
    }

    const QFileInfoList multiarchEntries = libDir.entryInfoList(
        QStringList() << "*-linux-gnu",
        QDir::Dirs | QDir::NoDotAndDotDot,
        QDir::Name
    );
    for (const QFileInfo& entry : multiarchEntries) {
        const QString candidate = QDir(entry.absoluteFilePath()).filePath("animica-wallet/node");
        if (!candidates.contains(candidate)) {
            candidates << candidate;
        }
    }

    const QString legacy = libDir.filePath("animica-wallet/node");
    if (!candidates.contains(legacy)) {
        candidates << legacy;
    }
}
#endif

QStringList bundledSitePackagesCandidates(const QString& nodeDirPath)
{
    if (nodeDirPath.isEmpty()) {
        return {};
    }

#ifdef Q_OS_WIN
    const QString candidate = QDir(nodeDirPath).filePath("venv/Lib/site-packages");
    const QFileInfo info(candidate);
    if (info.exists() && info.isDir()) {
        return { info.absoluteFilePath() };
    }
    return {};
#else
    const QDir libDir(QDir(nodeDirPath).filePath("venv/lib"));
    if (!libDir.exists()) {
        return {};
    }

    QStringList candidates;
    const QFileInfoList pythonEntries = libDir.entryInfoList(
        QStringList() << "python*",
        QDir::Dirs | QDir::NoDotAndDotDot,
        QDir::Name
    );
    for (const QFileInfo& entry : pythonEntries) {
        const QString candidate = QDir(entry.absoluteFilePath()).filePath("site-packages");
        const QFileInfo info(candidate);
        if (info.exists() && info.isDir()) {
            candidates << info.absoluteFilePath();
        }
    }
    return candidates;
#endif
}

QString firstExistingFile(const QStringList& candidates)
{
    for (const QString& candidate : candidates) {
        const QFileInfo info(candidate);
        if (info.exists() && info.isFile()) {
            return info.absoluteFilePath();
        }
    }
    return QString();
}

QString genesisFileNameForNetwork(const QString& network)
{
    const QString normalized = network.trimmed().toLower();
    if (normalized == QStringLiteral("testnet")) {
        return QStringLiteral("testnet.json");
    }
    if (normalized == QStringLiteral("devnet")) {
        return QStringLiteral("devnet.json");
    }
    return QStringLiteral("mainnet.json");
}
}

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
    QDir appDir(QCoreApplication::applicationDirPath());
    QStringList candidates;

    const QString override = qEnvironmentVariable("ANIMICA_WALLET_NODE_DIR");
    if (!override.isEmpty()) {
        candidates << override;
    }

#ifdef Q_OS_MACOS
    candidates << appDir.filePath("../Resources/node");
#elif defined(Q_OS_WIN)
    candidates << appDir.filePath("node");
#else
    candidates << appDir.filePath("node");
    appendLinuxInstallNodeCandidates(candidates, QDir(appDir.filePath("../lib")));
    candidates << appDir.filePath("../lib/node");
    appendLinuxInstallNodeCandidates(candidates, QDir(QStringLiteral("/usr/lib")));
#endif

#ifdef BUNDLED_NODE_PATH
    candidates << QStringLiteral(BUNDLED_NODE_PATH);
#endif

    for (const QString& candidate : candidates) {
        const QFileInfo info(candidate);
        if (info.exists() && info.isDir()) {
            return info.absoluteFilePath();
        }
    }

    return appDir.filePath("node");
}

QString AppPaths::bundledPythonPath()
{
    const QString nodeDir = getBundledNodePath();
    if (nodeDir.isEmpty()) {
        return QString();
    }

#ifdef Q_OS_WIN
    const QString candidate = QDir(nodeDir).filePath("venv/Scripts/python.exe");
#else
    const QString candidate = QDir(nodeDir).filePath("venv/bin/python");
#endif

    const QFileInfo info(candidate);
    if (info.exists() && info.isExecutable()) {
        return info.absoluteFilePath();
    }
    return QString();
}

QString AppPaths::bundledAssetsDir()
{
    const QString candidate = QDir(getBundledNodePath()).filePath("assets");
    const QFileInfo info(candidate);
    if (info.exists() && info.isDir()) {
        return info.absoluteFilePath();
    }
    return QString();
}

QString AppPaths::bundledParamsPath()
{
    const QString nodeDir = getBundledNodePath();
    const QStringList sitePackages = bundledSitePackagesCandidates(nodeDir);
    for (const QString& sitePackagesDir : sitePackages) {
        const QString canonical = firstExistingFile({
            QDir(sitePackagesDir).filePath("spec/params.yaml"),
            QDir(sitePackagesDir).filePath("core/genesis/spec/params.yaml"),
        });
        if (!canonical.isEmpty()) {
            return canonical;
        }
    }

    const QString assetsDir = bundledAssetsDir();
    if (assetsDir.isEmpty()) {
        return QString();
    }
    return firstExistingFile({ QDir(assetsDir).filePath("spec/params.yaml") });
}

QString AppPaths::bundledGenesisPath(const QString& network)
{
    const QString fileName = genesisFileNameForNetwork(network);
    const QString nodeDir = getBundledNodePath();
    const QStringList sitePackages = bundledSitePackagesCandidates(nodeDir);
    for (const QString& sitePackagesDir : sitePackages) {
        const QString canonical = firstExistingFile({
            QDir(sitePackagesDir).filePath(QStringLiteral("core/genesis/%1").arg(fileName)),
        });
        if (!canonical.isEmpty()) {
            return canonical;
        }
    }

    const QString assetsDir = bundledAssetsDir();
    if (assetsDir.isEmpty()) {
        return QString();
    }
    return firstExistingFile({
        QDir(assetsDir).filePath(QStringLiteral("genesis/%1").arg(fileName)),
    });
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
