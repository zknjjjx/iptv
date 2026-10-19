'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require ui';

var listService = rpc.declare({ object: 'service', method: 'list', params: [ 'name' ], expect: {} });
var control = rpc.declare({ object: 'file', method: 'exec', params: [ 'command', 'params' ], expect: {}, reject: true });
var readPassword = rpc.declare({ object: 'file', method: 'read', params: [ 'path' ], expect: {}, reject: true });
var commit = rpc.declare({ object: 'uci', method: 'commit', params: [ 'config' ], reject: true });

function action(name) {
	return control('/usr/libexec/iptv-speedtest-ctl', [ name ]).then(function (res) {
		if (res.code) throw new Error(res.stderr || '操作失败');
		ui.addNotification(null, E('p', {}, '操作完成，刷新页面查看状态。'));
	});
}

return view.extend({
	load: function () {
		return Promise.all([ uci.load('iptv_speedtest'), listService('iptv-speedtest') ]);
	},
	handleSaveApply: function (ev) {
		// Commit only this app: never apply unrelated pending network changes.
		return this.handleSave(ev).then(function () {
			return commit('iptv_speedtest');
		}).then(function () {
			return action('apply');
		}).catch(function (err) {
			ui.addNotification(null, E('p', {}, '应用失败：' + err.message), 'error');
		});
	},
	render: function (data) {
		var m = new form.Map('iptv_speedtest', 'IPTV 测速优选',
			'原生 Python 服务，不依赖 Docker。不修改网络、DNS 或防火墙。测速源、并发数、分组和定时请在独立管理网页设置。首次默认并发 4、仅手动测速。');
		var s = m.section(form.NamedSection, 'main', 'iptv_speedtest', '服务管理');
		s.anonymous = true;
		var status = s.option(form.DummyValue, '_status', '运行状态');
		status.cfgvalue = function () {
			var instances = ((data[1] || {})['iptv-speedtest'] || {}).instances || {};
			return Object.keys(instances).some(function (k) { return instances[k].running; }) ? '运行中' : '未运行';
		};
		var enabled = s.option(form.Flag, 'enabled', '启用服务及开机自启');
		enabled.default = '0'; enabled.rmempty = false;
		enabled.description = '保存并应用后，仅重启本插件。停止按钮临时停止，禁用后保存才会关闭开机自启。';
		var host = s.option(form.Value, 'listen_host', '监听 IPv4 地址');
		host.datatype = 'ip4addr'; host.default = '127.0.0.1'; host.rmempty = false;
		host.description = '填写本路由器 LAN 地址。127.0.0.1 仅本机；不建议 0.0.0.0（可能在其他接口暴露未加密订阅）。';
		var port = s.option(form.Value, 'port', '网页端口');
		port.datatype = 'port'; port.default = '8899'; port.rmempty = false;
		var storage = s.option(form.DummyValue, '_storage', '数据保存');
		storage.cfgvalue = function () { return '/etc/iptv-speedtest（配置、结果和管理密码；卸载保留）'; };
		var links = s.option(form.DummyValue, '_links', '管理及订阅地址');
		links.cfgvalue = function () {
			var h = uci.get('iptv_speedtest', 'main', 'listen_host') || '127.0.0.1';
			var p = uci.get('iptv_speedtest', 'main', 'port') || '8899';
			var base = 'http://' + (h === '0.0.0.0' ? window.location.hostname : h) + ':' + p;
			return E('div', {}, [
				E('p', {}, E('a', { href: base + '/', target: '_blank', rel: 'noopener noreferrer' }, '打开 IPTV 测速优选管理网页')),
				E('p', {}, E('a', { href: base + '/m3u', target: '_blank', rel: 'noopener noreferrer' }, 'M3U：' + base + '/m3u')),
				E('p', {}, E('a', { href: base + '/txt', target: '_blank', rel: 'noopener noreferrer' }, 'TXT：' + base + '/txt')),
				E('p', {}, '首次测速完成前订阅返回 503。管理密码由首次启动自动生成；只供受信任局域网使用，不要做公网端口转发。')
			]);
		};
		var pw = s.option(form.Button, '_password', '管理密码');
		pw.inputtitle = '查看管理密码';
		pw.onclick = function () {
			return readPassword('/etc/iptv-speedtest/admin-password').then(function (res) {
				ui.showModal('管理密码', [ E('p', {}, '在独立网页提示时输入；订阅地址不需要密码。'),
					E('code', {}, String(res.data || '').trim()), E('div', { 'class': 'right' },
					E('button', { 'class': 'btn', click: ui.hideModal }, '关闭')) ]);
			}).catch(function (err) { ui.addNotification(null, E('p', {}, '读取失败：请先启动服务。' + err.message), 'error'); });
		};
		[ [ 'start', '启动' ], [ 'stop', '停止' ], [ 'restart', '重启' ] ].forEach(function (item) {
			var b = s.option(form.Button, '_' + item[0], item[1] + '服务');
			b.inputtitle = item[1];
			b.onclick = function () { return action(item[0]); };
		});
		return m.render();
	}
});
