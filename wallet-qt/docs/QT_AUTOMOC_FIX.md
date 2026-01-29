# Qt AutoMOC Fix for AnimicaRpcClient

## Problem
The Qt wallet build was failing with the following MOC (Meta-Object Compiler) error:

```
/src/rpc/AnimicaRpcClient.h:217:1: error: Not a signal or slot declaration
```

## Root Cause
The `signals:` section in `AnimicaRpcClient.h` incorrectly contained:
1. **Member variable declarations** (lines 217-220): `m_network`, `m_endpoint`, `m_timeout`, `m_requestId`
2. **Private method declarations** (lines 203-215): `buildRequest()`, `nextId()`
3. **Duplicate method declaration** (line 201): `call()` was already declared in the public section

Qt's MOC strictly requires that `signals:` sections contain **ONLY** signal declarations (void return type functions that emit events). Any other content causes MOC to fail with "Not a signal or slot declaration".

## Solution
Restructured `AnimicaRpcClient.h` to follow proper Qt MOC conventions:

### Before (Incorrect)
```cpp
signals:
    void connected();
    void disconnected();
    void error(const QString& message);

    // ❌ WRONG: Private methods in signals section
    QNetworkReply* call(const QString& method, const QJsonValue& params = QJsonArray());
    QJsonObject buildRequest(const QString& method, const QJsonValue& params);
    int nextId();

    // ❌ WRONG: Member variables in signals section
    QNetworkAccessManager* m_network;
    QUrl m_endpoint;
    int m_timeout;
    int m_requestId;
};
```

### After (Correct)
```cpp
signals:
    void connected();
    void disconnected();
    void error(const QString& message);

private:
    // ✅ CORRECT: Private methods in private section
    QJsonObject buildRequest(const QString& method, const QJsonValue& params);
    int nextId();

    // ✅ CORRECT: Member variables in private section
    QNetworkAccessManager* m_network;
    QUrl m_endpoint;
    int m_timeout;
    int m_requestId;
};
```

## Changes Made
- **Removed** duplicate `call()` declaration from signals section (already declared in public section)
- **Moved** `buildRequest()` and `nextId()` helper methods to `private:` section
- **Moved** all member variables (`m_network`, `m_endpoint`, `m_timeout`, `m_requestId`) to `private:` section
- **Added** explanatory comment about MOC requirements

## Verification
A new test was added to ensure this issue doesn't reoccur:

**Test:** `tests/test_rpc_moc_syntax.cpp`
- Verifies that `AnimicaRpcClient.h` compiles with Qt's AUTOMOC enabled
- Checks that the Q_OBJECT macro is properly recognized
- Validates that signals are accessible via Qt's meta-object system
- Confirms proper inheritance from QObject

To run the test:
```bash
cd build
cmake .. -DBUILD_TESTING=ON
make test_rpc_moc_syntax
./tests/test_rpc_moc_syntax
```

## Qt MOC Requirements Summary
For future reference, Qt's MOC has strict rules:

### ✅ Allowed in `signals:` section
- Signal declarations: `void signalName(args);`
- Documentation comments
- Preprocessor directives (with caution)

### ❌ NOT allowed in `signals:` section
- Member variables
- Non-signal methods (methods with return types other than void, or methods that are not signals)
- Using declarations / type aliases
- Enums
- Nested structs/classes
- Q_PROPERTY macros
- Q_ENUM macros
- Static member declarations
- Friend declarations

### General Rules
1. **Always include `Q_OBJECT` macro** in classes that use signals/slots
2. **Derive from QObject** (directly or indirectly)
3. **Keep sections clean**: signals in `signals:`, slots in `public/private/protected slots:`, everything else in regular sections
4. **Member variables go last** in the `private:` section

## References
- Qt Documentation: [Signals & Slots](https://doc.qt.io/qt-6/signalsandslots.html)
- Qt Documentation: [Using the Meta-Object Compiler (moc)](https://doc.qt.io/qt-6/moc.html)
