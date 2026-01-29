#include <QtTest/QtTest>
#include "diagnostics/CommandAllowlist.h"
#include "diagnostics/RoleManager.h"

class TestAllowlist : public QObject
{
    Q_OBJECT

private slots:
    void testUserCommandsReadOnly()
    {
        // User role should only allow read-only commands
        QVERIFY(CommandAllowlist::isCommandAllowed("node status", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("peer list", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("sync status", RoleManager::Role::User));
        
        // Write operations should be denied
        QVERIFY(!CommandAllowlist::isCommandAllowed("peer add", RoleManager::Role::User));
        QVERIFY(!CommandAllowlist::isCommandAllowed("sync force", RoleManager::Role::User));
        QVERIFY(!CommandAllowlist::isCommandAllowed("node reset", RoleManager::Role::User));
    }

    void testOperatorCommandsIncludeNetworkOps()
    {
        // Operator should have user commands + network operations
        QVERIFY(CommandAllowlist::isCommandAllowed("node status", RoleManager::Role::Operator));
        QVERIFY(CommandAllowlist::isCommandAllowed("peer add 127.0.0.1:30333", RoleManager::Role::Operator));
        QVERIFY(CommandAllowlist::isCommandAllowed("sync force", RoleManager::Role::Operator));
        QVERIFY(CommandAllowlist::isCommandAllowed("node bootstrap", RoleManager::Role::Operator));
        
        // Dangerous operations still denied
        QVERIFY(!CommandAllowlist::isCommandAllowed("node reset", RoleManager::Role::Operator));
        QVERIFY(!CommandAllowlist::isCommandAllowed("mempool drop", RoleManager::Role::Operator));
    }

    void testDeveloperCommandsIncludeAll()
    {
        // Developer should have all commands
        QVERIFY(CommandAllowlist::isCommandAllowed("node status", RoleManager::Role::Developer));
        QVERIFY(CommandAllowlist::isCommandAllowed("peer add", RoleManager::Role::Developer));
        QVERIFY(CommandAllowlist::isCommandAllowed("sync force", RoleManager::Role::Developer));
        QVERIFY(CommandAllowlist::isCommandAllowed("node reset", RoleManager::Role::Developer));
        QVERIFY(CommandAllowlist::isCommandAllowed("mempool drop abc123", RoleManager::Role::Developer));
    }

    void testRpcMethodAllowlist()
    {
        // User: read-only RPC methods
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("node.getStatus", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("chain.getHead", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("p2p.getStatus", RoleManager::Role::User));
        
        // Write methods denied for user
        QVERIFY(!CommandAllowlist::isRpcMethodAllowed("sync.force", RoleManager::Role::User));
        QVERIFY(!CommandAllowlist::isRpcMethodAllowed("p2p.addPeer", RoleManager::Role::User));
        
        // Operator: write methods allowed
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("sync.force", RoleManager::Role::Operator));
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("p2p.addPeer", RoleManager::Role::Operator));
        
        // Developer: all methods allowed
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("miner.mine", RoleManager::Role::Developer));
        QVERIFY(CommandAllowlist::isRpcMethodAllowed("bootstrap.getManifest", RoleManager::Role::Developer));
    }

    void testCommandWithArguments()
    {
        // Commands with arguments should match prefix
        QVERIFY(CommandAllowlist::isCommandAllowed("node block 12345", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("node tx 0xabcd", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("peer info peer-id-123", RoleManager::Role::User));
    }

    void testCommandSuggestions()
    {
        QStringList suggestions = CommandAllowlist::getCommandSuggestions("node", RoleManager::Role::User);
        QVERIFY(suggestions.contains("node status"));
        QVERIFY(suggestions.contains("node head"));
        QVERIFY(suggestions.contains("node block"));
        QVERIFY(!suggestions.contains("node reset")); // Not available for User
        
        // Operator should have more suggestions
        QStringList opSuggestions = CommandAllowlist::getCommandSuggestions("node", RoleManager::Role::Operator);
        QVERIFY(opSuggestions.size() >= suggestions.size());
        QVERIFY(opSuggestions.contains("node bootstrap"));
    }

    void testCaseInsensitivity()
    {
        // Commands should be case-insensitive
        QVERIFY(CommandAllowlist::isCommandAllowed("NODE STATUS", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("Node Status", RoleManager::Role::User));
        QVERIFY(CommandAllowlist::isCommandAllowed("node STATUS", RoleManager::Role::User));
    }
};

QTEST_MAIN(TestAllowlist)
#include "test_allowlist.moc"
