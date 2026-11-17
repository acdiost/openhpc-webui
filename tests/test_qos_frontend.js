// Run with: node --test tests/test_qos_frontend.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page() {
    const elements = new Map();
    const calls = [];
    const item = {
        name: 'gpu', description: 'GPU jobs', priority: 50, flags: [],
        max_wall: '2-00:00:00', max_jobs_pu: 4, max_submit_jobs_pu: 8,
        max_tres_pu: 'cpu=64,gres/gpu=2', max_jobs_pa: 16,
        max_submit_jobs_pa: 32, max_tres_pa: 'gres/gpu=8',
    };
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, {
                _value: '', style: {},
                get value() { return this._value; },
                set value(value) { this._value = String(value); },
            });
            return elements.get(id);
        },
        addEventListener() {},
    };
    const context = {
        document, showToast() {},
        async fetch(url, options = {}) {
            calls.push({url, options});
            if (options.method === 'PUT') Object.assign(item, JSON.parse(options.body));
            return {ok: true, json: async () => ({qos: [item]})};
        },
    };
    const source = fs.readFileSync('templates/qos.html', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
    vm.createContext(context);
    vm.runInContext(source, context);
    vm.runInContext(`qosItems = [${JSON.stringify(item)}];`, context);
    context.openQosModal(0);
    return {context, elements, calls, item};
}

test('clearing a configured field sends null and preserves other limits', async () => {
    const {context, elements, calls} = page();
    elements.get('qosMaxJobs').value = '';
    await context.saveQos({preventDefault() {}});
    const request = calls.find(call => call.options.method === 'PUT');
    assert.deepEqual(JSON.parse(request.options.body), {max_jobs_pu: null});
});

test('all limits can become unlimited while retaining description and priority', async () => {
    const {context, calls} = page();
    context.clearQosLimits();
    await context.saveQos({preventDefault() {}});
    const request = calls.find(call => call.options.method === 'PUT');
    assert.deepEqual(JSON.parse(request.options.body), {
        max_wall: null, max_jobs_pu: null, max_submit_jobs_pu: null,
        max_tres_pu: null, max_jobs_pa: null, max_submit_jobs_pa: null, max_tres_pa: null,
    });
});

test('editing a count to zero preserves the zero limit', async () => {
    const {context, elements, calls} = page();
    elements.get('qosMaxSubmit').value = '0';
    await context.saveQos({preventDefault() {}});
    assert.deepEqual(JSON.parse(calls[0].options.body), {max_submit_jobs_pu: 0});
});

test('after saving unlimited limits the modal reopens with empty fields', async () => {
    const {context, elements} = page();
    context.clearQosLimits();
    await context.saveQos({preventDefault() {}});
    await context.loadQos();
    context.openQosModal(0);
    for (const id of ['qosMaxWall', 'qosMaxJobs', 'qosMaxSubmit', 'qosMaxTres',
                      'qosMaxJobsPA', 'qosMaxSubmitPA', 'qosMaxTresPA']) {
        assert.equal(elements.get(id).value, '');
    }
    assert.equal(elements.get('qosDescription').value, 'GPU jobs');
    assert.equal(elements.get('qosPriority').value, '50');
});
