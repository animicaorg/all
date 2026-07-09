#include "MiningDashboardWidget.h"
#include "ParticleLogoWidget.h"

#include "../rpc/AnimicaRpcClient.h"
#include "../wallet/BalanceTracker.h"
#include "../wallet/WalletAccount.h"
#include "../wallet/WalletEngine.h"

#include <QClipboard>
#include <QCoreApplication>
#include <QDesktopServices>
#include <QDir>
#include <QFileInfo>
#include <QProcessEnvironment>
#include <QFrame>
#include <QGridLayout>
#include <QGuiApplication>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QLabel>
#include <QMessageBox>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPlainTextEdit>
#include <QProcess>
#include <QPushButton>
#include <QScrollArea>
#include <QStackedLayout>
#include <QStandardPaths>
#include <QStyle>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>

namespace {
const char* kPoolBase = "https://pool.animica.org";

QString fmtCompact(double v)
{
    const double a = qAbs(v);
    if (a >= 1e9) return QString::number(v / 1e9, 'f', 2) + "B";
    if (a >= 1e6) return QString::number(v / 1e6, 'f', 2) + "M";
    if (a >= 1e3) return QString::number(v / 1e3, 'f', 1) + "k";
    return QString::number(qRound(v));
}

QString fmtHashrate(double hs)
{
    if (hs >= 1e6) return QString::number(hs / 1e6, 'f', 2) + " MH/s";
    if (hs >= 1e3) return QString::number(hs / 1e3, 'f', 2) + " kH/s";
    return QString::number(hs, 'f', 2) + " H/s";
}

QString fmtAnm(double v)
{
    if (qAbs(v) >= 1e3) return fmtCompact(v) + " ANM";
    return QString::number(v, 'f', 2) + " ANM";
}

double asNum(const QJsonValue& v)
{
    if (v.isDouble()) return v.toDouble();
    if (v.isString()) return v.toString().toDouble();
    return 0.0;
}
} // namespace

MiningDashboardWidget::MiningDashboardWidget(WalletEngine* engine,
                                             AnimicaRpcClient* rpcClient,
                                             QWidget* parent)
    : QWidget(parent)
    , m_engine(engine)
    , m_rpc(rpcClient)
{
    m_net = new QNetworkAccessManager(this);
    setupUi();

    m_animTimer = new QTimer(this);
    m_animTimer->setInterval(33);
    connect(m_animTimer, &QTimer::timeout, this, &MiningDashboardWidget::tickAnimations);
    m_animTimer->start();

    m_pollTimer = new QTimer(this);
    m_pollTimer->setInterval(12000);
    connect(m_pollTimer, &QTimer::timeout, this, &MiningDashboardWidget::pollStats);
    m_pollTimer->start();

    QTimer::singleShot(300, this, &MiningDashboardWidget::refresh);
    QTimer::singleShot(600, this, &MiningDashboardWidget::detectTrainingReady);
}

MiningDashboardWidget::~MiningDashboardWidget()
{
    for (QProcess* p : {m_minerProc, m_trainProc, m_pipProc, m_detectProc}) {
        if (p && p->state() != QProcess::NotRunning) {
            p->kill();
            p->waitForFinished(1500);
        }
    }
}

void MiningDashboardWidget::setupUi()
{
    setStyleSheet(R"(
        MiningDashboardWidget { background: #04060f; }
        QFrame#StatCard { background: rgba(255,255,255,0.035);
            border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; }
        QFrame#StatCard:hover { border: 1px solid rgba(22,217,195,0.55);
            background: rgba(22,217,195,0.06); }
        QLabel#StatTitle { color: #8aa0b6; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
        QLabel#StatValue { color: #eaf2fb; font-size: 25px; font-weight: 700; }
        QLabel#HeroTitle { color: #ffffff; font-size: 30px; font-weight: 800; letter-spacing: 0.5px; }
        QLabel#HeroSub { color: #b7c6d8; font-size: 13px; }
        QLabel#Pill { color: #16d9c3; font-size: 12px; font-weight: 600;
            background: rgba(22,217,195,0.10); border: 1px solid rgba(22,217,195,0.45);
            border-radius: 11px; padding: 3px 12px; }
        QLabel#Mono { color: #cfe0f0; font-family: monospace; font-size: 12px; }
        QPushButton#Mine { background: #16d9c3; color: #04121a; font-weight: 700;
            border: none; border-radius: 10px; padding: 10px 22px; font-size: 14px; }
        QPushButton#Mine:hover { background: #34e6d2; }
        QPushButton#Mine[mining="true"] { background: #ff6b6b; color: #1a0606; }
        QPushButton#Ghost { background: rgba(255,255,255,0.05); color: #cfe0f0;
            border: 1px solid rgba(255,255,255,0.14); border-radius: 10px; padding: 9px 18px; }
        QPushButton#Ghost:hover { border-color: rgba(58,155,255,0.6); color: #ffffff; }
        QPushButton#Copy { background: transparent; color: #16d9c3; border: 1px solid rgba(22,217,195,0.4);
            border-radius: 8px; padding: 3px 10px; }
        QPlainTextEdit#Log { background: #060a16; color: #9fe9dc; border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px; font-family: monospace; font-size: 11px; }
        QFrame#TrainCard { background: rgba(58,155,255,0.05);
            border: 1px solid rgba(58,155,255,0.22); border-radius: 14px; }
        QLabel#TrainTitle { color: #eaf2fb; font-size: 15px; font-weight: 700; }
        QLabel#TrainStatus { color: #8aa0b6; font-size: 12px; }
        QLabel#TrainStatus[ready="true"] { color: #16d9c3; }
        QPushButton#TrainPrimary { background: #3a9bff; color: #04121a; font-weight: 700;
            border: none; border-radius: 10px; padding: 9px 18px; font-size: 13px; }
        QPushButton#TrainPrimary:hover { background: #5cb0ff; }
        QPushButton#TrainPrimary:disabled { background: rgba(58,155,255,0.25); color: #6f8298; }
        QCheckBox#TrainOpt { color: #8aa0b6; font-size: 12px; }
    )");

    auto* scroll = new QScrollArea(this);
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);

    auto* content = new QWidget;
    content->setObjectName("DashContent");
    content->setStyleSheet("#DashContent { background: #04060f; }");
    auto* root = new QVBoxLayout(content);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    // ---- Animated hero with overlaid title ----
    auto* heroHost = new QWidget;
    heroHost->setFixedHeight(230);
    auto* heroStack = new QStackedLayout(heroHost);
    heroStack->setStackingMode(QStackedLayout::StackAll);
    heroStack->setContentsMargins(0, 0, 0, 0);

    m_hero = new ParticleLogoWidget;
    heroStack->addWidget(m_hero);

    auto* overlay = new QWidget;
    overlay->setAttribute(Qt::WA_TransparentForMouseEvents, true);
    overlay->setStyleSheet("background: transparent;");
    auto* ov = new QVBoxLayout(overlay);
    ov->setContentsMargins(32, 20, 32, 22);
    ov->addStretch();
    m_poolPill = new QLabel("●  pool.animica.org");
    m_poolPill->setObjectName("Pill");
    m_poolPill->setSizePolicy(QSizePolicy::Maximum, QSizePolicy::Maximum);
    ov->addWidget(m_poolPill);
    ov->addSpacing(8);
    auto* title = new QLabel("Animica Miner");
    title->setObjectName("HeroTitle");
    ov->addWidget(title);
    auto* sub = new QLabel(
        "Unified mining · AI useful-work · ENA training — all paid in ANM "
        "· powered by animica 1.9.12");
    sub->setObjectName("HeroSub");
    ov->addWidget(sub);
    heroStack->addWidget(overlay);
    heroStack->setCurrentWidget(overlay);

    root->addWidget(heroHost);

    // ---- Body ----
    auto* body = new QWidget;
    auto* bl = new QVBoxLayout(body);
    bl->setContentsMargins(28, 24, 28, 28);
    bl->setSpacing(18);

    // Stat grid.
    auto* grid = new QGridLayout;
    grid->setHorizontalSpacing(14);
    grid->setVerticalSpacing(14);

    m_iBalance = addStat(grid, 0, 0, "YOUR BALANCE", "#16d9c3", fmtAnm);
    m_iYourHash = addStat(grid, 0, 1, "YOUR HASHRATE", "#3a9bff", fmtHashrate);
    m_iPoolHash = addStat(grid, 0, 2, "POOL HASHRATE", "#3a9bff", fmtHashrate);
    m_iBlocks = addStat(grid, 0, 3, "BLOCKS FOUND", "#6fffd6", fmtCompact);
    m_iShares = addStat(grid, 1, 0, "SHARES ACCEPTED", "#16d9c3", fmtCompact);
    m_iMiners = addStat(grid, 1, 1, "ACTIVE MINERS", "#3a9bff", fmtCompact);
    m_iPaid = addStat(grid, 1, 2, "TOTAL PAID OUT", "#6fffd6", fmtAnm);
    m_iEnaContrib = addStat(grid, 1, 3, "ENA CONTRIBUTORS", "#16d9c3", fmtCompact);
    m_iEnaRuns = addStat(grid, 2, 0, "ENA TRAINING RUNS", "#3a9bff", fmtCompact);
    m_iEnaVerified = addStat(grid, 2, 1, "ENA VERIFIED JOBS", "#6fffd6", fmtCompact);
    bl->addLayout(grid);

    // Mining control row.
    auto* ctrlRow = new QHBoxLayout;
    ctrlRow->setSpacing(12);
    auto* mineTo = new QLabel("Mining to:");
    mineTo->setStyleSheet("color:#8aa0b6; font-size:12px;");
    ctrlRow->addWidget(mineTo);
    m_addressLabel = new QLabel("—");
    m_addressLabel->setObjectName("Mono");
    m_addressLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    ctrlRow->addWidget(m_addressLabel);
    auto* copyBtn = new QPushButton("Copy");
    copyBtn->setObjectName("Copy");
    copyBtn->setCursor(Qt::PointingHandCursor);
    connect(copyBtn, &QPushButton::clicked, this, [this]() {
        const QString a = miningAddress();
        if (!a.isEmpty()) QGuiApplication::clipboard()->setText(a);
    });
    ctrlRow->addWidget(copyBtn);
    ctrlRow->addStretch();
    m_mineStatus = new QLabel("Idle");
    m_mineStatus->setStyleSheet("color:#8aa0b6; font-size:12px;");
    ctrlRow->addWidget(m_mineStatus);
    m_mineButton = new QPushButton("Start Mining");
    m_mineButton->setObjectName("Mine");
    m_mineButton->setCursor(Qt::PointingHandCursor);
    connect(m_mineButton, &QPushButton::clicked, this, &MiningDashboardWidget::onStartStopClicked);
    ctrlRow->addWidget(m_mineButton);
    bl->addLayout(ctrlRow);

    // ---- ENA training (on-demand ML stack) ----
    auto* trainCard = new QFrame;
    trainCard->setObjectName("TrainCard");
    auto* tv = new QVBoxLayout(trainCard);
    tv->setContentsMargins(18, 14, 18, 16);
    tv->setSpacing(8);
    auto* trainHead = new QHBoxLayout;
    auto* trainTitle = new QLabel("ENA Training");
    trainTitle->setObjectName("TrainTitle");
    trainHead->addWidget(trainTitle);
    trainHead->addStretch();
    m_trainStatus = new QLabel("checking…");
    m_trainStatus->setObjectName("TrainStatus");
    trainHead->addWidget(m_trainStatus);
    tv->addLayout(trainHead);
    auto* trainDesc = new QLabel(
        "Earn ANM by training shared Animica models. The PyTorch + Transformers "
        "stack is kept out of the base app — enable it once (~1.6–2.5 GB download) "
        "to join an ENA training pool.");
    trainDesc->setObjectName("HeroSub");
    trainDesc->setWordWrap(true);
    tv->addWidget(trainDesc);
    auto* trainBtns = new QHBoxLayout;
    trainBtns->setSpacing(10);
    m_enableTrainBtn = new QPushButton("Enable ENA Training  (~2 GB)");
    m_enableTrainBtn->setObjectName("Ghost");
    m_enableTrainBtn->setCursor(Qt::PointingHandCursor);
    connect(m_enableTrainBtn, &QPushButton::clicked, this,
            &MiningDashboardWidget::onEnableTrainingClicked);
    trainBtns->addWidget(m_enableTrainBtn);
    m_startTrainBtn = new QPushButton("Start Training");
    m_startTrainBtn->setObjectName("TrainPrimary");
    m_startTrainBtn->setCursor(Qt::PointingHandCursor);
    m_startTrainBtn->setEnabled(false);
    connect(m_startTrainBtn, &QPushButton::clicked, this,
            &MiningDashboardWidget::onStartTrainingClicked);
    trainBtns->addWidget(m_startTrainBtn);
    trainBtns->addStretch();
    tv->addLayout(trainBtns);
    bl->addWidget(trainCard);

    // Log console.
    m_log = new QPlainTextEdit;
    m_log->setObjectName("Log");
    m_log->setReadOnly(true);
    m_log->setMaximumBlockCount(2000);
    m_log->setFixedHeight(150);
    m_log->setPlainText("Stats stream from pool.animica.org. Click “Start Mining” to run the "
                        "bundled animica 1.9.12 miner with your default account.\n");
    bl->addWidget(m_log);

    // Footer links.
    auto* foot = new QHBoxLayout;
    auto* poolBtn = new QPushButton("Open Pool Dashboard  ↗");
    poolBtn->setObjectName("Ghost");
    poolBtn->setCursor(Qt::PointingHandCursor);
    connect(poolBtn, &QPushButton::clicked, this, []() {
        QDesktopServices::openUrl(QUrl("https://pool.animica.org"));
    });
    foot->addWidget(poolBtn);
    auto* enaBtn = new QPushButton("ENA Training  ↗");
    enaBtn->setObjectName("Ghost");
    enaBtn->setCursor(Qt::PointingHandCursor);
    connect(enaBtn, &QPushButton::clicked, this, []() {
        QDesktopServices::openUrl(QUrl("https://pool.animica.org/training-pools"));
    });
    foot->addWidget(enaBtn);
    foot->addStretch();
    auto* refreshBtn = new QPushButton("Refresh");
    refreshBtn->setObjectName("Ghost");
    refreshBtn->setCursor(Qt::PointingHandCursor);
    connect(refreshBtn, &QPushButton::clicked, this, &MiningDashboardWidget::refresh);
    foot->addWidget(refreshBtn);
    bl->addLayout(foot);

    root->addWidget(body);
    root->addStretch();

    scroll->setWidget(content);
    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(0, 0, 0, 0);
    outer->addWidget(scroll);
}

int MiningDashboardWidget::addStat(QGridLayout* grid, int row, int col,
                                   const QString& title, const QString& accent,
                                   std::function<QString(double)> fmt)
{
    auto* card = new QFrame;
    card->setObjectName("StatCard");
    card->setMinimumHeight(94);
    auto* v = new QVBoxLayout(card);
    v->setContentsMargins(16, 12, 16, 12);
    v->setSpacing(6);

    auto* strip = new QFrame;
    strip->setFixedHeight(3);
    strip->setStyleSheet(QString("background:%1; border-radius:2px;").arg(accent));
    strip->setMaximumWidth(40);
    v->addWidget(strip);

    auto* t = new QLabel(title);
    t->setObjectName("StatTitle");
    v->addWidget(t);

    auto* val = new QLabel("—");
    val->setObjectName("StatValue");
    v->addWidget(val);
    v->addStretch();

    grid->addWidget(card, row, col);

    Stat s;
    s.card = card;
    s.value = val;
    s.fmt = std::move(fmt);
    m_stats.append(s);
    return m_stats.size() - 1;
}

void MiningDashboardWidget::setStat(int idx, double value)
{
    if (idx < 0 || idx >= m_stats.size()) return;
    Stat& s = m_stats[idx];
    if (!s.hasValue) {
        s.current = value; // snap on first real value
    }
    s.target = value;
    s.hasValue = true;
}

void MiningDashboardWidget::clearStat(int idx)
{
    if (idx < 0 || idx >= m_stats.size()) return;
    m_stats[idx].hasValue = false;
    if (m_stats[idx].value) m_stats[idx].value->setText("—");
}

void MiningDashboardWidget::tickAnimations()
{
    for (Stat& s : m_stats) {
        if (!s.hasValue || !s.value) continue;
        const double diff = s.target - s.current;
        if (qAbs(diff) < 0.001) {
            s.current = s.target;
        } else {
            s.current += diff * 0.18;
            if (qAbs(s.target - s.current) < qMax(0.5, qAbs(s.target) * 1e-4)) {
                s.current = s.target;
            }
        }
        s.value->setText(s.fmt ? s.fmt(s.current) : QString::number(s.current));
    }
}

void MiningDashboardWidget::setPoolOnline(bool online)
{
    if (!m_poolPill) return;
    if (online) {
        m_poolPill->setText("●  pool.animica.org");
        m_poolPill->setStyleSheet(
            "color:#16d9c3; background:rgba(22,217,195,0.10);"
            "border:1px solid rgba(22,217,195,0.45); border-radius:11px; padding:3px 12px;"
            "font-size:12px; font-weight:600;");
    } else {
        m_poolPill->setText("●  pool.animica.org — offline");
        m_poolPill->setStyleSheet(
            "color:#ff8a8a; background:rgba(255,107,107,0.10);"
            "border:1px solid rgba(255,107,107,0.45); border-radius:11px; padding:3px 12px;"
            "font-size:12px; font-weight:600;");
    }
}

void MiningDashboardWidget::refresh()
{
    if (m_addressLabel) {
        const QString a = miningAddress();
        m_addressLabel->setText(a.isEmpty() ? "no account yet — create one in the Accounts tab" : a);
    }
    pollStats();
    pollBalance();
}

void MiningDashboardWidget::requestJson(const QString& url,
                                        std::function<void(const QJsonDocument&, bool)> cb)
{
    QNetworkRequest req((QUrl(url)));
    req.setHeader(QNetworkRequest::UserAgentHeader, "AnimicaMiner/1.0");
    req.setAttribute(QNetworkRequest::RedirectPolicyAttribute,
                     QNetworkRequest::NoLessSafeRedirectPolicy);
    QNetworkReply* reply = m_net->get(req);
    connect(reply, &QNetworkReply::finished, this, [reply, cb]() {
        const bool ok = (reply->error() == QNetworkReply::NoError);
        QJsonDocument doc;
        if (ok) doc = QJsonDocument::fromJson(reply->readAll());
        cb(doc, ok && !doc.isNull());
        reply->deleteLater();
    });
}

void MiningDashboardWidget::pollStats()
{
    requestJson(QString("%1/api/mining/status").arg(kPoolBase),
                [this](const QJsonDocument& d, bool ok) {
                    setPoolOnline(ok);
                    if (ok) applyMiningStatus(d);
                });
    requestJson(QString("%1/api/miners").arg(kPoolBase),
                [this](const QJsonDocument& d, bool ok) { if (ok) applyMiners(d); });
    requestJson(QString("%1/api/ena/stats").arg(kPoolBase),
                [this](const QJsonDocument& d, bool ok) { if (ok) applyEnaStats(d); });
}

void MiningDashboardWidget::applyMiningStatus(const QJsonDocument& doc)
{
    const QJsonObject o = doc.object();
    setStat(m_iPoolHash, asNum(o.value("pool_hashrate")));
    const QJsonObject health = o.value("health").toObject();
    const QJsonObject acct = health.value("accounting").toObject();
    setStat(m_iBlocks, asNum(acct.value("accepted_blocks")));
    setStat(m_iShares, asNum(acct.value("accepted_shares")));
    // paid_out_total is in nano-ANM (1 ANM = 1e9).
    setStat(m_iPaid, asNum(acct.value("paid_out_total")) / 1e9);
    if (acct.contains("workers_with_balance")) {
        setStat(m_iMiners, asNum(acct.value("workers_with_balance")));
    }
}

void MiningDashboardWidget::applyMiners(const QJsonDocument& doc)
{
    const QJsonArray items = doc.object().value("items").toArray();
    setStat(m_iMiners, items.size());

    const QString mine = miningAddress();
    double yourHash = 0.0;
    bool found = false;
    for (const QJsonValue& it : items) {
        const QJsonObject m = it.toObject();
        if (!mine.isEmpty()
            && (m.value("address").toString() == mine
                || m.value("worker_id").toString() == mine)) {
            yourHash = asNum(m.value("hashrate_1h"));
            if (yourHash == 0.0) yourHash = asNum(m.value("hashrate_15m"));
            found = true;
            break;
        }
    }
    if (found) setStat(m_iYourHash, yourHash);
    else setStat(m_iYourHash, 0.0);
}

void MiningDashboardWidget::applyEnaStats(const QJsonDocument& doc)
{
    const QJsonObject o = doc.object();
    setStat(m_iEnaContrib, asNum(o.value("contributors")));
    setStat(m_iEnaRuns, asNum(o.value("training_runs_total")));
    setStat(m_iEnaVerified, asNum(o.value("jobs_verified")));
}

void MiningDashboardWidget::pollBalance()
{
    if (!m_engine) return;
    const QMap<QString, Balance> balances = m_engine->getBalances();
    if (balances.isEmpty()) {
        return; // keep prior / placeholder
    }
    quint64 total = 0;
    for (const Balance& b : balances) {
        total += b.confirmed;
    }
    setStat(m_iBalance, double(total) / 1e9);
}

QString MiningDashboardWidget::miningAddress() const
{
    if (!m_engine) return QString();
    const QList<WalletAccount> accounts = m_engine->listAccounts();
    if (accounts.isEmpty()) return QString();
    for (const WalletAccount& a : accounts) {
        if (a.isDefault && !a.address.isEmpty()) return a.address;
    }
    return accounts.first().address;
}

QString MiningDashboardWidget::locateAnimicaCli() const
{
    const QString appDir = QCoreApplication::applicationDirPath();
#ifdef Q_OS_WIN
    const QString exe = "animica.exe";
    const QStringList rel = {
        "/node/venv/Scripts/" + exe,
        "/../node/venv/Scripts/" + exe,
    };
#else
    const QString exe = "animica";
    const QStringList rel = {
        "/../Resources/node/venv/bin/" + exe,          // macOS .app
        "/node/venv/bin/" + exe,                        // portable
        "/../lib/animica-wallet/node/venv/bin/" + exe,  // linux .deb / AppImage
        "/../share/animica-wallet/node/venv/bin/" + exe,
    };
#endif
    for (const QString& r : rel) {
        const QString cand = QDir::cleanPath(appDir + r);
        if (QFileInfo(cand).isExecutable()) return cand;
    }
    const QString onPath = QStandardPaths::findExecutable("animica");
    return onPath; // may be empty
}

void MiningDashboardWidget::appendLog(const QString& line)
{
    if (m_log) m_log->appendPlainText(line.trimmed());
}

void MiningDashboardWidget::onStartStopClicked()
{
    if (m_minerProc && m_minerProc->state() != QProcess::NotRunning) {
        appendLog("■ Stopping miner…");
        m_minerProc->terminate();
        if (!m_minerProc->waitForFinished(3000)) {
            m_minerProc->kill();
        }
        return;
    }

    const QString cli = locateAnimicaCli();
    if (cli.isEmpty()) {
        const QString cmd = "pip install --upgrade animica && animica up";
        QMessageBox box(this);
        box.setWindowTitle("Bundled miner not found");
        box.setIcon(QMessageBox::Information);
        box.setText("Could not find the bundled animica miner next to this app.\n\n"
                    "You can run the miner from a terminal instead:");
        box.setInformativeText(cmd);
        QPushButton* copy = box.addButton("Copy command", QMessageBox::AcceptRole);
        box.addButton(QMessageBox::Close);
        box.exec();
        if (box.clickedButton() == copy) {
            QGuiApplication::clipboard()->setText(cmd);
        }
        return;
    }

    const QString addr = miningAddress();
    if (addr.isEmpty()) {
        QMessageBox::information(this, "No account",
            "Create or select an account in the Accounts tab first — mining "
            "rewards are paid to your wallet address.\n\n"
            "Without an account the miner would generate a throwaway address "
            "outside this wallet, and you could not spend the rewards.");
        return;
    }

    m_minerProc = new QProcess(this);
    m_minerProc->setProcessChannelMode(QProcess::MergedChannels);
    connect(m_minerProc, &QProcess::readyReadStandardOutput, this, [this]() {
        appendLog(QString::fromUtf8(m_minerProc->readAllStandardOutput()));
    });
    connect(m_minerProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus) {
                appendLog(QString("■ Miner exited (code %1).").arg(code));
                m_mineButton->setText("Start Mining");
                m_mineButton->setProperty("mining", false);
                m_mineButton->style()->unpolish(m_mineButton);
                m_mineButton->style()->polish(m_mineButton);
                m_mineStatus->setText("Idle");
            });
    connect(m_minerProc, &QProcess::started, this, [this, cli]() {
        appendLog(QString("▶ Started miner: %1 up --address …").arg(cli));
        m_mineButton->setText("Stop Mining");
        m_mineButton->setProperty("mining", true);
        m_mineButton->style()->unpolish(m_mineButton);
        m_mineButton->style()->polish(m_mineButton);
        m_mineStatus->setText("Mining…");
    });
    connect(m_minerProc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        appendLog(QString("⚠ Miner error: %1").arg(m_minerProc->errorString()));
    });

    // One-click CPU mining to pool.animica.org. Passing --address explicitly is
    // the key reliability fix: the bundled `animica up` otherwise falls back to
    // auto-resolving/auto-creating a wallet — the main source of the headless
    // "various errors", and it could pay rewards to an address outside this
    // wallet. We pass ONLY --address: the bundled animica runtime does not
    // support --only/--profile, so those would make `up` error out.
    const QStringList args{ QStringLiteral("up"), QStringLiteral("--address"), addr };
    appendLog(QString("Launching bundled animica miner for %1…\n▶ %2 %3")
                  .arg(addr, cli, args.join(' ')));
    m_minerProc->start(cli, args);
}

// ---------------------------------------------------------------------------
// ENA training (on-demand ML stack)
// ---------------------------------------------------------------------------

QString MiningDashboardWidget::locatePython() const
{
    const QString appDir = QCoreApplication::applicationDirPath();
#ifdef Q_OS_WIN
    const QStringList rel = {
        "/node/venv/python.exe", "/node/venv/Scripts/python.exe",
        "/../node/venv/python.exe",
    };
#elif defined(Q_OS_MAC)
    const QStringList rel = {
        "/../Resources/node/venv/bin/python3.12",
        "/../Resources/node/venv/bin/python3",
        "/node/venv/bin/python3",
    };
#else
    const QStringList rel = {
        "/node/venv/bin/python3",
        "/../lib/animica-wallet/node/venv/bin/python3",
        "/../lib/x86_64-linux-gnu/animica-wallet/node/venv/bin/python3",
        "/../share/animica-wallet/node/venv/bin/python3",
    };
#endif
    for (const QString& r : rel) {
        const QString cand = QDir::cleanPath(appDir + r);
        if (QFileInfo(cand).isExecutable()) return cand;
    }
    QString onPath = QStandardPaths::findExecutable("python3");
    if (onPath.isEmpty()) onPath = QStandardPaths::findExecutable("python");
    return onPath;
}

QString MiningDashboardWidget::enaRuntimeDir() const
{
    // Install the ML stack into a USER-WRITABLE dir — never the bundled venv,
    // which is read-only (Program Files / signed .app / root-owned /usr/lib).
    QString base = qEnvironmentVariable("ANIMICA_WALLET_DATA_DIR");
    if (base.isEmpty())
        base = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    const QString dir = QDir(base).filePath("ena-runtime");
    QDir().mkpath(dir);
    return dir;
}

void MiningDashboardWidget::injectRuntimeEnv(QProcess* proc) const
{
    // Prepend ena-runtime so the on-demand stack wins over the bundled venv
    // (e.g. the pinned huggingface_hub<1.0 overrides the bundle's 1.20.1).
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString rt = enaRuntimeDir();
    const QString cur = env.value("PYTHONPATH");
    env.insert("PYTHONPATH", cur.isEmpty() ? rt
                                           : rt + QDir::listSeparator() + cur);
    proc->setProcessEnvironment(env);
}

QStringList MiningDashboardWidget::enaPipArgs() const
{
    QStringList a;
    a << "-m" << "pip" << "install" << "--no-input" << "--upgrade" << "--no-cache-dir"
      << "--target" << enaRuntimeDir()
      << "torch" << "accelerate" << "transformers" << "datasets"
      << "sentence-transformers" << "peft" << "trl" << "sentencepiece"
      << "safetensors" << "protobuf" << "scipy" << "einops" << "numpy"
      << "huggingface_hub<1.0";
#ifdef Q_OS_LINUX
    a << "bitsandbytes";   // CUDA-only QLoRA, import-guarded elsewhere
#endif
    return a;
}

void MiningDashboardWidget::setTrainState(TrainState state)
{
    m_trainState = state;
    if (!m_trainStatus || !m_enableTrainBtn || !m_startTrainBtn) return;
    bool ready = false;
    switch (state) {
    case TrainState::Unknown:
        m_trainStatus->setText("checking…");
        m_enableTrainBtn->setEnabled(false);
        m_startTrainBtn->setEnabled(false);
        break;
    case TrainState::NotInstalled:
        m_trainStatus->setText("not installed");
        m_enableTrainBtn->setEnabled(true);
        m_enableTrainBtn->setText("Enable ENA Training  (~2 GB)");
        m_startTrainBtn->setEnabled(false);
        break;
    case TrainState::Installing:
        m_trainStatus->setText("installing…");
        m_enableTrainBtn->setEnabled(false);
        m_startTrainBtn->setEnabled(false);
        break;
    case TrainState::Ready:
        ready = true;
        m_trainStatus->setText("ready");
        m_enableTrainBtn->setEnabled(true);
        m_enableTrainBtn->setText("Repair / Update");
        m_startTrainBtn->setEnabled(true);
        m_startTrainBtn->setText("Start Training");
        break;
    case TrainState::Training:
        ready = true;
        m_trainStatus->setText("training…");
        m_enableTrainBtn->setEnabled(false);
        m_startTrainBtn->setEnabled(true);
        m_startTrainBtn->setText("Stop Training");
        break;
    }
    m_trainStatus->setProperty("ready", ready);
    m_trainStatus->style()->unpolish(m_trainStatus);
    m_trainStatus->style()->polish(m_trainStatus);
}

void MiningDashboardWidget::detectTrainingReady()
{
    if (m_trainState == TrainState::Installing || m_trainState == TrainState::Training)
        return;
    const QString py = locatePython();
    if (py.isEmpty()) { setTrainState(TrainState::NotInstalled); return; }
    if (m_detectProc && m_detectProc->state() != QProcess::NotRunning) return;
    setTrainState(TrainState::Unknown);
    m_detectProc = new QProcess(this);
    injectRuntimeEnv(m_detectProc);
    auto done = [this](bool ok) {
        if (m_trainState != TrainState::Installing && m_trainState != TrainState::Training)
            setTrainState(ok ? TrainState::Ready : TrainState::NotInstalled);
        if (m_detectProc) { m_detectProc->deleteLater(); m_detectProc = nullptr; }
    };
    connect(m_detectProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [done](int code, QProcess::ExitStatus) { done(code == 0); });
    connect(m_detectProc, &QProcess::errorOccurred, this,
            [done](QProcess::ProcessError) { done(false); });
    m_detectProc->start(py, QStringList() << "-c"
        << "import torch, transformers, peft, trl, accelerate, datasets, sentence_transformers");
}

void MiningDashboardWidget::onEnableTrainingClicked()
{
    if (m_pipProc && m_pipProc->state() != QProcess::NotRunning) return;
    const QString py = locatePython();
    if (py.isEmpty()) {
        QMessageBox::information(this, "Python not found",
            "Could not find the bundled Python runtime next to this app.");
        return;
    }
    setTrainState(TrainState::Installing);
    appendLog(QString("▶ Installing the ENA training stack into:\n   %1").arg(enaRuntimeDir()));
    appendLog("   One-time ~1.6–2.5 GB download (torch + transformers). You can keep mining.");
    m_pipProc = new QProcess(this);
    m_pipProc->setProcessChannelMode(QProcess::MergedChannels);
    injectRuntimeEnv(m_pipProc);
    connect(m_pipProc, &QProcess::readyReadStandardOutput, this, [this]() {
        appendLog(QString::fromUtf8(m_pipProc->readAllStandardOutput()));
    });
    connect(m_pipProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus) {
                if (code == 0) {
                    appendLog("✓ Training stack installed — verifying…");
                    detectTrainingReady();
                } else {
                    appendLog(QString("⚠ Install failed (pip exit %1). See output above; "
                                      "re-run to resume — the download is resumable.").arg(code));
                    setTrainState(TrainState::NotInstalled);
                }
                if (m_pipProc) { m_pipProc->deleteLater(); m_pipProc = nullptr; }
            });
    connect(m_pipProc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        if (m_pipProc) appendLog(QString("⚠ pip error: %1").arg(m_pipProc->errorString()));
    });
    m_pipProc->start(py, enaPipArgs());
}

void MiningDashboardWidget::onStartTrainingClicked()
{
    if (m_trainProc && m_trainProc->state() != QProcess::NotRunning) {
        appendLog("■ Stopping ENA training…");
        m_trainProc->terminate();
        if (!m_trainProc->waitForFinished(3000)) m_trainProc->kill();
        return;
    }
    const QString addr = miningAddress();
    if (addr.isEmpty()) {
        QMessageBox::information(this, "No account",
            "Create an account in the Accounts tab first — ENA training rewards "
            "are paid to your wallet address.");
        return;
    }
    if (m_trainPoolId.isEmpty()) {
        appendLog("Finding an ENA training pool…");
        requestJson(QString("%1/api/ena/pool/list").arg(kPoolBase),
            [this](const QJsonDocument& d, bool ok) {
                if (ok) {
                    const QJsonArray pools = d.object().value("pools").toArray();
                    if (!pools.isEmpty())
                        m_trainPoolId = pools.first().toObject().value("pool_id").toString();
                }
                if (m_trainPoolId.isEmpty())
                    appendLog("⚠ No ENA training pool is available right now.");
                else
                    onStartTrainingClicked();   // retry now that we have a pool
            });
        return;
    }
    const QString cli = locateAnimicaCli();
    if (cli.isEmpty()) {
        QMessageBox::information(this, "Runtime not found",
            "Could not find the bundled animica runtime next to this app.");
        return;
    }
    m_trainProc = new QProcess(this);
    m_trainProc->setProcessChannelMode(QProcess::MergedChannels);
    injectRuntimeEnv(m_trainProc);
    connect(m_trainProc, &QProcess::readyReadStandardOutput, this, [this]() {
        appendLog(QString::fromUtf8(m_trainProc->readAllStandardOutput()));
    });
    connect(m_trainProc, &QProcess::started, this, [this]() {
        appendLog(QString("▶ Joined ENA pool %1 as a trainer.").arg(m_trainPoolId));
        setTrainState(TrainState::Training);
    });
    connect(m_trainProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus) {
                appendLog(QString("■ Training stopped (code %1).").arg(code));
                setTrainState(TrainState::Ready);
                if (m_trainProc) { m_trainProc->deleteLater(); m_trainProc = nullptr; }
            });
    connect(m_trainProc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        if (m_trainProc) appendLog(QString("⚠ Training error: %1").arg(m_trainProc->errorString()));
    });
    QStringList args;
    args << "ena" << "pool" << "train-loop" << m_trainPoolId
         << "--worker-id" << addr << "--address" << addr
         << "--endpoint" << "https://pool.animica.org/api/ena";
    appendLog(QString("▶ %1 %2").arg(cli, args.join(' ')));
    m_trainProc->start(cli, args);
}
