'use strict';
// No browser/npm dependency: exercise the actual view event handlers with a tiny DOM.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const dir = path.join(__dirname, '../openwrt/files/www/luci-static/resources/view/iptv-speedtest');
class Node {
    constructor(tag, attrs = {}, children = []) {
        this.tag = tag; this.attrs = attrs; this.children = []; this.events = {};
        this.value = attrs.value || ''; this.checked = false;
        for (const child of Array.isArray(children) ? children : [children]) this.appendChild(child);
    }
    appendChild(child) { if (child != null) this.children.push(child); return child; }
    replaceChildren(...children) { this.children = []; children.forEach(x => this.appendChild(x)); }
    addEventListener(event, handler) { this.events[event] = handler; }
    emit(event) { if (this.events[event]) return this.events[event](); }
    querySelectorAll(selector) {
        const match = n => selector[0] === '.' ? (n.attrs.class || '').split(' ').includes(selector.slice(1)) : n.tag === selector;
        const result = [];
        const walk = n => { for (const c of n.children) if (c instanceof Node) { if (match(c)) result.push(c); walk(c); } };
        walk(this); return result;
    }
}
function setup(name, cfg) {
    const calls = [];
    const S = {
        el: (tag, attrs, kids) => new Node(tag, attrs, kids),
        input: (type, value, attrs) => new Node('input', {...attrs, type, value}),
        field: (label, input) => new Node('label', {}, [label, input]),
        button: (label, fn) => { const n = new Node('button', {}, label); n.addEventListener('click', fn); return n; },
        call: (...args) => { calls.push(args); return Promise.resolve({items: [], total: 0}); },
        notice() {}, fail() {}, table: () => new Node('table')
    };
    const view = new Function('view', 'S', fs.readFileSync(path.join(dir, name + '.js'), 'utf8'))({extend: x => x}, S);
    assert.equal(view.handleSaveApply, null, name + ' must not apply global pending UCI changes');
    assert.equal(view.handleSave, null); assert.equal(view.handleReset, null);
    return {root: view.render(cfg), calls};
}
function click(root, label, index = 0) {
    const button = root.querySelectorAll('button').filter(x => x.children.includes(label))[index];
    assert.ok(button, 'button ' + label); return button.emit('click');
}
function edit(node, value, event = 'input') { node.value = value; node.emit(event); }
function rows(root) { return root.querySelectorAll('.cbi-section-table-row'); }
{
    const {root, calls} = setup('sources', {proxy: 'unchanged', sources: [{name: 'old', url: 'http://old', enabled: true}]});
    let inputs = rows(root)[0].querySelectorAll('input');
    edit(inputs[1], 'edited'); edit(inputs[2], 'http://edited'); inputs[0].checked = false; inputs[0].emit('change');
    click(root, '新增源');
    inputs = rows(root)[0].querySelectorAll('input');
    assert.equal(inputs[1].value, 'edited'); assert.equal(inputs[2].value, 'http://edited'); assert.equal(inputs[0].checked, false);
    click(root, '删除', 1); click(root, '保存直播源');
    const saved = JSON.parse(calls.at(-1)[1]);
    assert.deepEqual(Object.keys(saved).sort(), ['proxy', 'sources']);
    assert.deepEqual(saved.sources, [{enabled: false, name: 'edited', url: 'http://edited'}]);
    console.log('PASS sources: add/delete preserve edits, save is page-local');
}
{
    const {root, calls} = setup('groups', {group_rules: [{group: 'A', keywords: 'a'}, {group: 'B', keywords: 'b'}]});
    edit(rows(root)[0].querySelectorAll('input')[0], 'edited A');
    edit(rows(root)[0].querySelectorAll('input')[1], 'edited keyword');
    click(root, '下移');
    assert.equal(rows(root)[1].querySelectorAll('input')[0].value, 'edited A');
    click(root, '新增分组'); click(root, '删除', 2); click(root, '保存分组');
    const saved = JSON.parse(calls.at(-1)[1]);
    assert.deepEqual(saved.group_rules, [{group: 'B', keywords: 'b'}, {group: 'edited A', keywords: 'edited keyword'}]);
    assert.deepEqual(Object.keys(saved).sort(), ['group_mode', 'group_rules', 'unmatched_group']);
    console.log('PASS groups: reorder/add/delete preserve edits, save is page-local');
}
{
    const {root, calls} = setup('options', {blacklist: '广告', min_height: 720, start_time: '03:00'});
    click(root, '保存测速选项');
    const saved = JSON.parse(calls.at(-1)[1]);
    assert.equal(saved.blacklist, '广告');
    assert.equal(saved.min_height, '720');
    assert.equal(saved.start_time, '03:00');
    assert.equal(Object.prototype.hasOwnProperty.call(saved, 'group_rules'), false);
    console.log('PASS options: blacklist is page-local');
}
for (const name of ['options', 'results']) {
    if (name === 'options') continue;
    setup(name, {}); console.log('PASS ' + name + ': no global UCI apply footer');
}
