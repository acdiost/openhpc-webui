// Run with: node --test tests/test_table_pagination.js
const {test} = require('node:test');
const assert = require('node:assert/strict');

const {getPaginationState} = require('../static/table_pagination.js');

test('pagination state handles an empty list', () => {
    assert.deepEqual(getPaginationState(0, 1, 20), {
        page: 1,
        pageSize: 20,
        total: 0,
        totalPages: 1,
        start: 0,
        end: 0,
    });
});

test('pagination state clamps page and reports the final range', () => {
    assert.deepEqual(getPaginationState(45, 99, 20), {
        page: 3,
        pageSize: 20,
        total: 45,
        totalPages: 3,
        start: 40,
        end: 45,
    });
});

test('pagination state normalizes invalid numeric input', () => {
    assert.deepEqual(getPaginationState(-5, 0, 0), {
        page: 1,
        pageSize: 20,
        total: 0,
        totalPages: 1,
        start: 0,
        end: 0,
    });
});
