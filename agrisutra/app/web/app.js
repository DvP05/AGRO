/* ═══════════════════════════════════════════════════════════
   AgriSutra — Frontend Application Logic
   ═══════════════════════════════════════════════════════════ */

const API = '';  // Same origin
let sessionId = null;
let messageCount = 0;

// ── DOM Elements ─────────────────────────────────────────────
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const btnSend = document.getElementById('btnSend');
const btnNewSession = document.getElementById('btnNewSession');
const welcomeScreen = document.getElementById('welcomeScreen');
const sessionIdDisplay = document.getElementById('sessionIdDisplay');
const leadStatusChip = document.getElementById('leadStatusChip');
const leadStatusText = document.getElementById('leadStatusText');
const msgCountEl = document.getElementById('msgCount');

// Brain panel elements
const ringFill = document.getElementById('ringFill');
const ringText = document.getElementById('ringText');
const fingerprintFields = document.getElementById('fingerprintFields');
const contradictionsCard = document.getElementById('contradictionsCard');
const contradictionsList = document.getElementById('contradictionsList');
const funnelAll = document.getElementById('funnelAll');
const funnelFiltered = document.getElementById('funnelFiltered');
const funnelRanked = document.getElementById('funnelRanked');
const topCandidatesList = document.getElementById('topCandidatesList');
const eliminatedCard = document.getElementById('eliminatedCard');
const eliminatedList = document.getElementById('eliminatedList');
const evidenceContent = document.getElementById('evidenceContent');
const debugIntent = document.getElementById('debugIntent');
const debugConfidence = document.getElementById('debugConfidence');
const debugEntities = document.getElementById('debugEntities');
const questionScores = document.getElementById('questionScores');
const orderModal = document.getElementById('orderModal');
const orderDetails = document.getElementById('orderDetails');
const btnCloseModal = document.getElementById('btnCloseModal');

// ── Session Management ───────────────────────────────────────
async function createSession() {
    try {
        const res = await fetch(`${API}/api/session`, { method: 'POST' });
        const data = await res.json();
        sessionId = data.session_id;
        sessionIdDisplay.textContent = sessionId;
        chatInput.disabled = false;
        btnSend.disabled = false;
        messageCount = 0;
        msgCountEl.textContent = '0 messages';
        chatMessages.innerHTML = '';
        chatMessages.appendChild(welcomeScreen);
        welcomeScreen.style.display = 'flex';
        updateLeadStatus('NEW');
        resetBrainPanels();
    } catch (err) {
        console.error('Failed to create session:', err);
    }
}

// ── Send Message ─────────────────────────────────────────────
async function sendMessage(text) {
    if (!text.trim() || !sessionId) return;

    // Hide welcome, show user message
    welcomeScreen.style.display = 'none';
    appendMessage('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';
    messageCount++;
    msgCountEl.textContent = `${messageCount * 2} messages`;

    // Show typing indicator
    const typingEl = showTyping();

    try {
        const res = await fetch(`${API}/api/session/${sessionId}/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();

        // Remove typing
        typingEl.remove();

        // Show agent reply
        appendMessage('agent', data.reply);

        // Update all brain panels
        updateLeadStatus(data.lead_status);
        updateFingerprint(data.fingerprint);
        updateCandidates(data.candidates);
        updateEvidence(data.evidence);
        updateDebug(data);
        updateQuestionScores(data.question_scores);

        // Check for order
        if (data.order && data.order.success) {
            showOrderModal(data.order);
        }

    } catch (err) {
        typingEl.remove();
        appendMessage('agent', '⚠️ Connection error. Please try again.');
        console.error('Send error:', err);
    }
}

// ── Message Rendering ────────────────────────────────────────
function appendMessage(role, text) {
    const el = document.createElement('div');
    el.className = `message ${role}`;
    const avatar = role === 'user' ? '👤' : '🌱';
    el.innerHTML = `
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-bubble">${renderMarkdown(text)}</div>
    `;
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showTyping() {
    const el = document.createElement('div');
    el.className = 'message agent';
    el.innerHTML = `
        <div class="msg-avatar">🌱</div>
        <div class="msg-bubble typing-indicator">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;
    chatMessages.appendChild(el);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return el;
}

// ── Simple Markdown Renderer ─────────────────────────────────
function renderMarkdown(text) {
    if (!text) return '';
    let html = text
        // Code blocks
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Bold
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Strikethrough
        .replace(/~~(.+?)~~/g, '<del>$1</del>')
        // Headers
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        // Table rows (basic)
        .replace(/\|(.+)\|/g, (match) => {
            const cells = match.split('|').filter(c => c.trim());
            if (cells.every(c => /^[\s\-]+$/.test(c))) return ''; // separator row
            const tag = cells.some(c => /\*\*/.test(c)) ? 'th' : 'td';
            return '<tr>' + cells.map(c => `<${tag}>${c.trim()}</${tag}>`).join('') + '</tr>';
        })
        // Line breaks
        .replace(/\n\n/g, '<br><br>')
        .replace(/\n/g, '<br>');

    // Wrap table rows in table
    if (html.includes('<tr>')) {
        html = html.replace(/(<tr>.*?<\/tr>(?:<br>)?)+/gs, (match) => {
            return '<table>' + match.replace(/<br>/g, '') + '</table>';
        });
    }

    return html;
}

// ── Lead Status ──────────────────────────────────────────────
function updateLeadStatus(status) {
    leadStatusText.textContent = status;
    leadStatusChip.setAttribute('data-status', status);
}

// ── Fingerprint Panel ────────────────────────────────────────
function updateFingerprint(fp) {
    if (!fp) return;

    // Completion ring
    const pct = Math.round((fp.completion_ratio || 0) * 100);
    ringFill.setAttribute('stroke-dasharray', `${pct}, 100`);
    ringText.textContent = `${pct}%`;

    // Fields
    const reqs = fp.requirements || {};
    let html = '';
    for (const [key, field] of Object.entries(reqs)) {
        const status = field.status === 'known' && field.value != null ? 'known' : 'unknown';
        const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const value = field.value != null ? field.value : '—';
        const source = field.source || '';
        html += `
            <div class="fp-field ${status}" id="fp-${key}">
                <span class="fp-field-name">${label}</span>
                <span class="fp-field-value">${value}</span>
                <span class="fp-field-source">${source}</span>
            </div>
        `;
    }
    fingerprintFields.innerHTML = html || '<div class="fp-empty">No data yet</div>';

    // Contradictions
    if (fp.contradictions && fp.contradictions.length > 0) {
        contradictionsCard.style.display = 'block';
        contradictionsList.innerHTML = fp.contradictions.map(c => `
            <div class="contradiction-item">
                <strong>${c.field.replace(/_/g, ' ')}:</strong>
                <del>${c.old_value}</del> → <strong>${c.new_value}</strong>
                <div style="font-size:0.65rem;color:var(--text-muted)">
                    ${c.old_source} → ${c.new_source}
                </div>
            </div>
        `).join('');
    } else {
        contradictionsCard.style.display = 'none';
    }
}

// ── Candidates Panel ─────────────────────────────────────────
function updateCandidates(candidates) {
    if (!candidates) return;

    // Funnel
    funnelAll.querySelector('.funnel-count').textContent = candidates.total || 0;
    funnelFiltered.querySelector('.funnel-count').textContent = candidates.after_filter || 0;
    funnelRanked.querySelector('.funnel-count').textContent = candidates.ranked_count || 0;

    // Add animation
    funnelAll.style.animation = 'none';
    funnelAll.offsetHeight; // trigger reflow
    funnelAll.style.animation = 'fpUpdate 0.5s ease';

    // Top candidates
    const tops = candidates.top_products || [];
    if (tops.length > 0) {
        topCandidatesList.innerHTML = tops.map((p, i) => {
            const score = Math.round((p.fit_score || 0) * 100);
            const scoreClass = score >= 70 ? 'score-high' : score >= 40 ? 'score-mid' : 'score-low';
            const priceStr = p.price ? `₹${p.price}` : 'N/A';
            return `
                <div class="candidate-item" onclick="viewEvidence('${p.product_id}')">
                    <div class="candidate-rank">#${i + 1}</div>
                    <div class="candidate-info">
                        <div class="candidate-name">${p.name}</div>
                        <div class="candidate-meta">${p.category || ''} • ${priceStr}</div>
                    </div>
                    <div class="candidate-score ${scoreClass}">${score}%</div>
                </div>
            `;
        }).join('');
    } else {
        topCandidatesList.innerHTML = '<div class="fp-empty">No matches yet</div>';
    }

    // Eliminated
    const elims = candidates.eliminated_products || [];
    if (elims.length > 0) {
        eliminatedCard.style.display = 'block';
        eliminatedList.innerHTML = elims.map(p => `
            <div class="eliminated-item">
                <div class="elim-name">🚫 ${p.name}</div>
                ${p.reasons.map(r => `<div class="elim-reason">${r}</div>`).join('')}
            </div>
        `).join('');
    } else {
        eliminatedCard.style.display = 'none';
    }
}

// ── Evidence Panel ───────────────────────────────────────────
function updateEvidence(evidence) {
    if (!evidence || !evidence.facts) {
        return;
    }

    let html = `<div style="font-size:0.75rem;color:var(--text-secondary);margin-bottom:8px;">
        📦 ${evidence.product_name || evidence.product_id}
        <span style="font-family:var(--font-mono);font-size:0.6rem;margin-left:8px;">v:${(evidence.catalogue_version || '').substring(0, 30)}</span>
    </div>`;

    for (const fact of evidence.facts) {
        const status = fact.status || 'UNKNOWN';
        let badgeClass = 'badge-verified', itemClass = '';
        if (status === 'MISSING') { badgeClass = 'badge-missing'; itemClass = 'missing'; }
        if (status === 'CONFLICT') { badgeClass = 'badge-conflict'; itemClass = 'conflict'; }

        html += `
            <div class="evidence-item ${itemClass}">
                <span class="evidence-badge ${badgeClass}">${status}</span>
                <span class="evidence-field">${fact.field}</span>
                <span class="evidence-value">${fact.value ?? 'null'}</span>
            </div>
        `;
    }
    evidenceContent.innerHTML = html;
}

async function viewEvidence(productId) {
    try {
        const res = await fetch(`${API}/api/catalogue/${productId}/evidence`);
        const data = await res.json();
        updateEvidence(data);
        // Switch to evidence tab
        switchTab('evidence');
    } catch (err) {
        console.error('Evidence fetch error:', err);
    }
}

// ── Debug Panel ──────────────────────────────────────────────
function updateDebug(data) {
    debugIntent.textContent = data.intent || '—';
    debugIntent.style.color = data.confidence > 0.5 ? 'var(--clr-primary)' : 'var(--clr-warning)';
    debugConfidence.textContent = data.confidence != null ? `${Math.round(data.confidence * 100)}%` : '—';
    debugEntities.textContent = JSON.stringify(data.entities || {}, null, 2);
}

function updateQuestionScores(scores) {
    if (!scores || scores.length === 0) {
        questionScores.innerHTML = '<div class="fp-empty">No pending questions</div>';
        return;
    }
    questionScores.innerHTML = scores.map(q => {
        const pct = Math.round(q.value_score * 100);
        return `
            <div class="qs-item">
                <span style="flex:1;font-size:0.75rem">${q.field.replace(/_/g, ' ')}</span>
                <div class="qs-bar-track" style="flex:1">
                    <div class="qs-bar-fill" style="width:${pct}%"></div>
                </div>
                <span class="qs-value">${pct}%</span>
            </div>
        `;
    }).join('');
}

// ── Order Modal ──────────────────────────────────────────────
function showOrderModal(order) {
    orderDetails.innerHTML = `
        <p><strong>Order ID:</strong> <code>${order.order_id}</code></p>
        <p><strong>Product:</strong> ${order.product_name}</p>
        <p><strong>Quantity:</strong> ${order.quantity}</p>
        <p><strong>Total:</strong> ${order.quoted_price ? '₹' + order.quoted_price : 'N/A'}</p>
        <p><strong>Status:</strong> ${order.payment_status}</p>
        <p style="color:var(--text-muted);font-size:0.8rem;margin-top:12px;">
            ⚠️ This is a simulated order. No payment processed.
        </p>
    `;
    orderModal.style.display = 'flex';
}

// ── Tab Switching ────────────────────────────────────────────
function switchTab(tabName) {
    document.querySelectorAll('.brain-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.brain-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.brain-tab[data-tab="${tabName}"]`).classList.add('active');
    document.querySelector(`.brain-content[data-tab="${tabName}"]`).classList.add('active');
}

// ── Reset Brain Panels ───────────────────────────────────────
function resetBrainPanels() {
    ringFill.setAttribute('stroke-dasharray', '0, 100');
    ringText.textContent = '0%';
    fingerprintFields.innerHTML = '<div class="fp-empty">No data yet — start a conversation</div>';
    contradictionsCard.style.display = 'none';
    funnelAll.querySelector('.funnel-count').textContent = '0';
    funnelFiltered.querySelector('.funnel-count').textContent = '0';
    funnelRanked.querySelector('.funnel-count').textContent = '0';
    topCandidatesList.innerHTML = '<div class="fp-empty">Run a recommendation first</div>';
    eliminatedCard.style.display = 'none';
    evidenceContent.innerHTML = '<div class="fp-empty">Select a product to view evidence</div>';
    debugIntent.textContent = '—';
    debugConfidence.textContent = '—';
    debugEntities.textContent = '{}';
    questionScores.innerHTML = '<div class="fp-empty">No scores yet</div>';
    switchTab('fingerprint');
}

// ── Event Listeners ──────────────────────────────────────────
btnNewSession.addEventListener('click', createSession);

btnSend.addEventListener('click', () => sendMessage(chatInput.value));

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(chatInput.value);
    }
});

// Auto-resize textarea
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
});

// Quick starters
document.querySelectorAll('.starter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
        const text = chip.getAttribute('data-text');
        sendMessage(text);
    });
});

// Stress test buttons
document.querySelectorAll('.stress-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const text = btn.getAttribute('data-text');
        sendMessage(text);
    });
});

// Brain tabs
document.querySelectorAll('.brain-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.getAttribute('data-tab')));
});

// Modal close
btnCloseModal.addEventListener('click', () => { orderModal.style.display = 'none'; });

// ── Auto-create session on load ──────────────────────────────
createSession();
