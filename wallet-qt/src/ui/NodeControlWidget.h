#ifndef NODECONTROLWIDGET_H
#define NODECONTROLWIDGET_H

#include <QWidget>
#include <QPushButton>
#include <QLabel>
#include <QComboBox>
#include <QTextEdit>
#include <QVBoxLayout>
#include "../node/NodeManager.h"

/**
 * @brief UI widget for controlling the embedded node.
 * 
 * Provides:
 * - Start/Stop/Restart buttons
 * - Network selection dropdown
 * - Status display (state, block height, peer count)
 * - Log viewer (last N lines from node log)
 * - Diagnostics button
 */
class NodeControlWidget : public QWidget
{
    Q_OBJECT

public:
    explicit NodeControlWidget(NodeManager* nodeManager, QWidget* parent = nullptr);

private slots:
    void onStartClicked();
    void onStopClicked();
    void onRestartClicked();
    void onDiagnosticsClicked();
    void onOpenLogsClicked();
    void onResetDataClicked();
    
    void onNodeStateChanged(NodeManager::State state);
    void onNodeReady();
    void onNodeError(const QString& message);
    void onSyncProgress(int currentBlock, int highestBlock, bool syncing);
    void onHealthTelemetryUpdated(
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
    );
    void onLogLinesAvailable(const QStringList& lines);
    void onNodeDegraded(const QString& reason);

private:
    void updateUI();
    QString stateToString(NodeManager::State state);
    QString stateColor(NodeManager::State state);
    
    NodeManager* m_nodeManager;
    
    // UI elements
    QWidget* m_degradedBanner;
    QLabel* m_degradedLabel;
    QPushButton* m_openLogsButton;
    QPushButton* m_resetDataButton;
    QPushButton* m_copyDiagButton;
    
    QComboBox* m_networkCombo;
    QPushButton* m_startButton;
    QPushButton* m_stopButton;
    QPushButton* m_restartButton;
    QPushButton* m_diagnosticsButton;
    
    QLabel* m_stateLabel;
    QLabel* m_blockHeightLabel;
    QLabel* m_syncStatusLabel;
    QLabel* m_peerCountLabel;
    QLabel* m_syncPhaseLabel;
    QLabel* m_lastErrorLabel;
    QLabel* m_lastBootstrapLabel;
    
    QTextEdit* m_logViewer;
};

#endif // NODECONTROLWIDGET_H
