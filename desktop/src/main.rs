// TSS Cam desktop app: the window, tray and installer around the receiver.
//
// The receiver itself (phone link, live view, game hooks) is the Python program in ../receiver, started here as a
// child process with --no-window; it serves its page on 127.0.0.1:47850 and this window shows it. The child sits in
// a kill-on-close job object, so it dies with the app even if the app crashes (Backtrack does the same for ffmpeg).
// Closing the window hides it to the tray: the receiver keeps running while you film. Quit is in the tray menu.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const UI_PORT: u16 = 47850;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct Receiver(Mutex<Option<Child>>);

/// Where the receiver's Python package lives: TSSCAM_RECEIVER, next to the app (installed), or the repo (dev).
fn receiver_dir(app: &AppHandle) -> PathBuf {
    if let Ok(d) = std::env::var("TSSCAM_RECEIVER") {
        return PathBuf::from(d);
    }
    if let Ok(res) = app.path().resource_dir() {
        let d = res.join("receiver");
        if d.join("tsscam").exists() {
            return d;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("receiver")
}

/// A kill-on-close job object holding the child: when this process ends, Windows ends the receiver too.
fn tie_to_app(child: &Child) {
    use windows_sys::Win32::System::JobObjects::*;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        AssignProcessToJobObject(job, child.as_raw_handle() as _);
        // the job handle stays open for the app's whole life on purpose: closing it is what kills the child
    }
}

fn start_receiver(app: &AppHandle) -> std::io::Result<Child> {
    let python = std::env::var("TSSCAM_PYTHON").unwrap_or_else(|_| "python".into());
    let child = Command::new(python)
        .args(["-m", "tsscam", "--no-window", "--ui-port", &UI_PORT.to_string()])
        .current_dir(receiver_dir(app))
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()?;
    tie_to_app(&child);
    Ok(child)
}

fn wait_ready(timeout: Duration) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], UI_PORT));
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn stop_receiver(app: &AppHandle) {
    if let Some(state) = app.try_state::<Receiver>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .setup(|app| {
            let handle = app.handle().clone();
            let child = start_receiver(&handle);
            let started = child.is_ok();
            app.manage(Receiver(Mutex::new(child.ok())));

            let win = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("TSS Cam")
                .inner_size(1040.0, 720.0)
                .min_inner_size(820.0, 600.0)
                .build()?;

            let show = MenuItem::with_id(app, "show", "Show TSS Cam", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().cloned().expect("app icon"))
                .tooltip("TSS Cam")
                .menu(&Menu::with_items(app, &[&show, &quit])?)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, e| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = e {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            std::thread::spawn(move || {
                let ok = started && wait_ready(Duration::from_secs(20));
                if ok {
                    if let Ok(url) = format!("http://127.0.0.1:{UI_PORT}/").parse() {
                        let _ = win.navigate(url);
                    }
                } else {
                    let msg = if started { "The receiver didn't start in time." } else { "Couldn't start the receiver: is Python installed?" };
                    let _ = win.eval(&format!(
                        "document.body.dataset.state='error';document.getElementById('msg').textContent={msg:?}"
                    ));
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close(); // keep filming: the window goes to the tray
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("TSS Cam failed to start");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_receiver(handle);
        }
    });
}
