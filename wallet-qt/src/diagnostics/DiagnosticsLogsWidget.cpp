#include "DiagnosticsLogsWidget.h"
#include "Redactor.h"
#include "../node/NodeManager.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QCheckBox>
#include <QFileDialog>
#include <QMessageBox>
#include <QDateTime>
#include <QMutexLocker>
#include <QScrollBar>

DiagnosticsLogsWidget::DiagnosticsLogsWidget(NodeManager* nodeManager, QWidget* parent)
    : QWidget(parent)
    , m_nodeManager(nodeManager)
    , m_maxLogLines(10000)
    , m_paused(false)
    , m_autoScroll(true)
{
    setupUi();
    setupConnections();
}

void DiagnosticsLogsWidget::setupUi()
{
    QVBoxLayout* mainLayout = new QVBoxLayout(this);

    // Filter controls
    QHBoxLayout* filterLayout = new QHBoxLayout();
    
    filterLayout->addWidget(new QLabel("Search:", this));
    m_searchBox = new QLineEdit(this);
    m_searchBox->setPlaceholderText("Search logs...");
    filterLayout->addWidget(m_searchBox, 1);

    filterLayout->addWidget(new QLabel("Level:", this));
    m_levelFilter = new QComboBox(this);
    m_levelFilter->addItem("All");
    m_levelFilter->addItem("ERROR");
    m_levelFilter->addItem("WARNING");
    m_levelFilter->addItem("INFO");
    m_levelFilter->addItem("DEBUG");
    filterLayout->addWidget(m_levelFilter);

    filterLayout->addWidget(new QLabel("Component:", this));
    m_componentFilter = new QLineEdit(this);
    m_componentFilter->setPlaceholderText("e.g., p2p, sync...");
    filterLayout->addWidget(m_componentFilter);

    mainLayout->addLayout(filterLayout);

    // Logs browser
    m_logsBrowser = new QTextBrowser(this);
    m_logsBrowser->setReadOnly(true);
    m_logsBrowser->setFont(QFont("Courier New", 9));
    m_logsBrowser->setLineWrapMode(QTextEdit::NoWrap);
    mainLayout->addWidget(m_logsBrowser, 1);

    // Control buttons
    QHBoxLayout* controlLayout = new QHBoxLayout();
    
    m_pauseButton = new QPushButton("Pause", this);
    m_pauseButton->setCheckable(true);
    controlLayout->addWidget(m_pauseButton);

    m_clearButton = new QPushButton("Clear", this);
    controlLayout->addWidget(m_clearButton);

    m_exportButton = new QPushButton("Export Logs", this);
    controlLayout->addWidget(m_exportButton);

    controlLayout->addStretch();

    m_autoScrollCheckbox = new QCheckBox("Auto-scroll", this);
    m_autoScrollCheckbox->setChecked(true);
    controlLayout->addWidget(m_autoScrollCheckbox);

    mainLayout->addLayout(controlLayout);
}

void DiagnosticsLogsWidget::setupConnections()
{
    connect(m_searchBox, &QLineEdit::textChanged, this, &DiagnosticsLogsWidget::onSearchTextChanged);
    connect(m_levelFilter, QOverload<int>::of(&QComboBox::currentIndexChanged), 
            this, &DiagnosticsLogsWidget::onLevelFilterChanged);
    connect(m_componentFilter, &QLineEdit::textChanged, this, &DiagnosticsLogsWidget::onComponentFilterChanged);
    connect(m_pauseButton, &QPushButton::toggled, this, &DiagnosticsLogsWidget::onPauseToggled);
    connect(m_clearButton, &QPushButton::clicked, this, &DiagnosticsLogsWidget::onClearClicked);
    connect(m_exportButton, &QPushButton::clicked, this, &DiagnosticsLogsWidget::onExportClicked);
    connect(m_autoScrollCheckbox, &QCheckBox::toggled, this, &DiagnosticsLogsWidget::onAutoScrollToggled);

    // Connect to NodeManager log signals
    if (m_nodeManager) {
        connect(m_nodeManager, &NodeManager::logLinesAvailable, 
                this, &DiagnosticsLogsWidget::appendLogLines);
    }
}

void DiagnosticsLogsWidget::appendLogLines(const QStringList& lines)
{
    if (m_paused) {
        return;
    }

    QMutexLocker locker(&m_bufferMutex);

    for (const QString& line : lines) {
        addToRingBuffer(line);
    }

    locker.unlock();

    // Update display
    updateFilteredDisplay();
}

void DiagnosticsLogsWidget::addToRingBuffer(const QString& line)
{
    m_logRingBuffer.append(line);
    
    // Maintain ring buffer size
    while (m_logRingBuffer.size() > m_maxLogLines) {
        m_logRingBuffer.removeFirst();
    }
}

void DiagnosticsLogsWidget::clearLogs()
{
    QMutexLocker locker(&m_bufferMutex);
    m_logRingBuffer.clear();
    m_logsBrowser->clear();
}

QString DiagnosticsLogsWidget::getFilteredLogs() const
{
    return m_logsBrowser->toPlainText();
}

void DiagnosticsLogsWidget::onSearchTextChanged(const QString& text)
{
    Q_UNUSED(text);
    updateFilteredDisplay();
}

void DiagnosticsLogsWidget::onLevelFilterChanged(int index)
{
    Q_UNUSED(index);
    updateFilteredDisplay();
}

void DiagnosticsLogsWidget::onComponentFilterChanged(const QString& text)
{
    Q_UNUSED(text);
    updateFilteredDisplay();
}

void DiagnosticsLogsWidget::onPauseToggled(bool paused)
{
    m_paused = paused;
    m_pauseButton->setText(paused ? "Resume" : "Pause");
}

void DiagnosticsLogsWidget::onClearClicked()
{
    clearLogs();
}

void DiagnosticsLogsWidget::onExportClicked()
{
    QString fileName = QFileDialog::getSaveFileName(
        this,
        "Export Logs",
        QString("logs_%1.txt").arg(QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss")),
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

    QMutexLocker locker(&m_bufferMutex);
    
    QTextStream out(&file);
    for (const QString& line : m_logRingBuffer) {
        // Apply redaction before export
        out << Redactor::redact(line) << "\n";
    }
    
    file.close();

    QMessageBox::information(this, "Export Complete", "Logs exported to: " + fileName);
    emit logExported(fileName);
}

void DiagnosticsLogsWidget::onAutoScrollToggled(bool enabled)
{
    m_autoScroll = enabled;
}

void DiagnosticsLogsWidget::updateFilteredDisplay()
{
    QMutexLocker locker(&m_bufferMutex);

    // Get current scroll position
    QScrollBar* scrollBar = m_logsBrowser->verticalScrollBar();
    bool wasAtBottom = (scrollBar->value() == scrollBar->maximum());

    // Build filtered output
    QStringList filteredLines;
    for (const QString& line : m_logRingBuffer) {
        if (matchesFilters(line)) {
            filteredLines.append(line);
        }
    }

    locker.unlock();

    // Update display
    m_logsBrowser->clear();
    
    for (const QString& line : filteredLines) {
        // Apply syntax highlighting based on level
        QString styledLine;
        if (line.contains("ERROR", Qt::CaseInsensitive)) {
            styledLine = QString("<span style='color: red;'>%1</span>").arg(line.toHtmlEscaped());
        } else if (line.contains("WARNING", Qt::CaseInsensitive) || line.contains("WARN", Qt::CaseInsensitive)) {
            styledLine = QString("<span style='color: orange;'>%1</span>").arg(line.toHtmlEscaped());
        } else if (line.contains("INFO", Qt::CaseInsensitive)) {
            styledLine = QString("<span style='color: black;'>%1</span>").arg(line.toHtmlEscaped());
        } else {
            styledLine = QString("<span style='color: gray;'>%1</span>").arg(line.toHtmlEscaped());
        }
        
        m_logsBrowser->append(styledLine);
    }

    // Auto-scroll to bottom if enabled and was at bottom
    if (m_autoScroll && wasAtBottom) {
        scrollBar->setValue(scrollBar->maximum());
    }
}

bool DiagnosticsLogsWidget::matchesFilters(const QString& line)
{
    // Search filter
    QString searchText = m_searchBox->text().trimmed();
    if (!searchText.isEmpty()) {
        if (!line.contains(searchText, Qt::CaseInsensitive)) {
            return false;
        }
    }

    // Level filter
    QString levelFilter = m_levelFilter->currentText();
    if (levelFilter != "All") {
        if (!line.contains(levelFilter, Qt::CaseInsensitive)) {
            return false;
        }
    }

    // Component filter
    QString componentText = m_componentFilter->text().trimmed();
    if (!componentText.isEmpty()) {
        if (!line.contains(componentText, Qt::CaseInsensitive)) {
            return false;
        }
    }

    return true;
}
