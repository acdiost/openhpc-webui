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
    const aiModelBar = document.getElementById("terminalAIModelBar");
    const aiModelInput = document.getElementById("terminalAIModelInput");
    const aiModelOptions = document.getElementById("terminalAIModelOptions");
    const aiModelApply = document.getElementById("terminalAIModelApply");
    const aiProvider = document.getElementById("terminalAIProvider");
    const aiConfigButton = document.getElementById("terminalAIConfigButton");
    const aiConfigDialog = document.getElementById("terminalAIConfigDialog");
    const aiConfigClose = document.getElementById("terminalAIConfigClose");
    const aiConfigCancel = document.getElementById("terminalAIConfigCancel");
    const aiConfigForm = document.getElementById("terminalAIConfigForm");
    const aiEnabledInput = document.getElementById("terminal_user_ai_enabled");
    const aiProviderInput = document.getElementById("terminal_user_ai_provider");
    const aiBaseURLInput = document.getElementById("terminal_user_ai_base_url");
    const aiConfigModelInput = document.getElementById("terminal_user_ai_model");
    const aiAPIKeyInput = document.getElementById("terminal_user_ai_api_key");
    const aiClearKeyInput = document.getElementById("terminal_user_ai_clear_key");
    const aiTimeoutInput = document.getElementById("terminal_user_ai_timeout");
    const aiKeyStatus = document.getElementById("terminalUserAIKeyStatus");
    const aiConfigStatus = document.getElementById("terminalAIConfigStatus");
    const aiPersistInput = document.getElementById("terminal_user_ai_persist");
    const aiPersistHelp = document.getElementById("terminalUserAIPersistHelp");
    const stepLimitControl = document.getElementById("terminalStepLimitControl");
    const stepLimitInput = document.getElementById("terminalStepLimit");
    const riskMaxSteps = document.getElementById("terminalRiskMaxSteps");
    const autoApproveControl = document.getElementById("terminalAutoApproveControl");
    const autoApproveCheckbox = document.getElementById("terminalAutoApprove");
    const autoApproveDialog = document.getElementById("terminalAutoApproveDialog");
    const autoApproveClose = document.getElementById("terminalAutoApproveClose");
    const autoApproveCancel = document.getElementById("terminalAutoApproveCancel");
    const autoApproveConfirm = document.getElementById("terminalAutoApproveConfirm");
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
    let autoApprove = false;
    let maxSteps = 10;
    let placeholderTimer = null;
    let placeholderVisible = false;
    let bracketedPasteBuffer = null;
    let currentAIConfig = {};
    let currentUsername = "";
    let persistedAIConfig = null;
    let pendingAIConfigPersistence = null;
    let restoringPersistedAIConfig = false;

    const placeholderText = "输入命令，或输入问题与 AI 对话…";
    const pasteStart = "\x1b[200~";
    const pasteEnd = "\x1b[201~";
    const providerDefaults = {
        deepseek: "https://api.deepseek.com",
        vllm: "http://127.0.0.1:8000/v1",
        sglang: "http://127.0.0.1:30000/v1",
        "openai-compatible": ""
    };
    const aiConfigDatabaseName = "openhpc-terminal-ai";
    const aiConfigStoreName = "encrypted-configs";

    function encryptedStorageAvailable() {
        return Boolean(window.isSecureContext && window.crypto && window.crypto.subtle && window.indexedDB);
    }

    function openAIConfigDatabase() {
        return new Promise((resolve, reject) => {
            const request = window.indexedDB.open(aiConfigDatabaseName, 1);
            request.onupgradeneeded = () => {
                const database = request.result;
                if (!database.objectStoreNames.contains(aiConfigStoreName)) {
                    database.createObjectStore(aiConfigStoreName);
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error("无法打开浏览器配置存储"));
        });
    }

    async function accessStoredAIConfig(mode, action) {
        const database = await openAIConfigDatabase();
        try {
            return await new Promise((resolve, reject) => {
                const transaction = database.transaction(aiConfigStoreName, mode);
                const request = action(transaction.objectStore(aiConfigStoreName));
                let result;
                request.onsuccess = () => { result = request.result; };
                request.onerror = () => reject(request.error || new Error("浏览器配置存储失败"));
                transaction.oncomplete = () => resolve(result);
                transaction.onabort = () => reject(transaction.error || new Error("浏览器配置事务失败"));
            });
        } finally {
            database.close();
        }
    }

    async function saveEncryptedAIConfig(config) {
        if (!encryptedStorageAvailable() || !currentUsername) {
            throw new Error("当前浏览器环境不支持安全的本地加密存储");
        }
        const key = await window.crypto.subtle.generateKey(
            {name: "AES-GCM", length: 256},
            false,
            ["encrypt", "decrypt"]
        );
        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        const plaintext = new TextEncoder().encode(JSON.stringify(config));
        const ciphertext = await window.crypto.subtle.encrypt(
            {name: "AES-GCM", iv}, key, plaintext
        );
        await accessStoredAIConfig("readwrite", (store) => store.put({
            version: 1,
            algorithm: "AES-GCM",
            key,
            iv,
            ciphertext,
        }, currentUsername));
        persistedAIConfig = Object.assign({}, config);
    }

    async function loadEncryptedAIConfig() {
        if (!encryptedStorageAvailable() || !currentUsername) return null;
        const record = await accessStoredAIConfig(
            "readonly", (store) => store.get(currentUsername)
        );
        if (!record) return null;
        if (record.version !== 1 || record.algorithm !== "AES-GCM") {
            throw new Error("浏览器中的 AI 配置版本不受支持");
        }
        const plaintext = await window.crypto.subtle.decrypt(
            {name: "AES-GCM", iv: new Uint8Array(record.iv)},
            record.key,
            record.ciphertext
        );
        const config = JSON.parse(new TextDecoder().decode(plaintext));
        if (
            !config || typeof config !== "object"
            || !Object.prototype.hasOwnProperty.call(providerDefaults, config.provider)
            || typeof config.base_url !== "string"
            || config.base_url.length > 2048
            || typeof config.model !== "string"
            || config.model.length > 256
            || typeof config.api_key !== "string"
            || config.api_key.length > 4096
            || !Number.isFinite(Number(config.timeout_seconds))
        ) {
            throw new Error("浏览器中的 AI 配置无效");
        }
        return config;
    }

    async function deleteEncryptedAIConfig() {
        if (!window.indexedDB || !currentUsername) return;
        await accessStoredAIConfig(
            "readwrite", (store) => store.delete(currentUsername)
        );
        persistedAIConfig = null;
    }

    async function restoreEncryptedAIConfig() {
        if (!encryptedStorageAvailable()) return;
        restoringPersistedAIConfig = true;
        try {
            const config = await loadEncryptedAIConfig();
            persistedAIConfig = config;
            if (!config || !ready) {
                restoringPersistedAIConfig = false;
                return;
            }
            send({
                type: "set_ai_config",
                enabled: config.enabled === true,
                provider: config.provider,
                base_url: config.base_url,
                model: config.model,
                api_key: config.api_key || null,
                clear_api_key: config.clear_api_key === true,
                timeout_seconds: Number(config.timeout_seconds || 60)
            });
            writeNotice("正在恢复此浏览器保存的 AI 配置…", "36");
        } catch (_) {
            restoringPersistedAIConfig = false;
            await deleteEncryptedAIConfig().catch(() => {});
            writeNotice("浏览器中保存的 AI 配置无法解密，已清除，请重新配置", "33");
        }
    }

    function displayWidth(value) {
        return Array.from(value).reduce((width, character) => width + (character.codePointAt(0) > 255 ? 2 : 1), 0);
    }

    function setModelChoices(models) {
        aiModelOptions.replaceChildren();
        for (const model of models || []) {
            if (!model) continue;
            const option = document.createElement("option");
            option.value = String(model);
            aiModelOptions.appendChild(option);
        }
    }

    function syncAIConfig(config) {
        currentAIConfig = Object.assign({}, config || {});
        aiAvailable = Boolean(currentAIConfig.available);
        newAIChatButton.style.display = aiAvailable ? "flex" : "none";
        aiModelBar.style.display = aiAvailable ? "flex" : "none";
        aiModelInput.value = aiAvailable ? String(currentAIConfig.model || "") : "";
        aiProvider.textContent = aiAvailable ? `${currentAIConfig.provider} / OpenAI 兼容接口` : "";
        aiModelBar.title = aiAvailable ? `模型服务：${currentAIConfig.provider} / OpenAI 兼容接口` : "";
        setModelChoices(aiAvailable ? currentAIConfig.model_options : []);
        autoApproveControl.style.display = aiAvailable ? "flex" : "none";
        stepLimitControl.style.display = aiAvailable ? "flex" : "none";
    }

    function openAIConfigDialog() {
        if (!ready) return;
        if (restoringPersistedAIConfig) {
            writeNotice("正在恢复此浏览器保存的 AI 配置，请稍候…", "36");
            return;
        }
        aiEnabledInput.checked = Boolean(currentAIConfig.enabled);
        aiProviderInput.value = currentAIConfig.provider || "deepseek";
        aiBaseURLInput.value = currentAIConfig.base_url || providerDefaults[aiProviderInput.value] || "";
        aiConfigModelInput.value = currentAIConfig.model || "";
        aiTimeoutInput.value = String(currentAIConfig.timeout_seconds || 60);
        aiAPIKeyInput.value = "";
        aiClearKeyInput.checked = false;
        aiPersistInput.checked = Boolean(persistedAIConfig);
        aiPersistInput.disabled = !encryptedStorageAvailable();
        aiPersistHelp.textContent = encryptedStorageAvailable()
            ? "勾选后，下次使用同一浏览器登录此账户时会自动恢复；清除站点数据会删除配置。"
            : "当前页面不是安全上下文或浏览器不支持 Web Crypto，无法持久化密钥。";
        aiKeyStatus.textContent = persistedAIConfig && persistedAIConfig.api_key
            ? "当前连接使用了此浏览器加密保存的密钥；留空可继续使用。"
            : currentAIConfig.api_key_configured
            ? "当前连接已有密钥；端点不变时留空可继续使用。"
            : "当前连接没有 API Key；本地服务可按实际配置留空。";
        aiConfigStatus.textContent = "";
        aiConfigStatus.style.color = "";
        aiConfigDialog.style.display = "flex";
        aiEnabledInput.focus();
    }

    function closeAIConfigDialog() {
        aiConfigDialog.style.display = "none";
        aiAPIKeyInput.value = "";
        aiClearKeyInput.checked = false;
        terminal.focus();
    }

    function applyAIModel() {
        const model = aiModelInput.value.trim();
        if (!ready || !aiAvailable) return;
        if (aiBusy) {
            writeNotice("请等待当前 AI 操作结束后再切换模型", "33");
            return;
        }
        if (!model) {
            writeNotice("模型名称不能为空", "31");
            return;
        }
        send({type: "set_ai_model", model});
    }

    function trackTerminalInput(data) {
        if (data === "\x7f") {
            currentLine = currentLine.slice(0, -1);
        } else if (data === "\x15" || data === "\x03") {
            currentLine = "";
        } else if (/^[^\x00-\x1f\x7f]+$/.test(data)) {
            currentLine += data;
        } else {
            lineTrackingReliable = false;
        }
    }

    function submitTrackedLine() {
        send({type: "submit", line: currentLine});
        if (currentLine.trim()) pendingAICommand = false;
        currentLine = "";
        lineTrackingReliable = true;
    }

    function restoreAIInputTracking() {
        // AI notices are written directly to xterm and are never user input.
        // Once a response finishes at an empty prompt, resume classification
        // even if an IME/control sequence made the previous line unreliable.
        if (!currentLine && bracketedPasteBuffer === null) {
            lineTrackingReliable = true;
        }
    }

    function handleSingleLineInput(data) {
        if (!aiAvailable || !lineTrackingReliable) return false;
        const match = data.match(/^([^\r\n\x00-\x1f\x7f]*)(\r\n?|\n)?$/);
        if (!match) return false;
        const text = match[1];
        const submitted = Boolean(match[2]);
        if (!text && !submitted) return false;
        if (text) {
            send({type: "input", data: text});
            currentLine += text;
        }
        if (submitted) submitTrackedLine();
        return true;
    }

    function handleBracketedPaste(data) {
        if (bracketedPasteBuffer === null) {
            const startIndex = data.indexOf(pasteStart);
            if (startIndex < 0) return false;
            const prefix = data.slice(0, startIndex);
            if (prefix) {
                send({type: "input", data: prefix});
                trackTerminalInput(prefix);
            }
            bracketedPasteBuffer = data.slice(startIndex + pasteStart.length);
        } else {
            bracketedPasteBuffer += data;
        }

        const endIndex = bracketedPasteBuffer.indexOf(pasteEnd);
        if (endIndex < 0) return true;
        const pastedText = bracketedPasteBuffer.slice(0, endIndex);
        const suffix = bracketedPasteBuffer.slice(endIndex + pasteEnd.length);
        bracketedPasteBuffer = null;
        if (!handleSingleLineInput(pastedText)) {
            // Preserve native bracketed-paste semantics for genuine multiline
            // shell input, which cannot be classified as one AI question.
            send({type: "input", data: pasteStart + pastedText + pasteEnd});
            lineTrackingReliable = false;
        }
        if (suffix) handleTerminalData(suffix);
        return true;
    }

    function handleTerminalData(data) {
        if (!ready) return;
        clearPlaceholder();
        if (aiAvailable && (bracketedPasteBuffer !== null || data.includes(pasteStart))) {
            handleBracketedPaste(data);
            return;
        }
        // IMEs and paste operations may deliver "text + Enter" in one event.
        // Classify it as one line instead of forwarding the Enter to the shell.
        if (handleSingleLineInput(data)) return;
        send({type: "input", data});
        if (aiAvailable) {
            trackTerminalInput(data);
            // Editing keys make only the current line impossible to reconstruct.
            // Once that line is submitted to the shell, resume AI classification.
            if (/(?:\r\n?|\n)$/.test(data)) {
                currentLine = "";
                lineTrackingReliable = true;
            }
        }
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
                currentUsername = String(message.username || "");
                aiConfigButton.disabled = false;
                syncAIConfig(message.ai);
                maxSteps = Number(message.ai_loop && message.ai_loop.max_steps) || 10;
                stepLimitInput.max = String(Number(message.ai_loop && message.ai_loop.max_allowed_steps) || 50);
                stepLimitInput.value = String(maxSteps);
                riskMaxSteps.textContent = String(maxSteps);
                autoApprove = false;
                autoApproveCheckbox.checked = false;
                bracketedPasteBuffer = null;
                currentLine = "";
                lineTrackingReliable = true;
                newAIChatButton.title = "开始新的 AI 对话";
                setStatus("connected", "已连接");
                fitAddon.fit();
                sendSize();
                terminal.focus();
                writeNotice(aiAvailable ? `AI 已启用：${message.ai.provider} / ${message.ai.model}` : "AI 未启用，所有输入均按 Shell 命令执行", aiAvailable ? "36" : "90");
                restoreEncryptedAIConfig();
            } else if (message.type === "ai_config_changed") {
                restoringPersistedAIConfig = false;
                syncAIConfig(message.ai);
                aiBusy = false;
                aiTurns = 0;
                pendingAICommand = false;
                autoApprove = false;
                autoApproveCheckbox.checked = false;
                currentLine = "";
                lineTrackingReliable = true;
                closeAIConfigDialog();
                newAIChatButton.title = "开始新的 AI 对话";
                writeNotice(
                    aiAvailable
                        ? `AI 配置已应用：${message.ai.provider} / ${message.ai.model}`
                        : "当前连接的 AI 已关闭，所有输入均按 Shell 命令执行",
                    aiAvailable ? "36" : "90"
                );
                const persistence = pendingAIConfigPersistence;
                pendingAIConfigPersistence = null;
                if (persistence) {
                    const operation = persistence.persist
                        ? saveEncryptedAIConfig(persistence.config)
                        : deleteEncryptedAIConfig();
                    operation.then(() => {
                        writeNotice(
                            persistence.persist
                                ? "AI 配置已使用 AES-GCM 加密保存在此浏览器"
                                : "已停止持久化并清除此浏览器保存的 AI 配置",
                            "36"
                        );
                    }).catch(() => {
                        writeNotice("AI 配置已应用，但浏览器加密存储失败", "33");
                    });
                }
            } else if (message.type === "ai_config_error") {
                if (restoringPersistedAIConfig) {
                    restoringPersistedAIConfig = false;
                    deleteEncryptedAIConfig().catch(() => {});
                    writeNotice(
                        `浏览器保存的 AI 配置无法恢复，已清除：${message.message || "配置无效"}`,
                        "33"
                    );
                    return;
                }
                pendingAIConfigPersistence = null;
                aiConfigStatus.textContent = message.message || "AI 配置无效";
                aiConfigStatus.style.color = "#b91c1c";
            } else if (message.type === "ai_thinking") {
                aiBusy = true;
                pendingAICommand = false;
                writeNotice("AI 正在思考…", "36");
            } else if (message.type === "ai_reply") {
                aiBusy = false;
                restoreAIInputTracking();
                aiTurns = Number(message.turns || aiTurns + 1);
                newAIChatButton.title = `开始新的 AI 对话（当前上下文 ${aiTurns} 轮）`;
                pendingAICommand = Boolean(message.command);
                writeNotice(`AI：${message.answer || ""}`, "36");
                if (message.command) {
                    const confirmation = message.requires_confirmation
                        ? "检测到高风险操作，自动批准已暂停；请审阅后按 Ctrl+Enter 确认执行"
                        : message.auto_approve
                            ? "自动批准已启用，即将执行"
                            : "按 Ctrl+Enter 确认执行";
                    writeNotice(`建议命令：${message.command}\n${confirmation}`, message.requires_confirmation ? "31" : "33");
                } else if (message.done === false) {
                    writeNotice("当前目标尚未完成，可补充信息后继续", "33");
                }
            } else if (message.type === "ai_executing") {
                aiBusy = true;
                pendingAICommand = false;
                writeNotice(`正在执行 AI 建议：${message.command}`, "35");
            } else if (message.type === "ai_summary") {
                aiBusy = false;
                restoreAIInputTracking();
                aiTurns = Number(message.turns || aiTurns);
                newAIChatButton.title = `开始新的 AI 对话（当前上下文 ${aiTurns} 轮）`;
                writeNotice(`AI 分析（退出码 ${message.exit_code}）：${message.summary}`, message.exit_code === 0 ? "32" : "33");
                pendingAICommand = Boolean(message.command);
                if (message.command) {
                    const confirmation = message.requires_confirmation
                        ? "检测到高风险操作，自动批准已暂停；请审阅后按 Ctrl+Enter 确认执行"
                        : message.auto_approve
                            ? "自动批准已启用，即将执行"
                            : "按 Ctrl+Enter 确认执行";
                    maxSteps = Number(message.max_steps || maxSteps);
                    writeNotice(`下一步（${Number(message.step || 0) + 1}/${maxSteps}）：${message.command}\n${confirmation}`, message.requires_confirmation ? "31" : "33");
                } else if (message.done === false) {
                    writeNotice("AI 未生成可执行的下一步，当前目标已保留，可补充信息后继续", "33");
                } else {
                    writeNotice("AI 目标循环已完成", "36");
                }
            } else if (message.type === "ai_auto_approve") {
                autoApprove = Boolean(message.enabled);
                autoApproveCheckbox.checked = autoApprove;
                writeNotice(autoApprove ? "自动批准已启用（高危命令仍需人工确认）" : "自动批准已关闭", autoApprove ? "33" : "36");
            } else if (message.type === "ai_loop_settings") {
                maxSteps = Number(message.max_steps || 10);
                stepLimitInput.max = String(Number(message.max_allowed_steps || 50));
                stepLimitInput.value = String(maxSteps);
                riskMaxSteps.textContent = String(maxSteps);
                writeNotice(`AI 单次目标循环上限已设为 ${maxSteps} 步`, "36");
            } else if (message.type === "ai_model_changed") {
                aiModelInput.value = String(message.model || "");
                if (![...aiModelOptions.options].some((option) => option.value === message.model)) {
                    const option = document.createElement("option");
                    option.value = String(message.model || "");
                    aiModelOptions.appendChild(option);
                }
                if (message.conversation_reset) {
                    aiBusy = false;
                    aiTurns = 0;
                    pendingAICommand = false;
                    autoApprove = false;
                    autoApproveCheckbox.checked = false;
                    restoreAIInputTracking();
                    newAIChatButton.title = "开始新的 AI 对话";
                    writeNotice(`已切换 AI 模型：${message.model}；已开始新对话并关闭自动批准`, "36");
                } else {
                    writeNotice(`当前 AI 模型：${message.model}`, "36");
                }
            } else if (message.type === "ai_loop_stopped") {
                aiBusy = false;
                pendingAICommand = false;
                restoreAIInputTracking();
                writeNotice(message.message || "AI 目标循环已停止", "33");
            } else if (message.type === "ai_error") {
                aiBusy = false;
                restoreAIInputTracking();
                writeNotice(`AI：${message.message || "请求失败"}`, "31");
            } else if (message.type === "ai_chat_reset") {
                aiBusy = false;
                aiTurns = 0;
                pendingAICommand = false;
                autoApprove = false;
                autoApproveCheckbox.checked = false;
                restoreAIInputTracking();
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
            aiConfigButton.disabled = true;
            pendingAIConfigPersistence = null;
            restoringPersistedAIConfig = false;
            aiBusy = false;
            autoApprove = false;
            autoApproveCheckbox.checked = false;
            bracketedPasteBuffer = null;
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

    function closeAutoApproveDialog() {
        autoApproveDialog.style.display = "none";
        autoApproveCheckbox.checked = autoApprove;
        terminal.focus();
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

    terminal.onData(handleTerminalData);
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
                    // Let xterm add bracketed-paste markers when the shell has
                    // enabled that mode; onData then classifies safe single lines.
                    terminal.paste(text);
                }
            }).catch(() => {});
            return false;
        }
        return true;
    });

    connectButton.addEventListener("click", connect);
    disconnectButton.addEventListener("click", disconnect);
    aiConfigButton.disabled = true;
    aiConfigButton.addEventListener("click", openAIConfigDialog);
    aiConfigClose.addEventListener("click", closeAIConfigDialog);
    aiConfigCancel.addEventListener("click", closeAIConfigDialog);
    aiConfigDialog.addEventListener("click", (event) => {
        if (event.target === aiConfigDialog) closeAIConfigDialog();
    });
    aiProviderInput.addEventListener("change", () => {
        const oldDefaults = Object.values(providerDefaults);
        if (!aiBaseURLInput.value || oldDefaults.includes(aiBaseURLInput.value)) {
            aiBaseURLInput.value = providerDefaults[aiProviderInput.value] || "";
        }
        if (currentAIConfig.api_key_configured) {
            aiKeyStatus.textContent = "切换 Provider 或端点时必须重新输入 API Key；旧密钥不会发送到新端点。";
        }
    });
    aiConfigForm.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!ready || aiBusy) {
            writeNotice("请连接终端并等待当前 AI 操作结束后再修改配置", "33");
            return;
        }
        const provider = aiProviderInput.value;
        const baseURL = aiBaseURLInput.value.trim().replace(/\/$/, "");
        const samePersistedEndpoint = Boolean(
            persistedAIConfig
            && persistedAIConfig.provider === provider
            && String(persistedAIConfig.base_url || "").replace(/\/$/, "") === baseURL
        );
        const apiKey = aiClearKeyInput.checked
            ? ""
            : aiAPIKeyInput.value || (samePersistedEndpoint ? persistedAIConfig.api_key : "");
        const config = {
            enabled: aiEnabledInput.checked,
            provider,
            base_url: aiBaseURLInput.value,
            model: aiConfigModelInput.value,
            api_key: apiKey,
            clear_api_key: aiClearKeyInput.checked,
            timeout_seconds: Number(aiTimeoutInput.value)
        };
        pendingAIConfigPersistence = {
            persist: aiPersistInput.checked,
            config
        };
        send({
            type: "set_ai_config",
            enabled: config.enabled,
            provider: config.provider,
            base_url: config.base_url,
            model: config.model,
            api_key: apiKey || null,
            clear_api_key: aiClearKeyInput.checked,
            timeout_seconds: config.timeout_seconds
        });
        aiConfigStatus.textContent = "正在应用配置…";
        aiConfigStatus.style.color = "";
    });
    newAIChatButton.addEventListener("click", () => {
        if (!ready || !aiAvailable || aiBusy) return;
        send({type: "new_ai_chat"});
        terminal.focus();
    });
    aiModelApply.addEventListener("click", applyAIModel);
    aiModelInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        applyAIModel();
    });
    stepLimitInput.addEventListener("change", () => {
        const upper = Number(stepLimitInput.max || 50);
        const requested = Number.parseInt(stepLimitInput.value, 10);
        const normalized = Number.isFinite(requested) ? Math.max(1, Math.min(upper, requested)) : maxSteps;
        stepLimitInput.value = String(normalized);
        send({type: "set_ai_max_steps", max_steps: normalized});
        terminal.focus();
    });
    autoApproveCheckbox.addEventListener("change", () => {
        if (!autoApproveCheckbox.checked) {
            send({type: "set_auto_approve", enabled: false});
            return;
        }
        autoApproveCheckbox.checked = false;
        autoApproveDialog.style.display = "flex";
        autoApproveConfirm.focus();
    });
    autoApproveClose.addEventListener("click", closeAutoApproveDialog);
    autoApproveCancel.addEventListener("click", closeAutoApproveDialog);
    autoApproveConfirm.addEventListener("click", () => {
        autoApproveDialog.style.display = "none";
        send({type: "set_auto_approve", enabled: true});
        terminal.focus();
    });
    autoApproveDialog.addEventListener("click", (event) => {
        if (event.target === autoApproveDialog) closeAutoApproveDialog();
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
        if (event.key === "Escape") {
            if (aiConfigDialog.style.display !== "none") closeAIConfigDialog();
            else if (autoApproveDialog.style.display !== "none") closeAutoApproveDialog();
            else if (leaveDialog.style.display !== "none") closeLeaveDialog();
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
