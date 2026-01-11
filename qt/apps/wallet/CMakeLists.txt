qt_add_executable(animica_wallet
    src/main.cpp
    src/AppBackend.cpp
    src/AppBackend.h
)

target_link_libraries(animica_wallet
    PRIVATE Qt6::Core Qt6::Quick Qt6::Qml Qt6::QuickControls2 AnimicaNodeKit
)

qt_add_qml_module(animica_wallet
    URI AnimicaWallet
    VERSION 1.0
    QML_FILES qml/main.qml
    RESOURCES qml/qml.qrc
)

set_target_properties(animica_wallet PROPERTIES
    MACOSX_BUNDLE TRUE
    WIN32_EXECUTABLE TRUE
)
