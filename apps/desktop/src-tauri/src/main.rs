use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::Mutex,
};
use tauri::{Manager, State};

struct Engine {
    child: Child,
    input: ChildStdin,
    output: BufReader<std::process::ChildStdout>,
}

impl Engine {
    fn request(
        &mut self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        let req = serde_json::json!({"protocol_version":1,"request_id":"desktop","method":method,"params":params});
        writeln!(self.input, "{}", req).map_err(|e| e.to_string())?;
        self.input.flush().map_err(|e| e.to_string())?;
        let mut line = String::new();
        if self
            .output
            .read_line(&mut line)
            .map_err(|e| e.to_string())?
            == 0
        {
            return Err("Engine stopped unexpectedly. Restart CrateLoom.".into());
        }
        let value: serde_json::Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        if value["protocol_version"] != 1 || value["request_id"] != "desktop" {
            return Err("Engine returned an incompatible protocol response.".into());
        }
        if let Some(error) = value.get("error") {
            return Err(error["message"]
                .as_str()
                .unwrap_or("Engine error")
                .to_string());
        }
        value
            .get("result")
            .cloned()
            .ok_or_else(|| "Engine response is missing a result.".into())
    }
}

struct EngineState(Mutex<Engine>);

#[tauri::command]
fn engine_call(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mut engine = state.0.lock().map_err(|e| e.to_string())?;
    let result = engine.request(&method, params)?;
    if method == "library.import" || method == "track.get" {
        if let Some(path) = result["path"].as_str() {
            app.asset_protocol_scope()
                .allow_file(path)
                .map_err(|e| e.to_string())?;
        }
    }
    Ok(result)
}

impl Drop for Engine {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn main() {
    // The override is for testing a frozen sidecar. The default remains the local development engine.
    let mut command = if let Some(executable) = std::env::var_os("CRATELOOM_ENGINE_EXEC") {
        Command::new(executable)
    } else {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
        let python = std::env::var("CRATELOOM_PYTHON").unwrap_or_else(|_| "python3".into());
        let mut command = Command::new(python);
        command.args(["-m", "crateloom"]).env("PYTHONPATH", root);
        command
    };
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("Could not start Python engine");
    let input = child.stdin.take().unwrap();
    let output = BufReader::new(child.stdout.take().unwrap());
    let mut engine = Engine {
        child,
        input,
        output,
    };
    let handshake = engine
        .request("handshake", serde_json::json!({}))
        .expect("Engine handshake failed");
    assert!(
        handshake["version"].is_string(),
        "Engine handshake lacks a version"
    );
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState(Mutex::new(engine)))
        .invoke_handler(tauri::generate_handler![engine_call])
        .run(tauri::generate_context!())
        .expect("desktop error");
}
