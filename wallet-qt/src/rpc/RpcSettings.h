#ifndef RPCSETTINGS_H
#define RPCSETTINGS_H

#include <QSettings>
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
    void save(const RpcEndpointSettings& settings);
    RpcEndpointSettings defaults() const;

    static QUrl toUrl(const RpcEndpointSettings& settings);
    static QString toDisplayUrl(const RpcEndpointSettings& settings);
    static bool isDefault(const RpcEndpointSettings& settings);

private:
    mutable QSettings m_settings;
    static RpcEndpointSettings defaultSettings();
};

#endif // RPCSETTINGS_H
