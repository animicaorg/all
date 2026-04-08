# AnimicaNode.cmake
#
# Provides the animica_build_node() function which:
# 1. Creates a Python virtual environment
# 2. Installs the Animica node and all its dependencies
# 3. Returns the path to the bundled Python interpreter
#
# This ensures a hermetic, reproducible build of the Animica node
# that can be bundled with the Qt wallet application.

# Find the repository root (assuming wallet-qt is a subdirectory)
get_filename_component(ANIMICA_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)

# Detect Python 3.11+ (required for Animica)
function(find_python_for_node OUT_PYTHON)
    # Try common Python 3 names in order of preference
    set(PYTHON_NAMES python3.12 python3.11 python3.10 python3 python)
    
    foreach(PY_NAME ${PYTHON_NAMES})
        find_program(PYTHON_CANDIDATE ${PY_NAME})
        if(PYTHON_CANDIDATE)
            # Check version
            execute_process(
                COMMAND ${PYTHON_CANDIDATE} -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
                OUTPUT_VARIABLE PY_VERSION
                OUTPUT_STRIP_TRAILING_WHITESPACE
                ERROR_QUIET
            )
            
            if(PY_VERSION VERSION_GREATER_EQUAL "3.10")
                message(STATUS "Found suitable Python: ${PYTHON_CANDIDATE} (${PY_VERSION})")
                set(${OUT_PYTHON} ${PYTHON_CANDIDATE} PARENT_SCOPE)
                return()
            else()
                message(STATUS "Python ${PYTHON_CANDIDATE} version ${PY_VERSION} too old (need 3.10+)")
            endif()
        endif()
        unset(PYTHON_CANDIDATE CACHE)
    endforeach()
    
    message(FATAL_ERROR 
        "Python 3.10+ not found. Please install Python 3.10 or newer:\n"
        "  Ubuntu/Debian: sudo apt-get install python3.11 python3.11-venv\n"
        "  macOS:         brew install python@3.11\n"
        "  Windows:       Download from https://www.python.org/downloads/\n"
    )
endfunction()

# Main function: Build the Animica node and return path to Python
function(animica_build_node OUT_VAR)
    message(STATUS "")
    message(STATUS "========================================")
    message(STATUS "Building Animica Node")
    message(STATUS "========================================")
    
    # Find Python
    find_python_for_node(PYTHON_EXE)
    
    # Determine build output directory
    set(NODE_BUILD_DIR "${CMAKE_BINARY_DIR}/animica-node")
    set(NODE_VENV_DIR "${NODE_BUILD_DIR}/venv")
    
    # Determine the Python executable in the venv
    if(WIN32)
        set(NODE_PYTHON "${NODE_VENV_DIR}/Scripts/python.exe")
        set(NODE_PIP "${NODE_VENV_DIR}/Scripts/pip.exe")
    else()
        set(NODE_PYTHON "${NODE_VENV_DIR}/bin/python")
        set(NODE_PIP "${NODE_VENV_DIR}/bin/pip")
    endif()
    
    # Create the virtual environment if it doesn't exist
    if(NOT EXISTS "${NODE_PYTHON}")
        message(STATUS "Creating Python virtual environment at ${NODE_VENV_DIR}")
        execute_process(
            COMMAND ${PYTHON_EXE} -m venv "${NODE_VENV_DIR}"
            RESULT_VARIABLE VENV_RESULT
            OUTPUT_VARIABLE VENV_OUTPUT
            ERROR_VARIABLE VENV_ERROR
        )
        
        if(NOT VENV_RESULT EQUAL 0)
            message(FATAL_ERROR 
                "Failed to create Python virtual environment:\n"
                "Command: ${PYTHON_EXE} -m venv ${NODE_VENV_DIR}\n"
                "Output: ${VENV_OUTPUT}\n"
                "Error: ${VENV_ERROR}\n"
            )
        endif()
    else()
        message(STATUS "Using existing virtual environment at ${NODE_VENV_DIR}")
    endif()
    
    # Upgrade pip, setuptools, wheel
    message(STATUS "Upgrading pip, setuptools, wheel")
    execute_process(
        COMMAND ${NODE_PYTHON} -m pip install --upgrade pip setuptools wheel
        RESULT_VARIABLE PIP_UPGRADE_RESULT
        OUTPUT_QUIET
        ERROR_VARIABLE PIP_UPGRADE_ERROR
    )
    
    if(NOT PIP_UPGRADE_RESULT EQUAL 0)
        message(WARNING "Failed to upgrade pip: ${PIP_UPGRADE_ERROR}")
    endif()
    
    # Install core dependencies (FastAPI, uvicorn, prometheus)
    message(STATUS "Installing core RPC dependencies")
    execute_process(
        COMMAND ${NODE_PIP} install 
            "fastapi>=0.115.0,<0.116.0"
            "uvicorn[standard]>=0.30.0,<1.0.0"
            "prometheus-client>=0.20.0,<1.0.0"
        RESULT_VARIABLE DEPS_RESULT
        OUTPUT_QUIET
        ERROR_VARIABLE DEPS_ERROR
    )
    
    if(NOT DEPS_RESULT EQUAL 0)
        message(FATAL_ERROR 
            "Failed to install RPC dependencies:\n"
            "Error: ${DEPS_ERROR}\n"
        )
    endif()
    
    # Install omni-sdk (required by animica CLI)
    set(SDK_PATH "${ANIMICA_REPO_ROOT}/sdk/python")
    if(EXISTS "${SDK_PATH}/pyproject.toml")
        message(STATUS "Installing omni-sdk from ${SDK_PATH}")
        execute_process(
            COMMAND ${NODE_PIP} install "${SDK_PATH}"
            RESULT_VARIABLE SDK_RESULT
            OUTPUT_QUIET
            ERROR_VARIABLE SDK_ERROR
        )
        
        if(NOT SDK_RESULT EQUAL 0)
            message(WARNING "Failed to install omni-sdk: ${SDK_ERROR}")
        endif()
    else()
        message(WARNING "omni-sdk not found at ${SDK_PATH}")
    endif()
    
    # Install animica package (CLI + node + wallet QR helper)
    set(ANIMICA_PATH "${ANIMICA_REPO_ROOT}/python")
    if(EXISTS "${ANIMICA_PATH}/pyproject.toml")
        message(STATUS "Installing animica package from ${ANIMICA_PATH}")
        execute_process(
            COMMAND ${NODE_PIP} install "${ANIMICA_PATH}[wallet_qt]"
            RESULT_VARIABLE ANIMICA_RESULT
            OUTPUT_QUIET
            ERROR_VARIABLE ANIMICA_ERROR
        )
        
        if(NOT ANIMICA_RESULT EQUAL 0)
            message(FATAL_ERROR 
                "Failed to install animica package:\n"
                "Path: ${ANIMICA_PATH}\n"
                "Error: ${ANIMICA_ERROR}\n"
            )
        endif()
    else()
        message(FATAL_ERROR "Animica package not found at ${ANIMICA_PATH}")
    endif()
    
    # Install pq package (optional, for PQ crypto)
    set(PQ_PATH "${ANIMICA_REPO_ROOT}/pq")
    if(EXISTS "${PQ_PATH}/pyproject.toml")
        message(STATUS "Installing pq package from ${PQ_PATH}")
        execute_process(
            COMMAND ${NODE_PIP} install "${PQ_PATH}"
            RESULT_VARIABLE PQ_RESULT
            OUTPUT_QUIET
            ERROR_VARIABLE PQ_ERROR
        )
        
        if(NOT PQ_RESULT EQUAL 0)
            message(WARNING "Failed to install pq package: ${PQ_ERROR}")
        endif()
    endif()
    
    # Copy repository modules into venv for bundled execution
    # The RPC module and others (core, consensus, execution, etc.) are not installed packages
    # but are meant to be imported from the repo root. We need to make them accessible.
    message(STATUS "Copying repository modules into venv")
    set(REPO_MODULES
        rpc
        core
        consensus
        execution
        mempool
        p2p
        mining
        proofs
        da
        randomness
        pq
        capabilities
        aicf
        queue
        chains
        genesis
        spec
        services
        billing
        relayer
    )
    
    set(VENV_SITE_PACKAGES "${NODE_VENV_DIR}/lib")
    if(WIN32)
        set(VENV_SITE_PACKAGES "${NODE_VENV_DIR}/Lib/site-packages")
    else()
        # Find the correct python version in lib/
        file(GLOB PYTHON_DIRS "${NODE_VENV_DIR}/lib/python*")
        if(PYTHON_DIRS)
            list(GET PYTHON_DIRS 0 PYTHON_DIR)
            set(VENV_SITE_PACKAGES "${PYTHON_DIR}/site-packages")
        endif()
    endif()
    
    foreach(MODULE ${REPO_MODULES})
        if(EXISTS "${ANIMICA_REPO_ROOT}/${MODULE}")
            message(STATUS "  Copying ${MODULE}")
            file(COPY "${ANIMICA_REPO_ROOT}/${MODULE}"
                 DESTINATION "${VENV_SITE_PACKAGES}"
                 PATTERN "*.pyc" EXCLUDE
                 PATTERN "__pycache__" EXCLUDE
                 PATTERN ".git" EXCLUDE
                 PATTERN "*.egg-info" EXCLUDE
            )
        endif()
    endforeach()
    
    message(STATUS "Repository modules copied to ${VENV_SITE_PACKAGES}")
    
    # Verify installation
    message(STATUS "Verifying node installation")
    execute_process(
        COMMAND ${NODE_PYTHON} -c "import rpc; import animica.qt_wallet_bridge; import animica.wallet_qr; import omni_sdk; import core"
        RESULT_VARIABLE VERIFY_RESULT
        ERROR_VARIABLE VERIFY_ERROR
    )
    
    if(NOT VERIFY_RESULT EQUAL 0)
        message(FATAL_ERROR 
            "Node installation verification failed:\n"
            "Error: ${VERIFY_ERROR}\n"
        )
    endif()
    
    message(STATUS "Animica node built successfully")
    message(STATUS "Node Python: ${NODE_PYTHON}")
    message(STATUS "Node venv: ${NODE_VENV_DIR}")
    message(STATUS "========================================")
    message(STATUS "")
    
    # Return the Python executable path
    set(${OUT_VAR} ${NODE_PYTHON} PARENT_SCOPE)
    
    # Also set global variables for bundling
    set(ANIMICA_NODE_PYTHON ${NODE_PYTHON} PARENT_SCOPE)
    set(ANIMICA_NODE_VENV ${NODE_VENV_DIR} PARENT_SCOPE)
endfunction()
