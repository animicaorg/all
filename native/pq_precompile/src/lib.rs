// Minimal native precompile prototype for PQ verification.
// This crate exposes a single C ABI function `pq_verify` that verifies a signature
// for a named PQ scheme. When compiled with the `with-oqs` feature, it links against
// the `oqs` crate (liboqs). Otherwise, it returns an error code.

use std::ffi::{CStr, c_void};
use std::os::raw::{c_uchar, c_int, c_char};

/// Verify using native library (C ABI). Kept for runtime linkage.
#[no_mangle]
pub extern "C" fn pq_verify(
    pubkey_ptr: *const c_uchar,
    pubkey_len: usize,
    msg_ptr: *const c_uchar,
    msg_len: usize,
    sig_ptr: *const c_uchar,
    sig_len: usize,
    scheme_ptr: *const c_char,
) -> c_int {
    // Return codes:
    //  1 -> verified
    //  0 -> not verified
    // -1 -> precompile not available / error

    if pubkey_ptr.is_null() || msg_ptr.is_null() || sig_ptr.is_null() || scheme_ptr.is_null() {
        return -1;
    }

    unsafe {
        let pubkey = std::slice::from_raw_parts(pubkey_ptr, pubkey_len);
        let msg = std::slice::from_raw_parts(msg_ptr, msg_len);
        let sig = std::slice::from_raw_parts(sig_ptr, sig_len);
        let scheme_c = CStr::from_ptr(scheme_ptr);
        let scheme = match scheme_c.to_str() {
            Ok(s) => s,
            Err(_) => return -1,
        };

        // If compiled with oqs feature, use it
        #[cfg(feature = "with-oqs")]
        {
            match oqs::sig::Sig::new(scheme) {
                Ok(verifier) => {
                    // oqs Rust binding's verify method signature may vary; use the provided API
                    match verifier.verify(msg, sig, pubkey) {
                        Ok(true) => return 1,
                        Ok(false) => return 0,
                        Err(_) => return 0,
                    }
                }
                Err(_) => return -1,
            }
        }

        // Fallback: precompile not available
        #[cfg(not(feature = "with-oqs"))]
        {
            return -1;
        }
    }
}


/// Rust-friendly verification helper used by the benchmark binary.
/// Returns Ok(true) if verified, Ok(false) if not verified, Err(()) on error/not-available.
pub fn verify_rust(pubkey: &[u8], msg: &[u8], sig: &[u8], scheme: &str) -> Result<bool, ()> {
    #[cfg(feature = "with-oqs")]
    {
        match oqs::sig::Sig::new(scheme) {
            Ok(verifier) => match verifier.verify(msg, sig, pubkey) {
                Ok(b) => Ok(b),
                Err(_) => Ok(false),
            },
            Err(_) => Err(()),
        }
    }

    #[cfg(not(feature = "with-oqs"))]
    {
        Err(())
    }
}
