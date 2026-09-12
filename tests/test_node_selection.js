// Run with: node --test tests/test_node_selection.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
    const elements = new Map();
    const getElementById = id => {
        if (!elements.has(id)) elements.set(id, {querySelectorAll: () => []});
        return elements.get(id);
    };
    const calls = [];
    const context = {
        window: {},
        document: {getElementById, querySelectorAll: () => [], addEventListener() {}},
        showToast() {},
        showConfirmModal(title, message, callback) {context.confirm = callback;},
        drainNodeAPI: async name => {calls.push(name); return name !== 'n2';},
        resumeNodeAPI: async name => {calls.push(name); return true;},
        deleteNodeConfigAPI: async name => {calls.push(name); return true;},
        fetchNodes: async () => [{name: 'n1'}, {name: 'n2'}],
        fetchNodesConfig: async () => [{name: 'n1'}, {name: 'n2'}],
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('static/nodes.js', 'utf8'), context);
    vm.runInContext("allNodesConfig = [{name:'n1'}, {name:'n2'}]", context);
    context.renderNodesTable([{name: 'n1'}, {name: 'n2'}]);
    return {context, elements, calls};
}

test('selection controls handle none, one, all and stale selections', () => {
    const {context: c, elements: e} = page();
    assert.equal(e.get('nodeEditSelected').disabled, true);
    c.selectNode('n1', true);
    assert.equal(e.get('selectAllNodes').indeterminate, true);
    assert.equal(e.get('nodeEditSelected').disabled, false);
    c.selectAllNodes(true);
    assert.equal(e.get('selectAllNodes').checked, true);
    assert.equal(e.get('nodeEditSelected').disabled, true);
    assert.equal(e.get('nodeDeleteSelected').disabled, false);
    c.renderNodesTable([{name: 'n2'}]);
    assert.equal(e.get('nodeSelectionCount').textContent, '已选 1 个节点');
    c.renderNodesTable([]);
    assert.equal(e.get('selectAllNodes').disabled, true);
    assert.equal(e.get('nodeSelectionCount').textContent, '已选 0 个节点');
});

test('runtime-only nodes cannot be edited or deleted', () => {
    const {context: c, elements: e, calls} = page();
    c.renderNodesTable([{name: 'runtime'}]);
    c.selectNode('runtime', true);
    assert.equal(e.get('nodeEditSelected').disabled, true);
    assert.equal(e.get('nodeDeleteSelected').disabled, true);
    c.runSelectedNodeAction('delete');
    assert.equal(c.confirm, undefined);
    assert.equal(calls.length, 0);
});

test('batch waits for confirmation and retains only failures', async () => {
    const {context: c, elements: e, calls} = page();
    c.selectAllNodes(true);
    c.runSelectedNodeAction('drain');
    assert.deepEqual(calls, []);
    await c.confirm();
    assert.deepEqual(calls, ['n1', 'n2']);
    assert.equal(e.get('nodeSelectionCount').textContent, '已选 1 个节点');
    c.runSelectedNodeAction('resume');
    await c.confirm();
    assert.deepEqual(calls, ['n1', 'n2', 'n2']);
    assert.equal(e.get('nodeSelectionCount').textContent, '已选 0 个节点');
});

test('batch uses confirmed snapshot and ignores repeated execution', async () => {
    const {context: c, calls} = page();
    let finish;
    c.deleteNodeConfigAPI = name => {
        calls.push(name);
        return new Promise(resolve => {finish = resolve;});
    };
    c.selectNode('n1', true);
    c.runSelectedNodeAction('delete');
    c.selectAllNodes(true);
    const callback = c.confirm;
    const running = callback();
    await callback();
    assert.deepEqual(calls, ['n1']);
    finish(true);
    await running;
});
