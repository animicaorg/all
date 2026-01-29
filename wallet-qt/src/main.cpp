#include <QApplication>
#include <QMainWindow>
#include <QMenuBar>
#include <QMenu>
#include <QAction>
#include <QMessageBox>
#include "ui/NodeControlWidget.h"
#include "node/NodeManager.h"
#include "platform/AppPaths.h"

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
    
    // Create main window
    QMainWindow window;
    window.setWindowTitle("Animica Wallet");
    window.setMinimumSize(800, 600);
    
    // Create node manager
    NodeManager nodeManager;
    
    // Create and set central widget
    NodeControlWidget* nodeControl = new NodeControlWidget(&nodeManager);
    window.setCentralWidget(nodeControl);
    
    // Create menu bar
    QMenuBar* menuBar = window.menuBar();
    
    // File menu
    QMenu* fileMenu = menuBar->addMenu("&File");
    
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
    
    QAction* openLogsAction = nodeMenu->addAction("Open &Logs Folder");
    QObject::connect(openLogsAction, &QAction::triggered, [&nodeManager]() {
        nodeManager.openLogsFolder();
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
