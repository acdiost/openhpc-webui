// Run with: node --test tests/test_slurm_user_partitions.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div') {
        this.tag = tag;
        this.children = [];
        this.listeners = {};
        this.style = {};
        this.value = '';
        this.textContent = '';
    }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this[name] = value; }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; this.value = ''; }
    add(option) {
        this.children.push(option);
        if (this.children.length === 1) this.value = option.value;
    }
    get options() { return this.children; }
    querySelectorAll(tag) {
        return this.children.flatMap((child) => [
            ...(child.tag === tag ? [child] : []),
            ...(child.querySelectorAll ? child.querySelectorAll(tag) : []),
        ]);
    }
    reset() {}
    focus() {}
    async fire(name) {
        return this.listeners[name]?.({preventDefault() {}});
    }
}

async function page(initialAssociations, partitionFailure = false, responses = {}) {
    const elements = new Map();
    const calls = [];
    const user = {
        username: 'alice', cluster: 'cluster', default_account: 'research',
        accounts: ['research'], admin_level: 'None', associations: initialAssociations,
    };
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new Element());
            return elements.get(id);
        },
        createElement(tag) { return new Element(tag); },
        addEventListener() {},
    };
    const context = {
        document,
        Option: class { constructor(label, value) { this.label = label; this.value = value; } },
        confirm: () => true,
        showToast() {},
        async fetch(url, options = {}) {
            calls.push({url, options});
            const override = responses[url]?.shift();
            if (override) return override;
            let data = {};
            if (url === '/api/slurm/users') data = {users: [JSON.parse(JSON.stringify(user))]};
            else if (url === '/api/slurm/partitions') {
                if (partitionFailure) return {ok: false, json: async () => ({detail: 'unavailable'})};
                data = {partitions: [{name: 'cpu'}, {name: 'gpu'}]};
            }
            else if (url === '/api/slurm/associations' && options.method === 'POST') {
                const payload = JSON.parse(options.body);
                user.associations.push({account: payload.account, partition: payload.partition || ''});
            } else if (url.startsWith('/api/slurm/associations/') && options.method === 'DELETE') {
                const partition = new URL(url, 'http://localhost').searchParams.get('partition');
                user.associations = user.associations.filter((item) => item.partition !== partition);
            }
            return {ok: true, json: async () => data};
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('static/slurm_users.js', 'utf8'), context);
    await new Promise(setImmediate);
    return {elements, calls};
}

test('renders allowed partitions and adds and removes an account partition', async () => {
    const {elements, calls} = await page([{account: 'research', partition: 'gpu'}]);
    const el = (id) => elements.get(id);
    const partitionLabel = () => el('slurmUsersBody').children[0].children[2].children[0].children[0].textContent;
    assert.equal(partitionLabel(), 'gpu');

    await el('slurmUsersBody').children[0].children[2].children[0].children[1].fire('click');
    assert.equal(el('slurmPartitionModal').style.display, 'flex');
    assert.equal(el('slurmPartitionAccount').value, 'research');

    el('slurmPartitionChoice').value = 'cpu';
    await el('slurmPartitionForm').fire('submit');
    assert.equal(partitionLabel(), 'cpu, gpu');
    const creation = calls.find((call) => call.options.method === 'POST');
    assert.deepEqual(JSON.parse(creation.options.body), {
        username: 'alice', account: 'research', partition: 'cpu',
    });

    const gpuRow = el('slurmPartitionList').children.find((row) => row.children[0].textContent === 'gpu');
    await gpuRow.children[1].fire('click');
    assert.equal(partitionLabel(), 'cpu');
    assert.ok(calls.some((call) => call.url.endsWith('/research/alice?partition=gpu') && call.options.method === 'DELETE'));
});

test('global association takes precedence in the displayed partition column', async () => {
    const {elements} = await page([
        {account: 'research', partition: ''},
        {account: 'research', partition: 'gpu'},
    ]);
    assert.equal(elements.get('slurmUsersBody').children[0].children[2].children[0].children[0].textContent, 'cpu, gpu（全局）');
});

test('user list remains available when partition catalog cannot be read', async () => {
    const {elements} = await page([{account: 'research', partition: ''}], true);
    assert.equal(elements.get('slurmUsersBody').children[0].children[2].children[0].children[0].textContent, '全部分区（全局）');
});

test('global association can be added and removed with the exact selector', async () => {
    const {elements, calls} = await page([{account: 'research', partition: 'gpu'}]);
    const el = (id) => elements.get(id);
    const partitionLabel = () => el('slurmUsersBody').children[0].children[2].children[0].children[0].textContent;
    await el('slurmUsersBody').children[0].children[2].children[0].children[1].fire('click');

    el('slurmPartitionChoice').value = '*';
    await el('slurmPartitionForm').fire('submit');
    assert.equal(partitionLabel(), 'cpu, gpu（全局）');
    assert.equal(JSON.parse(calls.find((call) => call.options.method === 'POST').options.body).partition, null);

    const globalRow = el('slurmPartitionList').children.find((row) => row.children[0].textContent.includes('全局关联'));
    await globalRow.children[1].fire('click');
    assert.equal(partitionLabel(), 'gpu');
    assert.ok(calls.some((call) => call.url.endsWith('/research/alice?partition=') && call.options.method === 'DELETE'));
});

test('renders users before the catalog resolves and updates the open editor when it arrives', async () => {
    let resolveCatalog;
    const catalog = new Promise((resolve) => { resolveCatalog = resolve; });
    const {elements} = await page([{account: 'research', partition: ''}], false, {
        '/api/slurm/partitions': [catalog, new Promise(() => {})],
    });
    const el = (id) => elements.get(id);
    const row = () => el('slurmUsersBody').children[0];
    const partitionLabel = () => row().children[2].children[0].children[0].textContent;
    assert.equal(el('slurmUserCount').textContent, '1 / 1 个用户');
    assert.equal(partitionLabel(), '全部分区（全局）');
    row().children[2].children[0].children[1].fire('click');
    assert.equal(el('slurmPartitionModal').style.display, 'flex');
    assert.deepEqual(el('slurmPartitionChoice').options.map((option) => option.value), ['']);

    resolveCatalog({ok: true, json: async () => ({partitions: [{name: 'cpu'}, {name: 'gpu'}]})});
    await new Promise(setImmediate);
    assert.equal(partitionLabel(), 'cpu, gpu（全局）');
    assert.deepEqual(el('slurmPartitionChoice').options.map((option) => option.value), ['', 'cpu', 'gpu']);
});

test('an old partition response cannot replace a refreshed catalog', async () => {
    let resolveOld;
    const stale = new Promise((resolve) => { resolveOld = resolve; });
    const catalog = {ok: true, json: async () => ({partitions: [{name: 'fresh'}]})};
    const {elements} = await page([{account: 'research', partition: ''}], false, {
        '/api/slurm/partitions': [stale, catalog],
    });
    const el = (id) => elements.get(id);
    await el('refreshSlurmUsers').fire('click');
    const label = () => el('slurmUsersBody').children[0].children[2].children[0].children[0].textContent;
    assert.equal(label(), 'fresh（全局）');
    resolveOld({ok: true, json: async () => ({partitions: [{name: 'stale'}]})});
    await new Promise(setImmediate);
    assert.equal(label(), 'fresh（全局）');
});

test('refresh keeps the open editor aligned and ignores its older catalog request', async () => {
    let resolveEditor;
    const staleEditor = new Promise((resolve) => { resolveEditor = resolve; });
    const response = (name) => ({ok: true, json: async () => ({partitions: [{name}]})});
    const {elements} = await page([{account: 'research', partition: ''}], false, {
        '/api/slurm/partitions': [response('initial'), staleEditor, response('fresh')],
    });
    const el = (id) => elements.get(id);
    const row = () => el('slurmUsersBody').children[0];
    const label = () => row().children[2].children[0].children[0].textContent;
    row().children[2].children[0].children[1].fire('click');
    await el('refreshSlurmUsers').fire('click');
    assert.equal(label(), 'fresh（全局）');
    assert.deepEqual(el('slurmPartitionChoice').options.map((option) => option.value), ['', 'fresh']);
    resolveEditor(response('stale'));
    await new Promise(setImmediate);
    assert.equal(label(), 'fresh（全局）');
    assert.deepEqual(el('slurmPartitionChoice').options.map((option) => option.value), ['', 'fresh']);
});
