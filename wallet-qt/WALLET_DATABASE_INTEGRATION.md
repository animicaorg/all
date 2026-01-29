# WalletDatabase Integration Guide

This guide shows how to integrate WalletDatabase into the Qt wallet for transaction journal and double-entry accounting.

## Overview

WalletDatabase provides:
- **Transaction Journal**: Track all wallet transactions with full lifecycle (CREATED → SIGNED → BROADCAST → MEMPOOL → CONFIRMED → FINAL)
- **Double-Entry Accounting**: Ledger entries for balance tracking with AVAILABLE, PENDING_IN, PENDING_OUT, and FEE_RESERVED types
- **Idempotency**: Prevent duplicate event processing
- **Reconciliation**: Audit trail for balance corrections
- **Thread Safety**: QMutex protection for concurrent access

## Integration with WalletEngine

### 1. Add WalletDatabase to WalletEngine

```cpp
// WalletEngine.h
#include "WalletDatabase.h"

class WalletEngine : public QObject
{
    // ...
private:
    WalletDatabase* m_database;
    QString m_databasePath;
};
```

### 2. Initialize in createWallet/openWallet

```cpp
bool WalletEngine::createWallet(const QString& password, const QString& dataDir)
{
    // ... existing keystore initialization ...
    
    // Initialize database
    m_databasePath = dataDir + "/wallet.db";
    m_database = new WalletDatabase(m_databasePath, this);
    
    if (!m_database->initialize()) {
        qCritical() << "Failed to initialize wallet database";
        delete m_database;
        m_database = nullptr;
        return false;
    }
    
    // Connect signals
    connect(m_database, &WalletDatabase::transactionAdded,
            this, &WalletEngine::handleTransactionAdded);
    connect(m_database, &WalletDatabase::transactionUpdated,
            this, &WalletEngine::handleTransactionUpdated);
    connect(m_database, &WalletDatabase::ledgerUpdated,
            this, &WalletEngine::handleLedgerUpdated);
    
    return true;
}
```

### 3. Send Transaction Flow

```cpp
QString WalletEngine::sendTransaction(const QString& fromAccountId, 
                                     const QString& toAddress,
                                     qint64 amount)
{
    if (isLocked()) {
        emit error("Wallet is locked");
        return QString();
    }
    
    // Step 1: Create transaction record
    WalletTx tx;
    tx.txid = generateTxId();  // Generate unique ID
    tx.direction = "out";
    tx.fromAccountId = fromAccountId;
    tx.toAddress = toAddress;
    tx.amount = amount;
    tx.fee = estimateFee(amount);
    tx.state = "CREATED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHeight = -1;
    
    // Step 2: Add to database in atomic transaction
    if (!m_database->beginTransaction()) {
        emit error("Failed to begin database transaction");
        return QString();
    }
    
    // Add transaction record
    if (!m_database->addTransaction(tx)) {
        m_database->rollback();
        emit error("Failed to add transaction to database");
        return QString();
    }
    
    // Add ledger entries for double-entry accounting
    qint64 stateVersion = m_database->nextStateVersion();
    qint64 now = QDateTime::currentMSecsSinceEpoch();
    
    // Debit available balance
    LedgerEntry debit;
    debit.txid = tx.txid;
    debit.accountId = fromAccountId;
    debit.asset = "ANM";
    debit.type = "AVAILABLE";
    debit.delta = -(amount + tx.fee);  // Negative for debit
    debit.stateVersion = stateVersion;
    debit.createdAt = now;
    
    if (!m_database->addLedgerEntry(debit)) {
        m_database->rollback();
        emit error("Insufficient balance");
        return QString();
    }
    
    // Reserve for pending out
    LedgerEntry pendingOut;
    pendingOut.txid = tx.txid;
    pendingOut.accountId = fromAccountId;
    pendingOut.asset = "ANM";
    pendingOut.type = "PENDING_OUT";
    pendingOut.delta = amount;  // Positive pending
    pendingOut.stateVersion = stateVersion + 1;
    pendingOut.createdAt = now;
    
    if (!m_database->addLedgerEntry(pendingOut)) {
        m_database->rollback();
        emit error("Failed to add pending entry");
        return QString();
    }
    
    // Reserve fee
    LedgerEntry feeReserve;
    feeReserve.txid = tx.txid;
    feeReserve.accountId = fromAccountId;
    feeReserve.asset = "ANM";
    feeReserve.type = "FEE_RESERVED";
    feeReserve.delta = tx.fee;
    feeReserve.stateVersion = stateVersion + 2;
    feeReserve.createdAt = now;
    
    if (!m_database->addLedgerEntry(feeReserve)) {
        m_database->rollback();
        emit error("Failed to add fee reserve");
        return QString();
    }
    
    // Commit database transaction
    if (!m_database->commit()) {
        emit error("Failed to commit database transaction");
        return QString();
    }
    
    // Step 3: Sign transaction
    QString signedTx = signTransaction(buildTxJson(tx), fromAccountId);
    if (signedTx.isEmpty()) {
        // Update state to FAILED
        tx.state = "FAILED";
        tx.failureReason = "Signing failed";
        tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
        m_database->updateTransaction(tx.txid, tx);
        return QString();
    }
    
    // Update state to SIGNED
    tx.state = "SIGNED";
    tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    m_database->updateTransaction(tx.txid, tx);
    
    // Step 4: Broadcast transaction
    if (broadcastTransaction(signedTx)) {
        tx.state = "BROADCAST";
        tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
        m_database->updateTransaction(tx.txid, tx);
    } else {
        tx.state = "FAILED";
        tx.failureReason = "Broadcast failed";
        tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
        m_database->updateTransaction(tx.txid, tx);
    }
    
    return tx.txid;
}
```

### 4. Receive Transaction Flow

```cpp
void WalletEngine::handleIncomingTransaction(const QString& txid,
                                            const QString& fromAddress,
                                            const QString& toAddress,
                                            qint64 amount,
                                            const QString& blockHash,
                                            qint64 blockHeight)
{
    // Find which account owns toAddress
    QString accountId = findAccountByAddress(toAddress);
    if (accountId.isEmpty()) {
        return;  // Not our transaction
    }
    
    // Check idempotency
    QString idempotencyKey = QString("%1:RECEIVE:rpc:%2").arg(txid).arg(blockHeight);
    if (m_database->checkIdempotency(idempotencyKey)) {
        qDebug() << "Already processed incoming tx:" << txid;
        return;
    }
    
    // Add transaction record
    WalletTx tx;
    tx.txid = txid;
    tx.direction = "in";
    tx.fromAccountId = QString();  // External sender
    tx.toAddress = toAddress;
    tx.amount = amount;
    tx.fee = 0;  // Receiver doesn't pay fee
    tx.state = blockHash.isEmpty() ? "MEMPOOL" : "CONFIRMED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHash = blockHash;
    tx.blockHeight = blockHeight;
    
    m_database->beginTransaction();
    
    if (!m_database->addTransaction(tx)) {
        m_database->rollback();
        return;
    }
    
    // Add pending incoming ledger entry
    LedgerEntry entry;
    entry.txid = txid;
    entry.accountId = accountId;
    entry.asset = "ANM";
    entry.type = blockHash.isEmpty() ? "PENDING_IN" : "AVAILABLE";
    entry.delta = amount;  // Positive for credit
    entry.stateVersion = m_database->nextStateVersion();
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();
    
    if (!m_database->addLedgerEntry(entry)) {
        m_database->rollback();
        return;
    }
    
    // Mark as processed
    m_database->markProcessed(idempotencyKey);
    
    m_database->commit();
    
    qDebug() << "Processed incoming tx:" << txid << "amount:" << amount;
}
```

### 5. Confirmation Updates

```cpp
void WalletEngine::handleConfirmationUpdate(const QString& txid,
                                           qint64 blockHeight,
                                           const QString& blockHash,
                                           int confirmations)
{
    WalletTx tx = m_database->getTransaction(txid);
    if (!tx.isValid()) {
        return;  // Not our transaction
    }
    
    // Check idempotency
    QString idempotencyKey = QString("%1:CONFIRM:rpc:%2").arg(txid).arg(blockHeight);
    if (m_database->checkIdempotency(idempotencyKey)) {
        return;
    }
    
    bool stateChanged = false;
    
    // Update transaction state
    if (tx.state == "MEMPOOL" && confirmations >= 1) {
        tx.state = "CONFIRMED";
        stateChanged = true;
    } else if (tx.state == "CONFIRMED" && confirmations >= 12) {
        tx.state = "FINAL";
        stateChanged = true;
    }
    
    tx.blockHash = blockHash;
    tx.blockHeight = blockHeight;
    tx.confirmations = confirmations;
    tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    
    m_database->beginTransaction();
    
    m_database->updateTransaction(txid, tx);
    
    // If moving from pending to confirmed, update ledger
    if (stateChanged && confirmations == 1) {
        if (tx.direction == "in") {
            // Convert PENDING_IN to AVAILABLE
            convertPendingInToAvailable(txid, tx.fromAccountId);
        } else if (tx.direction == "out") {
            // Remove PENDING_OUT entries (already debited)
            removePendingOut(txid, tx.fromAccountId);
        }
    }
    
    m_database->markProcessed(idempotencyKey);
    m_database->commit();
}

void WalletEngine::convertPendingInToAvailable(const QString& txid, const QString& accountId)
{
    // This is handled automatically by adding a new AVAILABLE entry
    // and offsetting the PENDING_IN entry
    QList<LedgerEntry> entries = m_database->getLedgerEntries(txid);
    for (const LedgerEntry& entry : entries) {
        if (entry.type == "PENDING_IN") {
            // Add offsetting entries
            LedgerEntry offset;
            offset.txid = txid;
            offset.accountId = entry.accountId;
            offset.asset = entry.asset;
            offset.type = "PENDING_IN";
            offset.delta = -entry.delta;  // Negative to cancel out
            offset.stateVersion = m_database->nextStateVersion();
            offset.createdAt = QDateTime::currentMSecsSinceEpoch();
            m_database->addLedgerEntry(offset);
            
            // Add available entry
            LedgerEntry available;
            available.txid = txid;
            available.accountId = entry.accountId;
            available.asset = entry.asset;
            available.type = "AVAILABLE";
            available.delta = entry.delta;  // Positive credit
            available.stateVersion = m_database->nextStateVersion();
            available.createdAt = QDateTime::currentMSecsSinceEpoch();
            m_database->addLedgerEntry(available);
        }
    }
}
```

### 6. Balance Display

```cpp
QString WalletEngine::formatBalance(const QString& accountId)
{
    qint64 available = m_database->getBalance(accountId, "ANM");
    qint64 pending = m_database->getPendingBalance(accountId, "ANM");
    
    // Convert from wei to ANM (1 ANM = 10^18 wei)
    double availableANM = available / 1e18;
    double pendingANM = pending / 1e18;
    
    if (pendingANM != 0) {
        return QString("Available: %1 ANM (Pending: %2%3 ANM)")
            .arg(availableANM, 0, 'f', 6)
            .arg(pendingANM > 0 ? "+" : "")
            .arg(pendingANM, 0, 'f', 6);
    } else {
        return QString("%1 ANM").arg(availableANM, 0, 'f', 6);
    }
}
```

## Thread Safety

WalletDatabase uses QMutex internally for thread safety. All public methods are protected. However, for multi-step operations, use explicit transactions:

```cpp
// Thread-safe multi-operation update
m_database->beginTransaction();
m_database->addTransaction(tx);
m_database->addLedgerEntry(entry1);
m_database->addLedgerEntry(entry2);
m_database->commit();
```

## Balance Invariants

The database enforces that available balance never goes negative:

```cpp
// This will fail if account has insufficient balance
LedgerEntry debit;
debit.type = "AVAILABLE";
debit.delta = -1000000000000000000;  // -1 ANM
if (!m_database->addLedgerEntry(debit)) {
    // Handle insufficient balance error
}
```

## Reconciliation

For periodic balance audits:

```cpp
void WalletEngine::reconcileBalances()
{
    QString runId = m_database->startReconciliation();
    
    // Capture before snapshot
    QJsonObject before;
    for (const WalletAccount& account : listAccounts()) {
        qint64 dbBalance = m_database->getBalance(account.accountId, "ANM");
        before[account.accountId] = dbBalance;
    }
    
    // Fetch actual balances from node
    QJsonObject after;
    for (const WalletAccount& account : listAccounts()) {
        Balance nodeBalance = m_rpcClient->getBalance(account.address);
        after[account.accountId] = (qint64)nodeBalance.confirmed;
    }
    
    m_database->recordReconciliationSnapshot(runId, 
        QJsonDocument(before).toJson(),
        QJsonDocument(after).toJson());
    
    // Apply corrections if needed
    QJsonArray changes;
    for (const QString& accountId : before.keys()) {
        qint64 dbBalance = before[accountId].toVariant().toLongLong();
        qint64 nodeBalance = after[accountId].toVariant().toLongLong();
        
        if (dbBalance != nodeBalance) {
            qint64 correction = nodeBalance - dbBalance;
            
            // Add correction ledger entry
            LedgerEntry entry;
            entry.txid = QString("reconciliation-%1").arg(runId);
            entry.accountId = accountId;
            entry.asset = "ANM";
            entry.type = "AVAILABLE";
            entry.delta = correction;
            entry.stateVersion = m_database->nextStateVersion();
            entry.createdAt = QDateTime::currentMSecsSinceEpoch();
            m_database->addLedgerEntry(entry);
            
            QJsonObject change;
            change["account"] = accountId;
            change["delta"] = correction;
            changes.append(change);
        }
    }
    
    m_database->completeReconciliation(runId, QJsonDocument(changes).toJson());
}
```

## Testing

Run the included tests:

```bash
cd build
cmake .. -DBUILD_TESTING=ON
make test_walletdatabase
./tests/test_walletdatabase
```

## Database Schema

See WalletDatabase.h for complete schema. Key tables:

- `wallet_tx`: Transaction journal
- `wallet_ledger_entry`: Double-entry ledger
- `idempotency_keys`: Event deduplication
- `reconciliation_runs`: Balance audit trail
