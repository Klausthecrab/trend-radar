// ─── Trend Radar Browser Extension — Popup ─────────────
// Issue #29: Grundgerüst (Tabs anzeigen, Button deaktiviert) ✅
// Issue #30: Send-Logik (Tabs schließen + POST) ✅ aktiv

const API_BASE = 'http://localhost:9126';

// ─── Tabs laden und rendern ─────────────────────
let currentTabs = [];

function renderTabs(tabs) {
  const list = document.getElementById('tabList');
  const counter = document.getElementById('tabCountLabel');
  const sendBtn = document.getElementById('sendBtn');

  currentTabs = tabs;
  counter.textContent = tabs.length + ' Tab' + (tabs.length !== 1 ? 's' : '');

  if (tabs.length === 0) {
    list.innerHTML = '<div class="tab-list-empty">Keine offenen Tabs in diesem Fenster</div>';
    sendBtn.disabled = true;
    return;
  }

  // Button aktivieren
  sendBtn.disabled = false;

  list.innerHTML = tabs.map((tab, i) => {
    const urlDisplay = tab.url.length > 65
      ? tab.url.slice(0, 62) + '…'
      : tab.url;

    return `
      <div class="tab-row" data-tab-id="${tab.id}" data-tab-url="${escapeHtml(tab.url)}">
        <div class="tab-row-main">
          <span class="tab-row-index">#${i + 1}</span>
          <div class="tab-row-info">
            <div class="tab-row-title" title="${escapeHtml(tab.title || '')}">
              ${escapeHtml(tab.title || 'Kein Titel')}
            </div>
            <div class="tab-row-url" title="${escapeHtml(tab.url)}">
              ${escapeHtml(urlDisplay)}
            </div>
          </div>
          <button class="tab-row-comment-toggle" onclick="toggleComment(${i})" title="Kommentar hinzufügen">✎</button>
        </div>
        <div class="tab-row-comment" id="comment-${i}">
          <input type="text" placeholder="Optionaler Kommentar …" data-tab-index="${i}">
        </div>
      </div>
    `;
  }).join('');
}

function toggleComment(index) {
  const el = document.getElementById('comment-' + index);
  if (el) el.classList.toggle('show');
}

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ─── Tabs laden ─────────────────────
function loadTabs() {
  chrome.tabs.query({ currentWindow: true }, (tabs) => {
    // Filter: only http/https tabs, exclude chrome://, brave:// etc.
    const validTabs = tabs.filter(t =>
      t.url && (t.url.startsWith('http://') || t.url.startsWith('https://'))
    );
    renderTabs(validTabs);
  });
}

// ─── Send-Logik (vorbereitet für #30) ─────────────
// #30: Diese Funktion wird in #30 aktiviert
async function sendAllTabs() {
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  btn.classList.add('sending');

  const tabs = currentTabs;
  let success = 0;
  let failed = 0;
  const results = [];

  for (let i = 0; i < tabs.length; i++) {
    const tab = tabs[i];
    const commentInput = document.querySelector(`#comment-${i} input`);
    const comment = commentInput ? commentInput.value.trim() : '';

    btn.textContent = `📡 Sende ${i + 1}/${tabs.length} …`;

    try {
      const res = await fetch(API_BASE + '/api/entries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: tab.url,
          title: tab.title || '',
          source_type: tab.url.includes('youtube.com') || tab.url.includes('youtu.be')
            ? 'youtube'
            : 'web',
        })
      });

      if (res.ok) {
        success++;
        results.push({ tab, status: 'ok' });
        // Tab schließen
        chrome.tabs.remove(tab.id);
      } else {
        failed++;
        results.push({ tab, status: 'error', error: await res.text() });
      }
    } catch (err) {
      failed++;
      results.push({ tab, status: 'error', error: err.message });
    }
  }

  // Ergebnis anzeigen
  const statusEl = document.getElementById('actionStatus');

  if (failed === 0) {
    btn.textContent = `✅ ${success} URL${success !== 1 ? 's' : ''} gesendet`;
    btn.classList.remove('sending');
    btn.classList.add('success');
    statusEl.textContent = '';
    statusEl.className = 'action-status success';
    // Popup nach 1,5s schließen bei vollem Erfolg
    setTimeout(() => window.close(), 1500);
  } else {
    btn.textContent = `⚠️ ${success}/${tabs.length} gesendet`;
    btn.classList.remove('sending');
    btn.classList.add('error');
    statusEl.textContent = `${failed} Fehler — Trend Radar erreichbar? Prüfe SSH-Tunnel.`;
    statusEl.className = 'action-status error';
    btn.disabled = false;
    // Nach 5s in Ausgangszustand zurück
    setTimeout(() => {
      btn.textContent = '📡 Alle schließen & senden';
      btn.classList.remove('error');
      statusEl.textContent = '';
      statusEl.className = 'action-status';
    }, 5000);
  }
}

// ─── Initialisierung ─────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadTabs();

  // Senden-Button aktivieren
  document.getElementById('sendBtn').addEventListener('click', sendAllTabs);
});