fn main() {
    println!("cargo:rerun-if-env-changed=TALKY_BOOTSTRAP_URL");
    tauri_build::build()
}
