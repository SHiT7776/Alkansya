/* Alkansya: admin console */
(() => {
  const { api, toast, openModal, closeModal, showError, setBusy } = window.Alk;
  const $ = (s, root = document) => root.querySelector(s);

  const randomPin = () => {
    const digits = Array.from(crypto.getRandomValues(new Uint32Array(6)), (x) => x % 10).join('');
    return new Set(digits).size > 2 ? digits : randomPin();
  };

  $('[data-generate-pin]').addEventListener('click', () => { $('#create-form').pin.value = randomPin(); });

  /* ---------- Credentials modal ---------- */
  function showCredentials(customer, title) {
    const m = $('#cred-modal');
    $('#cred-modal-title').textContent = title;
    m.querySelector('[data-c="name"]').textContent = customer.name;
    m.querySelector('[data-c="username"]').textContent = `@${customer.username}`;
    m.querySelector('[data-c="account"]').textContent = customer.account;
    m.querySelector('[data-c="pin"]').textContent = customer.pin;
    openModal(m);
  }

  $('[data-copy-pin]').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('#cred-modal [data-c="pin"]').textContent);
      toast('PIN copied to clipboard.', 'success');
    } catch (_) {
      toast("Couldn't copy. Please write it down.", 'error');
    }
  });

  document.querySelectorAll('#cred-modal [data-close], #cred-modal [data-done]')
    .forEach((b) => b.addEventListener('click', () => window.location.reload()));

  /* ---------- Create customer ---------- */
  $('#create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('[type=submit]');
    const data = Object.fromEntries(new FormData(form));
    if (!data.full_name.trim() || !data.username.trim()) return showError(form, 'Please fill in the name and username.');
    if (!/^\d{4,6}$/.test(data.pin)) return showError(form, 'The default PIN must be 4 to 6 digits.');
    setBusy(btn, true);
    const r = await api('/admin/api/customers', data);
    setBusy(btn, false);
    if (!r.ok) return showError(form, r.error);
    closeModal('create-modal');
    showCredentials(r.customer, 'Account ready!');
  });

  /* ---------- Unlock / reset PIN ---------- */
  document.addEventListener('click', async (e) => {
    const unlock = e.target.closest('[data-unlock]');
    if (unlock) {
      setBusy(unlock, true);
      const r = await api(`/admin/api/customers/${unlock.dataset.unlock}/unlock`);
      if (!r.ok) { setBusy(unlock, false); toast(r.error, 'error'); return; }
      toast(r.message, 'success');
      setTimeout(() => window.location.reload(), 700);
      return;
    }
    const reset = e.target.closest('[data-reset]');
    if (reset) {
      if (!window.confirm(`Issue a new temporary PIN for ${reset.dataset.name}? Their current PIN will stop working.`)) return;
      setBusy(reset, true);
      const r = await api(`/admin/api/customers/${reset.dataset.reset}/reset-pin`);
      setBusy(reset, false);
      if (!r.ok) { toast(r.error, 'error'); return; }
      showCredentials(r.customer, 'Temporary PIN issued');
    }
  });

  /* ---------- Search ---------- */
  $('#customer-search')?.addEventListener('input', (e) => {
    const q = e.target.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.customer-row').forEach((row) => {
      const hit = row.dataset.search.includes(q);
      row.classList.toggle('hidden', !hit);
      shown += hit ? 1 : 0;
    });
    $('#no-match')?.classList.toggle('hidden', shown > 0);
  });
})();
