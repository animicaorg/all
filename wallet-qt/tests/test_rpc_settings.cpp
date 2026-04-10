#include "../src/rpc/RpcSettings.h"

#include <QSettings>
#include <QtTest/QtTest>

class TestRpcSettings : public QObject
{
    Q_OBJECT

private slots:
    void init()
    {
        QSettings settings;
        settings.remove("RpcEndpoint");
        settings.sync();
    }

    void testCanonicalDefaults()
    {
        RpcSettings settings;
        const RpcEndpointSettings endpoint = settings.load();

        QCOMPARE(RpcSettings::canonicalNetwork(), QString("mainnet"));
        QCOMPARE(RpcSettings::canonicalChainId(), 1);
        QCOMPARE(RpcSettings::toDisplayUrl(endpoint), QString("https://rpc.animica.org/rpc"));
        QVERIFY(RpcSettings::isDefault(endpoint));
    }

    void testLegacyOverridesAreIgnored()
    {
        QSettings settings;
        settings.beginGroup("RpcEndpoint");
        settings.setValue("scheme", "http");
        settings.setValue("host", "127.0.0.1");
        settings.setValue("port", 8545);
        settings.setValue("path", "/rpc");
        settings.endGroup();
        settings.sync();

        RpcSettings rpcSettings;
        QCOMPARE(RpcSettings::toDisplayUrl(rpcSettings.load()), QString("https://rpc.animica.org/rpc"));
    }
};

QTEST_MAIN(TestRpcSettings)
#include "test_rpc_settings.moc"
