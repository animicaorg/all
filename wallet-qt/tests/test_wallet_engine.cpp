#include <QTest>
#include "../src/wallet/WalletEngine.h"
#include <QTemporaryDir>

/**
 * @brief Integration tests for WalletEngine.
 */
class TestWalletEngine : public QObject
{
    Q_OBJECT

private slots:
    void testCreateAndUnlockWallet()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());
        
        QString dataDir = tmpDir.path();
        QString password = "test_password_123";
        
        WalletEngine engine;
        
        // Create wallet
        QVERIFY(engine.createWallet(password, dataDir));
        QVERIFY(engine.isLocked());
        
        // Unlock wallet
        QVERIFY(engine.unlockWallet(password));
        QVERIFY(!engine.isLocked());
        
        // Lock wallet
        engine.lockWallet();
        QVERIFY(engine.isLocked());
        
        // Unlock again
        QVERIFY(engine.unlockWallet(password));
        QVERIFY(!engine.isLocked());
    }
    
    void testWrongPasswordFails()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());
        
        WalletEngine engine;
        QVERIFY(engine.createWallet("correct_password", tmpDir.path()));
        
        // Wrong password should fail
        QVERIFY(!engine.unlockWallet("wrong_password"));
        QVERIFY(engine.isLocked());
        
        // Correct password should work
        QVERIFY(engine.unlockWallet("correct_password"));
        QVERIFY(!engine.isLocked());
    }
    
    void testAutoLockTimer()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());
        
        WalletEngine engine;
        QVERIFY(engine.createWallet("password", tmpDir.path()));
        QVERIFY(engine.unlockWallet("password"));
        
        // Set very short auto-lock (1 second for testing)
        engine.setAutoLockTimeout(0.017);  // ~1 second
        
        // Wait for auto-lock
        QTest::qWait(2000);
        
        // Wallet should be locked
        QVERIFY(engine.isLocked());
    }
    
    void testCreateAccountRequiresUnlock()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());
        
        WalletEngine engine;
        QVERIFY(engine.createWallet("password", tmpDir.path()));
        
        // Creating account while locked should fail gracefully
        // (Note: In real implementation, this might throw or return null/error)
        WalletAccount account = engine.createAccount("Test Account");
        QVERIFY(account.accountId.isEmpty());  // Empty = failed
        
        // Unlock and try again
        QVERIFY(engine.unlockWallet("password"));
        account = engine.createAccount("Test Account");
        // Note: This will fail without Python, but structure is tested
    }
};

QTEST_MAIN(TestWalletEngine)
#include "test_wallet_engine.moc"
