#include "DiagnosticsWindow.h"
#include "RoleManager.h"
#include "ConsoleExecutor.h"
#include "NodeController.h"
#include "DiagnosticsConsoleWidget.h"
#include "DiagnosticsStatusWidget.h"
#include "DiagnosticsLogsWidget.h"
#include "../rpc/AnimicaRpcClient.h"
#include "../node/NodeManager.h"
#include <QMenuBar>
#include <QMenu>
#include <QAction>
#include <QCloseEvent>
#include <QTabWidget>
#include <QVBoxLayout>

DiagnosticsWindow::DiagnosticsWindow(AnimicaRpcClient* rpcClient, 
                                    NodeManager* nodeManager,
                                    QWidget* parent)
    : QMainWindow(parent)
    , m_rpcClient(rpcClient)
    , m_nodeManager(nodeManager)
{
    // Create core components
    m_roleManager = new RoleManager(this);
    m_executor = new ConsoleExecutor(rpcClient, this);
    m_controller = new NodeController(rpcClient, this);

    setupUi();
    setupMenuBar();
    setupConnections();

    setWindowTitle("Animica Diagnostics");
    resize(1000, 700);
}

DiagnosticsWindow::~DiagnosticsWindow()
{
    // Stop auto-refresh when closing
    if (m_statusWidget) {
        m_statusWidget->stopAutoRefresh();
    }
}

void DiagnosticsWindow::setupUi()
{
    // Central widget with tab widget
    QWidget* centralWidget = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(centralWidget);
    
    m_tabWidget = new QTabWidget(this);
    
    // Console tab
    m_consoleWidget = new DiagnosticsConsoleWidget(m_roleManager, m_executor, this);
    m_tabWidget->addTab(m_consoleWidget, "Console");
    
    // Node Status tab
    m_statusWidget = new DiagnosticsStatusWidget(m_controller, m_roleManager, this);
    m_tabWidget->addTab(m_statusWidget, "Node Status");
    
    // Logs tab
    m_logsWidget = new DiagnosticsLogsWidget(m_nodeManager, this);
    m_tabWidget->addTab(m_logsWidget, "Logs");
    
    layout->addWidget(m_tabWidget);
    setCentralWidget(centralWidget);
}

void DiagnosticsWindow::setupMenuBar()
{
    QMenuBar* menuBar = new QMenuBar(this);
    
    // File menu
    QMenu* fileMenu = menuBar->addMenu("&File");
    
    QAction* closeAction = fileMenu->addAction("&Close");
    closeAction->setShortcut(QKeySequence::Close);
    connect(closeAction, &QAction::triggered, this, &DiagnosticsWindow::close);
    
    // Settings menu
    QMenu* settingsMenu = menuBar->addMenu("&Settings");
    
    QAction* operatorAction = settingsMenu->addAction("&Operator Mode");
    operatorAction->setCheckable(true);
    operatorAction->setChecked(m_roleManager->isOperatorEnabled());
    connect(operatorAction, &QAction::toggled, [this](bool checked) {
        m_roleManager->setOperatorEnabled(checked);
    });
    
    QAction* developerAction = settingsMenu->addAction("&Developer Mode");
    developerAction->setCheckable(true);
    developerAction->setChecked(m_roleManager->isDeveloperEnabled());
    connect(developerAction, &QAction::toggled, [this](bool checked) {
        m_roleManager->setDeveloperEnabled(checked);
    });
    
    // Update menu when role changes
    connect(m_roleManager, &RoleManager::operatorEnabledChanged, operatorAction, &QAction::setChecked);
    connect(m_roleManager, &RoleManager::developerEnabledChanged, developerAction, &QAction::setChecked);
    
    setMenuBar(menuBar);
}

void DiagnosticsWindow::setupConnections()
{
    // Start auto-refresh when status tab is shown
    connect(m_tabWidget, &QTabWidget::currentChanged, [this](int index) {
        if (index == StatusTab) {
            m_statusWidget->startAutoRefresh();
        } else {
            m_statusWidget->stopAutoRefresh();
        }
    });
}

void DiagnosticsWindow::showAndActivate()
{
    show();
    raise();
    activateWindow();
    
    // Start auto-refresh if on status tab
    if (m_tabWidget->currentIndex() == StatusTab) {
        m_statusWidget->startAutoRefresh();
    }
}

void DiagnosticsWindow::closeEvent(QCloseEvent* event)
{
    // Stop auto-refresh
    m_statusWidget->stopAutoRefresh();
    
    event->accept();
}
