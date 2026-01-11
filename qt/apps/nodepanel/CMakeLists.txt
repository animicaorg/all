qt_add_executable(animica_nodepanel
    src/main.cpp
)

target_link_libraries(animica_nodepanel
    PRIVATE Qt6::Core Qt6::Quick Qt6::Qml Qt6::QuickControls2
)

qt_add_qml_module(animica_nodepanel
    URI AnimicaNodePanel
    VERSION 1.0
    QML_FILES qml/main.qml
    RESOURCES qml/qml.qrc
)

set_target_properties(animica_nodepanel PROPERTIES
    MACOSX_BUNDLE TRUE
    WIN32_EXECUTABLE TRUE
)
