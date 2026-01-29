#include <QtTest/QtTest>
#include "diagnostics/Redactor.h"

class TestRedactor : public QObject
{
    Q_OBJECT

private slots:
    void testPasswordRedaction()
    {
        QString input = "rpcpassword=mysecret123";
        QString output = Redactor::redact(input);
        QVERIFY(output.contains("***REDACTED***"));
        QVERIFY(!output.contains("mysecret123"));
    }

    void testTokenRedaction()
    {
        QString input = "ANIMICA_RPC_ADMIN_TOKEN=abcd1234efgh5678";
        QString output = Redactor::redact(input);
        QVERIFY(output.contains("***REDACTED***"));
        QVERIFY(!output.contains("abcd1234efgh5678"));
    }

    void testPrivateKeyRedaction()
    {
        QString input = "private_key: 0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef";
        QString output = Redactor::redact(input);
        QVERIFY(output.contains("***REDACTED***"));
        QVERIFY(!output.contains("1234567890abcdef"));
    }

    void testJsonKeyRedaction()
    {
        QString input = R"({"privateKey": "abcd1234", "balance": 1000})";
        QString output = Redactor::redact(input);
        QVERIFY(output.contains("***REDACTED***"));
        QVERIFY(!output.contains("abcd1234"));
        QVERIFY(output.contains("balance")); // Non-sensitive field preserved
    }

    void testSensitiveDataDetection()
    {
        QVERIFY(Redactor::containsSensitiveData("password=secret"));
        QVERIFY(Redactor::containsSensitiveData("private_key: 0x123456"));
        QVERIFY(!Redactor::containsSensitiveData("balance: 1000"));
        QVERIFY(!Redactor::containsSensitiveData("height: 12345"));
    }

    void testNonSensitiveDataPreserved()
    {
        QString input = "Block height: 12345, hash: 0xabcd";
        QString output = Redactor::redact(input);
        QCOMPARE(output, input); // Should be unchanged
    }

    void testHttpHeaderRedaction()
    {
        QString input = "X-Animica-Admin-Token: bearer_token_here";
        QString output = Redactor::redact(input);
        QVERIFY(output.contains("***REDACTED***"));
        QVERIFY(!output.contains("bearer_token_here"));
    }

    void testMultipleSecretsRedacted()
    {
        QString input = "password=secret1 and token=secret2";
        QString output = Redactor::redact(input);
        QVERIFY(!output.contains("secret1"));
        QVERIFY(!output.contains("secret2"));
        int redactedCount = output.count("***REDACTED***");
        QVERIFY(redactedCount >= 2);
    }
};

QTEST_MAIN(TestRedactor)
#include "test_redactor.moc"
