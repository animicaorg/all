#include <QtTest>

#include "AnimicaNodeKit/Keystore.h"

using namespace animica::nodekit;

class KeystoreTest : public QObject {
    Q_OBJECT

private slots:
    void encryptRoundTrip();
};

void KeystoreTest::encryptRoundTrip() {
    QTemporaryDir dir;
    QVERIFY(dir.isValid());

    Keystore store;
    store.setStoragePath(dir.filePath("keystore.json.enc"));
    QVERIFY(store.unlock("passphrase"));
    QVERIFY(store.createWallet("Primary"));
    const QVector<WalletEntry> entries = store.wallets();
    QVERIFY(!entries.isEmpty());

    store.lock();
    QVERIFY(store.unlock("passphrase"));
    const QVector<WalletEntry> reloaded = store.wallets();
    QCOMPARE(reloaded.size(), entries.size());
}

QTEST_MAIN(KeystoreTest)
#include "keystore_test.moc"
