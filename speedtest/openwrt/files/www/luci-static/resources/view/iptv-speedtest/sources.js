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
            unavailable.appendChild(S.el('h2', {}, '直播源'));
            unavailable.appendChild(S.el('div', { 'class': 'alert-message warning' }, '服务配置暂不可用：' + cfg.__error));
            unavailable.appendChild(S.button('刷新 / 重试', reloadPage, 'cbi-button-action'));
            return unavailable;
        }
        cfg = cfg || {};
        var root = S.el('div', { 'class': 'cbi-map' });
        var list = (cfg.sources || []).map(function (src) {
            return { url: src.url || '', name: src.name || '', enabled: src.enabled !== false };
        });
        root.appendChild(S.el('h2', {}, '直播源'));
        root.appendChild(S.el('p', { 'class': 'alert-message info' }, '这里只保存直播源和拉取代理，不会触发测速。拉取时先尝试代理，失败后直连；源地址若已带代理前缀会原样使用。'));
        var proxy = S.input('text', cfg.proxy || '', {
            'class': 'cbi-input-text', placeholder: '留空 = 不使用代理'
        });
        root.appendChild(S.field('拉取源列表代理', proxy));
        root.appendChild(S.el('p', { 'class': 'cbi-description' }, '代理只用于获取 m3u/txt 源列表，不是频道播放代理。'));
        var box = S.el('div', { 'class': 'cbi-section-table' });
        root.appendChild(box);

        function draw() {
            box.replaceChildren();
            if (!list.length)
                box.appendChild(S.el('p', { 'class': 'cbi-description' }, '还没有直播源，请新增一个 http(s) 或 file 地址。'));
            list.forEach(function (src, i) {
                var url = S.input('text', src.url, { 'class': 'cbi-input-text', placeholder: 'm3u / txt 订阅地址' });
                var name = S.input('text', src.name, { 'class': 'cbi-input-text', placeholder: '备注（可选）' });
                var en = S.input('checkbox', '', {});
                en.checked = src.enabled;
                // Keep the model synchronized before a redraw (especially after add/delete).
                url.addEventListener('input', function () { src.url = url.value; });
                name.addEventListener('input', function () { src.name = name.value; });
                en.addEventListener('change', function () { src.enabled = en.checked; });
                var test = S.button('测试源', function () {
                    test.disabled = true;
                    S.call('testsource', url.value).then(function (r) {
                        var kinds = Object.keys(r.kinds || {}).map(function (k) {
                            return k + ' ' + r.kinds[k];
                        }).join('，') || '无分类统计';
                        var via = r.via && r.via !== url.value ? '，备用地址：' + r.via : '';
                        S.notice('源测试完成：' + (r.count || 0) + ' 条（' + kinds + '），耗时 ' + (r.seconds || 0) + ' 秒' + via);
                    }).catch(function (e) {
                        S.notice('源测试失败：' + e.message, true);
                    }).then(function () { test.disabled = false; });
                });
                var del = S.button('删除', function () { list.splice(i, 1); draw(); }, 'cbi-button-remove');
                box.appendChild(S.el('div', { 'class': 'cbi-section-table-row' }, [
                    en, S.el('label', { 'class': 'cbi-label' }, '启用'), name, url, test, del
                ]));
            });
            box.appendChild(S.button('新增源', function () {
                list.push({ url: '', name: '', enabled: true });
                draw();
            }));
        }
        draw();
        root.appendChild(S.el('div', { 'class': 'cbi-page-actions' }, [
            S.button('保存直播源', function () {
                var sources = Array.prototype.map.call(box.querySelectorAll('.cbi-section-table-row'), function (row) {
                    var x = row.querySelectorAll('input');
                    return { enabled: x[0].checked, name: x[1].value, url: x[2].value };
                });
                S.call('save', JSON.stringify({ proxy: proxy.value, sources: sources }))
                    .then(function () { S.notice('直播源已保存'); })
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
