#include <QTest>
#include <QTemporaryFile>
#include "../src/wallet/EncryptedKeystore.h"

/**
 * @brief Security tests for EncryptedKeystore.
 */
class TestKeystoreSecurity : public QObject
{
    Q_OBJECT

private slots:
    void testWrongPasswordRejected()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        QString path = tmpFile.fileName();
        tmpFile.close();
        
        QByteArray payload = "secret data";
        QString password = "correct_password_123";
        
        QVERIFY(EncryptedKeystore::create(path, payload, password));
        
        QByteArray decrypted;
        QVERIFY(!EncryptedKeystore::unlock(path, "wrong_password", decrypted));
        QVERIFY(EncryptedKeystore::unlock(path, password, decrypted));
        QCOMPARE(decrypted, payload);
    }
    
    void testFileTamperingDetected()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        QString path = tmpFile.fileName();
        tmpFile.close();
        
        QByteArray payload = "important secret";
        QString password = "password123";
        
        QVERIFY(EncryptedKeystore::create(path, payload, password));
        
        // Tamper with file
        QFile file(path);
        QVERIFY(file.open(QIODevice::ReadOnly));
        QByteArray fileData = file.readAll();
        file.close();
        
        if (fileData.size() > 100) {
            fileData[fileData.size() / 2] ^= 0xFF;
        }
        
        QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Truncate));
        file.write(fileData);
        file.close();
        
        QByteArray decrypted;
        QVERIFY(!EncryptedKeystore::unlock(path, password, decrypted));
    }
    
    void testRoundtripEncryption()
    {
        QTemporaryFile tmpFile;
        QVERIFY(tmpFile.open());
        QString path = tmpFile.fileName();
        tmpFile.close();
        
        QList<int> sizes = {16, 100, 1000, 4096};
        
        for (int size : sizes) {
            QByteArray payload(size, 0);
            for (int i = 0; i < size; i++) {
                payload[i] = static_cast<char>(qrand() % 256);
            }
            
            QString password = QString("password_%1").arg(size);
            
            QVERIFY(EncryptedKeystore::create(path, payload, password));
            
            QByteArray decrypted;
            QVERIFY(EncryptedKeystore::unlock(path, password, decrypted));
            QCOMPARE(decrypted, payload);
        }
    }
};

QTEST_MAIN(TestKeystoreSecurity)
#include "test_keystore_security.moc"
