use tauri::AppHandle;
use tauri::image::Image;

pub const ICON_IDLE_BYTES: &[u8]       = include_bytes!("../icons/tray-idle-32.png");
const ICON_THINKING_BYTES: &[u8]       = include_bytes!("../icons/tray-thinking-32.png");
const ICON_RUNNING_BYTES:  &[u8]       = include_bytes!("../icons/tray-running-32.png");
const ICON_ATTENTION_BYTES: &[u8]      = include_bytes!("../icons/tray-attention-32.png");

pub fn state_to_bytes(state: &str) -> &'static [u8] {
    match state {
        "thinking"     => ICON_THINKING_BYTES,
        "task_running" => ICON_RUNNING_BYTES,
        "attention"    => ICON_ATTENTION_BYTES,
        _              => ICON_IDLE_BYTES,
    }
}

pub fn set_state(app: &AppHandle, state: &str) {
    let Some(tray) = app.tray_by_id("main-tray") else { return };
    if let Ok(image) = Image::from_bytes(state_to_bytes(state)) {
        let _ = tray.set_icon(Some(image));
    }
}

#[cfg(test)]
mod tests {
    use super::state_to_bytes;

    #[test]
    fn known_states_map_to_nonempty_icons() {
        for state in &["idle", "thinking", "task_running", "attention"] {
            assert!(!state_to_bytes(state).is_empty(),
                    "icon bytes for state '{state}' must not be empty");
        }
    }

    #[test]
    fn unknown_state_falls_back_to_idle() {
        assert_eq!(
            state_to_bytes("whatever"),
            state_to_bytes("idle"),
            "unknown state should use idle icon"
        );
    }
}
