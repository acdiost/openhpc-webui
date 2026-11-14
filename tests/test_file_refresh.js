// Run with: node --test tests/test_file_refresh.js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function filePage() {
    const elements = new Map();
    const timers = new Map();
    const calls = [];
    const responses = [];
    let timerId = 0;
    const document = {
        hidden: false,
        addEventListener() {},
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, {
                value: '', style: {}, textContent: '',
                selectionStart: 0, selectionEnd: 0, selectionDirection: 'none',
                scrollTop: 0, scrollLeft: 0,
                classList: {toggle() {}},
                focus() {},
                setAttribute(name, value) { this[name] = value; },
                setSelectionRange(start, end, direction) {
                    this.selectionStart = Math.min(start, this.value.length);
                    this.selectionEnd = Math.min(end, this.value.length);
                    this.selectionDirection = direction;
                },
            });
            return elements.get(id);
        },
    };
    const context = vm.createContext({
        document, URLSearchParams,
        setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
        clearTimeout(id) { timers.delete(id); },
        requestAnimationFrame() { return 1; },
        cancelAnimationFrame() {},
        showToast() {},
        async fetch(url, options) {
            calls.push({url, options});
            const response = responses.shift();
            if (!response) throw new Error('Unexpected request');
            return await response;
        },
    });
    const template = fs.readFileSync('templates/files.html', 'utf8');
    const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
        .replaceAll('{{ initial_path | tojson }}', '"/"');
    vm.runInContext(script, context);
    const reply = (content) => ({ok: true, json: async () => ({content, size: content.length, max_bytes: 2048})});
    return {
        context, document, calls, responses, reply,
        editor: document.getElementById('fileEditor'),
        status: document.getElementById('fileRefreshStatus'),
        async open(path, content) {
            responses.push(reply(content));
            await context.openEditor({path, name: path.slice(1)});
        },
        start(seconds = 3) {
            document.getElementById('fileRefreshInterval').value = String(seconds);
            context.toggleEditorRefresh();
        },
        async tick() {
            const entry = [...timers].find(([, timer]) => timer.delay > 0);
            assert.ok(entry, 'enabled auto refresh must schedule a refresh');
            timers.delete(entry[0]);
            await entry[1].callback();
        },
        refreshTimers() { return [...timers.values()].filter((timer) => timer.delay > 0).length; },
        nextDelay() { return [...timers.values()].find((timer) => timer.delay > 0)?.delay; },
    };
}

test('updates an open file while preserving selection and scroll, then stops on close', async () => {
    const page = filePage();
    await page.open('/log.txt', 'first line\n');
    page.start();
    Object.assign(page.editor, {selectionStart: 1, selectionEnd: 4, selectionDirection: 'backward', scrollTop: 40, scrollLeft: 12});
    page.responses.push(page.reply('first line\nsecond line\n'));
    await page.tick();
    assert.equal(page.editor.value, 'first line\nsecond line\n');
    assert.deepEqual([page.editor.selectionStart, page.editor.selectionEnd, page.editor.selectionDirection, page.editor.scrollTop, page.editor.scrollLeft], [1, 4, 'backward', 40, 12]);
    page.context.closeFileDialog();
    assert.equal(page.document.getElementById('fileDialog').style.display, 'none');
    assert.equal(page.refreshTimers(), 0);
});

test('pauses for unsaved edits and resumes after edits are undone', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start();
    page.editor.value = 'local edits';
    page.context.scheduleLineNumbers();
    await page.tick();
    assert.equal(page.editor.value, 'local edits');
    assert.equal(page.calls.length, 1);
    assert.match(page.status.textContent, /未保存/);
    page.context.closeFileDialog();
    assert.equal(page.document.getElementById('unsavedDialog').style.display, 'flex');
    page.context.hideUnsavedDialog();
    page.editor.value = 'original';
    page.responses.push(page.reply('external change'));
    await page.tick();
    assert.equal(page.editor.value, 'external change');
});

test('does not overwrite edits made while a refresh is in flight', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start();
    let resolve;
    page.responses.push(new Promise((done) => { resolve = done; }));
    const pending = page.tick();
    page.editor.value = 'typed while waiting';
    resolve(page.reply('external change'));
    await pending;
    assert.equal(page.editor.value, 'typed while waiting');
    page.context.closeFileDialog();
    assert.equal(page.document.getElementById('unsavedDialog').style.display, 'flex');
});

test('an old refresh cannot replace another open file or start another timer', async () => {
    const page = filePage();
    await page.open('/first.txt', 'first');
    page.start();
    let resolve;
    page.responses.push(new Promise((done) => { resolve = done; }));
    const pending = page.tick();
    assert.equal(page.refreshTimers(), 0);
    page.context.forceCloseFileDialog();
    await page.open('/second.txt', 'second');
    assert.equal(page.refreshTimers(), 0);
    page.start();
    resolve(page.reply('stale first'));
    await pending;
    assert.equal(page.editor.value, 'second');
    assert.equal(page.refreshTimers(), 1);
    page.responses.push(page.reply('new second'));
    await page.tick();
    assert.equal(page.editor.value, 'new second');
});

test('skips hidden pages and saving, then resumes reading', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start();
    page.document.hidden = true;
    await page.tick();
    page.document.hidden = false;
    vm.runInContext('dialogSaving = true', page.context);
    await page.tick();
    assert.equal(page.calls.length, 1);
    vm.runInContext('dialogSaving = false', page.context);
    page.responses.push(page.reply('latest'));
    await page.tick();
    assert.equal(page.editor.value, 'latest');
});

test('a failed refresh preserves content and a later success clears the error', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start();
    page.responses.push({ok: false, status: 403, json: async () => ({detail: '权限不足'})});
    await page.tick();
    assert.equal(page.editor.value, 'original');
    assert.match(page.status.textContent, /刷新失败.*权限不足/);
    page.responses.push(page.reply('recovered'));
    await page.tick();
    assert.equal(page.editor.value, 'recovered');
});

test('opening a file waits for manual start, and reopening never starts automatically', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    assert.equal(page.refreshTimers(), 0);
    assert.equal(page.calls.length, 1);
    page.start(7);
    page.responses.push(page.reply('updated'));
    await page.tick();
    assert.equal(page.editor.value, 'updated');
    page.context.closeFileDialog();
    await page.open('/log.txt', 'reopened');
    assert.equal(page.refreshTimers(), 0);
    assert.equal(page.editor.value, 'reopened');
});

test('stopping rejects an in-flight update, and restarting uses the chosen interval', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start(8);
    assert.equal(page.nextDelay(), 8000);
    let resolve;
    page.responses.push(new Promise((done) => { resolve = done; }));
    const pending = page.tick();
    page.context.toggleEditorRefresh();
    page.start(2);
    resolve(page.reply('stale update'));
    await pending;
    assert.equal(page.editor.value, 'original');
    assert.equal(page.refreshTimers(), 1);
    assert.equal(page.nextDelay(), 2000);
    page.responses.push(page.reply('fresh update'));
    await page.tick();
    assert.equal(page.editor.value, 'fresh update');
    page.context.toggleEditorRefresh();
    assert.equal(page.refreshTimers(), 0);
});

test('changing the interval invalidates the old request and schedules only the new interval', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    page.start(3);
    let resolve;
    page.responses.push(new Promise((done) => { resolve = done; }));
    const pending = page.tick();
    page.document.getElementById('fileRefreshInterval').value = '10';
    page.context.changeEditorRefreshInterval();
    resolve(page.reply('old interval response'));
    await pending;
    assert.equal(page.editor.value, 'original');
    assert.equal(page.refreshTimers(), 1);
    assert.equal(page.nextDelay(), 10000);
    page.responses.push(page.reply('new interval response'));
    await page.tick();
    assert.equal(page.editor.value, 'new interval response');
});

test('invalid intervals cannot start refresh and cannot replace an active valid interval', async () => {
    const page = filePage();
    await page.open('/log.txt', 'original');
    for (const invalid of ['', '0', '-1', '1.5', '86401']) {
        page.start(invalid);
        assert.equal(page.refreshTimers(), 0);
    }
    page.start(1);
    page.document.getElementById('fileRefreshInterval').value = '0';
    page.context.changeEditorRefreshInterval();
    assert.equal(page.nextDelay(), 1000);
    page.responses.push(page.reply('updated'));
    await page.tick();
    assert.equal(page.editor.value, 'updated');
});
