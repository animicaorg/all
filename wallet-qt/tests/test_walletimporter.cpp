#include "../src/wallet/WalletImporter.h"
#include <QtTest/QtTest>
#include <QTemporaryDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

class TestWalletImporter : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase();
    void cleanupTestCase();
    
    void testValidateValidFile();
    void testValidateInvalidJson();
    void testValidateMissingFields();
    void testImportNew();
    void testImportReplace();
    void testImportMerge();
    void testAtomicWrite();
    void testBackup();

private:
    QTemporaryDir* m_tempDir;
    QString createSampleWalletFile(const QString& path, int walletCount);
};

void TestWalletImporter::initTestCase()
{
    m_tempDir = new QTemporaryDir();
    QVERIFY(m_tempDir->isValid());
}

void TestWalletImporter::cleanupTestCase()
{
    delete m_tempDir;
}

QString TestWalletImporter::createSampleWalletFile(const QString& path, int walletCount)
{
    QJsonObject root;
    root["version"] = 1;
    
    QJsonArray wallets;
    for (int i = 0; i < walletCount; ++i) {
        QJsonObject wallet;
        wallet["label"] = QString("wallet_%1").arg(i);
        wallet["address"] = QString("anim1test%1").arg(i);
        wallet["alg_id"] = 4098;
        wallet["alg_name"] = "dilithium3";
        wallet["public_key_hex"] = QString("pub%1").arg(i).repeated(16);
        wallet["secret_key_hex"] = QString("sec%1").arg(i).repeated(16);
        wallet["created_at"] = "2026-01-29T00:00:00Z";
        wallets.append(wallet);
    }
    
    root["wallets"] = wallets;
    
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        return QString();
    }
    
    QJsonDocument doc(root);
    file.write(doc.toJson(QJsonDocument::Indented));
    file.close();
    
    return path;
}

void TestWalletImporter::testValidateValidFile()
{
    QString filePath = m_tempDir->filePath("valid.json");
    createSampleWalletFile(filePath, 3);
    
    WalletImporter importer;
    auto result = importer.validateWalletFile(filePath);
    
    QVERIFY(result.valid);
    QCOMPARE(result.walletCount, 3);
    QVERIFY(result.errorMessage.isEmpty());
}

void TestWalletImporter::testValidateInvalidJson()
{
    QString filePath = m_tempDir->filePath("invalid.json");
    QFile file(filePath);
    file.open(QIODevice::WriteOnly);
    file.write("{ invalid json }");
    file.close();
    
    WalletImporter importer;
    auto result = importer.validateWalletFile(filePath);
    
    QVERIFY(!result.valid);
    QVERIFY(!result.errorMessage.isEmpty());
}

void TestWalletImporter::testValidateMissingFields()
{
    QString filePath = m_tempDir->filePath("missing_fields.json");
    
    QJsonObject root;
    root["version"] = 1;
    QJsonArray wallets;
    
    QJsonObject wallet;
    wallet["address"] = "anim1test";
    // Missing public_key_hex and secret_key_hex
    wallets.append(wallet);
    
    root["wallets"] = wallets;
    
    QFile file(filePath);
    file.open(QIODevice::WriteOnly);
    QJsonDocument doc(root);
    file.write(doc.toJson());
    file.close();
    
    WalletImporter importer;
    auto result = importer.validateWalletFile(filePath);
    
    QVERIFY(!result.valid);
    QVERIFY(result.errorMessage.contains("missing"));
}

void TestWalletImporter::testImportNew()
{
    QString sourcePath = m_tempDir->filePath("source.json");
    QString targetPath = m_tempDir->filePath("target.json");
    
    createSampleWalletFile(sourcePath, 2);
    
    WalletImporter importer;
    auto result = importer.importWallets(
        sourcePath,
        targetPath,
        WalletImporter::ConflictResolution::Replace
    );
    
    QVERIFY(result.success);
    QCOMPARE(result.walletsImported, 2);
    QVERIFY(QFile::exists(targetPath));
}

void TestWalletImporter::testImportReplace()
{
    QString sourcePath = m_tempDir->filePath("source_replace.json");
    QString targetPath = m_tempDir->filePath("target_replace.json");
    
    createSampleWalletFile(targetPath, 1);
    createSampleWalletFile(sourcePath, 3);
    
    WalletImporter importer;
    auto result = importer.importWallets(
        sourcePath,
        targetPath,
        WalletImporter::ConflictResolution::Replace
    );
    
    QVERIFY(result.success);
    QCOMPARE(result.walletsImported, 3);
    QVERIFY(!result.backupPath.isEmpty());
    QVERIFY(QFile::exists(result.backupPath));
}

void TestWalletImporter::testImportMerge()
{
    QString sourcePath = m_tempDir->filePath("source_merge.json");
    QString targetPath = m_tempDir->filePath("target_merge.json");
    
    // Create target with wallet_0
    createSampleWalletFile(targetPath, 1);
    
    // Create source with wallet_0 and wallet_1 (wallet_0 is duplicate)
    createSampleWalletFile(sourcePath, 2);
    
    WalletImporter importer;
    auto result = importer.importWallets(
        sourcePath,
        targetPath,
        WalletImporter::ConflictResolution::Merge
    );
    
    QVERIFY(result.success);
    QCOMPARE(result.walletsImported, 1);  // Only wallet_1 imported
    QCOMPARE(result.walletsSkipped, 1);   // wallet_0 skipped
}

void TestWalletImporter::testAtomicWrite()
{
    QString targetPath = m_tempDir->filePath("atomic.json");
    QString sourcePath = m_tempDir->filePath("atomic_source.json");
    
    createSampleWalletFile(sourcePath, 1);
    
    WalletImporter importer;
    auto result = importer.importWallets(
        sourcePath,
        targetPath,
        WalletImporter::ConflictResolution::Replace
    );
    
    QVERIFY(result.success);
    
    // Verify no .tmp file left behind
    QString tempPath = m_tempDir->filePath(".wallets.json.tmp");
    QVERIFY(!QFile::exists(tempPath));
    
    // Verify target file exists and is valid JSON
    QVERIFY(QFile::exists(targetPath));
    
    QFile file(targetPath);
    QVERIFY(file.open(QIODevice::ReadOnly));
    QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
    QVERIFY(!doc.isNull());
}

void TestWalletImporter::testBackup()
{
    QString targetPath = m_tempDir->filePath("backup_test.json");
    createSampleWalletFile(targetPath, 1);
    
    WalletImporter importer;
    QString backupPath = importer.createBackup(targetPath);
    
    QVERIFY(!backupPath.isEmpty());
    QVERIFY(QFile::exists(backupPath));
    QVERIFY(backupPath.contains(".bak."));
    
    // Verify backup contains same data
    QFile original(targetPath);
    QFile backup(backupPath);
    
    QVERIFY(original.open(QIODevice::ReadOnly));
    QVERIFY(backup.open(QIODevice::ReadOnly));
    
    QCOMPARE(original.readAll(), backup.readAll());
}

QTEST_MAIN(TestWalletImporter)
#include "test_walletimporter.moc"
