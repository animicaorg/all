#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>

#include "AppBackend.h"

int main(int argc, char *argv[]) {
    QGuiApplication app(argc, argv);

    AppBackend backend;

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("appBackend", &backend);
    engine.load(QUrl(QStringLiteral("qrc:/main.qml")));

    if (engine.rootObjects().isEmpty()) {
        return -1;
    }
    return app.exec();
}
