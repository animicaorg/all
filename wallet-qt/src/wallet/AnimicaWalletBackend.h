#ifndef ANIMICAWALLETBACKEND_H
#define ANIMICAWALLETBACKEND_H

#include <QObject>
#include <QJsonObject>
#include <QString>

class AnimicaWalletBackend : public QObject
{
    Q_OBJECT

public:
    explicit AnimicaWalletBackend(QObject* parent = nullptr);

    void setWalletFile(const QString& walletFile);
    QString walletFile() const { return m_walletFile; }

    void setRpcUrl(const QString& rpcUrl);
    QString rpcUrl() const { return m_rpcUrl; }

    void setExplorerUrl(const QString& explorerUrl);
    QString explorerUrl() const { return m_explorerUrl; }

    QJsonObject call(const QString& operation, const QJsonObject& args = QJsonObject(), int timeoutMs = 60000) const;
    QString lastError() const { return m_lastError; }

    static QString findPythonInterpreter();
    static QString findRepoRoot();

private:
    static QJsonObject errorResponse(const QString& message, const QString& details = QString());

    mutable QString m_lastError;
    QString m_walletFile;
    QString m_rpcUrl;
    QString m_explorerUrl;
};

#endif // ANIMICAWALLETBACKEND_H
