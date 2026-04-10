#include "../src/rpc/AnimicaRpcClient.h"
#include "../src/rpc/RpcSettings.h"
#include "../src/wallet/AccountsWidget.h"
#include "../src/wallet/ContractInteractionWidget.h"
#include "../src/wallet/SendWidget.h"
#include "../src/wallet/SettingsWidget.h"
#include "../src/wallet/TransactionHistoryWidget.h"
#include "../src/wallet/TransactionMonitor.h"
#include "../src/wallet/WalletDatabase.h"
#include "../src/wallet/WalletEngine.h"

#include <QComboBox>
#include <QLabel>
#include <QLineEdit>
#include <QSpinBox>
#include <QTableWidget>
#include <QTemporaryDir>
#include <QtTest/QtTest>

#include <memory>

namespace {

void configureBackendEnvironment()
{
    qputenv("ANIMICA_REPO_ROOT", QByteArray(ANIMICA_REPO_ROOT_PATH));
}

struct WalletTestContext
{
    WalletTestContext()
        : rpcClient()
        , engine(&rpcClient)
    {
        configureBackendEnvironment();
        Q_ASSERT(tempDir.isValid());
        rpcClient.setEndpoint(RpcSettings::canonicalRpcUrl());
        rpcClient.setTimeout(250);
        rpcClient.setRetryPolicy(0, 0);
        const bool walletCreated = engine.createWallet(QString(), tempDir.path());
        Q_ASSERT(walletCreated);
        const WalletAccount account = engine.createAccount("Primary", 0x1001);
        Q_ASSERT(!account.address.isEmpty());
        database = std::make_unique<WalletDatabase>(tempDir.filePath("wallet.db"));
        Q_ASSERT(database->initialize());
        monitor = std::make_unique<TransactionMonitor>(&rpcClient, database.get());
    }

    QTemporaryDir tempDir;
    AnimicaRpcClient rpcClient;
    WalletEngine engine;
    std::unique_ptr<WalletDatabase> database;
    std::unique_ptr<TransactionMonitor> monitor;
};

} // namespace

class TestWalletWidgetSurfaces : public QObject
{
    Q_OBJECT

private slots:
    void testAccountsWidgetInitializes()
    {
        WalletTestContext ctx;
        AccountsWidget accounts(&ctx.engine);
        QVERIFY(accounts.findChild<QTableWidget*>());
    }

    void testSendWidgetInitializes()
    {
        WalletTestContext ctx;
        SendWidget send(&ctx.engine, &ctx.rpcClient, nullptr, nullptr);
        QVERIFY(send.findChild<QComboBox*>());
        QVERIFY(send.findChild<QLineEdit*>());
    }

    void testHistoryWidgetInitializes()
    {
        WalletTestContext ctx;
        TransactionHistoryWidget history(&ctx.engine);
        QVERIFY(history.findChild<QTableWidget*>());
    }

    void testContractWidgetInitializes()
    {
        WalletTestContext ctx;
        ContractInteractionWidget contracts(&ctx.engine);
        QVERIFY(contracts.findChild<QComboBox*>());
        QVERIFY(contracts.findChild<QLineEdit*>());
    }

    void testSettingsSurfaceShowsCanonicalHostedEndpoint()
    {
        configureBackendEnvironment();

        SettingsWidget settings("/tmp/test-wallets.json", "/tmp/test-wallet-data");
        QVERIFY(settings.findChild<QSpinBox*>());

        const QList<QLabel*> labels = settings.findChildren<QLabel*>();
        bool sawCanonicalEndpoint = false;
        bool sawMainnet = false;
        for (QLabel* label : labels) {
            if (label->text() == RpcSettings::canonicalRpcUrl()) {
                sawCanonicalEndpoint = true;
            }
            if (label->text() == QString("Animica Mainnet")) {
                sawMainnet = true;
            }
        }

        QVERIFY(sawCanonicalEndpoint);
        QVERIFY(sawMainnet);
    }
};

QTEST_MAIN(TestWalletWidgetSurfaces)
#include "test_wallet_widget.moc"
