use tauri::WebviewUrl;
use tauri::WebviewWindowBuilder;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External("http://localhost:9090".parse().unwrap()))
                .title("Talky")
                .inner_size(900.0, 650.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
