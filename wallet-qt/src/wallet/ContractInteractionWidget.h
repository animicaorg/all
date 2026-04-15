#ifndef CONTRACTINTERACTIONWIDGET_H
#define CONTRACTINTERACTIONWIDGET_H

#include <QFutureWatcher>
#include <QWidget>

class WalletEngine;
class QCheckBox;
class QComboBox;
class QLineEdit;
class QPlainTextEdit;
class QPushButton;

class ContractInteractionWidget : public QWidget
{
    Q_OBJECT

public:
    explicit ContractInteractionWidget(WalletEngine* engine, QWidget* parent = nullptr);
    ~ContractInteractionWidget() override;

private slots:
    void refreshWallets();
    void updateMethodList();
    void updatePreview();
    void onReadClicked();
    void onWriteClicked();
    void handleReadFinished();
    void handleWriteFinished();

private:
    void rememberContract(const QString& address);

    WalletEngine* m_engine;
    QComboBox* m_recentContractsCombo;
    QLineEdit* m_contractAddressEdit;
    QPlainTextEdit* m_abiEdit;
    QComboBox* m_methodCombo;
    QPlainTextEdit* m_argsEdit;
    QCheckBox* m_rawModeCheck;
    QLineEdit* m_rawPayloadEdit;
    QComboBox* m_walletCombo;
    QLineEdit* m_chainIdEdit;
    QLineEdit* m_maxFeeEdit;
    QPlainTextEdit* m_resultEdit;
    QPushButton* m_readButton;
    QPushButton* m_writeButton;
    QFutureWatcher<QJsonObject>* m_readWatcher;
    QFutureWatcher<QJsonObject>* m_writeWatcher;
};

#endif // CONTRACTINTERACTIONWIDGET_H
