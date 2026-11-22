/* AgriSutra — customer simulator + decision cockpit (vanilla JS, no build step). */

const API = '/api/v1';
let sessionId = null;
let lastPayload = null;
let isUserScrolledUp = false;
let busy = false;
let observing = null;        // WhatsApp session being watched (read-only chat)
let observeTimer = null;

const $ = (id) => document.getElementById(id);
const chatMessages = $('chatMessages');
const chatInput = $('chatInput');
const btnSend = $('btnSend');
const welcomeScreen = $('welcomeScreen');

const esc = (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmtVal = (v) => v === true ? 'yes' : v === false ? 'no' : v == null ? '—' : String(v).replace(/_/g, ' ');
const rupee = (n) => n == null ? 'N/A' : '₹' + Number(n).toLocaleString('en-IN');

async function api(path, opts = {}) {
    const res = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* keep status */ }
        throw new Error(detail);
    }
    return res.json();
}

// ── Scrolling ────────────────────────────────────────────────
// Programmatic scrolls are instant: a smooth scroll fires intermediate scroll events that would
// wrongly mark the user as "scrolled up" while new content is still arriving.
function scrollToBottom(force = false) {
    if (force) isUserScrolledUp = false;
    requestAnimationFrame(() => { chatMessages.scrollTop = chatMessages.scrollHeight; });
}
chatMessages.addEventListener('scroll', () => {
    const gap = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
    isUserScrolledUp = gap > 120;
    $('btnScrollBottom').style.display = isUserScrolledUp ? 'flex' : 'none';
    if (!isUserScrolledUp) $('unreadDot').style.display = 'none';
});
$('btnScrollBottom').addEventListener('click', () => chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: 'smooth' }));

function updateMessageCount() {
    const n = chatMessages.querySelectorAll('.message:not(.typing-indicator-msg)').length;
    $('msgCount').textContent = `${n} ${n === 1 ? 'message' : 'messages'}`;
}

// ── Session ──────────────────────────────────────────────────
async function createSession() {
    try {
        $('sessionIdDisplay').textContent = 'Connecting…';
        setInputEnabled(false);
        const data = await api('/sessions', { method: 'POST' });
        stopObserving(false);
        sessionId = data.session_id;
        lastPayload = null;
        $('sessionIdDisplay').textContent = sessionId;
        chatMessages.innerHTML = '';
        chatMessages.appendChild(welcomeScreen);
        welcomeScreen.style.display = 'flex';
        updateMessageCount();
        resetCockpit();
        setInputEnabled(true);
        chatInput.focus();
    } catch (err) {
        $('sessionIdDisplay').textContent = 'Connection error';
        chatMessages.innerHTML = `<div class="welcome-screen"><div class="welcome-icon">⚠️</div>
            <h3>Can't reach the AgriSutra server</h3><p>Start it with <code>python run.py</code>, then retry.</p>
            <button class="starter-chip" id="btnRetry">Retry connection</button></div>`;
        $('btnRetry').addEventListener('click', createSession);
    }
}

function setInputEnabled(on) {
    chatInput.disabled = !on;
    btnSend.disabled = !on;
}

// ── Sending ──────────────────────────────────────────────────
async function sendMessage(text, clientMessageId = null, { echo = true } = {}) {
    if (!text || !text.trim() || !sessionId || busy || observing) return null;
    const trimmed = text.trim();
    busy = true;
    welcomeScreen.style.display = 'none';
    clearQuickReplies();
    if (echo) appendMessage('user', trimmed);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    updateMessageCount();
    scrollToBottom(true);
    setInputEnabled(false);
    const typing = lastPayload?.flags?.bot_paused ? null : showTyping();
    try {
        const data = await api(`/sessions/${sessionId}/messages`, {
            method: 'POST', body: JSON.stringify({ text: trimmed, client_message_id: clientMessageId }),
        });
        typing?.remove();
        if (data.duplicate_delivery) {
            appendSystem('Duplicate delivery detected — served the stored result; the dialogue did not advance twice.');
        } else if (data.reply) {
            const el = appendMessage('agent', data.reply);
            if (data.product_cards?.length) el.querySelector('.msg-body').appendChild(renderCards(data.product_cards));
            else if (data.images?.length) el.querySelector('.msg-body').appendChild(renderImageStrip(data.images));
            if (data.next_action === 'AWAIT_EXPLICIT_CONFIRMATION' && data.order_preview) {
                el.querySelector('.msg-body').appendChild(renderPreviewCard(data.order_preview));
            }
            renderQuickReplies(data.quick_replies || []);
        } else if (data.next_action === 'HUMAN_OWNS_CONVERSATION') {
            appendSystem('Delivered to the salesperson. The bot did not reply because a human owns this chat.');
        }
        updateMessageCount();
        if (isUserScrolledUp) $('unreadDot').style.display = 'block'; else scrollToBottom();
        lastPayload = data;
        renderCockpit(data);
        if (data.order && data.next_action === 'ORDER_CONFIRMED') showOrderModal(data.order);
        return data;
    } catch (err) {
        typing?.remove();
        appendSystem(`Couldn't process that message (${esc(err.message)}). Check the server and try again.`);
        return null;
    } finally {
        busy = false;
        setInputEnabled(true);
        chatInput.focus();
    }
}

// ── Chat rendering ───────────────────────────────────────────
function appendMessage(role, text) {
    const el = document.createElement('div');
    el.className = `message ${role === 'human_agent' ? 'agent human' : role}`;
    const avatar = role === 'user' ? '👤' : role === 'human_agent' ? '🧑‍💼' : '🌱';
    const who = role === 'human_agent' ? '<div class="msg-who">SGB salesperson</div>' : '';
    el.innerHTML = `<div class="msg-avatar">${avatar}</div><div class="msg-body"><div class="msg-bubble">${who}${renderMarkdown(text)}</div></div>`;
    chatMessages.appendChild(el);
    return el;
}

function appendSystem(text) {
    const el = document.createElement('div');
    el.className = 'system-note';
    el.textContent = text;
    chatMessages.appendChild(el);
    scrollToBottom();
}

function showTyping() {
    const el = document.createElement('div');
    el.className = 'message agent typing-indicator-msg';
    el.innerHTML = `<div class="msg-avatar">🌱</div><div class="msg-bubble typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>`;
    chatMessages.appendChild(el);
    scrollToBottom();
    return el;
}

function renderQuickReplies(replies) {
    clearQuickReplies();
    if (!replies.length) return;
    const row = document.createElement('div');
    row.className = 'quick-replies';
    row.id = 'quickReplies';
    for (const r of replies) {
        const b = document.createElement('button');
        b.className = 'qr-chip';
        b.textContent = r;
        const prefill = { 'Change quantity': 'Make it 2 pieces', 'Change a requirement': 'Actually, ' }[r];
        b.addEventListener('click', () => {
            if (prefill) { chatInput.value = prefill; chatInput.focus(); return; }   // let the customer finish the sentence
            sendMessage(r === 'Show order preview' ? 'I want to buy this' :
                r === 'Talk to sales team' ? 'I want to talk to a salesperson' : r);
        });
        row.appendChild(b);
    }
    chatMessages.appendChild(row);
}
function clearQuickReplies() { $('quickReplies')?.remove(); }

function renderCards(cards) {
    const wrap = document.createElement('div');
    wrap.className = 'product-cards';
    for (const c of cards) {
        const facts = c.facts.map(f => `<li>${esc(f.label)}: <strong>${esc(f.value)}</strong>${f.qualifier ? ` <em>(${esc(f.qualifier)})</em>` : ''} <span class="cite">p.${esc(f.page)}</span></li>`).join('');
        const lim = c.limitations.length ? `<div class="card-lim">⚠️ ${c.limitations.map(esc).join(' · ')}</div>` : '';
        const card = document.createElement('div');
        card.className = 'product-card';
        const photo = c.image ? `<button class="pc-photo" data-src="${esc(c.image.url)}" data-caption="${esc(c.image.caption)}" aria-label="Enlarge photo of ${esc(c.name)}"><img src="${esc(c.image.url)}" alt="${esc(c.name)}" loading="lazy"></button>` : '';
        card.innerHTML = photo + `<div class="pc-head"><span class="pc-family">${esc(c.family)}</span><span class="status-badge s-${esc(c.status)}">${esc(c.status)}</span></div>
            <div class="pc-name">${esc(c.name)}</div><div class="pc-price">${rupee(c.price)} <span class="muted small">catalogue-listed</span></div>
            <ul class="pc-facts">${facts}</ul>${lim}
            <div class="pc-foot"><span class="muted small">Source: catalogue p.${esc(c.image_page)}</span><button class="link-btn" data-pid="${esc(c.product_id)}">Full Truth Receipt →</button></div>`;
        card.querySelector('.link-btn').addEventListener('click', () => viewProductEvidence(c.product_id));
        bindPhotos(card);
        wrap.appendChild(card);
    }
    return wrap;
}

function renderPreviewCard(p) {
    const card = document.createElement('div');
    card.className = 'preview-card';
    const thumb = p.image ? `<img class="pv-thumb" src="${esc(p.image.url)}" alt="${esc(p.product_name)}">` : '';
    card.innerHTML = `<div class="pv-head">🧾 Order preview v${esc(p.version)} <span class="status-badge s-MOCK">MOCK</span></div>
        <div class="pv-row pv-product">${thumb}<span>${esc(p.product_name)}</span><span>${p.items ? "" : "× " + esc(p.quantity)}</span></div>
        <div class="pv-row total"><span>Catalogue-listed total</span><span>${rupee(p.line_total)}</span></div>
        <div class="muted small">${esc(p.label)}</div>
        <button class="btn-confirm" data-preview="${esc(p.preview_id)}">Confirm this preview</button>`;
    card.querySelector('.btn-confirm').addEventListener('click', (ev) => confirmPreview(p.preview_id, ev.currentTarget));
    return card;
}

async function confirmPreview(previewId, btn) {
    if (busy) return;
    btn.disabled = true;
    appendMessage('user', `✔ Confirm preview ${previewId}`);
    try {
        const res = await api(`/sessions/${sessionId}/order-confirmation`, { method: 'POST', body: JSON.stringify({ preview_id: previewId }) });
        appendMessage('agent', res.reply);
        updateMessageCount();
        scrollToBottom();
        if (res.ok && res.order) showOrderModal(res.order);
        await refreshFromServer();
    } catch (err) {
        appendSystem(`Confirmation failed: ${err.message}`);
        btn.disabled = false;
    }
}

function renderImageStrip(images) {
    const wrap = document.createElement('div');
    wrap.className = 'image-strip';
    wrap.innerHTML = images.map(i => `<figure><button class="pc-photo" data-src="${esc(i.url)}" data-caption="${esc(i.caption)}" aria-label="Enlarge ${esc(i.caption)}"><img src="${esc(i.url)}" alt="${esc(i.caption)}" loading="lazy"></button><figcaption>${esc(i.caption)}</figcaption></figure>`).join('');
    bindPhotos(wrap);
    return wrap;
}

function bindPhotos(root) {
    root.querySelectorAll('.pc-photo').forEach(b => b.addEventListener('click', () => openLightbox(b.dataset.src, b.dataset.caption, b)));
}

let lightboxReturnFocus = null;
function openLightbox(src, caption, opener) {
    lightboxReturnFocus = opener;
    $('lightboxImg').src = src;
    $('lightboxImg').alt = caption;
    $('lightboxCaption').textContent = caption + ' · image extracted from the official SGB catalogue PDF';
    $('lightbox').hidden = false;
    $('btnLightboxClose').focus();
}
function closeLightbox() {
    $('lightbox').hidden = true;
    lightboxReturnFocus?.focus();
}
$('btnLightboxClose').addEventListener('click', closeLightbox);
$('lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('lightbox').hidden) closeLightbox(); });

// ── Markdown (escape first, then a safe subset) ──────────────
function renderMarkdown(text) {
    if (!text) return '';
    let s = esc(text).replace(/`([^`]+)`/g, '<code>$1</code>');
    const lines = s.split('\n'), out = [];
    let rows = [];
    const flush = () => { if (rows.length) { out.push(buildTable(rows)); rows = []; } };
    for (const line of lines) {
        const t = line.trim();
        if (t.startsWith('|') && t.endsWith('|')) rows.push(t); else { flush(); out.push(line); }
    }
    flush();
    let html = out.join('\n');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/~~(.+?)~~/g, '<del>$1</del>')
        .replace(/(^|[\s(])_([^_\n]+?)_(?=[\s.,)!?:]|$)/gm, '$1<em>$2</em>')
        .replace(/(?<![*\w])\*([^*\n]+?)\*(?![*\w])/g, '<em>$1</em>')
        .replace(/\\\*/g, '*')
        .replace(/^[•\-]\s+(.*)$/gm, '<li>$1</li>')
        .replace(/((?:<li>.*<\/li>\n?)+)/g, (m) => '<ul>' + m.replace(/\n/g, '') + '</ul>');
    return html.replace(/\n\n+/g, '<br><br>').replace(/(?<!<\/ul>|<\/table>)\n/g, '<br>');
}
function buildTable(rows) {
    const cells = (r) => r.split('|').slice(1, -1).map(c => c.trim());
    const head = cells(rows[0]).map(c => `<th>${c}</th>`).join('');
    const body = rows.slice(1).filter(r => !/^\|[\s\-:|]+\|$/.test(r)).map(r => '<tr>' + cells(r).map(c => `<td>${c}</td>`).join('') + '</tr>').join('');
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

// ── Cockpit rendering ────────────────────────────────────────
function renderCockpit(d) {
    $('leadStatusText').textContent = d.state || 'NEW';
    $('leadStatusChip').dataset.status = d.state || 'NEW';
    $('flagPaused').hidden = !d.flags?.bot_paused;
    $('flagOptOut').hidden = !d.flags?.opted_out;
    $('pausedBanner').hidden = !d.flags?.bot_paused;
    renderFingerprint(d.fingerprint);
    renderCandidates(d.candidates);
    renderReceipt(d.receipt);
    renderNlu(d);
    renderScores(d.question_scores);
    renderHandoff(d.handoff, d.flags);
    if (document.querySelector('.brain-tab.active')?.dataset.tab === 'replay') loadReplay();
}

const STATUS_CLASS = { KNOWN: 'known', UNKNOWN: 'unknown', SUPERSEDED: 'corrected', CONFLICTED: 'conflict' };

function renderFingerprint(fp) {
    if (!fp) return;
    const pct = Math.round((fp.completion_ratio || 0) * 100);
    $('ringFill').setAttribute('stroke-dasharray', `${pct}, 100`);
    $('ringText').textContent = `${pct}%`;
    $('fpRevision').textContent = `rev ${fp.revision}`;
    const changedNow = new Set((lastPayload?.changes || []).map(c => c.field));
    const rows = Object.entries(fp.slots).filter(([, s]) => s.relevant || s.status === 'KNOWN').map(([key, s]) => {
        const val = key === 'budget' && s.value != null ? rupee(s.value) : fmtVal(s.value);
        const sup = s.supersedes ? `<span class="fp-superseded">was ${esc(fmtVal(s.supersedes.value))}</span>` : '';
        const conf = s.status === 'KNOWN' ? `${Math.round(s.confidence * 100)}%` : '';
        return `<div class="fp-field ${STATUS_CLASS[s.status] || ''} ${changedNow.has(key) ? 'flash' : ''}">
            <span class="fp-field-name">${esc(s.label)}<span class="fp-kind">${esc(s.kind.replace(/_/g, ' ').toLowerCase())}</span></span>
            <span class="fp-field-value">${esc(val)} ${sup}</span>
            <span class="fp-field-source" title="${esc(s.source_text || '')}">${esc(s.status)}${conf ? ' · ' + conf : ''}${s.source_message_id ? ' · ' + esc(s.source_message_id) : ''}</span></div>`;
    }).join('');
    $('fingerprintFields').innerHTML = rows || '<div class="fp-empty">No data yet</div>';
    const hist = (fp.history || []).slice().reverse().map(h => `<div class="tl-item tl-${esc(h.type)}">
        <span class="tl-rev">r${esc(h.revision)}</span><span><strong>${esc(h.label)}</strong>: ${h.old_value != null ? `<del>${esc(fmtVal(h.old_value))}</del> → ` : ''}${esc(fmtVal(h.new_value))}
        <span class="muted small">${esc(h.type)}${h.reason ? ' — ' + esc(h.reason) : ''}</span></span></div>`).join('');
    $('historyList').innerHTML = hist || '<div class="fp-empty">No revisions yet</div>';
}

function renderCandidates(c) {
    if (!c) return;
    $('fnPool').textContent = c.pool_size;
    $('fnEligible').textContent = c.eligible_count;
    $('fnUnverified').textContent = c.unverified_count;
    $('fnExcluded').textContent = c.excluded_count;
    $('recMeta').textContent = `${c.recommendation_id} · fingerprint r${c.fingerprint_revision} · ${c.catalogue_version} · ${c.algorithm_version}`;
    $('evaluationList').innerHTML = c.evaluations.map(e => {
        const checks = e.checks.map(ch => `<tr><td>${esc(ch.label)}</td><td>${esc(fmtVal(ch.customer_value))}</td><td>${esc(fmtVal(ch.product_value))}</td>
            <td><span class="res res-${esc(ch.result)}">${esc(ch.result)}</span></td><td class="muted small">${ch.source ? 'p.' + esc(ch.source.page) : '—'}</td></tr>`).join('');
        const cf = e.counterfactual ? `<div class="counterfactual"><span class="hypo">HYPOTHETICAL</span> Would qualify ${esc(e.counterfactual.would_qualify)}.
            ${e.counterfactual.current_alternative ? `Current alternative: <strong>${esc(e.counterfactual.current_alternative.name)}</strong>.` : ''}
            <div class="muted small">Your Need Fingerprint is not changed by this view.</div></div>` : '';
        const pref = e.preference_fit != null ? `<span class="pref">preference fit ${Math.round(e.preference_fit * 100)}%</span>` : '';
        return `<div class="panel-card eval-card eval-${esc(e.status)}">
            <div class="eval-head"><img class="eval-thumb" src="/assets/products/${esc(e.product_id)}.jpg" alt="" onerror="this.remove()"><span class="eval-name">${esc(e.short_name)}</span><span>${rupee(e.price)}</span><span class="status-badge s-${esc(e.status)}">${esc(e.status)}</span>${pref}</div>
            ${checks ? `<div class="table-wrap"><table class="check-table"><thead><tr><th>Requirement</th><th>Customer</th><th>Product</th><th>Result</th><th>Src</th></tr></thead><tbody>${checks}</tbody></table></div>` : '<div class="muted small">No requirements known yet — neutral.</div>'}
            ${(e.preference_details || []).length ? `<div class="muted small">${e.preference_details.map(esc).join(' · ')}</div>` : ''}${cf}
            <button class="link-btn" data-pid="${esc(e.product_id)}">Truth Receipt →</button></div>`;
    }).join('');
    $('evaluationList').querySelectorAll('.link-btn').forEach(b => b.addEventListener('click', () => viewProductEvidence(b.dataset.pid)));
}

function claimRows(claims) {
    return claims.map(cl => {
        const shown = cl.rendered_value ?? (cl.observed_value ? `observed: “${cl.observed_value}”` : '—');
        return `<div class="evidence-item ev-${esc(cl.evidence_status)}">
            <span class="evidence-badge b-${esc(cl.evidence_status)}">${esc(cl.evidence_status)}</span>
            <span class="evidence-field">${esc(cl.subject_name)} · ${esc(cl.label)}</span>
            <span class="evidence-value">${esc(shown)}${cl.qualifier ? ` <em>(${esc(cl.qualifier)})</em>` : ''}</span>
            <span class="evidence-src">${cl.source_field ? esc(cl.source_field) : 'no source field'}${cl.source_page ? ` · p.${esc(cl.source_page)} ${esc(cl.source_location || '')}` : ''}</span>
            ${cl.note ? `<span class="evidence-note">${esc(cl.note)}</span>` : ''}</div>`;
    }).join('');
}

function renderReceipt(r) {
    if (!r) return;
    $('receiptMeta').innerHTML = `<div><span class="muted">Receipt</span> <code>${esc(r.receipt_id)}</code>${r.supersedes ? ` <span class="muted small">supersedes ${esc(r.supersedes.receipt_id)} (${esc(r.supersedes.reason)})</span>` : ''}</div>
        <div><span class="muted">Catalogue</span> <code>${esc(r.catalogue_version)}</code> · ${esc(r.source_file || '')}</div>
        <div><span class="muted">Source hash</span> ${r.source_hash ? `<code>${esc(r.source_hash.slice(0, 16))}…</code>` : '<span class="warn-text">not computed — source PDF not in repo</span>'}</div>`;
    const imgs = (r.images || []).map(i => `<figure class="receipt-img"><button class="pc-photo" data-src="${esc(i.url)}" data-caption="${esc(i.caption)}" aria-label="Enlarge ${esc(i.caption)}"><img src="${esc(i.url)}" alt="${esc(i.caption)}" loading="lazy"></button>
        <figcaption>${esc(i.caption)}<br><span class="mono">image sha256 ${esc(i.sha256.slice(0, 12))}…</span></figcaption></figure>`).join('');
    if (imgs) { $('receiptMeta').insertAdjacentHTML('beforeend', `<div class="receipt-imgs">${imgs}</div>`); bindPhotos($('receiptMeta')); }
    const sup = r.claims.filter(c => c.evidence_status === 'SUPPORTED').length;
    $('receiptClaims').innerHTML = `<div class="muted small">${sup} of ${r.claims.length} claims SUPPORTED — only those may be stated as fact.</div>` + claimRows(r.claims);
}

async function viewProductEvidence(pid) {
    try {
        const ev = await api(`/products/${encodeURIComponent(pid)}/evidence`);
        renderReceipt(ev);
        $('receiptMeta').insertAdjacentHTML('afterbegin', `<div class="receipt-product">Full record: <strong>${esc(ev.product_name)}</strong></div>`);
        switchTab('receipt');
        if (window.innerWidth <= 900) setMobilePane('brain');
    } catch (err) { appendSystem(`Couldn't load evidence: ${err.message}`); }
}

async function loadCatalogueFlags() {
    try {
        const rep = await api('/catalogue/report');
        $('catalogueFlags').innerHTML = `<div class="muted small">${esc(rep.catalogue_version)} · ${rep.product_count} products</div>` +
            rep.flags.map(f => `<div class="evidence-item ev-CONFLICTED"><span class="evidence-badge b-${f.status.includes('CONFLICT') ? 'CONFLICTED' : 'UNAPPROVED'}">${esc(f.status.replace(/_/g, ' '))}</span>
                <span class="evidence-field">${esc(f.product_id)} · ${esc(f.field)}</span><span class="evidence-value">observed “${esc(f.observed_value)}”</span>
                <span class="evidence-src">p.${esc(f.source.page)} ${esc(f.source.location)}</span></div>`).join('') +
            rep.warnings.map(w => `<div class="warn-line">⚠️ ${esc(w)}</div>`).join('');
    } catch (_) { $('catalogueFlags').innerHTML = '<div class="fp-empty">Report unavailable</div>'; }
}

function renderNlu(d) {
    $('debugNormalized').textContent = d.nlu?.normalized || '—';
    $('debugIntent').textContent = d.intent || '—';
    $('debugConfidence').textContent = d.confidence != null ? `${Math.round(d.confidence * 100)}%` : '—';
    $('debugRule').textContent = d.nlu?.rule || 'model decision';
    $('debugTop').textContent = (d.nlu?.model_top || []).map(t => `${t.intent} ${Math.round(t.p * 100)}%`).join(' · ') || '—';
    $('debugModel').textContent = d.nlu?.model_version || '—';
    $('debugEntities').textContent = JSON.stringify(d.entities || {}, null, 2);
}

function renderScores(scores) {
    if (!scores) return;
    if (!scores.length) { $('questionScores').innerHTML = '<div class="fp-empty">Nothing left to ask that would change the decision</div>'; return; }
    const max = Math.max(...scores.map(s => s.value), 0.01);
    const asked = lastPayload?.next_action === 'ASK_CLARIFYING_QUESTION';
    $('questionScores').innerHTML = scores.map((s, i) => {
        const parts = Object.entries(s.partitions).map(([a, ids]) => `${esc(fmtVal(a))}: ${ids.map(esc).join(', ')}`).join(' | ');
        return `<div class="qs-item ${i === 0 && asked ? 'chosen' : ''}">
            <div class="qs-top"><span class="qs-slot">${esc(s.slot.replace(/_/g, ' '))}${s.blocking ? ' <span class="blocking">blocking</span>' : ''}${i === 0 && asked ? ' <span class="asked">asked</span>' : ''}</span><span class="qs-value">${s.value.toFixed(2)}</span></div>
            <div class="qs-bar-track"><div class="qs-bar-fill" style="width:${Math.max(0, s.value / max * 100)}%"></div></div>
            <div class="qs-meta">IG ${s.information_gain} bits × crit ${s.criticality} × cover ${s.coverage} − effort ${s.effort_cost} − privacy ${s.privacy_cost} · ${s.candidate_count} candidates</div>
            ${parts ? `<div class="qs-meta">answers → ${parts}</div>` : ''}</div>`;
    }).join('');
}

// ── Handoff ──────────────────────────────────────────────────
function renderHandoff(h, flags) {
    const paused = !!flags?.bot_paused;
    $('handoffDot').hidden = !h || h.status === 'RETURNED_TO_BOT';
    $('btnTakeOver').disabled = paused;
    $('btnResume').disabled = !paused;
    $('agentCompose').hidden = !paused;
    if (!h) { $('handoffStatus').innerHTML = '<div class="fp-empty">No handoff requested. The agent can still take over at any time.</div>'; $('handoffPacket').innerHTML = '<div class="fp-empty">The packet appears when a handoff is queued</div>'; return; }
    $('handoffStatus').innerHTML = `<div class="debug-row"><span class="debug-label">Status</span><span class="status-badge s-${esc(h.status)}">${esc(h.status)}</span></div>
        <div class="debug-row"><span class="debug-label">Reason</span><span>${esc((h.reasons || [h.reason]).join(', '))}</span></div>
        <div class="debug-row"><span class="debug-label">Queue</span><span>${esc(h.queue)}</span></div>
        <div class="debug-row"><span class="debug-label">Agent</span><span>${esc(h.assigned_agent || '—')}</span></div>`;
    const p = h.context_snapshot || {};
    const need = Object.entries(p.need_fingerprint || {}).map(([k, s]) => `<li>${esc(s.label)}: <strong>${esc(fmtVal(s.value))}</strong>${s.supersedes ? ` <span class="muted">(was ${esc(fmtVal(s.supersedes.value))})</span>` : ''}</li>`).join('');
    const prods = (p.products_considered || []).filter(x => x.status !== 'EXCLUDED' || x.reasons.length).slice(0, 6).map(x => `<li>${esc(x.name)} — <span class="status-badge s-${esc(x.status)}">${esc(x.status)}</span> ${x.reasons.map(esc).join('; ')}</li>`).join('');
    const facts = (p.unresolved_facts || []).map(f => `<li>${esc(f.product)} · ${esc(f.field)}: ${esc(f.status)}</li>`).join('');
    $('handoffPacket').innerHTML = `<div class="packet">
        <div><span class="muted">Lead state</span> ${esc(p.lead_state)} · <span class="muted">catalogue</span> <code>${esc(p.catalogue_version || '—')}</code></div>
        <div class="muted small">${esc(p.summary)}</div>
        <h4>Need Fingerprint</h4><ul>${need || '<li class="muted">nothing captured</li>'}</ul>
        <h4>Products considered</h4><ul>${prods || '<li class="muted">none yet</li>'}</ul>
        <h4>Unresolved facts</h4><ul>${facts || '<li class="muted">none</li>'}</ul>
        <h4>Open questions</h4><div>${(p.open_questions || []).map(q => `<span class="chip">${esc(q.replace(/_/g, ' '))}</span>`).join(' ') || '<span class="muted">none</span>'}</div>
        <h4>DealGuard</h4><div>${p.dealguard ? `${esc(p.dealguard.outcome)} — ${esc(p.dealguard.reason)}` : '<span class="muted">no pricing decision</span>'}</div>
        <h4>Transcript</h4><div class="muted small">${(p.transcript || []).length} messages attached</div></div>`;
}

async function refreshFromServer() {
    const s = await api(`/sessions/${sessionId}/summary`);
    lastPayload = { ...(lastPayload || {}), state: s.lead_status, flags: s.flags, handoff: s.handoff, fingerprint: s.fingerprint, order: s.order };
    $('leadStatusText').textContent = s.lead_status;
    $('leadStatusChip').dataset.status = s.lead_status;
    $('flagPaused').hidden = !s.flags.bot_paused;
    $('flagOptOut').hidden = !s.flags.opted_out;
    $('pausedBanner').hidden = !s.flags.bot_paused;
    renderHandoff(s.handoff, s.flags);
}

$('btnTakeOver').addEventListener('click', async () => {
    if (!sessionId) return;
    await api(`/sessions/${sessionId}/handoff`, { method: 'POST', body: JSON.stringify({ agent: 'demo-agent' }) });
    appendSystem('Salesperson “demo-agent” took over. The bot is paused.');
    await refreshFromServer();
});
$('btnResume').addEventListener('click', async () => {
    await api(`/sessions/${sessionId}/resume-bot`, { method: 'POST', body: JSON.stringify({ agent: 'demo-agent' }) });
    appendSystem('The salesperson returned the conversation to the bot.');
    await refreshFromServer();
});
async function sendAgent() {
    const text = $('agentInput').value.trim();
    if (!text) return;
    try {
        await api(`/sessions/${sessionId}/agent-message`, { method: 'POST', body: JSON.stringify({ agent: 'demo-agent', text }) });
        appendMessage('human_agent', text);
        $('agentInput').value = '';
        if (observing) loadOutbox(observing);
        updateMessageCount();
        scrollToBottom();
    } catch (err) { appendSystem(`Agent message not sent: ${err.message}`); }
}
$('btnAgentSend').addEventListener('click', sendAgent);
$('agentInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendAgent(); });

// ── Replay ───────────────────────────────────────────────────
async function loadReplay() {
    if (!sessionId) return;
    try {
        const { traces } = await api(`/sessions/${sessionId}/replay`);
        if (!traces.length) { $('replayList').innerHTML = '<div class="fp-empty">No turns yet</div>'; return; }
        $('replayList').innerHTML = traces.slice().reverse().map(t => {
            const cands = (t.candidates || []).filter(c => c.status !== 'EXCLUDED' || Object.values(c.checks).includes('FAIL')).map(c => `<span class="chip s-${esc(c.status)}">${esc(c.product_id)} ${esc(c.status)}</span>`).join(' ');
            const claims = (t.claims || []).map(c => `${esc(c.product)}.${esc(c.field)}=${esc(c.status)}`).join(', ');
            return `<details class="replay-item"><summary><span class="tl-rev">T${esc(t.turn)}</span> “${esc(t.customer_text)}” <span class="muted small">→ ${esc(t.next_action || '—')}</span></summary>
                <div class="replay-grid">
                <span>Intent</span><span>${esc(t.nlu.intent)} (${Math.round(t.nlu.confidence * 100)}%)${t.nlu.rule ? ` · rule: ${esc(t.nlu.rule)}` : ''}</span>
                <span>Model</span><span class="mono small">${esc(t.nlu.model_version)}</span>
                <span>Profile</span><span>r${esc(t.fingerprint_revision_before)} → r${esc(t.fingerprint_revision_after)}${t.changes.length ? ': ' + t.changes.map(c => `${esc(c.field)} ${esc(c.type)}`).join(', ') : ''}</span>
                <span>Catalogue</span><span class="mono small">${esc(t.catalogue_version)}</span>
                <span>Candidates</span><span>${cands || '—'}</span>
                <span>Question</span><span>${esc(t.question_asked || '—')}</span>
                <span>Evidence</span><span>${t.receipt_id ? `<code>${esc(t.receipt_id)}</code> ` : ''}<span class="small">${claims || '—'}</span></span>
                <span>DealGuard</span><span>${esc(t.dealguard || '—')}</span>
                <span>State</span><span>${esc(t.state_before)} → ${esc(t.state_after)}</span>
                <span>Side effects</span><span>${(t.side_effects || []).map(esc).join(', ') || 'none'}</span>
                <span>Time</span><span>${esc(t.processing_ms)} ms${t.bot_paused ? ' · bot paused' : ''}</span></div></details>`;
        }).join('');
    } catch (_) { $('replayList').innerHTML = '<div class="fp-empty">Replay unavailable</div>'; }
}

// ── Order modal ──────────────────────────────────────────────
function showOrderModal(o) {
    $('orderDetails').innerHTML = `<p><strong>Order ID:</strong> <code>${esc(o.order_id)}</code></p>
        <p><strong>Product:</strong> ${esc(o.product_name)}${(o.details && o.details.items) ? "" : " × " + esc(o.quantity)}</p>
        <p><strong>Catalogue-listed total:</strong> ${rupee(o.quoted_price)}</p>
        <p><strong>Type:</strong> ${esc(o.order_type)} · <strong>Payment:</strong> ${esc(o.payment_status)}</p>
        <p class="muted small">Preview ${esc(o.details?.preview_id || '')} · confirmation ${esc(o.confirmation_message_id)} · ${esc(o.catalogue_version)}</p>
        <p class="modal-note">Simulation only — no payment, delivery or real order.</p>`;
    $('orderModal').style.display = 'flex';
    $('btnCloseModal').focus();
}
$('btnCloseModal').addEventListener('click', () => { $('orderModal').style.display = 'none'; chatInput.focus(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('orderModal').style.display === 'flex') $('btnCloseModal').click(); });

// ── Tabs / panes ─────────────────────────────────────────────
function switchTab(name) {
    document.querySelectorAll('.brain-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.brain-content').forEach(c => c.classList.toggle('active', c.dataset.tab === name));
    if (name === 'replay') loadReplay();
    if (name === 'whatsapp') loadWhatsApp();
}
document.querySelectorAll('.brain-tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));

function setMobilePane(name) {
    $('dualPane').dataset.activePane = name;
    $('btnToggleChat').classList.toggle('active', name === 'chat');
    $('btnToggleBrain').classList.toggle('active', name === 'brain');
}
$('btnToggleChat').addEventListener('click', () => setMobilePane('chat'));
$('btnToggleBrain').addEventListener('click', () => setMobilePane('brain'));
setMobilePane('chat');

function resetCockpit() {
    $('ringFill').setAttribute('stroke-dasharray', '0, 100');
    $('ringText').textContent = '0%';
    $('fpRevision').textContent = 'rev 0';
    $('fingerprintFields').innerHTML = '<div class="fp-empty">No data yet — start a conversation</div>';
    $('historyList').innerHTML = '<div class="fp-empty">No revisions yet</div>';
    ['fnPool', 'fnEligible', 'fnUnverified', 'fnExcluded'].forEach(id => $(id).textContent = '0');
    $('recMeta').textContent = '';
    $('evaluationList').innerHTML = '<div class="panel-card"><div class="fp-empty">No candidates evaluated yet</div></div>';
    $('receiptMeta').innerHTML = '';
    $('receiptClaims').innerHTML = '<div class="fp-empty">No product claims yet</div>';
    $('questionScores').innerHTML = '<div class="fp-empty">No questions scored yet</div>';
    $('replayList').innerHTML = '<div class="fp-empty">No turns yet</div>';
    ['debugNormalized', 'debugIntent', 'debugConfidence', 'debugRule', 'debugTop', 'debugModel'].forEach(id => $(id).textContent = '—');
    $('debugEntities').textContent = '{}';
    $('leadStatusText').textContent = 'NEW';
    $('leadStatusChip').dataset.status = 'NEW';
    $('flagPaused').hidden = true;
    $('flagOptOut').hidden = true;
    $('pausedBanner').hidden = true;
    renderHandoff(null, {});
    switchTab('need');
}

// ── Inputs ───────────────────────────────────────────────────
$('btnNewSession').addEventListener('click', createSession);
btnSend.addEventListener('click', () => sendMessage(chatInput.value));
chatInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(chatInput.value); } });
chatInput.addEventListener('input', () => { chatInput.style.height = 'auto'; chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px'; });
document.querySelectorAll('.starter-chip').forEach(c => c.addEventListener('click', () => sendMessage(c.dataset.text)));
document.querySelectorAll('.stress-btn').forEach(b => b.addEventListener('click', async () => {
    if (window.innerWidth <= 900) setMobilePane('chat');
    if (b.id === 'btnDuplicateDelivery') {
        const key = 'dup-' + Math.random().toString(36).slice(2, 10);
        await sendMessage(b.dataset.text, key);
        await sendMessage(b.dataset.text, key, { echo: false });
        return;
    }
    sendMessage(b.dataset.text);
}));

// ── WhatsApp channel ─────────────────────────────────────────
async function loadWhatsApp() {
    try {
        const st = await api('/whatsapp/status');
        const hook = `${location.origin}${st.webhook_path}`;
        $('waStatus').innerHTML = `
            <div class="debug-row"><span class="debug-label">Mode</span><span class="status-badge ${st.send_mode === 'live' ? 's-ELIGIBLE' : 's-MOCK'}">${st.send_mode === 'live' ? 'LIVE — sends real messages' : 'DRY RUN — records, never sends'}</span></div>
            <div class="debug-row"><span class="debug-label">Webhook</span><code class="small">${esc(hook)}</code></div>
            <div class="debug-row"><span class="debug-label">Graph API</span><span>${esc(st.api_version)}</span></div>
            <div class="debug-row"><span class="debug-label">Credentials</span><span class="small">verify token ${st.verify_token_set ? '✓' : '✗'} · app secret ${st.app_secret_set ? '✓' : '✗'} · access token ${st.access_token_set ? '✓' : '✗'} · phone number id ${st.phone_number_id_set ? '✓' : '✗'}</span></div>
            ${st.problems.map(p => `<div class="warn-line">⚠️ ${esc(p)}</div>`).join('')}
            <p class="muted small">Setup steps: README → “Run it on WhatsApp”. Try it without Meta: <code>python scripts/whatsapp_simulator.py</code></p>`;
        const { conversations } = await api('/whatsapp/conversations');
        $('waConversations').innerHTML = conversations.length ? conversations.map(c => `
            <button class="wa-conv ${observing === c.session_id ? 'active' : ''}" data-sid="${esc(c.session_id)}">
                <span class="wa-avatar">👤</span>
                <span class="wa-conv-main"><strong>${esc(c.profile_name || 'Customer')}</strong> <span class="mono small">${esc(c.customer)}</span><br>
                <span class="muted small">${esc(c.lead_status)} · last message ${c.last_inbound_at ? esc(new Date(c.last_inbound_at).toLocaleString()) : '—'}</span></span>
            </button>`).join('') : '<div class="fp-empty">No WhatsApp conversations yet</div>';
        $('waConversations').querySelectorAll('.wa-conv').forEach(b => b.addEventListener('click', () => observe(b.dataset.sid)));
        if (observing) await loadOutbox(observing);
    } catch (err) { $('waStatus').innerHTML = `<div class="fp-empty">WhatsApp status unavailable (${esc(err.message)})</div>`; }
}

function waBubble(m) {
    const p = m.payload, it = p.interactive;
    let body = '';
    if (p.type === 'text') body = waMarkup(p.text.body);
    else if (p.type === 'image') body = `<img class="wa-img" src="${esc(p.image.link || '')}" alt="">${p.image.caption ? `<div>${waMarkup(p.image.caption)}</div>` : ''}`;
    else if (it) {
        body = waMarkup(it.body.text);
        if (it.type === 'button') body += `<div class="wa-buttons">${it.action.buttons.map(b => `<span>${esc(b.reply.title)}</span>`).join('')}</div>`;
        else body += `<div class="wa-buttons"><span>☰ ${esc(it.action.button)}</span></div><div class="wa-rows">${it.action.sections[0].rows.map(r => `<div>${esc(r.title)}${r.description ? `<br><span class="muted">${esc(r.description)}</span>` : ''}</div>`).join('')}</div>`;
    }
    const who = m.kind.startsWith('agent') ? '<div class="msg-who">SGB salesperson</div>' : '';
    const status = `${esc(m.status)}${m.delivery_status ? ' · ' + esc(m.delivery_status) : ''}${m.error ? ' — ' + esc(m.error) : ''}`;
    return `<div class="wa-bubble ${m.status.startsWith('SUPPRESSED') || m.status === 'FAILED' ? 'blocked' : ''}">${who}${body}<div class="wa-meta">${status}</div></div>`;
}

function waMarkup(t) {   // WhatsApp markup → HTML (escape first)
    return esc(t).replace(/\*([^*\n]+)\*/g, '<strong>$1</strong>').replace(/(^|\s)_([^_\n]+)_/g, '$1<em>$2</em>')
        .replace(/~([^~\n]+)~/g, '<del>$1</del>').replace(/\n/g, '<br>');
}

async function loadOutbox(sid) {
    try {
        const { messages } = await api(`/whatsapp/conversations/${encodeURIComponent(sid)}/outbox`);
        $('waOutbox').innerHTML = messages.length ? messages.map(waBubble).join('') : '<div class="fp-empty">Nothing sent yet</div>';
        $('waOutbox').scrollTop = $('waOutbox').scrollHeight;
    } catch (_) { $('waOutbox').innerHTML = '<div class="fp-empty">Outbox unavailable</div>'; }
}

async function observe(sid) {
    observing = sessionId = sid;
    $('sessionIdDisplay').textContent = sid;
    $('observeBanner').hidden = false;
    setInputEnabled(false);
    chatInput.placeholder = 'Watching WhatsApp — take over from the Handoff tab to reply';
    lastPayload = null;
    await refreshObserved(true);
    clearInterval(observeTimer);
    observeTimer = setInterval(() => refreshObserved(false), 4000);
    loadWhatsApp();
}

async function refreshObserved(force) {
    if (!observing) return;
    try {
        const [{ messages }, summary, ev] = await Promise.all([
            api(`/sessions/${observing}/messages`), api(`/sessions/${observing}/summary`), api(`/sessions/${observing}/evidence`)]);
        if (force || messages.length !== chatMessages.querySelectorAll('.message').length) {
            chatMessages.innerHTML = '';
            messages.forEach(m => appendMessage(m.role, m.content));
            updateMessageCount();
            scrollToBottom(true);
        }
        renderFingerprint(summary.fingerprint);
        renderHandoff(summary.handoff, summary.flags);
        if (ev.latest) renderReceipt(ev.latest);
        $('leadStatusText').textContent = summary.lead_status;
        $('leadStatusChip').dataset.status = summary.lead_status;
        $('flagPaused').hidden = !summary.flags.bot_paused;
        $('flagOptOut').hidden = !summary.flags.opted_out;
        if (document.querySelector('.brain-tab.active')?.dataset.tab === 'whatsapp') loadOutbox(observing);
        if (document.querySelector('.brain-tab.active')?.dataset.tab === 'replay') loadReplay();
    } catch (_) { /* transient; next poll retries */ }
}

function stopObserving(reset = true) {
    if (!observing && !reset) return;
    observing = null;
    clearInterval(observeTimer);
    $('observeBanner').hidden = true;
    chatInput.placeholder = 'Type as the customer…';
    if (reset) createSession();
}
$('btnStopObserve').addEventListener('click', () => stopObserving(true));

createSession();
loadCatalogueFlags();
