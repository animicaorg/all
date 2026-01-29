#ifndef DIAGNOSTICSLOGSWIDGET_H
#define DIAGNOSTICSLOGSWIDGET_H

#include <QWidget>
#include <QTextBrowser>
#include <QLineEdit>
#include <QComboBox>
#include <QPushButton>
#include <QCheckBox>
#include <QStringList>
#include <QMutex>

class NodeManager;

/**
 * @brief Log viewer widget for diagnostics.
 * 
 * Features:
 * - Ring buffer (10k lines) in memory
 * - Filter controls: level dropdown, component filter, search box
 * - Pause/resume live updates
 * - Export logs button (with redaction)
 * - Connects to NodeManager log capture
 * - Auto-scroll when not paused
 */
class DiagnosticsLogsWidget : public QWidget
{
    Q_OBJECT

public:
    explicit DiagnosticsLogsWidget(NodeManager* nodeManager, QWidget* parent = nullptr);

    /**
     * @brief Clear log buffer.
     */
    void clearLogs();

    /**
     * @brief Get filtered log text.
     * @return Current visible logs as text
     */
    QString getFilteredLogs() const;

public slots:
    /**
     * @brief Add log lines from node.
     * @param lines New log lines
     */
    void appendLogLines(const QStringList& lines);

signals:
    void logExported(const QString& filePath);

private slots:
    void onSearchTextChanged(const QString& text);
    void onLevelFilterChanged(int index);
    void onComponentFilterChanged(const QString& text);
    void onPauseToggled(bool paused);
    void onClearClicked();
    void onExportClicked();
    void onAutoScrollToggled(bool enabled);

private:
    void setupUi();
    void setupConnections();
    void updateFilteredDisplay();
    bool matchesFilters(const QString& line);
    void addToRingBuffer(const QString& line);

    NodeManager* m_nodeManager;
    
    QTextBrowser* m_logsBrowser;
    QLineEdit* m_searchBox;
    QComboBox* m_levelFilter;
    QLineEdit* m_componentFilter;
    QPushButton* m_pauseButton;
    QPushButton* m_clearButton;
    QPushButton* m_exportButton;
    QCheckBox* m_autoScrollCheckbox;

    QStringList m_logRingBuffer;
    int m_maxLogLines;
    bool m_paused;
    bool m_autoScroll;
    
    QMutex m_bufferMutex;
};

#endif // DIAGNOSTICSLOGSWIDGET_H
