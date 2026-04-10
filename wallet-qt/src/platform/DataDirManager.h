#ifndef DATADIRMANAGER_H
#define DATADIRMANAGER_H

#include <QObject>
#include <QSettings>
#include <QString>

class DataDirManager : public QObject
{
    Q_OBJECT

public:
    explicit DataDirManager(QObject* parent = nullptr);

    QString getDataDir() const;
    bool setDataDir(const QString& path, bool validate = true);
    static QString getDefaultDataDir();

    QString getWalletsFilePath() const;
    QString getLogsDir() const;

    bool validateDataDir(const QString& path, QString& errorMsg) const;
    bool ensureDirectoriesExist();
    bool isDataDirInitialized() const;

signals:
    void dataDirChanged(const QString& newPath);

private:
    QSettings* m_settings;

    static constexpr const char* SETTINGS_KEY_DATA_DIR = "dataDir/customPath";
    static constexpr const char* ENV_VAR_DATA_DIR = "ANIMICA_WALLET_DATA_DIR";
};

#endif // DATADIRMANAGER_H
