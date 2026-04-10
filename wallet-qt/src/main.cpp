#include <QAction>
#include <QApplication>
#include <QDesktopServices>
#include <QDir>
#include <QFileDialog>
#include <QIcon>
#include <QKeySequence>
#include <QMainWindow>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QPushButton>
#include <QUrl>

#include "platform/DataDirManager.h"
#include "rpc/AnimicaRpcClient.h"
#include "rpc/RpcSettings.h"
#include "wallet/TransactionMonitor.h"
#include "wallet/WalletDatabase.h"
#include "wallet/WalletEngine.h"
#include "wallet/WalletExporter.h"
#include "wallet/WalletImporter.h"
#include "wallet/WalletWidget.h"

int main(int argc, char* argv[])
{
    QApplication app(argc, argv);

    app.setApplicationName("AnimicaWallet");
    app.setApplicationVersion(QStringLiteral(WALLET_VERSION));
    app.setOrganizationName("Animica");
    app.setOrganizationDomain("animica.org");
    app.setWindowIcon(QIcon(":/icons/animica-wallet.png"));

    DataDirManager dataDirManager;
    if (!dataDirManager.ensureDirectoriesExist()) {
        QMessageBox::critical(
            nullptr,
            "Startup Error",
            "Failed to prepare the Animica Wallet data directory."
        );
        return 1;
    }

    QMainWindow window;
    window.setWindowTitle("Animica Wallet");
    window.setWindowIcon(QIcon(":/icons/animica-wallet.png"));
    window.setMinimumSize(960, 680);

    AnimicaRpcClient rpcClient;
    rpcClient.setEndpoint(RpcSettings::canonicalRpcUrl());

    WalletEngine walletEngine(&rpcClient);
    WalletDatabase walletDb(QDir(dataDirManager.getDataDir()).filePath("wallet.db"), &window);
    if (!walletDb.initialize()) {
        QMessageBox::warning(&window, "Wallet Error", "Failed to initialize the wallet activity database.");
    }

    TransactionMonitor txMonitor(&rpcClient, &walletDb, &window);
    txMonitor.start();

    const QString walletFilePath = dataDirManager.getWalletsFilePath();
    if (!walletEngine.openWallet(walletFilePath)) {
        const QString detail = walletEngine.lastError().trimmed();
        QMessageBox::warning(
            &window,
            "Wallet Error",
            detail.isEmpty()
                ? QStringLiteral("Failed to initialize the canonical wallets.json store.")
                : detail
        );
    }

    auto* walletWidget = new WalletWidget(&walletEngine, &rpcClient, &walletDb, &txMonitor, &window);
    walletWidget->setRpcEndpoint(RpcSettings::canonicalRpcUrl());
    window.setCentralWidget(walletWidget);

    QMenuBar* menuBar = window.menuBar();

    QMenu* fileMenu = menuBar->addMenu("&File");
    QMenu* walletMenu = fileMenu->addMenu("&Wallet");

    QAction* importWalletAction = walletMenu->addAction("&Import wallets.json...");
    QObject::connect(importWalletAction, &QAction::triggered, [&window, &dataDirManager, &walletEngine, walletWidget]() {
        const QMessageBox::StandardButton reply = QMessageBox::warning(
            &window,
            "Import Wallets",
            "wallets.json contains private keys.\n\n"
            "Importing will add or replace wallet data in your Animica Wallet data directory.\n"
            "An automatic backup is created when wallets already exist.\n\n"
            "Continue?",
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::No
        );
        if (reply != QMessageBox::Yes) {
            return;
        }

        const QString sourceFile = QFileDialog::getOpenFileName(
            &window,
            "Select wallets.json to import",
            QDir::homePath(),
            "Wallet Files (wallets.json *.json);;All Files (*)"
        );
        if (sourceFile.isEmpty()) {
            return;
        }

        WalletImporter importer;
        const auto validation = importer.validateWalletFile(sourceFile);
        if (!validation.valid) {
            QMessageBox::critical(
                &window,
                "Import Failed",
                QString("Invalid wallet file:\n%1").arg(validation.errorMessage)
            );
            return;
        }

        const QString targetFile = dataDirManager.getWalletsFilePath();
        WalletImporter::ConflictResolution resolution = WalletImporter::ConflictResolution::Replace;
        if (WalletImporter::walletFileExists(targetFile)) {
            QMessageBox msgBox(&window);
            msgBox.setWindowTitle("Wallets Already Exist");
            msgBox.setText("A wallets.json file already exists in your wallet data directory.");
            msgBox.setInformativeText("How would you like to proceed?");

            QPushButton* replaceBtn = msgBox.addButton("Replace (backup created)", QMessageBox::DestructiveRole);
            QPushButton* mergeBtn = msgBox.addButton("Merge (no duplicates)", QMessageBox::AcceptRole);
            QPushButton* cancelBtn = msgBox.addButton(QMessageBox::Cancel);
            msgBox.setDefaultButton(cancelBtn);
            msgBox.exec();

            if (msgBox.clickedButton() == replaceBtn) {
                resolution = WalletImporter::ConflictResolution::Replace;
            } else if (msgBox.clickedButton() == mergeBtn) {
                resolution = WalletImporter::ConflictResolution::Merge;
            } else {
                return;
            }
        }

        const auto result = importer.importWallets(sourceFile, targetFile, resolution);
        if (!result.success) {
            QMessageBox::critical(&window, "Import Failed", result.errorMessage);
            return;
        }

        QString message = QString("Successfully imported %1 wallet(s).").arg(result.walletsImported);
        if (result.walletsSkipped > 0) {
            message += QString("\n%1 duplicate(s) skipped.").arg(result.walletsSkipped);
        }
        if (!result.backupPath.isEmpty()) {
            message += QString("\n\nBackup created:\n%1").arg(result.backupPath);
        }

        walletEngine.openWallet(targetFile);
        walletWidget->refresh();
        QMessageBox::information(&window, "Import Successful", message);
    });

    QAction* exportWalletAction = walletMenu->addAction("&Export wallets.json...");
    QObject::connect(exportWalletAction, &QAction::triggered, [&window, &dataDirManager]() {
        const QMessageBox::StandardButton reply = QMessageBox::warning(
            &window,
            "Export Wallets",
            "wallets.json contains private keys that control your funds.\n\n"
            "Only export it to secure storage that you control.\n"
            "Never share it or upload it to cloud services.\n\n"
            "Continue?",
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::No
        );
        if (reply != QMessageBox::Yes) {
            return;
        }

        const QString sourceFile = dataDirManager.getWalletsFilePath();
        WalletExporter exporter;
        QString errorMsg;
        if (!exporter.canExport(sourceFile, errorMsg)) {
            QMessageBox::warning(&window, "Export Failed", errorMsg);
            return;
        }

        const QString destFile = QFileDialog::getSaveFileName(
            &window,
            "Export wallets.json",
            QDir::homePath() + "/wallets.json",
            "Wallet Files (wallets.json *.json);;All Files (*)"
        );
        if (destFile.isEmpty()) {
            return;
        }

        const auto result = exporter.exportWallets(sourceFile, destFile, true);
        if (!result.success) {
            QMessageBox::critical(&window, "Export Failed", result.errorMessage);
            return;
        }

        QMessageBox::information(
            &window,
            "Export Successful",
            QString("Wallet exported to:\n%1\n\nKeep this file secure.").arg(result.exportPath)
        );
    });

    fileMenu->addSeparator();
    QAction* exitAction = fileMenu->addAction("E&xit");
    exitAction->setShortcut(QKeySequence::Quit);
    QObject::connect(exitAction, &QAction::triggered, &app, &QApplication::quit);

    QMenu* settingsMenu = menuBar->addMenu("&Settings");

    QAction* showDataDirAction = settingsMenu->addAction("Show &Data Directory");
    QObject::connect(showDataDirAction, &QAction::triggered, [&window, &dataDirManager]() {
        const QString dataDir = dataDirManager.getDataDir();
        const QString message =
            QString("Current data directory:\n\n%1\n\n")
                .arg(dataDir)
            + "This directory contains your wallet store, local wallet database, address book, and logs.\n\n"
              "Open this folder in the file manager?";

        const QMessageBox::StandardButton reply = QMessageBox::information(
            &window,
            "Data Directory",
            message,
            QMessageBox::Open | QMessageBox::Cancel
        );
        if (reply == QMessageBox::Open) {
            QDesktopServices::openUrl(QUrl::fromLocalFile(dataDir));
        }
    });

    QAction* changeDataDirAction = settingsMenu->addAction("&Change Data Directory...");
    QObject::connect(changeDataDirAction, &QAction::triggered, [&window, &dataDirManager, &app]() {
        const QString currentDir = dataDirManager.getDataDir();
        const QString newDir = QFileDialog::getExistingDirectory(
            &window,
            "Choose Data Directory",
            currentDir,
            QFileDialog::ShowDirsOnly | QFileDialog::DontResolveSymlinks
        );
        if (newDir.isEmpty() || newDir == currentDir) {
            return;
        }

        QString errorMsg;
        if (!dataDirManager.validateDataDir(newDir, errorMsg)) {
            QMessageBox::critical(&window, "Invalid Directory", errorMsg);
            return;
        }

        const QString message = QString(
            "Change wallet data directory to:\n%1\n\n"
            "This changes where the wallet stores wallets.json, address book data, wallet.db, and logs.\n"
            "Restart the wallet after saving the new path.\n\n"
            "Current directory:\n%2\n"
        ).arg(newDir, currentDir);

        const QMessageBox::StandardButton reply = QMessageBox::question(
            &window,
            "Change Data Directory",
            message,
            QMessageBox::Yes | QMessageBox::No
        );
        if (reply != QMessageBox::Yes) {
            return;
        }

        if (!dataDirManager.setDataDir(newDir, true)) {
            QMessageBox::critical(&window, "Error", "Failed to update the wallet data directory.");
            return;
        }

        QMessageBox::information(
            &window,
            "Restart Required",
            "Data directory changed.\n\nRestart Animica Wallet to use the new location."
        );
        app.quit();
    });

    QMenu* helpMenu = menuBar->addMenu("&Help");
    QAction* aboutAction = helpMenu->addAction("&About");
    QObject::connect(aboutAction, &QAction::triggered, [&window]() {
        QMessageBox::about(
            &window,
            "About Animica Wallet",
            "<h2>Animica Wallet v0.1.0</h2>"
            "<p>A remote Animica desktop wallet for mainnet accounts.</p>"
            "<p><b>Default endpoint:</b> https://rpc.animica.org/rpc</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Canonical wallets.json account management</li>"
            "<li>Balances, send, receive, history, and contracts over hosted RPC</li>"
            "<li>Fast startup without running a local node</li>"
            "</ul>"
            "<p>© 2026 Animica. All rights reserved.</p>"
        );
    });

    QAction* aboutQtAction = helpMenu->addAction("About &Qt");
    QObject::connect(aboutQtAction, &QAction::triggered, &app, &QApplication::aboutQt);

    window.show();
    return app.exec();
}
