use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

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

/// Bootstrap installer URL. Resolved at build time by `build.rs` to a
/// commit-hash-pinned `raw.githubusercontent.com` URL derived from
/// `git rev-parse HEAD`. Override with `TALKY_BOOTSTRAP_URL_OVERRIDE` env
/// var at build time if you need a custom URL.
const BOOTSTRAP_URL: &str = env!("TALKY_BOOTSTRAP_URL");

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

fn bootstrap_log_path() -> PathBuf {
    talky_run_dir().join("bootstrap.log")
}

/// Run the bootstrap installer in-process, piping stdout/stderr.
/// Invokes `on_stage(key, msg)` for each `::stage::<key>::<msg>` line.
/// Tees all output to `~/.talky/run/bootstrap.log`.
/// Returns Ok on exit 0, Err with a short reason otherwise.
fn run_bootstrap<F: FnMut(&str, &str)>(mut on_stage: F) -> Result<(), String> {
    let _ = fs::create_dir_all(talky_run_dir());
    let log_path = bootstrap_log_path();
    let mut log = fs::File::create(&log_path)
        .map_err(|e| format!("Cannot open bootstrap log {}: {e}", log_path.display()))?;
    let _ = writeln!(log, "# Bootstrap URL: {BOOTSTRAP_URL}");

    let cmd = format!("set -o pipefail; curl -LsSf {BOOTSTRAP_URL} | bash");
    let mut child = Command::new("bash")
        .arg("-c")
        .arg(&cmd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn bash: {e}"))?;

    let stdout = child.stdout.take().ok_or("no stdout")?;
    let stderr = child.stderr.take().ok_or("no stderr")?;

    // Drain stderr in a side thread, appended to the same log.
    let log_path_for_err = log_path.clone();
    let stderr_handle = thread::spawn(move || {
        if let Ok(mut log_err) = fs::OpenOptions::new().append(true).open(&log_path_for_err) {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = writeln!(log_err, "[stderr] {line}");
            }
        }
    });

    let reader = BufReader::new(stdout);
    for line in reader.lines().map_while(Result::ok) {
        let _ = writeln!(log, "{line}");
        if let Some(rest) = line.strip_prefix("::stage::") {
            let mut parts = rest.splitn(2, "::");
            let key = parts.next().unwrap_or("").trim();
            let msg = parts.next().unwrap_or("").trim();
            on_stage(key, msg);
        }
    }
    let _ = stderr_handle.join();

    let status = child.wait().map_err(|e| format!("wait: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "Installer exited with status {}. See log at {}",
            status,
            log_path.display()
        ))
    }
}

fn js_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('\n', "\\n")
        .replace('\r', "")
}

fn render_bootstrap_splash(win: &WebviewWindow) {
    let log = js_escape(&bootstrap_log_path().display().to_string());
    let url = js_escape(BOOTSTRAP_URL);
    let html = format!(
        "<style>body{{font-family:-apple-system,system-ui,sans-serif;padding:32px;max-width:560px;margin:auto;color:#222}} \
.spinner{{display:inline-block;width:14px;height:14px;border:2px solid #ccc;border-top-color:#333;border-radius:50%;animation:s 1s linear infinite;margin-right:10px;vertical-align:-2px}} \
@keyframes s{{to{{transform:rotate(360deg)}}}} \
#stage{{font-size:1.1em;margin:18px 0}} #url{{opacity:.5;font-size:.8em;word-break:break-all}} \
button{{padding:8px 14px;font-size:.95em;cursor:pointer}}</style> \
<h2>Installing Talky…</h2> \
<div id=\"stage\"><span class=\"spinner\"></span><span id=\"stage-msg\">Starting…</span></div> \
<p id=\"url\">From: {url}</p> \
<p id=\"log-link\" style=\"display:none\"><a href=\"#\" onclick=\"window.__openLog()\">Open install log</a></p>"
    );
    let _ = win.eval(&format!(
        "document.body.innerHTML = '{}'; window.__logPath = '{}'; \
         window.__openLog = function(){{ try {{ \
           (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
             ? window.__TAURI_INTERNALS__.invoke('open_bootstrap_log') \
             : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke('open_bootstrap_log')); \
         }} catch(e) {{}} return false; }};",
        js_escape(&html),
        log
    ));
}

fn update_bootstrap_stage(win: &WebviewWindow, key: &str, msg: &str) {
    let text = if msg.is_empty() {
        key.to_string()
    } else {
        msg.to_string()
    };
    let _ = win.eval(&format!(
        "var el=document.getElementById('stage-msg');if(el)el.textContent='{}';",
        js_escape(&text)
    ));
}

fn render_bootstrap_failure(win: &WebviewWindow, err: &str) {
    let log = bootstrap_log_path().display().to_string();
    let html = format!(
        "<style>body{{font-family:-apple-system,system-ui,sans-serif;padding:32px;max-width:560px;margin:auto;color:#222}} \
pre{{background:#f4f4f4;padding:10px;border-radius:4px;white-space:pre-wrap;font-size:.85em}} \
button{{padding:8px 14px;font-size:.95em;cursor:pointer;margin-right:8px}}</style> \
<h2>Talky install failed</h2> \
<pre>{}</pre> \
<p><button onclick=\"window.location.reload()\">Retry</button> \
<button onclick=\"window.__openLog()\">Open log</button></p> \
<p style=\"opacity:.6;font-size:.8em\">Log: <code>{}</code></p>",
        err.replace('<', "&lt;"),
        log.replace('<', "&lt;")
    );
    let _ = win.eval(&format!(
        "document.body.innerHTML = '{}'; \
         window.__openLog = function(){{ try {{ \
           (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
             ? window.__TAURI_INTERNALS__.invoke('open_bootstrap_log') \
             : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke('open_bootstrap_log')); \
         }} catch(e) {{}} return false; }};",
        js_escape(&html)
    ));
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

#[tauri::command]
fn open_bootstrap_log() -> Result<(), String> {
    let path = bootstrap_log_path();
    Command::new("open")
        .arg(&path)
        .status()
        .map_err(|e| format!("open: {e}"))?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![open_bootstrap_log])
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
                        // Show in-shell progress UI and pipe install-talky.sh through it.
                        if let Some(win) = handle.get_webview_window("main") {
                            render_bootstrap_splash(&win);
                        }
                        let win_for_stages = handle.get_webview_window("main");
                        let result = run_bootstrap(|key, msg| {
                            if let Some(ref w) = win_for_stages {
                                update_bootstrap_stage(w, key, msg);
                            }
                        });
                        match result {
                            Ok(()) => {
                                // Installer done. Try ensure_daemon_running again — it'll
                                // find the freshly installed binary and spawn the daemon.
                                match ensure_daemon_running() {
                                    StartupOutcome::DaemonReady(port) => {
                                        let url = webview_url_for(port);
                                        if let Some(win) = handle.get_webview_window("main") {
                                            let _ = win.eval(&format!(
                                                "window.location.replace('{url}');"
                                            ));
                                        }
                                    }
                                    other => {
                                        if let Some(win) = handle.get_webview_window("main") {
                                            let msg = match other {
                                                StartupOutcome::Failed(m) => m,
                                                StartupOutcome::BootstrapNeeded => {
                                                    "Installer reported success but talky binary still missing".into()
                                                }
                                                _ => "Unexpected post-install state".into(),
                                            };
                                            render_bootstrap_failure(&win, &msg);
                                        }
                                    }
                                }
                            }
                            Err(err) => {
                                if let Some(win) = handle.get_webview_window("main") {
                                    render_bootstrap_failure(&win, &err);
                                }
                            }
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
