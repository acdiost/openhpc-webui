/**
 * Nodes Page - Slurm 节点管理
 */

let allNodes = [];
let allNodesConfig = [];

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

    if (nodes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-gray-500 py-8">暂无节点数据</td></tr>';
        return;
    }

    tbody.innerHTML = nodes.map(node => {
        const stateBadge = getStateBadge(node.state);
        const gres = node.config?.gres || node.gres || '-';

        return `
            <tr>
                <td><strong>${node.name}</strong></td>
                <td class="col-status">${stateBadge}</td>
                <td class="col-number">${node.cpus || node.config?.cpus || '-'}</td>
                <td class="col-number">${node.memory || node.config?.real_memory || '-'}</td>
                <td>${node.partition || '-'}</td>
                <td><code style="font-size: 11px;">${gres}</code></td>
                <td class="col-actions" style="width:286px;min-width:286px"><div class="data-table-actions">
                    <button onclick="drainNode('${node.name}')" class="btn" style="padding: 4px 12px; font-size: 12px; background-color: #f59e0b; color: white; margin-right: 4px;">下线</button>
                    <button onclick="resumeNode('${node.name}')" class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px; margin-right: 4px;">上线</button>
                    <button onclick="editNode('${node.name}')" class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px; margin-right: 4px;">编辑</button>
                    <button onclick="deleteNode('${node.name}')" class="btn" style="padding: 4px 12px; font-size: 12px; background-color: #ef4444; color: white;">删除</button>
                </div></td>
            </tr>
        `;
    }).join('');
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
        return `<span class="badge">${state}</span>`;
    }
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
        gres: formData.get('gres') || null
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
            <h3>编辑节点: ${nodeName}</h3>
            <button type="button" onclick="closeEditNodeModal()" class="modal-close" aria-label="关闭编辑节点弹窗">&times;</button>
        </div>
        <form id="editNodeForm" class="space-y-4">
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CPUs</label>
                    <input type="number" name="cpus" value="${nodeConfig.cpus || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">Boards</label>
                    <input type="number" name="boards" value="${nodeConfig.boards || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">SocketsPerBoard</label>
                    <input type="number" name="sockets_per_board" value="${nodeConfig.sockets_per_board || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">CoresPerSocket</label>
                    <input type="number" name="cores_per_socket" value="${nodeConfig.cores_per_socket || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">ThreadsPerCore</label>
                    <input type="number" name="threads_per_core" value="${nodeConfig.threads_per_core || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">RealMemory (MB)</label>
                    <input type="number" name="real_memory" value="${nodeConfig.real_memory || ''}" min="1" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Gres</label>
                <input type="text" name="gres" value="${nodeConfig.gres || ''}" class="w-full px-3 py-2 border border-gray-300 rounded-md">
            </div>

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
        gres: formData.get('gres') || null
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

// 下线节点
function drainNode(nodeName) {
    showConfirmModal(
        '下线节点',
        `确定要下线节点 "${nodeName}" 吗？节点将停止接受新作业。`,
        async () => {
            const success = await drainNodeAPI(nodeName);
            if (success) {
                await loadNodes();
            }
        }
    );
}

// 上线节点
function resumeNode(nodeName) {
    showConfirmModal(
        '上线节点',
        `确定要恢复节点 "${nodeName}" 上线吗？节点将恢复接受新作业。`,
        async () => {
            const success = await resumeNodeAPI(nodeName);
            if (success) {
                await loadNodes();
            }
        }
    );
}

// 删除节点
function deleteNode(nodeName) {
    showConfirmModal(
        '删除节点',
        `确定要删除节点 "${nodeName}" 的配置吗？此操作将从配置文件中移除该节点。`,
        async () => {
            const success = await deleteNodeConfigAPI(nodeName);
            if (success) {
                await loadNodes();
            }
        }
    );
}

// 导出函数供全局使用
window.showAddNodeModal = showAddNodeModal;
window.closeAddNodeModal = closeAddNodeModal;
window.editNode = editNode;
window.closeEditNodeModal = closeEditNodeModal;
window.drainNode = drainNode;
window.resumeNode = resumeNode;
window.deleteNode = deleteNode;
window.loadNodes = loadNodes;
