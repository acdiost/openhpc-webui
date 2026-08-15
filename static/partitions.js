/**
 * Partitions Page - Slurm 分区管理
 */

let allNodes = [];
let allNodesConfig = [];  // 节点配置列表（来自 /etc/slurm/node.conf）
let allPartitions = [];

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', async () => {
    await loadPartitions();
    await loadNodes();
    await loadNodesConfig();  // 加载节点配置用于创建分区
});

// 加载所有分区
async function loadPartitions() {
    const partitions = await fetchPartitions();
    allPartitions = partitions;
    renderPartitionsTable(partitions);
}

// 加载所有节点
async function loadNodes() {
    const nodes = await fetchNodes();
    allNodes = nodes;
}

// 加载节点配置（用于创建分区时的节点选择）
async function loadNodesConfig() {
    const nodes = await fetchNodesConfig();
    allNodesConfig = nodes;
}

// 渲染分区表格
function renderPartitionsTable(partitions) {
    const tbody = document.getElementById('partitionsTableBody');
    if (!tbody) return;

    if (partitions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-gray-500 py-8">暂无分区数据</td></tr>';
        return;
    }

    tbody.innerHTML = partitions.map(partition => {
        const stateBadge = getStateBadge(partition.state);
        const defaultBadge = partition.default
            ? '<span class="badge badge-info">是</span>'
            : '-';

        // 解析节点状态
        const nodeStats = parseNodeStats(partition);

        // 生成节点状态徽章
        const nodeStateBadges = getNodeStateBadges(partition.node_state);

        return `
            <tr>
                <td><strong>${partition.name}</strong></td>
                <td class="col-status">${stateBadge}</td>
                <td class="col-number">${nodeStats.total}</td>
                <td class="col-number">${nodeStats.alloc}</td>
                <td class="col-number">${nodeStats.idle}</td>
                <td class="col-number">${nodeStats.down}</td>
                <td style="white-space: nowrap;">${nodeStateBadges}</td>
                <td>${partition.max_time || '-'}</td>
                <td class="col-status">${defaultBadge}</td>
                <td class="col-actions"><div class="data-table-actions">
                    <button onclick="editPartition('${partition.name}')" class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px; margin-right: 4px;">编辑</button>
                    <button onclick="deletePartition('${partition.name}')" class="btn" style="padding: 4px 12px; font-size: 12px; background-color: #ef4444; color: white;">删除</button>
                </div></td>
            </tr>
        `;
    }).join('');
}

// 解析节点统计信息
function parseNodeStats(partition) {
    // 直接使用后端返回的节点状态统计
    let stats = {
        total: parseInt(partition.total_nodes) || 0,
        alloc: parseInt(partition.alloc_nodes) || 0,
        idle: parseInt(partition.idle_nodes) || 0,
        down: parseInt(partition.offline_nodes) || 0
    };

    return stats;
}

// 获取状态徽章
function getStateBadge(state) {
    const stateUpper = (state || 'UP').toUpperCase();
    if (stateUpper === 'UP') {
        return '<span class="badge badge-success">UP</span>';
    } else if (stateUpper === 'DOWN') {
        return '<span class="badge badge-danger">DOWN</span>';
    } else if (stateUpper.includes('DRAIN')) {
        return '<span class="badge badge-warning">DRAIN</span>';
    } else {
        return `<span class="badge">${state}</span>`;
    }
}

// 生成节点状态徽章
function getNodeStateBadges(nodeStateStr) {
    if (!nodeStateStr || nodeStateStr === 'N/A') {
        return '<span class="text-gray-400">-</span>';
    }

    // 解析逗号分隔的状态
    const states = nodeStateStr.split(',').map(s => s.trim());
    const uniqueStates = [...new Set(states)];

    // 为每个状态生成徽章
    const badges = uniqueStates.map(state => {
        const stateUpper = state.toUpperCase();
        let badgeClass = 'badge';
        let badgeText = state;

        if (stateUpper === 'IDLE') {
            badgeClass = 'badge badge-success';
            badgeText = '空闲';
        } else if (stateUpper === 'MIXED') {
            badgeClass = 'badge badge-info';
            badgeText = '混合';
        } else if (stateUpper === 'ALLOCATED' || stateUpper === 'ALLOC') {
            badgeClass = 'badge badge-warning';
            badgeText = '已分配';
        } else if (stateUpper === 'DRAINED' || stateUpper === 'DRAIN' || stateUpper === 'DRAINING') {
            badgeClass = 'badge badge-warning';
            badgeText = '维护中';
        } else if (stateUpper === 'DOWN' || stateUpper === 'INVAL' || stateUpper === 'INVALID') {
            badgeClass = 'badge badge-danger';
            badgeText = stateUpper === 'DOWN' ? '离线' : '异常';
        } else {
            badgeClass = 'badge';
            badgeText = state;
        }

        return `<span class="${badgeClass}" style="font-size: 11px; margin-right: 4px;">${badgeText}</span>`;
    });

    return badges.join('');
}

// 显示创建分区模态框
async function showCreatePartitionModal() {
    // 确保节点配置已加载
    if (allNodesConfig.length === 0) {
        await loadNodesConfig();
    }

    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'createPartitionModal';

    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-2xl w-full mx-4 shadow-xl max-h-90vh overflow-y-auto';

    // 生成节点选择列表（使用配置文件中的节点，避免重复）
    const nodesHTML = allNodesConfig.length > 0 ? generateNodesCheckboxes() : '<p class="text-gray-500">暂无可用节点，请先在节点管理页面创建节点配置</p>';

    modal.innerHTML = `
        <h3 class="text-xl font-semibold text-gray-900 mb-4">创建新分区</h3>
        <form id="createPartitionForm" class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">分区名称 *</label>
                <input type="text" name="name" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="partition_name">
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-2">选择节点 *</label>
                <div class="border border-gray-300 rounded-md p-3 max-h-48 overflow-y-auto">
                    ${nodesHTML}
                </div>
                <p class="text-xs text-gray-500 mt-1">提示: 也可以手动输入节点范围（如 node[01-08]）</p>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">节点范围（手动输入）</label>
                <input type="text" id="nodesManualInput" name="nodes_manual" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="node[01-08] 或留空使用上方勾选">
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">状态</label>
                    <select name="state" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                        <option value="UP">UP</option>
                        <option value="DOWN">DOWN</option>
                        <option value="DRAIN">DRAIN</option>
                    </select>
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">最大时间</label>
                    <input type="text" name="max_time" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="INFINITE 或 24:00:00">
                </div>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">允许的组 (AllowGroups)</label>
                <input type="text" name="allow_groups" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="group1,group2 或留空">
            </div>

            <div class="flex items-center">
                <input type="checkbox" name="default" id="defaultCheckbox" class="mr-2">
                <label for="defaultCheckbox" class="text-sm text-gray-700">设为默认分区</label>
            </div>

            <div class="flex gap-3 justify-end mt-6 pt-4 border-t">
                <button type="button" onclick="closeCreatePartitionModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                    取消
                </button>
                <button type="submit" class="px-4 py-2 text-white rounded-md transition" style="background-color: #dc3023;">
                    创建分区
                </button>
            </div>
        </form>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // 处理表单提交
    document.getElementById('createPartitionForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleCreatePartition(e.target);
    });

    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeCreatePartitionModal();
    });
}

// 生成节点复选框列表（使用配置文件数据，避免重复）
function generateNodesCheckboxes() {
    if (allNodesConfig.length === 0) return '<p class="text-gray-500">无可用节点</p>';

    return `
        <div class="space-y-2">
            ${allNodesConfig.map(node => {
                // 构建配置信息显示
                const cpuInfo = node.cpus ? `${node.cpus} CPUs` : 'N/A';
                const memInfo = node.real_memory ? `${node.real_memory}MB` : '';
                const configDisplay = [cpuInfo, memInfo].filter(x => x).join(' | ');

                return `
                    <label class="flex items-center hover:bg-gray-50 p-2 rounded cursor-pointer">
                        <input type="checkbox" name="node_checkbox" value="${node.name}" class="mr-2">
                        <span class="flex-1">${node.name}</span>
                        <span class="text-xs text-gray-500">${configDisplay}</span>
                    </label>
                `;
            }).join('')}
        </div>
    `;
}

// 处理创建分区
async function handleCreatePartition(form) {
    const formData = new FormData(form);

    // 获取节点：优先使用手动输入，否则使用勾选的节点
    let nodes = formData.get('nodes_manual');
    if (!nodes) {
        const checkedNodes = Array.from(form.querySelectorAll('input[name="node_checkbox"]:checked'))
            .map(cb => cb.value);

        if (checkedNodes.length === 0) {
            showToast('请选择节点或手动输入节点范围', 'error');
            return;
        }

        // 将选中的节点转换为逗号分隔
        nodes = checkedNodes.join(',');
    }

    const partitionData = {
        name: formData.get('name'),
        nodes: nodes,
        state: formData.get('state'),
        max_time: formData.get('max_time') || null,
        allow_groups: formData.get('allow_groups') || null,
        default: formData.get('default') === 'on'
    };

    const success = await createPartition(partitionData);
    if (success) {
        closeCreatePartitionModal();
        await loadPartitions();
    }
}

function closeCreatePartitionModal() {
    const modal = document.getElementById('createPartitionModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
}

// 编辑分区
function editPartition(partitionName) {
    const partition = allPartitions.find(p => p.name === partitionName);
    if (!partition) {
        showToast('分区不存在', 'error');
        return;
    }

    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'editPartitionModal';

    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-2xl w-full mx-4 shadow-xl max-h-90vh overflow-y-auto';

    modal.innerHTML = `
        <h3 class="text-xl font-semibold text-gray-900 mb-4">编辑分区: ${partition.name}</h3>
        <form id="editPartitionForm" class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">节点范围</label>
                <input type="text" name="nodes" value="${partition.nodes || ''}" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="node[01-08]">
            </div>

            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">状态</label>
                    <select name="state" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                        <option value="UP" ${partition.state === 'UP' ? 'selected' : ''}>UP</option>
                        <option value="DOWN" ${partition.state === 'DOWN' ? 'selected' : ''}>DOWN</option>
                        <option value="DRAIN" ${partition.state === 'DRAIN' ? 'selected' : ''}>DRAIN</option>
                    </select>
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">最大时间</label>
                    <input type="text" name="max_time" value="${partition.max_time || ''}" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="INFINITE 或 24:00:00">
                </div>
            </div>

            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">允许的组 (AllowGroups)</label>
                <input type="text" name="allow_groups" value="${partition.allow_groups || ''}" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="group1,group2">
            </div>

            <div class="flex items-center">
                <input type="checkbox" name="default" id="editDefaultCheckbox" ${partition.default ? 'checked' : ''} class="mr-2">
                <label for="editDefaultCheckbox" class="text-sm text-gray-700">设为默认分区</label>
            </div>

            <div class="flex gap-3 justify-end mt-6 pt-4 border-t">
                <button type="button" onclick="closeEditPartitionModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
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

    document.getElementById('editPartitionForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        await handleEditPartition(partitionName, e.target);
    });

    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeEditPartitionModal();
    });
}

// 处理编辑分区
async function handleEditPartition(partitionName, form) {
    const formData = new FormData(form);

    const partitionData = {
        nodes: formData.get('nodes') || null,
        state: formData.get('state'),
        max_time: formData.get('max_time') || null,
        allow_groups: formData.get('allow_groups') || null,
        default: formData.get('default') === 'on'
    };

    const success = await updatePartition(partitionName, partitionData);
    if (success) {
        closeEditPartitionModal();
        await loadPartitions();
    }
}

function closeEditPartitionModal() {
    const modal = document.getElementById('editPartitionModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
}

// 删除分区
function deletePartition(partitionName) {
    showConfirmModal(
        '删除分区',
        `确定要删除分区 "${partitionName}" 吗？此操作将从配置文件中移除该分区。`,
        async () => {
            const success = await deletePartitionAPI(partitionName);
            if (success) {
                await loadPartitions();
            }
        }
    );
}

// 导出函数供全局使用
window.showCreatePartitionModal = showCreatePartitionModal;
window.closeCreatePartitionModal = closeCreatePartitionModal;
window.editPartition = editPartition;
window.closeEditPartitionModal = closeEditPartitionModal;
window.deletePartition = deletePartition;
window.loadPartitions = loadPartitions;
