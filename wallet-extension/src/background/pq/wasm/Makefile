# Makefile for compiling Dilithium3 C reference implementation to WASM
#
# Requirements:
#   - Emscripten SDK (emsdk) installed and activated
#   - Run: source /path/to/emsdk/emsdk_env.sh
#
# Usage:
#   make          # Build both dilithium3.wasm and sphincs_shake_128s.wasm
#   make clean    # Remove build artifacts

CC = emcc
CFLAGS = -O3 -s WASM=1 -s EXPORTED_RUNTIME_METHODS='["ccall","cwrap"]' \
         -s ALLOW_MEMORY_GROWTH=1 -s MODULARIZE=1 -s EXPORT_ES6=1 \
         -s ENVIRONMENT='web,worker' -s MALLOC='emmalloc' \
         --no-entry

.PHONY: all clean

all: dilithium3.wasm sphincs_shake_128s.wasm

dilithium3.wasm: dilithium3.c
	$(CC) $(CFLAGS) -o dilithium3.mjs dilithium3.c
	@echo "Built dilithium3.wasm successfully"
	@echo "Note: Generated dilithium3.mjs and dilithium3.wasm"

sphincs_shake_128s.wasm: sphincs_shake_128s.c
	$(CC) $(CFLAGS) -o sphincs_shake_128s.mjs sphincs_shake_128s.c
	@echo "Built sphincs_shake_128s.wasm successfully"

clean:
	rm -f *.wasm *.mjs *.js
	@echo "Cleaned build artifacts"

# Help target
help:
	@echo "Dilithium3 WASM Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all     - Build both dilithium3.wasm and sphincs_shake_128s.wasm (default)"
	@echo "  clean   - Remove build artifacts"
	@echo "  help    - Show this help message"
	@echo ""
	@echo "Requirements:"
	@echo "  - Emscripten SDK must be installed and activated"
	@echo "  - Run: source /path/to/emsdk/emsdk_env.sh"
	@echo ""
	@echo "Example:"
	@echo "  $$ source ~/emsdk/emsdk_env.sh"
	@echo "  $$ make"
