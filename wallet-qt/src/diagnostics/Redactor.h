#ifndef REDACTOR_H
#define REDACTOR_H

#include <QString>
#include <QRegularExpression>
#include <QVector>

/**
 * @brief Redacts sensitive information from text outputs.
 * 
 * This class implements pattern-based redaction for:
 * - Credentials (passwords, tokens, admin keys)
 * - Private keys and secrets
 * - Seed phrases (12/24 word mnemonics)
 * - Environment variables with sensitive names
 * 
 * All redacted values are replaced with "***REDACTED***" while
 * preserving key names for debugging context.
 * 
 * Thread-safe for use in concurrent log processing.
 */
class Redactor
{
public:
    /**
     * @brief Redact sensitive information from text.
     * @param text Input text to redact
     * @return Text with sensitive values replaced by ***REDACTED***
     */
    static QString redact(const QString& text);

    /**
     * @brief Check if text contains sensitive patterns.
     * @param text Input text to check
     * @return true if sensitive data detected
     */
    static bool containsSensitiveData(const QString& text);

    /**
     * @brief Add custom redaction pattern.
     * @param pattern Regular expression pattern to redact
     * @param replacement Replacement text (default: ***REDACTED***)
     */
    static void addPattern(const QString& pattern, const QString& replacement = "***REDACTED***");

private:
    struct RedactionRule {
        QRegularExpression pattern;
        QString replacement;
    };

    static QVector<RedactionRule> s_rules;
    static bool s_initialized;
    
    static void initializeRules();
    static QString applyRule(const QString& text, const RedactionRule& rule);
};

#endif // REDACTOR_H
