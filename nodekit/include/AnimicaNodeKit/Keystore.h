#pragma once

#include <QObject>
#include <QJsonObject>
#include <QVector>

namespace animica::nodekit {

struct WalletEntry {
    QString label;
    QString address;
    QString algId;
    QString publicKey;
    QString createdAt;
};

class Keystore : public QObject {
    Q_OBJECT

public:
    explicit Keystore(QObject *parent = nullptr);

    void setStoragePath(const QString &path);
    bool unlock(const QString &passphrase);
    void lock();
    bool isUnlocked() const;

    QVector<WalletEntry> wallets() const;
    bool createWallet(const QString &label, const QString &algId = QStringLiteral("pq_default"));
    bool importWallet(const QString &label, const QString &secret);
    QJsonObject signTx(const QJsonObject &tx, QString *error);

private:
    bool loadEncrypted();
    bool saveEncrypted();
    QJsonObject buildNewKeystore() const;

    QString storagePath_{};
    QString passphrase_{};
    bool unlocked_ = false;
    QJsonObject plaintext_{};
};

} // namespace animica::nodekit
