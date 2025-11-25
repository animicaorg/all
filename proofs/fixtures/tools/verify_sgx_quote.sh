#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-$HOME/animica}"
QUOTE="${1:-$ROOT/proofs/fixtures/sgx_quote.bin}"
[[ -f "$QUOTE" ]] || { echo "Usage: $0 /path/to/sgx_quote.bin"; exit 1; }

# A tiny C verifier compiled on-the-fly against libsgx_dcap_quoteverify
WORK="$(mktemp -d)"
cat > "$WORK/verify.c" <<'C'
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <sgx_ql_lib_common.h>
#include <sgx_dcap_quoteverify.h>

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "quote path required\n"); return 2; }
  const char* path = argv[1];
  FILE* f = fopen(path, "rb");
  if (!f) { perror("fopen"); return 3; }
  fseek(f, 0, SEEK_END);
  long sz = ftell(f);
  fseek(f, 0, SEEK_SET);
  unsigned char* buf = (unsigned char*)malloc(sz);
  fread(buf, 1, sz, f);
  fclose(f);

  quote3_error_t ret;
  sgx_ql_qv_result_t verdict = SGX_QL_QV_RESULT_UNSPECIFIED;
  time_t now = time(NULL);
  ret = sgx_qv_set_enclave_load_policy(SGX_QL_DEFAULT);
  if (ret != SGX_QL_SUCCESS) { printf("set policy ret=%d\n", ret); return 4; }

  ret = sgx_qv_verify_quote(buf, (uint32_t)sz, NULL, now, NULL, NULL, &verdict, 0, NULL);
  printf("[libdcap] verify ret=%d, verdict=%d\n", ret, verdict);
  if (ret == SGX_QL_SUCCESS) {
    if (verdict == SGX_QL_QV_RESULT_OK) puts("[OK] Quote is valid for current TCB.");
    else puts("[WARN] Quote parsed but verdict not OK (out-of-date or config needed).");
  }
  free(buf);
  return 0;
}
C
cc -O2 "$WORK/verify.c" -o "$WORK/verify" -lsgx_dcap_quoteverify || {
  echo "[-] Failed to link against libsgx_dcap_quoteverify. Install libsgx-dcap-quote-verify-dev."
  exit 1;
}
"$WORK/verify" "$QUOTE"
rm -rf "$WORK"
