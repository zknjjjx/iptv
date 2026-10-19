'use strict';
'require view';
'require iptv-speedtest as S';

function reloadPage() { window.location.reload(); }

var numberSpecs = [
    [ 'interval_hours', '重测间隔（小时）', { min: '0', max: '168', step: '1' }, '留空或 0 时按后端规则仅手动/每天一次。' ],
    [ 'keep', '每频道保留条数', { min: '1', max: '20', step: '1' } ],
    [ 'workers', '并发数', { min: '1', max: '256', step: '1' }, '软路由较弱时可调低。' ],
    [ 'timeout', '超时（秒）', { min: '2', max: '60', step: '1' } ],
    [ 'smooth_ratio', '流畅倍率', { min: '0', max: '5', step: '0.1' }, '达到此实时倍率算流畅。' ],
    [ 'drop_ratio', '剔除倍率', { min: '0', max: '5', step: '0.1' }, '低于此值剔除，中间值保留但排后。' ],
    [ 'retest_workers', '复测并发', { min: '1', max: '32', step: '1' }, '不流畅频道再次测试时的并发数。' ],
    [ 'min_kbps', '最低码率（kbps）', { min: '0', max: '100000', step: '1' }, '例如 2000 约为高清门槛。' ]
];

return view.extend({
    load: function () {
        return S.call('config').catch(function (e) { return { __error: e.message }; });
    },

    render: function (cfg) {
        if (cfg && cfg.__error) {
            var unavailable = S.el('div', { 'class': 'cbi-map' });
            unavailable.appendChild(S.el('h2', {}, '测速选项'));
            unavailable.appendChild(S.el('div', { 'class': 'alert-message warning' }, '服务配置暂不可用：' + cfg.__error));
            unavailable.appendChild(S.button('刷新 / 重试', reloadPage, 'cbi-button-action'));
            return unavailable;
        }
        cfg = cfg || {};
        var root = S.el('div', { 'class': 'cbi-map' });
        root.appendChild(S.el('h2', {}, '测速选项'));
        root.appendChild(S.el('p', { 'class': 'alert-message info' }, '保存只写入测速选项，不会自动测速。数值会由后端限制在有效范围；启动即测速默认关闭，避免重启路由器时默认全量测速。'));
        var vals = {};
        var schedule = S.el('div', { 'class': 'cbi-section' });
        schedule.appendChild(S.el('h3', {}, '测速计划'));
        var run = S.input('checkbox', ''); run.checked = !!cfg.run_on_start; vals.run_on_start = run;
        schedule.appendChild(S.field('启动即测速', run));
        var start = S.input('time', cfg.start_time || '', { 'class': 'cbi-input-text', step: '60' }); vals.start_time = start;
        schedule.appendChild(S.field('每天开始时间', start));
        schedule.appendChild(S.el('p', { 'class': 'cbi-description' }, '留空 = 从上次运行起算；填写后以此时间为基准按间隔重复。'));
        numberSpecs.slice(0, 1).forEach(function (spec) { addNumber(schedule, spec); });
        root.appendChild(schedule);

        var tuning = S.el('div', { 'class': 'cbi-section' });
        tuning.appendChild(S.el('h3', {}, '筛选与测速参数'));
        numberSpecs.slice(1).forEach(function (spec) { addNumber(tuning, spec); });
        var height = S.el('select', { 'class': 'cbi-input-select' }, [
            S.el('option', { value: '0' }, '不限'),
            S.el('option', { value: '576' }, '576p（标清）'),
            S.el('option', { value: '720' }, '720p'),
            S.el('option', { value: '1080' }, '1080p'),
            S.el('option', { value: '2160' }, '4K')
        ]);
        height.value = String(cfg.min_height == null ? 0 : cfg.min_height); vals.min_height = height;
        tuning.appendChild(S.field('最低分辨率', height));
        tuning.appendChild(S.el('p', { 'class': 'cbi-description' }, '低于所选高度的频道会被剔除；未知分辨率是否保留见下方。'));
        [
            [ 'keep_unknown_res', '分辨率未知时保留', '有些源测不出分辨率，关闭后会一并剔除。' ],
            [ 'skip_vod', '丢弃点播占位视频', '' ],
            [ 'merge_names', '合并同名频道', '例如 CCTV-1 高清与 CCTV1。' ]
        ].forEach(function (item) {
            var check = S.input('checkbox', ''); check.checked = !!cfg[item[0]]; vals[item[0]] = check;
            tuning.appendChild(S.field(item[1], check));
            if (item[2]) tuning.appendChild(S.el('p', { 'class': 'cbi-description' }, item[2]));
        });
        root.appendChild(tuning);

        var filter = S.el('div', { 'class': 'cbi-section' });
        filter.appendChild(S.el('h3', {}, '过滤与输出'));
        var black = S.input('text', cfg.blacklist || '', { 'class': 'cbi-input-text', placeholder: '逗号分隔：频道名或链接含有即丢弃' }); vals.blacklist = black;
        filter.appendChild(S.field('屏蔽关键词', black));
        var ip = S.el('select', { 'class': 'cbi-input-select' }, [
            S.el('option', { value: 'all' }, '全部'), S.el('option', { value: 'ipv4' }, '仅 IPv4'), S.el('option', { value: 'ipv6' }, '仅 IPv6')
        ]); ip.value = cfg.ip_version || 'all'; vals.ip_version = ip;
        filter.appendChild(S.field('IP 类型', ip));
        var epg = S.input('url', cfg.epg_url || '', { 'class': 'cbi-input-text', placeholder: 'XMLTV / EPG 地址' }); vals.epg_url = epg;
        filter.appendChild(S.field('EPG 节目单地址', epg));
        root.appendChild(filter);

        root.appendChild(S.el('div', { 'class': 'cbi-page-actions' }, [
            S.button('保存测速选项', function () {
                var payload = {};
                numberSpecs.forEach(function (spec) { payload[spec[0]] = vals[spec[0]].value; });
                payload.start_time = vals.start_time.value;
                payload.min_height = vals.min_height.value;
                [ 'keep_unknown_res', 'skip_vod', 'merge_names', 'run_on_start' ].forEach(function (key) { payload[key] = vals[key].checked; });
                payload.blacklist = vals.blacklist.value; payload.ip_version = vals.ip_version.value; payload.epg_url = vals.epg_url.value;
                S.call('save', JSON.stringify(payload)).then(function () { S.notice('测速选项已保存'); })
                    .catch(function (e) { S.notice('保存失败：' + e.message, true); });
            }),
            S.button('撤销本页', reloadPage, 'cbi-button')
        ]));
        return root;

        function addNumber(section, spec) {
            var input = S.input('number', cfg[spec[0]], Object.assign({ 'class': 'cbi-input-text' }, spec[2]));
            vals[spec[0]] = input;
            section.appendChild(S.field(spec[1], input));
            if (spec[3]) section.appendChild(S.el('p', { 'class': 'cbi-description' }, spec[3]));
        }
    },
    handleSave: null,
    handleSaveApply: null,
    handleReset: null
});
