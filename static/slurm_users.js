(() => {
    let users = [];
    let editing = null;
    let partitionUsername = null;
    let availablePartitions = [];
    let busy = false;
    let loadVersion = 0;
    let partitionEditorVersion = 0;
    let partitionCatalogVersion = 0;
    let appliedPartitionCatalogVersion = 0;
    const el = (id) => document.getElementById(id);
    function errorAt(id, message = '') {
        el(id).textContent = message;
        el(id).hidden = !message;
    }
    async function request(url, options = {}) {
        const response = await fetch(url, options);
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('服务返回异常，请刷新或重新登录'); }
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请检查输入或重新登录');
        return data;
    }
    function allowedPartitions(user) {
        const associations = user.associations || [];
        if (associations.some((assoc) => !assoc.partition)) {
            return availablePartitions.length
                ? `${[...new Set(availablePartitions)].sort().join(', ')}（全局）`
                : '全部分区（全局）';
        }
        return [...new Set(associations.map((assoc) => assoc.partition).filter(Boolean))].sort().join(', ') || '—';
    }
    function render() {
        const query = el('slurmUserSearch').value.trim().toLowerCase();
        const visible = users.filter((user) => user.username.toLowerCase().includes(query));
        el('slurmUserCount').textContent = `${visible.length} / ${users.length} 个用户`;
        const body = el('slurmUsersBody');
        body.replaceChildren();
        for (const user of visible) {
            const row = document.createElement('tr');
            const values = [user.username, user.cluster, allowedPartitions(user), user.default_account || '—', user.accounts.join(', ') || '—', user.admin_level || 'None', user.associations.length];
            for (const [index, value] of values.entries()) {
                const cell = document.createElement('td');
                if (index === 2) {
                    cell.title = value;
                    const content = document.createElement('div');
                    content.className = 'slurm-partition-cell';
                    const label = document.createElement('span');
                    label.className = 'slurm-partition-value';
                    label.textContent = value;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'btn btn-secondary slurm-partition-edit';
                    button.textContent = '修改';
                    button.setAttribute('aria-label', `修改 ${user.username} 允许的分区`);
                    button.disabled = busy;
                    button.addEventListener('click', () => openPartitionEditor(user));
                    content.append(label, button);
                    cell.append(content);
                } else {
                    cell.textContent = value;
                }
                row.append(cell);
            }
            const cell = document.createElement('td');
            cell.className = 'col-actions';
            const actions = document.createElement('div');
            actions.className = 'data-table-actions';
            for (const [label, handler] of [['修改默认账户', () => openForm(user)], ['删除', () => removeUser(user)]]) {
                const button = document.createElement('button');
                button.className = 'btn btn-secondary';
                button.textContent = label;
                button.disabled = busy;
                button.addEventListener('click', handler);
                actions.append(button);
            }
            cell.append(actions); row.append(cell); body.append(row);
        }
        if (!visible.length) {
            const cell = document.createElement('td');
            cell.colSpan = 8; cell.textContent = query ? '没有匹配的用户' : '当前集群暂无 Slurm 用户';
            const row = document.createElement('tr'); row.append(cell); body.append(row);
        }
        if (partitionUsername && el('slurmPartitionModal').style.display === 'flex') renderPartitionEditor();
    }
    async function load() {
        const version = ++loadVersion;
        const catalogVersion = ++partitionCatalogVersion;
        let usersLoaded = false;
        errorAt('slurmUserError');
        el('slurmUserCount').textContent = '加载中…';
        request('/api/slurm/partitions').then((data) => {
            if (version !== loadVersion || catalogVersion < appliedPartitionCatalogVersion) return;
            appliedPartitionCatalogVersion = catalogVersion;
            availablePartitions = (data.partitions || []).map((item) => item.name).filter(Boolean);
            if (usersLoaded) render();
        }).catch(() => {}); // Partition discovery is optional; do not hold up the user list.
        try {
            const data = await request('/api/slurm/users');
            if (version !== loadVersion) return;
            users = data.users;
            usersLoaded = true;
            render();
        } catch (error) {
            if (version !== loadVersion) return;
            users = []; el('slurmUsersBody').replaceChildren();
            el('slurmUserCount').textContent = '加载失败';
            errorAt('slurmUserError', error.message);
        }
    }
    function setBusy(value) {
        busy = value;
        for (const id of ['saveSlurmUser', 'createSlurmUser', 'closeSlurmUser', 'cancelSlurmUser', 'closeSlurmPartition', 'cancelSlurmPartition', 'slurmPartitionAccount', 'slurmPartitionChoice']) el(id).disabled = value;
        el('saveSlurmPartition').disabled = value || !el('slurmPartitionChoice').value;
        el('slurmUsersBody').querySelectorAll('button').forEach((button) => { button.disabled = value; });
        el('slurmPartitionList').querySelectorAll('button').forEach((button) => { button.disabled = value; });
    }
    function renderPartitionEditor() {
        const user = users.find((item) => item.username === partitionUsername);
        if (!user) { closePartitionEditor(true); return; }
        const accountSelect = el('slurmPartitionAccount');
        if (!user.accounts.includes(accountSelect.value)) {
            accountSelect.replaceChildren();
            for (const name of user.accounts) accountSelect.add(new Option(name, name));
            accountSelect.value = user.accounts.includes(user.default_account) ? user.default_account : user.accounts[0];
        }
        const account = accountSelect.value;
        const associations = user.associations.filter((item) => item.account === account);
        const list = el('slurmPartitionList');
        list.replaceChildren();
        for (const assoc of associations) {
            const item = document.createElement('div');
            item.className = 'slurm-partition-item';
            const label = document.createElement('span');
            label.textContent = assoc.partition || '全局关联（不限制分区）';
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'btn btn-secondary';
            button.textContent = '移除'; button.disabled = busy;
            button.addEventListener('click', () => removePartitionAssociation(account, assoc.partition || ''));
            item.append(label, button); list.append(item);
        }
        if (!associations.length) {
            const item = document.createElement('p');
            item.textContent = '该账户暂无分区关联'; list.append(item);
        }
        const existing = new Set(associations.map((item) => item.partition || ''));
        const choice = el('slurmPartitionChoice');
        choice.replaceChildren();
        choice.add(new Option('请选择分区', ''));
        if (!existing.has('')) choice.add(new Option('全局关联（不限制分区）', '*'));
        for (const partition of availablePartitions) {
            if (!existing.has(partition)) choice.add(new Option(partition, partition));
        }
        el('saveSlurmPartition').disabled = busy || !choice.value;
    }
    async function openPartitionEditor(user) {
        if (busy) return;
        const editorVersion = ++partitionEditorVersion;
        const version = loadVersion;
        const catalogVersion = ++partitionCatalogVersion;
        partitionUsername = user.username;
        errorAt('slurmPartitionError');
        el('slurmPartitionTitle').textContent = `修改允许的分区：${user.username}`;
        const accountSelect = el('slurmPartitionAccount');
        accountSelect.replaceChildren();
        for (const account of user.accounts) accountSelect.add(new Option(account, account));
        accountSelect.value = user.accounts.includes(user.default_account) ? user.default_account : user.accounts[0];
        el('slurmPartitionModal').style.display = 'flex';
        renderPartitionEditor();
        accountSelect.focus();
        try {
            const data = await request('/api/slurm/partitions');
            if (partitionEditorVersion !== editorVersion || version !== loadVersion || partitionUsername !== user.username) return;
            if (catalogVersion >= appliedPartitionCatalogVersion) {
                appliedPartitionCatalogVersion = catalogVersion;
                availablePartitions = (data.partitions || []).map((item) => item.name).filter(Boolean);
                render();
            }
        } catch (error) {
            if (partitionEditorVersion === editorVersion && version === loadVersion && partitionUsername === user.username) {
                errorAt('slurmPartitionError', error.message);
            }
        }
    }
    function closePartitionEditor(force = false) {
        if (busy && !force) return;
        ++partitionEditorVersion;
        el('slurmPartitionModal').style.display = 'none';
        partitionUsername = null;
    }
    async function removePartitionAssociation(account, partition) {
        if (busy || !partitionUsername) return;
        const label = partition || '全局关联';
        if (!confirm(`确定移除 ${partitionUsername}/${account} 的“${label}”关联？该关联上的 QoS 和额度也会被删除，可能影响作业。`)) return;
        const username = partitionUsername;
        errorAt('slurmPartitionError');
        setBusy(true);
        try {
            const query = `?partition=${encodeURIComponent(partition)}`;
            await request(`/api/slurm/associations/${encodeURIComponent(account)}/${encodeURIComponent(username)}${query}`, {method: 'DELETE'});
            showToast('分区关联已移除', 'success');
            await load();
        } catch (error) { errorAt('slurmPartitionError', error.message); }
        finally { setBusy(false); }
    }
    async function openForm(user = null) {
        if (busy) return;
        editing = user;
        el('slurmUserForm').reset();
        errorAt('slurmUserFormError');
        el('slurmUserModalTitle').textContent = user ? '修改默认账户' : '创建 Slurm 用户';
        el('slurmUsername').value = user ? user.username : '';
        el('slurmUsername').disabled = !!user;
        const select = el('slurmDefaultAccount');
        select.replaceChildren();
        el('slurmUserModal').style.display = 'flex';
        setBusy(true);
        try {
            const accounts = user ? user.accounts : (await request('/api/slurm/accounts')).accounts.map((account) => account.name);
            select.add(new Option('请选择账户', ''));
            for (const account of accounts) select.add(new Option(account, account));
            if (user) select.value = user.default_account;
            if (!accounts.length) errorAt('slurmUserFormError', '没有可选账户，请先创建 Slurm 账户或添加用户关联');
        } catch (error) { errorAt('slurmUserFormError', error.message); }
        finally { setBusy(false); (user ? select : el('slurmUsername')).focus(); }
    }
    function closeForm() {
        if (busy) return;
        el('slurmUserModal').style.display = 'none';
        el('createSlurmUser').focus();
    }
    async function removeUser(user) {
        if (busy || !confirm(`删除 Slurm 用户“${user.username}”在集群“${user.cluster}”中的全部账户和分区关联？可能影响排队或运行中的作业。LDAP / 系统登录用户不会删除。`)) return;
        setBusy(true);
        try {
            await request(`/api/slurm/users/${encodeURIComponent(user.username)}`, {method: 'DELETE'});
            showToast('已移除用户在当前集群中的全部关联', 'success');
            await load();
        } catch (error) { errorAt('slurmUserError', error.message); }
        finally { setBusy(false); }
    }
    el('slurmUserForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (busy) return;
        errorAt('slurmUserFormError');
        const account = el('slurmDefaultAccount').value;
        const payload = editing ? {default_account: account} : {username: el('slurmUsername').value.trim(), account};
        setBusy(true);
        try {
            const url = editing ? `/api/slurm/users/${encodeURIComponent(editing.username)}` : '/api/slurm/users';
            await request(url, {method: editing ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
            el('slurmUserModal').style.display = 'none';
            showToast('Slurm 用户已保存', 'success');
            await load();
        } catch (error) { errorAt('slurmUserFormError', error.message); }
        finally { setBusy(false); }
    });
    el('createSlurmUser').addEventListener('click', () => openForm());
    el('closeSlurmUser').addEventListener('click', closeForm);
    el('cancelSlurmUser').addEventListener('click', closeForm);
    el('refreshSlurmUsers').addEventListener('click', load);
    el('slurmUserSearch').addEventListener('input', render);
    el('slurmPartitionAccount').addEventListener('change', renderPartitionEditor);
    el('slurmPartitionChoice').addEventListener('change', () => {
        el('saveSlurmPartition').disabled = busy || !el('slurmPartitionChoice').value;
    });
    el('slurmPartitionForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (busy || !partitionUsername) return;
        const account = el('slurmPartitionAccount').value;
        const choice = el('slurmPartitionChoice').value;
        if (!account || !choice) return;
        const user = users.find((item) => item.username === partitionUsername);
        const hasGlobal = user?.associations.some((item) => item.account === account && !item.partition);
        if (choice !== '*' && hasGlobal && !confirm('该账户已有全局关联，新增分区关联不会收紧权限。仍要添加吗？')) return;
        if (choice === '*' && user?.associations.some((item) => item.account === account && item.partition) && !confirm('新增全局关联后，该账户现有的分区限制将不再生效。仍要添加吗？')) return;
        errorAt('slurmPartitionError');
        setBusy(true);
        try {
            await request('/api/slurm/associations', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: partitionUsername, account, partition: choice === '*' ? null : choice}),
            });
            showToast('分区关联已添加', 'success');
            await load();
        } catch (error) { errorAt('slurmPartitionError', error.message); }
        finally { setBusy(false); }
    });
    el('closeSlurmPartition').addEventListener('click', () => closePartitionEditor());
    el('cancelSlurmPartition').addEventListener('click', () => closePartitionEditor());
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeForm(); closePartitionEditor(); } });
    load();
})();
