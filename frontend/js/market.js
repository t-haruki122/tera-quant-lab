import { fetchAPI } from './api.js';
import { formatMarketChange, formatMarketValue } from './market-format.js';
import { normalizeSymbol, serializeRoute } from './router.js';
import { formatTimestamp } from './utils.js';

const svgNS = 'http://www.w3.org/2000/svg';

function element(doc, tag, className, content = '') {
    const node = doc.createElement(tag);
    if (className) node.classList.add(className);
    node.textContent = content;
    return node;
}

function renderSparkline(doc, values) {
    const data = (Array.isArray(values) ? values.filter(Number.isFinite) : []).slice(-30);
    if (data.length < 2) return element(doc, 'span', 'market-card-meta', '日次履歴なし');
    const min = Math.min(...data);
    const span = Math.max(...data) - min;
    const points = data.map((value, index) => {
        const x = 2 + index / (data.length - 1) * 116;
        const y = span ? 26 - (value - min) / span * 24 : 14;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
    });
    const svg = doc.createElementNS(svgNS, 'svg');
    svg.classList.add('market-sparkline');
    svg.setAttribute('viewBox', '0 0 120 28');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `直近${data.length}件の日次終値の推移`);
    const line = doc.createElementNS(svgNS, 'polyline');
    line.setAttribute('points', points.join(' '));
    line.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.appendChild(line);
    return svg;
}

function createCard(doc, symbol, grid) {
    const link = element(doc, 'a', 'market-card');
    link.setAttribute('href', serializeRoute({ view: 'detail', symbol }));
    const title = element(doc, 'h3', 'market-card-name');
    const value = element(doc, 'p', 'market-card-value');
    const placeholder = element(doc, 'div', 'market-placeholder');
    placeholder.setAttribute('aria-hidden', 'true');
    const change = element(doc, 'p', 'market-card-change');
    const chart = element(doc, 'div', 'market-card-chart');
    const meta = element(doc, 'p', 'market-card-meta');
    const time = element(doc, 'time');
    const stale = element(doc, 'span', 'market-stale', '古いデータ');
    meta.append(time, stale);
    const note = element(doc, 'p', 'market-card-meta');
    const error = element(doc, 'p', 'text-error');
    link.append(title, value, placeholder, change, chart, meta, note, error);
    grid.appendChild(link);

    function setLoading(loading) {
        placeholder.hidden = !loading;
        value.hidden = loading;
        change.hidden = loading;
        chart.hidden = loading && !chart.firstElementChild?.matches('svg');
    }

    return {
        setLoading,
        update(item, loading) {
            title.textContent = item.name || symbol;
            value.textContent = formatMarketValue(item.value, item.unit);
            const movement = formatMarketChange(item);
            change.textContent = movement.text;
            change.dataset.direction = movement.direction;
            chart.replaceChildren(renderSparkline(doc, item.sparkline));
            time.textContent = item.as_of ? `更新時刻 ${formatTimestamp(item.as_of)}` : '更新時刻 —';
            if (item.as_of) time.setAttribute('datetime', item.as_of);
            else time.removeAttribute('datetime');
            stale.hidden = !item.stale;
            note.textContent = item.note ?? '';
            note.hidden = !item.note;
            error.textContent = item.error ?? '';
            error.hidden = !item.error;
            setLoading(loading && !Number.isFinite(item.value));
        },
    };
}

export function createMarketView({ documentRef = document, request = fetchAPI } = {}) {
    const groups = new Map();
    const cards = new Map();
    let sequence = 0;

    function paint(items, loading) {
        const doc = documentRef;
        for (const item of items) {
            const symbol = normalizeSymbol(item.symbol);
            if (!symbol) continue;
            if (!groups.has(item.group)) {
                const section = element(doc, 'section', 'market-group');
                const grid = element(doc, 'div', 'market-grid');
                section.append(element(doc, 'h2', null, item.group), grid);
                doc.getElementById('market-groups').appendChild(section);
                groups.set(item.group, grid);
            }
            if (!cards.has(symbol)) cards.set(symbol, createCard(doc, symbol, groups.get(item.group)));
            cards.get(symbol).update(item, loading);
        }
        doc.getElementById('market-skeleton').hidden = cards.size > 0 || !loading;
    }

    return {
        cancel() { ++sequence; },
        async load() {
            const id = ++sequence;
            const status = documentRef.getElementById('market-status');
            const skeleton = documentRef.getElementById('market-skeleton');
            const options = { cache: 'no-store' };
            status.textContent = '経済指標を読み込み中…';
            skeleton.hidden = cards.size > 0;
            try {
                const data = await request('/market/overview?cached=true', options);
                if (id !== sequence) return;
                paint(data.indicators, true);
            } catch {
                if (id !== sequence) return;
            }
            status.textContent = '経済指標を更新中…';
            try {
                const data = await request('/market/overview?cached=false', options);
                if (id !== sequence) return;
                paint(data.indicators, false);
                status.textContent = data.indicators.some(item => item.error)
                    ? '一部のデータを更新できませんでした。' : '経済指標を更新しました。';
            } catch (error) {
                if (id !== sequence) return;
                skeleton.hidden = true;
                for (const card of cards.values()) card.setLoading(false);
                status.textContent = `経済指標を更新できませんでした。 ${error.message}`;
            }
        },
    };
}
