/** Wire the shared dialog keyboard, focus and submit behavior. */

import { createDialog } from './dialog.js';

export function createActionModal(id) {
    const part = name => document.getElementById(`${id}-${name}`);
    const dialog = createDialog(`${id}-overlay`);
    let active = null;

    function setError(message = '') {
        const error = part('error');
        error.textContent = message;
        error.classList.toggle('hidden', !message);
    }

    function close() {
        if (!active) return;
        const { onClose } = active;
        active = null;
        dialog.close(onClose);
    }

    function open({ focus, opener = document.activeElement, fallbackFocus, onSubmit, onClose }) {
        active = { onSubmit, onClose };
        setError();
        part('submit').disabled = false;
        dialog.open(focus, { opener, fallbackFocus });
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
        dialog.bind(close);
        overlay.addEventListener('keydown', event => {
            if (!active || event.isComposing || event.keyCode === 229) return;
            if (event.key === 'Enter' && !event.target.closest('button') &&
                !(event.target.tagName === 'TEXTAREA' && event.shiftKey)) {
                event.preventDefault();
                part('form').requestSubmit();
            }
        });
    }

    return { open, close, bind };
}
