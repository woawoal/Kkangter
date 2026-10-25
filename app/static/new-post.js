// 글쓰기: 구분 전환, 캐릭터 선택기, 사진 리사이즈 후 업로드
(() => {
  const form = document.getElementById('f');
  const picked = { have: new Map(), want: new Map() };
  let photos = []; // {blob, url}

  // ---- 구분 전환 ----
  const curType = () => form.querySelector('input[name=type]:checked').value;
  function applyType() {
    const t = curType();
    document.querySelectorAll('[data-show]').forEach(el => {
      el.style.display = el.dataset.show.split(' ').includes(t) ? '' : 'none';
    });
    document.getElementById('have-label').textContent = t === 'sell' ? '양도할 인형' : '내가 줄 인형';
    document.getElementById('want-label').textContent = t === 'want' ? '구하는 인형' : '받고 싶은 인형';
    document.getElementById('price-label').textContent = t === 'want' ? '희망 가격' : '전체 가격 (일괄)';
    renderPicked('have');
  }
  form.querySelectorAll('input[name=type]').forEach(r => r.addEventListener('change', applyType));

  // ---- 캐릭터 선택기 ----
  const esc = s => String(s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const norm = s => (s || '').toLowerCase().replace(/\s/g, '');
  document.querySelectorAll('.picker').forEach(box => {
    const role = box.dataset.role;
    box.innerHTML = `<input class="q" type="search" placeholder="🔍 캐릭터·시리즈 검색"><div class="ser"></div><div class="chars"></div>`;
    const q = box.querySelector('.q'), ser = box.querySelector('.ser'), chars = box.querySelector('.chars');
    let sel = CATALOG[0] ? CATALOG[0].id : null;
    ser.innerHTML = CATALOG.map(s => `<button type="button" data-id="${s.id}">${esc(s.name)}</button>`).join('');
    ser.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; sel = +b.dataset.id; q.value = ''; draw(); });
    q.addEventListener('input', draw);
    chars.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      const id = +b.dataset.id;
      if (picked[role].has(id)) picked[role].delete(id);
      else picked[role].set(id, { name: b.dataset.name, price: '' });
      renderPicked(role); draw();
    });
    function draw() {
      const term = norm(q.value);
      let list = [];
      if (term) {
        CATALOG.forEach(s => s.chars.forEach(c => {
          if ([c.name, c.aliases, s.name].some(x => norm(x).includes(term))) list.push({ ...c, sname: s.name });
        }));
      } else {
        const s = CATALOG.find(s => s.id === sel); list = s ? s.chars.map(c => ({ ...c, sname: '' })) : [];
      }
      ser.querySelectorAll('button').forEach(b => b.classList.toggle('on', !term && +b.dataset.id === sel));
      chars.innerHTML = list.length ? list.map(c =>
        `<button type="button" data-id="${c.id}" data-name="${esc(c.name)}" class="${picked[role].has(c.id) ? 'on' : ''}">${esc(c.name)}${c.secret ? ' ✦' : ''}${c.sname ? `<br><small class="muted">${esc(c.sname)}</small>` : ''}</button>`
      ).join('') : '<div class="muted small" style="grid-column:1/-1;padding:8px">검색 결과가 없어요</div>';
    }
    box._draw = draw; draw();
  });

  function renderPicked(role) {
    const el = document.getElementById(role + '-picked');
    const withPrice = role === 'have' && curType() === 'sell';
    el.innerHTML = [...picked[role]].map(([id, v]) =>
      `<span class="pick">${esc(v.name)}${withPrice ? ` <input inputmode="numeric" placeholder="가격(원)" data-id="${id}" value="${esc(v.price)}">` : ''}<button type="button" data-id="${id}" aria-label="삭제">×</button></span>`
    ).join('');
    el.querySelectorAll('button').forEach(b => b.onclick = () => {
      picked[role].delete(+b.dataset.id); renderPicked(role);
      document.querySelector(`.picker[data-role=${role}]`)._draw();
    });
    el.querySelectorAll('input').forEach(i => i.oninput = () => { picked[role].get(+i.dataset.id).price = i.value; });
  }
  if (PRESET) picked[curType() === 'want' ? 'want' : 'have'].set(PRESET.id, { name: PRESET.name, price: '' });

  // ---- 사진: 긴 변 1600px JPEG로 줄여서 업로드 (모바일 데이터 절약) ----
  const input = document.getElementById('photo-input'), wrap = document.getElementById('photos');
  input.addEventListener('change', async () => {
    for (const f of [...input.files]) {
      if (photos.length >= MAX) { alert(`사진은 최대 ${MAX}장까지예요`); break; }
      const blob = await shrink(f).catch(() => f);
      photos.push({ blob, url: URL.createObjectURL(blob) });
    }
    input.value = ''; drawPhotos();
  });
  function drawPhotos() {
    wrap.querySelectorAll('.th').forEach(n => n.remove());
    photos.forEach((p, i) => {
      const d = document.createElement('div'); d.className = 'th'; d.style.backgroundImage = `url(${p.url})`;
      d.innerHTML = `<button type="button" style="position:absolute;top:-6px;right:-6px;width:22px;height:22px;border-radius:50%;border:0;background:#2B2320;color:#fff">×</button>`;
      d.querySelector('button').onclick = () => { photos.splice(i, 1); drawPhotos(); };
      wrap.insertBefore(d, wrap.querySelector('.add'));
    });
  }
  function shrink(file, max = 1600) {
    return new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => {
        const r = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement('canvas'); c.width = img.width * r; c.height = img.height * r;
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        c.toBlob(b => b ? res(b) : rej(), 'image/jpeg', 0.85);
      };
      img.onerror = rej; img.src = URL.createObjectURL(file);
    });
  }

  // ---- 거래방법 ----
  const ship = () => document.getElementById('ship_method').value =
    [...document.querySelectorAll('#ship input:checked')].map(i => i.value).join(', ');

  // ---- 카탈로그 추가 요청 ----
  document.getElementById('req').onclick = async e => {
    e.preventDefault();
    const t = prompt('추가할 시리즈와 캐릭터 이름을 알려주세요 (예: OO 시리즈 - XX)');
    if (!t) return;
    const fd = new FormData(); fd.append('text', t);
    await fetch('/api/catalog-request', { method: 'POST', body: fd });
    alert('요청했어요! 확인 후 추가할게요');
  };

  // ---- 제출 ----
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const t = curType(), err = [];
    if (t !== 'want' && !picked.have.size) err.push('가지고 있는 인형을 선택해 주세요');
    if (t !== 'sell' && !picked.want.size) err.push('원하는 인형을 선택해 주세요');
    if (t !== 'want' && !photos.length) err.push('실물 사진을 1장 이상 올려 주세요');
    if (err.length) { alert(err.join('\n')); return; }
    ship();
    const fd = new FormData();
    fd.append('type', t);
    ['price', 'note', 'ship_method'].forEach(n => fd.append(n, form.elements[n].value));
    if (t !== 'want') picked.have.forEach((v, id) => { fd.append('have', id); if (v.price) fd.append('price_' + id, v.price); });
    if (t !== 'sell') picked.want.forEach((v, id) => fd.append('want', id));
    if (t !== 'want') photos.forEach((p, i) => fd.append('photos', p.blob, `photo${i}.jpg`));
    const btn = document.getElementById('submit'); btn.disabled = true; btn.textContent = '올리는 중…';
    try {
      const res = await fetch('/new', { method: 'POST', body: fd });
      if (res.redirected) { location.href = res.url; return; }
      document.open(); document.write(await res.text()); document.close();
    } catch (_) {
      alert('네트워크 오류가 났어요. 다시 시도해 주세요'); btn.disabled = false; btn.textContent = '올리기';
    }
  });

  applyType();
})();
