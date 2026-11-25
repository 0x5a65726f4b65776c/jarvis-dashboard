use std::collections::HashMap;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{command, AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};

struct OwnedProcess {
    child: Child,
    process_group: Option<i32>,
}

/// Keep ownership here until exit or cancellation, including after pipes close.
static PROCESSES: std::sync::LazyLock<Mutex<HashMap<String, OwnedProcess>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

/// Separate map for stdin handles — survives after child is taken for wait.
static STDIN_HANDLES: std::sync::LazyLock<Mutex<HashMap<String, ChildStdin>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

#[derive(serde::Deserialize, Default)]
pub struct SpawnOpts {
    pub cwd: Option<String>,
    pub env: Option<HashMap<String, String>>,
    pub env_remove: Option<Vec<String>>,
    pub detached: Option<bool>,
}

#[command]
pub async fn spawn_process(
    app: AppHandle,
    id: String,
    program: String,
    args: Vec<String>,
    opts: SpawnOpts,
) -> Result<u32, String> {
    let mut cmd = Command::new(&program);
    cmd.args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let Some(cwd) = &opts.cwd {
        cmd.current_dir(cwd);
    }
    if let Some(env) = &opts.env {
        for (k, v) in env {
            cmd.env(k, v);
        }
    }
    // Remove specified env vars (prevents inheriting from parent process)
    if let Some(env_remove) = &opts.env_remove {
        for k in env_remove {
            cmd.env_remove(k);
        }
    }
    // Always remove Claude session markers to prevent "nested session" errors
    cmd.env_remove("CLAUDECODE");
    cmd.env_remove("CLAUDE_CODE_ENTRYPOINT");
    cmd.env_remove("CLAUDE_CODE_MAX_OUTPUT_TOKENS");

    #[cfg(unix)]
    if opts.detached == Some(true) {
        cmd.process_group(0);
    }

    let mut child = cmd.spawn().map_err(|e| format!("spawn({}): {}", program, e))?;
    let pid = child.id().unwrap_or(0);
    let process_group = if cfg!(unix) && opts.detached == Some(true) && pid > 0 {
        Some(pid as i32)
    } else {
        None
    };

    // Take stdout/stderr/stdin before storing child
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let stdin = child.stdin.take();

    // Store stdin separately so it survives the wait task taking the child
    if let Some(stdin) = stdin {
        let mut handles = STDIN_HANDLES.lock().unwrap();
        handles.insert(id.clone(), stdin);
    }

    // Store child (without stdin) for kill
    {
        let mut procs = PROCESSES.lock().unwrap();
        procs.insert(id.clone(), OwnedProcess { child, process_group });
    }

    // Use a shared counter to detect when both stdout+stderr are done
    let done_count = std::sync::Arc::new(std::sync::atomic::AtomicU8::new(0));
    let total_streams = (stdout.is_some() as u8) + (stderr.is_some() as u8);

    // Stream stdout
    if let Some(out) = stdout {
        let app_clone = app.clone();
        let id_clone = id.clone();
        let done = done_count.clone();
        let id_for_wait = id.clone();
        let app_for_wait = app.clone();
        tokio::spawn(async move {
            let reader = BufReader::new(out);
            let mut lines = reader.lines();
            while let Ok(Some(line)) = lines.next_line().await {
                // Append \n so JS NDJSON parser can split lines
                let _ = app_clone.emit(
                    &format!("process-stdout-{}", id_clone),
                    format!("{}\n", line),
                );
            }
            // Check if both streams done → emit close
            let prev = done.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if prev + 1 >= total_streams {
                emit_close(&app_for_wait, &id_for_wait).await;
            }
        });
    }

    // Stream stderr
    if let Some(err) = stderr {
        let app_clone = app.clone();
        let id_clone = id.clone();
        let done = done_count.clone();
        let id_for_wait = id.clone();
        let app_for_wait = app.clone();
        tokio::spawn(async move {
            let reader = BufReader::new(err);
            let mut lines = reader.lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let _ = app_clone.emit(
                    &format!("process-stderr-{}", id_clone),
                    format!("{}\n", line),
                );
            }
            let prev = done.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if prev + 1 >= total_streams {
                emit_close(&app_for_wait, &id_for_wait).await;
            }
        });
    }

    // If no streams at all, emit close immediately
    if total_streams == 0 {
        let app_clone = app.clone();
        let id_clone = id.clone();
        tokio::spawn(async move {
            emit_close(&app_clone, &id_clone).await;
        });
    }

    Ok(pid)
}

/// Wait for the process to exit and emit the close event.
async fn emit_close(app: &AppHandle, id: &str) {
    // Closing stdout/stderr does not prove exit. Leave the child registered so
    // cancellation remains available while waiting; never hold a lock across await.
    let code = loop {
        let code = {
            let mut procs = PROCESSES.lock().unwrap();
            match procs.get_mut(id) {
                Some(owned) => match owned.child.try_wait() {
                    Ok(None) => None,
                    result => {
                        let code = result.ok().flatten().and_then(|s| s.code()).unwrap_or(-1);
                        procs.remove(id);
                        STDIN_HANDLES.lock().unwrap().remove(id);
                        Some(code)
                    }
                },
                // A cancelling owner removed the child. Do not report exit zero.
                None => Some(-1),
            }
        };
        if let Some(code) = code { break code; }
        tokio::time::sleep(Duration::from_millis(25)).await;
    };
    let _ = app.emit(&format!("process-close-{}", id), code);
}

fn signal_group(process_group: Option<i32>, signal: i32) -> Result<(), String> {
    #[cfg(unix)]
    if let Some(pgid) = process_group.filter(|pgid| *pgid > 0) {
        // Only groups created by this spawn registry are accepted; no client PID.
        let result = unsafe { libc::kill(-pgid, signal) };
        if result != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("signal process group: {}", error));
            }
        }
    }
    Ok(())
}

#[command]
pub async fn kill_process(id: String) -> Result<(), String> {
    let maybe_owned = {
        let mut procs = PROCESSES.lock().unwrap();
        STDIN_HANDLES.lock().unwrap().remove(&id);
        procs.remove(&id)
    };
    if let Some(mut owned) = maybe_owned {
        if owned.process_group.is_some() {
            let term_result = signal_group(owned.process_group, libc::SIGTERM);
            let exited = tokio::time::timeout(Duration::from_secs(2), owned.child.wait()).await;
            // A leader's exit can leave descendants alive. Finish this owned
            // group immediately; do not retain a delayed raw PID kill afterward.
            let kill_result = signal_group(owned.process_group, libc::SIGKILL);
            if !matches!(exited, Ok(Ok(_))) {
                owned.child.kill().await.map_err(|e| format!("kill: {}", e))?;
            }
            term_result?;
            kill_result?;
        } else {
            owned.child.kill().await.map_err(|e| format!("kill: {}", e))?;
        }
    }
    Ok(())
}

#[command]
pub async fn stdin_write(id: String, data: String) -> Result<(), String> {
    // Take stdin out temporarily to avoid holding Mutex across await
    let maybe_stdin = {
        let mut handles = STDIN_HANDLES.lock().unwrap();
        handles.remove(&id)
    };
    if let Some(mut stdin) = maybe_stdin {
        let bytes = data.into_bytes();
        let result = stdin.write_all(&bytes).await;
        // Cancellation may have removed this process while the write awaited.
        // Use the same lock order as kill_process; do not resurrect its stdin.
        let procs = PROCESSES.lock().unwrap();
        if procs.contains_key(&id) {
            STDIN_HANDLES.lock().unwrap().insert(id, stdin);
        }
        result.map_err(|e| format!("stdin_write: {}", e))?;
    } else {
        return Err(format!("stdin_write: no stdin handle for process {}", id));
    }
    Ok(())
}

#[command]
pub async fn stdin_close(id: String) -> Result<(), String> {
    let mut handles = STDIN_HANDLES.lock().unwrap();
    handles.remove(&id); // Drop stdin to close it
    Ok(())
}

/// Transcribe audio: writes raw audio blob to temp file, converts to 16kHz WAV via ffmpeg,
/// then runs whisper-cli and returns the transcription text.
#[command]
pub async fn transcribe_audio(
    audio_data: Vec<u8>,
    whisper_path: String,
    whisper_model: String,
    language: String,
) -> Result<String, String> {
    use tokio::process::Command as TokioCommand;

    let tmp_dir = std::env::temp_dir();
    let raw_path = tmp_dir.join("jarvis-voice-raw.mp4");
    let wav_path = tmp_dir.join("jarvis-voice-capture.wav");

    // 1. Write raw audio blob to disk
    std::fs::write(&raw_path, &audio_data)
        .map_err(|e| format!("write raw audio: {}", e))?;

    // 2. Convert to 16kHz mono WAV using ffmpeg
    let ffmpeg = if std::path::Path::new("/opt/homebrew/bin/ffmpeg").exists() {
        "/opt/homebrew/bin/ffmpeg"
    } else if std::path::Path::new("/usr/local/bin/ffmpeg").exists() {
        "/usr/local/bin/ffmpeg"
    } else {
        "ffmpeg"
    };

    let ffmpeg_out = TokioCommand::new(ffmpeg)
        .args([
            "-y", "-i", raw_path.to_str().unwrap(),
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            wav_path.to_str().unwrap(),
        ])
        .output()
        .await
        .map_err(|e| format!("ffmpeg: {}", e))?;

    if !ffmpeg_out.status.success() {
        let stderr = String::from_utf8_lossy(&ffmpeg_out.stderr);
        let _ = std::fs::remove_file(&raw_path);
        return Err(format!("ffmpeg failed: {}", stderr));
    }
    let _ = std::fs::remove_file(&raw_path);

    // 3. Run whisper-cli
    let whisper_out = TokioCommand::new(&whisper_path)
        .args([
            "-m", &whisper_model,
            "-f", wav_path.to_str().unwrap(),
            "--no-timestamps",
            "-l", &language,
        ])
        .output()
        .await
        .map_err(|e| format!("whisper: {}", e))?;

    let _ = std::fs::remove_file(&wav_path);

    let stdout = String::from_utf8_lossy(&whisper_out.stdout).to_string();
    let stderr = String::from_utf8_lossy(&whisper_out.stderr).to_string();

    // Parse text — remove timestamp prefixes like [00:00:00.000 --> 00:00:05.000]
    let text = stdout
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .map(|l| {
            // Strip [timestamp] prefix
            if l.starts_with('[') {
                if let Some(idx) = l.find(']') {
                    l[idx + 1..].trim()
                } else {
                    l
                }
            } else {
                l
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_string();

    // Parse detected language from stderr
    let detected_lang = if language == "auto" {
        stderr
            .lines()
            .find_map(|l| {
                if let Some(idx) = l.find("auto-detected language:") {
                    Some(l[idx + 23..].trim().to_string())
                } else {
                    None
                }
            })
    } else {
        None
    };

    // Return as JSON object
    let result = serde_json::json!({
        "text": text,
        "detectedLang": detected_lang,
    });

    Ok(result.to_string())
}

#[command]
pub fn exec_sync(command: String) -> Result<String, String> {
    let output = std::process::Command::new("sh")
        .args(["-c", &command])
        .output()
        .map_err(|e| format!("exec: {}", e))?;
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

#[command]
pub async fn open_url(url: String) -> Result<(), String> {
    std::process::Command::new("open")
        .arg(&url)
        .spawn()
        .map_err(|e| format!("open_url: {}", e))?;
    Ok(())
}

#[command]
pub async fn open_app(name: String) -> Result<(), String> {
    std::process::Command::new("open")
        .args(["-a", &name])
        .spawn()
        .map_err(|e| format!("open_app: {}", e))?;
    Ok(())
}

#[cfg(all(test, unix))]
mod cancellation_tests {
    use super::*;

    #[tokio::test]
    async fn cancellation_stops_descendants_even_when_the_leader_exits_first_or_ignores_term() {
        for ignore_leader in [false, true] {
            let id = uuid::Uuid::new_v4().to_string();
            let dir = std::env::temp_dir().join(format!("jarvis-native-cancel-{}", id));
            std::fs::create_dir(&dir).unwrap();
            let ready = dir.join("ready");
            let marker = dir.join("effect");
            let mut cmd = Command::new("sh");
            cmd.args([
                "-c",
                "if [ \"$3\" = yes ]; then trap '' TERM; fi; sh -c 'trap \"\" TERM; printf ready > \"$1\"; sleep 3; printf residual > \"$2\"' child \"$1\" \"$2\" & wait",
                "fixture",
            ])
                .arg(&ready).arg(&marker).arg(if ignore_leader { "yes" } else { "no" })
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
                .process_group(0);
            let child = cmd.spawn().unwrap();
            let pid = child.id().unwrap() as i32;
            PROCESSES.lock().unwrap().insert(id.clone(), OwnedProcess { child, process_group: Some(pid) });
            for _ in 0..100 {
                if ready.exists() { break; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            let started = ready.exists();
            let cancelled_at = std::time::Instant::now();
            kill_process(id.clone()).await.unwrap();
            // Cross the side-effect time even when the grace period was used.
            let remaining = Duration::from_millis(3200).saturating_sub(cancelled_at.elapsed());
            tokio::time::sleep(remaining).await;
            let residual = marker.exists();
            std::fs::remove_dir_all(&dir).unwrap();
            assert!(started, "fixture descendant must be ready before cancellation");
            assert!(!residual, "cancelled native process group left a running descendant");
            assert!(!PROCESSES.lock().unwrap().contains_key(&id));
        }
    }

    #[tokio::test]
    async fn uncancelled_process_group_can_complete_normally() {
        let mut cmd = Command::new("sh");
        cmd.args(["-c", "exit 0"]).process_group(0);
        assert!(cmd.spawn().unwrap().wait().await.unwrap().success());
    }
}
