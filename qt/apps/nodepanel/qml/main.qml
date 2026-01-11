import QtQuick 2.15
import QtQuick.Controls 2.15

ApplicationWindow {
    visible: true
    width: 800
    height: 600
    title: "Animica Node Panel"

    Column {
        anchors.centerIn: parent
        spacing: 12
        Text { text: "Node diagnostics" }
        Text { text: "(NodeKit integration pending)" }
    }
}
