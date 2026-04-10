#include "AppPaths.h"

#include <QDebug>
#include <QDir>
#include <QStandardPaths>

QString AppPaths::baseDir()
{
    const QString base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    ensureDir(base);
    return base;
}

QString AppPaths::walletDir()
{
    return baseDir();
}

QString AppPaths::logsDir()
{
    const QString path = baseDir() + QStringLiteral("/logs");
    ensureDir(path);
    return path;
}

QString AppPaths::walletLogFile()
{
    return logsDir() + QStringLiteral("/wallet.log");
}

bool AppPaths::ensureDirectoriesExist()
{
    bool success = true;
    success &= ensureDir(baseDir());
    success &= ensureDir(logsDir());
    return success;
}

bool AppPaths::ensureDir(const QString& path)
{
    QDir dir(path);
    if (dir.exists()) {
        return true;
    }

    if (dir.mkpath(QStringLiteral("."))) {
        qDebug() << "Created directory:" << path;
        return true;
    }

    qWarning() << "Failed to create directory:" << path;
    return false;
}
