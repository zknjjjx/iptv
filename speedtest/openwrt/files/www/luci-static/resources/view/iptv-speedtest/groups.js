'use strict';
'require view';
'require iptv-speedtest as S';

function reloadPage() { window.location.reload(); }

return view.extend({
    load: function () {
        return S.call('config').catch(function (e) { return { __error: e.message }; });
    },

    render: function (cfg) {
        if (cfg && cfg.__error) {
            var unavailable = S.el('div', { 'class': 'cbi-map' });
            unavailable.appendChild(S.el('h2', {}, '分组'));
            unavailable.appendChild(S.el('div', { 'class': 'alert-message warning' }, '服务配置暂不可用：' + cfg.__error));
            unavailable.appendChild(S.button('刷新 / 重试', reloadPage, 'cbi-button-action'));
            return unavailable;
        }
        cfg = cfg || {};
        var root = S.el('div', { 'class': 'cbi-map' });
        var rules = (cfg.group_rules || []).map(function (x) {
            return { group: x.group || '', keywords: x.keywords || '' };
        });
        root.appendChild(S.el('h2', {}, '分组'));
        root.appendChild(S.el('p', { 'class': 'alert-message info' }, '规则按从上到下匹配；频道名含任一关键词即归入该组。保存只写入分组配置，不会触发测速。黑名单请在“测速选项”页设置。'));
        var mode = S.el('select', { 'class': 'cbi-input-select' }, [
            S.el('option', { value: 'rules' }, '按关键词规则分组'),
            S.el('option', { value: 'source' }, '保留源里的原始分组')
        ]);
        mode.value = cfg.group_mode || 'rules';
        root.appendChild(S.field('分组方式', mode));
        var unmatched = S.input('text', cfg.unmatched_group || '', {
            'class': 'cbi-input-text', placeholder: '留空 = 使用源里的原始分组'
        });
        root.appendChild(S.field('未匹配的频道', unmatched));
        root.appendChild(S.el('p', { 'class': 'cbi-description' }, '“按关键词规则分组”时，未命中规则的频道会进入此组；留空则保留源分组。'));
        var box = S.el('div', { 'class': 'cbi-section-table' });
        root.appendChild(box);
        function draw() {
            box.replaceChildren();
            if (!rules.length)
                box.appendChild(S.el('p', { 'class': 'cbi-description' }, '还没有规则。'));
            rules.forEach(function (rule, i) {
                var group = S.input('text', rule.group, { 'class': 'cbi-input-text', placeholder: '分组名' });
                var keywords = S.input('text', rule.keywords, { 'class': 'cbi-input-text', placeholder: '关键词，逗号分隔' });
                group.addEventListener('input', function () { rule.group = group.value; });
                keywords.addEventListener('input', function () { rule.keywords = keywords.value; });
                var up = S.button('上移', function () {
                    if (i) { var prev = rules[i - 1]; rules[i - 1] = rules[i]; rules[i] = prev; draw(); }
                });
                var down = S.button('下移', function () {
                    if (i + 1 < rules.length) { var next = rules[i + 1]; rules[i + 1] = rules[i]; rules[i] = next; draw(); }
                });
                var del = S.button('删除', function () { rules.splice(i, 1); draw(); }, 'cbi-button-remove');
                box.appendChild(S.el('div', { 'class': 'cbi-section-table-row' }, [ group, keywords, up, down, del ]));
            });
            box.appendChild(S.button('新增分组', function () { rules.push({ group: '', keywords: '' }); draw(); }));
        }
        draw();
        root.appendChild(S.el('div', { 'class': 'cbi-page-actions' }, [
            S.button('保存分组', function () {
                var rows = box.querySelectorAll('.cbi-section-table-row');
                var out = Array.prototype.map.call(rows, function (row) {
                    var x = row.querySelectorAll('input');
                    return { group: x[0].value, keywords: x[1].value };
                });
                S.call('save', JSON.stringify({ group_mode: mode.value, group_rules: out, unmatched_group: unmatched.value }))
                    .then(function () { S.notice('分组已保存'); })
                    .catch(function (e) { S.notice('保存失败：' + e.message, true); });
            }),
            S.button('撤销本页', reloadPage, 'cbi-button')
        ]));
        return root;
    },
    handleSave: null,
    handleSaveApply: null,
    handleReset: null
});
