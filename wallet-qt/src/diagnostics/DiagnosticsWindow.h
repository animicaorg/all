#ifndef DIAGNOSTICSWINDOW_H
#define DIAGNOSTICSWINDOW_H

#include <QMainWindow>
#include <QTabWidget>

class RoleManager;
class ConsoleExecutor;
class NodeController;
class AnimicaRpcClient;
class NodeManager;
class DiagnosticsConsoleWidget;
class DiagnosticsStatusWidget;
class DiagnosticsLogsWidget;

/**
 * @brief Main diagnostics window for Animica wallet.
 * 
 * Provides 3 tabs:
 * - Console: Command execution with allowlists
 * - Node Status: Real-time status dashboard
 * - Logs: Log viewer with filtering
 * 
 * Menu bar with:
 * - File > Close
 * - Settings > Operator Mode, Developer Mode
 */
class DiagnosticsWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit DiagnosticsWindow(AnimicaRpcClient* rpcClient, 
                              NodeManager* nodeManager,
                              QWidget* parent = nullptr);
    ~DiagnosticsWindow() override;

    /**
     * @brief Show window and activate.
     */
    void showAndActivate();

protected:
    void closeEvent(QCloseEvent* event) override;

private:
    enum TabIndex {
        ConsoleTab = 0,
        StatusTab = 1,
        LogsTab = 2
    };

    void setupUi();
    void setupMenuBar();
    void setupConnections();

    // Core components
    RoleManager* m_roleManager;
    ConsoleExecutor* m_executor;
    NodeController* m_controller;
    AnimicaRpcClient* m_rpcClient;
    NodeManager* m_nodeManager;

    // UI components
    QTabWidget* m_tabWidget;
    DiagnosticsConsoleWidget* m_consoleWidget;
    DiagnosticsStatusWidget* m_statusWidget;
    DiagnosticsLogsWidget* m_logsWidget;
};

#endif // DIAGNOSTICSWINDOW_H
