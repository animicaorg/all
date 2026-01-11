enable_testing()

find_package(Qt6 REQUIRED COMPONENTS Test)

add_executable(nodekit_rpc_test rpc_client_test.cpp)
target_link_libraries(nodekit_rpc_test PRIVATE AnimicaNodeKit Qt6::Test)
add_test(NAME nodekit_rpc_test COMMAND nodekit_rpc_test)

add_executable(nodekit_keystore_test keystore_test.cpp)
target_link_libraries(nodekit_keystore_test PRIVATE AnimicaNodeKit Qt6::Test)
add_test(NAME nodekit_keystore_test COMMAND nodekit_keystore_test)

add_executable(nodekit_integration_test nodekit_integration_test.cpp)
target_link_libraries(nodekit_integration_test PRIVATE AnimicaNodeKit Qt6::Test)
add_test(NAME nodekit_integration_test COMMAND nodekit_integration_test)
