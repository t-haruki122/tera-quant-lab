import assert from 'node:assert/strict';

const voidTags = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
const tags = /<!--[\s\S]*?-->|<![^>]*>|<\/?[A-Za-z][\w:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>|[^<]+/g;
const attribute = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parse(html) {
    const root = { children: [] };
    const stack = [root];
    const nodes = [];
    for (const token of html.match(tags) ?? []) {
        if (token.startsWith('<!')) continue;
        const parent = stack.at(-1);
        if (!token.startsWith('<')) {
            parent.children.push(token);
            continue;
        }
        const [, closing, tag, attrs] = /^<(\/?)([\w:-]+)([\s\S]*)>$/.exec(token);
        const name = tag.toLowerCase();
        if (closing) {
            const index = stack.findLastIndex(node => node.name === name);
            if (index > 0) stack.length = index;
            continue;
        }
        const node = {
            name, parent, children: [],
            attrs: new Map([...attrs.matchAll(attribute)].map(([, key, double, single, bare]) =>
                [key.toLowerCase(), double ?? single ?? bare ?? ''])),
        };
        parent.children.push(node);
        nodes.push(node);
        if (!voidTags.has(name) && !attrs.endsWith('/')) stack.push(node);
    }
    return nodes;
}

function text(node) {
    if (typeof node === 'string') return node.replace(/&(?:nbsp|#160|#xA0);/gi, ' ');
    if (node.attrs.get('aria-hidden') === 'true' || node.attrs.has('hidden') || node.name === 'svg') return '';
    if (node.name === 'img') return node.attrs.get('alt') ?? '';
    return node.children.map(text).join('').trim();
}

function hasLabel(node, labels) {
    const id = node.attrs.get('id');
    if (id && labels.some(label => label.attrs.get('for') === id && text(label))) return true;
    for (let parent = node.parent; parent; parent = parent.parent) {
        if (parent.name === 'label') return !parent.attrs.has('for') && Boolean(text(parent));
    }
    return false;
}

export function assertAccessibleControls(html) {
    const nodes = parse(html);
    const ids = new Map(nodes.filter(node => node.attrs.has('id')).map(node => [node.attrs.get('id'), node]));
    const labels = nodes.filter(node => node.name === 'label');
    const problems = [];
    for (const node of nodes) {
        const { name, attrs } = node;
        const type = attrs.get('type')?.toLowerCase();
        const identifier = attrs.get('id') ?? attrs.get('class') ?? name;
        if (name === 'button' || (name === 'input' && ['button', 'reset', 'submit', 'image'].includes(type))) {
            const references = (attrs.get('aria-labelledby') ?? '').trim().split(/\s+/);
            const named = attrs.get('aria-label')?.trim() || references.some(id => ids.has(id) && text(ids.get(id)));
            const visible = name === 'button' ? text(node) : (attrs.get('alt') ?? attrs.get('value') ?? '');
            if (!named && !/[\p{L}\p{N}]/u.test(visible)) problems.push(`Button without an accessible name: ${identifier}`);
        } else if (['input', 'select', 'textarea'].includes(name) && type !== 'hidden' && !hasLabel(node, labels)) {
            problems.push(`Input without a label: ${identifier}`);
        }
    }
    assert.deepEqual(problems, [], problems.join('\n'));
}
