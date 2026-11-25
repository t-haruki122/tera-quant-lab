import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const element = () => {
    const classes = new Set(['hidden']);
    return {
        value: '', textContent: '', innerHTML: '', disabled: false,
        classList: {
            add: name => classes.add(name), remove: name => classes.delete(name),
            contains: name => classes.has(name), toggle: () => {},
        },
        children: [],
        appendChild(child) { this.children.push(child); }, focus() {}, querySelector: () => element(),
    };
};
export const elements = new Map();
globalThis.document = {
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
