// NODE_PATH may point to an isolated jsdom installation; no live browser or network needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const window = new JSDOM('').window;
const DOMPurify = require('../static/lib/purify.min.js')(window);
const source = fs.readFileSync('static/docent.js', 'utf8');
const helpers = source.slice(source.indexOf('    function escapeHtml('), source.indexOf('    function setQuery('));
const context = { DOMPurify, recordSources: {}, tr: (ko, en) => en,
    marked: { parse: text => text.replace(/\[([^\]]+)\]\(([^\s]+)\)/g, '<a href="$2">$1</a>') } };
vm.createContext(context);
vm.runInContext(helpers, context);
for (const attack of [
    '<svg onmouseover="alert(1)"></svg>',
    '<img src=x onerror="alert(1)">',
    '<a onclick="alert(1)" href="javascript:alert(1)">x</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '[click](javascript:alert%281%29)',
    '<a href="data:text/html,evil">x</a>',
    '<span onfocus="alert(1)" tabindex=0>x</span>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=1 onerror=alert(1)>">'
]) {
    const html = context.renderMarkdown(attack);
    const template = window.document.createElement('template');
    template.innerHTML = html;
    for (const element of template.content.querySelectorAll('*')) {
        assert(!['SCRIPT', 'SVG', 'MATH', 'IMG', 'IFRAME'].includes(element.tagName), html);
        for (const attribute of element.attributes) {
            assert(!attribute.name.startsWith('on'), html);
            if (attribute.name === 'href') assert(/^(https?:|mailto:|#)/i.test(attribute.value), html);
        }
    }
}
assert.equal(context.renderMarkdown('<strong>safe</strong>'), '<strong>safe</strong>');
assert(context.renderMarkdown('[safe](https://example.test)').includes('href="https://example.test"'));
context.DOMPurify = undefined;
assert.equal(context.renderMarkdown('<svg onmouseover="evil()">'), '&lt;svg onmouseover=&quot;evil()&quot;&gt;');
console.log('Rendering safety checks passed (8 attack cases, safe markup, fail-closed fallback)');
