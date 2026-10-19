'use strict';
'require baseclass';
'require rpc';
'require ui';

var api = {};
[ 'status', 'config', 'report', 'failed', 'testsource', 'save', 'refresh', 'stop' ].forEach(function (method) {
    var params = method === 'failed' ? [ 'offset', 'limit', 'query', 'reason' ] :
        method === 'report' ? [ 'offset', 'limit', 'query' ] :
        method === 'testsource' ? [ 'url' ] : method === 'save' ? [ 'data' ] : [];
    api[method] = rpc.declare({ object: 'iptv-speedtest', method: method, params: params, expect: {}, reject: true });
});

function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (!api[method])
        return Promise.reject(new Error('未知后端方法：' + method));
    return api[method].apply(null, args).then(function (result) {
        if (!result || result.ok !== true || !Object.prototype.hasOwnProperty.call(result, 'data'))
            throw new Error(result && result.error ? result.error : '后端返回无效响应');
        return result.data;
    });
}

function text(value) {
    return value === null || value === undefined ? '' : String(value);
}

function el(tag, attrs, children) {
    var node = E(tag, attrs || {});
    (Array.isArray(children) ? children : [ children ]).forEach(function (child) {
        if (child !== null && child !== undefined)
            node.appendChild(typeof child === 'object' ? child : document.createTextNode(text(child)));
    });
    return node;
}

function field(label, input) {
    return el('div', { 'class': 'cbi-value' }, [
        el('label', { 'class': 'cbi-value-title' }, label),
        el('div', { 'class': 'cbi-value-field' }, input)
    ]);
}

function button(label, fn, cls) {
    var b = el('button', {
        'class': 'cbi-button ' + (cls || 'cbi-button-action'),
        type: 'button'
    }, label);
    b.addEventListener('click', fn);
    return b;
}

function input(type, value, attrs) {
    var a = Object.assign({ type: type, value: text(value) }, attrs || {});
    return el('input', a);
}

function notice(message, error) {
    ui.addNotification(null, el('p', {}, message), error ? 'error' : 'info');
}

function fail(message, root) {
    if (root)
        root.replaceChildren(el('div', { 'class': 'alert-message warning' }, message));
    else
        notice(message, true);
}

function table(headers, rows) {
    var t = el('table', { 'class': 'table cbi-section-table' });
    var head = el('tr');
    headers.forEach(function (h) { head.appendChild(el('th', {}, h)); });
    t.appendChild(el('thead', {}, head));
    var body = el('tbody');
    rows.forEach(function (row) {
        var tr = el('tr');
        row.forEach(function (cell) { tr.appendChild(el('td', {}, cell)); });
        body.appendChild(tr);
    });
    t.appendChild(body);
    return t;
}

return baseclass.extend({
    api: api,
    call: call,
    el: el,
    field: field,
    button: button,
    input: input,
    notice: notice,
    fail: fail,
    table: table,
    text: text
});
