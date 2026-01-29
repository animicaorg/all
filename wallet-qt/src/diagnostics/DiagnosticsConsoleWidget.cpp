#include "DiagnosticsConsoleWidget.h"
#include "CommandAllowlist.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPushButton>
#include <QCheckBox>
#include <QLabel>
#include <QFileDialog>
#include <QClipboard>
#include <QApplication>
#include <QKeyEvent>
#include <QStringListModel>
#include <QMessageBox>
#include <QDateTime>

DiagnosticsConsoleWidget::DiagnosticsConsoleWidget(RoleManager* roleManager, 
                                                   ConsoleExecutor* executor,
                                                   QWidget* parent)
    : QWidget(parent)
    , m_roleManager(roleManager)
    , m_executor(executor)
    , m_historyIndex(-1)
{
    setupUi();
    setupConnections();
    updateCompleter();
}

void DiagnosticsConsoleWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);

    // Output browser
    m_outputBrowser = new QTextBrowser(this);
    m_outputBrowser->setReadOnly(true);
    m_outputBrowser->setOpenExternalLinks(false);
    m_outputBrowser->setFont(QFont("Courier New", 10));
    mainLayout->addWidget(m_outputBrowser, 1);

    // Input area
    QHBoxLayout* inputLayout = new QHBoxLayout();
    
    m_commandInput = new QLineEdit(this);
    m_commandInput->setPlaceholderText("Enter command (e.g., 'node status' or 'rpc call node.getStatus')");
    m_commandInput->installEventFilter(this);
    inputLayout->addWidget(m_commandInput, 1);

    m_executeButton = new QPushButton("Execute", this);
    m_executeButton->setDefault(true);
    inputLayout->addWidget(m_executeButton);

    mainLayout->addLayout(inputLayout);

    // Action buttons
    QHBoxLayout* buttonLayout = new QHBoxLayout();
    
    m_clearButton = new QPushButton("Clear", this);
    buttonLayout->addWidget(m_clearButton);

    m_copyButton = new QPushButton("Copy Output", this);
    buttonLayout->addWidget(m_copyButton);

    m_exportButton = new QPushButton("Export Session", this);
    buttonLayout->addWidget(m_exportButton);

    buttonLayout->addStretch();

    // Role settings
    m_operatorModeCheckbox = new QCheckBox("Operator Mode", this);
    m_operatorModeCheckbox->setChecked(m_roleManager->isOperatorEnabled());
    buttonLayout->addWidget(m_operatorModeCheckbox);

    m_developerModeCheckbox = new QCheckBox("Developer Mode", this);
    m_developerModeCheckbox->setChecked(m_roleManager->isDeveloperEnabled());
    buttonLayout->addWidget(m_developerModeCheckbox);

    m_roleLabel = new QLabel(this);
    m_roleLabel->setStyleSheet("font-weight: bold;");
    buttonLayout->addWidget(m_roleLabel);

    mainLayout->addLayout(buttonLayout);

    // Initial role display
    onRoleChanged(m_roleManager->getCurrentRole());

    // Welcome message
    appendOutput("Animica Diagnostics Console", "color: blue; font-weight: bold;");
    appendOutput("Current role: " + RoleManager::roleToString(m_roleManager->getCurrentRole()), "color: gray;");
    appendOutput("Type commands or use autocomplete. Press Up/Down for history.\n", "color: gray;");
}

void DiagnosticsConsoleWidget::setupConnections()
{
    connect(m_executeButton, &QPushButton::clicked, this, &DiagnosticsConsoleWidget::onExecuteClicked);
    connect(m_commandInput, &QLineEdit::returnPressed, this, &DiagnosticsConsoleWidget::onExecuteClicked);
    connect(m_clearButton, &QPushButton::clicked, this, &DiagnosticsConsoleWidget::onClearClicked);
    connect(m_copyButton, &QPushButton::clicked, this, &DiagnosticsConsoleWidget::onCopyOutputClicked);
    connect(m_exportButton, &QPushButton::clicked, this, &DiagnosticsConsoleWidget::onExportSessionClicked);
    connect(m_operatorModeCheckbox, &QCheckBox::toggled, this, &DiagnosticsConsoleWidget::onOperatorModeToggled);
    connect(m_developerModeCheckbox, &QCheckBox::toggled, this, &DiagnosticsConsoleWidget::onDeveloperModeToggled);
    connect(m_roleManager, &RoleManager::roleChanged, this, &DiagnosticsConsoleWidget::onRoleChanged);
}

void DiagnosticsConsoleWidget::onExecuteClicked()
{
    QString command = m_commandInput->text().trimmed();
    if (command.isEmpty()) {
        return;
    }

    executeCommand(command);
    
    // Add to history
    if (m_commandHistory.isEmpty() || m_commandHistory.last() != command) {
        m_commandHistory.append(command);
        if (m_commandHistory.size() > 100) {
            m_commandHistory.removeFirst();
        }
    }
    m_historyIndex = m_commandHistory.size();

    m_commandInput->clear();
}

void DiagnosticsConsoleWidget::executeCommand(const QString& command)
{
    // Display command
    appendOutput("\n> " + command, "color: green; font-weight: bold;");

    // Check allowlist
    RoleManager::Role role = m_roleManager->getCurrentRole();
    if (!CommandAllowlist::isCommandAllowed(command, role)) {
        appendOutput("Error: Command not allowed for current role (" + 
                    RoleManager::roleToString(role) + ")", "color: red;");
        emit commandExecuted(command, false);
        return;
    }

    // Execute
    ConsoleExecutor::ExecutionResult result = m_executor->execute(command);

    // Display result
    if (result.success) {
        appendOutput(result.output);
        if (result.truncated) {
            appendOutput("(Output was truncated)", "color: orange;");
        }
        appendOutput(QString("(Completed in %1ms)").arg(result.durationMs), "color: gray;");
    } else {
        QString errorMsg = result.error.isEmpty() ? "Command failed" : result.error;
        if (result.timedOut) {
            errorMsg = "Timeout: " + errorMsg;
        }
        appendOutput("Error: " + errorMsg, "color: red;");
        if (!result.output.isEmpty()) {
            appendOutput(result.output);
        }
    }

    emit commandExecuted(command, result.success);
}

void DiagnosticsConsoleWidget::appendOutput(const QString& text, const QString& style)
{
    QString html;
    if (style.isEmpty()) {
        html = QString("<pre>%1</pre>").arg(text.toHtmlEscaped());
    } else {
        html = QString("<pre style=\"%1\">%2</pre>").arg(style, text.toHtmlEscaped());
    }
    m_outputBrowser->append(html);
}

void DiagnosticsConsoleWidget::onClearClicked()
{
    m_outputBrowser->clear();
    appendOutput("Console cleared", "color: gray;");
}

void DiagnosticsConsoleWidget::clearOutput()
{
    m_outputBrowser->clear();
}

QString DiagnosticsConsoleWidget::getSessionText() const
{
    return m_outputBrowser->toPlainText();
}

void DiagnosticsConsoleWidget::onCopyOutputClicked()
{
    QApplication::clipboard()->setText(getSessionText());
    appendOutput("Output copied to clipboard", "color: gray;");
}

void DiagnosticsConsoleWidget::onExportSessionClicked()
{
    QString fileName = QFileDialog::getSaveFileName(
        this,
        "Export Console Session",
        QString("console_session_%1.txt").arg(QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss")),
        "Text Files (*.txt);;All Files (*)"
    );

    if (fileName.isEmpty()) {
        return;
    }

    QFile file(fileName);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QMessageBox::warning(this, "Export Failed", "Failed to open file for writing: " + file.errorString());
        return;
    }

    QTextStream out(&file);
    out << getSessionText();
    file.close();

    appendOutput("Session exported to: " + fileName, "color: gray;");
}

void DiagnosticsConsoleWidget::onOperatorModeToggled(bool enabled)
{
    m_roleManager->setOperatorEnabled(enabled);
    updateCompleter();
}

void DiagnosticsConsoleWidget::onDeveloperModeToggled(bool enabled)
{
    m_roleManager->setDeveloperEnabled(enabled);
    updateCompleter();
}

void DiagnosticsConsoleWidget::onRoleChanged(RoleManager::Role role)
{
    QString roleText = "Role: " + RoleManager::roleToString(role);
    m_roleLabel->setText(roleText);

    // Update checkboxes without triggering signals
    m_operatorModeCheckbox->blockSignals(true);
    m_developerModeCheckbox->blockSignals(true);
    
    m_operatorModeCheckbox->setChecked(m_roleManager->isOperatorEnabled());
    m_developerModeCheckbox->setChecked(m_roleManager->isDeveloperEnabled());
    
    m_operatorModeCheckbox->blockSignals(false);
    m_developerModeCheckbox->blockSignals(false);
}

void DiagnosticsConsoleWidget::updateCompleter()
{
    // Get allowed commands for current role
    QSet<QString> commands = CommandAllowlist::getAllowedCommands(m_roleManager->getCurrentRole());
    QStringList commandList = commands.values();
    commandList.sort();

    // Delete old completer if it exists
    if (m_completer) {
        m_completer->deleteLater();
        m_completer = nullptr;
    }
    
    // Create new completer
    m_completer = new QCompleter(commandList, this);
    m_completer->setCaseSensitivity(Qt::CaseInsensitive);
    m_completer->setCompletionMode(QCompleter::PopupCompletion);
    m_commandInput->setCompleter(m_completer);
}

bool DiagnosticsConsoleWidget::eventFilter(QObject* watched, QEvent* event)
{
    if (watched == m_commandInput && event->type() == QEvent::KeyPress) {
        QKeyEvent* keyEvent = static_cast<QKeyEvent*>(event);
        
        if (keyEvent->key() == Qt::Key_Up) {
            navigateHistory(-1);
            return true;
        } else if (keyEvent->key() == Qt::Key_Down) {
            navigateHistory(1);
            return true;
        }
    }

    return QWidget::eventFilter(watched, event);
}

void DiagnosticsConsoleWidget::navigateHistory(int direction)
{
    if (m_commandHistory.isEmpty()) {
        return;
    }

    m_historyIndex += direction;
    
    if (m_historyIndex < 0) {
        m_historyIndex = 0;
    } else if (m_historyIndex >= m_commandHistory.size()) {
        m_historyIndex = m_commandHistory.size();
        m_commandInput->clear();
        return;
    }

    m_commandInput->setText(m_commandHistory.at(m_historyIndex));
}
