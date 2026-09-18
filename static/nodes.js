/**
 * Nodes Page - Slurm 节点管理
 */

let allNodes = [];
let allNodesConfig = [];
let visibleNodes = [];
const selectedNodes = new Set();
let nodeActionRunning = false;

function escapeNodeText(value) {
    return String(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
}

function updateNodeSelection() {
    const count = selectedNodes.size;
    const hasConfigs = count > 0 && [...selectedNodes].every(name =>
        allNodesConfig.some(node => node.name === name));
    const selectAll = document.getElementById('selectAllNodes');
    selectAll.checked = visibleNodes.length > 0 && count === visibleNodes.length;
    selectAll.indeterminate = count > 0 && count < visibleNodes.length;
    selectAll.disabled = nodeActionRunning || visibleNodes.length === 0;
    document.getElementById('nodeSelectionCount').textContent =
        `${nodeActionRunning ? '正在处理，' : ''}已选 ${count} 个节点`;
    for (const action of ['Drain', 'Resume', 'Clear']) {
        document.getElementById(`node${action}Selected`).disabled = nodeActionRunning || count === 0;
    }
    document.getElementById('nodeEditSelected').disabled = nodeActionRunning || count !== 1 || !hasConfigs;
    document.getElementById('nodeDeleteSelected').disabled = nodeActionRunning || !hasConfigs;
    document.querySelectorAll('.node-selector').forEach(input => {
        input.checked = selectedNodes.has(input.value);
        input.disabled = nodeActionRunning;
        input.closest('tr').classList.toggle('node-selected', input.checked);
    });
}

function selectNode(name, checked) {
    if (nodeActionRunning) return;
    if (checked && visibleNodes.some(node => node.name === name)) selectedNodes.add(name);
    else selectedNodes.delete(name);
    updateNodeSelection();
}

function selectAllNodes(checked) {
    if (nodeActionRunning) return;
    selectedNodes.clear();
    if (checked) visibleNodes.forEach(node => selectedNodes.add(node.name));
    updateNodeSelection();
}

function editSelectedNode() {
    if (!nodeActionRunning && selectedNodes.size === 1) editNode([...selectedNodes][0]);
}

function runSelectedNodeAction(action) {
    if (nodeActionRunning || selectedNodes.size === 0) return;
    const actions = {
        drain: {label: '下线', api: drainNodeAPI, detail: '节点将停止接受新作业。'},
        resume: {label: '上线', api: resumeNodeAPI, detail: '节点将恢复接受新作业。'},
        delete: {label: '删除', api: deleteNodeConfigAPI, detail: '将从配置文件中移除所选节点。'}
    };
    const operation = actions[action];
    if (!operation) return;
    const names = [...selectedNodes];
    if (action === 'delete' && names.some(name => !allNodesConfig.some(node => node.name === name))) {
        showToast('所选节点中有节点缺少对应配置，无法删除', 'error');
        return;
    }
    showConfirmModal(
        `${operation.label}所选节点`,
        `确定要${operation.label}以下 ${names.length} 个节点吗？${operation.detail}\n${names.join('、')}`,
        async () => {
            if (nodeActionRunning) return;
            nodeActionRunning = true;
            updateNodeSelection();
            const failures = [];
            try {
                // 配置写入必须串行，避免并发覆盖同一文件。
                for (const name of names) {
                    try {
                        if (await operation.api(name)) selectedNodes.delete(name);
                        else failures.push(name);
                    } catch (error) {
                        failures.push(name);
                    }
                }
                showToast(`${operation.label}完成：成功 ${names.length - failures.length} 个，失败 ${failures.length} 个` +
                    (failures.length ? `（${failures.join('、')}）` : ''), failures.length ? 'error' : 'success');
                await loadNodes();
            } finally {
                nodeActionRunning = false;
                updateNodeSelection();
            }
        }
    );
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', async () => {
    await loadNodes();
});

// 加载所有节点
async function loadNodes() {
    // 并行加载运行时状态和配置文件
    const [runtimeNodes, configNodes] = await Promise.all([
        fetchNodes(),
        fetchNodesConfig()
    ]);

    allNodes = runtimeNodes;
    allNodesConfig = configNodes;

    // 合并运行时状态和配置信息
    const mergedNodes = mergeNodeData(runtimeNodes, configNodes);
    renderNodesTable(mergedNodes);
}

// 合并节点运行时状态和配置信息
function mergeNodeData(runtimeNodes, configNodes) {
    const merged = [];
    const processedNodes = new Set();

    // 去重：同一节点可能出现在多个分区，需要合并
    const nodeMap = new Map();

    runtimeNodes.forEach(node => {
        if (!nodeMap.has(node.name)) {
            nodeMap.set(node.name, {
                ...node,
                partitions: [node.partition]
            });
        } else {
            // 合并分区信息
            const existing = nodeMap.get(node.name);
            if (node.partition && !existing.partitions.includes(node.partition)) {
                existing.partitions.push(node.partition);
            }
        }
    });

    // 将 Map 转换为数组，并添加配置信息
    nodeMap.forEach((node, nodeName) => {
        const config = configNodes.find(c => c.name === nodeName);
        merged.push({
            ...node,
            partition: node.partitions.join(', '),  // 显示所有分区
            config: config || null
        });
        processedNodes.add(nodeName);
    });

    // 添加配置中存在但运行时不存在的节点
    configNodes.forEach(config => {
        if (!processedNodes.has(config.name)) {
            merged.push({
                name: config.name,
                state: 'N/A',
                cpus: config.cpus || 'N/A',
                memory: config.real_memory || 'N/A',
                partition: 'N/A',
                config: config
            });
        }
    });

    return merged;
}

// 渲染节点表格
function renderNodesTable(nodes) {
    const tbody = document.getElementById('nodesTableBody');
    if (!tbody) return;

    visibleNodes = nodes;
    const availableNames = new Set(nodes.map(node => node.name));
    for (const name of selectedNodes) {
        if (!availableNames.has(name)) selectedNodes.delete(name);
    }

    if (nodes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-gray-500 py-8">暂无节点数据</td></tr>';
        updateNodeSelection();
        return;
    }

    tbody.innerHTML = nodes.map(node => {
        const stateBadge = getStateBadge(node.state);
        const gres = node.config?.gres || node.gres || '-';

        return `
            <tr>
                <td><input class="node-selector" type="checkbox" value="${escapeNodeText(node.name)}" aria-label="选择节点 ${escapeNodeText(node.name)}"></td>
                <td><strong>${escapeNodeText(node.name)}</strong></td>
                <td class="col-status">${stateBadge}</td>
                <td class="col-number">${escapeNodeText(node.cpus || node.config?.cpus || '-')}</td>
                <td class="col-number">${escapeNodeText(node.memory || node.config?.real_memory || '-')}</td>
                <td>${escapeNodeText(node.partition || '-')}</td>
                <td><code style="font-size: 11px;">${escapeNodeText(gres)}</code></td>
            </tr>
        `;
    }).join('');
    tbody.querySelectorAll('.node-selector').forEach(input => {
        input.addEventListener('change', () => selectNode(input.value, input.checked));
    });
    updateNodeSelection();
}

// 获取状态徽章
function getStateBadge(state) {
    const stateUpper = (state || 'N/A').toUpperCase();
    if (stateUpper.includes('IDLE')) {
        return '<span class="badge badge-success">IDLE</span>';
    } else if (stateUpper.includes('ALLOC') || stateUpper.includes('MIXED')) {
        return '<span class="badge badge-warning">ALLOC</span>';
    } else if (stateUpper.includes('DRAIN')) {
        return '<span class="badge badge-warning">DRAIN</span>';
    } else if (stateUpper.includes('DOWN') || stateUpper.includes('INVAL')) {
        return '<span class="badge badge-danger">DOWN</span>';
    } else if (stateUpper === 'N/A') {
        return '<span class="badge">N/A</span>';
    } else {
        return `<span class="badge">${escapeNodeText(state)}</span>`;
    }
}

// 添加和编辑使用相同的节点配置字段。
function nodeConfigFields() {
    return `
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">NodeAddr (可选)</label>
            <input type="text" name="node_addr" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="172.90.50.6">
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Parameters (可选)</label>
            <input type="text" name="parameters" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="numa_node_as_socket">
            <p class="text-sm text-gray-500">多个参数用逗号分隔，需与节点 Slurm 版本及 CPU 拓扑匹配。</p>
        </div>
        <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">State (配置状态，可选)</label>
            <input type="text" name="state" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="UNKNOWN">
            <p class="text-sm text-gray-500">设置配置文件中的状态；节点上线或下线请使用列表操作。</p>
        </div>
    `;
}

// 显示添加节点模态框
function showAddNodeModal() {
    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'addNodeModal';

    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-2xl w-full mx-4 shadow-xl max-h-90vh overflow-y-auto';

    modal.innerHTML = `
        <div class="modal-scroll-header">
            <h3>添加新节点</h3>
            <button type="button" onclick="closeAddNodeModal()" class="modal-close" aria-label="关闭添加节点弹窗">&times;</button>
        </div>
        <form id="addNodeForm" class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">节点名称 *</label>
                <input type="text" name="name" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="node01 或 node[01-08]">
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CPUs *</label>
                    <input type="number" name="cpus" required min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="256">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Boards</label>
                    <input type="number" name="boards" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="1">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">SocketsPerBoard</label>
                    <input type="number" name="sockets_per_board" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="2">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CoresPerSocket</label>
                    <input type="number" name="cores_per_socket" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="128">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">ThreadsPerCore</label>
                    <input type="number" name="threads_per_core" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="1">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">RealMemory (MB)</label>
                    <input type="number" name="real_memory" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="385000">
                </div>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Gres (可选)</label>
                <input type="text" name="gres" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="gpu:nvidia_geforce_rtx_4090:1">
            </div>

            ${nodeConfigFields()}

            <div class="flex gap-3 justify-end mt-6 pt-4 border-t">
                <button type="button" onclick="closeAddNodeModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                    取消
                </button>
                <button type="submit" class="px-4 py-2 text-white rounded-md transition" style="background-color: #dc3023;">
                    添加节点
                </button>
            </div>
        </form>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // 处理表单提交
    document.getElementById('addNodeForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleAddNode(e.target);
    });

    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeAddNodeModal();
    });
}

// 处理添加节点
async function handleAddNode(form) {
    const formData = new FormData(form);

    const nodeData = {
        name: formData.get('name'),
        cpus: parseInt(formData.get('cpus')),
        boards: parseInt(formData.get('boards')) || null,
        sockets_per_board: parseInt(formData.get('sockets_per_board')) || null,
        cores_per_socket: parseInt(formData.get('cores_per_socket')) || null,
        threads_per_core: parseInt(formData.get('threads_per_core')) || null,
        real_memory: parseInt(formData.get('real_memory')) || null,
        gres: formData.get('gres') || null,
        node_addr: formData.get('node_addr').trim(),
        parameters: formData.get('parameters').trim(),
        state: formData.get('state').trim()
    };

    const success = await createNode(nodeData);
    if (success) {
        closeAddNodeModal();
        await loadNodes();
    }
}

function closeAddNodeModal() {
    const modal = document.getElementById('addNodeModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
}

// 编辑节点
function editNode(nodeName) {
    const nodeConfig = allNodesConfig.find(n => n.name === nodeName);
    if (!nodeConfig) {
        showToast('节点配置不存在', 'error');
        return;
    }

    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'editNodeModal';

    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-2xl w-full mx-4 shadow-xl max-h-90vh overflow-y-auto';

    modal.innerHTML = `
        <div class="modal-scroll-header">
            <h3>编辑节点: ${escapeNodeText(nodeName)}</h3>
            <button type="button" onclick="closeEditNodeModal()" class="modal-close" aria-label="关闭编辑节点弹窗">&times;</button>
        </div>
        <form id="editNodeForm" class="space-y-4">
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CPUs</label>
                    <input type="number" name="cpus" value="${escapeNodeText(nodeConfig.cpus || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Boards</label>
                    <input type="number" name="boards" value="${escapeNodeText(nodeConfig.boards || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">SocketsPerBoard</label>
                    <input type="number" name="sockets_per_board" value="${escapeNodeText(nodeConfig.sockets_per_board || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CoresPerSocket</label>
                    <input type="number" name="cores_per_socket" value="${escapeNodeText(nodeConfig.cores_per_socket || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">ThreadsPerCore</label>
                    <input type="number" name="threads_per_core" value="${escapeNodeText(nodeConfig.threads_per_core || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">RealMemory (MB)</label>
                    <input type="number" name="real_memory" value="${escapeNodeText(nodeConfig.real_memory || '')}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Gres</label>
                <input type="text" name="gres" value="${escapeNodeText(nodeConfig.gres || '')}" class="w-full px-3 py-2 border border-gray-300 rounded-md">
            </div>

            ${nodeConfigFields()}

            <div class="flex gap-3 justify-end mt-6 pt-4 border-t">
                <button type="button" onclick="closeEditNodeModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                    取消
                </button>
                <button type="submit" class="px-4 py-2 text-white rounded-md transition" style="background-color: #dc3023;">
                    更新
                </button>
            </div>
        </form>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    const editForm = document.getElementById('editNodeForm');
    for (const field of ['node_addr', 'parameters', 'state']) {
        editForm.elements[field].value = nodeConfig[field] || '';
    }

    document.getElementById('editNodeForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleEditNode(nodeName, e.target);
    });

    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeEditNodeModal();
    });
}

// 处理编辑节点
async function handleEditNode(nodeName, form) {
    const formData = new FormData(form);

    const nodeData = {
        cpus: parseInt(formData.get('cpus')) || null,
        boards: parseInt(formData.get('boards')) || null,
        sockets_per_board: parseInt(formData.get('sockets_per_board')) || null,
        cores_per_socket: parseInt(formData.get('cores_per_socket')) || null,
        threads_per_core: parseInt(formData.get('threads_per_core')) || null,
        real_memory: parseInt(formData.get('real_memory')) || null,
        gres: formData.get('gres') || null,
        node_addr: formData.get('node_addr').trim(),
        parameters: formData.get('parameters').trim(),
        state: formData.get('state').trim()
    };

    const success = await updateNodeConfig(nodeName, nodeData);
    if (success) {
        closeEditNodeModal();
        await loadNodes();
    }
}

function closeEditNodeModal() {
    const modal = document.getElementById('editNodeModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
}

// 导出函数供全局使用
window.showAddNodeModal = showAddNodeModal;
window.closeAddNodeModal = closeAddNodeModal;
window.editNode = editNode;
window.closeEditNodeModal = closeEditNodeModal;
window.selectAllNodes = selectAllNodes;
window.editSelectedNode = editSelectedNode;
window.runSelectedNodeAction = runSelectedNodeAction;
window.loadNodes = loadNodes;
