/**
 * OpenHPC Web Portal - Main JavaScript
 * Theme Color: #dc3023
 */

// Global configuration
const config = {
    autoRefreshInterval: 30000, // 30 seconds
    primaryColor: '#dc3023',
    apiBase: '/api/ldap'
};

// Auto-refresh functionality
let autoRefreshTimer = null;

// ==================== Slurm API Functions ====================

// Fetch all accounts
async function fetchAccounts() {
    try {
        const response = await fetch('/api/slurm/accounts');
        const data = await response.json();
        return data.accounts || [];
    } catch (error) {
        console.error('Failed to fetch accounts:', error);
        showToast('获取账户列表失败', 'error');
        return [];
    }
}

// Create new account
async function createAccount(accountData) {
    try {
        const response = await fetch('/api/slurm/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(accountData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create account');
        }
        showToast('账户创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create account:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update account
async function updateAccount(accountName, accountData) {
    try {
        const response = await fetch(`/api/slurm/accounts/${encodeURIComponent(accountName)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(accountData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update account');
        }
        showToast('账户更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update account:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete account
async function deleteAccount(accountName) {
    try {
        const response = await fetch(`/api/slurm/accounts/${encodeURIComponent(accountName)}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete account');
        }
        showToast(`账户 "${accountName}" 已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete account:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Fetch associations (cluster users)
async function fetchAssociations(account) {
    try {
        const query = account ? `?account=${encodeURIComponent(account)}` : '';
        const response = await fetch(`/api/slurm/associations${query}`);
        const data = await response.json();
        return data.associations || [];
    } catch (error) {
        console.error('Failed to fetch associations:', error);
        showToast('获取关联列表失败', 'error');
        return [];
    }
}

// Create association
async function createAssociation(payload) {
    try {
        const response = await fetch('/api/slurm/associations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create association');
        }
        showToast('关联创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create association:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update association
async function updateAssociation(accountName, username, payload) {
    try {
        const response = await fetch(
            `/api/slurm/associations/${encodeURIComponent(accountName)}/${encodeURIComponent(username)}`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update association');
        }
        showToast('关联更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update association:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete association
async function deleteAssociation(accountName, username, partition) {
    try {
        const query = partition ? `?partition=${encodeURIComponent(partition)}` : '';
        const response = await fetch(
            `/api/slurm/associations/${encodeURIComponent(accountName)}/${encodeURIComponent(username)}${query}`,
            { method: 'DELETE' }
        );
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete association');
        }
        showToast(`关联 "${username}/${accountName}" 已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete association:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Set association TRES minutes
async function setAssociationTRESMinutes(accountName, username, payload) {
    try {
        const response = await fetch(
            `/api/slurm/associations/${encodeURIComponent(accountName)}/${encodeURIComponent(username)}/tres-minutes`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to set TRES minutes');
        }
        showToast('核时/卡时拨付成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to set TRES minutes:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Increment a user's CPU/GPU hour balance on a Slurm association.
async function grantUserCredits(username, payload) {
    try {
        const response = await fetch(
            `/api/slurm/users/${encodeURIComponent(username)}/credit`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to grant TRES credits');
        }
        showToast(data.message || '核时/卡时拨付成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to grant TRES credits:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Fetch all partitions
async function fetchPartitions() {
    try {
        const response = await fetch('/api/slurm/partitions');
        const data = await response.json();
        return data.partitions || [];
    } catch (error) {
        console.error('Failed to fetch partitions:', error);
        showToast('获取分区列表失败', 'error');
        return [];
    }
}

// Fetch all nodes
async function fetchNodes() {
    try {
        const response = await fetch('/api/slurm/nodes');
        const data = await response.json();
        return data.nodes || [];
    } catch (error) {
        console.error('Failed to fetch nodes:', error);
        showToast('获取节点列表失败', 'error');
        return [];
    }
}

// Create new partition
async function createPartition(partitionData) {
    try {
        const response = await fetch('/api/slurm/partitions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(partitionData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create partition');
        }
        showToast('分区创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create partition:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update partition
async function updatePartition(partitionName, partitionData) {
    try {
        const response = await fetch(`/api/slurm/partitions/${partitionName}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(partitionData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update partition');
        }
        showToast('分区更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update partition:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete partition
async function deletePartitionAPI(partitionName) {
    try {
        const response = await fetch(`/api/slurm/partitions/${partitionName}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete partition');
        }
        showToast(`分区 "${partitionName}" 已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete partition:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Fetch nodes from config file
async function fetchNodesConfig() {
    try {
        const response = await fetch('/api/slurm/nodes/config');
        const data = await response.json();
        return data.nodes || [];
    } catch (error) {
        console.error('Failed to fetch nodes config:', error);
        showToast('获取节点配置失败', 'error');
        return [];
    }
}

// Create new node
async function createNode(nodeData) {
    try {
        const response = await fetch('/api/slurm/nodes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(nodeData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create node');
        }
        showToast('节点创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create node:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update node config
async function updateNodeConfig(nodeName, nodeData) {
    try {
        const response = await fetch(`/api/slurm/nodes/${nodeName}/config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(nodeData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update node config');
        }
        showToast('节点配置更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update node config:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete node config
async function deleteNodeConfigAPI(nodeName) {
    try {
        const response = await fetch(`/api/slurm/nodes/${nodeName}/config`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete node config');
        }
        showToast(`节点 "${nodeName}" 配置已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete node config:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Drain node (offline)
async function drainNodeAPI(nodeName) {
    try {
        const response = await fetch(`/api/slurm/nodes/${nodeName}/drain`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: '管理员手动下线' })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to drain node');
        }
        showToast(`节点 "${nodeName}" 已下线`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to drain node:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Resume node (online)
async function resumeNodeAPI(nodeName) {
    try {
        const response = await fetch(`/api/slurm/nodes/${nodeName}/resume`, {
            method: 'POST'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to resume node');
        }
        showToast(`节点 "${nodeName}" 已上线`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to resume node:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// ==================== LDAP API Functions ====================

// Fetch LDAP connection status
async function fetchLDAPStatus() {
    try {
        const response = await fetch(`${config.apiBase}/status`);
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Failed to fetch LDAP status:', error);
        return { status: 'error', error: error.message };
    }
}

// Fetch all users
async function fetchUsers() {
    try {
        const response = await fetch(`${config.apiBase}/users`);
        const data = await response.json();
        return data.users || [];
    } catch (error) {
        console.error('Failed to fetch users:', error);
        showToast('获取用户列表失败', 'error');
        return [];
    }
}

// Fetch specific user
async function fetchUser(username) {
    try {
        const response = await fetch(`${config.apiBase}/users/${username}`);
        if (!response.ok) throw new Error('User not found');
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch user:', error);
        return null;
    }
}

// Create new user
async function createUser(userData) {
    try {
        const response = await fetch(`${config.apiBase}/users`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(userData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create user');
        }
        showToast('用户创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create user:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete user (API call)
async function deleteUserAPI(username) {
    try {
        const response = await fetch(`${config.apiBase}/users/${username}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete user');
        }
        showToast(`用户 "${username}" 已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete user:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Disable user login by setting the LDAP login shell to nologin
async function disableUserAPI(username) {
    try {
        const encodedUsername = encodeURIComponent(username);
        const response = await fetch(
            `${config.apiBase}/users/${encodedUsername}/disable`,
            { method: 'POST' }
        );
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to disable user');
        }
        showToast(data.message || `用户 "${username}" 已禁用`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to disable user:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update user
async function updateUser(username, userData) {
    try {
        const response = await fetch(`${config.apiBase}/users/${username}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(userData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update user');
        }
        showToast('用户更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update user:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Reset user SSH key and download private key
async function resetUserSshKey(username) {
    if (!confirm(`确定要为用户 "${username}" 生成并重置 SSH 密钥吗？旧密钥将失效。`)) {
        return false;
    }
    try {
        const response = await fetch(
            `${config.apiBase}/users/${encodeURIComponent(username)}/ssh-key`,
            { method: 'POST' }
        );
        if (!response.ok) {
            let detail = '重置密钥失败';
            try {
                const data = await response.json();
                detail = data.detail || detail;
            } catch (e) {
                // ignore parse error
            }
            throw new Error(detail);
        }

        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        let filename = `${username}_id_rsa`;
        const match = disposition.match(/filename="([^"]+)"/i);
        if (match && match[1]) filename = match[1];

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        showToast('密钥已生成并下载', 'success');
        return true;
    } catch (error) {
        console.error('Failed to reset ssh key:', error);
        showToast(error.message || '重置密钥失败', 'error');
        return false;
    }
}

// Fetch all groups
async function fetchGroups() {
    try {
        const response = await fetch(`${config.apiBase}/groups`);
        const data = await response.json();
        return data.groups || [];
    } catch (error) {
        console.error('Failed to fetch groups:', error);
        showToast('获取组列表失败', 'error');
        return [];
    }
}

// Create new group
async function createGroup(groupData) {
    try {
        const response = await fetch(`${config.apiBase}/groups`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(groupData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to create group');
        }
        showToast('组创建成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to create group:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Update group
async function updateGroup(groupName, groupData) {
    try {
        const response = await fetch(`${config.apiBase}/groups/${groupName}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(groupData)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update group');
        }
        showToast('组更新成功', 'success');
        return true;
    } catch (error) {
        console.error('Failed to update group:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Delete group
async function deleteGroup(groupName) {
    try {
        const response = await fetch(`${config.apiBase}/groups/${groupName}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to delete group');
        }
        showToast(`组 "${groupName}" 已删除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to delete group:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Add user to group
async function addUserToGroup(username, groupName) {
    try {
        const response = await fetch(`${config.apiBase}/groups/add-member`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, group_name: groupName })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to add user to group');
        }
        showToast(`用户已添加到组 "${groupName}"`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to add user to group:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// Remove user from group
async function removeUserFromGroup(username, groupName) {
    try {
        const response = await fetch(`${config.apiBase}/groups/remove-member`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, group_name: groupName })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to remove user from group');
        }
        showToast(`用户已从组 "${groupName}" 中移除`, 'success');
        return true;
    } catch (error) {
        console.error('Failed to remove user from group:', error);
        showToast(error.message, 'error');
        return false;
    }
}

// ==================== UI Functions ====================

function enableAutoRefresh() {
    if (autoRefreshTimer) return;

    autoRefreshTimer = setInterval(() => {
        // Refresh page data without full reload
        console.log('Auto-refreshing data...');
        location.reload();
    }, config.autoRefreshInterval);

    showToast('已启用自动刷新', 'success');
}

function disableAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
        showToast('已关闭自动刷新', 'info');
    }
}

// Toast notifications
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 px-6 py-3 rounded-lg shadow-lg text-white text-sm font-medium z-50 animate-slide-in`;

    const colors = {
        success: 'bg-green-600',
        error: 'bg-red-600',
        warning: 'bg-yellow-600',
        info: 'bg-gray-700'
    };

    toast.classList.add(colors[type] || colors.info);
    toast.textContent = message;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Modal stack for managing multiple modals
let modalStack = [];

// Generic Modal
function showModal(title, content, options = {}) {
    // Generate unique modal ID
    const modalId = 'modal_' + Date.now() + '_' + Math.random().toString(36).slice(2, 11);

    // Create modal backdrop
    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = modalId;
    backdrop.style.zIndex = 50 + modalStack.length; // Increment z-index for stacked modals

    // Create modal content
    const modal = document.createElement('div');
    const maxWidth = options.maxWidth || 'max-w-2xl';
    modal.className = `bg-white rounded-lg p-6 ${maxWidth} w-full mx-4 shadow-xl max-h-90vh overflow-y-auto`;

    modal.innerHTML = `
        <div class="flex justify-between items-center mb-4">
            <h3 class="text-xl font-semibold text-gray-900">${title}</h3>
            <button onclick="closeModal('${modalId}')" class="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
        </div>
        <div class="text-gray-700">
            ${content}
        </div>
        <div class="flex justify-end mt-6">
            <button onclick="closeModal('${modalId}')" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                关闭
            </button>
        </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Add to modal stack
    modalStack.push(modalId);

    // Close on backdrop click
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeModal(modalId);
    });

    // Return modalId so it can be closed later
    return modalId;
}

function closeModal(modalId) {
    // If no modalId provided, close the topmost modal
    if (!modalId && modalStack.length > 0) {
        modalId = modalStack[modalStack.length - 1];
    }

    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => {
            modal.remove();
            // Remove from stack
            const index = modalStack.indexOf(modalId);
            if (index > -1) {
                modalStack.splice(index, 1);
            }
        }, 200);
    }
}

// Confirmation Modal
function showConfirmModal(title, message, onConfirm) {
    // Create modal backdrop
    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'confirmModal';

    // Create modal content
    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl';

    modal.innerHTML = `
        <h3 class="text-xl font-semibold text-gray-900 mb-3">${title}</h3>
        <p class="text-gray-600 mb-6">${message}</p>
        <div class="flex gap-3 justify-end">
            <button onclick="closeConfirmModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                取消
            </button>
            <button onclick="confirmAction()" class="px-4 py-2 text-white rounded-md transition" style="background-color: ${config.primaryColor};">
                确认
            </button>
        </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Store callback
    window._confirmCallback = onConfirm;

    // Close on backdrop click
    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeConfirmModal();
    });
}

function closeConfirmModal() {
    const modal = document.getElementById('confirmModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
    window._confirmCallback = null;
}

function confirmAction() {
    if (window._confirmCallback) {
        window._confirmCallback();
    }
    closeConfirmModal();
}

// Delete user with confirmation
function deleteUser(username) {
    showConfirmModal(
        '删除用户',
        `确定要删除用户 "${username}" 吗？此操作无法撤销。`,
        async () => {
            const success = await deleteUserAPI(username);
            if (success) {
                // Reload the page to refresh the user list
                setTimeout(() => location.reload(), 1000);
            }
        }
    );
}

// Cancel job with confirmation
function cancelJob(jobId) {
    showConfirmModal(
        '取消作业',
        `确定要取消作业 #${jobId} 吗？`,
        () => {
            // TODO: Implement actual cancel API call
            showToast(`作业 #${jobId} 已取消`, 'success');
            console.log('Cancel job:', jobId);
        }
    );
}

// Table search functionality
function setupTableSearch(inputId, tableId) {
    const input = document.getElementById(inputId);
    const table = document.getElementById(tableId);

    if (!input || !table) return;

    input.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();
        const rows = table.querySelectorAll('tbody tr');

        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(searchTerm) ? '' : 'none';
        });
    });
}

// Filter table by select dropdown
function setupTableFilter(selectId, tableId, columnIndex) {
    const select = document.getElementById(selectId);
    const table = document.getElementById(tableId);

    if (!select || !table) return;

    select.addEventListener('change', (e) => {
        const filterValue = e.target.value.toLowerCase();
        const rows = table.querySelectorAll('tbody tr');

        rows.forEach(row => {
            const cell = row.cells[columnIndex];
            if (!cell) return;

            const cellText = cell.textContent.toLowerCase();
            row.style.display = !filterValue || cellText.includes(filterValue) ? '' : 'none';
        });
    });
}

// Sort table by column
function sortTable(tableId, columnIndex, ascending = true) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    rows.sort((a, b) => {
        const aValue = a.cells[columnIndex].textContent.trim();
        const bValue = b.cells[columnIndex].textContent.trim();

        // Try numeric comparison first
        const aNum = parseFloat(aValue);
        const bNum = parseFloat(bValue);

        if (!isNaN(aNum) && !isNaN(bNum)) {
            return ascending ? aNum - bNum : bNum - aNum;
        }

        // Fallback to string comparison
        return ascending ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
    });

    rows.forEach(row => tbody.appendChild(row));
}

// Make table headers clickable for sorting
function enableTableSorting(tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const headers = table.querySelectorAll('thead th');
    const sortStates = {}; // Track sort direction per column

    headers.forEach((header, index) => {
        header.style.cursor = 'pointer';
        header.style.userSelect = 'none';
        sortStates[index] = true; // true = ascending

        header.addEventListener('click', () => {
            // Toggle sort direction
            sortStates[index] = !sortStates[index];
            sortTable(tableId, index, sortStates[index]);

            // Update header indicator
            headers.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
            header.classList.add(sortStates[index] ? 'sorted-asc' : 'sorted-desc');
        });
    });
}

// Refresh button with loading state
function refreshData(buttonId) {
    const button = document.getElementById(buttonId);
    if (!button) return;

    button.disabled = true;
    button.textContent = '刷新中...';

    // Simulate API call
    setTimeout(() => {
        location.reload();
    }, 500);
}

// Create user modal
function showCreateUserModal() {
    const backdrop = document.createElement('div');
    backdrop.className = 'fixed inset-0 bg-opacity-50 flex items-center justify-center z-50';
    backdrop.id = 'createUserModal';

    const modal = document.createElement('div');
    modal.className = 'bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl';

    modal.innerHTML = `
        <h3 class="text-xl font-semibold text-gray-900 mb-4">创建新用户</h3>
        <form id="createUserForm" class="space-y-4">
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">用户名</label>
                <input type="text" name="username" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="username">
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">UID</label>
                <input type="number" name="uid" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="10001">
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">GID</label>
                <input type="number" name="gid" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="10001">
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">家目录</label>
                <input type="text" name="home" required class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="/home/username">
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Shell</label>
                <select name="shell" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                    <option value="/bin/bash">/bin/bash</option>
                    <option value="/bin/zsh">/bin/zsh</option>
                    <option value="/bin/sh">/bin/sh</option>
                </select>
            </div>
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">密码 (可选)</label>
                <input type="password" name="password" class="w-full px-3 py-2 border border-gray-300 rounded-md" placeholder="留空则不设置密码">
            </div>
            <div class="flex gap-3 justify-end mt-6">
                <button type="button" onclick="closeCreateUserModal()" class="px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition">
                    取消
                </button>
                <button type="submit" class="px-4 py-2 text-white rounded-md transition" style="background-color: ${config.primaryColor};">
                    创建
                </button>
            </div>
        </form>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Handle form submission
    document.getElementById('createUserForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const userData = {
            username: formData.get('username'),
            uid: parseInt(formData.get('uid')),
            gid: parseInt(formData.get('gid')),
            home: formData.get('home'),
            shell: formData.get('shell'),
            password: formData.get('password') || null
        };

        const success = await createUser(userData);
        if (success) {
            closeCreateUserModal();
            setTimeout(() => location.reload(), 1000);
        }
    });

    backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) closeCreateUserModal();
    });
}

function closeCreateUserModal() {
    const modal = document.getElementById('createUserModal');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => modal.remove(), 200);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('OpenHPC Web Portal initialized');

    // Add smooth scroll behavior
    document.documentElement.style.scrollBehavior = 'smooth';

    // Initialize tooltips (if any elements have data-tooltip attribute)
    initializeTooltips();
});

// Simple tooltip system
function initializeTooltips() {
    const elementsWithTooltip = document.querySelectorAll('[data-tooltip]');

    elementsWithTooltip.forEach(element => {
        element.addEventListener('mouseenter', (e) => {
            const tooltip = document.createElement('div');
            tooltip.className = 'fixed bg-gray-900 text-white text-xs px-3 py-2 rounded shadow-lg z-50';
            tooltip.textContent = element.getAttribute('data-tooltip');
            tooltip.id = 'active-tooltip';

            document.body.appendChild(tooltip);

            const rect = element.getBoundingClientRect();
            tooltip.style.left = rect.left + (rect.width / 2) - (tooltip.offsetWidth / 2) + 'px';
            tooltip.style.top = rect.top - tooltip.offsetHeight - 5 + 'px';
        });

        element.addEventListener('mouseleave', () => {
            const tooltip = document.getElementById('active-tooltip');
            if (tooltip) tooltip.remove();
        });
    });
}

// Export functions for global use
window.showModal = showModal;
window.closeModal = closeModal;
window.showToast = showToast;
window.showConfirmModal = showConfirmModal;
window.closeConfirmModal = closeConfirmModal;
window.confirmAction = confirmAction;
window.deleteUser = deleteUser;
window.cancelJob = cancelJob;
window.setupTableSearch = setupTableSearch;
window.setupTableFilter = setupTableFilter;
window.sortTable = sortTable;
window.enableTableSorting = enableTableSorting;
window.refreshData = refreshData;
window.showCreateUserModal = showCreateUserModal;
window.closeCreateUserModal = closeCreateUserModal;
window.enableAutoRefresh = enableAutoRefresh;
window.disableAutoRefresh = disableAutoRefresh;

// Export LDAP API functions
window.fetchLDAPStatus = fetchLDAPStatus;
window.fetchUsers = fetchUsers;
window.fetchUser = fetchUser;
window.createUser = createUser;
window.deleteUserAPI = deleteUserAPI;
window.disableUserAPI = disableUserAPI;
window.resetUserSshKey = resetUserSshKey;
window.fetchGroups = fetchGroups;
window.addUserToGroup = addUserToGroup;
window.removeUserFromGroup = removeUserFromGroup;

// Export Slurm API functions
window.fetchAccounts = fetchAccounts;
window.createAccount = createAccount;
window.updateAccount = updateAccount;
window.deleteAccount = deleteAccount;
window.fetchAssociations = fetchAssociations;
window.createAssociation = createAssociation;
window.updateAssociation = updateAssociation;
window.deleteAssociation = deleteAssociation;
window.setAssociationTRESMinutes = setAssociationTRESMinutes;
window.fetchPartitions = fetchPartitions;
window.fetchNodes = fetchNodes;
window.fetchNodesConfig = fetchNodesConfig;
window.createPartition = createPartition;
window.updatePartition = updatePartition;
window.deletePartitionAPI = deletePartitionAPI;
window.createNode = createNode;
window.updateNodeConfig = updateNodeConfig;
window.deleteNodeConfigAPI = deleteNodeConfigAPI;
window.drainNodeAPI = drainNodeAPI;
window.resumeNodeAPI = resumeNodeAPI;
