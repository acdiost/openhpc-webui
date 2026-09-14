(() => {
    let users = [];
    let editing = null;
    let busy = false;
    let loadVersion = 0;
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
    function render() {
        const query = el('slurmUserSearch').value.trim().toLowerCase();
        const visible = users.filter((user) => user.username.toLowerCase().includes(query));
        el('slurmUserCount').textContent = `${visible.length} / ${users.length} 个用户`;
        const body = el('slurmUsersBody');
        body.replaceChildren();
        for (const user of visible) {
            const row = document.createElement('tr');
            for (const value of [user.username, user.cluster, user.default_account || '—', user.accounts.join(', ') || '—', user.admin_level || 'None', user.associations.length]) {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.append(cell);
            }
            const cell = document.createElement('td');
            cell.className = 'col-actions';
            const actions = document.createElement('div');
            actions.className = 'data-table-actions';
            for (const [label, handler] of [['默认账户', () => openForm(user)], ['删除', () => removeUser(user)]]) {
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
            cell.colSpan = 7; cell.textContent = query ? '没有匹配的用户' : '当前集群暂无 Slurm 用户';
            const row = document.createElement('tr'); row.append(cell); body.append(row);
        }
    }
    async function load() {
        const version = ++loadVersion;
        errorAt('slurmUserError');
        el('slurmUserCount').textContent = '加载中…';
        try {
            const data = await request('/api/slurm/users');
            if (version !== loadVersion) return;
            users = data.users; render();
        } catch (error) {
            if (version !== loadVersion) return;
            users = []; el('slurmUsersBody').replaceChildren();
            el('slurmUserCount').textContent = '加载失败';
            errorAt('slurmUserError', error.message);
        }
    }
    function setBusy(value) {
        busy = value;
        for (const id of ['saveSlurmUser', 'createSlurmUser', 'closeSlurmUser', 'cancelSlurmUser']) el(id).disabled = value;
        el('slurmUsersBody').querySelectorAll('button').forEach((button) => { button.disabled = value; });
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
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeForm(); });
    load();
})();
