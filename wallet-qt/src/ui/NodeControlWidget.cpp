#include "NodeControlWidget.h"
#include <QGroupBox>
#include <QHBoxLayout>
#include <QMessageBox>
#include <QClipboard>
#include <QApplication>

NodeControlWidget::NodeControlWidget(NodeManager* nodeManager, QWidget* parent)
    : QWidget(parent)
    , m_nodeManager(nodeManager)
{
    // Create UI elements
    QVBoxLayout* mainLayout = new QVBoxLayout(this);
    
    // === Network Selection Group ===
    QGroupBox* networkGroup = new QGroupBox("Network", this);
    QHBoxLayout* networkLayout = new QHBoxLayout(networkGroup);
    
    m_networkCombo = new QComboBox(this);
    m_networkCombo->addItem("Devnet (Local Development)", "devnet");
    m_networkCombo->addItem("Testnet (Public Test Network)", "testnet");
    m_networkCombo->addItem("Mainnet (Production)", "mainnet");
    m_networkCombo->setCurrentIndex(0);  // Default to devnet
    
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
    
    m_stateLabel->setTextFormat(Qt::RichText);
    m_blockHeightLabel->setTextFormat(Qt::RichText);
    m_syncStatusLabel->setTextFormat(Qt::RichText);
    
    statusLayout->addWidget(m_stateLabel);
    statusLayout->addWidget(m_blockHeightLabel);
    statusLayout->addWidget(m_syncStatusLabel);
    
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
    
    connect(m_nodeManager, &NodeManager::stateChanged, this, &NodeControlWidget::onNodeStateChanged);
    connect(m_nodeManager, &NodeManager::nodeReady, this, &NodeControlWidget::onNodeReady);
    connect(m_nodeManager, &NodeManager::error, this, &NodeControlWidget::onNodeError);
    connect(m_nodeManager, &NodeManager::syncProgress, this, &NodeControlWidget::onSyncProgress);
    connect(m_nodeManager, &NodeManager::logLinesAvailable, this, &NodeControlWidget::onLogLinesAvailable);
    
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

void NodeControlWidget::onLogLinesAvailable(const QStringList& lines)
{
    for (const QString& line : lines) {
        m_logViewer->append(line);
    }
    
    // Auto-scroll to bottom
    m_logViewer->moveCursor(QTextCursor::End);
}

void NodeControlWidget::updateUI()
{
    NodeManager::State state = m_nodeManager->state();
    
    // Update state label with color
    QString stateStr = stateToString(state);
    QString color = stateColor(state);
    m_stateLabel->setText(QString("State: <b><span style='color: %1;'>%2</span></b>")
                          .arg(color, stateStr));
    
    // Enable/disable controls based on state
    bool canStart = (state == NodeManager::State::Stopped || state == NodeManager::State::Error);
    bool canStop = (state == NodeManager::State::Running || state == NodeManager::State::Starting);
    bool canRestart = (state == NodeManager::State::Running);
    
    m_startButton->setEnabled(canStart);
    m_stopButton->setEnabled(canStop);
    m_restartButton->setEnabled(canRestart);
    m_networkCombo->setEnabled(canStart);
    
    // Clear status if stopped
    if (state == NodeManager::State::Stopped || state == NodeManager::State::Error) {
        m_blockHeightLabel->setText("Block Height: <b>N/A</b>");
        m_syncStatusLabel->setText("Sync Status: <b>N/A</b>");
    }
}

QString NodeControlWidget::stateToString(NodeManager::State state)
{
    switch (state) {
        case NodeManager::State::Stopped: return "Stopped";
        case NodeManager::State::Starting: return "Starting...";
        case NodeManager::State::Running: return "Running";
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
        case NodeManager::State::Running: return "#4CAF50";  // Green
        case NodeManager::State::Stopping: return "#FF9800";  // Orange
        case NodeManager::State::Error: return "#f44336";  // Red
        default: return "#000000";  // Black
    }
}
