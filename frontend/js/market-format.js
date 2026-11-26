const number = (value, minimumFractionDigits = 2, maximumFractionDigits = minimumFractionDigits) =>
    value.toLocaleString('ja-JP', { minimumFractionDigits, maximumFractionDigits });

export function formatMarketValue(value, unit) {
    if (!Number.isFinite(value)) return '—';
    const sign = value < 0 ? '−' : '';
    const magnitude = Math.abs(value);
    const amount = number(magnitude);
    switch (unit) {
        case 'jpy': return `${sign}${amount}円`;
        case 'usd': return `${sign}$${amount}`;
        case 'usd_per_oz': return `${sign}$${number(magnitude, 1, 2)}/oz`;
        case 'usd_per_barrel': return `${sign}$${amount}/バレル`;
        case 'yield_pct': return `${sign}${number(magnitude, 2, 3)}%`;
        default: return `${sign}${amount}`;
    }
}

export function formatYieldChange(value) {
    if (!Number.isFinite(value)) return '—';
    const sign = value < 0 ? '−' : value > 0 ? '+' : '';
    return `${sign}${number(Math.abs(value), 2, 3)} pt`;
}

export function formatMarketChange(item) {
    const value = item.unit === 'yield_pct' ? item.change_pt : item.change;
    if (!Number.isFinite(value)) return { text: '前日比 —', direction: 'flat' };
    const direction = value < 0 ? 'down' : value > 0 ? 'up' : 'flat';
    const label = { up: '▲ 上昇', down: '▼ 下落', flat: '→ 横ばい' }[direction];
    if (item.unit === 'yield_pct') return { text: `前日比 ${label} ${formatYieldChange(value)}`, direction };
    const sign = value > 0 ? '+' : '';
    const pct = Number.isFinite(item.change_pct)
        ? `（${item.change_pct > 0 ? '+' : ''}${number(item.change_pct)}%）` : '';
    return { text: `前日比 ${label} ${sign}${formatMarketValue(value, item.unit)}${pct}`, direction };
}
