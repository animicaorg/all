#ifndef DIAGNOSTICSSTATUSWIDGET_H
#define DIAGNOSTICSSTATUSWIDGET_H

#include "NodeController.h"
#include "RoleManager.h"
#include <QWidget>
#include <QTimer>
#include <QGroupBox>
#include <QLabel>

class QPushButton;

/**
 * @brief Node status dashboard widget for diagnostics.
 * 
 * Features:
 * - 4 status panels: Chain/Head, Sync, Peers, Mempool
 * - Action buttons: Bootstrap, Sync Force/Pause/Resume
 * - Role-gated buttons (disabled for User role)
 * - Auto-refresh every 5 seconds
 * - Confirmation dialogs for operator actions
 */
class DiagnosticsStatusWidget : public QWidget
{
    Q_OBJECT

public:
    explicit DiagnosticsStatusWidget(NodeController* controller, 
                                    RoleManager* roleManager,
                                    QWidget* parent = nullptr);

    /**
     * @brief Start auto-refresh timer.
     */
    void startAutoRefresh();

    /**
     * @brief Stop auto-refresh timer.
     */
    void stopAutoRefresh();

    /**
     * @brief Manually refresh status.
     */
    void refresh();

signals:
    void statusRefreshed(bool success);
    void actionExecuted(const QString& action, bool success);

private slots:
    void onRefreshClicked();
    void onBootstrapClicked();
    void onSyncForceClicked();
    void onSyncPauseClicked();
    void onSyncResumeClicked();
    void onRoleChanged(RoleManager::Role role);
    void onAutoRefresh();

private:
    void setupUi();
    void setupConnections();
    void updateStatusDisplay(const NodeController::NodeStatus& status);
    void updateActionButtons();
    QString formatTimestamp(qint64 timestamp);
    QString formatHashrate(double hashrate);

    NodeController* m_controller;
    RoleManager* m_roleManager;
    QTimer* m_autoRefreshTimer;

    // Status panels
    QGroupBox* m_chainPanel;
    QLabel* m_chainIdLabel;
    QLabel* m_headHeightLabel;
    QLabel* m_headHashLabel;
    QLabel* m_headTimestampLabel;

    QGroupBox* m_syncPanel;
    QLabel* m_syncPhaseLabel;
    QLabel* m_syncProgressLabel;
    QLabel* m_syncHeightLabel;
    QLabel* m_syncQueueLabel;

    QGroupBox* m_peersPanel;
    QLabel* m_peersInboundLabel;
    QLabel* m_peersOutboundLabel;
    QLabel* m_peersTotalLabel;
    QLabel* m_peersListenLabel;

    QGroupBox* m_mempoolPanel;
    QLabel* m_mempoolCountLabel;
    QLabel* m_mempoolRejectedLabel;
    QLabel* m_hashrateLabel;

    // Action buttons
    QPushButton* m_refreshButton;
    QPushButton* m_bootstrapButton;
    QPushButton* m_syncForceButton;
    QPushButton* m_syncPauseButton;
    QPushButton* m_syncResumeButton;

    QLabel* m_lastUpdateLabel;
};

#endif // DIAGNOSTICSSTATUSWIDGET_H
