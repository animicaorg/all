#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTemporaryDir>
#include <QtTest/QtTest>

#include "../src/platform/AppPaths.h"

namespace {
class ScopedEnvVar
{
public:
    ScopedEnvVar(const char* name, const QByteArray& value)
        : m_name(name)
        , m_hadOriginal(qEnvironmentVariableIsSet(name))
        , m_original(qgetenv(name))
    {
        qputenv(name, value);
    }

    ~ScopedEnvVar()
    {
        if (m_hadOriginal) {
            qputenv(m_name.constData(), m_original);
        } else {
            qunsetenv(m_name.constData());
        }
    }

private:
    QByteArray m_name;
    bool m_hadOriginal;
    QByteArray m_original;
};

QString bundledSitePackagesDir(const QString& nodeDir)
{
#ifdef Q_OS_WIN
    return QDir(nodeDir).filePath("venv/Lib/site-packages");
#else
    return QDir(nodeDir).filePath("venv/lib/python-test/site-packages");
#endif
}

bool writeFile(const QString& path, const QByteArray& contents = "test")
{
    if (!QDir().mkpath(QFileInfo(path).absolutePath())) {
        return false;
    }

    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return false;
    }

    return file.write(contents) == contents.size();
}
}

class TestAppPaths : public QObject
{
    Q_OBJECT

private slots:
    void testBundledGenesisPrefersCanonicalSitePackages();
    void testBundledGenesisFallsBackToAssets();
    void testBundledParamsPrefersCanonicalSitePackages();
};

void TestAppPaths::testBundledGenesisPrefersCanonicalSitePackages()
{
    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    const QString nodeDir = QDir(tempDir.path()).filePath("node");
    const QString canonicalGenesis = QDir(bundledSitePackagesDir(nodeDir)).filePath("core/genesis/mainnet.json");
    const QString assetGenesis = QDir(nodeDir).filePath("assets/genesis/mainnet.json");

    QVERIFY(writeFile(canonicalGenesis, "canonical"));
    QVERIFY(writeFile(assetGenesis, "asset"));

    ScopedEnvVar nodeOverride("ANIMICA_WALLET_NODE_DIR", nodeDir.toUtf8());
    QCOMPARE(AppPaths::bundledGenesisPath("mainnet"), QFileInfo(canonicalGenesis).absoluteFilePath());
}

void TestAppPaths::testBundledGenesisFallsBackToAssets()
{
    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    const QString nodeDir = QDir(tempDir.path()).filePath("node");
    const QString assetGenesis = QDir(nodeDir).filePath("assets/genesis/testnet.json");

    QVERIFY(writeFile(assetGenesis, "asset"));

    ScopedEnvVar nodeOverride("ANIMICA_WALLET_NODE_DIR", nodeDir.toUtf8());
    QCOMPARE(AppPaths::bundledGenesisPath("testnet"), QFileInfo(assetGenesis).absoluteFilePath());
}

void TestAppPaths::testBundledParamsPrefersCanonicalSitePackages()
{
    QTemporaryDir tempDir;
    QVERIFY(tempDir.isValid());

    const QString nodeDir = QDir(tempDir.path()).filePath("node");
    const QString canonicalParams = QDir(bundledSitePackagesDir(nodeDir)).filePath("spec/params.yaml");
    const QString assetParams = QDir(nodeDir).filePath("assets/spec/params.yaml");

    QVERIFY(writeFile(canonicalParams, "canonical"));
    QVERIFY(writeFile(assetParams, "asset"));

    ScopedEnvVar nodeOverride("ANIMICA_WALLET_NODE_DIR", nodeDir.toUtf8());
    QCOMPARE(AppPaths::bundledParamsPath(), QFileInfo(canonicalParams).absoluteFilePath());
}

QTEST_MAIN(TestAppPaths)
#include "test_app_paths.moc"
