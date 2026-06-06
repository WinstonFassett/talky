use std::process::Command;

fn main() {
    let url = bootstrap_url();
    println!("cargo:rustc-env=TALKY_BOOTSTRAP_URL={url}");
    println!("cargo:rerun-if-env-changed=TALKY_BOOTSTRAP_URL_OVERRIDE");
    println!("cargo:rerun-if-changed=../../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../../.git/refs/heads");
    tauri_build::build()
}

fn bootstrap_url() -> String {
    if let Ok(override_url) = std::env::var("TALKY_BOOTSTRAP_URL_OVERRIDE") {
        if !override_url.is_empty() {
            return override_url;
        }
    }
    let hash = git_head_hash().unwrap_or_else(|| "main".to_string());
    format!(
        "https://raw.githubusercontent.com/WinstonFassett/talky/{hash}/scripts/bootstrap/install-talky.sh"
    )
}

fn git_head_hash() -> Option<String> {
    let out = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    if s.is_empty() { None } else { Some(s) }
}
