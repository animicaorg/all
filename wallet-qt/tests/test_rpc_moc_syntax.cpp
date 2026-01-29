/**
 * @file test_rpc_moc_syntax.cpp
 * @brief Compile-time test to verify AnimicaRpcClient.h MOC syntax
 * 
 * This test ensures that AnimicaRpcClient.h can be processed by Qt's MOC
 * (Meta-Object Compiler) without errors. The test will fail to compile
 * if MOC encounters invalid signal/slot declarations.
 * 
 * Background: Prior to the fix, line 217 caused the error:
 * "Not a signal or slot declaration" because member variables and
 * private methods were incorrectly placed inside the signals: section.
 * 
 * This test verifies that:
 * 1. The class properly includes Q_OBJECT macro
 * 2. The signals: section contains only signal declarations
 * 3. Private methods and member variables are in the private: section
 */

#include <QtTest/QtTest>
#include "../src/rpc/AnimicaRpcClient.h"

class TestRpcMocSyntax : public QObject
{
    Q_OBJECT

private slots:
    /**
     * @brief Test that AnimicaRpcClient can be instantiated
     * 
     * This simple test ensures the header compiles and MOC processes it correctly.
     * If MOC fails, this test won't even compile.
     */
    void testInstantiation()
    {
        AnimicaRpcClient client;
        QVERIFY(true); // If we get here, MOC succeeded
    }

    /**
     * @brief Test that signals are properly declared
     * 
     * Verify that signals are accessible via Qt's meta-object system.
     */
    void testSignalsExist()
    {
        AnimicaRpcClient client;
        
        // Check that the meta-object system recognizes the signals
        const QMetaObject* metaObj = client.metaObject();
        QVERIFY(metaObj != nullptr);
        
        // Verify the class name
        QCOMPARE(QString(metaObj->className()), QString("AnimicaRpcClient"));
        
        // Check that signals exist in the meta-object
        // connected(), disconnected(), error(QString) should be present
        int signalCount = 0;
        for (int i = metaObj->methodOffset(); i < metaObj->methodCount(); ++i) {
            QMetaMethod method = metaObj->method(i);
            if (method.methodType() == QMetaMethod::Signal) {
                signalCount++;
                qDebug() << "Found signal:" << method.methodSignature();
            }
        }
        
        // Should have at least 3 signals: connected(), disconnected(), error(QString)
        // (may have more if inherited from QObject)
        QVERIFY(signalCount >= 3);
    }

    /**
     * @brief Test that the class inherits from QObject
     */
    void testInheritance()
    {
        AnimicaRpcClient client;
        QObject* obj = &client;
        QVERIFY(obj != nullptr);
        QVERIFY(qobject_cast<AnimicaRpcClient*>(obj) != nullptr);
    }
};

// Register the test class with Qt's test framework
QTEST_MAIN(TestRpcMocSyntax)
#include "test_rpc_moc_syntax.moc"
