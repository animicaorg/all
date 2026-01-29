#include "CommandAllowlist.h"

bool CommandAllowlist::s_initialized = false;
QSet<QString> CommandAllowlist::s_userCommands;
QSet<QString> CommandAllowlist::s_operatorCommands;
QSet<QString> CommandAllowlist::s_developerCommands;
QSet<QString> CommandAllowlist::s_userRpcMethods;
QSet<QString> CommandAllowlist::s_operatorRpcMethods;
QSet<QString> CommandAllowlist::s_developerRpcMethods;

void CommandAllowlist::initializeAllowlists()
{
    if (s_initialized) {
        return;
    }

    // User CLI commands (read-only)
    s_userCommands = {
        "node status",
        "node head",
        "node block",
        "node tx",
        "peer list",
        "peer info",
        "peer diagnose",
        "peer test-latency",
        "sync status",
        "mempool list",
        "mempool stats",
    };

    // Operator CLI commands (adds network operations)
    s_operatorCommands = s_userCommands;
    for (const auto& cmd : QStringList{
        "peer add",
        "peer remove",
        "peer bootstrap",
        "sync pause",
        "sync resume",
        "sync force",
        "node bootstrap",
    }) {
        s_operatorCommands.insert(cmd);
    }

    // Developer CLI commands (adds dangerous operations)
    s_developerCommands = s_operatorCommands;
    for (const auto& cmd : QStringList{
        "node reset",
        "mempool drop",
    }) {
        s_developerCommands.insert(cmd);
    }

    // User RPC methods (read-only)
    s_userRpcMethods = {
        // Node & Health
        "node.ping",
        "node.getStatus",
        "node.syncStatus",
        
        // Chain
        "chain.getHead",
        "chain.getParams",
        "chain.getChainId",
        "chain.getChainIdentity",
        "chain.getNetworkHashrate",
        "chain.getForks",
        "chain.getCheckpoints",
        "chain.getBlockByHeight",
        "block.getBlockByNumber",
        "block.getBlockByHash",
        
        // Sync
        "sync.getStatus",
        
        // P2P
        "p2p.getStatus",
        "p2p.getPeerStats",
        "p2p.debugStatus",
        "p2p.syncDebug",
        "net.peerCount",
        "net.peers",
        "net.getBootstrapSeeds",
        "p2p.getPeerInfo",
        "p2p.getBans",
        "p2p.getVerifierSeeds",
        
        // Transaction
        "tx.getTransactionByHash",
        "tx.getTransaction",
        "tx.getTransactionStatus",
        "tx.getStatus",
        "tx.decodeRawTransaction",
        "tx.debugVerifyRawTransaction",
        
        // State
        "state.getBalance",
        "state.getNonce",
        "state.getPendingNonce",
        "state.getNextNonce",
        "state.getAccount",
        "state.getRichList",
        "state.getTotalSupply",
        
        // Mempool
        "mempool.getPending",
        "mempool.getStats",
        "mempool.getInfo",
        
        // Mining (read-only)
        "miner.getBlockTemplate",
        "mining.getTemplateStatus",
        "mining.getCredits",
    };

    // Operator RPC methods (adds write operations)
    s_operatorRpcMethods = s_userRpcMethods;
    s_operatorRpcMethods.unite({
        // Sync control
        "sync.force",
        "sync.trigger",
        "sync.start",
        "sync.pause",
        "sync.resume",
        "sync.setTarget",
        
        // P2P management
        "p2p.addPeer",
        "p2p.removePeer",
        "p2p.importPeers",
        "p2p.addPeers",
        "p2p.banPeer",
        "p2p.unbanPeer",
        
        // Mempool
        "mempool.dropTransaction",
        
        // Mining
        "miner.stop",
    });

    // Developer RPC methods (adds dangerous operations)
    s_developerRpcMethods = s_operatorRpcMethods;
    s_developerRpcMethods.unite({
        // Bootstrap
        "bootstrap.getManifest",
        "bootstrap.getSeeds",
        "bootstrap.getSnapshotManifest",
        
        // Mining
        "miner.mine",
        
        // Transaction (wallet handles this, but exposed for debugging)
        "tx.sendRawTransaction",
    });

    s_initialized = true;
}

bool CommandAllowlist::isCommandAllowed(const QString& command, RoleManager::Role role)
{
    if (!s_initialized) {
        initializeAllowlists();
    }

    QString normalized = command.trimmed().toLower();

    // Get appropriate allowlist based on role
    const QSet<QString>* allowlist = nullptr;
    switch (role) {
    case RoleManager::Role::Developer:
        allowlist = &s_developerCommands;
        break;
    case RoleManager::Role::Operator:
        allowlist = &s_operatorCommands;
        break;
    case RoleManager::Role::User:
    default:
        allowlist = &s_userCommands;
        break;
    }

    // Check for exact match or prefix match
    for (const QString& allowed : *allowlist) {
        if (normalized == allowed || normalized.startsWith(allowed + " ")) {
            return true;
        }
    }

    return false;
}

bool CommandAllowlist::isRpcMethodAllowed(const QString& method, RoleManager::Role role)
{
    if (!s_initialized) {
        initializeAllowlists();
    }

    // Get appropriate allowlist based on role
    const QSet<QString>* allowlist = nullptr;
    switch (role) {
    case RoleManager::Role::Developer:
        allowlist = &s_developerRpcMethods;
        break;
    case RoleManager::Role::Operator:
        allowlist = &s_operatorRpcMethods;
        break;
    case RoleManager::Role::User:
    default:
        allowlist = &s_userRpcMethods;
        break;
    }

    return allowlist->contains(method);
}

QSet<QString> CommandAllowlist::getAllowedCommands(RoleManager::Role role)
{
    if (!s_initialized) {
        initializeAllowlists();
    }

    switch (role) {
    case RoleManager::Role::Developer:
        return s_developerCommands;
    case RoleManager::Role::Operator:
        return s_operatorCommands;
    case RoleManager::Role::User:
    default:
        return s_userCommands;
    }
}

QSet<QString> CommandAllowlist::getAllowedRpcMethods(RoleManager::Role role)
{
    if (!s_initialized) {
        initializeAllowlists();
    }

    switch (role) {
    case RoleManager::Role::Developer:
        return s_developerRpcMethods;
    case RoleManager::Role::Operator:
        return s_operatorRpcMethods;
    case RoleManager::Role::User:
    default:
        return s_userRpcMethods;
    }
}

QStringList CommandAllowlist::getCommandSuggestions(const QString& partial, RoleManager::Role role)
{
    if (!s_initialized) {
        initializeAllowlists();
    }

    QString normalized = partial.trimmed().toLower();
    QStringList suggestions;

    const QSet<QString> allowed = getAllowedCommands(role);
    for (const QString& command : allowed) {
        if (command.startsWith(normalized)) {
            suggestions.append(command);
        }
    }

    suggestions.sort();
    return suggestions;
}
