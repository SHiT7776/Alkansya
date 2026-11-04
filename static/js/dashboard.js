/* Alkansya: customer dashboard */
(() => {
  const { api, toast, openModal, closeModal, showError, setBusy, icons } = window.Alk;
  const $ = (s, root = document) => root.querySelector(s);
  const PIN_OK = /^\d{4,6}$/;

  const setEye = (btn, name, size = 'h-5 w-5') => {
    btn.innerHTML = `<i data-lucide="${name}" class="${size}"></i>`;
    icons();
  };

  /* ---------- Quick amount chips ---------- */
  document.querySelectorAll('[data-quick]').forEach((chip) => chip.addEventListener('click', () => {
    const form = chip.closest('form');
    form.amount.value = chip.dataset.quick;
    form.pin.focus();
  }));

  /* ---------- Balance: PIN-protected peek that auto-hides ---------- */
  const balance = $('#balance');
  const hint = $('#balance-hint');
  const balBtn = $('#balance-toggle');
  const HIDDEN = '₱ ••••••';
  const HINT = hint.textContent;
  let hideTimer;

  function hideBalance() {
    clearInterval(hideTimer);
    balance.textContent = HIDDEN;
    hint.textContent = HINT;
    if (balBtn.dataset.shown) { balBtn.dataset.shown = ''; setEye(balBtn, 'eye'); }
  }

  balBtn.addEventListener('click', () => (balBtn.dataset.shown ? hideBalance() : openModal('balance-modal')));

  $('#balance-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('[type=submit]');
    const pin = form.pin.value;
    if (!PIN_OK.test(pin)) return showError(form, 'Please enter your 4–6 digit PIN.');
    setBusy(btn, true);
    const r = await api('/api/balance', { pin });
    setBusy(btn, false);
    if (!r.ok) { form.pin.value = ''; return showError(form, r.error); }
    closeModal('balance-modal');
    balance.textContent = r.balance;
    balBtn.dataset.shown = '1';
    setEye(balBtn, 'eye-off');
    let left = r.hide_in;
    hint.textContent = `PIN verified · hiding again in ${left}s`;
    clearInterval(hideTimer);
    hideTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) { hideBalance(); return; }
      hint.textContent = `PIN verified · hiding again in ${left}s`;
    }, 1000);
    toast('Balance verified with your PIN.', 'success');
  });

  /* ---------- Account number mask ---------- */
  const acct = $('#acct');
  const acctBtn = $('#acct-toggle');
  let acctTimer;
  acctBtn.addEventListener('click', () => {
    const show = acct.textContent.includes('•');
    acct.textContent = show ? acct.dataset.full : acct.dataset.masked;
    setEye(acctBtn, show ? 'eye-off' : 'eye', 'h-4 w-4');
    clearTimeout(acctTimer);
    if (show) {
      acctTimer = setTimeout(() => { acct.textContent = acct.dataset.masked; setEye(acctBtn, 'eye', 'h-4 w-4'); }, 10000);
    }
  });

  /* ---------- Deposit / withdraw ---------- */
  document.querySelectorAll('form[data-txn]').forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = form.querySelector('[type=submit]');
    const amount = form.amount.value.replace(/[,₱\s]/g, '');
    const pin = form.pin.value;
    if (!amount || !(Number(amount) > 0)) return showError(form, 'Please enter an amount greater than zero.');
    if (!PIN_OK.test(pin)) return showError(form, 'Please enter your 4–6 digit PIN to authorize.');
    setBusy(btn, true);
    const r = await api('/api/transaction', { type: form.dataset.txn, amount, pin });
    setBusy(btn, false);
    if (!r.ok) { form.pin.value = ''; return showError(form, r.error); }
    closeModal(form.closest('.modal'));
    $('#txn-empty')?.remove();
    $('#txn-list').insertAdjacentHTML('afterbegin', r.row);
    icons();
    hideBalance();
    showReceipt(r.receipt, r.signout_in);
  }));

  /* ---------- Receipt + auto sign-out after each transaction ---------- */
  const receipt = $('#receipt-modal');
  let receiptTimer;

  function showReceipt(data, seconds) {
    const set = (key, value) => { const el = receipt.querySelector(`[data-r="${key}"]`); if (el) el.textContent = value; };
    set('title', data.title);
    set('amount', `${data.kind === 'deposit' ? '+' : '−'}${data.amount}`);
    ['reference', 'date', 'account', 'balance'].forEach((k) => set(k, data[k]));
    openModal(receipt);

    const bar = receipt.querySelector('[data-r="bar"]');
    bar.style.transition = 'none';
    bar.style.width = '100%';
    requestAnimationFrame(() => requestAnimationFrame(() => {
      bar.style.transition = `width ${seconds}s linear`;
      bar.style.width = '0%';
    }));

    const deadline = Date.now() + seconds * 1000;
    clearInterval(receiptTimer);
    const tick = () => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      set('countdown', left);
      if (left <= 0) { clearInterval(receiptTimer); window.location.href = '/logout?reason=auto'; }
    };
    tick();
    receiptTimer = setInterval(tick, 250);
  }

  receipt.querySelector('[data-continue]').addEventListener('click', () => {
    clearInterval(receiptTimer);
    closeModal(receipt);
    toast('Ready when you are. Your next transaction will ask for your PIN again.', 'info');
  });

  /* ---------- Change PIN ---------- */
  $('#pin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('[type=submit]');
    const data = { current_pin: form.current_pin.value, new_pin: form.new_pin.value, confirm_pin: form.confirm_pin.value };
    if (!PIN_OK.test(data.current_pin) || !PIN_OK.test(data.new_pin)) return showError(form, 'PINs must be 4 to 6 digits.');
    if (data.new_pin !== data.confirm_pin) return showError(form, "The new PINs don't match.");
    setBusy(btn, true);
    const r = await api('/api/change-pin', data);
    if (!r.ok) { setBusy(btn, false); return showError(form, r.error); }
    closeModal('pin-modal');
    toast('PIN updated! Please sign in again with your new PIN.', 'success');
    setTimeout(() => { window.location.href = r.redirect; }, 1600);
  });
})();
