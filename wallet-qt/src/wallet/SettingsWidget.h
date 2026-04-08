#ifndef SETTINGSWIDGET_H
#define SETTINGSWIDGET_H

#include "../rpc/RpcSettings.h"

#include <QWidget>

class AnimicaRpcClient;
class QComboBox;
class QLineEdit;
class QPlainTextEdit;
class QSpinBox;
class QPushButton;
class QLabel;

class SettingsWidget : public QWidget
{
    Q_OBJECT

public:
    explicit SettingsWidget(const QString& walletFilePath, const QString& dataDir, QWidget* parent = nullptr);

signals:
    void rpcSettingsApplied(const RpcEndpointSettings& settings, const QString& explorerUrl, int pollIntervalMs, int timeoutMs);

private slots:
    void onSaveClicked();
    void onDefaultsClicked();
    void onExportClicked();
    void onImportClicked();
    void updateEffectiveConfig();

private:
    void load();
    bool validate(QString& errorMessage) const;

    QString m_walletFilePath;
    QString m_dataDir;
    QComboBox* m_networkCombo;
    QLineEdit* m_rpcUrlEdit;
    QSpinBox* m_chainIdSpin;
    QLineEdit* m_explorerUrlEdit;
    QPlainTextEdit* m_fallbackRpcEdit;
    QSpinBox* m_pollIntervalSpin;
    QSpinBox* m_timeoutSpin;
    QComboBox* m_logLevelCombo;
    QLabel* m_walletFileLabel;
    QLabel* m_dataDirLabel;
    QPlainTextEdit* m_effectiveConfigEdit;
    QPushButton* m_saveButton;
    QPushButton* m_defaultsButton;
    QPushButton* m_exportButton;
    QPushButton* m_importButton;
};

#endif // SETTINGSWIDGET_H
