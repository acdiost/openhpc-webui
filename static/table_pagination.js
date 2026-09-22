(function (root) {
    'use strict';

    const paginators = new WeakMap();

    function getPaginationState(total, page, pageSize) {
        const normalizedTotal = Math.max(0, Number.isFinite(Number(total)) ? Math.floor(Number(total)) : 0);
        const normalizedPageSize = Number(pageSize) > 0 ? Math.floor(Number(pageSize)) : 20;
        const totalPages = Math.max(1, Math.ceil(normalizedTotal / normalizedPageSize));
        const normalizedPage = Math.min(
            totalPages,
            Math.max(1, Number.isFinite(Number(page)) ? Math.floor(Number(page)) : 1),
        );
        const start = normalizedTotal === 0 ? 0 : (normalizedPage - 1) * normalizedPageSize;

        return {
            page: normalizedPage,
            pageSize: normalizedPageSize,
            total: normalizedTotal,
            totalPages,
            start,
            end: Math.min(start + normalizedPageSize, normalizedTotal),
        };
    }

    function isPlaceholderRow(row) {
        if (row.cells.length !== 1 || Number(row.cells[0].colSpan) <= 1) return false;
        return !row.querySelector('button, input, select, a[href]');
    }

    function createButton(label, className) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = className;
        button.textContent = label;
        return button;
    }

    function setupTablePagination(table) {
        if (!table || paginators.has(table)) return paginators.get(table);
        const body = table.tBodies[0];
        if (!body) return null;

        let currentPage = 1;
        let pageSize = Number(table.dataset.pageSize) || 20;
        let refreshTimer = null;

        const pagination = document.createElement('nav');
        pagination.className = 'table-pagination';
        pagination.setAttribute('aria-label', `${table.getAttribute('aria-label') || '数据表格'}分页`);

        const pageSizeGroup = document.createElement('label');
        pageSizeGroup.className = 'table-pagination__size';
        pageSizeGroup.append(document.createTextNode('每页显示 '));
        const pageSizeSelect = document.createElement('select');
        pageSizeSelect.setAttribute('aria-label', '每页显示条数');
        for (const size of [20, 50, 100]) {
            const option = document.createElement('option');
            option.value = String(size);
            option.textContent = String(size);
            option.selected = size === pageSize;
            pageSizeSelect.append(option);
        }
        pageSizeGroup.append(pageSizeSelect, document.createTextNode(' 条'));

        const summary = document.createElement('span');
        summary.className = 'table-pagination__summary';
        summary.setAttribute('aria-live', 'polite');

        const controls = document.createElement('div');
        controls.className = 'table-pagination__controls';
        const previous = createButton('上一页', 'table-pagination__button');
        const next = createButton('下一页', 'table-pagination__button');
        controls.append(previous, next);
        pagination.append(pageSizeGroup, summary, controls);

        const anchor = table.closest('.data-table-scroll') || table;
        anchor.insertAdjacentElement('afterend', pagination);

        function refresh(resetPage = false) {
            if (resetPage) currentPage = 1;
            const allRows = Array.from(body.rows);
            const placeholderRows = allRows.filter(isPlaceholderRow);
            const rows = allRows.filter(
                (row) => !isPlaceholderRow(row) && row.style.display !== 'none',
            );
            const state = getPaginationState(rows.length, currentPage, pageSize);
            currentPage = state.page;

            allRows.forEach((row) => { row.hidden = false; });
            rows.forEach((row, index) => {
                row.hidden = index < state.start || index >= state.end;
            });
            placeholderRows.forEach((row) => { row.hidden = false; });

            const first = state.total === 0 ? 0 : state.start + 1;
            summary.textContent = `第 ${state.page} / ${state.totalPages} 页 · ${first}-${state.end} 条，共 ${state.total} 条`;
            previous.disabled = state.page <= 1;
            next.disabled = state.page >= state.totalPages;
            pagination.hidden = state.total <= state.pageSize;
        }

        function scheduleRefresh(resetPage = true) {
            window.clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(() => refresh(resetPage), 0);
        }

        previous.addEventListener('click', () => {
            currentPage -= 1;
            refresh();
        });
        next.addEventListener('click', () => {
            currentPage += 1;
            refresh();
        });
        pageSizeSelect.addEventListener('change', () => {
            pageSize = Number(pageSizeSelect.value) || 20;
            refresh(true);
        });
        table.addEventListener('table-pagination:refresh', (event) => {
            refresh(event.detail?.resetPage !== false);
        });

        const observer = new MutationObserver(() => scheduleRefresh(true));
        observer.observe(body, {childList: true});

        const api = {refresh, disconnect: () => observer.disconnect()};
        paginators.set(table, api);
        refresh();
        return api;
    }

    function initializeTablePagination() {
        document.querySelectorAll('table[data-paginate="true"]').forEach(setupTablePagination);
    }

    root.getPaginationState = getPaginationState;
    root.setupTablePagination = setupTablePagination;
    root.initializeTablePagination = initializeTablePagination;

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {getPaginationState};
    }
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initializeTablePagination);
        } else {
            initializeTablePagination();
        }
    }
})(typeof window !== 'undefined' ? window : globalThis);
