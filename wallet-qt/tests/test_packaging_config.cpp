#include <QFile>
#include <QTest>

class TestPackagingConfig : public QObject
{
    Q_OBJECT

private slots:
    void testBuildScriptsUseSingleRemoteWalletMode();
    void testLinuxReleaseStagesInstalledTreeAndPortableArtifacts();
    void testMacReleaseStagesInstalledBundle();
    void testWindowsReleaseStagesInstalledTree();
    void testWindowsCrossReleaseScripts();
    void testBundleVerifierChecksQtAppLayoutOnly();
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

void TestPackagingConfig::testBuildScriptsUseSingleRemoteWalletMode()
{
    const QString cmakeContent = readFile("CMakeLists.txt");
    const QString linuxBuild = readFile("scripts/build-linux.sh");
    const QString macBuild = readFile("scripts/build-mac.sh");
    const QString windowsBuild = readFile("scripts/build-windows.ps1");

    QVERIFY(!cmakeContent.isEmpty());
    QVERIFY(!linuxBuild.isEmpty());
    QVERIFY(!macBuild.isEmpty());
    QVERIFY(!windowsBuild.isEmpty());

    QVERIFY(!cmakeContent.contains("WALLET_REMOTE_RPC_ONLY"));
    QVERIFY(!linuxBuild.contains("WALLET_REMOTE_RPC_ONLY"));
    QVERIFY(!macBuild.contains("WALLET_REMOTE_RPC_ONLY"));
    QVERIFY(!windowsBuild.contains("WALLET_REMOTE_RPC_ONLY"));
    QVERIFY(cmakeContent.contains("https://rpc.animica.org"));
}

void TestPackagingConfig::testLinuxReleaseStagesInstalledTreeAndPortableArtifacts()
{
    const QString content = readFile("scripts/release-linux.sh");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("cmake --install"));
    QVERIFY(content.contains("verify-bundle-layout.py"));
    QVERIFY(content.contains("linuxdeployqt"));
    QVERIFY(content.contains("tar -czf"));
    QVERIFY(content.contains("animica-wallet_${PACKAGE_VERSION}_${DEB_ARCH}.deb"));
    QVERIFY(content.contains("https://rpc.animica.org"));
    QVERIFY(!content.contains("resolve_linux_node_root"));
}

void TestPackagingConfig::testMacReleaseStagesInstalledBundle()
{
    const QString content = readFile("scripts/release-mac.sh");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("cmake --install"));
    QVERIFY(content.contains("verify-bundle-layout.py"));
    QVERIFY(content.contains("adhoc", Qt::CaseInsensitive));
    QVERIFY(!content.contains("Contents/Resources/node"));
}

void TestPackagingConfig::testWindowsReleaseStagesInstalledTree()
{
    const QString content = readFile("scripts/release-windows.ps1");
    const QString packageContent = readFile("scripts/package-windows-installer.ps1");
    QVERIFY(!content.isEmpty());
    QVERIFY(!packageContent.isEmpty());
    QVERIFY(content.contains("build-windows.ps1"));
    QVERIFY(content.contains("package-windows-installer.ps1"));
    QVERIFY(content.contains("PerMachine", Qt::CaseInsensitive));
    QVERIFY(packageContent.contains("Inno Setup", Qt::CaseInsensitive));
    QVERIFY(packageContent.contains("Qt6Core.dll"));
    QVERIFY(packageContent.contains("plugins\\platforms\\qwindows.dll"));
}

void TestPackagingConfig::testWindowsCrossReleaseScripts()
{
    const QString buildContent = readFile("scripts/build-windows-cross.sh");
    const QString releaseContent = readFile("scripts/release-windows-cross.sh");
    const QString publishContent = readFile("scripts/publish-wallet-downloads.sh");
    const QString toolchainContent = readFile("cmake/toolchains/mingw64.cmake");

    QVERIFY(!buildContent.isEmpty());
    QVERIFY(buildContent.contains("makensis"));
    QVERIFY(buildContent.contains("CMAKE_TOOLCHAIN_FILE"));
    QVERIFY(buildContent.contains("SHA256SUMS.txt"));
    QVERIFY(buildContent.contains("animica-wallet-setup-x64.exe"));
    QVERIFY(buildContent.contains("hosted-rpc"));
    QVERIFY(!buildContent.contains("--node-venv"));

    QVERIFY(!releaseContent.isEmpty());
    QVERIFY(!releaseContent.contains("--node-venv"));

    QVERIFY(!publishContent.isEmpty());
    QVERIFY(publishContent.contains("manifest.json"));
    QVERIFY(publishContent.contains("animica-wallet-windows.sha256"));

    QVERIFY(!toolchainContent.isEmpty());
    QVERIFY(toolchainContent.contains("CMAKE_SYSTEM_NAME Windows"));
    QVERIFY(toolchainContent.contains("CMAKE_RC_COMPILER"));
}

void TestPackagingConfig::testBundleVerifierChecksQtAppLayoutOnly()
{
    const QString verifier = readFile("scripts/verify-bundle-layout.py");
    QVERIFY(!verifier.isEmpty());
    QVERIFY(verifier.contains("Qt6Core.dll"));
    QVERIFY(verifier.contains("libqcocoa.dylib"));
    QVERIFY(!verifier.contains("assets/genesis"));
    QVERIFY(!verifier.contains("venv"));
}

void TestPackagingConfig::testCMakeIncludesPackagingMetadata()
{
    const QString content = readFile("CMakeLists.txt");
    QVERIFY(!content.isEmpty());
    QVERIFY(content.contains("MACOSX_BUNDLE_INFO_PLIST"));
    QVERIFY(content.contains("include(CPack)"));
    QVERIFY(content.contains("CPACK_PACKAGE_EXECUTABLES"));
    QVERIFY(content.contains("resources/wallet-qt.qrc"));
    QVERIFY(content.contains("WALLET_ENABLE_QT_INSTALL_DEPLOYMENT"));
    QVERIFY(content.contains("https://rpc.animica.org"));
}

QTEST_MAIN(TestPackagingConfig)
#include "test_packaging_config.moc"
