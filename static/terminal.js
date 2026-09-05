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
    const terminalWindow = document.getElementById("terminalWindow");
    const minimizeButton = document.getElementById("minimizeTerminalButton");
    const newAIChatButton = document.getElementById("newAIChatButton");
    const leaveDialog = document.getElementById("terminalLeaveDialog");
    const leaveCloseButton = document.getElementById("terminalLeaveClose");
    const stayButton = document.getElementById("terminalStayButton");
    const openTargetButton = document.getElementById("terminalOpenTargetButton");
    const leaveButton = document.getElementById("terminalLeaveButton");
    let socket = null;
    let ready = false;
    let resizeTimer = null;
    let keepaliveTimer = null;
    let manuallyClosed = false;
    let minimized = false;
    let pendingNavigation = null;
    let allowPageUnload = false;
    let aiAvailable = false;
    let pendingAICommand = false;
    let currentLine = "";
    let lineTrackingReliable = true;
    let aiBusy = false;
    let aiTurns = 0;
    let placeholderTimer = null;
    let placeholderVisible = false;

    const placeholderText = "输入命令，或输入问题与 AI 对话…";

    function displayWidth(value) {
        return Array.from(value).reduce((width, character) => width + (character.codePointAt(0) > 255 ? 2 : 1), 0);
    }

    function clearPlaceholder() {
        window.clearTimeout(placeholderTimer);
        placeholderTimer = null;
        if (!placeholderVisible) return;
        placeholderVisible = false;
        terminal.write("\x1b[K");
    }

    function schedulePlaceholder() {
        window.clearTimeout(placeholderTimer);
        placeholderTimer = null;
        if (!ready || !aiAvailable || aiBusy || pendingAICommand || currentLine || !lineTrackingReliable) return;
        if (terminal.buffer.active.type === "alternate") return;
        placeholderTimer = window.setTimeout(() => {
            if (!ready || aiBusy || pendingAICommand || currentLine || !lineTrackingReliable) return;
            if (terminal.cols - terminal.buffer.active.cursorX < displayWidth(placeholderText)) return;
            placeholderVisible = true;
            terminal.write(`\x1b7\x1b[2m${placeholderText}\x1b[0m\x1b8`);
        }, 350);
    }

    function setStatus(state, text) {
        status.className = `terminal-status${state ? ` is-${state}` : ""}`;
        statusText.textContent = text;
        connectButton.disabled = state === "connecting" || state === "connected";
        disconnectButton.disabled = state !== "connecting" && state !== "connected";
    }

    function writeNotice(message, color) {
        clearPlaceholder();
        const safe = String(message || "").replace(/\x1b/g, "").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
        terminal.writeln(`\r\n\x1b[${color || "33"}m${safe.replace(/\n/g, "\r\n")}\x1b[0m`, schedulePlaceholder);
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
                clearPlaceholder();
                terminal.write(new Uint8Array(event.data), schedulePlaceholder);
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
                aiAvailable = Boolean(message.ai && message.ai.available);
                newAIChatButton.style.display = aiAvailable ? "flex" : "none";
                newAIChatButton.title = "开始新的 AI 对话";
                setStatus("connected", "已连接");
                fitAddon.fit();
                sendSize();
                terminal.focus();
                writeNotice(aiAvailable ? `AI 已启用：${message.ai.provider} / ${message.ai.model}` : "AI 未启用，所有输入均按 Shell 命令执行", aiAvailable ? "36" : "90");
            } else if (message.type === "ai_thinking") {
                aiBusy = true;
                pendingAICommand = false;
                writeNotice("AI 正在思考…", "36");
            } else if (message.type === "ai_reply") {
                aiBusy = false;
                aiTurns = Number(message.turns || aiTurns + 1);
                newAIChatButton.title = `开始新的 AI 对话（当前上下文 ${aiTurns} 轮）`;
                pendingAICommand = Boolean(message.command);
                writeNotice(`AI：${message.answer || ""}`, "36");
                if (message.command) {
                    writeNotice(`建议命令：${message.command}\n按 Ctrl+Enter 确认执行`, "33");
                }
            } else if (message.type === "ai_executing") {
                aiBusy = true;
                pendingAICommand = false;
                writeNotice(`正在执行 AI 建议：${message.command}`, "35");
            } else if (message.type === "ai_summary") {
                aiBusy = false;
                writeNotice(`AI 分析（退出码 ${message.exit_code}）：${message.summary}`, message.exit_code === 0 ? "32" : "33");
            } else if (message.type === "ai_error") {
                aiBusy = false;
                writeNotice(`AI：${message.message || "请求失败"}`, "31");
            } else if (message.type === "ai_chat_reset") {
                aiBusy = false;
                aiTurns = 0;
                pendingAICommand = false;
                newAIChatButton.title = "开始新的 AI 对话";
                writeNotice("已开始新的 AI 对话，Shell 会话保持不变", "36");
            } else if (message.type === "error") {
                setStatus("error", "连接异常");
                writeNotice(message.message || "终端连接异常", "31");
            }
        });
        socket.addEventListener("close", (event) => {
            window.clearInterval(keepaliveTimer);
            ready = false;
            aiBusy = false;
            clearPlaceholder();
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

    function setMinimized(value) {
        minimized = value;
        terminalWindow.classList.toggle("is-minimized", minimized);
        minimizeButton.setAttribute("aria-expanded", String(!minimized));
        minimizeButton.querySelector("span").textContent = minimized ? "展开" : "收起";
        minimizeButton.querySelector("path").setAttribute(
            "d",
            minimized ? "M7 17L17 7m0 0H9m8 0v8" : "M5 12h14"
        );
        if (!minimized) {
            window.requestAnimationFrame(() => {
                fitAddon.fit();
                sendSize();
                terminal.focus();
            });
        }
    }

    function closeLeaveDialog() {
        leaveDialog.style.display = "none";
        pendingNavigation = null;
        openTargetButton.removeAttribute("href");
        terminal.focus();
    }

    function openLeaveDialog(url) {
        pendingNavigation = url;
        openTargetButton.setAttribute("href", url);
        leaveDialog.style.display = "flex";
        stayButton.focus();
    }

    function shouldGuardNavigation(event, link) {
        if (!ready || event.defaultPrevented || event.button !== 0) return false;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
        if (link.target === "_blank" || link.hasAttribute("download")) return false;
        const target = new URL(link.href, window.location.href);
        if (target.origin !== window.location.origin) return false;
        return target.href !== window.location.href;
    }

    terminal.onData((data) => {
        if (!ready) return;
        clearPlaceholder();
        if (aiAvailable && data === "\r") {
            if (lineTrackingReliable) send({type: "submit", line: currentLine});
            else send({type: "input", data});
            if (currentLine.trim()) pendingAICommand = false;
            currentLine = "";
            lineTrackingReliable = true;
            return;
        }
        send({type: "input", data});
        if (!aiAvailable) return;
        if (data === "\x7f") {
            currentLine = currentLine.slice(0, -1);
        } else if (data === "\x15" || data === "\x03") {
            currentLine = "";
        } else if (/^[^\x00-\x1f\x7f]+$/.test(data)) {
            currentLine += data;
        } else {
            lineTrackingReliable = false;
        }
    });
    terminal.onResize(sendSize);
    terminal.attachCustomKeyEventHandler((event) => {
        if (event.type === "keydown" && event.ctrlKey && !event.shiftKey && event.key === "Enter") {
            if (ready && pendingAICommand) send({type: "execute_ai"});
            else if (ready) writeNotice("当前没有待确认的 AI 命令", "33");
            return false;
        }
        const modifier = event.ctrlKey || event.metaKey;
        if (!modifier || !event.shiftKey || event.type !== "keydown") return true;
        if (event.key.toLowerCase() === "c" && terminal.hasSelection()) {
            navigator.clipboard.writeText(terminal.getSelection()).catch(() => {});
            return false;
        }
        if (event.key.toLowerCase() === "v") {
            navigator.clipboard.readText().then((text) => {
                if (ready && text) {
                    send({type: "input", data: text});
                    if (aiAvailable && lineTrackingReliable && !/[\r\n\x00-\x1f\x7f]/.test(text)) currentLine += text;
                    else if (aiAvailable) lineTrackingReliable = false;
                }
            }).catch(() => {});
            return false;
        }
        return true;
    });

    connectButton.addEventListener("click", connect);
    disconnectButton.addEventListener("click", disconnect);
    newAIChatButton.addEventListener("click", () => {
        if (!ready || !aiAvailable || aiBusy) return;
        send({type: "new_ai_chat"});
        terminal.focus();
    });
    minimizeButton.addEventListener("click", () => setMinimized(!minimized));
    leaveCloseButton.addEventListener("click", closeLeaveDialog);
    stayButton.addEventListener("click", closeLeaveDialog);
    openTargetButton.addEventListener("click", () => {
        if (!pendingNavigation) return;
        window.setTimeout(closeLeaveDialog, 0);
    });
    leaveButton.addEventListener("click", () => {
        if (!pendingNavigation) return;
        const target = pendingNavigation;
        allowPageUnload = true;
        disconnect();
        window.location.assign(target);
    });
    leaveDialog.addEventListener("click", (event) => {
        if (event.target === leaveDialog) closeLeaveDialog();
    });
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link || !shouldGuardNavigation(event, link)) return;
        event.preventDefault();
        openLeaveDialog(link.href);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && leaveDialog.style.display !== "none") {
            closeLeaveDialog();
        }
    });
    window.addEventListener("resize", () => {
        window.clearTimeout(resizeTimer);
        if (!minimized) resizeTimer = window.setTimeout(() => fitAddon.fit(), 100);
    });
    window.addEventListener("beforeunload", (event) => {
        if (ready && !allowPageUnload) {
            event.preventDefault();
            event.returnValue = "";
            return;
        }
        if (socket) socket.close(1000, "页面关闭");
    });

    connect();
})();
