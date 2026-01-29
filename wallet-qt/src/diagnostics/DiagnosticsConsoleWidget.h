#ifndef DIAGNOSTICSCONSOLEWIDGET_H
#define DIAGNOSTICSCONSOLEWIDGET_H

#include "RoleManager.h"
#include "ConsoleExecutor.h"
#include <QWidget>
#include <QLineEdit>
#include <QTextBrowser>
#include <QStringList>
#include <QCompleter>

class QPushButton;
class QCheckBox;
class QLabel;

/**
 * @brief Console widget for diagnostics commands.
 * 
 * Features:
 * - Command input with autocomplete from allowlist
 * - Command history with up/down navigation
 * - JSON pretty-print in output
 * - Copy output and export session buttons
 * - Operator/Developer mode toggles
 * - Output is automatically redacted
 */
class DiagnosticsConsoleWidget : public QWidget
{
    Q_OBJECT

public:
    explicit DiagnosticsConsoleWidget(RoleManager* roleManager, 
                                     ConsoleExecutor* executor,
                                     QWidget* parent = nullptr);

    /**
     * @brief Clear console output.
     */
    void clearOutput();

    /**
     * @brief Get console session text.
     * @return Full session text with commands and outputs
     */
    QString getSessionText() const;

signals:
    void commandExecuted(const QString& command, bool success);

private slots:
    void onExecuteClicked();
    void onClearClicked();
    void onCopyOutputClicked();
    void onExportSessionClicked();
    void onOperatorModeToggled(bool enabled);
    void onDeveloperModeToggled(bool enabled);
    void onRoleChanged(RoleManager::Role role);

private:
    void setupUi();
    void setupConnections();
    void executeCommand(const QString& command);
    void appendOutput(const QString& text, const QString& style = QString());
    void updateCompleter();
    bool eventFilter(QObject* watched, QEvent* event) override;
    void navigateHistory(int direction);

    RoleManager* m_roleManager;
    ConsoleExecutor* m_executor;

    QLineEdit* m_commandInput;
    QTextBrowser* m_outputBrowser;
    QPushButton* m_executeButton;
    QPushButton* m_clearButton;
    QPushButton* m_copyButton;
    QPushButton* m_exportButton;
    QCheckBox* m_operatorModeCheckbox;
    QCheckBox* m_developerModeCheckbox;
    QLabel* m_roleLabel;
    QCompleter* m_completer;

    QStringList m_commandHistory;
    int m_historyIndex;
};

#endif // DIAGNOSTICSCONSOLEWIDGET_H
