#ifndef RPCSETTINGSDIALOG_H
#define RPCSETTINGSDIALOG_H

#include <QDialog>
#include "../rpc/RpcSettings.h"

class AnimicaRpcClient;
class RpcReply;
class QComboBox;
class QLineEdit;
class QSpinBox;
class QLabel;
class QPushButton;

class RpcSettingsDialog : public QDialog
{
    Q_OBJECT

public:
    explicit RpcSettingsDialog(QWidget* parent = nullptr);

signals:
    void settingsSaved(const RpcEndpointSettings& settings);

private slots:
    void onTestConnection();
    void onSave();
    void onResetDefaults();
    void updateUrlPreview();

private:
    void loadFromSettings();
    RpcEndpointSettings gatherSettings() const;
    void updateSecurityWarning();
    void setStatusMessage(const QString& message, const QString& color);

    RpcSettings m_settingsStore;
    AnimicaRpcClient* m_testClient;
    RpcReply* m_testReply;

    QComboBox* m_schemeCombo;
    QLineEdit* m_hostEdit;
    QSpinBox* m_portSpin;
    QLineEdit* m_pathEdit;
    QLineEdit* m_usernameEdit;
    QLineEdit* m_passwordEdit;
    QLabel* m_urlPreviewLabel;
    QLabel* m_securityWarningLabel;
    QLabel* m_statusLabel;
    QPushButton* m_testButton;
    QPushButton* m_saveButton;
    QPushButton* m_resetButton;
};

#endif // RPCSETTINGSDIALOG_H
