#ifndef ROLEMANAGER_H
#define ROLEMANAGER_H

#include <QObject>
#include <QSettings>

/**
 * @brief Manages User/Operator/Developer roles for diagnostics UI.
 * 
 * Roles determine which commands and RPC methods are accessible:
 * - User: Read-only operations (default, always enabled)
 * - Operator: Network operations (bootstrap, sync control, peer management)
 * - Developer: Dangerous operations (node reset, dev RPC methods)
 * 
 * Role states are persisted in application settings.
 * Higher roles inherit lower role permissions (Developer > Operator > User).
 */
class RoleManager : public QObject
{
    Q_OBJECT

public:
    enum class Role {
        User,       // Read-only, safe queries
        Operator,   // User + network operations
        Developer   // Operator + dangerous operations
    };
    Q_ENUM(Role)

    explicit RoleManager(QObject* parent = nullptr);

    /**
     * @brief Get current active role.
     * @return Current role (highest enabled)
     */
    Role getCurrentRole() const;

    /**
     * @brief Check if Operator role is enabled.
     */
    bool isOperatorEnabled() const { return m_operatorEnabled; }

    /**
     * @brief Check if Developer role is enabled.
     */
    bool isDeveloperEnabled() const { return m_developerEnabled; }

    /**
     * @brief Enable or disable Operator role.
     * @param enabled true to enable
     */
    void setOperatorEnabled(bool enabled);

    /**
     * @brief Enable or disable Developer role.
     * @param enabled true to enable
     */
    void setDeveloperEnabled(bool enabled);

    /**
     * @brief Get role display name.
     * @param role Role to get name for
     * @return User-friendly role name
     */
    static QString roleToString(Role role);

signals:
    /**
     * @brief Emitted when active role changes.
     * @param role New current role
     */
    void roleChanged(Role role);

    /**
     * @brief Emitted when Operator role is enabled/disabled.
     * @param enabled New state
     */
    void operatorEnabledChanged(bool enabled);

    /**
     * @brief Emitted when Developer role is enabled/disabled.
     * @param enabled New state
     */
    void developerEnabledChanged(bool enabled);

private:
    void loadSettings();
    void saveSettings();

    bool m_operatorEnabled;
    bool m_developerEnabled;
    QSettings* m_settings;
};

#endif // ROLEMANAGER_H
