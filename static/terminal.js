(function () {
    "use strict";

    const container = document.getElementById("terminal");
    if (!container || typeof Terminal === "undefined" || typeof FitAddon === "undefined") return;

    const terminal = new Terminal({
        allowProposedApi: false,
        convertEol: false,
        cursorBlink: true,
        cursorStyle: "bar",
        fontFamily: "SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
        fontSize: 14,
        lineHeight: 1.2,
        scrollback: 5000,
        theme: {
            background: "#0f141b",
            foreground: "#d8dee9",
            cursor: "#f3f4f6",
            cursorAccent: "#0f141b",
            selectionBackground: "#3b4a5f",
            black: "#1f2937",
            red: "#ef4444",
            green: "#22c55e",
            yellow: "#f59e0b",
            blue: "#60a5fa",
            magenta: "#c084fc",
            cyan: "#22d3ee",
            white: "#e5e7eb"
        }
    });
    const fitAddon = new FitAddon.FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(container);
    fitAddon.fit();

    const connectButton = document.getElementById("connectButton");
    const disconnectButton = document.getElementById("disconnectButton");
    const status = document.getElementById("terminalStatus");
    const statusText = document.getElementById("terminalStatusText");
    let socket = null;
    let ready = false;
    let resizeTimer = null;
    let keepaliveTimer = null;
    let manuallyClosed = false;

    function setStatus(state, text) {
        status.className = `terminal-status${state ? ` is-${state}` : ""}`;
        statusText.textContent = text;
        connectButton.disabled = state === "connecting" || state === "connected";
        disconnectButton.disabled = state !== "connecting" && state !== "connected";
    }

    function writeNotice(message, color) {
        terminal.writeln(`\r\n\x1b[${color || "33"}m${message}\x1b[0m`);
    }

    function send(payload) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(payload));
        }
    }

    function sendSize() {
        if (!ready) return;
        send({type: "resize", cols: terminal.cols, rows: terminal.rows});
    }

    function connect() {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
        manuallyClosed = false;
        ready = false;
        setStatus("connecting", "连接中");
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${protocol}//${window.location.host}/ws/terminal`);
        socket.binaryType = "arraybuffer";

        socket.addEventListener("open", () => {
            keepaliveTimer = window.setInterval(() => send({type: "ping"}), 25000);
        });
        socket.addEventListener("message", (event) => {
            if (event.data instanceof ArrayBuffer) {
                terminal.write(new Uint8Array(event.data));
                return;
            }
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (_) {
                return;
            }
            if (message.type === "ready") {
                ready = true;
                setStatus("connected", "已连接");
                fitAddon.fit();
                sendSize();
                terminal.focus();
            } else if (message.type === "error") {
                setStatus("error", "连接异常");
                writeNotice(message.message || "终端连接异常", "31");
            }
        });
        socket.addEventListener("close", (event) => {
            window.clearInterval(keepaliveTimer);
            ready = false;
            socket = null;
            if (!manuallyClosed) {
                setStatus(event.code === 1000 ? "" : "error", "已断开");
                if (event.code !== 1000) writeNotice(event.reason || "终端连接已断开", "31");
            } else {
                setStatus("", "已断开");
            }
        });
        socket.addEventListener("error", () => {
            setStatus("error", "连接失败");
        });
    }

    function disconnect() {
        manuallyClosed = true;
        ready = false;
        if (socket) socket.close(1000, "用户断开连接");
        setStatus("", "已断开");
    }

    terminal.onData((data) => {
        if (ready) send({type: "input", data});
    });
    terminal.onResize(sendSize);
    terminal.attachCustomKeyEventHandler((event) => {
        const modifier = event.ctrlKey || event.metaKey;
        if (!modifier || !event.shiftKey || event.type !== "keydown") return true;
        if (event.key.toLowerCase() === "c" && terminal.hasSelection()) {
            navigator.clipboard.writeText(terminal.getSelection()).catch(() => {});
            return false;
        }
        if (event.key.toLowerCase() === "v") {
            navigator.clipboard.readText().then((text) => {
                if (ready && text) send({type: "input", data: text});
            }).catch(() => {});
            return false;
        }
        return true;
    });

    connectButton.addEventListener("click", connect);
    disconnectButton.addEventListener("click", disconnect);
    window.addEventListener("resize", () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => fitAddon.fit(), 100);
    });
    window.addEventListener("beforeunload", () => {
        if (socket) socket.close(1000, "页面关闭");
    });

    connect();
})();
