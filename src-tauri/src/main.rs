#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    io::{Read, Write},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use uuid::Uuid;

const BACKEND_HOST: &str = "127.0.0.1";
const STARTUP_TIMEOUT: Duration = Duration::from_secs(30);

const FETCH_BRIDGE: &str = r#"
(() => {
  const backendOrigin = "__BACKEND_ORIGIN__";
  const sessionToken = "__SESSION_TOKEN__";
  const nativeFetch = window.fetch.bind(window);

  window.fetch = (input, init = {}) => {
    const sourceUrl = input instanceof Request
      ? input.url
      : input instanceof URL
        ? input.href
        : String(input);

    try {
      const parsed = new URL(sourceUrl, window.location.href);
      const isApiRequest = parsed.pathname.startsWith('/api/');
      const belongsToApp = parsed.origin === window.location.origin;
      const belongsToBackend = parsed.origin === backendOrigin;
      const isRelative = sourceUrl.startsWith('/');

      if (isApiRequest && (belongsToApp || belongsToBackend || isRelative)) {
        const targetUrl = `${backendOrigin}${parsed.pathname}${parsed.search}${parsed.hash}`;
        const headers = new Headers(input instanceof Request ? input.headers : undefined);
        new Headers(init.headers ?? undefined).forEach((value, key) => headers.set(key, value));
        headers.set('X-CaptionNest-Session', sessionToken);

        const requestInput = input instanceof Request
          ? new Request(targetUrl, input)
          : targetUrl;
        return nativeFetch(requestInput, { ...init, headers });
      }
    } catch (_) {
      // Keep normal fetch semantics for malformed or unrelated URLs.
    }

    return nativeFetch(input, init);
  };
})();
"#;

#[derive(Default)]
struct SidecarState {
    child: Mutex<Option<CommandChild>>,
}

fn reserve_local_port() -> Result<u16, Box<dyn std::error::Error>> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 0);
    let listener = TcpListener::bind(address)?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn wait_for_backend(port: u16, session_token: &str) -> Result<(), Box<dyn std::error::Error>> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let started = Instant::now();
    while started.elapsed() < STARTUP_TIMEOUT {
        if let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
            let request = format!(
                "GET /api/health HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nX-CaptionNest-Session: {session_token}\r\nConnection: close\r\n\r\n"
            );
            let mut response = [0_u8; 64];
            if stream.write_all(request.as_bytes()).is_ok() {
                if let Ok(bytes_read) = stream.read(&mut response) {
                    let status_line = String::from_utf8_lossy(&response[..bytes_read]);
                    if status_line.starts_with("HTTP/1.1 200")
                        || status_line.starts_with("HTTP/1.0 200")
                    {
                        return Ok(());
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!("本地服务未能在 {} 秒内启动", STARTUP_TIMEOUT.as_secs()).into())
}

fn stop_sidecar(app: &AppHandle) {
    let state = app.state::<SidecarState>();
    if let Ok(mut child) = state.child.lock() {
        if let Some(child) = child.take() {
            let _ = child.kill();
        }
    };
}

fn start_desktop(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let app_data_dir = app.path().app_local_data_dir()?;
    fs::create_dir_all(&app_data_dir)?;

    let port = reserve_local_port()?;
    let session_token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let backend_origin = format!("http://{BACKEND_HOST}:{port}");
    let args = vec![
        "--host".to_string(),
        BACKEND_HOST.to_string(),
        "--port".to_string(),
        port.to_string(),
        "--data-dir".to_string(),
        app_data_dir.to_string_lossy().into_owned(),
    ];

    let (mut events, child) = app
        .shell()
        .sidecar("captionnest-sidecar")?
        .args(args)
        .env("CAPTIONNEST_SESSION_TOKEN", &session_token)
        .current_dir(&app_data_dir)
        .spawn()?;

    app.manage(SidecarState {
        child: Mutex::new(Some(child)),
    });

    tauri::async_runtime::spawn(async move {
        while events.recv().await.is_some() {
            // Draining the channel prevents a verbose sidecar from blocking on a full pipe.
            // Output is deliberately not persisted because it may contain user file paths.
        }
    });

    if let Err(error) = wait_for_backend(port, &session_token) {
        stop_sidecar(app.handle());
        return Err(error);
    }

    let initialization_script = FETCH_BRIDGE
        .replace("__BACKEND_ORIGIN__", &backend_origin)
        .replace("__SESSION_TOKEN__", &session_token);

    let window_result =
        WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
            .title("CaptionNest")
            .inner_size(1280.0, 820.0)
            .min_inner_size(760.0, 560.0)
            .resizable(true)
            .initialization_script(initialization_script)
            .build();
    if let Err(error) = window_result {
        stop_sidecar(app.handle());
        return Err(error.into());
    }

    Ok(())
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_sidecar(window.app_handle());
            }
        })
        .setup(|app| {
            if let Err(error) = start_desktop(app) {
                app.dialog()
                    .message(format!("本地服务启动失败：{error}"))
                    .title("CaptionNest 无法启动")
                    .kind(MessageDialogKind::Error)
                    .blocking_show();
                return Err(error);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("无法启动 CaptionNest 桌面应用");

    application.run(|app, event| match event {
        RunEvent::ExitRequested { .. } | RunEvent::Exit => stop_sidecar(app),
        _ => {}
    });
}
