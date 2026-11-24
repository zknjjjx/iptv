'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require ui';
'require poll';
'require iptv-speedtest as speed';

var listService = rpc.declare({
    object: 'service', method: 'list', params: [ 'name' ], expect: {}, reject: true
});
var control = rpc.declare({
    object: 'file', method: 'exec', params: [ 'command', 'params' ], expect: {}, reject: true
});
var commit = rpc.declare({
    object: 'uci', method: 'commit', params: [ 'config' ], expect: {}, reject: true
});

function action(name) {
    return control('/usr/libexec/iptv-speedtest-ctl', [ name ]).then(function (result) {
        if (!result || result.code)
            throw new Error(result && result.stderr ? result.stderr : '操作失败');
        return result;
    });
}

function running(data) {
    var instances = ((data || {})['iptv-speedtest'] || {}).instances || {};
    return Object.keys(instances).some(function (key) { return instances[key].running; });
}

function copy(text) {
    if (navigator.clipboard)
        return navigator.clipboard.writeText(text).then(function () { ui.addNotification(null, E('p', {}, '地址已复制')); });
    var area = E('textarea', { style: 'position:fixed;opacity:0' });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
}

function value(data, key, fallback) {
    return data && data[key] !== null && data[key] !== undefined ? data[key] : fallback;
}

return view.extend({
    load: function () {
        return Promise.all([
            uci.load('iptv_speedtest'),
            listService('iptv-speedtest'),
            speed.call('status').catch(function () { return null; })
        ]);
    },

    handleSaveApply: function (ev) {
        return this.handleSave(ev)
            .then(function () { return commit('iptv_speedtest'); })
            .then(function () { return action('apply'); })
            .catch(function (e) {
                ui.addNotification(null, E('p', {}, '应用失败：' + e.message), 'error');
            });
    },

    render: function (data) {
        var initial = data[2];
        var m = new form.Map(
            'iptv_speedtest',
            'IPTV 测速优选',
            '仅服务页保存会提交 UCI 并重启本插件；其它页保存只写测速配置，不会自动测速。'
        );
        var s = m.section(form.NamedSection, 'main', 'iptv_speedtest', '服务状态与控制');
        s.anonymous = true;

        var status = s.option(form.DummyValue, '_status', '运行状态');
        status.cfgvalue = function () {
            return E('span', { id: 'iptv-status' }, value(initial, 'status', running(data[1]) ? '运行中' : '未运行'));
        };
        var progress = s.option(form.DummyValue, '_progress', '测速进度');
        progress.cfgvalue = function () {
            var wrap = E('div', { id: 'iptv-progress-wrap' });
            wrap.appendChild(E('progress', { id: 'iptv-progress', max: '0', value: '0' }));
            wrap.appendChild(E('span', { id: 'iptv-progress-label' }, '0 / 0'));
            return wrap;
        };
        var counts = s.option(form.DummyValue, '_counts', '本轮统计');
        counts.cfgvalue = function () { return E('span', { id: 'iptv-counts' }, '已测 0 · 存活 0 · 频道 0'); };
        var runInfo = s.option(form.DummyValue, '_run_info', '运行记录');
        runInfo.cfgvalue = function () { return E('span', { id: 'iptv-run-info' }, '上次运行：— · 耗时：— · 下次运行：—'); };
        var liveStats = s.option(form.DummyValue, '_live_stats', '本轮来源统计');
        liveStats.cfgvalue = function () { return E('div', { id: 'iptv-live-stats', style: 'overflow-x:auto' }, '暂无数据'); };
        var sourceStats = s.option(form.DummyValue, '_source_stats', '上轮来源统计');
        sourceStats.cfgvalue = function () { return E('div', { id: 'iptv-source-stats', style: 'overflow-x:auto' }, '暂无数据'); };

        var run = s.option(form.Button, '_run', '测速控制');
        run.inputtitle = '开始测速';
        run.onclick = function () {
            if (!window.confirm('开始测速会占用带宽并产生较多连接，继续？')) return;
            return speed.call('refresh').then(function () { ui.addNotification(null, E('p', {}, '测速已开始')); })
                .catch(function (e) { ui.addNotification(null, E('p', {}, e.message), 'error'); });
        };
        var halt = s.option(form.Button, '_halt', '停止测速');
        halt.inputtitle = '停止测速';
        halt.onclick = function () {
            if (!window.confirm('停止当前测速？')) return;
            return speed.call('stop').then(function () { ui.addNotification(null, E('p', {}, '已请求停止测速')); })
                .catch(function (e) { ui.addNotification(null, E('p', {}, e.message), 'error'); });
        };
        var enabled = s.option(form.Flag, 'enabled', '启用服务及开机自启');
        enabled.default = '0';
        enabled.rmempty = false;
        var host = s.option(form.Value, 'listen_host', '监听 IPv4 地址');
        host.datatype = 'ip4addr';
        host.default = '127.0.0.1';
        host.rmempty = false;
        var port = s.option(form.Value, 'port', '监听端口');
        port.datatype = 'port';
        port.default = '8899';
        port.rmempty = false;
        var links = s.option(form.DummyValue, '_links', '订阅地址');
        links.cfgvalue = function () {
            var h = uci.get('iptv_speedtest', 'main', 'listen_host') || '127.0.0.1';
            var p = uci.get('iptv_speedtest', 'main', 'port') || '8899';
            var base = 'http://' + (h === '0.0.0.0' ? window.location.hostname : h) + ':' + p;
            return E('div', {}, [
                E('p', {}, '管理页：' + base + '/（请在 LuCI 中管理）'),
                E('p', {}, [ E('code', {}, base + '/m3u'), E('button', { 'class': 'cbi-button', type: 'button', click: function () { copy(base + '/m3u'); } }, '复制') ]),
                E('p', {}, [ E('code', {}, base + '/txt'), E('button', { 'class': 'cbi-button', type: 'button', click: function () { copy(base + '/txt'); } }, '复制') ]),
                E('p', { 'class': 'alert-message warning' }, '监听回环地址只能本机访问；勿将管理页公网转发。HTTP 8899 外部播放器订阅保持不变。')
            ]);
        };
        [ [ 'start', '启动服务' ], [ 'stop', '停止服务' ], [ 'restart', '重启服务' ] ].forEach(function (item) {
            var b = s.option(form.Button, '_' + item[0], item[1]);
            b.inputtitle = item[1];
            b.onclick = function () {
                var msg = item[0] === 'start' ? '启动服务？' : item[0] === 'restart' ? '重启会短暂中断订阅，继续？' : '停止服务会中断订阅，继续？';
                if (!window.confirm(msg)) return;
                return action(item[0]).then(function () { ui.addNotification(null, E('p', {}, '操作已提交，请稍后刷新状态。')); });
            };
        });

        var rendered = m.render();
        function statsTable(node, stats) {
            if (!node) return;
            var keys = Object.keys(stats || {});
            if (!keys.length) { node.textContent = '暂无数据'; return; }
            var rows = keys.map(function (key) {
                var item = stats[key] || {};
                return [ item.name || key, item.error ? '拉取失败：' + item.error : String(value(item, 'used', 0)) + ' / ' + value(item, 'total', 0),
                    String(value(item, 'tested', value(item, 'used', 0))), String(value(item, 'alive', 0)), item.via || '—' ];
            });
            node.replaceChildren(speed.table([ '源', '使用 / 总条数', '已测', '成功', '备用地址' ], rows));
        }
        function updateStatus(current) {
            var statusNode = document.getElementById('iptv-status');
            var progressNode = document.getElementById('iptv-progress');
            var progressLabel = document.getElementById('iptv-progress-label');
            var countsNode = document.getElementById('iptv-counts');
            var runNode = document.getElementById('iptv-run-info');
            var sourceNode = document.getElementById('iptv-source-stats');
            var total = Number(value(current, 'total', 0)) || 0;
            var done = Number(value(current, 'progress', 0)) || 0;
            if (statusNode) statusNode.textContent = value(current, 'status', '未知');
            var state = value(current, 'status', '');
            var busy = [ '测速中', '拉取源', '正在停止' ].indexOf(state) !== -1 || state.indexOf('复测 ') === 0;
            var runNodeControl = document.querySelector('[id="widget.cbid.iptv_speedtest.main._run"]');
            var stopNodeControl = document.querySelector('[id="widget.cbid.iptv_speedtest.main._halt"]');
            if (runNodeControl) runNodeControl.disabled = busy;
            if (stopNodeControl) stopNodeControl.disabled = !busy || state === '正在停止';
            if (progressNode) { progressNode.max = String(total); progressNode.value = String(Math.min(done, total || done)); }
            if (progressLabel) progressLabel.textContent = done + ' / ' + total;
            if (countsNode) countsNode.textContent = '已测 ' + value(current, 'tested', 0) + ' · 存活 ' + value(current, 'alive', 0) + ' · 频道 ' + value(current, 'channels', 0);
            if (runNode) runNode.textContent = '上次运行：' + (value(current, 'last_run', '') || '—') + ' · 耗时：' + value(current, 'duration_s', '—') + ' 秒 · 下次运行：' + (value(current, 'next_run', '') || '手动');
            statsTable(sourceNode, value(current, 'source_stats', {}));
            statsTable(document.getElementById('iptv-live-stats'), value(current, 'cur_stats', {}));
        }
        function unavailable(error) {
            updateStatus({ status: '后端不可用：' + error.message, total: 0, progress: 0 });
            var wrap = document.getElementById('iptv-status');
            if (wrap && !document.getElementById('iptv-status-retry')) {
                var retry = E('button', { id: 'iptv-status-retry', 'class': 'cbi-button', type: 'button' }, '刷新 / 重试');
                retry.addEventListener('click', function () { speed.call('status').then(updateStatus).catch(unavailable); });
                wrap.parentNode.appendChild(retry);
            }
        }
        poll.add(function () { return speed.call('status').then(updateStatus).catch(unavailable); }, 5);
        updateStatus(initial || { status: '未运行' });
        return rendered;
    }
});
