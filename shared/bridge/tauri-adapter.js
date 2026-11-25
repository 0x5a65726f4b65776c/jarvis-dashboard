/**
 * Tauri Adapter — maps PlatformAdapter to Tauri invoke() commands.
 *
 * Used by macos.  All file/process/OS operations delegate to
 * Rust commands registered in src-tauri/src/main.rs.
 *
 * Sync shims (readFileSync etc.) use a pre-loaded cache populated at
 * startup by macos-bootstrap.js.  Cache misses fall through to async
 * invoke() wrapped in a synchronous-looking helper (only safe during
 * init when the event loop is available).
 */
(function () {
  const { invoke } = window.__TAURI__.core;

  // ── File cache (populated by bootstrap before modules load) ──
  const _fileCache = new Map();

  const adapter = {
    platform: "tauri",

    // ── Pre-load cache ──
    _fileCache,

    async preloadFiles(paths) {
      const results = await invoke("batch_read_files", { paths });
      for (const [path, content] of Object.entries(results)) {
        _fileCache.set(path, content);
      }
    },

    // ── File System ──
    readFile(path) {
      // Sync path: return from cache
      if (_fileCache.has(path)) return _fileCache.get(path);
      // Async fallback — should not normally be hit after preload
      console.warn("[tauri-adapter] Cache miss for:", path);
      return null;
    },

    async readFileAsync(path) {
      if (_fileCache.has(path)) return _fileCache.get(path);
      const content = await invoke("read_file", { path });
      _fileCache.set(path, content);
      return content;
    },

    async writeFile(path, content) {
      await invoke("write_file", { path, content });
      _fileCache.set(path, content);
    },

    async stat(path) {
      return await invoke("stat_file", { path });
    },

    async readdir(path) {
      return await invoke("readdir", { path });
    },

    async exists(path) {
      return await invoke("exists", { path });
    },

    async mkdir(path, recursive = false) {
      await invoke("mkdir", { path, recursive });
    },

    // ── Process ──
    spawn(program, args, opts) {
      // Returns a Node.js ChildProcess-like object.
      // Tauri streams stdout/stderr via events.
      const id = crypto.randomUUID();
      const listeners = { stdout: [], stderr: [], close: [], error: [] };
      const stdinErrors = [];
      const unlisten = [];
      let spawned = false, spawning = false, closed = false, cancelRequested = false, killRequested = false;

      // Track when spawn completes so stdin.write waits for the process to be ready
      let _spawnResolve;
      const _spawnReady = new Promise(r => { _spawnResolve = r; });

      const finishClose = (code) => {
        if (closed) return;
        closed = true;
        child.stdin.writable = false;
        _spawnResolve();
        listeners.close.forEach(cb => cb(code));
        unlisten.splice(0).forEach(u => u());
      };
      const requestKill = () => {
        if (!spawned || closed || killRequested) return;
        killRequested = true;
        // Rust owns the process group and bounded escalation for this UUID.
        invoke("kill_process", { id }).catch(err => child.emit("error", err));
      };

      const child = {
        pid: null,
        _id: id,
        // Process-level events: close, error
        on(event, cb) { (listeners[event] || []).push(cb); return child; },
        emit(event, ...args) { (listeners[event] || []).forEach(cb => cb(...args)); },
        kill() {
          cancelRequested = true;
          child.stdin.writable = false;
          if (!spawned && !spawning) finishClose(-1);
          requestKill();
          return true;
        },
        killTree() { return child.kill(); },
        // Node.js ChildProcess has stdout/stderr as stream-like objects
        stdout: {
          on(event, cb) {
            if (event === "data") listeners.stdout.push(cb);
            return child.stdout;
          },
        },
        stderr: {
          on(event, cb) {
            if (event === "data") listeners.stderr.push(cb);
            return child.stderr;
          },
        },
        stdin: {
          writable: true,
          on(event, cb) { if (event === "error") stdinErrors.push(cb); return child.stdin; },
          write(data) {
            // Wait for spawn to complete before writing to stdin
            _spawnReady.then(() => {
              if (!spawned || closed || cancelRequested) return;
              invoke("stdin_write", { id, data }).catch(e => {
                stdinErrors.forEach(cb => cb(e));
              });
            });
          },
          end() {
            child.stdin.writable = false;
            _spawnReady.then(() => {
              if (!spawned || closed || cancelRequested) return;
              invoke("stdin_close", { id }).catch(e => {
                stdinErrors.forEach(cb => cb(e));
              });
            });
          },
        },
      };

      // Subscribe before spawning so even an immediate exit has a close event.
      const subscriptions = [];
      if (window.__TAURI__.event) {
        const { listen } = window.__TAURI__.event;
        subscriptions.push(
          listen(`process-stdout-${id}`, e => { if (!closed && !cancelRequested) listeners.stdout.forEach(cb => cb(e.payload)); }),
          listen(`process-stderr-${id}`, e => { if (!closed && !cancelRequested) listeners.stderr.forEach(cb => cb(e.payload)); }),
          listen(`process-close-${id}`, e => finishClose(e.payload))
        );
      }

      const pendingWrite = window.__pendingBinaryWrite;
      if (pendingWrite) window.__pendingBinaryWrite = null;
      Promise.all(subscriptions.map(p => p.then(u => closed ? u() : unlisten.push(u))))
        .then(() => pendingWrite?.catch(() => {}))
        .then(() => {
          if (cancelRequested) { finishClose(-1); return; }
          spawning = true;
          return invoke("spawn_process", { id, program, args, opts: opts || {} }).then(pid => {
            child.pid = pid;
            spawned = true;
            if (cancelRequested) requestKill();
            _spawnResolve();
          });
        })
        .catch(err => {
          child.emit("error", err);
          finishClose(-1);
        });

      return child;
    },

    async exec(command) {
      return await invoke("exec_sync", { command });
    },

    async kill(pid) {
      await invoke("kill_process", { id: String(pid) });
    },

    // ── OS ──
    homedir() {
      return adapter._homedir || "/Users/unknown";
    },

    tmpdir() {
      return adapter._tmpdir || "/tmp";
    },

    // ── Vault ──
    vaultBasePath() {
      return adapter._vaultBasePath || "";
    },

    openNote(path) {
      const vaultName = (adapter._vaultBasePath || "").split("/").pop() || "vault";
      invoke("open_url", { url: `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(path)}` })
        .catch(() => {});
    },

    async queryRecentFiles(folder, count) {
      return await invoke("get_recent_files", { root: folder, count });
    },

    async countFiles(folder) {
      return await invoke("count_files", { folder });
    },

    async parseYamlFrontmatter(path) {
      return await invoke("parse_yaml_frontmatter", { path });
    },

    // ── UI ──
    showNotice(message, duration = 3000) {
      // Use a toast notification
      const toast = document.createElement("div");
      toast.textContent = message;
      Object.assign(toast.style, {
        position: "fixed", bottom: "24px", left: "50%", transform: "translateX(-50%)",
        background: "#1a1a2e", color: "#e0e6ed", padding: "10px 20px",
        borderRadius: "8px", border: "1px solid rgba(0,212,255,0.3)",
        fontSize: "13px", zIndex: "10000", opacity: "0",
        transition: "opacity 0.3s",
      });
      document.body.appendChild(toast);
      requestAnimationFrame(() => { toast.style.opacity = "1"; });
      setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
      }, duration);
    },
  };

  window.__tauriAdapter = adapter;
})();
