// Hide the console window on Windows release builds. A desktop app should not
// show a terminal behind it; in debug the console is kept so the API's output
// and any panic message remain visible.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    apollo_lib::run()
}
