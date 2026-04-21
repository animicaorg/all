#ifndef SETTINGSWIDGET_H
#define SETTINGSWIDGET_H

#include <QWidget>

class QLabel;
class QLineEdit;
class QPlainTextEdit;
class QPushButton;
class QCheckBox;
class QSpinBox;

class SettingsWidget : public QWidget
{
    Q_OBJECT

public:
    explicit SettingsWidget(const QString& walletFilePath, const QString& dataDir, QWidget* parent = nullptr);

signals:
    void settingsApplied(const QString& explorerUrl, int pollIntervalMs, int timeoutMs);

private slots:
    void onSaveClicked();
    void onDefaultsClicked();
    void onExportClicked();
    void onImportClicked();
    void onSetTransferPasswordClicked();
    void onClearTransferPasswordClicked();
    void updateEffectiveConfig();

private:
    void load();
    bool validate(QString& errorMessage) const;
    void refreshSecurityState();

    QString m_walletFilePath;
    QString m_dataDir;
    QLabel* m_networkValueLabel;
    QLabel* m_rpcUrlValueLabel;
    QLineEdit* m_explorerUrlEdit;
    QSpinBox* m_pollIntervalSpin;
    QSpinBox* m_timeoutSpin;
    QLabel* m_walletFileLabel;
    QLabel* m_dataDirLabel;
    QLabel* m_transferPasswordStatusLabel;
    QCheckBox* m_requireTransferPasswordCheck;
    QCheckBox* m_encryptWalletCheck;
    QPlainTextEdit* m_effectiveConfigEdit;
    QPushButton* m_saveButton;
    QPushButton* m_defaultsButton;
    QPushButton* m_exportButton;
    QPushButton* m_importButton;
    QPushButton* m_setTransferPasswordButton;
    QPushButton* m_clearTransferPasswordButton;
};

#endif // SETTINGSWIDGET_H
