#include <QtTest/QtTest>
#include "../src/wallet/WalletDatabase.h"
#include <QTemporaryDir>
#include <QDebug>

/**
 * @brief Unit tests for WalletDatabase
 */
class TestWalletDatabase : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void cleanupTestCase();
    void init();
    void cleanup();

    // Transaction journal tests
    void testAddTransaction();
    void testUpdateTransaction();
    void testListTransactions();
    void testStateTransitionValidation();

    // Ledger tests
    void testAddLedgerEntry();
    void testGetBalance();
    void testBalanceInvariant();

    // State version tests
    void testStateVersion();

    // Idempotency tests
    void testIdempotency();

    // Reconciliation tests
    void testReconciliation();

    // Transaction support tests
    void testAtomicTransaction();

private:
    WalletDatabase* db;
    QTemporaryDir* tempDir;
    QString dbPath;

    WalletTx makeTx(const QString& txid, const QString& accountId = "account-uuid-1", const QString& direction = "out");
};

void TestWalletDatabase::initTestCase()
{
    tempDir = new QTemporaryDir();
    QVERIFY(tempDir->isValid());
}

void TestWalletDatabase::cleanupTestCase()
{
    delete tempDir;
}

void TestWalletDatabase::init()
{
    dbPath = tempDir->filePath("test_wallet.db");
    db = new WalletDatabase(dbPath);
    QVERIFY(db->initialize());
}

void TestWalletDatabase::cleanup()
{
    delete db;
    QFile::remove(dbPath);
}

WalletTx TestWalletDatabase::makeTx(const QString& txid, const QString& accountId, const QString& direction)
{
    WalletTx tx;
    tx.txid = txid;
    tx.direction = direction;
    tx.fromAccountId = direction == "out" ? accountId : QString();
    tx.toAddress = "anim1receiver";
    tx.amount = 1'000'000'000;
    tx.fee = 10'000'000;
    tx.state = "CREATED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHeight = -1;
    tx.confirmations = 0;
    return tx;
}

void TestWalletDatabase::testAddTransaction()
{
    WalletTx tx;
    tx.txid = "0x1234567890abcdef";
    tx.direction = "out";
    tx.fromAccountId = "account-uuid-1";
    tx.toAddress = "anim1receiver";
    tx.amount = 1'000'000'000;  // 1 ANM
    tx.fee = 10'000'000;        // 0.01 ANM
    tx.state = "CREATED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHeight = -1;
    tx.confirmations = 0;

    QVERIFY(db->addTransaction(tx));

    WalletTx retrieved = db->getTransaction(tx.txid);
    QVERIFY(retrieved.isValid());
    QCOMPARE(retrieved.txid, tx.txid);
    QCOMPARE(retrieved.direction, tx.direction);
    QCOMPARE(retrieved.amount, tx.amount);
    QCOMPARE(retrieved.state, tx.state);
}

void TestWalletDatabase::testUpdateTransaction()
{
    // Add initial transaction
    WalletTx tx;
    tx.txid = "0xabcdef1234567890";
    tx.direction = "out";
    tx.fromAccountId = "account-uuid-1";
    tx.toAddress = "anim1receiver";
    tx.amount = 1'000'000'000;
    tx.fee = 10'000'000;
    tx.state = "CREATED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHeight = -1;

    QVERIFY(db->addTransaction(tx));

    // Update to SIGNED state
    tx.state = "SIGNED";
    tx.lastUpdateAt = QDateTime::currentMSecsSinceEpoch();
    QVERIFY(db->updateTransaction(tx.txid, tx));

    WalletTx retrieved = db->getTransaction(tx.txid);
    QCOMPARE(retrieved.state, QString("SIGNED"));
}

void TestWalletDatabase::testListTransactions()
{
    // Add multiple transactions
    for (int i = 0; i < 5; i++) {
        WalletTx tx;
        tx.txid = QString("0x%1").arg(i, 16, 16, QChar('0'));
        tx.direction = (i % 2 == 0) ? "out" : "in";
        tx.fromAccountId = (i % 2 == 0) ? "account-uuid-1" : QString();
        tx.toAddress = QString("anim1receiver%1").arg(i);
        tx.amount = 1'000'000'000 + i;
        tx.fee = 10'000'000;
        tx.state = "CONFIRMED";
        tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch() + i;
        tx.lastUpdateAt = tx.firstSeenAt;
        tx.blockHeight = 100 + i;

        QVERIFY(db->addTransaction(tx));
    }

    QList<WalletTx> all = db->listTransactions();
    QCOMPARE(all.size(), 5);

    QList<WalletTx> accountTxs = db->listTransactions("account-uuid-1");
    QCOMPARE(accountTxs.size(), 3);  // 3 outgoing transactions
}

void TestWalletDatabase::testStateTransitionValidation()
{
    WalletTx tx;
    tx.txid = "0xvalidation";
    tx.direction = "out";
    tx.fromAccountId = "account-uuid-1";
    tx.toAddress = "anim1receiver";
    tx.amount = 1'000'000'000;
    tx.fee = 10'000'000;
    tx.state = "CREATED";
    tx.firstSeenAt = QDateTime::currentMSecsSinceEpoch();
    tx.lastUpdateAt = tx.firstSeenAt;
    tx.blockHeight = -1;

    QVERIFY(db->addTransaction(tx));

    // Valid transition: CREATED -> SIGNED
    tx.state = "SIGNED";
    QVERIFY(db->updateTransaction(tx.txid, tx));

    // Invalid transition: SIGNED -> CONFIRMED (should be SIGNED -> BROADCAST first)
    tx.state = "CONFIRMED";
    QVERIFY(!db->updateTransaction(tx.txid, tx));
}

void TestWalletDatabase::testAddLedgerEntry()
{
    QVERIFY(db->addTransaction(makeTx("0xledger1")));

    LedgerEntry entry;
    entry.txid = "0xledger1";
    entry.accountId = "account-uuid-1";
    entry.asset = "ANM";
    entry.type = "AVAILABLE";
    entry.delta = 1'000'000'000;  // +1 ANM
    entry.stateVersion = db->nextStateVersion();
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();

    QVERIFY(db->addLedgerEntry(entry));

    QList<LedgerEntry> entries = db->getLedgerEntries("0xledger1");
    QCOMPARE(entries.size(), 1);
    QCOMPARE(entries[0].delta, entry.delta);
}

void TestWalletDatabase::testGetBalance()
{
    QString accountId = "account-uuid-1";

    QVERIFY(db->addTransaction(makeTx("0xinit", accountId)));
    QVERIFY(db->addTransaction(makeTx("0xspend", accountId)));

    // Add initial balance
    LedgerEntry entry1;
    entry1.txid = "0xinit";
    entry1.accountId = accountId;
    entry1.asset = "ANM";
    entry1.type = "AVAILABLE";
    entry1.delta = 5'000'000'000;  // +5 ANM
    entry1.stateVersion = db->nextStateVersion();
    entry1.createdAt = QDateTime::currentMSecsSinceEpoch();
    QVERIFY(db->addLedgerEntry(entry1));

    qint64 balance = db->getBalance(accountId, "ANM");
    QCOMPARE(balance, 5'000'000'000);

    // Subtract some balance
    LedgerEntry entry2;
    entry2.txid = "0xspend";
    entry2.accountId = accountId;
    entry2.asset = "ANM";
    entry2.type = "AVAILABLE";
    entry2.delta = -2'000'000'000;  // -2 ANM
    entry2.stateVersion = db->nextStateVersion();
    entry2.createdAt = QDateTime::currentMSecsSinceEpoch();
    QVERIFY(db->addLedgerEntry(entry2));

    balance = db->getBalance(accountId, "ANM");
    QCOMPARE(balance, 3'000'000'000);  // 5 - 2 = 3 ANM
}

void TestWalletDatabase::testBalanceInvariant()
{
    QString accountId = "account-uuid-1";
    QVERIFY(db->addTransaction(makeTx("0xnegative", accountId)));

    // Try to create negative balance
    LedgerEntry entry;
    entry.txid = "0xnegative";
    entry.accountId = accountId;
    entry.asset = "ANM";
    entry.type = "AVAILABLE";
    entry.delta = -1'000'000'000;  // -1 ANM (would make balance negative)
    entry.stateVersion = db->nextStateVersion();
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();

    // Should fail due to balance invariant
    QVERIFY(!db->addLedgerEntry(entry));
}

void TestWalletDatabase::testStateVersion()
{
    qint64 v1 = db->nextStateVersion();
    qint64 v2 = db->nextStateVersion();
    qint64 v3 = db->nextStateVersion();

    QVERIFY(v2 > v1);
    QVERIFY(v3 > v2);
    QCOMPARE(v2, v1 + 1);
    QCOMPARE(v3, v2 + 1);
}

void TestWalletDatabase::testIdempotency()
{
    QString key = "0xtxid:BROADCAST:rpc:1234";

    // First check - should return false (not processed)
    QVERIFY(!db->checkIdempotency(key));

    // Mark as processed
    QVERIFY(db->markProcessed(key));

    // Second check - should return true (already processed)
    QVERIFY(db->checkIdempotency(key));
}

void TestWalletDatabase::testReconciliation()
{
    QString runId = db->startReconciliation();
    QVERIFY(!runId.isEmpty());

    QString beforeJson = R"({"account-1": 1000000000})";
    QString afterJson = R"({"account-1": 2000000000})";
    QVERIFY(db->recordReconciliationSnapshot(runId, beforeJson, afterJson));

    QString changesJson = R"([{"account": "account-1", "delta": 1000000000}])";
    QVERIFY(db->completeReconciliation(runId, changesJson));
}

void TestWalletDatabase::testAtomicTransaction()
{
    QString accountId = "account-uuid-1";
    QVERIFY(db->addTransaction(makeTx("0xatomic", accountId)));

    // Start transaction
    QVERIFY(db->beginTransaction());

    // Add ledger entry
    qint64 version1 = db->nextStateVersion();
    LedgerEntry entry;
    entry.txid = "0xatomic";
    entry.accountId = accountId;
    entry.asset = "ANM";
    entry.type = "AVAILABLE";
    entry.delta = 1'000'000'000;
    entry.stateVersion = version1;
    entry.createdAt = QDateTime::currentMSecsSinceEpoch();
    QVERIFY(db->addLedgerEntry(entry));

    // Rollback
    QVERIFY(db->rollback());

    // Balance should be 0 (entry was rolled back)
    qint64 balance = db->getBalance(accountId, "ANM");
    QCOMPARE(balance, 0);

    // Try again with commit
    // Note: state version counter does not roll back, so we get a gap in sequence
    // This is acceptable as the counter only needs to be monotonic
    QVERIFY(db->beginTransaction());
    qint64 version2 = db->nextStateVersion();
    QVERIFY(version2 > version1);  // Version advanced despite rollback
    entry.stateVersion = version2;
    QVERIFY(db->addLedgerEntry(entry));
    QVERIFY(db->commit());

    // Balance should be updated
    balance = db->getBalance(accountId, "ANM");
    QCOMPARE(balance, 1'000'000'000);
}

QTEST_MAIN(TestWalletDatabase)
#include "test_walletdatabase.moc"
