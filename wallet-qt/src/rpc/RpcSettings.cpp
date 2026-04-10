#include "RpcSettings.h"

namespace {
constexpr const char* kCanonicalScheme = "https";
constexpr const char* kCanonicalHost = "rpc.animica.org";
constexpr int kCanonicalPort = 443;
constexpr int kCanonicalChainId = 1;
}

RpcSettings::RpcSettings() = default;

RpcEndpointSettings RpcSettings::defaultSettings()
{
    RpcEndpointSettings settings;
    settings.scheme = QString::fromLatin1(kCanonicalScheme);
    settings.host = QString::fromLatin1(kCanonicalHost);
    settings.port = kCanonicalPort;
    settings.path = QStringLiteral("/rpc");
    return settings;
}

RpcEndpointSettings RpcSettings::defaults() const
{
    return defaultSettings();
}

RpcEndpointSettings RpcSettings::load() const
{
    return defaultSettings();
}

QUrl RpcSettings::toUrl(const RpcEndpointSettings& settings)
{
    QUrl url;
    url.setScheme(settings.scheme);
    url.setHost(settings.host);

    const bool useDefaultHttpsPort =
        settings.scheme.compare(QStringLiteral("https"), Qt::CaseInsensitive) == 0 && settings.port == 443;
    const bool useDefaultHttpPort =
        settings.scheme.compare(QStringLiteral("http"), Qt::CaseInsensitive) == 0 && settings.port == 80;
    if (settings.port > 0 && !useDefaultHttpsPort && !useDefaultHttpPort) {
        url.setPort(settings.port);
    }

    if (!settings.path.trimmed().isEmpty() && settings.path != QStringLiteral("/")) {
        url.setPath(settings.path.startsWith('/') ? settings.path : QStringLiteral("/") + settings.path);
    }

    if (!settings.username.isEmpty()) {
        url.setUserName(settings.username);
        url.setPassword(settings.password);
    }

    return url;
}

QString RpcSettings::toDisplayUrl(const RpcEndpointSettings& settings)
{
    return toUrl(settings).toString();
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

QString RpcSettings::canonicalRpcUrl()
{
    return toDisplayUrl(defaultSettings());
}

QString RpcSettings::canonicalNetwork()
{
    return QStringLiteral("mainnet");
}

int RpcSettings::canonicalChainId()
{
    return kCanonicalChainId;
}
