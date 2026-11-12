use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
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
async fn engine_call(
    app: tauri::AppHandle,
    state: State<'_, EngineState>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    const METHODS: &[&str] = &[
        "handshake",
        "library.list",
        "playlists.list",
        "playlists.save",
        "metadata.get",
        "metadata.reload",
        "metadata.save",
        "metadata.search",
        "metadata.cover",
        "metadata.job",
        "metadata.artwork",
        "metadata.write",
        "metadata.batch.start",
        "metadata.batch.get",
        "metadata.batch.latest",
        "metadata.batch.cancel",
        "providers.capabilities",
        "providers.resolve",
        "providers.job",
        "providers.cover",
        "acquisitions.list",
        "acquisitions.clear_completed",
        "acquisitions.enqueue",
        "acquisitions.cancel",
        "acquisitions.retry",
        "sets.capabilities",
        "sets.configure_spotify",
        "sets.configure_soundcloud",
        "sets.list",
        "sets.delete",
        "sets.get",
        "sets.scan",
        "sets.cancel_task",
        "sets.cancel_scan",
        "sets.resume_scan",
        "sets.auto_scan",
        "sets.prepare",
        "sets.add_source",
        "sets.add_local",
        "sets.pending_analysis",
        "sets.reorder",
        "sets.artwork",
        "sets.apply_recommended",
        "track.playback",
        "playback.prepare",

        "library.import",
        "imports.start",
        "imports.get",
        "imports.cancel",
        "imports.latest",
        "track.relink",
        "track.get",
        "analysis.run",
        "analysis.enqueue",
        "structure.enqueue",
        "jobs.get",
        "jobs.cancel",
        "grid.set",
        "grid.offset",
        "grid.fixed",
        "grid.restore",
        "sections.save",
        "cues.save",
        "cues.propose",
        "cues.auto",
        "export.rekordbox",
        "export.enqueue",
    ];
    if !METHODS.contains(&method.as_str()) {
        return Err("Unknown engine method".into());
    }
    let mut engine = state.0.lock().map_err(|e| e.to_string())?;
    let result = engine.request(&method, params)?;
    if method == "library.import" || method == "track.get" || method == "track.playback" || method == "track.relink" {
        if let Some(path) = result["path"].as_str() {
            app.asset_protocol_scope()
                .allow_file(path)
                .map_err(|e| e.to_string())?;
        }
        if let Some(path) = result["detail_analysis"]["cache_path"].as_str() {
            app.asset_protocol_scope()
                .allow_file(path)
                .map_err(|e| e.to_string())?;
        }
    }
    let artwork=result["artwork_path"].as_str().filter(|_|method=="metadata.get" || method=="metadata.reload").or_else(||result["metadata"]["artwork_path"].as_str()).or_else(|| result["path"].as_str().filter(|_| method=="metadata.artwork")).or_else(|| result["result"]["path"].as_str().filter(|_| method=="metadata.job" || method=="providers.job"));
    if let Some(path)=artwork { app.asset_protocol_scope().allow_file(path).map_err(|e|e.to_string())?; }
    // Artwork stays local; grant only engine-returned paths, including set reports.
    for report in [&result, &result["result"]] {
        if let Some(tracks) = report["tracks"].as_array() {
            for track in tracks {
                if let Some(path) = track["artwork_path"].as_str() {
                    app.asset_protocol_scope().allow_file(path).map_err(|e|e.to_string())?;
                }
            }
        }
        if method == "library.list" {
            if let Some(tracks) = report.as_array() {
                for track in tracks {
                    if let Some(path) = track["metadata"]["artwork_path"].as_str() {
                        app.asset_protocol_scope().allow_file(path).map_err(|e|e.to_string())?;
                    }
                }
            }
        }
    }
    Ok(result)
}

impl Drop for Engine {
    fn drop(&mut self) {
        // Let the Python host stop its worker process group before falling back to kill.
        let _ = writeln!(self.input, "{{\"protocol_version\":1,\"request_id\":\"shutdown\",\"method\":\"system.shutdown\",\"params\":{{}}}}");
        let _ = self.input.flush();
        let deadline = Instant::now() + Duration::from_secs(4);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn allowed_reference(url: &tauri::Url) -> bool {
    url.scheme() == "https"
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && matches!(url.host_str(), Some("www.youtube.com" | "music.youtube.com"
            | "www.shazam.com" | "open.spotify.com" | "spotify.link" | "soundcloud.com" | "on.soundcloud.com" | "musicbrainz.org"))
}

#[tauri::command]
fn open_reference(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|_| "Invalid source URL")?;
    if !allowed_reference(&parsed) {
        return Err("Unsupported source URL".into());
    }
    open::that_detached(parsed.as_str()).map_err(|e| e.to_string())
}

fn bundled_engine() -> Option<std::path::PathBuf> {
    let binary = std::env::current_exe().ok()?.with_file_name(
        if cfg!(windows) { "crateloom-engine.exe" } else { "crateloom-engine" }
    );
    binary.is_file().then_some(binary)
}

fn main() {
    // The override is for testing a frozen sidecar. The default remains the local development engine.
    let mut command = if let Some(executable) = std::env::var_os("CRATELOOM_ENGINE_EXEC") {
        Command::new(executable)
    } else if let Some(binary) = bundled_engine() {
        Command::new(binary)
    } else {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
        let python = std::env::var("CRATELOOM_PYTHON").unwrap_or_else(|_| "python3".into());
        let mut command = Command::new(python);
        command.args(["-m", "crateloom"]).env("PYTHONPATH", root);
        command
    };
    // Finder-launched apps omit Homebrew paths. FFmpeg is an external dependency.
    #[cfg(target_os = "macos")]
    {
        let existing = std::env::var_os("PATH").unwrap_or_default();
        let mut paths: Vec<_> = std::env::split_paths(&existing).collect();
        paths.extend([std::path::PathBuf::from("/opt/homebrew/bin"), std::path::PathBuf::from("/usr/local/bin")]);
        if let Ok(path) = std::env::join_paths(paths) {
            command.env("PATH", path);
        }
        if let Some(home) = std::env::var_os("HOME") {
            let cache = std::path::PathBuf::from(home).join("Library/Caches/CrateLoom");
            for (key, folder) in [
                ("CRATELOOM_ANALYSIS_PYTHON", "analysis-env"),
                ("CRATELOOM_STRUCTURE_PYTHON", "structure-env"),
                ("CRATELOOM_METADATA_PYTHON", "metadata-env"),
                ("CRATELOOM_SHAZAM_PYTHON", "shazam-env"),
            ] {
                let python = cache.join(folder).join("bin/python");
                if std::env::var_os(key).is_none() && python.is_file() {
                    command.env(key, python);
                }
            }
        }
    }
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
        .invoke_handler(tauri::generate_handler![engine_call, open_reference])
        .run(tauri::generate_context!())
        .expect("desktop error");
}

#[cfg(test)]
mod tests {
    #[test]
    fn reference_opener_only_accepts_https_provider_hosts() {
        for (input, expected) in [
            ("https://music.youtube.com/watch?v=uXn-zOt68V8", true),
            ("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b", true),
            ("https://soundcloud.com/forss/flickermood", true),
            ("https://soundcloud.com.example.org/track", false),
            ("https://soundcloud.com@other.example/track", false),
            ("https://user@soundcloud.com/track", false),
            ("https://soundcloud.com:8080/track", false),
            ("file:///tmp/test", false),
        ] {
            assert_eq!(super::allowed_reference(&tauri::Url::parse(input).unwrap()), expected);
        }
    }

    #[test]
    fn frontend_methods_are_exposed_by_bridge() {
        let frontend = [include_str!("../../src/main.tsx"), include_str!("../../src/MetadataEditor.tsx"),
            include_str!("../../src/DiscoveryPanel.tsx"), include_str!("../../src/MetadataBatch.tsx"),
            include_str!("../../src/SetImportPanel.tsx")].join("");
        let bridge = include_str!("main.rs");
        for method in ["cues.auto", "grid.fixed", "grid.restore", "imports.start", "imports.get", "imports.cancel", "imports.latest", "track.relink", "playlists.list", "playlists.save", "metadata.get", "metadata.reload", "metadata.save", "metadata.search", "metadata.cover", "metadata.job", "metadata.artwork", "metadata.write", "providers.capabilities", "providers.resolve", "providers.job", "providers.cover", "acquisitions.list", "acquisitions.enqueue", "acquisitions.cancel", "acquisitions.retry", "export.enqueue"] {
            assert!(frontend.contains(method));
            let quoted = format!("\"{}\"", method);
            assert!(bridge.split("if !METHODS").next().unwrap().contains(&quoted), "Missing method: {}", method);
        }
        for method in ["metadata.batch.start", "metadata.batch.get", "metadata.batch.latest", "metadata.batch.cancel", "sets.cancel_task", "sets.cancel_scan"] {
            assert!(frontend.contains(method));
            assert!(bridge.split("if !METHODS").next().unwrap().contains(&format!("\"{}\"",method)));
        }
    }
}
