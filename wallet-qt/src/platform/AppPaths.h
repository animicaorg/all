#ifndef APPPATHS_H
#define APPPATHS_H

#include <QString>

class AppPaths
{
public:
    static QString baseDir();
    static QString walletDir();
    static QString logsDir();
    static QString walletLogFile();
    static bool ensureDirectoriesExist();

private:
    AppPaths() = default;
    static bool ensureDir(const QString& path);
};

#endif // APPPATHS_H
