#ifndef RECEIVEWIDGET_H
#define RECEIVEWIDGET_H

#include <QWidget>
#include <QComboBox>
#include <QFutureWatcher>
#include <QLabel>
#include <QPushButton>
#include <QLineEdit>
#include <QShowEvent>
#include <QString>
#include <QTimer>

#include "ReceiveQrService.h"

#include <memory>

class WalletEngine;
struct Balance;

/**
 * @brief Widget for receiving funds.
 * 
 * Displays:
 * - Account selector with balance
 * - Current account address with copy button
 * - QR code rendered from the receive URI
 * - Optional request amount and message fields
 * 
 * Features:
 * - Copy address to clipboard with visual feedback
 * - Save QR code as PNG
 * - Monospace font for address display
 * - Auto-updates when accounts or QR inputs change
 * 
 * Layout:
 * ┌─────────────────────────────────────┐
 * │ Receive Funds                       │
 * ├─────────────────────────────────────┤
 * │ Account:  [Select Account ▼]       │
 * │           Balance: 100.5 ANM       │
 * │                                     │
 * │ Your Address:                       │
 * │ ┌─────────────────────────────────┐ │
 * │ │  anim1qpzry9x8gf2tvdw0s3jn54khce│ │
 * │ │  6mua7lmqqqxw                    │ │
 * │ │  [Copy to Clipboard]             │ │
 * │ └─────────────────────────────────┘ │
 * │                                     │
 * │       ┌───────────────┐             │
 * │       │   QR Code     │             │
 * │       │    PNG QR     │             │
 * │       └───────────────┘             │
 * │                                     │
 * │ Amount:        [_______________]    │
 * │ Message:       [_______________]    │
 * │ [Save QR as PNG]                    │
 * │                                     │
 * └─────────────────────────────────────┘
 * 
 * Example Usage:
 * @code
 *   ReceiveWidget* widget = new ReceiveWidget(walletEngine, this);
 *   layout->addWidget(widget);
 * @endcode
 */
class ReceiveWidget : public QWidget
{
    Q_OBJECT
    
public:
    /**
     * @brief Construct receive widget.
     * @param walletEngine Wallet engine for account access
     * @param parent Parent widget
     */
    explicit ReceiveWidget(
        WalletEngine* walletEngine,
        QWidget* parent = nullptr,
        std::shared_ptr<ReceiveQrService> qrService = nullptr
    );
    
    ~ReceiveWidget();
    
public slots:
    /**
     * @brief Refresh account list and balances.
     */
    void refresh();
    
private slots:
    void onAccountChanged(int index);
    void onCopyClicked();
    void onSaveQrClicked();
    void onBalanceUpdated(const QString& address, const Balance& balance);
    void onQrGenerationFinished();
    
private:
    void showEvent(QShowEvent* event) override;
    void setupUi();
    void updateAccounts();
    void updateAddress();
    void updateBalance();
    void scheduleQrGeneration();
    void startQrGeneration();
    void applyQrResult(const ReceiveQrResult& result);
    QString formatBalance(qint64 wei) const;
    
    WalletEngine* m_walletEngine;
    std::shared_ptr<ReceiveQrService> m_qrService;
    
    // UI components
    QComboBox* m_accountCombo;
    QLabel* m_addressLabel;
    QLabel* m_qrCodeLabel;
    QLabel* m_qrStatusLabel;
    QPushButton* m_copyButton;
    QPushButton* m_saveQrButton;
    QLineEdit* m_amountEdit;
    QLineEdit* m_messageEdit;
    QLabel* m_balanceLabel;
    QTimer* m_qrUpdateTimer;
    QFutureWatcher<ReceiveQrResult>* m_qrWatcher;
    QImage m_currentQrImage;
    QString m_currentQrPayload;
    quint64 m_qrRequestedRevision;
    quint64 m_qrActiveRevision;
    bool m_qrGenerationPending;
};

#endif // RECEIVEWIDGET_H
