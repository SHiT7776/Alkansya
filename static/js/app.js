/* Alkansya: shared front-end helpers (vanilla JS) */
(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const icons = () => window.lucide && window.lucide.createIcons();

  /* ---------- Toasts ---------- */
  function toast(message, type = 'info', opts = {}) {
    const root = document.getElementById('toasts');
    if (!root || !message) return;
    const styles = {
      success: ['bg-forest text-cream', 'circle-check'],
      error: ['bg-rose-600 text-white', 'circle-alert'],
      info: ['bg-ink text-cream', 'info'],
    };
    const [tone, icon] = styles[type] || styles.info;
    const el = document.createElement('div');
    el.className = `toast pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium shadow-2xl ${tone}`;
    el.innerHTML = `<i data-lucide="${icon}" class="h-5 w-5 shrink-0"></i><span class="flex-1"></span>`;
    el.querySelector('span').textContent = message;
    const dismiss = () => { el.classList.add('toast-out'); setTimeout(() => el.remove(), 250); };
    if (opts.action) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shrink-0 rounded-full bg-white/15 px-3 py-1 text-xs font-semibold transition hover:bg-white/25';
      btn.textContent = opts.action;
      btn.addEventListener('click', () => { opts.onAction?.(); dismiss(); });
      el.appendChild(btn);
    }
    root.appendChild(el);
    icons();
    setTimeout(dismiss, opts.duration || 4500);
  }

  /* ---------- Idle auto sign-out ---------- */
  const idleSeconds = parseInt(document.body.dataset.idle || '0', 10);
  let idleDeadline = Date.now() + idleSeconds * 1000;
  let idleWarned = false;
  let leaving = false;
  function resetIdle() { idleDeadline = Date.now() + idleSeconds * 1000; idleWarned = false; }

  const idleEls = document.querySelectorAll('[data-idle-countdown]');
  if (idleSeconds > 0 && idleEls.length) {
    const tick = () => {
      const left = Math.max(0, Math.round((idleDeadline - Date.now()) / 1000));
      const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
      idleEls.forEach((el) => { el.textContent = text; });
      if (left <= 60 && !idleWarned) {
        idleWarned = true;
        toast('Still there? For your security you will be signed out in 1 minute.', 'info',
          { action: 'Stay signed in', onAction: () => api('/api/ping'), duration: 55000 });
      }
      if (left <= 0 && !leaving) { leaving = true; window.location.href = '/logout?reason=timeout'; }
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ---------- API (fetch + CSRF) ---------- */
  async function api(url, data = {}) {
    let res;
    try {
      res = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(data),
      });
    } catch (_) {
      return { ok: false, error: 'Network error. Please check your connection and try again.' };
    }
    let body;
    try { body = await res.json(); } catch (_) { body = { ok: false, error: `Something went wrong (${res.status}). Please try again.` }; }
    if (!body.ok && body.redirect) {
      setTimeout(() => { window.location.href = body.redirect; }, 1800);
    } else {
      resetIdle();
    }
    return body;
  }

  /* ---------- Modals ---------- */
  let lastFocus = null;
  function openModal(id) {
    const m = typeof id === 'string' ? document.getElementById(id) : id;
    if (!m) return;
    lastFocus = document.activeElement;
    m.classList.remove('hidden');
    m.classList.add('flex');
    document.body.classList.add('overflow-hidden');
    requestAnimationFrame(() => m.classList.add('is-open'));
    setTimeout(() => {
      const target = m.querySelector('input:not([type=hidden])') || m.querySelector('[data-continue], [data-done]');
      target?.focus();
    }, 80);
  }
  function closeModal(id) {
    const m = typeof id === 'string' ? document.getElementById(id) : id;
    if (!m) return;
    m.classList.remove('is-open', 'flex');
    m.classList.add('hidden');
    if (!document.querySelector('.modal.flex')) document.body.classList.remove('overflow-hidden');
    m.querySelectorAll('form').forEach((f) => f.reset());
    m.querySelectorAll('[data-error]').forEach((e) => e.classList.add('hidden'));
    lastFocus?.focus?.();
  }

  function showError(form, message) {
    const box = form.querySelector('[data-error]');
    if (!box) { toast(message, 'error'); return; }
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function setBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<svg class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-opacity=".25" stroke-width="3"/><path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>Processing…';
    } else {
      btn.disabled = false;
      if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
    }
  }

  /* ---------- Global listeners ---------- */
  document.addEventListener('click', (e) => {
    const opener = e.target.closest('[data-open]');
    if (opener) { openModal(opener.dataset.open); return; }

    const closer = e.target.closest('[data-close]');
    if (closer) { closeModal(closer.closest('.modal')); return; }

    if (e.target.classList?.contains('modal') && !e.target.dataset.static) { closeModal(e.target); return; }

    const eye = e.target.closest('.toggle-secret');
    if (eye) {
      const input = eye.parentElement.querySelector('input');
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.innerHTML = `<i data-lucide="${show ? 'eye-off' : 'eye'}" class="h-5 w-5"></i>`;
      icons();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const open = [...document.querySelectorAll('.modal.flex')].pop();
    if (open && !open.dataset.static) closeModal(open);
  });

  // PIN fields accept digits only.
  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-digits]')) e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
  });

  // Show a spinner on classic (non-AJAX) form submits.
  document.querySelectorAll('form[data-loading-form]').forEach((f) => {
    f.addEventListener('submit', () => setBusy(f.querySelector('[type=submit]'), true));
  });

  window.Alk = { api, toast, openModal, closeModal, showError, setBusy, icons, resetIdle };

  icons();
  (window.FLASHES || []).forEach(([category, message]) =>
    toast(message, category === 'error' ? 'error' : category === 'success' ? 'success' : 'info'));
})();
