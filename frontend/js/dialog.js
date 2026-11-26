const focusable = 'button, a[href], input, select, textarea, [tabindex]';
const openOverlays = [];

function canFocus(element) {
    return element?.isConnected && !element.matches(':disabled') && element.getClientRects().length;
}

export function createDialog(id) {
    const overlay = () => document.getElementById(id);
    let session = null;

    function open(focus, { opener = document.activeElement, fallbackFocus } = {}) {
        const element = overlay();
        if (session?.element !== element || element.classList.contains('hidden')) {
            session = { element, opener, fallbackFocus };
            if (!openOverlays.includes(element)) openOverlays.push(element);
        }
        element.classList.remove('hidden');
        (focus ?? element.querySelector('.modal')).focus();
    }

    function close(onClose) {
        if (!session) return;
        const { element, opener, fallbackFocus } = session;
        session = null;
        openOverlays.splice(openOverlays.indexOf(element), 1);
        element.classList.add('hidden');
        onClose?.();
        const target = [opener, fallbackFocus].find(canFocus) ?? document.getElementById('main-content');
        target?.focus();
    }

    function bind(closeDialog = close) {
        const element = overlay();
        const isTop = () => session?.element === element && openOverlays.at(-1) === element;
        const stops = () => [...element.querySelectorAll(focusable)].filter(stop => canFocus(stop) && stop.tabIndex >= 0);
        const focusEdge = backwards => (stops().at(backwards ? -1 : 0) ?? element.querySelector('.modal')).focus();
        element?.addEventListener('click', event => {
            if (event.target === element) closeDialog();
        });
        function handleKey(event) {
            if (!isTop() || event.isComposing || event.keyCode === 229) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeDialog();
            } else if (event.key === 'Tab') {
                const fields = stops();
                const index = fields.indexOf(document.activeElement);
                const edge = event.shiftKey ? 0 : fields.length - 1;
                if (index === -1 || index === edge) {
                    event.preventDefault();
                    focusEdge(event.shiftKey);
                }
            }
        }
        element?.addEventListener('keydown', handleKey);
        document.addEventListener('keydown', event => {
            if (isTop() && !element.contains(event.target)) handleKey(event);
        });
        document.addEventListener('focusin', event => {
            if (isTop() && !element.contains(event.target)) focusEdge(false);
        });
    }

    return { open, close, bind };
}
