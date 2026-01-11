import QtQuick 2.15
import QtQuick.Controls 2.15

ApplicationWindow {
    visible: true
    width: 1200
    height: 720
    title: "Animica Wallet"

    TabView {
        anchors.fill: parent

        Tab {
            title: "Overview"
            Column {
                spacing: 12
                padding: 16
                Text { text: "Safe Head: " + appBackend.safeHeadHeight }
                Text { text: "Tip Head: " + appBackend.headHeight }
                Text { text: "Peers: " + appBackend.peers }
                Text { text: "Sync Phase: " + appBackend.syncPhase }
                Rectangle {
                    width: 160
                    height: 32
                    radius: 4
                    color: appBackend.nodeHealthy ? "#2ecc71" : "#e74c3c"
                    Text {
                        anchors.centerIn: parent
                        color: "white"
                        text: appBackend.nodeHealthy ? "Node Healthy" : "Node Degraded"
                    }
                }
            }
        }

        Tab {
            title: "Wallets"
            Column {
                spacing: 12
                padding: 16
                TextField { id: walletLabel; placeholderText: "Wallet label" }
                Button {
                    text: "Create Wallet"
                    onClicked: appBackend.createWallet(walletLabel.text)
                }
            }
        }

        Tab {
            title: "Send"
            Column {
                spacing: 12
                padding: 16
                TextField { placeholderText: "Recipient" }
                TextField { placeholderText: "Amount" }
                TextField { placeholderText: "Fee" }
                Button { text: "Preflight" }
                Button { text: "Send" }
            }
        }

        Tab {
            title: "Receive"
            Column {
                spacing: 12
                padding: 16
                Text { text: "Receive address will appear here" }
                Button { text: "Copy Address" }
            }
        }

        Tab {
            title: "Logs & Metrics"
            Column {
                spacing: 12
                padding: 16
                ListView {
                    width: parent.width
                    height: parent.height - 50
                    model: appBackend.logs
                    delegate: Text { text: modelData }
                }
            }
        }

        Tab {
            title: "Recovery"
            Column {
                spacing: 12
                padding: 16
                Button { text: "Apply Snapshot" }
                Button { text: "Reset Peers" }
                Button { text: "Force Resync" }
            }
        }
    }
}
