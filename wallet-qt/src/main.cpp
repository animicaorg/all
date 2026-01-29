#include <QApplication>
#include <QMainWindow>
#include <QMenuBar>
#include <QMenu>
#include <QAction>
#include <QMessageBox>
#include <QFileDialog>
#include <QDesktopServices>
#include <QUrl>
#include "ui/NodeControlWidget.h"
#include "node/NodeManager.h"
#include "platform/AppPaths.h"
#include "platform/DataDirManager.h"
#include "rpc/AnimicaRpcClient.h"
#include "diagnostics/DiagnosticsWindow.h"
#include "wallet/WalletImporter.h"
#include "wallet/WalletExporter.h"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    
    // Set application metadata
    app.setApplicationName("AnimicaWallet");
    app.setApplicationVersion("0.1.0");
    app.setOrganizationName("Animica");
    app.setOrganizationDomain("animica.org");
    
    // Ensure directories exist
    if (!AppPaths::ensureDirectoriesExist()) {
        QMessageBox::critical(nullptr, "Startup Error",
                             "Failed to create application directories. Check permissions.");
        return 1;
    }
    
    // Create data directory manager
    DataDirManager dataDirManager;
    dataDirManager.ensureDirectoriesExist();
    
    // Create main window
    QMainWindow window;
    window.setWindowTitle("Animica Wallet");
    window.setMinimumSize(800, 600);
    
    // Create node manager with data directory manager
    NodeManager nodeManager(&dataDirManager);
    
    // Create RPC client
    AnimicaRpcClient rpcClient;
    rpcClient.setEndpoint("http://127.0.0.1:8545/rpc");
    
    // Create diagnostics window (parent to window for proper cleanup)
    DiagnosticsWindow* diagnosticsWindow = new DiagnosticsWindow(&rpcClient, &nodeManager, &window);
    
    // Create and set central widget
    NodeControlWidget* nodeControl = new NodeControlWidget(&nodeManager);
    window.setCentralWidget(nodeControl);
    
    // Create menu bar
    QMenuBar* menuBar = window.menuBar();
    
    // File menu
    QMenu* fileMenu = menuBar->addMenu("&File");
    
    // Wallet submenu
    QMenu* walletMenu = fileMenu->addMenu("&Wallet");
    
    QAction* importWalletAction = walletMenu->addAction("&Import wallets.json...");
    QObject::connect(importWalletAction, &QAction::triggered, [&window, &dataDirManager]() {
        // Stop node warning
        QMessageBox::StandardButton reply = QMessageBox::warning(
            &window,
            "Import Wallets",
            "⚠️ WARNING: wallets.json contains private keys!\n\n"
            "Importing will:\n"
            "• Add or replace wallet data in your data directory\n"
            "• Create an automatic backup if wallets already exist\n\n"
            "It is recommended to stop the node before importing.\n\n"
            "Do you want to continue?",
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::No
        );
        
        if (reply != QMessageBox::Yes) {
            return;
        }
        
        // Select file
        QString sourceFile = QFileDialog::getOpenFileName(
            &window,
            "Select wallets.json to import",
            QDir::homePath(),
            "Wallet Files (wallets.json *.json);;All Files (*)"
        );
        
        if (sourceFile.isEmpty()) {
            return;
        }
        
        // Validate file
        WalletImporter importer;
        auto validation = importer.validateWalletFile(sourceFile);
        
        if (!validation.valid) {
            QMessageBox::critical(&window, "Import Failed",
                QString("Invalid wallet file:\n%1").arg(validation.errorMessage));
            return;
        }
        
        // Check for existing file
        QString targetFile = dataDirManager.getWalletsFilePath();
        WalletImporter::ConflictResolution resolution = WalletImporter::ConflictResolution::Replace;
        
        if (WalletImporter::walletFileExists(targetFile)) {
            QMessageBox msgBox(&window);
            msgBox.setWindowTitle("Wallets Already Exist");
            msgBox.setText("A wallets.json file already exists in your data directory.");
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
        
        // Import
        auto result = importer.importWallets(sourceFile, targetFile, resolution);
        
        if (result.success) {
            QString msg = QString("Successfully imported %1 wallet(s)").arg(result.walletsImported);
            if (result.walletsSkipped > 0) {
                msg += QString("\n%1 duplicate(s) skipped").arg(result.walletsSkipped);
            }
            if (!result.backupPath.isEmpty()) {
                msg += QString("\n\nBackup created:\n%1").arg(result.backupPath);
            }
            QMessageBox::information(&window, "Import Successful", msg);
        } else {
            QMessageBox::critical(&window, "Import Failed", result.errorMessage);
        }
    });
    
    QAction* exportWalletAction = walletMenu->addAction("&Export wallets.json...");
    QObject::connect(exportWalletAction, &QAction::triggered, [&window, &dataDirManager]() {
        // Security warning
        QMessageBox::StandardButton reply = QMessageBox::warning(
            &window,
            "Export Wallets",
            "⚠️ SECURITY WARNING\n\n"
            "wallets.json contains private keys that control your funds!\n\n"
            "• Only export to secure, encrypted storage\n"
            "• Never share this file with anyone\n"
            "• Never upload to cloud services\n\n"
            "Do you want to continue?",
            QMessageBox::Yes | QMessageBox::No,
            QMessageBox::No
        );
        
        if (reply != QMessageBox::Yes) {
            return;
        }
        
        QString sourceFile = dataDirManager.getWalletsFilePath();
        
        // Check source exists
        WalletExporter exporter;
        QString errorMsg;
        if (!exporter.canExport(sourceFile, errorMsg)) {
            QMessageBox::warning(&window, "Export Failed", errorMsg);
            return;
        }
        
        // Select destination
        QString destFile = QFileDialog::getSaveFileName(
            &window,
            "Export wallets.json",
            QDir::homePath() + "/wallets.json",
            "Wallet Files (wallets.json *.json);;All Files (*)"
        );
        
        if (destFile.isEmpty()) {
            return;
        }
        
        // Export
        auto result = exporter.exportWallets(sourceFile, destFile, true);
        
        if (result.success) {
            QMessageBox::information(&window, "Export Successful",
                QString("Wallet exported to:\n%1\n\nRemember to keep this file secure!").arg(result.exportPath));
        } else {
            QMessageBox::critical(&window, "Export Failed", result.errorMessage);
        }
    });
    
    fileMenu->addSeparator();
    
    QAction* exitAction = fileMenu->addAction("E&xit");
    exitAction->setShortcut(QKeySequence::Quit);
    QObject::connect(exitAction, &QAction::triggered, &app, &QApplication::quit);
    
    // Node menu
    QMenu* nodeMenu = menuBar->addMenu("&Node");
    
    QAction* startNodeAction = nodeMenu->addAction("&Start Node");
    startNodeAction->setShortcut(QKeySequence("Ctrl+S"));
    QObject::connect(startNodeAction, &QAction::triggered, [&nodeManager]() {
        nodeManager.startNode("devnet");
    });
    
    QAction* stopNodeAction = nodeMenu->addAction("S&top Node");
    stopNodeAction->setShortcut(QKeySequence("Ctrl+T"));
    QObject::connect(stopNodeAction, &QAction::triggered, [&nodeManager]() {
        nodeManager.stopNode();
    });
    
    nodeMenu->addSeparator();
    
    QAction* diagnosticsAction = nodeMenu->addAction("&Diagnostics...");
    diagnosticsAction->setShortcut(QKeySequence("Ctrl+D"));
    QObject::connect(diagnosticsAction, &QAction::triggered, [diagnosticsWindow]() {
        diagnosticsWindow->showAndActivate();
    });
    
    nodeMenu->addSeparator();
    
    QAction* openLogsAction = nodeMenu->addAction("Open &Logs Folder");
    QObject::connect(openLogsAction, &QAction::triggered, [&nodeManager]() {
        nodeManager.openLogsFolder();
    });
    
    // Settings menu
    QMenu* settingsMenu = menuBar->addMenu("&Settings");
    
    QAction* showDataDirAction = settingsMenu->addAction("Show &Data Directory");
    QObject::connect(showDataDirAction, &QAction::triggered, [&window, &dataDirManager]() {
        QString dataDir = dataDirManager.getDataDir();
        QString msg = QString("Current data directory:\n\n%1\n\n").arg(dataDir);
        
        QString storedNetwork = dataDirManager.getStoredNetworkId();
        if (!storedNetwork.isEmpty()) {
            msg += QString("Configured for network: %1\n\n").arg(storedNetwork);
        }
        
        msg += "This directory contains:\n"
               "• wallets.json (your wallet keys)\n"
               "• chain-* (blockchain data)\n"
               "• logs (node and wallet logs)\n"
               "• snapshots (chain snapshots)\n\n"
               "Open this folder in file manager?";
        
        QMessageBox::StandardButton reply = QMessageBox::information(
            &window,
            "Data Directory",
            msg,
            QMessageBox::Open | QMessageBox::Cancel
        );
        
        if (reply == QMessageBox::Open) {
            QDesktopServices::openUrl(QUrl::fromLocalFile(dataDir));
        }
    });
    
    QAction* changeDataDirAction = settingsMenu->addAction("&Change Data Directory...");
    QObject::connect(changeDataDirAction, &QAction::triggered, [&window, &dataDirManager, &nodeManager]() {
        // Warn to stop node first
        if (nodeManager.isRunning()) {
            QMessageBox::warning(&window, "Node Running",
                "Please stop the node before changing the data directory.");
            return;
        }
        
        QString currentDir = dataDirManager.getDataDir();
        
        QString newDir = QFileDialog::getExistingDirectory(
            &window,
            "Choose Data Directory",
            currentDir,
            QFileDialog::ShowDirsOnly | QFileDialog::DontResolveSymlinks
        );
        
        if (newDir.isEmpty()) {
            return;
        }
        
        if (newDir == currentDir) {
            return;
        }
        
        // Validate directory
        QString errorMsg;
        if (!dataDirManager.validateDataDir(newDir, errorMsg)) {
            QMessageBox::critical(&window, "Invalid Directory", errorMsg);
            return;
        }
        
        // Warn about changing directory
        QString msg = QString("Change data directory to:\n%1\n\n"
            "This will:\n"
            "• Use wallets and chain data from the new directory\n"
            "• Require restarting the wallet\n\n"
            "Previous data directory:\n%2\n\n"
            "Your old data will NOT be moved or deleted.\n\n"
            "Continue?").arg(newDir, currentDir);
        
        QMessageBox::StandardButton reply = QMessageBox::question(
            &window,
            "Change Data Directory",
            msg,
            QMessageBox::Yes | QMessageBox::No
        );
        
        if (reply == QMessageBox::Yes) {
            if (dataDirManager.setDataDir(newDir, true)) {
                QMessageBox::information(&window, "Restart Required",
                    "Data directory changed.\n\nPlease restart the wallet for changes to take effect.");
                app.quit();
            } else {
                QMessageBox::critical(&window, "Error",
                    "Failed to set data directory. Check logs for details.");
            }
        }
    });
    
    // Help menu
    QMenu* helpMenu = menuBar->addMenu("&Help");
    
    QAction* aboutAction = helpMenu->addAction("&About");
    QObject::connect(aboutAction, &QAction::triggered, [&window]() {
        QMessageBox::about(&window, "About Animica Wallet",
                          "<h2>Animica Wallet v0.1.0</h2>"
                          "<p>A desktop wallet for the Animica blockchain with embedded node.</p>"
                          "<p>This is an early prototype implementing node control functionality.</p>"
                          "<p><b>Features:</b></p>"
                          "<ul>"
                          "<li>Embedded Animica node (standalone Python mode)</li>"
                          "<li>Localhost-only RPC communication</li>"
                          "<li>Network selection (mainnet/testnet/devnet)</li>"
                          "<li>Node lifecycle management</li>"
                          "<li>Sync progress monitoring</li>"
                          "<li>Log viewing and diagnostics</li>"
                          "</ul>"
                          "<p><b>Coming soon:</b></p>"
                          "<ul>"
                          "<li>Key management and wallet creation</li>"
                          "<li>Send/receive transactions</li>"
                          "<li>Balance display</li>"
                          "<li>Transaction history</li>"
                          "</ul>"
                          "<p>© 2024 Animica. All rights reserved.</p>");
    });
    
    QAction* aboutQtAction = helpMenu->addAction("About &Qt");
    QObject::connect(aboutQtAction, &QAction::triggered, &app, &QApplication::aboutQt);
    
    // Show window
    window.show();
    
    return app.exec();
}
