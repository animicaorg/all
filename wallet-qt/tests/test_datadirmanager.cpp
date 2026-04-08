#include "../src/platform/DataDirManager.h"
#include <QtTest/QtTest>
#include <QTemporaryDir>
#include <QFile>

class TestDataDirManager : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void cleanupTestCase();
    
    void testGetDefaultDataDir();
    void testSetCustomDataDir();
    void testValidateDataDir();
    void testEnsureDirectoriesExist();
    void testNetworkMarker();
    void testNetworkCompatibility();
    void testGetPaths();

private:
    QTemporaryDir* m_tempDir;
};

void TestDataDirManager::initTestCase()
{
    m_tempDir = new QTemporaryDir();
    QVERIFY(m_tempDir->isValid());
}

void TestDataDirManager::cleanupTestCase()
{
    delete m_tempDir;
}

void TestDataDirManager::testGetDefaultDataDir()
{
    QString defaultDir = DataDirManager::getDefaultDataDir();
    QVERIFY(!defaultDir.isEmpty());
    
#if defined(Q_OS_LINUX)
    QVERIFY(defaultDir.contains(".animica"));
#elif defined(Q_OS_MACOS)
    QVERIFY(defaultDir.contains("Library/Application Support/Animica"));
#elif defined(Q_OS_WIN)
    QVERIFY(defaultDir.contains("AppData"));
#endif
}

void TestDataDirManager::testSetCustomDataDir()
{
    DataDirManager manager;
    QString customPath = m_tempDir->filePath("custom");
    
    QVERIFY(manager.setDataDir(customPath, false));
    QCOMPARE(manager.getDataDir(), customPath);
}

void TestDataDirManager::testValidateDataDir()
{
    DataDirManager manager;
    QString errorMsg;
    
    // Valid directory
    QString validPath = m_tempDir->filePath("valid");
    QVERIFY(manager.validateDataDir(validPath, errorMsg));
    
    // Relative path should fail
    QVERIFY(!manager.validateDataDir("relative/path", errorMsg));
    QVERIFY(!errorMsg.isEmpty());
}

void TestDataDirManager::testEnsureDirectoriesExist()
{
    DataDirManager manager;
    QString testPath = m_tempDir->filePath("test_dirs");
    manager.setDataDir(testPath, false);
    
    QVERIFY(manager.ensureDirectoriesExist());
    
    // Check directories were created
    QVERIFY(QDir(testPath).exists());
    QVERIFY(QDir(manager.getLogsDir()).exists());

#if !WALLET_REMOTE_RPC_ONLY
    QVERIFY(QDir(manager.getSnapshotsDir()).exists());
    QVERIFY(QDir(manager.getChainDataDir(1)).exists());
    QVERIFY(QDir(manager.getChainDataDir(2)).exists());
    QVERIFY(QDir(manager.getChainDataDir(1337)).exists());
#endif
}

void TestDataDirManager::testNetworkMarker()
{
    DataDirManager manager;
    QString testPath = m_tempDir->filePath("network_test");
    manager.setDataDir(testPath, false);
    manager.ensureDirectoriesExist();
    
    // Initially no network marker
    QVERIFY(manager.getStoredNetworkId().isEmpty());
    
    // Set network marker
    QVERIFY(manager.setStoredNetworkId("devnet"));
    QCOMPARE(manager.getStoredNetworkId(), QString("devnet"));
    
    // Update network marker
    QVERIFY(manager.setStoredNetworkId("testnet"));
    QCOMPARE(manager.getStoredNetworkId(), QString("testnet"));
}

void TestDataDirManager::testNetworkCompatibility()
{
    DataDirManager manager;
    QString testPath = m_tempDir->filePath("compat_test");
    manager.setDataDir(testPath, false);
    manager.ensureDirectoriesExist();
    
    QString errorMsg;
    
    // No stored network - any network is compatible
    QVERIFY(manager.checkNetworkCompatibility("mainnet", errorMsg));
    QVERIFY(manager.checkNetworkCompatibility("testnet", errorMsg));
    
    // Set network to devnet
    manager.setStoredNetworkId("devnet");
    
    // Same network is compatible
    QVERIFY(manager.checkNetworkCompatibility("devnet", errorMsg));
    
    // Different network is not compatible
    QVERIFY(!manager.checkNetworkCompatibility("mainnet", errorMsg));
    QVERIFY(!errorMsg.isEmpty());
}

void TestDataDirManager::testGetPaths()
{
    DataDirManager manager;
    QString testPath = m_tempDir->filePath("paths_test");
    manager.setDataDir(testPath, false);
    
    // Test various path getters
    QVERIFY(manager.getWalletsFilePath().endsWith("wallets.json"));
    QVERIFY(manager.getLogsDir().endsWith("logs"));
    QVERIFY(manager.getSnapshotsDir().endsWith("snapshots"));
    QVERIFY(manager.getChainDataDir(1).endsWith("chain-1"));
    QVERIFY(manager.getChainDataDir(2).endsWith("chain-2"));
    QVERIFY(manager.getChainDataDir(1337).endsWith("chain-1337"));
}

QTEST_MAIN(TestDataDirManager)
#include "test_datadirmanager.moc"
