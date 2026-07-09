#ifndef MININGDASHBOARDWIDGET_H
#define MININGDASHBOARDWIDGET_H

#include <QVector>
#include <QWidget>
#include <functional>

class WalletEngine;
class AnimicaRpcClient;
class ParticleLogoWidget;
class QNetworkAccessManager;
class QNetworkReply;
class QTimer;
class QLabel;
class QFrame;
class QPushButton;
class QPlainTextEdit;
class QProcess;

/**
 * @brief The "Mining" landing dashboard — an animated, brand-native hero plus a
 *        live grid of pool / wallet / ENA statistics, and a one-click control to
 *        start the bundled `animica` miner (which runs animica 1.9.12).
 *
 * Stats are polled from the public pool API (pool.animica.org) and the wallet's
 * RPC client; values ease toward their new targets for a smooth count-up feel.
 */
class MiningDashboardWidget : public QWidget
{
    Q_OBJECT

public:
    explicit MiningDashboardWidget(WalletEngine* engine,
                                   AnimicaRpcClient* rpcClient,
                                   QWidget* parent = nullptr);
    ~MiningDashboardWidget() override;

public slots:
    void refresh();

private:
    struct Stat {
        QFrame* card = nullptr;
        QLabel* value = nullptr;
        double current = 0.0;
        double target = 0.0;
        bool hasValue = false;
        std::function<QString(double)> fmt;
    };

    // On-demand ENA training: the ML stack (torch/transformers/…) is NOT bundled
    // (keeps the base app small); the user installs it on demand into a writable
    // runtime dir, then can join an ENA training pool to earn ANM.
    enum class TrainState { Unknown, NotInstalled, Installing, Ready, Training };

    void setupUi();
    int addStat(class QGridLayout* grid, int row, int col,
                const QString& title, const QString& accent,
                std::function<QString(double)> fmt);
    void setStat(int idx, double value);
    void clearStat(int idx);

    void pollStats();
    void requestJson(const QString& url, std::function<void(const QJsonDocument&, bool)> cb);
    void applyMiningStatus(const QJsonDocument& doc);
    void applyMiners(const QJsonDocument& doc);
    void applyEnaStats(const QJsonDocument& doc);
    void pollBalance();

    void tickAnimations();
    void setPoolOnline(bool online);

    QString miningAddress() const;
    QString locateAnimicaCli() const;
    void onStartStopClicked();
    void appendLog(const QString& line);

    // ENA training (on-demand ML stack).
    QString locatePython() const;
    QString enaRuntimeDir() const;
    QStringList enaPipArgs() const;
    void injectRuntimeEnv(QProcess* proc) const;   // prepend ena-runtime to PYTHONPATH
    void setTrainState(TrainState state);
    void detectTrainingReady();
    void onEnableTrainingClicked();
    void onStartTrainingClicked();

    WalletEngine* m_engine = nullptr;
    AnimicaRpcClient* m_rpc = nullptr;
    QNetworkAccessManager* m_net = nullptr;

    ParticleLogoWidget* m_hero = nullptr;
    QLabel* m_poolPill = nullptr;
    QLabel* m_addressLabel = nullptr;
    QPushButton* m_mineButton = nullptr;
    QLabel* m_mineStatus = nullptr;
    QPlainTextEdit* m_log = nullptr;

    QVector<Stat> m_stats;
    // Stat indices.
    int m_iBalance = -1, m_iYourHash = -1, m_iPoolHash = -1, m_iBlocks = -1,
        m_iShares = -1, m_iMiners = -1, m_iPaid = -1, m_iEnaContrib = -1,
        m_iEnaRuns = -1, m_iEnaVerified = -1;

    QTimer* m_pollTimer = nullptr;
    QTimer* m_animTimer = nullptr;
    QProcess* m_minerProc = nullptr;

    // ENA training UI + processes (kept separate from mining so the user can
    // mine and train concurrently).
    TrainState m_trainState = TrainState::Unknown;
    QLabel* m_trainStatus = nullptr;
    QPushButton* m_enableTrainBtn = nullptr;
    QPushButton* m_startTrainBtn = nullptr;
    QProcess* m_pipProc = nullptr;
    QProcess* m_trainProc = nullptr;
    QProcess* m_detectProc = nullptr;
    QString m_trainPoolId;              // resolved from the ENA pool list
};

#endif // MININGDASHBOARDWIDGET_H
