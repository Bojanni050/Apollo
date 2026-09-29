//! Desktop shell for Apollo.
//!
//! The application is a Python/FastAPI backend plus a React frontend. This
//! crate is only the window and the process supervisor: it starts the
//! repository's own virtualenv Python, waits for the API to answer, points the
//! webview at it, and shuts the child down when the window closes.
//!
//! Why the window loads a URL rather than bundling the frontend
//! ------------------------------------------------------------
//! `frontendDist` is still declared in tauri.conf.json so `tauri build`
//! produces a self-contained bundle, but the window is navigated to the local
//! server at runtime. Loading the page from the server itself means the
//! webview and the API share one origin, exactly as they do behind the Vite
//! dev proxy. That avoids three separate problems:
//!
//!   * no CORS configuration, and no wildcard-origin relaxation,
//!   * no `tauri://` asset protocol or CSP gymnastics for API calls, and
//!   * the session cookie (HttpOnly) is an ordinary same-origin cookie.
//!
//! Why the child process is started with std::process
//! ---------------------------------------------------
//! Tauri can spawn sidecars through `tauri-plugin-shell`, but that plugin
//! requires granting the frontend `shell:allow-execute` -- the ability to run
//! arbitrary commands from page script. This app does not need that: the
//! process is launched once, from Rust, with a fixed argument list. Spawning
//! it directly means the webview has no process-spawning permission at all,
//! which is a meaningfully smaller attack surface if the frontend is ever
//! compromised.

use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
use windows_sys::Win32::Foundation::CloseHandle;
#[cfg(windows)]
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
#[cfg(windows)]
use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

use serde::Serialize;
use tauri::{Manager, WindowEvent};

/// How long to wait for the API to become healthy before giving up.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

/// How often to poll `/api/health` while starting.
const POLL_INTERVAL: Duration = Duration::from_millis(300);

/// A Win32 handle that is safe to move between threads.
///
/// `windows_sys::…::HANDLE` is a raw `*mut c_void`, which is neither `Send` nor
/// `Sync`, and Tauri's managed state requires both. The handle is only ever
/// closed by the OS when this process exits, and the operations on it (create,
/// assign, close) are all safe to perform from any thread, so asserting that
/// here is sound.
#[cfg(windows)]
#[derive(Clone, Copy)]
#[allow(dead_code)] // The handle is never read; holding it is the entire point.
struct JobHandle(windows_sys::Win32::Foundation::HANDLE);

// SAFETY: see the type's documentation. The handle is an opaque kernel object
// owned by this process; the operations performed on it are thread-safe and it
// carries no thread-affine state.
#[cfg(windows)]
unsafe impl Send for JobHandle {}
#[cfg(windows)]
unsafe impl Sync for JobHandle {}

/// A child process that is killed when this value is dropped.
///
/// Wrapping the `Child` means the server cannot outlive the app even on a path
/// that skips the window event -- a panic, or a forced termination of this
/// process. On Windows the process is additionally placed in a Job Object
/// configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so the operating system
/// terminates the whole tree (the Python launcher *and* the interpreter it
/// spawns) when the handle closes, rather than relying on this code running at
/// all.
struct ServerProcess {
    inner: Mutex<Option<Child>>,
    /// Held only to keep the job object alive; closing it kills the tree.
    #[cfg(windows)]
    #[allow(dead_code)]
    job: JobHandle,
}

impl ServerProcess {
    fn new(child: Child) -> Self {
        #[cfg(windows)]
        let job = JobHandle(attach_to_kill_on_close_job(&child));
        Self {
            inner: Mutex::new(Some(child)),
            #[cfg(windows)]
            job,
        }
    }

    /// Terminate the server now and reap it, so it does not linger as a zombie.
    fn stop(&self) {
        if let Ok(mut slot) = self.inner.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for ServerProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Put `child` in a job that kills its whole tree when this process exits.
#[cfg(windows)]
fn attach_to_kill_on_close_job(
    child: &Child,
) -> windows_sys::Win32::Foundation::HANDLE {
    // A null name means an unnamed job object.
    let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if job.is_null() {
        return job;
    }

    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    unsafe {
        windows_sys::Win32::System::JobObjects::SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
    }

    let process = unsafe { OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, child.id()) };
    if !process.is_null() {
        unsafe {
            AssignProcessToJobObject(job, process);
            CloseHandle(process);
        }
    }
    job
}

/// Reported to the frontend so it can show the real port, or explain a failure.
#[derive(Serialize, Clone)]
struct ServerStatus {
    url: String,
    error: Option<String>,
}

/// Repository root: `src-tauri/` lives directly beneath it.
fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is `<repo>/src-tauri`; its parent is the repo root.
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri always has a parent directory")
        .to_path_buf()
}

/// Path to the virtualenv interpreter.
///
/// Returns an error naming the fix rather than falling back to a global
/// Python: running the app against packages the operator did not choose is
/// exactly the drift the virtualenv exists to prevent.
fn python_executable() -> Result<PathBuf, String> {
    let root = repo_root();
    let relative = if cfg!(windows) {
        PathBuf::from(".venv").join("Scripts").join("python.exe")
    } else {
        PathBuf::from(".venv").join("bin").join("python")
    };
    let path = root.join(&relative);

    if !path.exists() {
        return Err(format!(
            "No virtualenv found at {}.\n\nRun scripts\\setup.ps1 first (or: python -m venv .venv).",
            path.display()
        ));
    }
    Ok(path)
}

/// Ask the OS for a free TCP port.
///
/// Binding to port 0 and reading the assigned port back cannot be wrong. An
/// explicit bind to a preferred port would need care: on Windows a socket
/// with SO_REUSEADDR can bind an address another process is actively listening
/// on, so such a check can report a busy port as free.
fn find_free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

/// Whether the server is answering on `port`.
///
/// A real HTTP request, not a socket connect: the socket is bound before the
/// application has finished starting, so a successful connect would prove only
/// that something is listening.
///
/// Both paths are checked. `/api/health` proves the application started, and `/`
/// proves the frontend bundle is actually mounted -- checking only the API
/// would report success for a server that then serves a 404 page to the window.
fn is_ready(port: u16) -> bool {
    let api_ok = ureq::get(&format!("http://127.0.0.1:{port}/api/health"))
        .timeout(Duration::from_secs(2))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false);

    if !api_ok {
        return false;
    }

    ureq::get(&format!("http://127.0.0.1:{port}/"))
        .timeout(Duration::from_secs(2))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false)
}

/// Start the API and wait until it is healthy.
///
/// Returns the child process (so the caller can keep ownership and kill it on
/// exit) and the URL it is serving.
fn start_server() -> Result<(Child, String), String> {
    let python = python_executable()?;
    let port = find_free_port().map_err(|e| format!("Could not find a free port: {e}"))?;
    let url = format!("http://127.0.0.1:{port}");

    // The working directory must be backend/ so that "app.main:app" imports and
    // the relative .env path in the settings resolves to backend/.env.
    let backend_dir = repo_root().join("backend");

    // The API's output goes to a log file, not to an inherited console.
    //
    // CREATE_NO_WINDOW is deliberate: the shell is a windows_subsystem binary
    // with no console of its own, and a child that wants stdio may be given one
    // by Windows, which shows up as an unexplained terminal beside the app.
    // The cost of that guarantee is that the backend's stderr no longer prints
    // where a reader might see it, so it goes to a file and the tail of it is
    // quoted in the failure message -- which is where it is actually useful,
    // because that message is shown in the app's own window.
    let log_path = std::env::temp_dir().join("apollo-backend.log");
    let stderr = match std::fs::File::create(&log_path) {
        Ok(file) => Stdio::from(file),
        // No log file is not a reason to refuse to start; the backend simply
        // gets no stderr, and a failure then says so.
        Err(_) => Stdio::null(),
    };

    let mut command = Command::new(&python);
    command
        .current_dir(&backend_dir)
        .arg("-m")
        // `app.serve` mounts the built frontend on the same port as the API, so
        // the webview and the API share one origin. Plain `uvicorn app.main:app`
        // would serve /api only, and the window would get a 404 for the page
        // itself.
        .arg("app.serve")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        // No --reload: this is a shipped app, and a reloader would spawn a
        // second process this crate does not own, so it would outlive the
        // window and keep holding the port.
        .stdout(Stdio::null())
        .stderr(stderr);

    // Windows-only. A no-op elsewhere, where a console is not created anyway.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(windows_sys::Win32::System::Threading::CREATE_NO_WINDOW);
    }

    let mut child = command
        .spawn()
        .map_err(|e| format!("Could not start the API ({python:?}): {e}"))?;

    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if is_ready(port) {
            return Ok((child, url));
        }
        // If it already exited there is no point waiting out the timeout.
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!(
                "The server exited immediately ({status}).\n\n\
                 If it mentions a missing frontend build, run:  npm --prefix frontend run build\n\
                 Otherwise check backend/.env -- APP_ENV must be \"development\" for local use.\n\n{}",
                backend_log_tail(&log_path)
            ));
        }
        std::thread::sleep(POLL_INTERVAL);
    }

    let _ = child.kill();
    Err(format!(
        "The server did not respond within {} seconds.\n\n\
         See backend/.env. The last thing it said:\n\n{}",
        STARTUP_TIMEOUT.as_secs(),
        backend_log_tail(&log_path)
    ))
}

/// The last lines the API wrote, for a failure message.
///
/// The backend's output goes to a file rather than to a console, so this is the
/// only place its reason can be shown. An empty result says so explicitly: a
/// blank section would read as "no output" when the real answer is "no log
/// file", which is a different thing to go and look for.
fn backend_log_tail(path: &Path) -> String {
    let Ok(text) = std::fs::read_to_string(path) else {
        return format!("(no API log at {})", path.display());
    };
    let lines: Vec<&str> = text.lines().rev().take(20).collect();
    if lines.is_empty() {
        return "(the API wrote nothing to its log)".to_string();
    }
    let mut out = lines.into_iter().rev().collect::<Vec<_>>().join("\n");
    out.truncate(2000);
    format!("--- {} ---\n{}", path.display(), out)
}

/// Show a short message in the window itself, for failures that happen before
/// the app has loaded anything.
fn show_startup_error(app: &tauri::AppHandle, message: &str) {
    use tauri::WebviewWindowBuilder;

    // A failed window may already exist; creating another with the same label
    // would panic, so ignore that case.
    if let Some(existing) = app.get_webview_window("main") {
        let _ = existing.set_title("Apollo — failed to start");
        return;
    }

    let escaped = message
        .replace('\\', "\\\\")
        .replace('"', "&quot;")
        .replace('\n', "<br>");

    let _ = WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::App("index.html".into()))
        .title("Apollo — failed to start")
        .inner_size(900.0, 500.0)
        .build();

    if let Some(window) = app.get_webview_window("main") {
        let script = format!(
            "document.addEventListener('DOMContentLoaded', () => {{ \
               document.body.innerHTML = '<div style=\"font:14px system-ui;padding:40px;color:#dfe3ea;background:#14161a\">\
               <h2 style=\"color:#d97a7a;margin-top:0\">Could not start Apollo</h2>\
               <pre style=\"white-space:pre-wrap;color:#8b93a1\">{escaped}</pre></div>'; }});"
        );
        let _ = window.eval(&script);
    }
}

/// Build and run the desktop application.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let handle = app.handle().clone();

            match start_server() {
                Ok((child, url)) => {
                    // Own the child so it is killed on exit. Without this the
                    // API would outlive the window and keep holding its port.
                    app.manage(ServerProcess::new(child));
                    handle.manage(ServerStatus {
                        url: url.clone(),
                        error: None,
                    });

                    // Navigate the window to the running server. Done here
                    // rather than in tauri.conf.json because the port is only
                    // known at runtime.
                    if let Some(window) = app.get_webview_window("main") {
                        if let Err(error) = window.navigate(tauri::Url::parse(&url).map_err(
                            |e| format!("Invalid server URL {url}: {e}"),
                        )?) {
                            show_startup_error(&handle, &format!("Could not open {url}: {error}"));
                        }
                    } else {
                        show_startup_error(&handle, "The main window was not created.");
                    }
                }
                Err(message) => show_startup_error(&handle, &message),
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Stop the API when the main window is destroyed, so quitting never
            // leaves an orphaned server holding a port. ServerProcess::drop
            // covers the remaining paths (panic, forced exit).
            if let WindowEvent::Destroyed = event {
                if window.label() == "main" {
                    if let Some(state) = window.app_handle().try_state::<ServerProcess>() {
                        state.stop();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the Apollo desktop app");
}

