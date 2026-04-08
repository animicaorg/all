#include <QFile>
#include <QTest>

class TestPackagingConfig : public QObject
{
    Q_OBJECT

private slots:
    void testAnimicaNodeBuildAvoidsEditableInstalls();
    void testMacReleaseStagesInstalledBundle();
    void testWindowsReleaseStagesInstalledTree();
    void testCMakeIncludesPackagingMetadata();

private:
    static QString readFile(const QString& relativePath)
    {
        QFile file(QStringLiteral(WALLET_QT_SOURCE_DIR) + "/" + relativePath);
        if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
            return QString();
        }
        return QString::fromUtf8(file.readAll());
    }
};

void TestPackagingConfig::testAnimicaNodeBuildAvoidsEditableInstalls()
{
    const QString content = readFile("cmake/AnimicaNode.cmake");
    const QString pyproject = readFile("../python/pyproject.toml");
    QVERIFY(!content.isEmpty());
    QVERIFY(!content.contains(" install -e "));
    QVERIFY(content.contains("[wallet_qt]"));
    QVERIFY(pyproject.contains("segno"));
    QVERIFY(content.contains("spec"));
}

void TestPackagingConfig::testMacReleaseStagesInstalledBundle()
{
    const QString content = readFile("scripts/release-mac.sh");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("cmake --install"));
    QVERIFY(content.contains("verify-bundle-layout.py"));
    QVERIFY(content.contains("adhoc", Qt::CaseInsensitive));
}

void TestPackagingConfig::testWindowsReleaseStagesInstalledTree()
{
    const QString content = readFile("scripts/release-windows.ps1");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("cmake --install"));
    QVERIFY(content.contains("cpack -G WIX"));
    QVERIFY(content.contains("verify-bundle-layout.py"));
    QVERIFY(content.contains("per-user", Qt::CaseInsensitive));
}

void TestPackagingConfig::testCMakeIncludesPackagingMetadata()
{
    const QString content = readFile("CMakeLists.txt");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("MACOSX_BUNDLE_INFO_PLIST"));
    QVERIFY(content.contains("include(CPack)"));
    QVERIFY(content.contains("CPACK_PACKAGE_EXECUTABLES"));
    QVERIFY(content.contains("resources/wallet-qt.qrc"));
}

QTEST_MAIN(TestPackagingConfig)
#include "test_packaging_config.moc"
