#include "Redactor.h"
#include <QMutex>
#include <QMutexLocker>

QVector<Redactor::RedactionRule> Redactor::s_rules;
bool Redactor::s_initialized = false;

static QMutex s_mutex;

void Redactor::initializeRules()
{
    if (s_initialized) {
        return;
    }

    s_rules.clear();

    // Credential patterns (key=value, key:value, or "key": "value" formats)
    s_rules.append({
        QRegularExpression(R"((rpcpassword|admin_token|password|token|auth|bearer)[\s]*[=:]\s*([^\s,}\]"']+|"[^"]*"|'[^']*'))"),
        R"(\1=***REDACTED***)"
    });

    // Environment variables with sensitive content
    s_rules.append({
        QRegularExpression(R"((ANIMICA_RPC_ADMIN_TOKEN|.*PASSWORD|.*SECRET|.*TOKEN|.*KEY)[\s]*[=:]\s*([^\s,}\]"']+|"[^"]*"|'[^']*'))"),
        R"(\1=***REDACTED***)"
    });

    // HTTP headers with tokens
    s_rules.append({
        QRegularExpression(R"((X-Animica-Admin-Token|Authorization|Bearer)[\s]*:\s*([^\s,}\]"'\r\n]+))"),
        R"(\1: ***REDACTED***)"
    });

    // Private key patterns (private_key, privkey, secret followed by hex)
    s_rules.append({
        QRegularExpression(R"((private_key|privkey|secret|priv)[\s]*[=:]\s*(0x[0-9a-fA-F]{6,}|[0-9a-fA-F]{6,}|"0x?[0-9a-fA-F]{6,}"|'0x?[0-9a-fA-F]{6,}'))"),
        R"(\1=***REDACTED***)"
    });

    // Long hex strings that look like keys (64+ chars) after sensitive keywords
    s_rules.append({
        QRegularExpression(R"((key|secret|priv)[^\s]*[\s]*[=:]\s*0x([0-9a-fA-F]{64,}))"),
        R"(\1=0x***REDACTED***)"
    });

    // Standalone long hex strings (128+ chars, likely keys)
    s_rules.append({
        QRegularExpression(R"(\b0x[0-9a-fA-F]{128,}\b)"),
        "0x***REDACTED***"
    });

    // Seed phrase patterns (12 or 24 word sequences)
    // Simplified: detect "word1 word2 word3..." patterns
    s_rules.append({
        QRegularExpression(R"((\b[a-z]{3,8}\s){11}[a-z]{3,8}\b)"),  // 12 words
        "***REDACTED_SEED_PHRASE***"
    });
    s_rules.append({
        QRegularExpression(R"((\b[a-z]{3,8}\s){23}[a-z]{3,8}\b)"),  // 24 words
        "***REDACTED_SEED_PHRASE***"
    });

    // JSON field patterns with sensitive keys
    s_rules.append({
        QRegularExpression(R"re("(privateKey|private_key|secretKey|secret_key|mnemonic|seed|password)"\s*:\s*"([^"]*)")re"),
        R"("\1": "***REDACTED***")"
    });

    s_initialized = true;
}

QString Redactor::redact(const QString& text)
{
    QMutexLocker locker(&s_mutex);
    
    if (!s_initialized) {
        initializeRules();
    }

    QString result = text;
    for (const auto& rule : s_rules) {
        result = result.replace(rule.pattern, rule.replacement);
    }

    return result;
}

bool Redactor::containsSensitiveData(const QString& text)
{
    QMutexLocker locker(&s_mutex);
    
    if (!s_initialized) {
        initializeRules();
    }

    for (const auto& rule : s_rules) {
        if (rule.pattern.match(text).hasMatch()) {
            return true;
        }
    }

    return false;
}

void Redactor::addPattern(const QString& pattern, const QString& replacement)
{
    QMutexLocker locker(&s_mutex);
    
    s_rules.append({
        QRegularExpression(pattern),
        replacement
    });
}
