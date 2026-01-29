#include "DiagnosticsStatusWidget.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QPushButton>
#include <QLabel>
#include <QGroupBox>
#include <QMessageBox>
#include <QDateTime>

DiagnosticsStatusWidget::DiagnosticsStatusWidget(NodeController* controller, 
                                                RoleManager* roleManager,
                                                QWidget* parent)
    : QWidget(parent)
    , m_controller(controller)
    , m_roleManager(roleManager)
    , m_autoRefreshTimer(new QTimer(this))
{
    setupUi();
    setupConnections();
    
    // Initial refresh
    refresh();
    
    // Setup auto-refresh
    m_autoRefreshTimer->setInterval(5000);  // 5 seconds
}

void DiagnosticsStatusWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);

    // Status panels grid
    QGridLayout* panelsLayout = new QGridLayout();

    // Chain panel
    m_chainPanel = new QGroupBox("Chain / Head", this);
    QVBoxLayout* chainLayout = new QVBoxLayout(m_chainPanel);
    m_chainIdLabel = new QLabel("Chain ID: -", this);
    m_headHeightLabel = new QLabel("Height: -", this);
    m_headHashLabel = new QLabel("Hash: -", this);
    m_headTimestampLabel = new QLabel("Timestamp: -", this);
    chainLayout->addWidget(m_chainIdLabel);
    chainLayout->addWidget(m_headHeightLabel);
    chainLayout->addWidget(m_headHashLabel);
    chainLayout->addWidget(m_headTimestampLabel);
    chainLayout->addStretch();
    panelsLayout->addWidget(m_chainPanel, 0, 0);

    // Sync panel
    m_syncPanel = new QGroupBox("Sync Status", this);
    QVBoxLayout* syncLayout = new QVBoxLayout(m_syncPanel);
    m_syncPhaseLabel = new QLabel("Phase: -", this);
    m_syncProgressLabel = new QLabel("Progress: -", this);
    m_syncHeightLabel = new QLabel("Height: -", this);
    m_syncQueueLabel = new QLabel("Queue: -", this);
    syncLayout->addWidget(m_syncPhaseLabel);
    syncLayout->addWidget(m_syncProgressLabel);
    syncLayout->addWidget(m_syncHeightLabel);
    syncLayout->addWidget(m_syncQueueLabel);
    syncLayout->addStretch();
    panelsLayout->addWidget(m_syncPanel, 0, 1);

    // Peers panel
    m_peersPanel = new QGroupBox("P2P Network", this);
    QVBoxLayout* peersLayout = new QVBoxLayout(m_peersPanel);
    m_peersInboundLabel = new QLabel("Inbound: -", this);
    m_peersOutboundLabel = new QLabel("Outbound: -", this);
    m_peersTotalLabel = new QLabel("Total: -", this);
    m_peersListenLabel = new QLabel("Listen: -", this);
    peersLayout->addWidget(m_peersInboundLabel);
    peersLayout->addWidget(m_peersOutboundLabel);
    peersLayout->addWidget(m_peersTotalLabel);
    peersLayout->addWidget(m_peersListenLabel);
    peersLayout->addStretch();
    panelsLayout->addWidget(m_peersPanel, 1, 0);

    // Mempool panel
    m_mempoolPanel = new QGroupBox("Mempool & Mining", this);
    QVBoxLayout* mempoolLayout = new QVBoxLayout(m_mempoolPanel);
    m_mempoolCountLabel = new QLabel("TX Count: -", this);
    m_mempoolRejectedLabel = new QLabel("Rejected (1h): -", this);
    m_hashrateLabel = new QLabel("Hashrate: -", this);
    mempoolLayout->addWidget(m_mempoolCountLabel);
    mempoolLayout->addWidget(m_mempoolRejectedLabel);
    mempoolLayout->addWidget(m_hashrateLabel);
    mempoolLayout->addStretch();
    panelsLayout->addWidget(m_mempoolPanel, 1, 1);

    mainLayout->addLayout(panelsLayout, 1);

    // Action buttons
    QHBoxLayout* actionsLayout = new QHBoxLayout();
    
    m_refreshButton = new QPushButton("Refresh Now", this);
    actionsLayout->addWidget(m_refreshButton);

    actionsLayout->addStretch();

    m_bootstrapButton = new QPushButton("Bootstrap", this);
    m_bootstrapButton->setToolTip("Connect to public bootstrap RPC (Operator)");
    actionsLayout->addWidget(m_bootstrapButton);

    m_syncForceButton = new QPushButton("Force Sync", this);
    m_syncForceButton->setToolTip("Trigger P2P sync round (Operator)");
    actionsLayout->addWidget(m_syncForceButton);

    m_syncPauseButton = new QPushButton("Pause Sync", this);
    m_syncPauseButton->setToolTip("Pause background sync (Operator)");
    actionsLayout->addWidget(m_syncPauseButton);

    m_syncResumeButton = new QPushButton("Resume Sync", this);
    m_syncResumeButton->setToolTip("Resume background sync (Operator)");
    actionsLayout->addWidget(m_syncResumeButton);

    mainLayout->addLayout(actionsLayout);

    // Last update label
    m_lastUpdateLabel = new QLabel("Last update: Never", this);
    m_lastUpdateLabel->setStyleSheet("color: gray;");
    mainLayout->addWidget(m_lastUpdateLabel);

    // Update button states
    updateActionButtons();
}

void DiagnosticsStatusWidget::setupConnections()
{
    connect(m_autoRefreshTimer, &QTimer::timeout, this, &DiagnosticsStatusWidget::onAutoRefresh);
    connect(m_refreshButton, &QPushButton::clicked, this, &DiagnosticsStatusWidget::onRefreshClicked);
    connect(m_bootstrapButton, &QPushButton::clicked, this, &DiagnosticsStatusWidget::onBootstrapClicked);
    connect(m_syncForceButton, &QPushButton::clicked, this, &DiagnosticsStatusWidget::onSyncForceClicked);
    connect(m_syncPauseButton, &QPushButton::clicked, this, &DiagnosticsStatusWidget::onSyncPauseClicked);
    connect(m_syncResumeButton, &QPushButton::clicked, this, &DiagnosticsStatusWidget::onSyncResumeClicked);
    connect(m_roleManager, &RoleManager::roleChanged, this, &DiagnosticsStatusWidget::onRoleChanged);
}

void DiagnosticsStatusWidget::startAutoRefresh()
{
    m_autoRefreshTimer->start();
}

void DiagnosticsStatusWidget::stopAutoRefresh()
{
    m_autoRefreshTimer->stop();
}

void DiagnosticsStatusWidget::refresh()
{
    NodeController::NodeStatus status = m_controller->queryStatus();
    updateStatusDisplay(status);
    
    m_lastUpdateLabel->setText("Last update: " + QDateTime::currentDateTime().toString("yyyy-MM-dd HH:mm:ss"));
    
    emit statusRefreshed(status.available);
}

void DiagnosticsStatusWidget::onRefreshClicked()
{
    refresh();
}

void DiagnosticsStatusWidget::onAutoRefresh()
{
    refresh();
}

void DiagnosticsStatusWidget::onBootstrapClicked()
{
    QMessageBox::StandardButton reply = QMessageBox::question(
        this,
        "Confirm Bootstrap",
        "This will connect to the public bootstrap RPC to sync chain state.\n\nContinue?",
        QMessageBox::Yes | QMessageBox::No
    );

    if (reply != QMessageBox::Yes) {
        return;
    }

    QString result = m_controller->triggerBootstrap();
    bool success = !result.contains("error", Qt::CaseInsensitive);
    
    QMessageBox::information(this, "Bootstrap Result", result);
    emit actionExecuted("Bootstrap", success);
    
    // Refresh status
    refresh();
}

void DiagnosticsStatusWidget::onSyncForceClicked()
{
    QMessageBox::StandardButton reply = QMessageBox::question(
        this,
        "Confirm Force Sync",
        "This will trigger a P2P sync round.\n\nContinue?",
        QMessageBox::Yes | QMessageBox::No
    );

    if (reply != QMessageBox::Yes) {
        return;
    }

    QString result = m_controller->forceSyncRound();
    bool success = !result.contains("error", Qt::CaseInsensitive);
    
    QMessageBox::information(this, "Force Sync Result", result);
    emit actionExecuted("Force Sync", success);
    
    // Refresh status
    refresh();
}

void DiagnosticsStatusWidget::onSyncPauseClicked()
{
    QString result = m_controller->pauseSync();
    bool success = !result.contains("error", Qt::CaseInsensitive);
    
    QMessageBox::information(this, "Pause Sync Result", result);
    emit actionExecuted("Pause Sync", success);
    
    // Refresh status
    refresh();
}

void DiagnosticsStatusWidget::onSyncResumeClicked()
{
    QString result = m_controller->resumeSync();
    bool success = !result.contains("error", Qt::CaseInsensitive);
    
    QMessageBox::information(this, "Resume Sync Result", result);
    emit actionExecuted("Resume Sync", success);
    
    // Refresh status
    refresh();
}

void DiagnosticsStatusWidget::onRoleChanged(RoleManager::Role role)
{
    Q_UNUSED(role);
    updateActionButtons();
}

void DiagnosticsStatusWidget::updateStatusDisplay(const NodeController::NodeStatus& status)
{
    if (!status.available) {
        m_chainIdLabel->setText("Chain ID: N/A (Node offline)");
        m_headHeightLabel->setText("Height: N/A");
        m_headHashLabel->setText("Hash: N/A");
        m_headTimestampLabel->setText("Timestamp: N/A");
        
        m_syncPhaseLabel->setText("Phase: N/A");
        m_syncProgressLabel->setText("Progress: N/A");
        m_syncHeightLabel->setText("Height: N/A");
        m_syncQueueLabel->setText("Queue: N/A");
        
        m_peersInboundLabel->setText("Inbound: N/A");
        m_peersOutboundLabel->setText("Outbound: N/A");
        m_peersTotalLabel->setText("Total: N/A");
        m_peersListenLabel->setText("Listen: N/A");
        
        m_mempoolCountLabel->setText("TX Count: N/A");
        m_mempoolRejectedLabel->setText("Rejected (1h): N/A");
        m_hashrateLabel->setText("Hashrate: N/A");
        
        return;
    }

    // Chain status
    m_chainIdLabel->setText(QString("Chain ID: %1").arg(status.chain.chainId));
    m_headHeightLabel->setText(QString("Height: %1").arg(status.chain.headHeight));
    m_headHashLabel->setText(QString("Hash: %1").arg(status.chain.headHash.left(16) + "..."));
    m_headTimestampLabel->setText(QString("Timestamp: %1").arg(formatTimestamp(status.chain.headTimestamp)));

    // Sync status
    m_syncPhaseLabel->setText(QString("Phase: %1").arg(status.sync.phase));
    m_syncProgressLabel->setText(QString("Progress: %1%").arg(status.sync.progress * 100, 0, 'f', 1));
    m_syncHeightLabel->setText(QString("Height: %1 / %2").arg(status.sync.currentHeight).arg(status.sync.targetHeight));
    m_syncQueueLabel->setText(QString("Queue: %1 blocks, %2 in-flight").arg(status.sync.queueDepth).arg(status.sync.inFlightHeaders));

    // Peers status
    m_peersInboundLabel->setText(QString("Inbound: %1").arg(status.peers.inbound));
    m_peersOutboundLabel->setText(QString("Outbound: %1").arg(status.peers.outbound));
    m_peersTotalLabel->setText(QString("Total: %1").arg(status.peers.total));
    
    QString listenAddrs = status.peers.listenAddrs.isEmpty() ? "None" : status.peers.listenAddrs.join(", ");
    m_peersListenLabel->setText(QString("Listen: %1").arg(listenAddrs));

    // Mempool status
    m_mempoolCountLabel->setText(QString("TX Count: %1").arg(status.mempool.txCount));
    m_mempoolRejectedLabel->setText(QString("Rejected (1h): %1").arg(status.mempool.rejectedLast1h));
    m_hashrateLabel->setText(QString("Hashrate: %1").arg(formatHashrate(status.hashrate.hashrateSps)));
}

void DiagnosticsStatusWidget::updateActionButtons()
{
    bool canOperate = m_roleManager->getCurrentRole() >= RoleManager::Role::Operator;
    
    m_bootstrapButton->setEnabled(canOperate);
    m_syncForceButton->setEnabled(canOperate);
    m_syncPauseButton->setEnabled(canOperate);
    m_syncResumeButton->setEnabled(canOperate);
}

QString DiagnosticsStatusWidget::formatTimestamp(qint64 timestamp)
{
    if (timestamp == 0) {
        return "N/A";
    }
    return QDateTime::fromSecsSinceEpoch(timestamp).toString("yyyy-MM-dd HH:mm:ss");
}

QString DiagnosticsStatusWidget::formatHashrate(double hashrate)
{
    if (hashrate < 1000) {
        return QString("%1 H/s").arg(hashrate, 0, 'f', 2);
    } else if (hashrate < 1000000) {
        return QString("%1 KH/s").arg(hashrate / 1000, 0, 'f', 2);
    } else if (hashrate < 1000000000) {
        return QString("%1 MH/s").arg(hashrate / 1000000, 0, 'f', 2);
    } else {
        return QString("%1 GH/s").arg(hashrate / 1000000000, 0, 'f', 2);
    }
}
