#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRandomGenerator>
#include <QTemporaryFile>
#include <QTest>

#include "../src/wallet/EncryptedKeystore.h"

class TestKeystoreSecurity : public QObject
{
    Q_OBJECT

private slots:
    void testWrongPasswordRejected()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        const QString path = tmpFile.fileName();
        tmpFile.close();

        const QByteArray payload = "secret data";
        const QString password = "correct_password_123";

        QVERIFY(EncryptedKeystore::create(path, payload, password));

        EncryptedKeystore keystore;
        QVERIFY(keystore.load(path));

        QByteArray decrypted;
        QVERIFY(!keystore.unlock("wrong_password", decrypted));
        QVERIFY(keystore.unlock(password, decrypted));
        QCOMPARE(decrypted, payload);
    }

    void testFileTamperingDetected()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        const QString path = tmpFile.fileName();
        tmpFile.close();

        QVERIFY(EncryptedKeystore::create(path, QByteArray("important secret"), "password123"));

        QFile file(path);
        QVERIFY(file.open(QIODevice::ReadOnly));
        const QByteArray fileData = file.readAll();
        file.close();

        QJsonParseError parseError;
        QJsonDocument doc = QJsonDocument::fromJson(fileData, &parseError);
        QVERIFY(parseError.error == QJsonParseError::NoError);
        QVERIFY(doc.isObject());

        QJsonObject obj = doc.object();
        QString ciphertext = obj.value("encrypted_payload").toString();
        QVERIFY(ciphertext.size() > 8);
        ciphertext[4] = ciphertext[4] == QChar('A') ? QChar('B') : QChar('A');
        obj["encrypted_payload"] = ciphertext;

        const QByteArray tamperedData = QJsonDocument(obj).toJson(QJsonDocument::Compact);
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        QCOMPARE(file.write(tamperedData), tamperedData.size());
        file.close();

        EncryptedKeystore keystore;
        QVERIFY(keystore.load(path));

        QByteArray decrypted;
        QVERIFY(!keystore.unlock("password123", decrypted));
    }

    void testRoundtripEncryptionAndPasswordChange()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        const QString path = tmpFile.fileName();
        tmpFile.close();

        QList<int> sizes = {16, 100, 1000, 4096};
        for (int size : sizes) {
            QByteArray payload(size, Qt::Uninitialized);
            for (int i = 0; i < size; ++i) {
                payload[i] = static_cast<char>(QRandomGenerator::global()->bounded(256));
            }

            const QString oldPassword = QString("password_%1").arg(size);
            const QString newPassword = QString("new_password_%1").arg(size);

            QVERIFY(EncryptedKeystore::create(path, payload, oldPassword));

            EncryptedKeystore keystore;
            QVERIFY(keystore.load(path));
            QVERIFY(keystore.changePassword(oldPassword, newPassword));

            QByteArray decrypted;
            QVERIFY(!keystore.unlock(oldPassword, decrypted));
            QVERIFY(keystore.unlock(newPassword, decrypted));
            QCOMPARE(decrypted, payload);
        }
    }
};

QTEST_MAIN(TestKeystoreSecurity)
#include "test_keystore_security.moc"
