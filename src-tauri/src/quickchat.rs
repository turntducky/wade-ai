use tauri::{AppHandle, Manager};

pub fn toggle(app: &AppHandle) {
    let Some(win) = app.get_webview_window("quickchat") else { return };
    if win.is_visible().unwrap_or(false) {
        let _ = win.hide();
    } else {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

pub fn hide(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("quickchat") {
        let _ = win.hide();
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn module_symbols_exist() {
        let _: fn(&tauri::AppHandle) = super::toggle;
        let _: fn(&tauri::AppHandle) = super::hide;
    }
}
