#include <QtTest>

#include "AnimicaNodeKit/RpcClient.h"

using namespace animica::nodekit;

class RpcClientTest : public QObject {
    Q_OBJECT

private slots:
    void parsesSuccess();
    void parsesError();
    void parsesInvalid();
};

void RpcClientTest::parsesSuccess() {
    const QByteArray payload = R"({"jsonrpc":"2.0","result":{"height":42},"id":1})";
    QJsonObject result;
    RpcError error;
    QVERIFY(RpcClient::parseResponse(payload, &result, &error));
    QCOMPARE(result.value("height").toInt(), 42);
}

void RpcClientTest::parsesError() {
    const QByteArray payload = R"({"jsonrpc":"2.0","error":{"code":-1,"message":"fail"},"id":1})";
    QJsonObject result;
    RpcError error;
    QVERIFY(!RpcClient::parseResponse(payload, &result, &error));
    QCOMPARE(error.type, RpcErrorType::Rpc);
    QCOMPARE(error.message, QStringLiteral("fail"));
}

void RpcClientTest::parsesInvalid() {
    const QByteArray payload = "not-json";
    QJsonObject result;
    RpcError error;
    QVERIFY(!RpcClient::parseResponse(payload, &result, &error));
    QCOMPARE(error.type, RpcErrorType::Parse);
}

QTEST_MAIN(RpcClientTest)
#include "rpc_client_test.moc"
