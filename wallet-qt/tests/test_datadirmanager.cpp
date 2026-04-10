#include "../src/platform/AppPaths.h"
#include "../src/platform/DataDirManager.h"

#include <QFile>
#include <QTemporaryDir>
#include <QtTest/QtTest>

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
    void testGetPaths();

private:
    QTemporaryDir* m_tempDir = nullptr;
};

void TestDataDirManager::initTestCase()
{
    QCoreApplication::setApplicationName("AnimicaWalletTest");
    m_tempDir = new QTemporaryDir();
    QVERIFY(m_tempDir->isValid());
}

void TestDataDirManager::cleanupTestCase()
{
    delete m_tempDir;
}

void TestDataDirManager::testGetDefaultDataDir()
{
    const QString defaultDir = DataDirManager::getDefaultDataDir();
    QVERIFY(!defaultDir.isEmpty());
    QCOMPARE(defaultDir, AppPaths::walletDir());
}

void TestDataDirManager::testSetCustomDataDir()
{
    DataDirManager manager;
    const QString customPath = m_tempDir->filePath("custom");

    QVERIFY(manager.setDataDir(customPath, false));
    QCOMPARE(manager.getDataDir(), customPath);
}

void TestDataDirManager::testValidateDataDir()
{
    DataDirManager manager;
    QString errorMsg;

    const QString validPath = m_tempDir->filePath("valid");
    QVERIFY(manager.validateDataDir(validPath, errorMsg));

    QVERIFY(!manager.validateDataDir("relative/path", errorMsg));
    QVERIFY(!errorMsg.isEmpty());
}

void TestDataDirManager::testEnsureDirectoriesExist()
{
    DataDirManager manager;
    const QString testPath = m_tempDir->filePath("test_dirs");
    manager.setDataDir(testPath, false);

    QVERIFY(manager.ensureDirectoriesExist());
    QVERIFY(QDir(testPath).exists());
    QVERIFY(QDir(manager.getLogsDir()).exists());
    QVERIFY(!QDir(testPath).exists("chain-1"));
    QVERIFY(!QDir(testPath).exists("snapshots"));
}

void TestDataDirManager::testGetPaths()
{
    DataDirManager manager;
    const QString testPath = m_tempDir->filePath("paths_test");
    manager.setDataDir(testPath, false);

    QVERIFY(manager.getWalletsFilePath().endsWith("wallets.json"));
    QVERIFY(manager.getLogsDir().endsWith("logs"));
}

QTEST_MAIN(TestDataDirManager)
#include "test_datadirmanager.moc"
