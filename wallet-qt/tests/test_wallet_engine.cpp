#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonObject>
#include <QMetaObject>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>

#include "../src/rpc/AnimicaRpcClient.h"
#include "../src/wallet/WalletEngine.h"

namespace {

void configureBackendEnvironment()
{
    qputenv("ANIMICA_REPO_ROOT", QByteArray(ANIMICA_REPO_ROOT_PATH));
}

WalletEngine* makeEngine(AnimicaRpcClient& rpcClient, QObject* parent = nullptr)
{
    configureBackendEnvironment();
    rpcClient.setEndpoint("http://127.0.0.1:8545/rpc");
    return new WalletEngine(&rpcClient, parent);
}

} // namespace

class TestWalletEngine : public QObject
{
    Q_OBJECT

private slots:
    void testCreateWalletAndManageAccounts()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());

        AnimicaRpcClient rpcClient;
        WalletEngine engine(&rpcClient);
        configureBackendEnvironment();

        QVERIFY(engine.createWallet(QString(), tmpDir.path()));
        QVERIFY(engine.isLoaded());
        QVERIFY(!engine.isLocked());
        QVERIFY(QFileInfo::exists(QDir(tmpDir.path()).filePath("wallets.json")));

        const QJsonArray algorithms = engine.supportedAlgorithms();
        QVERIFY(!algorithms.isEmpty());

        bool hasDilithium = false;
        bool hasSphincs = false;
        for (const QJsonValue& value : algorithms) {
            const QString name = value.toObject().value("name").toString();
            hasDilithium = hasDilithium || (name == "dilithium3");
            hasSphincs = hasSphincs || (name == "sphincs_shake_128s");
        }
        QVERIFY(hasDilithium);
        QVERIFY(hasSphincs);

        const WalletAccount primary = engine.createAccount("Primary", 0x1001);
        QVERIFY(!primary.accountId.isEmpty());
        QVERIFY(!primary.address.isEmpty());
        QCOMPARE(primary.label, QString("Primary"));
        QVERIFY(primary.isDefault);
        QVERIFY(engine.validateAddress(primary.address));

        const WalletAccount backup = engine.createAccount("Backup", 0x1002);
        QVERIFY(!backup.accountId.isEmpty());
        QVERIFY(!backup.address.isEmpty());
        QCOMPARE(backup.label, QString("Backup"));
        QVERIFY(!backup.isDefault);

        QCOMPARE(engine.listAccounts().size(), 2);

        QVERIFY(engine.renameAccount(primary.accountId, "Treasury"));
        const WalletAccount renamed = engine.getAccount(primary.accountId);
        QCOMPARE(renamed.label, QString("Treasury"));

        const WalletAccount defaultWallet = engine.setDefaultAccount(backup.accountId);
        QCOMPARE(defaultWallet.accountId, backup.accountId);
        QVERIFY(defaultWallet.isDefault);
        QVERIFY(!engine.getAccount(primary.accountId).isDefault);

        QVERIFY(engine.removeAccount(primary.accountId));
        const QList<WalletAccount> remaining = engine.listAccounts();
        QCOMPARE(remaining.size(), 1);
        QCOMPARE(remaining.first().accountId, backup.accountId);
        QVERIFY(remaining.first().isDefault);
    }

    void testExportImportAndAddressBookPersistence()
    {
        QTemporaryDir sourceDir;
        QTemporaryDir targetDir;
        QVERIFY(sourceDir.isValid());
        QVERIFY(targetDir.isValid());

        AnimicaRpcClient sourceRpcClient;
        WalletEngine sourceEngine(&sourceRpcClient);
        configureBackendEnvironment();

        QVERIFY(sourceEngine.createWallet(QString(), sourceDir.path()));
        const WalletAccount wallet = sourceEngine.createAccount("Imported Source", 0x1001);
        QVERIFY(!wallet.accountId.isEmpty());

        const QJsonObject publicInfo = sourceEngine.exportPublicInfo(wallet.accountId);
        QCOMPARE(publicInfo.value("label").toString(), QString("Imported Source"));
        QCOMPARE(publicInfo.value("address").toString(), wallet.address);

        const QString exportPath = QDir(sourceDir.path()).filePath("single-wallet-export.json");
        QVERIFY(sourceEngine.exportSecretMaterial(wallet.accountId, exportPath));
        QVERIFY(QFileInfo::exists(exportPath));

        AnimicaRpcClient targetRpcClient;
        WalletEngine targetEngine(&targetRpcClient);
        configureBackendEnvironment();

        QVERIFY(targetEngine.createWallet(QString(), targetDir.path()));
        QVERIFY(targetEngine.importWalletsFile(exportPath, true));
        const QList<WalletAccount> importedAccounts = targetEngine.listAccounts();
        QCOMPARE(importedAccounts.size(), 1);
        QCOMPARE(importedAccounts.first().address, wallet.address);

        QVERIFY(!targetEngine.validateAddress("not-an-address"));
        QVERIFY(targetEngine.addContact("Treasury", wallet.address, "Cold reserve"));
        QVERIFY(!targetEngine.addContact("Treasury Duplicate", wallet.address, "Duplicate should fail"));

        const QString contactsJson = QDir(targetDir.path()).filePath("contacts-export.json");
        const QString contactsCsv = QDir(targetDir.path()).filePath("contacts-export.csv");
        const auto jsonExport = targetEngine.exportContactsFile(contactsJson);
        const auto csvExport = targetEngine.exportContactsFile(contactsCsv);
        QVERIFY(jsonExport.ok);
        QVERIFY(csvExport.ok);
        QCOMPARE(jsonExport.exported, 1);
        QCOMPARE(csvExport.exported, 1);

        const QList<Contact> contacts = targetEngine.listContacts();
        QCOMPARE(contacts.size(), 1);
        QCOMPARE(contacts.first().label, QString("Treasury"));
        QCOMPARE(contacts.first().note, QString("Cold reserve"));

        AnimicaRpcClient reopenedRpcClient;
        WalletEngine reopenedEngine(&reopenedRpcClient);
        configureBackendEnvironment();

        QVERIFY(reopenedEngine.openWallet(QDir(targetDir.path()).filePath("wallets.json")));
        const QList<Contact> reopenedContacts = reopenedEngine.listContacts();
        QCOMPARE(reopenedContacts.size(), 1);
        QCOMPARE(reopenedContacts.first().address, wallet.address);

        QTemporaryDir importedContactsDir;
        QVERIFY(importedContactsDir.isValid());
        AnimicaRpcClient importedContactsRpcClient;
        WalletEngine importedContactsEngine(&importedContactsRpcClient);
        configureBackendEnvironment();
        QVERIFY(importedContactsEngine.createWallet(QString(), importedContactsDir.path()));
        const auto importResult = importedContactsEngine.importContactsFile(contactsCsv, false);
        QVERIFY(importResult.ok);
        QCOMPARE(importResult.imported, 1);
        QCOMPARE(importResult.skipped, 0);
        QCOMPARE(importedContactsEngine.listContacts().size(), 1);
        QCOMPARE(importedContactsEngine.listContacts().first().address, wallet.address);
    }

    void testLockUnlockAndManualAutoLock()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());

        AnimicaRpcClient rpcClient;
        WalletEngine engine(&rpcClient);
        configureBackendEnvironment();

        QVERIFY(engine.createWallet(QString(), tmpDir.path()));
        const WalletAccount wallet = engine.createAccount("Primary", 0x1001);
        QVERIFY(!wallet.accountId.isEmpty());

        QSignalSpy lockedSpy(&engine, &WalletEngine::walletLocked);
        QSignalSpy unlockedSpy(&engine, &WalletEngine::walletUnlocked);

        engine.lockWallet();
        QVERIFY(engine.isLocked());
        QCOMPARE(engine.listAccounts().size(), 0);
        QCOMPARE(lockedSpy.count(), 1);

        QVERIFY(engine.unlockWallet(QString()));
        QVERIFY(!engine.isLocked());
        QCOMPARE(engine.listAccounts().size(), 1);
        QCOMPARE(unlockedSpy.count(), 1);

        engine.setAutoLockTimeout(1);
        QVERIFY(QMetaObject::invokeMethod(&engine, "handleAutoLock", Qt::DirectConnection));
        QVERIFY(engine.isLocked());
        QCOMPARE(lockedSpy.count(), 2);
    }

    void testFailedOpenDoesNotMarkEngineLoaded()
    {
        QTemporaryDir tmpDir;
        QVERIFY(tmpDir.isValid());

        const QString blockerPath = QDir(tmpDir.path()).filePath("not-a-directory");
        QFile blocker(blockerPath);
        QVERIFY(blocker.open(QIODevice::WriteOnly | QIODevice::Truncate));
        blocker.write("blocker");
        blocker.close();

        AnimicaRpcClient rpcClient;
        WalletEngine engine(&rpcClient);
        configureBackendEnvironment();

        QVERIFY(!engine.openWallet(blockerPath + "/wallets.json"));
        QVERIFY(!engine.isLoaded());
        QVERIFY(engine.isLocked());
        QCOMPARE(engine.listAccounts().size(), 0);

        const WalletAccount failedAccount = engine.createAccount("Primary", 0x1001);
        QVERIFY(failedAccount.accountId.isEmpty());
        QVERIFY(failedAccount.address.isEmpty());

        QVERIFY(engine.createWallet(QString(), tmpDir.path()));
        QVERIFY(engine.isLoaded());
        QVERIFY(!engine.isLocked());
    }
};

QTEST_MAIN(TestWalletEngine)
#include "test_wallet_engine.moc"
