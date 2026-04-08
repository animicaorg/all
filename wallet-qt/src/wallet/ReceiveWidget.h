#ifndef RECEIVEWIDGET_H
#define RECEIVEWIDGET_H

#include <QWidget>
#include <QComboBox>
#include <QLabel>
#include <QPushButton>
#include <QLineEdit>
#include <QString>

class WalletEngine;
struct Balance;

/**
 * @brief Widget for receiving funds.
 * 
 * Displays:
 * - Account selector with balance
 * - Current account address with copy button
 * - QR code / QR availability notice for address
 * - Optional payment note field
 * 
 * Features:
 * - Copy address to clipboard with visual feedback
 * - Monospace font for address display
 * - Auto-updates when accounts change
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
 * │       │  (Placeholder)│             │
 * │       └───────────────┘             │
 * │                                     │
 * │ Payment Note: [___________________] │
 * │ (local label, not sent)            │
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
        QWidget* parent = nullptr
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
    void onBalanceUpdated(const QString& address, const Balance& balance);
    
private:
    void setupUi();
    void updateAccounts();
    void updateAddress();
    void updateBalance();
    void generateQRCode();
    QString formatBalance(qint64 wei) const;
    
    WalletEngine* m_walletEngine;
    
    // UI components
    QComboBox* m_accountCombo;
    QLabel* m_addressLabel;
    QLabel* m_qrCodeLabel;
    QPushButton* m_copyButton;
    QLineEdit* m_noteEdit;
    QLabel* m_balanceLabel;
};

#endif // RECEIVEWIDGET_H
