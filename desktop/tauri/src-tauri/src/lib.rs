use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Bootstrap & launch flow for the Talky shell.
///
/// On startup:
///   1. Check if the daemon is already running (port runfile + ready file).
///   2. If not, find the `talky` CLI on PATH and spawn `talky daemon`.
///   3. If `talky` is missing, launch the bootstrap installer in Terminal.app
///      (or print a clear error in dev mode).
///   4. Poll for the port runfile, then open the webview at http://localhost:<port>.
///
/// Failure mode: a fallback splash page surfaces the error to the user.
const DAEMON_POLL_TIMEOUT: Duration = Duration::from_secs(120);
const DAEMON_POLL_INTERVAL: Duration = Duration::from_millis(250);

/// Bootstrap installer URL. Build-time override via `TALKY_BOOTSTRAP_URL` env
/// var (consumed by build.rs / option_env!). Defaults to `main` for shipped
/// builds; spike branches can rebuild with the var set to test end-to-end:
///
///   TALKY_BOOTSTRAP_URL='https://raw.githubusercontent.com/winstonfassett/talky/spike/distribution-parity/scripts/bootstrap/install-talky.sh' \
///       cargo tauri build --bundles app
const BOOTSTRAP_URL: &str = match option_env!("TALKY_BOOTSTRAP_URL") {
    Some(url) => url,
    None => "https://raw.githubusercontent.com/WinstonFassett/talky/main/scripts/bootstrap/install-talky.sh",
};

fn talky_run_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".talky").join("run")
}

fn read_port_runfile() -> Option<u16> {
    fs::read_to_string(talky_run_dir().join("talky-daemon.port"))
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
}

/// Loopback HTTP port — only present when the daemon's primary listener
/// is HTTPS. When present, prefer it so we can speak plain HTTP from the
/// webview without self-signed-cert friction.
fn read_loopback_port_runfile() -> Option<u16> {
    fs::read_to_string(talky_run_dir().join("talky-daemon.loopback-port"))
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
}

/// Webview URL: prefer the loopback HTTP port when TLS is on so we don't
/// hit self-signed-cert warnings inside WKWebView. When no loopback port
/// is published, the primary port is plain HTTP and we use it directly.
fn webview_url_for(port: u16) -> String {
    if let Some(loopback) = read_loopback_port_runfile() {
        format!("http://localhost:{loopback}")
    } else {
        format!("http://localhost:{port}")
    }
}

fn daemon_is_ready() -> bool {
    let ready_path = talky_run_dir().join("talky-daemon.ready");
    let Ok(content) = fs::read_to_string(&ready_path) else { return false };
    let Ok(pid) = content.trim().parse::<i32>() else { return false };
    unsafe { libc::kill(pid, 0) == 0 }
}

fn find_talky_binary() -> Option<PathBuf> {
    // Search PATH plus the canonical uv tool location.
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(path) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path) {
            candidates.push(dir.join("talky"));
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        candidates.push(PathBuf::from(&home).join(".local/share/uv/tools/talky/bin/talky"));
        candidates.push(PathBuf::from(&home).join(".local/bin/talky"));
    }
    candidates.into_iter().find(|p| p.is_file())
}

fn spawn_daemon(talky_bin: &PathBuf) -> std::io::Result<()> {
    // Spawn detached. The CLI handles backgrounding itself; we just kick it off.
    Command::new(talky_bin).arg("daemon").spawn()?;
    Ok(())
}

/// Try to launch the bootstrap installer in Terminal.app. Returns `Ok(())`
/// only when osascript exits 0 AND a Terminal window was actually told to
/// run the script. Any failure (Terminal busy, AppleEvent timeout, sandboxed
/// environment) returns an `Err` so the splash can surface a manual fallback.
fn launch_bootstrap_in_terminal() -> Result<(), String> {
    let script = format!(
        "echo 'Installing Talky...'; curl -LsSf {} | bash; echo; echo 'Press return to close.'; read",
        BOOTSTRAP_URL
    );
    // `activate` first so Terminal is foregrounded and ready to accept events.
    // Without it, a launched-but-not-running Terminal can return -1712.
    let applescript = format!(
        "tell application \"Terminal\"\nactivate\ndo script \"{}\"\nend tell",
        script.replace('\\', "\\\\").replace('"', "\\\"")
    );
    let output = Command::new("osascript")
        .arg("-e")
        .arg(&applescript)
        .output()
        .map_err(|e| format!("osascript not available: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("osascript exited with status {}", output.status)
        } else {
            stderr
        });
    }
    Ok(())
}

/// Result type for the shell's startup decision.
enum StartupOutcome {
    DaemonReady(u16),
    BootstrapNeeded,
    Failed(String),
}

fn ensure_daemon_running() -> StartupOutcome {
    // Already running? Use existing port.
    if daemon_is_ready() {
        if let Some(port) = read_port_runfile() {
            return StartupOutcome::DaemonReady(port);
        }
    }

    // Need to spawn. Find the binary first.
    let Some(talky_bin) = find_talky_binary() else {
        return StartupOutcome::BootstrapNeeded;
    };

    if let Err(e) = spawn_daemon(&talky_bin) {
        return StartupOutcome::Failed(format!("Failed to spawn talky daemon: {e}"));
    }

    // Poll for port runfile + ready signal.
    let deadline = Instant::now() + DAEMON_POLL_TIMEOUT;
    while Instant::now() < deadline {
        if daemon_is_ready() {
            if let Some(port) = read_port_runfile() {
                return StartupOutcome::DaemonReady(port);
            }
        }
        thread::sleep(DAEMON_POLL_INTERVAL);
    }
    StartupOutcome::Failed("Daemon did not become ready within 120s".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            // Run startup in a worker thread so we don't block the main runloop.
            // Show a tiny "starting..." window immediately; swap URL when ready.
            let splash_url = WebviewUrl::App("index.html".into());
            let _window = WebviewWindowBuilder::new(app, "main", splash_url)
                .title("Talky")
                .inner_size(900.0, 650.0)
                .build()?;

            let handle = app.handle().clone();
            thread::spawn(move || {
                match ensure_daemon_running() {
                    StartupOutcome::DaemonReady(port) => {
                        let url = webview_url_for(port);
                        if let Some(win) = handle.get_webview_window("main") {
                            let _ = win.eval(&format!("window.location.replace('{url}');"));
                        }
                    }
                    StartupOutcome::BootstrapNeeded => {
                        let terminal_result = launch_bootstrap_in_terminal();
                        if let Some(win) = handle.get_webview_window("main") {
                            let html = match terminal_result {
                                Ok(()) => format!(
                                    "<h1>Installing Talky…</h1>\
<p>Follow the prompts in the Terminal window that just opened.\
After install completes, relaunch this app.</p>\
<p style=\"opacity:.6;font-size:.85em\">Installer URL: <code>{}</code></p>",
                                    BOOTSTRAP_URL
                                ),
                                Err(msg) => format!(
                                    "<h1>Talky needs to be installed</h1>\
<p>We couldn't open Terminal automatically. Open Terminal yourself and run:</p>\
<pre style=\"background:#f4f4f4;padding:8px;border-radius:4px\">curl -LsSf {} | bash</pre>\
<p>Then relaunch this app.</p>\
<details><summary>Details</summary><pre>{}</pre></details>",
                                    BOOTSTRAP_URL,
                                    msg.replace('<', "&lt;")
                                ),
                            };
                            let escaped = html.replace('\\', "\\\\").replace('\'', "\\'").replace('\n', "\\n");
                            let _ = win.eval(&format!("document.body.innerHTML = '{escaped}';"));
                        }
                    }
                    StartupOutcome::Failed(msg) => {
                        if let Some(win) = handle.get_webview_window("main") {
                            let _ = win.eval(&format!(
                                "document.body.innerHTML = '<h1>Talky failed to start</h1><pre>{}</pre>';",
                                msg.replace('\'', "\\'").replace('\n', "\\n")
                            ));
                        }
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
