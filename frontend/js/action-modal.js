/** Wire the shared dialog keyboard, focus and submit behavior. */

export function createActionModal(id) {
    const part = name => document.getElementById(`${id}-${name}`);
    let active = null;

    function setError(message = '') {
        const error = part('error');
        error.textContent = message;
        error.classList.toggle('hidden', !message);
    }

    function close() {
        if (!active) return;
        const { opener, fallbackFocus, onClose } = active;
        active = null;
        part('overlay').classList.add('hidden');
        onClose?.();
        const focusTarget = opener?.isConnected && !opener.disabled ? opener : fallbackFocus;
        focusTarget?.focus();
    }

    function open({ focus, opener = document.activeElement, fallbackFocus, onSubmit, onClose }) {
        active = { opener, fallbackFocus, onSubmit, onClose };
        setError();
        part('submit').disabled = false;
        part('overlay').classList.remove('hidden');
        focus.focus();
    }

    async function submit(event) {
        event.preventDefault();
        const action = active;
        const button = part('submit');
        if (!action || button.disabled) return;
        button.disabled = true;
        setError();
        try {
            await action.onSubmit();
            if (active === action) close();
        } catch (error) {
            if (active === action) setError(error.message);
        } finally {
            if (active === action) button.disabled = false;
        }
    }

    function bind() {
        const overlay = part('overlay');
        part('close').addEventListener('click', close);
        part('cancel').addEventListener('click', close);
        part('form').addEventListener('submit', submit);
        overlay.addEventListener('click', event => {
            if (event.target === overlay) close();
        });
        overlay.addEventListener('keydown', event => {
            if (!active || event.isComposing) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                close();
            } else if (event.key === 'Enter' && !event.target.closest('button') &&
                !(event.target.tagName === 'TEXTAREA' && event.shiftKey)) {
                event.preventDefault();
                part('form').requestSubmit();
            } else if (event.key === 'Tab') {
                const stops = [...overlay.querySelectorAll('button, input, textarea')]
                    .filter(element => !element.disabled && element.getClientRects().length);
                const first = stops[0];
                const last = stops.at(-1);
                if (event.shiftKey && event.target === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && event.target === last) {
                    event.preventDefault();
                    first.focus();
                }
            }
        });
    }

    return { open, close, bind };
}
