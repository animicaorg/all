#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{env, str::FromStr};

use tauri::{
  App, AppHandle, Emitter, Manager, Result as TauriResult, Url, WebviewUrl, WebviewWindowBuilder,
};

/// Expose app version to the renderer (handy for About/help UI).
#[tauri::command]
fn app_version() -> String {
  env!("CARGO_PKG_VERSION").to_string()
}

fn is_true(v: &str) -> bool {
  matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

/// Decide how to load the UI:
/// - OFFLINE: bundled `dist` assets (index.html) packaged by Tauri.
/// - URL: external remote URL (with optional offline fallback if misconfigured).
fn resolve_start_url() -> WebviewUrl {
  let mode = env::var("EXPLORER_MODE").unwrap_or_else(|_| "offline".into());
  if mode.eq_ignore_ascii_case("url") {
    if let Ok(raw) = env::var("EXPLORER_URL") {
      if let Ok(u) = Url::from_str(&raw) {
        return WebviewUrl::External(u);
      }
      eprintln!(
        "[explorer] EXPLORER_URL='{}' is not a valid URL; falling back to offline bundle.",
        raw
      );
    } else {
      eprintln!("[explorer] EXPLORER_URL not set in url mode; falling back to offline bundle.");
    }
  }
  // Default: load bundled assets (index.html in distDir).
  WebviewUrl::App("index.html".into())
}

/// Create (or re-create) the main window and point it to the chosen start URL.
fn create_main_window(app: &App) -> TauriResult<()> {
  let start = resolve_start_url();

  // Basic window options tuned for a desktop explorer.
  let win = WebviewWindowBuilder::new(app, "main", start)
    .title("Animica Explorer")
    .inner_size(1200.0, 780.0)
    .min_inner_size(960.0, 600.0)
    .resizable(true)
    .visible(true)
    .build()?;

  // Optionally open devtools in debug builds if requested.
  if cfg!(debug_assertions) && is_true(&env::var("EXPLORER_DEVTOOLS").unwrap_or_default()) {
    let _ = win.open_devtools();
  }

  Ok(())
}

fn focus_main(app: &AppHandle) {
  if let Some(w) = app.get_webview_window("main") {
    let _ = w.show();
    let _ = w.set_focus();
  }
}

/// If the shell is started a second time (e.g. via a deep link), focus the
/// existing window and forward the intent (argv) to the renderer.
fn handle_second_instance(app: &AppHandle, argv: &[String]) {
  focus_main(app);

  // Forward the first animica:// deep link (if any) to the UI.
  if let Some(link) = argv.iter().find(|a| a.starts_with("animica://")) {
    let _ = app.emit("deeplink", link.to_string());
  }
}

fn main() {
  tauri::Builder::default()
    .invoke_handler(tauri::generate_handler![app_version])
    .setup(|app| {
      // Create the main window at startup.
      create_main_window(app)?;

      // Optional: print runtime mode to stdout for diagnostics.
      let mode = env::var("EXPLORER_MODE").unwrap_or_else(|_| "offline".into());
      let url_dbg = match resolve_start_url() {
        WebviewUrl::External(u) => u.to_string(),
        WebviewUrl::App(p) => format!("app://{p}"),
      };
      println!("[explorer] mode={mode} start_url={url_dbg}");

      Ok(())
    })
    // Single-instance behavior: focus existing window and pass any deep link.
    // This handler is built-in to Tauri and does not require an external plugin.
    .on_second_instance(|app, argv, _cwd| handle_second_instance(app, &argv))
    .on_window_event(|ev| {
      // Example policy tweaks (add more as needed).
      if let tauri::WindowEvent::CloseRequested { api, .. } = ev.event() {
        // Allow users to truly quit with Cmd/Ctrl+Q or File → Quit;
        // otherwise, on Close button, just hide to keep background tasks (if any).
        // For a pure viewer app, we can simply allow close; set to hide if desired.
        if is_true(&env::var("EXPLORER_HIDE_ON_CLOSE").unwrap_or_default()) {
          ev.window().hide().ok();
          api.prevent_close();
        }
      }
    })
    .run(tauri::generate_context!())
    .expect("error while running Animica Explorer");
}
