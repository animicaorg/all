#pragma once

#include <QObject>

namespace animica::nodekit {

class LocalChainAdapter : public QObject {
    Q_OBJECT

public:
    explicit LocalChainAdapter(QObject *parent = nullptr) : QObject(parent) {}
    virtual ~LocalChainAdapter() = default;

    virtual bool isAvailable() const = 0;
    virtual bool start() = 0;
    virtual void stop() = 0;
};

class BitcoinAdapter : public LocalChainAdapter {
    Q_OBJECT

public:
    explicit BitcoinAdapter(QObject *parent = nullptr) : LocalChainAdapter(parent) {}
    bool isAvailable() const override { return false; }
    bool start() override { return false; }
    void stop() override {}
};

class EthereumAdapter : public LocalChainAdapter {
    Q_OBJECT

public:
    explicit EthereumAdapter(QObject *parent = nullptr) : LocalChainAdapter(parent) {}
    bool isAvailable() const override { return false; }
    bool start() override { return false; }
    void stop() override {}
};

} // namespace animica::nodekit
