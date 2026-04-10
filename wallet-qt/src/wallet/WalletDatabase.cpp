#include "WalletDatabase.h"
#include <QSqlQuery>
#include <QSqlError>
#include <QVariant>
#include <QDateTime>
#include <QUuid>
#include <QMutexLocker>
#include <QDebug>

WalletDatabase::WalletDatabase(const QString& dbPath, QObject* parent)
    : QObject(parent)
    , m_dbPath(dbPath)
    , m_stateVersion(0)
    , m_connectionName(QUuid::createUuid().toString())
{
}

WalletDatabase::~WalletDatabase()
{
    if (m_db.isOpen()) {
        m_db.close();
    }
    QSqlDatabase::removeDatabase(m_connectionName);
}

bool WalletDatabase::initialize()
{
    QMutexLocker locker(&m_mutex);
    
    // Create unique connection for this instance
    m_db = QSqlDatabase::addDatabase("QSQLITE", m_connectionName);
    m_db.setDatabaseName(m_dbPath);
    
    if (!m_db.open()) {
        QString errorMsg = QString("Failed to open database: %1").arg(m_db.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    // Enable foreign key constraints
    QSqlQuery pragmaQuery(m_db);
    if (!pragmaQuery.exec("PRAGMA foreign_keys = ON")) {
        qWarning() << "Failed to enable foreign key constraints:" << pragmaQuery.lastError().text();
    }
    
    qDebug() << "Opened wallet database:" << m_dbPath;
    
    // Create tables if needed
    if (!createTables()) {
        return false;
    }
    
    // Load current state version from metadata (NULL converts to 0 for empty table)
    QSqlQuery query(m_db);
    query.prepare("SELECT COALESCE(MAX(state_version), 0) FROM wallet_ledger_entry");
    if (query.exec() && query.next()) {
        m_stateVersion = query.value(0).toLongLong();
    }
    
    qDebug() << "Initialized wallet database, current state version:" << m_stateVersion;
    return true;
}

bool WalletDatabase::createTables()
{
    QSqlQuery query(m_db);
    
    // wallet_tx table
    QString createWalletTx = R"(
        CREATE TABLE IF NOT EXISTS wallet_tx (
            txid TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            from_account_id TEXT,
            to_address TEXT,
            amount INTEGER NOT NULL,
            fee INTEGER,
            state TEXT NOT NULL,
            first_seen_at INTEGER NOT NULL,
            last_update_at INTEGER NOT NULL,
            block_hash TEXT,
            block_height INTEGER,
            confirmations INTEGER DEFAULT 0,
            raw_tx BLOB,
            failure_reason TEXT,
            CHECK (direction IN ('in', 'out', 'self')),
            CHECK (state IN ('CREATED', 'SIGNED', 'BROADCAST', 'MEMPOOL', 'CONFIRMED', 'FINAL', 'DROPPED', 'REORGED', 'FAILED'))
        )
    )";
    
    if (!query.exec(createWalletTx)) {
        QString errorMsg = QString("Failed to create wallet_tx table: %1").arg(query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    // Indexes for wallet_tx
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_wallet_tx_state ON wallet_tx(state)")) {
        qWarning() << "Failed to create index idx_wallet_tx_state:" << query.lastError().text();
    }
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_wallet_tx_account ON wallet_tx(from_account_id)")) {
        qWarning() << "Failed to create index idx_wallet_tx_account:" << query.lastError().text();
    }
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_wallet_tx_block ON wallet_tx(block_height)")) {
        qWarning() << "Failed to create index idx_wallet_tx_block:" << query.lastError().text();
    }
    
    // wallet_ledger_entry table
    QString createLedger = R"(
        CREATE TABLE IF NOT EXISTS wallet_ledger_entry (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            txid TEXT NOT NULL,
            account_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            type TEXT NOT NULL,
            delta INTEGER NOT NULL,
            state_version INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (txid) REFERENCES wallet_tx(txid),
            CHECK (type IN ('AVAILABLE', 'PENDING_IN', 'PENDING_OUT', 'FEE_RESERVED'))
        )
    )";
    
    if (!query.exec(createLedger)) {
        QString errorMsg = QString("Failed to create wallet_ledger_entry table: %1").arg(query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    // Indexes for wallet_ledger_entry
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_ledger_txid ON wallet_ledger_entry(txid)")) {
        qWarning() << "Failed to create index idx_ledger_txid:" << query.lastError().text();
    }
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_ledger_account ON wallet_ledger_entry(account_id)")) {
        qWarning() << "Failed to create index idx_ledger_account:" << query.lastError().text();
    }
    if (!query.exec("CREATE INDEX IF NOT EXISTS idx_ledger_version ON wallet_ledger_entry(state_version)")) {
        qWarning() << "Failed to create index idx_ledger_version:" << query.lastError().text();
    }
    
    // idempotency_keys table
    QString createIdempotency = R"(
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            processed_at INTEGER NOT NULL
        )
    )";
    
    if (!query.exec(createIdempotency)) {
        QString errorMsg = QString("Failed to create idempotency_keys table: %1").arg(query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    // reconciliation_runs table
    QString createReconciliation = R"(
        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            run_id TEXT PRIMARY KEY,
            started_at INTEGER NOT NULL,
            completed_at INTEGER,
            status TEXT NOT NULL,
            before_snapshot TEXT,
            after_snapshot TEXT,
            changes_applied TEXT,
            CHECK (status IN ('running', 'completed', 'failed'))
        )
    )";
    
    if (!query.exec(createReconciliation)) {
        QString errorMsg = QString("Failed to create reconciliation_runs table: %1").arg(query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    qDebug() << "Created wallet database tables";
    return true;
}

qint64 WalletDatabase::getCurrentTimestamp()
{
    return QDateTime::currentMSecsSinceEpoch();
}

bool WalletDatabase::isValidStateTransition(const QString& oldState, const QString& newState)
{
    // Define valid state transitions
    static const QMap<QString, QStringList> validTransitions = {
        {"CREATED", {"SIGNED", "FAILED"}},
        {"SIGNED", {"BROADCAST", "FAILED"}},
        {"BROADCAST", {"MEMPOOL", "DROPPED", "FAILED"}},
        {"MEMPOOL", {"CONFIRMED", "DROPPED", "FAILED"}},
        {"CONFIRMED", {"FINAL", "REORGED"}},
        {"FINAL", {}},  // Terminal state
        {"DROPPED", {}},  // Terminal state
        {"REORGED", {"MEMPOOL", "CONFIRMED", "DROPPED"}},  // Can return to mempool or be re-included directly
        {"FAILED", {}}  // Terminal state
    };
    
    // Allow same state (idempotent updates)
    if (oldState == newState) {
        return true;
    }
    
    // Check if transition is valid
    if (validTransitions.contains(oldState)) {
        return validTransitions[oldState].contains(newState);
    }
    
    return false;
}

bool WalletDatabase::checkBalanceInvariant(const LedgerEntry& entry)
{
    // Only check AVAILABLE type (others are pending/reserved)
    if (entry.type != "AVAILABLE") {
        return true;
    }
    
    // Calculate what the new balance would be (mutex already held by caller)
    qint64 currentBalance = getBalanceUnlocked(entry.accountId, entry.asset);
    qint64 newBalance = currentBalance + entry.delta;
    
    // Available balance must never go negative
    if (newBalance < 0) {
        qWarning() << "Balance invariant violation: account" << entry.accountId
                   << "asset" << entry.asset
                   << "would have balance" << newBalance;
        return false;
    }
    
    return true;
}

WalletTx WalletDatabase::getTransactionUnlocked(const QString& txid)
{
    // Mutex already held by caller
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT txid, direction, from_account_id, to_address,
               amount, fee, state, first_seen_at, last_update_at,
               block_hash, block_height, confirmations, raw_tx, failure_reason
        FROM wallet_tx
        WHERE txid = :txid
    )");
    
    query.bindValue(":txid", txid);
    
    if (!query.exec()) {
        qWarning() << "Failed to get transaction:" << query.lastError().text();
        return WalletTx();
    }
    
    if (!query.next()) {
        return WalletTx();  // Not found
    }
    
    WalletTx tx;
    tx.txid = query.value(0).toString();
    tx.direction = query.value(1).toString();
    tx.fromAccountId = query.value(2).toString();
    tx.toAddress = query.value(3).toString();
    tx.amount = query.value(4).toLongLong();
    tx.fee = query.value(5).toLongLong();
    tx.state = query.value(6).toString();
    tx.firstSeenAt = query.value(7).toLongLong();
    tx.lastUpdateAt = query.value(8).toLongLong();
    tx.blockHash = query.value(9).toString();
    tx.blockHeight = query.value(10).toLongLong();
    tx.confirmations = query.value(11).toInt();
    tx.rawTx = query.value(12).toByteArray();
    tx.failureReason = query.value(13).toString();
    
    return tx;
}

qint64 WalletDatabase::getBalanceUnlocked(const QString& accountId, const QString& asset)
{
    // Mutex already held by caller
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT COALESCE(SUM(delta), 0)
        FROM wallet_ledger_entry
        WHERE account_id = :account_id
          AND asset = :asset
          AND type = 'AVAILABLE'
    )");
    
    query.bindValue(":account_id", accountId);
    query.bindValue(":asset", asset);
    
    if (!query.exec()) {
        qWarning() << "Failed to get balance:" << query.lastError().text();
        return 0;
    }
    
    if (query.next()) {
        return query.value(0).toLongLong();
    }
    
    return 0;
}


// ==================== Transaction Journal ====================

bool WalletDatabase::addTransaction(const WalletTx& tx)
{
    QMutexLocker locker(&m_mutex);
    
    if (!tx.isValid()) {
        qWarning() << "Cannot add invalid transaction";
        return false;
    }
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        INSERT INTO wallet_tx (
            txid, direction, from_account_id, to_address,
            amount, fee, state, first_seen_at, last_update_at,
            block_hash, block_height, confirmations, raw_tx, failure_reason
        ) VALUES (
            :txid, :direction, :from_account_id, :to_address,
            :amount, :fee, :state, :first_seen_at, :last_update_at,
            :block_hash, :block_height, :confirmations, :raw_tx, :failure_reason
        )
    )");
    
    query.bindValue(":txid", tx.txid);
    query.bindValue(":direction", tx.direction);
    query.bindValue(":from_account_id", tx.fromAccountId);
    query.bindValue(":to_address", tx.toAddress);
    query.bindValue(":amount", tx.amount);
    query.bindValue(":fee", tx.fee);
    query.bindValue(":state", tx.state);
    query.bindValue(":first_seen_at", tx.firstSeenAt);
    query.bindValue(":last_update_at", tx.lastUpdateAt);
    query.bindValue(":block_hash", tx.blockHash);
    query.bindValue(":block_height", tx.blockHeight);
    query.bindValue(":confirmations", tx.confirmations);
    query.bindValue(":raw_tx", tx.rawTx);
    query.bindValue(":failure_reason", tx.failureReason);
    
    if (!query.exec()) {
        QString errorMsg = QString("Failed to add transaction %1: %2")
            .arg(tx.txid, query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    qDebug() << "Added transaction:" << tx.txid << "state:" << tx.state;
    emit transactionAdded(tx);
    return true;
}

bool WalletDatabase::updateTransaction(const QString& txid, const WalletTx& tx)
{
    QMutexLocker locker(&m_mutex);
    
    if (!tx.isValid() || txid != tx.txid) {
        qWarning() << "Invalid transaction update";
        return false;
    }
    
    // Check if transaction exists and get old state (use unlocked version)
    WalletTx oldTx = getTransactionUnlocked(txid);
    if (!oldTx.isValid()) {
        qWarning() << "Transaction not found:" << txid;
        return false;
    }
    
    // Validate state transition
    if (!isValidStateTransition(oldTx.state, tx.state)) {
        qWarning() << "Invalid state transition:" << oldTx.state << "->" << tx.state;
        return false;
    }
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        UPDATE wallet_tx SET
            direction = :direction,
            from_account_id = :from_account_id,
            to_address = :to_address,
            amount = :amount,
            fee = :fee,
            state = :state,
            last_update_at = :last_update_at,
            block_hash = :block_hash,
            block_height = :block_height,
            confirmations = :confirmations,
            raw_tx = :raw_tx,
            failure_reason = :failure_reason
        WHERE txid = :txid
    )");
    
    query.bindValue(":txid", tx.txid);
    query.bindValue(":direction", tx.direction);
    query.bindValue(":from_account_id", tx.fromAccountId);
    query.bindValue(":to_address", tx.toAddress);
    query.bindValue(":amount", tx.amount);
    query.bindValue(":fee", tx.fee);
    query.bindValue(":state", tx.state);
    query.bindValue(":last_update_at", tx.lastUpdateAt);
    query.bindValue(":block_hash", tx.blockHash);
    query.bindValue(":block_height", tx.blockHeight);
    query.bindValue(":confirmations", tx.confirmations);
    query.bindValue(":raw_tx", tx.rawTx);
    query.bindValue(":failure_reason", tx.failureReason);
    
    if (!query.exec()) {
        QString errorMsg = QString("Failed to update transaction %1: %2")
            .arg(tx.txid, query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    qDebug() << "Updated transaction:" << tx.txid << "state:" << oldTx.state << "->" << tx.state;
    emit transactionUpdated(tx);
    return true;
}

WalletTx WalletDatabase::getTransaction(const QString& txid)
{
    QMutexLocker locker(&m_mutex);
    return getTransactionUnlocked(txid);
}

QList<WalletTx> WalletDatabase::listTransactions(const QString& accountId)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    
    if (accountId.isEmpty()) {
        // List all transactions
        query.prepare(R"(
            SELECT txid, direction, from_account_id, to_address,
                   amount, fee, state, first_seen_at, last_update_at,
                   block_hash, block_height, confirmations, raw_tx, failure_reason
            FROM wallet_tx
            ORDER BY last_update_at DESC
        )");
    } else {
        // List transactions for specific account
        query.prepare(R"(
            SELECT txid, direction, from_account_id, to_address,
                   amount, fee, state, first_seen_at, last_update_at,
                   block_hash, block_height, confirmations, raw_tx, failure_reason
            FROM wallet_tx
            WHERE from_account_id = :account_id
            ORDER BY last_update_at DESC
        )");
        query.bindValue(":account_id", accountId);
    }
    
    if (!query.exec()) {
        qWarning() << "Failed to list transactions:" << query.lastError().text();
        return QList<WalletTx>();
    }
    
    QList<WalletTx> transactions;
    while (query.next()) {
        WalletTx tx;
        tx.txid = query.value(0).toString();
        tx.direction = query.value(1).toString();
        tx.fromAccountId = query.value(2).toString();
        tx.toAddress = query.value(3).toString();
        tx.amount = query.value(4).toLongLong();
        tx.fee = query.value(5).toLongLong();
        tx.state = query.value(6).toString();
        tx.firstSeenAt = query.value(7).toLongLong();
        tx.lastUpdateAt = query.value(8).toLongLong();
        tx.blockHash = query.value(9).toString();
        tx.blockHeight = query.value(10).toLongLong();
        tx.confirmations = query.value(11).toInt();
        tx.rawTx = query.value(12).toByteArray();
        tx.failureReason = query.value(13).toString();
        transactions.append(tx);
    }
    
    return transactions;
}

bool WalletDatabase::deleteTransaction(const QString& txid)
{
    QMutexLocker locker(&m_mutex);
    
    // Delete associated ledger entries first (foreign key constraint)
    QSqlQuery query(m_db);
    query.prepare("DELETE FROM wallet_ledger_entry WHERE txid = :txid");
    query.bindValue(":txid", txid);
    
    if (!query.exec()) {
        qWarning() << "Failed to delete ledger entries:" << query.lastError().text();
        return false;
    }
    
    // Delete transaction
    query.prepare("DELETE FROM wallet_tx WHERE txid = :txid");
    query.bindValue(":txid", txid);
    
    if (!query.exec()) {
        qWarning() << "Failed to delete transaction:" << query.lastError().text();
        return false;
    }
    
    qDebug() << "Deleted transaction:" << txid;
    return true;
}

// ==================== Ledger Entries ====================

bool WalletDatabase::addLedgerEntry(const LedgerEntry& entry)
{
    QMutexLocker locker(&m_mutex);
    
    // Check balance invariant before adding
    if (!checkBalanceInvariant(entry)) {
        QString errorMsg = QString("Cannot add ledger entry: would violate balance invariant for account %1")
            .arg(entry.accountId);
        qWarning() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        INSERT INTO wallet_ledger_entry (
            txid, account_id, asset, type, delta, state_version, created_at
        ) VALUES (
            :txid, :account_id, :asset, :type, :delta, :state_version, :created_at
        )
    )");
    
    query.bindValue(":txid", entry.txid);
    query.bindValue(":account_id", entry.accountId);
    query.bindValue(":asset", entry.asset);
    query.bindValue(":type", entry.type);
    query.bindValue(":delta", entry.delta);
    query.bindValue(":state_version", entry.stateVersion);
    query.bindValue(":created_at", entry.createdAt);
    
    if (!query.exec()) {
        QString errorMsg = QString("Failed to add ledger entry: %1").arg(query.lastError().text());
        qCritical() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    qDebug() << "Added ledger entry: account" << entry.accountId
             << "type" << entry.type << "delta" << entry.delta;
    emit ledgerUpdated(entry.accountId);
    return true;
}

QList<LedgerEntry> WalletDatabase::getLedgerEntries(const QString& txid)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT entry_id, txid, account_id, asset, type, delta, state_version, created_at
        FROM wallet_ledger_entry
        WHERE txid = :txid
        ORDER BY state_version ASC
    )");
    
    query.bindValue(":txid", txid);
    
    if (!query.exec()) {
        qWarning() << "Failed to get ledger entries:" << query.lastError().text();
        return QList<LedgerEntry>();
    }
    
    QList<LedgerEntry> entries;
    while (query.next()) {
        LedgerEntry entry;
        entry.entryId = query.value(0).toLongLong();
        entry.txid = query.value(1).toString();
        entry.accountId = query.value(2).toString();
        entry.asset = query.value(3).toString();
        entry.type = query.value(4).toString();
        entry.delta = query.value(5).toLongLong();
        entry.stateVersion = query.value(6).toLongLong();
        entry.createdAt = query.value(7).toLongLong();
        entries.append(entry);
    }
    
    return entries;
}

QList<LedgerEntry> WalletDatabase::getAccountLedger(const QString& accountId)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT entry_id, txid, account_id, asset, type, delta, state_version, created_at
        FROM wallet_ledger_entry
        WHERE account_id = :account_id
        ORDER BY state_version ASC
    )");
    
    query.bindValue(":account_id", accountId);
    
    if (!query.exec()) {
        qWarning() << "Failed to get account ledger:" << query.lastError().text();
        return QList<LedgerEntry>();
    }
    
    QList<LedgerEntry> entries;
    while (query.next()) {
        LedgerEntry entry;
        entry.entryId = query.value(0).toLongLong();
        entry.txid = query.value(1).toString();
        entry.accountId = query.value(2).toString();
        entry.asset = query.value(3).toString();
        entry.type = query.value(4).toString();
        entry.delta = query.value(5).toLongLong();
        entry.stateVersion = query.value(6).toLongLong();
        entry.createdAt = query.value(7).toLongLong();
        entries.append(entry);
    }
    
    return entries;
}

QList<LedgerEntry> WalletDatabase::listLedgerEntries()
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT entry_id, txid, account_id, asset, type, delta, state_version, created_at
        FROM wallet_ledger_entry
        ORDER BY state_version ASC
    )");
    
    if (!query.exec()) {
        qWarning() << "Failed to list ledger entries:" << query.lastError().text();
        return QList<LedgerEntry>();
    }
    
    QList<LedgerEntry> entries;
    while (query.next()) {
        LedgerEntry entry;
        entry.entryId = query.value(0).toLongLong();
        entry.txid = query.value(1).toString();
        entry.accountId = query.value(2).toString();
        entry.asset = query.value(3).toString();
        entry.type = query.value(4).toString();
        entry.delta = query.value(5).toLongLong();
        entry.stateVersion = query.value(6).toLongLong();
        entry.createdAt = query.value(7).toLongLong();
        entries.append(entry);
    }
    
    return entries;
}

bool WalletDatabase::deleteLedgerEntry(qint64 ledgerId)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare("DELETE FROM wallet_ledger_entry WHERE entry_id = :entry_id");
    query.bindValue(":entry_id", ledgerId);
    
    if (!query.exec()) {
        QString errorMsg = QString("Failed to delete ledger entry %1: %2")
            .arg(ledgerId)
            .arg(query.lastError().text());
        qWarning() << errorMsg;
        emit error(errorMsg);
        return false;
    }
    
    return true;
}

qint64 WalletDatabase::getBalance(const QString& accountId, const QString& asset)
{
    QMutexLocker locker(&m_mutex);
    return getBalanceUnlocked(accountId, asset);
}

qint64 WalletDatabase::getPendingBalance(const QString& accountId, const QString& asset)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        SELECT COALESCE(SUM(delta), 0)
        FROM wallet_ledger_entry
        WHERE account_id = :account_id
          AND asset = :asset
          AND type IN ('PENDING_IN', 'PENDING_OUT')
    )");
    
    query.bindValue(":account_id", accountId);
    query.bindValue(":asset", asset);
    
    if (!query.exec()) {
        qWarning() << "Failed to get pending balance:" << query.lastError().text();
        return 0;
    }
    
    if (query.next()) {
        return query.value(0).toLongLong();
    }
    
    return 0;
}

// ==================== State Version ====================

qint64 WalletDatabase::nextStateVersion()
{
    QMutexLocker locker(&m_mutex);
    return ++m_stateVersion;
}

// ==================== Idempotency ====================

bool WalletDatabase::checkIdempotency(const QString& key)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare("SELECT key FROM idempotency_keys WHERE key = :key");
    query.bindValue(":key", key);
    
    if (!query.exec()) {
        qWarning() << "Failed to check idempotency:" << query.lastError().text();
        return false;
    }
    
    return query.next();  // true if key exists (already processed)
}

bool WalletDatabase::markProcessed(const QString& key)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        INSERT INTO idempotency_keys (key, processed_at)
        VALUES (:key, :processed_at)
    )");
    
    query.bindValue(":key", key);
    query.bindValue(":processed_at", getCurrentTimestamp());
    
    if (!query.exec()) {
        qWarning() << "Failed to mark processed:" << query.lastError().text();
        return false;
    }
    
    qDebug() << "Marked as processed:" << key;
    return true;
}

// ==================== Reconciliation ====================

QString WalletDatabase::startReconciliation()
{
    QMutexLocker locker(&m_mutex);
    
    QString runId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        INSERT INTO reconciliation_runs (run_id, started_at, status)
        VALUES (:run_id, :started_at, 'running')
    )");
    
    query.bindValue(":run_id", runId);
    query.bindValue(":started_at", getCurrentTimestamp());
    
    if (!query.exec()) {
        qWarning() << "Failed to start reconciliation:" << query.lastError().text();
        return QString();
    }
    
    qDebug() << "Started reconciliation run:" << runId;
    return runId;
}

bool WalletDatabase::recordReconciliationSnapshot(const QString& runId, const QString& beforeJson, const QString& afterJson)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        UPDATE reconciliation_runs
        SET before_snapshot = :before_snapshot,
            after_snapshot = :after_snapshot
        WHERE run_id = :run_id
    )");
    
    query.bindValue(":run_id", runId);
    query.bindValue(":before_snapshot", beforeJson);
    query.bindValue(":after_snapshot", afterJson);
    
    if (!query.exec()) {
        qWarning() << "Failed to record reconciliation snapshot:" << query.lastError().text();
        return false;
    }
    
    return true;
}

bool WalletDatabase::completeReconciliation(const QString& runId, const QString& changesJson)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        UPDATE reconciliation_runs
        SET status = 'completed',
            completed_at = :completed_at,
            changes_applied = :changes_applied
        WHERE run_id = :run_id
    )");
    
    query.bindValue(":run_id", runId);
    query.bindValue(":completed_at", getCurrentTimestamp());
    query.bindValue(":changes_applied", changesJson);
    
    if (!query.exec()) {
        qWarning() << "Failed to complete reconciliation:" << query.lastError().text();
        return false;
    }
    
    qDebug() << "Completed reconciliation run:" << runId;
    return true;
}

bool WalletDatabase::failReconciliation(const QString& runId, const QString& errorMsg)
{
    QMutexLocker locker(&m_mutex);
    
    QSqlQuery query(m_db);
    query.prepare(R"(
        UPDATE reconciliation_runs
        SET status = 'failed',
            completed_at = :completed_at,
            changes_applied = :error_msg
        WHERE run_id = :run_id
    )");
    
    query.bindValue(":run_id", runId);
    query.bindValue(":completed_at", getCurrentTimestamp());
    query.bindValue(":error_msg", errorMsg);
    
    if (!query.exec()) {
        qWarning() << "Failed to mark reconciliation as failed:" << query.lastError().text();
        return false;
    }
    
    qDebug() << "Failed reconciliation run:" << runId << "error:" << errorMsg;
    return true;
}

// ==================== Transaction Support ====================

bool WalletDatabase::beginTransaction()
{
    QMutexLocker locker(&m_mutex);
    
    if (!m_db.transaction()) {
        qWarning() << "Failed to begin transaction:" << m_db.lastError().text();
        return false;
    }
    
    return true;
}

bool WalletDatabase::commit()
{
    QMutexLocker locker(&m_mutex);
    
    if (!m_db.commit()) {
        qWarning() << "Failed to commit transaction:" << m_db.lastError().text();
        return false;
    }
    
    return true;
}

bool WalletDatabase::rollback()
{
    QMutexLocker locker(&m_mutex);
    
    if (!m_db.rollback()) {
        qWarning() << "Failed to rollback transaction:" << m_db.lastError().text();
        return false;
    }
    
    return true;
}
