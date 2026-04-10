#ifndef RPCSETTINGS_H
#define RPCSETTINGS_H

#include <QUrl>

struct RpcEndpointSettings {
    QString scheme;
    QString host;
    int port;
    QString path;
    QString username;
    QString password;
};

class RpcSettings
{
public:
    RpcSettings();

    RpcEndpointSettings load() const;
    RpcEndpointSettings defaults() const;

    static QUrl toUrl(const RpcEndpointSettings& settings);
    static QString toDisplayUrl(const RpcEndpointSettings& settings);
    static bool isDefault(const RpcEndpointSettings& settings);
    static QString canonicalRpcUrl();
    static QString canonicalNetwork();
    static int canonicalChainId();

private:
    static RpcEndpointSettings defaultSettings();
};

#endif // RPCSETTINGS_H
