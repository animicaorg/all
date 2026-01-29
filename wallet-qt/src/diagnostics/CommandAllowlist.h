#ifndef COMMANDALLOWLIST_H
#define COMMANDALLOWLIST_H

#include "RoleManager.h"
#include <QSet>
#include <QString>

/**
 * @brief Command and RPC method allowlists for diagnostics console.
 * 
 * Implements security policy from docs/diagnostics_surface.md:
 * - User role: Read-only commands and RPC methods
 * - Operator role: User + network operations
 * - Developer role: Operator + dangerous operations
 * 
 * Commands are CLI commands like "node status", "peer list".
 * RPC methods are JSON-RPC methods like "node.getStatus", "chain.getHead".
 */
class CommandAllowlist
{
public:
    /**
     * @brief Check if CLI command is allowed for role.
     * @param command Full command string (e.g., "node status")
     * @param role User role
     * @return true if allowed
     */
    static bool isCommandAllowed(const QString& command, RoleManager::Role role);

    /**
     * @brief Check if RPC method is allowed for role.
     * @param method RPC method name (e.g., "node.getStatus")
     * @param role User role
     * @return true if allowed
     */
    static bool isRpcMethodAllowed(const QString& method, RoleManager::Role role);

    /**
     * @brief Get all allowed CLI commands for role.
     * @param role User role
     * @return Set of allowed command prefixes
     */
    static QSet<QString> getAllowedCommands(RoleManager::Role role);

    /**
     * @brief Get all allowed RPC methods for role.
     * @param role User role
     * @return Set of allowed RPC methods
     */
    static QSet<QString> getAllowedRpcMethods(RoleManager::Role role);

    /**
     * @brief Get command suggestions for autocomplete.
     * @param partial Partial command string
     * @param role User role
     * @return List of matching commands
     */
    static QStringList getCommandSuggestions(const QString& partial, RoleManager::Role role);

private:
    static void initializeAllowlists();
    static bool s_initialized;

    // CLI command allowlists
    static QSet<QString> s_userCommands;
    static QSet<QString> s_operatorCommands;
    static QSet<QString> s_developerCommands;

    // RPC method allowlists
    static QSet<QString> s_userRpcMethods;
    static QSet<QString> s_operatorRpcMethods;
    static QSet<QString> s_developerRpcMethods;
};

#endif // COMMANDALLOWLIST_H
