// Animica Explorer — optional commands (open URL, clipboard)
// These are intentionally "optional":
// - URL opening uses the `tauri-plugin-shell` when the `shell-open` feature is enabled,
//   otherwise it emits an event the renderer can handle.
// - Clipboard uses `tauri-plugin-clipboard-manager` when the `clipboard` feature is enabled,
//   otherwise falls back to an event (or returns an error on read).

use std::env;
use std::str::FromStr;

use tauri::{AppHandle, Emitter, Url};

#[cfg(feature = "shell-open")]
use tauri_plugin_shell::ShellExt;

#[cfg(feature = "clipboard")]
use tauri_plugin_clipboard_manager::ClipboardManagerExt;

/// Parse a URL string and validate against a conservative allowlist.
fn parse_and_validate_url(raw: &str) -> Result<Url, String> {
  let url = Url::from_str(raw).map_err(|e| format!("invalid url: {e}"))?;

  // Always allow our custom deep-link scheme.
  if url.scheme() == "animica" {
    return Ok(url);
  }

  // Allow local app:// / file:// only when running in offline mode.
  if matches!(url.scheme(), "app" | "file") {
    let mode = env::var("EXPLORER_MODE").unwrap_or_else(|_| "offline".into());
    if mode.eq_ignore_ascii_case("offline") {
      return Ok(url);
    } else {
      return Err("app:// and file:// are only allowed in offline mode".into());
    }
  }

  // Only http(s) beyond this point.
  if !matches!(url.scheme(), "https" | "http") {
    return Err(format!("scheme not allowed: {}", url.scheme()));
  }

  // Build host allowlist (comma-separated env), with sensible defaults.
  let mut allowed: Vec<String> = env::var("EXPLORER_ALLOWED_HOSTS")
    .unwrap_or_else(|_| "explorer.animica.dev,rpc.animica.dev".into())
    .split(',')
    .filter(|s| !s.trim().is_empty())
    .map(|s| s.trim().to_ascii_lowercase())
    .collect();

  // Also allow localhost during development.
  if cfg!(debug_assertions) {
    allowed.push("localhost".into());
    allowed.push("127.0.0.1".into());
    allowed.push("::1".into());
  }

  let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
  if !allowed.iter().any(|h| h == &host) {
    return Err(format!(
      "host not in allowlist: {host} (allowed: {})",
      allowed.join(",")
    ));
  }

  Ok(url)
}

/// Open a URL in the user's default handler.
///
/// If the `shell-open` feature (and plugin) is enabled, we call OS open directly;
/// otherwise we emit a `system:open` event the renderer can choose to handle.
#[tauri::command]
pub fn open_external(app: AppHandle, raw_url: String) -> Result<(), String> {
  let url = parse_and_validate_url(&raw_url)?;

  #[cfg(feature = "shell-open")]
  {
    // Use the shell plugin (recommended).
    app
      .shell()
      .open(url, None)
      .map_err(|e| format!("failed to open url: {e}"))?;
    return Ok(());
  }

  #[cfg(not(feature = "shell-open"))]
  {
    // Fallback: let the UI decide what to do with this (e.g., window.open).
    app
      .emit("system:open", url.to_string())
      .map_err(|e| format!("emit failed: {e}"))?;
    Ok(())
  }
}

/// Write text to the clipboard.
///
/// With `clipboard` feature, uses the clipboard plugin; otherwise, emits `clipboard:copy`
/// so the renderer can copy using the Web Clipboard API.
#[tauri::command]
pub fn clipboard_write(app: AppHandle, text: String) -> Result<(), String> {
  #[cfg(feature = "clipboard")]
  {
    app
      .clipboard_manager()
      .write_text(text.clone())
      .map_err(|e| format!("clipboard write failed: {e}"))?;
    return Ok(());
  }

  #[cfg(not(feature = "clipboard"))]
  {
    app
      .emit("clipboard:copy", text)
      .map_err(|e| format!("emit failed: {e}"))?;
    Ok(())
  }
}

/// Read text from the clipboard.
///
/// Only available when the `clipboard` feature is enabled; otherwise returns an error to the caller.
#[tauri::command]
pub fn clipboard_read(app: AppHandle) -> Result<String, String> {
  #[cfg(feature = "clipboard")]
  {
    return app
      .clipboard_manager()
      .read_text()
      .map_err(|e| format!("clipboard read failed: {e}"));
  }

  #[cfg(not(feature = "clipboard"))]
  {
    Err("clipboard read unavailable (build without `clipboard` feature)".into())
  }
}

/// Utility: add these commands to your `invoke_handler!` list:
///   tauri::generate_handler![app_version, open_external, clipboard_write, clipboard_read]
///
/// (Where `app_version` is defined in `main.rs`.)
