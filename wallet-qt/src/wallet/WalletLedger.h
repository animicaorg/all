#ifndef WALLETLEDGER_H
#define WALLETLEDGER_H

#include <QString>
#include <QDateTime>

/**
 * @brief Ledger entry for transaction monitoring and history.
 * 
 * Simplified ledger structure for tracking transaction effects.
 * Used by TransactionMonitor to maintain transaction history.
 */
struct WalletLedger {
    qint64 ledgerId;                // Auto-increment ID (primary key)
    QString txHash;                 // Transaction hash
    QString accountAddress;         // Account address (bech32m)
    QString asset;                  // Asset identifier ("ANM" for native token)
    qint64 amountAtomic;            // Amount in atomic units (wei)
    QString type;                   // Entry type: "credit", "debit", "reversal"
    QDateTime createdAt;            // Creation timestamp
    
    // Additional fields for transaction context
    QString direction;              // "in", "out", "self"
    QString state;                  // Transaction state
    qint64 blockHeight;             // Block height (-1 if not mined)
    
    WalletLedger()
        : ledgerId(0)
        , asset("ANM")
        , amountAtomic(0)
        , blockHeight(-1)
    {
    }
    
    /**
     * @brief Check if ledger entry is valid.
     * @return true if ledgerId is set
     */
    bool isValid() const { return ledgerId > 0; }
};

#endif // WALLETLEDGER_H
