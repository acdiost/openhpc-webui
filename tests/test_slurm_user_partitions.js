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

async function page(initialAssociations) {
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
            let data = {};
            if (url === '/api/slurm/users') data = {users: [JSON.parse(JSON.stringify(user))]};
            else if (url === '/api/slurm/partitions') data = {partitions: [{name: 'cpu'}, {name: 'gpu'}]};
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
    assert.equal(el('slurmUsersBody').children[0].children[4].textContent, 'gpu');

    await el('slurmUsersBody').children[0].children[7].children[0].children[1].fire('click');
    assert.equal(el('slurmPartitionModal').style.display, 'flex');
    assert.equal(el('slurmPartitionAccount').value, 'research');

    el('slurmPartitionChoice').value = 'cpu';
    await el('slurmPartitionForm').fire('submit');
    assert.equal(el('slurmUsersBody').children[0].children[4].textContent, 'cpu, gpu');
    const creation = calls.find((call) => call.options.method === 'POST');
    assert.deepEqual(JSON.parse(creation.options.body), {
        username: 'alice', account: 'research', partition: 'cpu',
    });

    const gpuRow = el('slurmPartitionList').children.find((row) => row.children[0].textContent === 'gpu');
    await gpuRow.children[1].fire('click');
    assert.equal(el('slurmUsersBody').children[0].children[4].textContent, 'cpu');
    assert.ok(calls.some((call) => call.url.endsWith('/research/alice?partition=gpu') && call.options.method === 'DELETE'));
});

test('global association takes precedence in the displayed partition column', async () => {
    const {elements} = await page([
        {account: 'research', partition: ''},
        {account: 'research', partition: 'gpu'},
    ]);
    assert.equal(elements.get('slurmUsersBody').children[0].children[4].textContent, '全局关联');
});

test('global association can be added and removed with the exact selector', async () => {
    const {elements, calls} = await page([{account: 'research', partition: 'gpu'}]);
    const el = (id) => elements.get(id);
    await el('slurmUsersBody').children[0].children[7].children[0].children[1].fire('click');

    el('slurmPartitionChoice').value = '*';
    await el('slurmPartitionForm').fire('submit');
    assert.equal(el('slurmUsersBody').children[0].children[4].textContent, '全局关联');
    assert.equal(JSON.parse(calls.find((call) => call.options.method === 'POST').options.body).partition, null);

    const globalRow = el('slurmPartitionList').children.find((row) => row.children[0].textContent.includes('全局关联'));
    await globalRow.children[1].fire('click');
    assert.equal(el('slurmUsersBody').children[0].children[4].textContent, 'gpu');
    assert.ok(calls.some((call) => call.url.endsWith('/research/alice?partition=') && call.options.method === 'DELETE'));
});
