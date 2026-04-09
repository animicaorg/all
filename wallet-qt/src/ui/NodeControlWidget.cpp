#include "NodeControlWidget.h"
#include <QGroupBox>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QClipboard>
#include <QApplication>
#include <QDesktopServices>
#include <QScrollBar>
#include <QUrl>

NodeControlWidget::NodeControlWidget(NodeManager* nodeManager, QWidget* parent)
    : QWidget(parent)
    , m_nodeManager(nodeManager)
    , m_degradedBanner(nullptr)
{
    // Create UI elements
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    
    // === Degraded State Banner (hidden by default) ===
    m_degradedBanner = new QWidget(this);
    m_degradedBanner->setStyleSheet("QWidget { background-color: #FFF3CD; border: 2px solid #FFD700; border-radius: 5px; padding: 10px; }");
    m_degradedBanner->setVisible(false);
    
    QVBoxLayout* bannerLayout = new QVBoxLayout(m_degradedBanner);
    
    m_degradedLabel = new QLabel("⚠️ Node started but is degraded (P2P/sync issues). You can still use local wallet features.", this);
    m_degradedLabel->setStyleSheet("QLabel { color: #856404; font-weight: bold; }");
    m_degradedLabel->setWordWrap(true);
    bannerLayout->addWidget(m_degradedLabel);
    
    // Recovery action buttons
    QHBoxLayout* actionsLayout = new QHBoxLayout();
    
    m_openLogsButton = new QPushButton("Open Node Logs", this);
    m_resetDataButton = new QPushButton("Reset Local Node Data", this);
    m_copyDiagButton = new QPushButton("Copy Diagnostics", this);
    
    m_openLogsButton->setStyleSheet("QPushButton { background-color: #FFC107; color: black; padding: 5px; }");
    m_resetDataButton->setStyleSheet("QPushButton { background-color: #FF5722; color: white; padding: 5px; }");
    m_copyDiagButton->setStyleSheet("QPushButton { background-color: #2196F3; color: white; padding: 5px; }");
    
    actionsLayout->addWidget(m_openLogsButton);
    actionsLayout->addWidget(m_resetDataButton);
    actionsLayout->addWidget(m_copyDiagButton);
    actionsLayout->addStretch();
    
    bannerLayout->addLayout(actionsLayout);
    mainLayout->addWidget(m_degradedBanner);
    
    // === Network Selection Group ===
    QGroupBox* networkGroup = new QGroupBox("Network", this);
    QHBoxLayout* networkLayout = new QHBoxLayout(networkGroup);
    
    m_networkCombo = new QComboBox(this);
    m_networkCombo->addItem("Devnet (Local Development)", "devnet");
    m_networkCombo->addItem("Testnet (Public Test Network)", "testnet");
    m_networkCombo->addItem("Mainnet (Production)", "mainnet");
    m_networkCombo->setCurrentIndex(2);  // Default to mainnet for end-user builds
    
    networkLayout->addWidget(new QLabel("Select Network:", this));
    networkLayout->addWidget(m_networkCombo);
    networkLayout->addStretch();
    
    mainLayout->addWidget(networkGroup);
    
    // === Control Buttons Group ===
    QGroupBox* controlGroup = new QGroupBox("Node Control", this);
    QHBoxLayout* controlLayout = new QHBoxLayout(controlGroup);
    
    m_startButton = new QPushButton("Start Node", this);
    m_stopButton = new QPushButton("Stop Node", this);
    m_restartButton = new QPushButton("Restart Node", this);
    m_diagnosticsButton = new QPushButton("Diagnostics", this);
    
    m_startButton->setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; }");
    m_stopButton->setStyleSheet("QPushButton { background-color: #f44336; color: white; font-weight: bold; padding: 8px; }");
    m_restartButton->setStyleSheet("QPushButton { background-color: #FF9800; color: white; font-weight: bold; padding: 8px; }");
    
    controlLayout->addWidget(m_startButton);
    controlLayout->addWidget(m_stopButton);
    controlLayout->addWidget(m_restartButton);
    controlLayout->addStretch();
    controlLayout->addWidget(m_diagnosticsButton);
    
    mainLayout->addWidget(controlGroup);
    
    // === Status Group ===
    QGroupBox* statusGroup = new QGroupBox("Node Status", this);
    QVBoxLayout* statusLayout = new QVBoxLayout(statusGroup);
    
    m_stateLabel = new QLabel("State: <b>Stopped</b>", this);
    m_blockHeightLabel = new QLabel("Block Height: <b>N/A</b>", this);
    m_syncStatusLabel = new QLabel("Sync Status: <b>N/A</b>", this);
    m_peerCountLabel = new QLabel("Peers: <b>N/A</b>", this);
    m_syncPhaseLabel = new QLabel("Sync Phase: <b>N/A</b>", this);
    m_lastErrorLabel = new QLabel("Last Error: <b>None</b>", this);
    m_lastBootstrapLabel = new QLabel("Last Bootstrap Contact: <b>N/A</b>", this);
    
    m_stateLabel->setTextFormat(Qt::RichText);
    m_blockHeightLabel->setTextFormat(Qt::RichText);
    m_syncStatusLabel->setTextFormat(Qt::RichText);
    
    statusLayout->addWidget(m_stateLabel);
    statusLayout->addWidget(m_blockHeightLabel);
    statusLayout->addWidget(m_syncStatusLabel);
    statusLayout->addWidget(m_peerCountLabel);
    statusLayout->addWidget(m_syncPhaseLabel);
    statusLayout->addWidget(m_lastErrorLabel);
    statusLayout->addWidget(m_lastBootstrapLabel);
    
    mainLayout->addWidget(statusGroup);
    
    // === Log Viewer Group ===
    QGroupBox* logGroup = new QGroupBox("Node Logs (Recent)", this);
    QVBoxLayout* logLayout = new QVBoxLayout(logGroup);
    
    m_logViewer = new QTextEdit(this);
    m_logViewer->setReadOnly(true);
    m_logViewer->setLineWrapMode(QTextEdit::NoWrap);
    m_logViewer->setFontFamily("Monospace");
    m_logViewer->setMaximumHeight(200);
    m_logViewer->setPlaceholderText("Node logs will appear here...");
    
    logLayout->addWidget(m_logViewer);
    
    mainLayout->addWidget(logGroup);
    
    // Connect signals
    connect(m_startButton, &QPushButton::clicked, this, &NodeControlWidget::onStartClicked);
    connect(m_stopButton, &QPushButton::clicked, this, &NodeControlWidget::onStopClicked);
    connect(m_restartButton, &QPushButton::clicked, this, &NodeControlWidget::onRestartClicked);
    connect(m_diagnosticsButton, &QPushButton::clicked, this, &NodeControlWidget::onDiagnosticsClicked);
    
    connect(m_openLogsButton, &QPushButton::clicked, this, &NodeControlWidget::onOpenLogsClicked);
    connect(m_resetDataButton, &QPushButton::clicked, this, &NodeControlWidget::onResetDataClicked);
    connect(m_copyDiagButton, &QPushButton::clicked, this, &NodeControlWidget::onDiagnosticsClicked);
    
    connect(m_nodeManager, &NodeManager::stateChanged, this, &NodeControlWidget::onNodeStateChanged);
    connect(m_nodeManager, &NodeManager::nodeReady, this, &NodeControlWidget::onNodeReady);
    connect(m_nodeManager, &NodeManager::error, this, &NodeControlWidget::onNodeError);
    connect(m_nodeManager, &NodeManager::syncProgress, this, &NodeControlWidget::onSyncProgress);
    connect(m_nodeManager, &NodeManager::healthTelemetryUpdated, this, &NodeControlWidget::onHealthTelemetryUpdated);
    connect(m_nodeManager, &NodeManager::logLinesAvailable, this, &NodeControlWidget::onLogLinesAvailable);
    connect(m_nodeManager, &NodeManager::nodeDegraded, this, &NodeControlWidget::onNodeDegraded);
    
    // Initial UI state
    updateUI();
}

void NodeControlWidget::onStartClicked()
{
    QString network = m_networkCombo->currentData().toString();
    
    if (m_nodeManager->startNode(network)) {
        m_logViewer->clear();
        m_logViewer->append(QString("Starting node on network: %1...").arg(network));
    }
}

void NodeControlWidget::onStopClicked()
{
    m_nodeManager->stopNode();
    m_logViewer->append("Stopping node...");
}

void NodeControlWidget::onRestartClicked()
{
    QString network = m_networkCombo->currentData().toString();
    m_nodeManager->restartNode(network);
    m_logViewer->clear();
    m_logViewer->append(QString("Restarting node on network: %1...").arg(network));
}

void NodeControlWidget::onDiagnosticsClicked()
{
    QString diagnostics = m_nodeManager->collectDiagnostics();
    
    // Copy to clipboard
    QClipboard* clipboard = QApplication::clipboard();
    clipboard->setText(diagnostics);
    
    // Show message box
    QMessageBox msgBox(this);
    msgBox.setWindowTitle("Node Diagnostics");
    msgBox.setText("Diagnostics copied to clipboard!");
    msgBox.setDetailedText(diagnostics);
    msgBox.setIcon(QMessageBox::Information);
    msgBox.exec();
}

void NodeControlWidget::onNodeStateChanged(NodeManager::State state)
{
    updateUI();
    
    QString stateStr = stateToString(state);
    m_logViewer->append(QString("Node state changed: %1").arg(stateStr));
}

void NodeControlWidget::onNodeReady()
{
    m_logViewer->append("✓ Node is ready and accepting RPC calls!");
}

void NodeControlWidget::onNodeError(const QString& message)
{
    m_logViewer->append(QString("✗ Error: %1").arg(message));
    
    QMessageBox::warning(this, "Node Error", message);
}

void NodeControlWidget::onNodeDegraded(const QString& reason)
{
    m_logViewer->append(QString("⚠ Node degraded: %1").arg(reason));
    m_degradedLabel->setText(QString("⚠️ Node degraded: %1. You can still use local wallet features.").arg(reason));
    m_degradedBanner->setVisible(true);
}

void NodeControlWidget::onOpenLogsClicked()
{
    m_nodeManager->openLogsFolder();
}

void NodeControlWidget::onResetDataClicked()
{
    QMessageBox::StandardButton reply = QMessageBox::question(
        this,
        "Reset Chain Data",
        "This will delete all local blockchain data and restart sync from scratch.\n\n"
        "Are you sure you want to continue?",
        QMessageBox::Yes | QMessageBox::No
    );
    
    if (reply == QMessageBox::Yes) {
        // Stop node first
        if (m_nodeManager->isRunning()) {
            m_nodeManager->stopNode();
        }
        
        // Determine chain ID from current network
        QString network = m_networkCombo->currentData().toString();
        int chainId = 1337;  // devnet
        if (network == "mainnet") chainId = 1;
        else if (network == "testnet") chainId = 2;
        
        // Reset data
        if (m_nodeManager->resetChainData(chainId)) {
            QMessageBox::information(this, "Success", "Chain data has been reset successfully.");
            m_logViewer->append("✓ Chain data reset successfully");
        } else {
            QMessageBox::critical(this, "Error", "Failed to reset chain data.");
            m_logViewer->append("✗ Failed to reset chain data");
        }
    }
}

void NodeControlWidget::onSyncProgress(int currentBlock, int highestBlock, bool syncing)
{
    if (syncing) {
        double progress = (highestBlock > 0) ? (100.0 * currentBlock / highestBlock) : 0.0;
        m_blockHeightLabel->setText(QString("Block Height: <b>%1 / %2</b>")
                                     .arg(currentBlock)
                                     .arg(highestBlock));
        m_syncStatusLabel->setText(QString("Sync Status: <b>Syncing (%1%)</b>")
                                    .arg(progress, 0, 'f', 1));
    } else {
        m_blockHeightLabel->setText(QString("Block Height: <b>%1</b>").arg(currentBlock));
        m_syncStatusLabel->setText("Sync Status: <b>Synced ✓</b>");
    }
}

void NodeControlWidget::onHealthTelemetryUpdated(
    int peerCount,
    int localHeight,
    int networkHeight,
    const QString& syncPhase,
    const QString& lastError,
    const QString& lastBootstrapContact,
    bool rpcReady,
    bool p2pReady,
    bool syncing,
    bool synced
)
{
    m_peerCountLabel->setText(QString("Peers: <b>%1</b>").arg(peerCount));
    m_blockHeightLabel->setText(QString("Block Height: <b>%1 / %2</b>").arg(localHeight).arg(networkHeight));
    m_syncPhaseLabel->setText(QString("Sync Phase: <b>%1</b>").arg(syncPhase.isEmpty() ? "N/A" : syncPhase));
    m_lastErrorLabel->setText(QString("Last Error: <b>%1</b>").arg(lastError.isEmpty() ? "None" : lastError.toHtmlEscaped()));
    m_lastBootstrapLabel->setText(QString("Last Bootstrap Contact: <b>%1</b>").arg(lastBootstrapContact.isEmpty() ? "N/A" : lastBootstrapContact));

    QString status;
    if (!rpcReady) {
        status = "Process running, RPC not ready";
    } else if (!p2pReady) {
        status = "RPC ready, waiting for peers";
    } else if (syncing) {
        status = "Syncing";
    } else if (synced) {
        status = "Synced ✓";
    } else {
        status = "P2P ready";
    }
    m_syncStatusLabel->setText(QString("Sync Status: <b>%1</b>").arg(status));
}

void NodeControlWidget::onLogLinesAvailable(const QStringList& lines)
{
    QScrollBar* scrollBar = m_logViewer->verticalScrollBar();
    const bool wasNearBottom = !scrollBar || (scrollBar->maximum() - scrollBar->value() <= 4);

    for (const QString& line : lines) {
        m_logViewer->append(line);
    }

    // Auto-scroll only if the user was already near the bottom.
    // This preserves manual scrolling when inspecting older log lines.
    if (wasNearBottom) {
        m_logViewer->moveCursor(QTextCursor::End);
        if (scrollBar) {
            scrollBar->setValue(scrollBar->maximum());
        }
    }
}

void NodeControlWidget::updateUI()
{
    NodeManager::State state = m_nodeManager->state();
    
    // Update state label with color
    QString stateStr = stateToString(state);
    QString color = stateColor(state);
    m_stateLabel->setText(QString("State: <b><span style='color: %1;'>%2</span></b>")
                          .arg(color, stateStr));
    
    // Show/hide degraded banner
    m_degradedBanner->setVisible(state == NodeManager::State::Degraded);
    
    // Enable/disable controls based on state
    bool canStart = (state == NodeManager::State::Stopped || state == NodeManager::State::Error);
    bool canStop = (state == NodeManager::State::ProcessRunning ||
                    state == NodeManager::State::RpcReady || 
                    state == NodeManager::State::P2PReady ||
                    state == NodeManager::State::Syncing ||
                    state == NodeManager::State::Synced ||
                    state == NodeManager::State::Healthy || 
                    state == NodeManager::State::Degraded || 
                    state == NodeManager::State::Starting);
    bool canRestart = (state == NodeManager::State::ProcessRunning ||
                       state == NodeManager::State::RpcReady ||
                       state == NodeManager::State::P2PReady ||
                       state == NodeManager::State::Syncing ||
                       state == NodeManager::State::Synced ||
                       state == NodeManager::State::Healthy || 
                       state == NodeManager::State::Degraded);
    
    m_startButton->setEnabled(canStart);
    m_stopButton->setEnabled(canStop);
    m_restartButton->setEnabled(canRestart);
    m_networkCombo->setEnabled(canStart);
    
    // Clear status if stopped
    if (state == NodeManager::State::Stopped || state == NodeManager::State::Error) {
        m_blockHeightLabel->setText("Block Height: <b>N/A</b>");
        m_syncStatusLabel->setText("Sync Status: <b>N/A</b>");
        m_peerCountLabel->setText("Peers: <b>N/A</b>");
        m_syncPhaseLabel->setText("Sync Phase: <b>N/A</b>");
        if (state == NodeManager::State::Stopped) {
            m_lastErrorLabel->setText("Last Error: <b>None</b>");
            m_lastBootstrapLabel->setText("Last Bootstrap Contact: <b>N/A</b>");
        }
    }
}

QString NodeControlWidget::stateToString(NodeManager::State state)
{
    switch (state) {
        case NodeManager::State::Stopped: return "Stopped";
        case NodeManager::State::Starting: return "Starting...";
        case NodeManager::State::ProcessRunning: return "Process Running";
        case NodeManager::State::RpcReady: return "RPC Ready";
        case NodeManager::State::P2PReady: return "P2P Ready";
        case NodeManager::State::Syncing: return "Syncing";
        case NodeManager::State::Synced: return "Synced";
        case NodeManager::State::Healthy: return "Running (Healthy)";
        case NodeManager::State::Degraded: return "Running (Degraded)";
        case NodeManager::State::Stopping: return "Stopping...";
        case NodeManager::State::Error: return "Error";
        default: return "Unknown";
    }
}

QString NodeControlWidget::stateColor(NodeManager::State state)
{
    switch (state) {
        case NodeManager::State::Stopped: return "#757575";  // Gray
        case NodeManager::State::Starting: return "#FF9800";  // Orange
        case NodeManager::State::ProcessRunning: return "#4A5568";
        case NodeManager::State::RpcReady: return "#2196F3";  // Blue
        case NodeManager::State::P2PReady: return "#0EA5E9";
        case NodeManager::State::Syncing: return "#F59E0B";
        case NodeManager::State::Synced: return "#4CAF50";
        case NodeManager::State::Healthy: return "#4CAF50";  // Green
        case NodeManager::State::Degraded: return "#FFC107";  // Amber/Warning
        case NodeManager::State::Stopping: return "#FF9800";  // Orange
        case NodeManager::State::Error: return "#f44336";  // Red
        default: return "#000000";  // Black
    }
}
