#ifndef L2WIDGET_H
#define L2WIDGET_H

#include <QWidget>
#include <QComboBox>
#include <QDoubleSpinBox>
#include <QFutureWatcher>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QShowEvent>
#include <QString>
#include <QTextEdit>
#include <QTimer>

class WalletEngine;
class AnimicaRpcClient;

/**
 * @brief "ANM Instant (L2)" tab.
 *
 * Additive companion to the L1 wallet views. It surfaces the Animica 10.0.0
 * ANM-native L2 ("ANM Instant") over the SAME JSON-RPC endpoint the wallet
 * already uses (methods prefixed l2_):
 *
 *  - a live L2 status panel (enabled / mode / chain id / settlement / head
 *    batch / state root / signature backend);
 *  - the selected account's DISTINCT L2 balance (never conflated with L1);
 *  - the bridge deposit address + instructions for funding L2 from L1;
 *  - a Send-Instant form that runs prepare -> sign(signingHash) -> submit via
 *    the wallet backend (which owns the account's ML-DSA-65 key — the same key
 *    used for L1) and then polls the tx toward PROVEN.
 *
 * The widget NEVER moves L1 ANM into L2 implicitly: depositing is an explicit
 * L1 transfer the user makes to the bridge address shown here.
 *
 * Threading: read calls (status / balance / tx-status poll) go through the
 * shared AnimicaRpcClient, which owns a main-thread QNetworkAccessManager, so
 * they run synchronously on the GUI thread (same pattern as WalletWidget's RPC
 * probe). Only the send path — which drives the out-of-process Python signer
 * via WalletEngine::sendInstant — runs on a worker via QtConcurrent, exactly
 * like SendWidget's submit.
 */
class L2Widget : public QWidget
{
    Q_OBJECT

public:
    explicit L2Widget(
        WalletEngine* engine,
        AnimicaRpcClient* rpcClient,
        QWidget* parent = nullptr
    );
    ~L2Widget() override;

public slots:
    /** @brief Refresh the account list, L2 status, and L2 balance. */
    void refresh();

private slots:
    void onAccountChanged(int index);
    void onSendClicked();
    void handleSendFinished();
    void pollTransactionStatus();

private:
    void showEvent(QShowEvent* event) override;
    void setupUi();
    void updateAccounts();
    void loadL2Info();
    void applyStatus(const QJsonObject& status);
    void applyBalance(const QJsonObject& balance);
    void appendStatus(const QString& line);
    QString currentAccountAddress() const;
    static QString formatNanosAsAnm(const QString& nanos);

    WalletEngine* m_engine;
    AnimicaRpcClient* m_rpcClient;

    // UI — top / status
    QComboBox* m_accountCombo;
    QLabel* m_l2BalanceLabel;
    QLabel* m_statusEnabledLabel;
    QLabel* m_statusModeLabel;
    QLabel* m_statusChainLabel;
    QLabel* m_statusSettlementLabel;
    QLabel* m_statusHeadBatchLabel;
    QLabel* m_statusStateRootLabel;
    QLabel* m_statusBackendLabel;
    QLabel* m_depositAddressLabel;
    QPushButton* m_refreshButton;

    // UI — send
    QLineEdit* m_recipientEdit;
    QDoubleSpinBox* m_amountSpinBox;
    QPushButton* m_sendButton;
    QTextEdit* m_sendStatusView;

    // Async / polling
    QFutureWatcher<QJsonObject>* m_sendWatcher;   // prepare/sign/submit (subprocess)
    QTimer* m_pollTimer;                          // l2_getTransaction poll
    QString m_pendingTxid;
    int m_pollAttempts;
};

#endif // L2WIDGET_H
