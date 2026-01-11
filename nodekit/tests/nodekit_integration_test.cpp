#include <QtTest>

#include "AnimicaNodeKit/NodeKit.h"

using namespace animica::nodekit;

class NodeKitIntegrationTest : public QObject {
    Q_OBJECT

private slots:
    void startNode();
};

void NodeKitIntegrationTest::startNode() {
    NodeKit kit;
    NodeKitConfig config;
    config.appId = "test";
    config.chainId = "dev";
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    config.dataDir = dir.path();
    config.rpcPort = 18400;
    kit.configure(config);

    if (!kit.processManager()->start()) {
        QSKIP("Animica node binary not available for integration test");
    }

    QTRY_VERIFY_WITH_TIMEOUT(kit.processManager()->isRunning(), 5000);
    kit.processManager()->stop();
    QVERIFY(!kit.processManager()->isRunning());
}

QTEST_MAIN(NodeKitIntegrationTest)
#include "nodekit_integration_test.moc"
