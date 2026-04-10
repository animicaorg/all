#include <QComboBox>
#include <QDir>
#include <QFileInfo>
#include <QImage>
#include <QLabel>
#include <QLineEdit>
#include <QMutex>
#include <QMutexLocker>
#include <QProcessEnvironment>
#include <QPushButton>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QTest>

#include "../src/rpc/AnimicaRpcClient.h"
#include "../src/rpc/RpcSettings.h"
#include "../src/wallet/ReceiveQrService.h"
#include "../src/wallet/ReceiveWidget.h"
#include "../src/wallet/WalletEngine.h"

namespace {

void configureBackendEnvironment()
{
    qputenv("ANIMICA_REPO_ROOT", QByteArray(ANIMICA_REPO_ROOT_PATH));
}

QString localPython()
{
#ifdef Q_OS_WIN
    const QString repoPython = QDir(QStringLiteral(ANIMICA_REPO_ROOT_PATH)).filePath(".venv/Scripts/python.exe");
#else
    const QString repoPython = QDir(QStringLiteral(ANIMICA_REPO_ROOT_PATH)).filePath(".venv/bin/python");
#endif
    if (QFileInfo::exists(repoPython)) {
        return repoPython;
    }

    const QString python3 = QStandardPaths::findExecutable("python3");
    if (!python3.isEmpty()) {
        return python3;
    }
    return QStandardPaths::findExecutable("python");
}

class FakeReceiveQrService : public ReceiveQrService
{
public:
    ReceiveQrResult generate(const ReceiveQrRequest& request) const override
    {
        QMutexLocker locker(&m_mutex);
        ++m_callCount;
        m_lastRequest = request;

        ReceiveQrResult result;
        result.status = ReceiveQrResult::Status::Success;
        result.payload = buildPayload(request);
        result.image = QImage(64, 64, QImage::Format_ARGB32);
        result.image.fill(Qt::white);
        for (int y = 8; y < 56; y += 8) {
            for (int x = 8; x < 56; x += 8) {
                if (((x + y) / 8) % 2 == 0) {
                    result.image.setPixelColor(x, y, Qt::black);
                }
            }
        }
        return result;
    }

    int callCount() const
    {
        QMutexLocker locker(&m_mutex);
        return m_callCount;
    }

    ReceiveQrRequest lastRequest() const
    {
        QMutexLocker locker(&m_mutex);
        return m_lastRequest;
    }

private:
    mutable QMutex m_mutex;
    mutable int m_callCount = 0;
    mutable ReceiveQrRequest m_lastRequest;
};

class MissingDependencyWidgetService : public ReceiveQrService
{
public:
    ReceiveQrResult generate(const ReceiveQrRequest& request) const override
    {
        ReceiveQrResult result;
        result.status = ReceiveQrResult::Status::DependencyMissing;
        result.payload = buildPayload(request);
        result.errorSummary = "QR generation dependency missing.";
        result.errorDetails = "Install the bundled wallet_qt Python extras before packaging.";
        return result;
    }
};

class LocalPythonReceiveQrService : public ReceiveQrService
{
protected:
    QString resolvePythonInterpreter() const override
    {
        return localPython();
    }

    QProcessEnvironment baseEnvironment() const override
    {
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert("PYTHONPATH", QDir(QStringLiteral(ANIMICA_REPO_ROOT_PATH)).filePath("python"));
        env.insert("ANIMICA_WALLET_QR_FORCE_IMPORT_ERROR", "1");
        return env;
    }
};

} // namespace

class TestReceiveQr : public QObject
{
    Q_OBJECT

private slots:
    void testBuildPayloadUsesAnimicaUri();
    void testNormalizeAmount();
    void testSavePngWritesFile();
    void testDependencyMissingFailureIsActionable();
    void testReceiveWidgetRefreshesQrOnInputChanges();
    void testReceiveWidgetShowsDependencyFailure();
};

void TestReceiveQr::testBuildPayloadUsesAnimicaUri()
{
    ReceiveQrRequest request;
    request.address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq";
    QCOMPARE(ReceiveQrService::buildPayload(request),
             QString("animica:anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"));

    request.amount = "1.25";
    request.message = "Invoice 42";
    QCOMPARE(ReceiveQrService::buildPayload(request),
             QString("animica:anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq?amount=1.25&memo=Invoice%2042"));
}

void TestReceiveQr::testNormalizeAmount()
{
    bool ok = false;
    QCOMPARE(ReceiveQrService::normalizeAmount("001.230000000", &ok), QString("1.23"));
    QVERIFY(ok);

    QCOMPARE(ReceiveQrService::normalizeAmount("", &ok), QString());
    QVERIFY(ok);

    QCOMPARE(ReceiveQrService::normalizeAmount("1.1234567890", &ok), QString());
    QVERIFY(!ok);
}

void TestReceiveQr::testSavePngWritesFile()
{
    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    QImage image(32, 32, QImage::Format_ARGB32);
    image.fill(Qt::white);
    image.setPixelColor(4, 4, Qt::black);

    const QString outputPath = tempDir.filePath("receive-qr.png");
    QString errorMessage;
    QVERIFY(ReceiveQrService::savePng(image, outputPath, &errorMessage));
    QVERIFY2(errorMessage.isEmpty(), qPrintable(errorMessage));
    QVERIFY(QFileInfo::exists(outputPath));

    QImage reloaded(outputPath);
    QVERIFY(!reloaded.isNull());
}

void TestReceiveQr::testDependencyMissingFailureIsActionable()
{
    LocalPythonReceiveQrService service;

    ReceiveQrRequest request;
    request.address = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq";
    const ReceiveQrResult result = service.generate(request);

    QCOMPARE(result.status, ReceiveQrResult::Status::DependencyMissing);
    QVERIFY(result.errorSummary.contains("dependency", Qt::CaseInsensitive));
    QVERIFY(!result.errorDetails.isEmpty());
    QVERIFY(result.payload.startsWith("animica:"));
}

void TestReceiveQr::testReceiveWidgetRefreshesQrOnInputChanges()
{
    configureBackendEnvironment();

    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    AnimicaRpcClient rpcClient;
    rpcClient.setEndpoint(RpcSettings::canonicalRpcUrl());
    rpcClient.setTimeout(250);
    rpcClient.setRetryPolicy(0, 0);
    WalletEngine engine(&rpcClient);
    QVERIFY(engine.createWallet(QString(), tempDir.path()));

    const WalletAccount first = engine.createAccount("Primary", 0x1001);
    const WalletAccount second = engine.createAccount("Backup", 0x1002);
    QVERIFY(!first.address.isEmpty());
    QVERIFY(!second.address.isEmpty());

    auto qrService = std::make_shared<FakeReceiveQrService>();
    ReceiveWidget widget(&engine, nullptr, qrService);
    widget.show();
    widget.refresh();

    auto* accountCombo = widget.findChild<QComboBox*>("receiveAccountCombo");
    auto* amountEdit = widget.findChild<QLineEdit*>("receiveAmountEdit");
    auto* messageEdit = widget.findChild<QLineEdit*>("receiveMessageEdit");
    auto* saveButton = widget.findChild<QPushButton*>("receiveSaveQrButton");
    auto* qrLabel = widget.findChild<QLabel*>("receiveQrCodeLabel");
    QVERIFY(accountCombo);
    QVERIFY(amountEdit);
    QVERIFY(messageEdit);
    QVERIFY(saveButton);
    QVERIFY(qrLabel);

    QTRY_VERIFY(qrService->callCount() >= 1);
    QTRY_VERIFY(saveButton->isEnabled());
    QTRY_VERIFY(!qrLabel->pixmap(Qt::ReturnByValue).isNull());
    const QString initialAddress = qrService->lastRequest().address;
    QVERIFY(initialAddress == first.address || initialAddress == second.address);

    const int initialCalls = qrService->callCount();
    amountEdit->setText("1.5");
    messageEdit->setText("rent");
    QTRY_VERIFY(qrService->callCount() > initialCalls);
    QCOMPARE(qrService->lastRequest().amount, QString("1.5"));
    QCOMPARE(qrService->lastRequest().message, QString("rent"));

    const int nextIndex = accountCombo->currentIndex() == 0 ? 1 : 0;
    const QString expectedAddress = initialAddress == first.address ? second.address : first.address;
    accountCombo->setCurrentIndex(nextIndex);
    QTRY_COMPARE(qrService->lastRequest().address, expectedAddress);
}

void TestReceiveQr::testReceiveWidgetShowsDependencyFailure()
{
    configureBackendEnvironment();

    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    AnimicaRpcClient rpcClient;
    rpcClient.setEndpoint(RpcSettings::canonicalRpcUrl());
    rpcClient.setTimeout(250);
    rpcClient.setRetryPolicy(0, 0);
    WalletEngine engine(&rpcClient);
    QVERIFY(engine.createWallet(QString(), tempDir.path()));
    const WalletAccount first = engine.createAccount("Primary", 0x1001);
    QVERIFY(!first.address.isEmpty());

    auto qrService = std::make_shared<MissingDependencyWidgetService>();
    ReceiveWidget widget(&engine, nullptr, qrService);
    widget.show();
    widget.refresh();

    auto* saveButton = widget.findChild<QPushButton*>("receiveSaveQrButton");
    auto* qrLabel = widget.findChild<QLabel*>("receiveQrCodeLabel");
    auto* statusLabel = widget.findChild<QLabel*>("receiveQrStatusLabel");
    QVERIFY(saveButton);
    QVERIFY(qrLabel);
    QVERIFY(statusLabel);

    QTRY_VERIFY(!saveButton->isEnabled());
    QTRY_VERIFY(qrLabel->text().contains("dependency", Qt::CaseInsensitive));
    QTRY_VERIFY(statusLabel->text().contains("wallet_qt", Qt::CaseInsensitive));
}

QTEST_MAIN(TestReceiveQr)
#include "test_receive_qr.moc"
