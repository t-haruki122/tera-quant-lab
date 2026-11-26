import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const events = () => {
    const listeners = new Map();
    return {
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        dispatchEvent(event) {
            if (!event.target) Object.defineProperty(event, 'target', { value: this });
            Object.defineProperty(event, 'currentTarget', { value: this, configurable: true });
            for (const listener of listeners.get(event.type) ?? []) listener(event);
            if (event.bubbles && !event.cancelBubble) this.parentElement?.dispatchEvent(event);
        },
    };
};

const element = () => {
    const classes = new Set(['hidden']);
    const attrs = new Map();
    return {
        ...events(),
        value: '', textContent: '', innerHTML: '', disabled: false, isConnected: true, tabIndex: 0, dataset: {},
        getAttribute: name => attrs.get(name) ?? null,
        setAttribute: (name, value) => attrs.set(name, String(value)),
        removeAttribute: name => attrs.delete(name),
        getClientRects: () => [1],
        matches(selector) { return selector === ':disabled' && this.disabled; },
        classList: {
            add: name => classes.add(name), remove: name => classes.delete(name),
            contains: name => classes.has(name),
            toggle(name, force = !classes.has(name)) {
                if (force) classes.add(name);
                else classes.delete(name);
                return force;
            },
        },
        children: [],
        appendChild(child) { this.children.push(child); child.parentElement = this; },
        replaceChildren(...children) {
            for (const child of this.children) child.parentElement = null;
            this.children = [];
            for (const child of children) this.appendChild(child);
        },
        contains(child) { return child === this || this.children.some(node => node.contains(child)); },
        focus() { document.activeElement = this; },
        closest(selector) { return selector === 'button' && this.tagName === 'BUTTON' ? this : null; },
        querySelector: () => element(),
        querySelectorAll: () => [],
        click() { if (!this.disabled) this.dispatchEvent(new Event('click', { bubbles: true, cancelable: true })); },
        requestSubmit() { this.dispatchEvent(new Event('submit', { cancelable: true })); },
    };
};
export const elements = new Map();
globalThis.document = {
    ...events(),
    hidden: false,
    getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
    },
    createElement: element,
    querySelectorAll: () => [],
    querySelector: () => element(),
};
export const storage = new Map();
globalThis.localStorage = {
    getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
};
globalThis.setTimeout = () => 0;
globalThis.window = globalThis;
export const fromRepo = path => import(pathToFileURL(resolve(path)).href);

export function pressKey(overlay, target, key, options = {}) {
    const event = new Event('keydown', { cancelable: true });
    for (const [name, value] of Object.entries({ target, key, ...options })) {
        Object.defineProperty(event, name, { value });
    }
    overlay.dispatchEvent(event);
    return event;
}
