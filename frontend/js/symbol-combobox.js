import { fetchAPI } from './api.js';
import { normalizeSymbol } from './router.js';

export function bindSymbolCombobox(input, listbox, {
    onSubmit, getLocalItems = () => [], request = fetchAPI, delay = 200,
} = {}) {
    if (!input || !listbox) return;
    const owner = input.ownerDocument ?? document;
    const listId = input.getAttribute('aria-controls');
    let composing = false;
    let sequence = 0;
    let timer;
    let items = [];
    let selected = -1;

    function activate(index) {
        if (selected >= 0) listbox.children[selected]?.setAttribute('aria-selected', 'false');
        selected = index;
        const option = listbox.children[index];
        if (option) {
            option.setAttribute('aria-selected', 'true');
            input.setAttribute('aria-activedescendant', option.id);
            option.scrollIntoView?.({ block: 'nearest' });
        } else {
            input.removeAttribute('aria-activedescendant');
        }
    }

    function show(results) {
        const previous = items[selected]?.symbol;
        const seen = new Set();
        items = [];
        listbox.replaceChildren();
        selected = -1;
        for (const result of results) {
            const symbol = normalizeSymbol(result.symbol);
            if (!symbol || seen.has(symbol)) continue;
            seen.add(symbol);
            const option = owner.createElement('div');
            const index = items.length;
            option.id = `${listId}-${index}`;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            option.textContent = [symbol, result.name].filter(Boolean).join(' ') +
                (result.segment ? `（${result.segment}）` : '');
            option.addEventListener('mousedown', event => event.preventDefault());
            option.addEventListener('click', () => choose(index));
            items.push({ symbol });
            listbox.appendChild(option);
        }
        listbox.hidden = items.length === 0;
        input.setAttribute('aria-expanded', String(items.length > 0));
        activate(items.findIndex(item => item.symbol === previous));
    }

    function close() {
        sequence += 1;
        clearTimeout(timer);
        show([]);
    }

    function choose(index) {
        const item = items[index];
        if (!item) return;
        input.value = item.symbol;
        close();
        onSubmit?.();
    }

    function suggest() {
        close();
        const raw = input.value;
        const query = raw.trim();
        if (composing || [...query].length > 50) return;
        const local = getLocalItems(query).slice(0, 10);
        if (local.length) show(local);
        if (!query) return;
        const current = sequence;
        timer = setTimeout(async () => {
            try {
                const data = await request(`/symbols/search?q=${encodeURIComponent(query)}`, { anonymous: true });
                if (current === sequence && raw === input.value && !composing) {
                    show([...data.items, ...local].slice(0, 20));
                }
            } catch {
                // Manual ticker entry and any watchlist suggestions stay available.
            }
        }, delay);
    }

    input.addEventListener('input', event => { if (!event.isComposing) suggest(); });
    input.addEventListener('compositionstart', () => { composing = true; close(); });
    input.addEventListener('compositionend', () => { composing = false; suggest(); });
    input.addEventListener('blur', close);
    input.addEventListener('keydown', event => {
        if (composing || event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Escape' && items.length) {
            event.preventDefault();
            event.stopPropagation();
            close();
        } else if (event.key === 'Escape') {
            close();
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            const down = event.key === 'ArrowDown';
            if (!items.length && !input.value) suggest();
            if (!items.length) return;
            event.preventDefault();
            const next = selected < 0 ? (down ? 0 : items.length - 1) : selected + (down ? 1 : -1);
            activate((next + items.length) % items.length);
        } else if (event.key === 'Enter') {
            if (selected >= 0) {
                event.preventDefault();
                choose(selected);
            } else {
                close();
                if (onSubmit) {
                    event.preventDefault();
                    onSubmit();
                }
            }
        }
    });
}
