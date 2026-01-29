#include "RoleManager.h"

RoleManager::RoleManager(QObject* parent)
    : QObject(parent)
    , m_operatorEnabled(false)
    , m_developerEnabled(false)
    , m_settings(new QSettings("Animica", "Wallet", this))
{
    loadSettings();
}

RoleManager::Role RoleManager::getCurrentRole() const
{
    if (m_developerEnabled) {
        return Role::Developer;
    }
    if (m_operatorEnabled) {
        return Role::Operator;
    }
    return Role::User;
}

void RoleManager::setOperatorEnabled(bool enabled)
{
    if (m_operatorEnabled == enabled) {
        return;
    }

    m_operatorEnabled = enabled;
    saveSettings();
    emit operatorEnabledChanged(enabled);
    emit roleChanged(getCurrentRole());
}

void RoleManager::setDeveloperEnabled(bool enabled)
{
    if (m_developerEnabled == enabled) {
        return;
    }

    m_developerEnabled = enabled;
    saveSettings();
    emit developerEnabledChanged(enabled);
    emit roleChanged(getCurrentRole());
}

QString RoleManager::roleToString(Role role)
{
    switch (role) {
    case Role::User:
        return "User";
    case Role::Operator:
        return "Operator";
    case Role::Developer:
        return "Developer";
    default:
        break;
    }
    return "Unknown";
}

void RoleManager::loadSettings()
{
    m_settings->beginGroup("Diagnostics");
    m_operatorEnabled = m_settings->value("operatorEnabled", false).toBool();
    m_developerEnabled = m_settings->value("developerEnabled", false).toBool();
    m_settings->endGroup();
}

void RoleManager::saveSettings()
{
    m_settings->beginGroup("Diagnostics");
    m_settings->setValue("operatorEnabled", m_operatorEnabled);
    m_settings->setValue("developerEnabled", m_developerEnabled);
    m_settings->endGroup();
    m_settings->sync();
}
