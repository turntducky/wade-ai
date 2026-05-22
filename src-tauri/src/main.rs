#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod quickchat;
mod tray;

use std::time::Duration;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::ShellExt;

#[tauri::command]
fn update_tray(app: AppHandle, state: String) {
    tray::set_state(&app, &state);
}

#[tauri::command]
fn show_notification(app: AppHandle, title: String, body: String) {
    let _ = app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show();
}

async fn backend_ready() -> bool {
    for _ in 0..30 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if tokio::net::TcpStream::connect("127.0.0.1:8000").await.is_ok() {
            return true;
        }
    }
    false
}

fn show_main_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn fatal_dialog(app: &AppHandle, msg: &str) {
    app.dialog().message(msg).title("W.A.D.E. Error").blocking_show();
    app.exit(1);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let h = app.handle().clone();
            let qc = WebviewWindowBuilder::new(
                app,
                "quickchat",
                WebviewUrl::External(
                    "http://localhost:8000/ui#quickchat".parse().unwrap(),
                ),
            )
            .title("")
            .inner_size(400.0, 120.0)
            .resizable(false)
            .decorations(false)
            .always_on_top(true)
            .visible(false)
            .skip_taskbar(true)
            .build()?;

            let h_qc = h.clone();
            qc.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    quickchat::hide(&h_qc);
                }
            });

            let open_item = MenuItem::with_id(app, "open",      "Open W.A.D.E.", true, None::<&str>)?;
            let qc_item   = MenuItem::with_id(app, "quickchat", "Quick Chat",    true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit",      "Quit",          true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_item, &qc_item, &quit_item])?;

            TrayIconBuilder::with_id("main-tray")
                .icon(Image::from_bytes(tray::ICON_IDLE_BYTES)?)
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quickchat" => quickchat::toggle(app),
                    "quit"      => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                })
                .build(app)?;

            let h_hk = h.clone();
            if let Err(e) = app.global_shortcut().on_shortcut(
                "Ctrl+Shift+W",
                move |_app, _shortcut, event| {
                    if event.state == ShortcutState::Pressed {
                        quickchat::toggle(&h_hk);
                    }
                },
            ) {
                eprintln!("[wade] Could not register Ctrl+Shift+W: {e}");
            }

            tauri::async_runtime::spawn(async move {
                if tokio::net::TcpStream::connect("127.0.0.1:8000").await.is_ok() {
                    show_main_window(&h);
                    return;
                }

                let shell = h.shell();
                match shell
                    .command("python")
                    .args([
                        "-m", "uvicorn", "app.main:app",
                        "--host", "127.0.0.1",
                        "--port", "8000",
                        "--log-level", "error",
                    ])
                    .spawn()
                {
                    Err(e) => {
                        eprintln!("[wade] spawn failed: {e}");
                        fatal_dialog(
                            &h,
                            "W.A.D.E. backend failed to start.\n\
                             Ensure Python is on PATH and 'uvicorn' is installed.",
                        );
                    }
                    Ok((_rx, _child)) => {
                        tauri::async_runtime::spawn(async move {
                            let mut rx = _rx;
                            while rx.recv().await.is_some() {}
                        });
                        if backend_ready().await {
                            show_main_window(&h);
                        } else {
                            fatal_dialog(
                                &h,
                                "Backend did not respond within 15 seconds.\n\
                                 Ollama may still be loading a model. Try again shortly.",
                            );
                        }
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![update_tray, show_notification])
        .run(tauri::generate_context!())
        .expect("error while running W.A.D.E. desktop");
}
