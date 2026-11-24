'use strict';
'require view';
'require iptv-speedtest as S';

return view.extend({
    render: function () {
        var root = S.el('div', { 'class': 'cbi-map' });
        var tab = 'report', offset = 0, limit = 50, query = '', generation = 0;
        root.appendChild(S.el('h2', {}, '测速结果'));
        var controls = S.el('div', { 'class': 'cbi-section', style: 'display:flex;flex-wrap:wrap;gap:8px;align-items:center' });
        var search = S.input('search', '', { placeholder: '搜索频道、链接或失败原因', 'aria-label': '搜索频道、链接或失败原因' });
        var kind = S.el('select', { 'aria-label': '结果类型' }, [
            S.el('option', { value: 'report' }, '成功'), S.el('option', { value: 'failed' }, '失败清单')
        ]);
        var reason = S.el('select', { 'aria-label': '失败原因', hidden: true }, [S.el('option', { value: '' }, '全部原因')]);
        var body = S.el('div', { style: 'overflow-x:auto' });
        var exporting = S.button('导出失败 TXT', function () {
            var selectedQuery = search.value, selectedReason = reason.value, lines = [], size = 0;
            exporting.disabled = true;
            function next(start) {
                return S.call('failed', start, 100, selectedQuery, selectedReason).then(function (page) {
                    (page.items || []).forEach(function (item) {
                        var line = (item.name || '') + ',' + (item.url || item.src || '') + '  # ' + (item.reason || '');
                        size += line.length;
                        if (size > 16 * 1024 * 1024) throw new Error('导出过大，请先缩小搜索范围');
                        lines.push(line);
                    });
                    var count = (page.items || []).length;
                    if (start + count < page.total) {
                        if (!count) throw new Error('结果正在变化，请重试');
                        return next(start + count);
                    }
                });
            }
            return next(0).then(function () {
                var url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }));
                var link = S.el('a', { href: url, download: 'failed.txt' });
                document.body.appendChild(link); link.click(); link.remove();
                setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
                S.notice('已导出 ' + lines.length + ' 条失败记录');
            }).catch(function (e) { S.notice('导出失败：' + e.message, true); })
                .then(function () { exporting.disabled = false; });
        }, 'cbi-button');
        exporting.hidden = true;
        controls.appendChild(kind); controls.appendChild(search); controls.appendChild(reason);
        controls.appendChild(S.button('查询 / 刷新', function () { offset = 0; query = search.value; load(); }));
        controls.appendChild(exporting); root.appendChild(controls); root.appendChild(body);
        function load() {
            var requestId = ++generation, selectedTab = tab, selectedReason = reason.value;
            var args = [selectedTab, offset, limit, query];
            if (selectedTab === 'failed') args.push(selectedReason);
            return S.call.apply(null, args).then(function (page) {
                if (requestId !== generation) return;
                page = page || { items: [], total: 0 };
                if (selectedTab === 'failed') {
                    reason.replaceChildren(S.el('option', { value: '' }, '全部原因'));
                    Object.keys(page.reasons || {}).sort(function (a, b) { return page.reasons[b] - page.reasons[a]; }).forEach(function (key) {
                        reason.appendChild(S.el('option', { value: key }, key + '（' + page.reasons[key] + '）'));
                    });
                    reason.value = selectedReason;
                }
                var rows = (page.items || []).map(function (item) {
                    if (selectedTab === 'failed')
                        return [item.group || '', item.name || '', item.reason || '', item.url || item.src || ''];
                    return [item.group || '', item.name || '', item.kbps == null ? '—' : (item.kbps / 1000).toFixed(1) + ' Mbps',
                        item.res || '—', item.rt == null ? '—' : String(item.rt) + (item.weak ? '（勉强）' : ''),
                        item.ttfb == null ? '—' : item.ttfb + ' s', item.url || ''];
                });
                var heads = selectedTab === 'failed' ? ['分组', '频道', '失败原因', '链接'] : ['分组', '频道', '码率', '分辨率', '实时倍率', '首包', '链接'];
                body.replaceChildren(S.table(heads, rows), S.el('p', {}, '共 ' + (page.total || 0) + ' 条，当前 ' +
                    (rows.length ? ((page.offset || 0) + 1) + '–' + Math.min((page.offset || 0) + rows.length, page.total || 0) : '0') + ' 条'));
                var nav = S.el('div');
                if (offset > 0) nav.appendChild(S.button('上一页', function () { offset = Math.max(0, offset - limit); load(); }, 'cbi-button'));
                if (offset + limit < (page.total || 0)) nav.appendChild(S.button('下一页', function () { offset += limit; load(); }, 'cbi-button'));
                body.appendChild(nav);
            }).catch(function (e) {
                if (requestId !== generation) return;
                S.fail('结果暂不可用：' + e.message, body);
                body.appendChild(S.button('刷新 / 重试', load));
            });
        }
        kind.addEventListener('change', function () {
            tab = kind.value; offset = 0; reason.value = '';
            reason.hidden = exporting.hidden = tab !== 'failed'; load();
        });
        reason.addEventListener('change', function () { offset = 0; load(); });
        load();
        return root;
    },
    handleSave: null,
    handleSaveApply: null,
    handleReset: null
});
