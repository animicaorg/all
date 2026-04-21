#include "../src/rpc/AnimicaRpcClient.h"
#include "../src/rpc/RpcSettings.h"
#include "../src/wallet/AccountsWidget.h"
#include "../src/wallet/ContractInteractionWidget.h"
#include "../src/wallet/FeeEstimator.h"
#include "../src/wallet/ReceiveWidget.h"
#include "../src/wallet/SendWidget.h"
#include "../src/wallet/SettingsWidget.h"
#include "../src/wallet/TransactionHistoryWidget.h"
#include "../src/wallet/TransactionMonitor.h"
#include "../src/wallet/WalletDatabase.h"
#include "../src/wallet/WalletEngine.h"
#include "../src/wallet/WalletWidget.h"

#include <QComboBox>
#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMetaObject>
#include <QSettings>
#include <QSpinBox>
#include <QTableWidget>
#include <QTemporaryDir>
#include <QtTest/QtTest>

#include <memory>

namespace {

void configureBackendEnvironment()
{
    qputenv("ANIMICA_REPO_ROOT", QByteArray(ANIMICA_REPO_ROOT_PATH));
    QSettings settings;
    settings.remove("WalletQt/security");
    settings.sync();
}

QString isolatedTestRpcEndpoint()
{
    // Avoid external DNS/network dependencies in widget tests.
    return QStringLiteral("http://127.0.0.1:9/rpc");
}

struct WalletTestContext
{
    WalletTestContext()
        : rpcClient()
        , engine(&rpcClient)
    {
        configureBackendEnvironment();
        Q_ASSERT(tempDir.isValid());
        rpcClient.setEndpoint(isolatedTestRpcEndpoint());
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

    void testFeeEstimatorUsesScalarMaxFeeReserve()
    {
        FeeEstimator estimator(nullptr);
        const qint64 slowTierFee = estimator.getGasPrice(FeeEstimator::Slow);
        const qint64 gasLimit = FeeEstimator::standardTransferGas();
        const qint64 reserve = estimator.calculateFee(FeeEstimator::Slow, gasLimit);
        QCOMPARE(reserve, slowTierFee * gasLimit);
    }

    void testHistoryWidgetInitializes()
    {
        WalletTestContext ctx;
        TransactionHistoryWidget history(&ctx.engine);
        QVERIFY(history.findChild<QTableWidget*>());
    }

    void testHistoryWidgetShowsPendingEntriesWithDefaultStatusFilter()
    {
        WalletTestContext ctx;
        const QString walletPath = ctx.engine.walletFilePath();
        QFile walletFile(walletPath);
        QVERIFY(walletFile.open(QIODevice::ReadOnly));
        const QJsonDocument doc = QJsonDocument::fromJson(walletFile.readAll());
        walletFile.close();
        QVERIFY(doc.isObject());

        QJsonObject root = doc.object();
        QJsonArray wallets = root.value("wallets").toArray();
        QVERIFY(!wallets.isEmpty());
        QJsonObject wallet = wallets.first().toObject();
        const QString fromAddress = wallet.value("address").toString();
        QVERIFY(!fromAddress.isEmpty());

        const QString now = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
        QJsonObject pendingTx;
        pendingTx["tx_hash"] = "0xabc123";
        pendingTx["from"] = fromAddress;
        pendingTx["to"] = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq";
        pendingTx["value"] = 1'250'000'000;
        pendingTx["fee_reserved"] = 25'000'000;
        pendingTx["reserve_amount"] = 1'275'000'000;
        pendingTx["nonce"] = 7;
        pendingTx["chain_id"] = 1;
        pendingTx["status"] = "mempool_accepted";
        pendingTx["created_at"] = now;
        pendingTx["updated_at"] = now;

        QJsonArray pending;
        pending.append(pendingTx);
        wallet["pending_txs"] = pending;
        wallets[0] = wallet;
        root["wallets"] = wallets;

        QVERIFY(walletFile.open(QIODevice::WriteOnly | QIODevice::Truncate));
        walletFile.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
        walletFile.close();

        TransactionHistoryWidget history(&ctx.engine);
        QTableWidget* table = history.findChild<QTableWidget*>();
        QVERIFY(table != nullptr);

        history.refresh();
        QTRY_COMPARE(table->rowCount(), 1);
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

    void testRpcReplyFormatsHostedGatewayErrors()
    {
        QCOMPARE(
            RpcReply::describeNetworkError(
                QNetworkReply::UnknownContentError,
                QStringLiteral("Error transferring https://rpc.animica.org/rpc - server replied: Bad Gateway"),
                502,
                QStringLiteral("Bad Gateway")
            ),
            QStringLiteral("Hosted RPC unavailable (HTTP 502 Bad Gateway).")
        );
    }

    void testWalletWidgetExplainsHostedRpcOutage()
    {
        WalletTestContext ctx;
        WalletWidget widget(&ctx.engine, &ctx.rpcClient, ctx.database.get(), ctx.monitor.get());

        QVERIFY(QMetaObject::invokeMethod(
            &widget,
            "handleRpcError",
            Q_ARG(QString, QStringLiteral("Hosted RPC unavailable (HTTP 502 Bad Gateway)."))
        ));

        QLabel* title = widget.findChild<QLabel*>(QStringLiteral("connectionBannerTitle"));
        QLabel* details = widget.findChild<QLabel*>(QStringLiteral("connectionBannerDetails"));
        QVERIFY(title != nullptr);
        QVERIFY(details != nullptr);
        QCOMPARE(title->text(), QStringLiteral("Hosted RPC temporarily unavailable."));
        QVERIFY(details->text().contains(QStringLiteral("server-side outage")));
        QVERIFY(details->text().contains(QStringLiteral("HTTP 502 Bad Gateway")));
    }

    void testWalletWidgetUnlockActionRestoresLockedStore()
    {
        WalletTestContext ctx;
        ctx.engine.lockWallet();
        QVERIFY(ctx.engine.isLocked());

        WalletWidget widget(&ctx.engine, &ctx.rpcClient, ctx.database.get(), ctx.monitor.get());
        QVERIFY(QMetaObject::invokeMethod(&widget, "onUnlockWalletAction"));

        QVERIFY(!ctx.engine.isLocked());
    }

    void testReceiveWidgetShowsUnavailableWhenStoreNotLoaded()
    {
        configureBackendEnvironment();

        AnimicaRpcClient rpcClient;
        rpcClient.setEndpoint(isolatedTestRpcEndpoint());
        WalletEngine engine(&rpcClient);
        ReceiveWidget receive(&engine);

        QComboBox* combo = receive.findChild<QComboBox*>(QStringLiteral("receiveAccountCombo"));
        QVERIFY(combo != nullptr);
        QCOMPARE(combo->count(), 1);
        QCOMPARE(combo->itemText(0), QStringLiteral("(Wallet Unavailable)"));
        QVERIFY(!combo->isEnabled());
    }
};

QTEST_MAIN(TestWalletWidgetSurfaces)
#include "test_wallet_widget.moc"
