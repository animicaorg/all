#include "RpcSettings.h"

namespace {
const char kGroupName[] = "RpcEndpoint";
}

RpcSettings::RpcSettings()
    : m_settings()
{
}

RpcEndpointSettings RpcSettings::defaultSettings()
{
    RpcEndpointSettings settings;
    settings.scheme = "http";
    settings.host = "127.0.0.1";
    settings.port = 8545;
    settings.path = "/rpc";
    return settings;
}

RpcEndpointSettings RpcSettings::defaults() const
{
    return defaultSettings();
}

RpcEndpointSettings RpcSettings::load() const
{
    RpcEndpointSettings settings = defaultSettings();

    m_settings.beginGroup(kGroupName);
    settings.scheme = m_settings.value("scheme", settings.scheme).toString();
    settings.host = m_settings.value("host", settings.host).toString();
    settings.port = m_settings.value("port", settings.port).toInt();
    settings.path = m_settings.value("path", settings.path).toString();
    settings.username = m_settings.value("username", settings.username).toString();
    settings.password = m_settings.value("password", settings.password).toString();
    m_settings.endGroup();

    return settings;
}

void RpcSettings::save(const RpcEndpointSettings& settings)
{
    m_settings.beginGroup(kGroupName);
    m_settings.setValue("scheme", settings.scheme);
    m_settings.setValue("host", settings.host);
    m_settings.setValue("port", settings.port);
    m_settings.setValue("path", settings.path);
    m_settings.setValue("username", settings.username);
    m_settings.setValue("password", settings.password);
    m_settings.endGroup();
    m_settings.sync();
}

QUrl RpcSettings::toUrl(const RpcEndpointSettings& settings)
{
    QUrl url;
    url.setScheme(settings.scheme);
    url.setHost(settings.host);
    url.setPort(settings.port);
    url.setPath(settings.path.startsWith('/') ? settings.path : "/" + settings.path);

    if (!settings.username.isEmpty()) {
        url.setUserName(settings.username);
        url.setPassword(settings.password);
    }

    return url;
}

QString RpcSettings::toDisplayUrl(const RpcEndpointSettings& settings)
{
    QUrl url;
    url.setScheme(settings.scheme);
    url.setHost(settings.host);
    url.setPort(settings.port);
    url.setPath(settings.path.startsWith('/') ? settings.path : "/" + settings.path);
    return url.toString(QUrl::FullyDecoded);
}

bool RpcSettings::isDefault(const RpcEndpointSettings& settings)
{
    const RpcEndpointSettings defaults = defaultSettings();
    return settings.scheme == defaults.scheme
        && settings.host == defaults.host
        && settings.port == defaults.port
        && settings.path == defaults.path
        && settings.username.isEmpty()
        && settings.password.isEmpty();
}
