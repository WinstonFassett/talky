use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

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
/// How long we'll wait for the daemon's ready file once the HTTP port is up.
/// Bumped from 120s to 600s in 08d0 because a cold HF cache can take several
/// minutes to populate on a fresh install. The wait is no longer silent —
/// during this window the splash polls /api/ready and surfaces task progress.
const DAEMON_POLL_TIMEOUT: Duration = Duration::from_secs(600);
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
        format!("http://localhost:{loopback}/?autoconnect=true")
    } else {
        format!("http://localhost:{port}/?autoconnect=true")
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
    // Note: we replace document.documentElement.innerHTML below, so this
    // string carries a full <head>+<body>. Replacing only body would orphan
    // the user-agent stylesheet's default <body> properties from a prior
    // splash render, producing weird inline layouts.
    let html = format!(
        "<head><meta charset=\"utf-8\"><meta name=\"color-scheme\" content=\"light dark\"><style> \
*,*::before,*::after{{box-sizing:border-box}} \
:root{{color-scheme:light dark;--bg:#ffffff;--fg:#1a1a1a;--muted:#666;--rule:#e5e5e5;--code-bg:#f4f4f4;--spinner-track:#d0d0d0;--spinner-head:#222}} \
@media (prefers-color-scheme: dark){{:root{{--bg:#1c1c1e;--fg:#f2f2f7;--muted:#8e8e93;--rule:#3a3a3c;--code-bg:#2c2c2e;--spinner-track:#48484a;--spinner-head:#f2f2f7}}}} \
html,body{{margin:0;padding:0;background:var(--bg);color:var(--fg)}} \
body{{font-family:-apple-system,system-ui,sans-serif;line-height:1.4;min-height:100vh}} \
.wrap{{max-width:640px;margin:0 auto;padding:32px}} \
h2{{margin:0 0 16px 0;font-weight:600;font-size:1.4em}} \
.spinner{{display:inline-block;width:14px;height:14px;border:2px solid var(--spinner-track);border-top-color:var(--spinner-head);border-radius:50%;animation:s 1s linear infinite;margin-right:10px;vertical-align:-2px}} \
@keyframes s{{to{{transform:rotate(360deg)}}}} \
#stage{{font-size:1.05em;margin:18px 0;overflow-wrap:anywhere}} \
#elapsed{{color:var(--muted);font-size:.85em;font-variant-numeric:tabular-nums;margin-left:8px;white-space:nowrap}} \
#url{{color:var(--muted);font-size:.8em;overflow-wrap:anywhere;margin:8px 0 0 0}} \
details{{margin-top:20px;border-top:1px solid var(--rule);padding-top:14px}} \
summary{{cursor:pointer;color:var(--muted);font-size:.9em;user-select:none}} \
summary:hover{{color:var(--fg)}} \
#log-tail{{background:var(--code-bg);color:var(--fg);padding:10px;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.75em;line-height:1.45;max-height:260px;overflow:auto;margin-top:10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}</style></head><body> \
<div class=\"wrap\"> \
<h2>Installing Talky…</h2> \
<div id=\"stage\"><span class=\"spinner\"></span><span id=\"stage-msg\">Starting…</span><span id=\"elapsed\"></span></div> \
<p id=\"url\">From: {url}</p> \
<details id=\"details\"><summary>Show details</summary><pre id=\"log-tail\">(log not yet available)</pre></details> \
</div></body>"
    );
    // The splash sets up:
    //  - window.__openLog: opens the full log in a system editor
    //  - window.__stageStart: timestamp the current stage started, used by
    //    the elapsed-time ticker. update_bootstrap_stage() resets it.
    //  - elapsed-time ticker: updates #elapsed every 250ms with "(Xs)" or "(Xm Ys)"
    //  - log-tail poller: every 2s, when <details> is open, fetches the
    //    last 50 lines of bootstrap.log and updates #log-tail
    let _ = win.eval(&format!(
        "document.documentElement.innerHTML = '{}'; window.__logPath = '{}'; \
         var _inv = function(cmd, args){{ try {{ \
           return (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
             ? window.__TAURI_INTERNALS__.invoke(cmd, args) \
             : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke(cmd, args)); \
         }} catch(e) {{ return Promise.reject(e); }} }}; \
         window.__openLog = function(){{ _inv('open_bootstrap_log'); return false; }}; \
         window.__stageStart = Date.now(); \
         var _fmtElapsed = function(ms){{ var s = Math.floor(ms/1000); if (s < 60) return '(' + s + 's)'; \
           var m = Math.floor(s/60); var r = s - m*60; return '(' + m + 'm ' + r + 's)'; }}; \
         if (window.__elapsedTimer) clearInterval(window.__elapsedTimer); \
         window.__elapsedTimer = setInterval(function(){{ \
           var el = document.getElementById('elapsed'); \
           if (!el) return; \
           var dt = Date.now() - window.__stageStart; \
           el.textContent = ' ' + _fmtElapsed(dt); \
         }}, 250); \
         if (window.__tailTimer) clearInterval(window.__tailTimer); \
         window.__tailTimer = setInterval(function(){{ \
           var det = document.getElementById('details'); \
           if (!det || !det.open) return; \
           var p = _inv('read_bootstrap_log_tail', {{ lines: 50 }}); \
           if (p && p.then) p.then(function(text){{ \
             var pre = document.getElementById('log-tail'); \
             if (!pre) return; \
             pre.textContent = text || '(log empty)'; \
             pre.scrollTop = pre.scrollHeight; \
           }}); \
         }}, 2000);",
        js_escape(&html),
        log
    ));
}

/// Splash shown while we're waiting on the daemon's ready file but HTTP is
/// already serving. Polls /api/ready via a Tauri command and renders any
/// open readiness tasks (HF model downloads, native dep installs, etc.).
/// 08d0 — turns a silent multi-minute hang into a visible progress display.
fn render_starting_splash(win: &WebviewWindow, port: u16) {
    let html = "<head><meta charset=\"utf-8\"><meta name=\"color-scheme\" content=\"light dark\"><style> \
*,*::before,*::after{box-sizing:border-box} \
:root{color-scheme:light dark;--bg:#ffffff;--fg:#1a1a1a;--muted:#666;--rule:#e5e5e5;--code-bg:#f4f4f4;--spinner-track:#d0d0d0;--spinner-head:#222;--bar-track:#e5e5e5;--bar-fill:#0a84ff} \
@media (prefers-color-scheme: dark){:root{--bg:#1c1c1e;--fg:#f2f2f7;--muted:#8e8e93;--rule:#3a3a3c;--code-bg:#2c2c2e;--spinner-track:#48484a;--spinner-head:#f2f2f7;--bar-track:#3a3a3c;--bar-fill:#0a84ff}} \
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg)} \
body{font-family:-apple-system,system-ui,sans-serif;line-height:1.4;min-height:100vh} \
.wrap{max-width:640px;margin:0 auto;padding:32px} \
h2{margin:0 0 16px 0;font-weight:600;font-size:1.4em} \
.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--spinner-track);border-top-color:var(--spinner-head);border-radius:50%;animation:s 1s linear infinite;margin-right:10px;vertical-align:-2px} \
@keyframes s{to{transform:rotate(360deg)}} \
#stage{font-size:1.05em;margin:18px 0;overflow-wrap:anywhere} \
#elapsed{color:var(--muted);font-size:.85em;font-variant-numeric:tabular-nums;margin-left:8px;white-space:nowrap} \
.task{margin:14px 0;padding:10px 12px;border:1px solid var(--rule);border-radius:6px} \
.task-name{font-size:.95em;overflow-wrap:anywhere} \
.task-msg{color:var(--muted);font-size:.8em;margin-top:4px;overflow-wrap:anywhere} \
.bar{margin-top:8px;height:6px;background:var(--bar-track);border-radius:3px;overflow:hidden} \
.bar-fill{height:100%;background:var(--bar-fill);transition:width 200ms ease;width:0%} \
.bar-fill.indet{width:30%;animation:slide 1.4s ease-in-out infinite} \
@keyframes slide{0%{margin-left:-30%}100%{margin-left:100%}} \
#hint{color:var(--muted);font-size:.8em;margin-top:18px}</style></head><body> \
<div class=\"wrap\"> \
<h2>Starting Talky…</h2> \
<div id=\"stage\"><span class=\"spinner\"></span><span id=\"stage-msg\">Waiting for daemon…</span><span id=\"elapsed\"></span></div> \
<div id=\"tasks\"></div> \
<p id=\"hint\">First launch downloads voice models. Subsequent launches are fast.</p> \
</div></body>";
    let _ = win.eval(&format!(
        "document.documentElement.innerHTML = '{}'; \
         window.__daemonPort = {}; \
         window.__stageStart = Date.now(); \
         var _fmtElapsed = function(ms){{ var s = Math.floor(ms/1000); if (s < 60) return '(' + s + 's)'; \
           var m = Math.floor(s/60); var r = s - m*60; return '(' + m + 'm ' + r + 's)'; }}; \
         if (window.__elapsedTimer) clearInterval(window.__elapsedTimer); \
         window.__elapsedTimer = setInterval(function(){{ \
           var el = document.getElementById('elapsed'); \
           if (!el) return; \
           el.textContent = ' ' + _fmtElapsed(Date.now() - window.__stageStart); \
         }}, 250); \
         var _inv = function(cmd, args){{ try {{ \
           return (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
             ? window.__TAURI_INTERNALS__.invoke(cmd, args) \
             : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke(cmd, args)); \
         }} catch(e) {{ return Promise.reject(e); }} }}; \
         if (window.__readyTimer) clearInterval(window.__readyTimer); \
         window.__readyTimer = setInterval(function(){{ \
           var p = _inv('read_daemon_readiness', {{ port: window.__daemonPort }}); \
           if (!p || !p.then) return; \
           p.then(function(json){{ \
             var data; try {{ data = JSON.parse(json); }} catch(e) {{ return; }} \
             var tasks = (data && data.tasks) || []; \
             var stageMsg = document.getElementById('stage-msg'); \
             if (stageMsg) stageMsg.textContent = tasks.length > 0 ? 'Preparing voice models…' : 'Waiting for daemon…'; \
             var container = document.getElementById('tasks'); \
             if (!container) return; \
             container.innerHTML = ''; \
             for (var i = 0; i < tasks.length; i++) {{ \
               var t = tasks[i]; \
               var div = document.createElement('div'); div.className = 'task'; \
               var name = document.createElement('div'); name.className = 'task-name'; name.textContent = t.name || '(task)'; \
               div.appendChild(name); \
               var bar = document.createElement('div'); bar.className = 'bar'; \
               var fill = document.createElement('div'); fill.className = 'bar-fill'; \
               if (t.pct != null) {{ fill.style.width = Math.max(0, Math.min(100, t.pct)) + '%'; }} \
               else {{ fill.classList.add('indet'); }} \
               bar.appendChild(fill); div.appendChild(bar); \
               if (t.msg) {{ var m = document.createElement('div'); m.className='task-msg'; m.textContent = t.msg; div.appendChild(m); }} \
               container.appendChild(div); \
             }} \
           }}).catch(function(){{}}); \
         }}, 500);",
        js_escape(html),
        port
    ));
}

fn update_bootstrap_stage(win: &WebviewWindow, key: &str, msg: &str) {
    let text = if msg.is_empty() {
        key.to_string()
    } else {
        msg.to_string()
    };
    let _ = win.eval(&format!(
        "var el=document.getElementById('stage-msg');if(el)el.textContent='{}'; \
         window.__stageStart = Date.now(); \
         var et=document.getElementById('elapsed');if(et)et.textContent='';",
        js_escape(&text)
    ));
}

fn render_bootstrap_failure(win: &WebviewWindow, err: &str) {
    let log = bootstrap_log_path().display().to_string();
    let html = format!(
        "<head><meta charset=\"utf-8\"><meta name=\"color-scheme\" content=\"light dark\"><style> \
*,*::before,*::after{{box-sizing:border-box}} \
:root{{color-scheme:light dark;--bg:#ffffff;--fg:#1a1a1a;--muted:#666;--code-bg:#f4f4f4;--btn-bg:#f0f0f0;--btn-border:#d0d0d0}} \
@media (prefers-color-scheme: dark){{:root{{--bg:#1c1c1e;--fg:#f2f2f7;--muted:#8e8e93;--code-bg:#2c2c2e;--btn-bg:#3a3a3c;--btn-border:#48484a}}}} \
html,body{{margin:0;padding:0;background:var(--bg);color:var(--fg)}} \
body{{font-family:-apple-system,system-ui,sans-serif;line-height:1.4;min-height:100vh}} \
.wrap{{max-width:560px;margin:0 auto;padding:32px}} \
h2{{margin:0 0 16px 0;font-weight:600;font-size:1.4em}} \
pre{{background:var(--code-bg);color:var(--fg);padding:10px;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.82em;line-height:1.45;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}} \
button{{padding:8px 14px;font-size:.95em;cursor:pointer;margin-right:8px;background:var(--btn-bg);color:var(--fg);border:1px solid var(--btn-border);border-radius:6px}} \
button:hover{{filter:brightness(1.1)}} \
button:disabled{{opacity:.5;cursor:wait}} \
.log-path{{color:var(--muted);font-size:.8em}} \
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}</style></head><body> \
<div class=\"wrap\"> \
<h2>Talky install failed</h2> \
<pre>{}</pre> \
<p><button id=\"retry-btn\" onclick=\"window.__retryBootstrap(this)\">Retry</button> \
<button onclick=\"window.__openLog()\">Open log</button></p> \
<p class=\"log-path\">Log: <code>{}</code></p> \
</div></body>",
        err.replace('<', "&lt;"),
        log.replace('<', "&lt;")
    );
    let _ = win.eval(&format!(
        "document.documentElement.innerHTML = '{}'; \
         window.__openLog = function(){{ try {{ \
           (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
             ? window.__TAURI_INTERNALS__.invoke('open_bootstrap_log') \
             : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke('open_bootstrap_log')); \
         }} catch(e) {{}} return false; }}; \
         window.__retryBootstrap = function(btn){{ \
           try {{ if (btn) {{ btn.disabled = true; btn.textContent = 'Retrying…'; }} }} catch(e) {{}} \
           try {{ \
             (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) \
               ? window.__TAURI_INTERNALS__.invoke('retry_bootstrap') \
               : (window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke('retry_bootstrap')); \
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

fn ensure_daemon_running(app: Option<&AppHandle>) -> StartupOutcome {
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

    // Two-phase wait:
    //   1. Wait for the HTTP port runfile — usually a couple of seconds.
    //   2. Once the daemon is serving HTTP, swap the splash to "Starting
    //      daemon" mode and let it poll /api/ready directly (showing HF
    //      download progress etc.). We just wait for the ready file.
    let deadline = Instant::now() + DAEMON_POLL_TIMEOUT;
    let mut splash_rendered = false;
    while Instant::now() < deadline {
        if daemon_is_ready() {
            if let Some(port) = read_port_runfile() {
                return StartupOutcome::DaemonReady(port);
            }
        }
        // Once we have a port, the daemon's HTTP is up — show the
        // starting-daemon splash so the user sees download progress instead
        // of a hung index.html.
        if !splash_rendered {
            if let (Some(handle), Some(port)) = (app, read_port_runfile()) {
                if let Some(win) = handle.get_webview_window("main") {
                    render_starting_splash(&win, port);
                    splash_rendered = true;
                }
            }
        }
        thread::sleep(DAEMON_POLL_INTERVAL);
    }
    StartupOutcome::Failed(format!(
        "Daemon did not become ready within {}s",
        DAEMON_POLL_TIMEOUT.as_secs()
    ))
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

/// Return the last `lines` lines of ~/.talky/run/bootstrap.log so the
/// splash can surface install activity (dd2b — bootstrap visibility).
/// Empty string if the log doesn't exist yet.
#[tauri::command]
fn read_bootstrap_log_tail(lines: usize) -> Result<String, String> {
    let path = bootstrap_log_path();
    let content = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return Ok(String::new()),
    };
    let n = lines.max(1);
    let tail: Vec<&str> = content.lines().rev().take(n).collect();
    Ok(tail.into_iter().rev().collect::<Vec<_>>().join("\n"))
}

/// Proxy GET http://localhost:<port>/api/ready and return the raw JSON body.
/// The starting-daemon splash polls this so it can render readiness tasks
/// (HF model downloads etc.) while waiting for the ready file. Short timeout
/// because the daemon is local and any hang here would just stall the UI.
#[tauri::command]
fn read_daemon_readiness(port: u16) -> Result<String, String> {
    let url = format!("http://localhost:{port}/api/ready");
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(2))
        .build();
    let resp = agent
        .get(&url)
        .call()
        .map_err(|e| format!("readiness GET {url}: {e}"))?;
    resp.into_string()
        .map_err(|e| format!("readiness body read: {e}"))
}

/// Clear runfiles for a daemon whose pid is gone. Avoids second-spawn
/// races on retry: ensure_daemon_running(Some(&handle)) trusts the pid file, so a
/// stale one would make it think the daemon is up when it isn't.
fn clear_stale_runfiles() {
    if daemon_is_ready() {
        return;
    }
    let run_dir = talky_run_dir();
    for name in [
        "talky-daemon.pid",
        "talky-daemon.ready",
        "talky-daemon.port",
        "talky-daemon.loopback-port",
        "talky-daemon.lock",
    ] {
        let _ = fs::remove_file(run_dir.join(name));
    }
}

/// The single startup flow: ensure daemon → navigate webview, with
/// bootstrap fallback. Called from `setup()` on first launch and again
/// from the `retry_bootstrap` command after a failure.
fn run_startup(handle: AppHandle) {
    thread::spawn(move || {
        match ensure_daemon_running(Some(&handle)) {
            StartupOutcome::DaemonReady(port) => {
                let url = webview_url_for(port);
                if let Some(win) = handle.get_webview_window("main") {
                    let _ = win.eval(&format!("window.location.replace('{url}');"));
                }
            }
            StartupOutcome::BootstrapNeeded => {
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
                    Ok(()) => match ensure_daemon_running(Some(&handle)) {
                        StartupOutcome::DaemonReady(port) => {
                            let url = webview_url_for(port);
                            if let Some(win) = handle.get_webview_window("main") {
                                let _ = win.eval(&format!("window.location.replace('{url}');"));
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
                    },
                    Err(err) => {
                        if let Some(win) = handle.get_webview_window("main") {
                            render_bootstrap_failure(&win, &err);
                        }
                    }
                }
            }
            StartupOutcome::Failed(msg) => {
                if let Some(win) = handle.get_webview_window("main") {
                    render_bootstrap_failure(&win, &msg);
                }
            }
        }
    });
}

#[tauri::command]
fn retry_bootstrap(app: AppHandle) -> Result<(), String> {
    clear_stale_runfiles();
    // Show a minimal "retrying..." placeholder so the user sees immediate
    // feedback while ensure_daemon_running runs.
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.eval(
            "document.documentElement.innerHTML = '<head><meta charset=\"utf-8\"><meta name=\"color-scheme\" content=\"light dark\"><style>:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a}@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f2f2f7}}html,body{margin:0;padding:0;background:var(--bg);color:var(--fg)}body{font-family:-apple-system,system-ui,sans-serif;line-height:1.4;min-height:100vh}.wrap{max-width:560px;margin:0 auto;padding:32px}h2{margin:0 0 16px 0;font-weight:600;font-size:1.4em}</style></head><body><div class=\"wrap\"><h2>Retrying…</h2></div></body>';"
        );
    }
    run_startup(app);
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            open_bootstrap_log,
            retry_bootstrap,
            read_bootstrap_log_tail,
            read_daemon_readiness
        ])
        .setup(|app| {
            // Run startup in a worker thread so we don't block the main runloop.
            // Show a tiny "starting..." window immediately; swap URL when ready.
            let splash_url = WebviewUrl::App("index.html".into());
            let _window = WebviewWindowBuilder::new(app, "main", splash_url)
                .title("Talky")
                .inner_size(900.0, 650.0)
                .build()?;

            run_startup(app.handle().clone());

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
