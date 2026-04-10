#ifndef SENDWIDGET_H
#define SENDWIDGET_H

#include <QWidget>
#include <QComboBox>
#include <QLineEdit>
#include <QDoubleSpinBox>
#include <QLabel>
#include <QPushButton>
#include <QTextEdit>
#include <QFutureWatcher>
#include <QString>
#include "FeeEstimator.h"
#include "BalanceTracker.h"

class WalletEngine;
class AnimicaRpcClient;
class WalletDatabase;
class TransactionMonitor;

/**
 * @brief Widget for sending transactions.
 * 
 * Provides comprehensive UI for:
 * - Account selection with balance display
 * - Address validation with visual feedback
 * - Amount input with Max button
 * - Fee tier selection with real-time estimation
 * - Optional memo field
 * - Confirmation dialog before sending
 * 
 * Transaction lifecycle:
 * 1. Validate inputs (address, amount, balance)
 * 2. Get confirmation from user
 * 3. Build unsigned transaction
 * 4. Sign with WalletEngine
 * 5. Reserve balance in database (PENDING_OUT + FEE_RESERVED)
 * 6. Broadcast via RPC
 * 7. Track with TransactionMonitor
 */
class SendWidget : public QWidget
{
    Q_OBJECT
    
public:
    explicit SendWidget(
        WalletEngine* walletEngine,
        AnimicaRpcClient* rpcClient,
        WalletDatabase* database,
        TransactionMonitor* monitor,
        QWidget* parent = nullptr
    );
    ~SendWidget();
    
    /**
     * @brief Clear form and reset to initial state.
     */
    void clearForm();
    
    /**
     * @brief Set recipient address (e.g., from address book).
     * @param address Bech32m address
     */
    void setRecipientAddress(const QString& address);
    
    /**
     * @brief Set amount (e.g., from URI scheme).
     * @param amount Amount in ANM
     */
    void setAmount(double amount);
    
signals:
    /**
     * @brief Emitted when transaction is successfully sent.
     * @param txHash Transaction hash
     */
    void transactionSent(const QString& txHash);
    
    /**
     * @brief Emitted on error.
     * @param message Error message
     */
    void error(const QString& message);
    
private slots:
    void onSendClicked();
    void onMaxClicked();
    void onFeeTierChanged(int index);
    void onAddressChanged();
    void onAmountChanged();
    void onAccountChanged(int index);
    void onBalanceUpdated(const QString& address, const Balance& balance);
    void handleSendFinished();
    
private:
    void setupUI();
    void refreshAccounts();
    void updateFeeDisplay();
    void updateBalanceLabel();
    void updateRecipientCompleter();
    bool validateInputs();
    bool validateAddress(const QString& address);
    void showValidationError(const QString& field, const QString& message);
    void clearValidationErrors();
    void showError(const QString& title, const QString& message);
    void showSuccess(const QString& title, const QString& message);
    QString normalizedRecipientAddress() const;
    QString getCurrentAccountId() const;
    QString getCurrentAccountAddress() const;
    qint64 getAvailableBalance() const;
    FeeEstimator::FeeTier currentFeeTier() const;
    
    WalletEngine* m_walletEngine;
    AnimicaRpcClient* m_rpcClient;
    WalletDatabase* m_database;
    TransactionMonitor* m_monitor;
    FeeEstimator* m_feeEstimator;
    
    // UI components
    QComboBox* m_fromAccountCombo;
    QLineEdit* m_toAddressEdit;
    QDoubleSpinBox* m_amountSpinBox;
    QComboBox* m_feeTierCombo;
    QLabel* m_feeLabel;
    QLineEdit* m_memoEdit;
    QLineEdit* m_nonceEdit;
    QLineEdit* m_validAfterEdit;
    QLineEdit* m_validUntilEdit;
    QLineEdit* m_dataPayloadEdit;
    QPushButton* m_maxButton;
    QPushButton* m_sendButton;
    QLabel* m_balanceLabel;
    QLabel* m_addressValidationLabel;
    QLabel* m_amountValidationLabel;
    QLabel* m_feeWarningLabel;
    QLabel* m_statusLabel;
    QFutureWatcher<QJsonObject>* m_sendWatcher;
    
    qint64 m_chainId;
};

#endif // SENDWIDGET_H
