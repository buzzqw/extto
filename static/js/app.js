/**
 * EXTTO — Media Torrent Automation System
 * Copyright (C) 2024-2026 Andrea Zanzani <azanzani@gmail.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the EUPL-1.2 EN.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
 *
 * SPDX-License-Identifier: EUPL-1.2 EN
 * Donations: https://www.paypal.com/donate/?business=azanzani%40gmail.com&currency_code=EUR
 */

// ============================================================================
// EXTTO Web Interface - JavaScript v9.5 (FINAL FULL RESTORED)
// ============================================================================

const API_BASE = '';
// Il collegamento diretto verrà impostato dinamicamente leggendo il config all'avvio
let LT_BASE = ''; 

let currentView = 'dashboard';
let autoScroll = true;

// ============================================================================
// LINGUE AUDIO/SUB SUPPORTATE — unica fonte di verità per tutti i dropdown
// Aggiungere una riga qui aggiorna automaticamente tutta l'interfaccia.
// code = sigla ISO 639-2 usata nei titoli torrent
// ============================================================================
const EXTTO_LANGUAGES = [
    { code: 'ita', label: 'Italiano'    },
    { code: 'eng', label: 'English'     },
    { code: 'deu', label: 'Deutsch'     },
    { code: 'fra', label: 'Français'    },
    { code: 'spa', label: 'Español'     },
    { code: 'por', label: 'Português'   },
    { code: 'jpn', label: '日本語'       },
    { code: 'chi', label: '中文'         },
    { code: 'kor', label: '한국어'        },
    { code: 'rus', label: 'Русский'     },
    { code: 'ara', label: 'العربية'     },
    { code: 'nld', label: 'Nederlands'  },
    { code: 'pol', label: 'Polski'      },
    { code: 'tur', label: 'Türkçe'      },
    { code: 'swe', label: 'Svenska'     },
    { code: 'nor', label: 'Norsk'       },
    { code: 'dan', label: 'Dansk'       },
    { code: 'fin', label: 'Suomi'       },
    { code: 'hun', label: 'Magyar'      },
    { code: 'cze', label: 'Čeština'     },
    { code: 'ron', label: 'Română'      },
    { code: 'ukr', label: 'Українська'  },
];

/**
 * Popola un <select> con le lingue di EXTTO_LANGUAGES.
 * withAny    → aggiunge "— nessuna preferenza —" come prima voce
 * withCombo  → aggiunge "<Lingua> + English" per la lingua primaria (primaryCode)
 * withCustom → aggiunge "Personalizzato…" come ultima voce
 */
function _fillLangSelect(selectId, { withAny=false, withCombo=false, withCustom=false, primaryCode=null } = {}) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '';
    if (withAny) {
        const o = document.createElement('option');
        o.value = ''; o.textContent = t('— nessuna preferenza —');
        sel.appendChild(o);
    }
    EXTTO_LANGUAGES.forEach(l => {
        const o = document.createElement('option');
        o.value = l.code; o.textContent = l.label;
        sel.appendChild(o);
        if (withCombo && primaryCode && l.code === primaryCode && l.code !== 'eng') {
            const combo = document.createElement('option');
            combo.value = `${l.code},eng`;
            const engLabel = EXTTO_LANGUAGES.find(x => x.code === 'eng')?.label || 'English';
            combo.textContent = `${l.label} + ${engLabel}`;
            sel.appendChild(combo);
        }
    });
    if (withCustom) {
        const o = document.createElement('option');
        o.value = 'custom'; o.textContent = t('Personalizzato…');
        sel.appendChild(o);
    }
    // Ripristina valore precedente se valido, altrimenti usa lingua default config
    if (prev && [...sel.options].some(o => o.value === prev)) {
        sel.value = prev;
    } else {
        // Per i select audio (non subtitoli): preseleziona la lingua default contenuti
        const defLang = (typeof app !== 'undefined' && app._primaryLang) ? app._primaryLang : '';
        if (defLang && [...sel.options].some(o => o.value === defLang)) {
            sel.value = defLang;
        }
    }
}

// Funzione di traduzione globale: t('chiave') -> traduzione o chiave originale
function t(key) {
    if (typeof app !== 'undefined' && app._i18nDict) {
        if (app._i18nDict[key]) return app._i18nDict[key];
        
        if (key && typeof key === 'string' && app._i18nDictNorm) {
            let normKey = key.replace(/\s+/g, ' ').trim();
            if (app._i18nDictNorm[normKey]) return app._i18nDictNorm[normKey];
        }
    }
    return key;
}

let logEventSource = null;
let sysChartInst = null;
let sysChartPollId = null;
let torrentTagsDb = {};
let currentTagFilter = 'all';
let activeRechecks = new Map(); // <-- NUOVO: Memoria Avanzata per Esiti Recheck

// NUOVE VARIABILI PER LA MEMORIA DEL GRAFICO
const MAX_SYS_POINTS = 120;
const titles = { dashboard: 'Dashboard', discovery: 'Esplora', comics: 'Fumetti',
    series: 'Serie TV', movies: 'Film', archive: 'Archivio', torrent: 'Scarico',
    amule: 'aMule / ed2k',
    config: 'Configurazione', maintenance: 'Manutenzione DB', charts: 'Grafici', logs: 'Log',
    manual: 'Manuale', health: 'Salute di Sistema', extto: 'Dettagli Serie',
    radarr: 'Dettagli Film', activity: 'Attività', license: 'Licenza & Informazioni' };
// Risolve il titolo della view con traduzione se disponibile
function viewTitle(view) { const k = titles[view] || 'Pannello di controllo'; return t(k); }
// Alias (stessa struttura, usata per riferimento i18n keys)
const titlesI18nKeys = titles;
let sysData = { labels: [], cpu: [], ram: [], disk: [], ramdisk: [], dl: [], ul: [] };

// ============================================================================
// SHOW / HIDE HELPERS
// Usano classList invece di style.display, così il CSS mantiene il controllo
// sul valore di display corretto (block / flex / grid / etc).
// .hidden { display: none !important } è definita in style.css sezione 18.
// ============================================================================

/** Nasconde uno o più elementi. Accetta Element, NodeList, Array, o ID stringa. */
function hideEl(...els) {
    for (const el of els) {
        if (!el) continue;
        if (typeof el === 'string') { hideEl(document.getElementById(el)); continue; }
        if (el.forEach) { el.forEach(e => hideEl(e)); continue; }
        el.classList.add('hidden');
    }
}

/** Mostra uno o più elementi. Accetta Element, NodeList, Array, o ID stringa. */
function showEl(...els) {
    for (const el of els) {
        if (!el) continue;
        if (typeof el === 'string') { showEl(document.getElementById(el)); continue; }
        if (el.forEach) { el.forEach(e => showEl(e)); continue; }
        el.classList.remove('hidden');
    }
}

/** Alterna visibilità. Restituisce true se ora è visibile. */
function toggleEl(el) {
    if (!el) return false;
    const nowHidden = el.classList.toggle('hidden');
    return !nowHidden;
}

/** Mostra l'elemento se la condizione è vera, altrimenti lo nasconde. */
function showIf(el, condition) {
    if (!el) return;
    if (typeof el === 'string') { showIf(document.getElementById(el), condition); return; }
    condition ? el.classList.remove('hidden') : el.classList.add('hidden');
}

/** Verifica se un elemento è visibile (non ha la classe .hidden). */
function isVisible(el) {
    if (!el) return false;
    return !el.classList.contains('hidden');
}

/** Popola il widget sottotitoli (preset + campo custom) con un valore esistente. */
function _populateSubtitleWidget(presetId, customId, value) {
    const preset = document.getElementById(presetId);
    const custom = document.getElementById(customId);
    if (!preset) return;
    const knownCodes  = EXTTO_LANGUAGES.map(l => l.code);
    const knownCombos = EXTTO_LANGUAGES.filter(l => l.code !== 'eng').map(l => `${l.code},eng`);
    const known = ['', ...knownCodes, ...knownCombos];
    if (known.includes(value)) {
        preset.value = value;
        if (custom) { custom.value = ''; hideEl(custom); }
    } else {
        preset.value = 'custom';
        if (custom) { custom.value = value; showEl(custom); }
    }
}

const app = {
    // ========================================================================
    // INITIALIZATION
    // ========================================================================
    async init() {
        // 0. Applica le traduzioni UI prima di mostrare qualsiasi cosa
        await this.applyTranslations();

        // 1. Recupera la porta dinamica del motore e la lingua default contenuti
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const data = await res.json();
            const enginePort = data.settings.engine_port || 8889;
            LT_BASE = `${window.location.protocol}//${window.location.hostname}:${enginePort}`;
            // Lingua default contenuti: usata per preselezionare i select audio nelle serie/film
            const dl = (data.settings.default_language || '').trim().toLowerCase();
            if (dl) {
                this._primaryLang = dl;
                this._updateBonusLangLabel(dl);
            } else {
                // Fallback: se non c'è default_language configurata, usa la lingua UI
                // (es. UI in inglese → propone 'eng' nei nuovi contenuti)
                const UI_TO_CONTENT = {
                    'it': 'ita', 'en': 'eng', 'de': 'deu',
                    'fr': 'fra', 'es': 'spa', 'pt': 'por',
                    'ja': 'jpn', 'zh': 'zho', 'ru': 'rus',
                    'ko': 'kor', 'ar': 'ara', 'nl': 'nld',
                };
                const uiLang = (this._activeLang || '').split('-')[0].toLowerCase();
                const contentLang = UI_TO_CONTENT[uiLang] || '';
                if (contentLang) {
                    this._primaryLang = contentLang;
                    this._updateBonusLangLabel(contentLang);
                }
            }
        } catch (e) {
            LT_BASE = `${window.location.protocol}//${window.location.hostname}:8889`;
        }

        // 2. Procedi con il normale avvio dell'interfaccia
        this.initLangSelects();
        this.setupNavigation();
        this.setupConfigTabs();
        this.setupSearchHandlers();
        this.loadDashboard();
        this.startLogStream();
        this.startSystemStatsPoll(); 
        
        this._dashboardInterval = setInterval(() => {
            if (currentView === 'dashboard') {
                this.loadStats();
                this.updateNextRunTimer();
            }
        }, 30000);
    },
    
    // ========================================================================
    // NAVIGATION & SEARCH
    // ========================================================================
    setupNavigation() {
        document.querySelectorAll('.nav-item[data-view]').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const view = item.dataset.view;
                if (view) this.switchView(view);
            });
        });
        // Drawer items
        document.querySelectorAll('.mobile-drawer-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const view = item.dataset.view;
                if (view) this.switchView(view);
            });
        });
        // Quick bar visibility: mostra solo su dashboard
        this._updateQuickBar();
    },
    
    switchView(view) {
        if (!view) return;
        document.querySelectorAll('.nav-item').forEach(item => {
            let targetView = view;
            if (view === 'extto') targetView = 'series';
            if (view === 'radarr') targetView = 'movies'; 
            item.classList.toggle('active', item.dataset.view === targetView);
        });

        document.querySelectorAll('.view').forEach(v => {
            v.classList.remove('active');
            if (v.id === `view-${view}`) v.classList.add('active');
        });
        
        const viewTitleEl = document.getElementById('view-title');
        if (viewTitleEl) viewTitleEl.textContent = viewTitle(view);
        
        currentView = view;
        
        // Aggiorna quickbar e drawer active state
        this._updateQuickBar();
        this._updateDrawerActive(view);

        // Avvia/ferma polling torrent
        if (view === 'torrent') {
            this.startTorrentPoll();
        } else {
            this.stopTorrentPoll();
        }

        // Avvia/ferma polling aMule
        if (view === 'amule') {
            this.amuleStartPoll();
        } else {
            this.amuleStopPoll();
        }

        // Ferma polling attività se si cambia vista
        if (view !== 'activity' && this._activityPollInterval) {
            clearInterval(this._activityPollInterval);
            this._activityPollInterval = null;
        }

        // Carica i dati specifici della view
        switch(view) {
            case 'dashboard': this.loadDashboard(); break;
            case 'discovery': this.loadDiscovery('movie'); break;
            case 'license': this.loadLicense(); break;
            case 'series': this.loadSeries(); break;
            case 'movies': this.loadMovies(); break;
            case 'radarr': break;
            case 'archive': this.loadArchive(); break;
            case 'torrent': 
                fetch(`${API_BASE}/api/config?_t=` + Date.now()).then(r => r.json()).then(data => {
                    const s = data.settings || {};
                    const parseLimit = (v) => { let val = parseInt(v)||0; return val > 1000000 ? Math.round(val/1024) : val; };
                    const dl = parseLimit(s.libtorrent_dl_limit);
                    const ul = parseLimit(s.libtorrent_ul_limit);
                    const dlInput = document.getElementById('quick-dl-limit');
                    const ulInput = document.getElementById('quick-ul-limit');
                    if(dlInput) dlInput.value = dl;
                    if(ulInput) ulInput.value = ul;
                }).catch(()=>{});
                break; 
            case 'config': this.loadConfig(); break;
            case 'maintenance': this.loadDbInfo(); this.loadBackupSettings(); this.loadConfigForMaintenance(); break;
            case 'logs': this.loadLogs(); break;
            case 'health': this.loadHealth(); break;
            case 'wanted': this.loadWanted(); break;
            case 'calendar': this.loadCalendar(); break;
            case 'activity': this.loadActivity(); break;
            case 'comics': this.comicsSetTab('explore'); break;
            case 'amule': this.amuleOnEnter(); break;
            case 'charts': {
                const cv = document.getElementById('systemChart');
                if (cv) { cv.style.height=''; cv.style.width=''; cv.removeAttribute('height'); cv.removeAttribute('width'); }
                setTimeout(() => { if (sysChartInst) { sysChartInst.destroy(); sysChartInst = null; } this.initSystemChart(); }, 420);
                break;
            }
        }
    },

    setupSearchHandlers() {
        const seriesInput = document.getElementById('series-search');
        if (seriesInput) seriesInput.addEventListener('input', (e) => {
            clearTimeout(this._seriesSearchTimer);
            this._seriesSearchTimer = setTimeout(() => this.loadSeries(e.target.value), 300);
        });

        const archiveInput = document.getElementById('archive-search');
        if (archiveInput) archiveInput.addEventListener('input', (e) => {
            clearTimeout(this._archiveSearchTimer);
            this._archiveSearchTimer = setTimeout(() => this.loadArchive(0, e.target.value), 300);
        });

        const movieInput = document.getElementById('movies-search');
        if (movieInput) {
            movieInput.addEventListener('input', (e) => {
                const term = e.target.value.toLowerCase();
                document.querySelectorAll('#movies-config-list .table-row:not(.table-header), #movies-downloaded-list .table-row:not(.table-header)').forEach(row => {
                    if (row.textContent.toLowerCase().includes(term)) {
                        row.style.setProperty('display', 'grid', 'important');
                    } else {
                        row.style.setProperty('display', 'none', 'important');
                    }
                });
            });
        }

        const logInput = document.getElementById('log-search');
        if (logInput) {
            logInput.addEventListener('input', (e) => {
                const term = e.target.value.toLowerCase();
                document.querySelectorAll('.log-line').forEach(row => {
                    showIf(row, row.textContent.toLowerCase().includes(term));
                });
            });
        }
    },
    
    // ========================================================================
    // LICENZA
    // ========================================================================
    async loadLicense() {
        const el = document.getElementById('license-text-content');
        if (!el) return;
        // Carica solo la prima volta
        if (el._loaded) return;
        el.textContent = 'Caricamento...';
        try {
            const resp = await fetch('/api/license');
            const data = await resp.json();
            if (data.error) {
                el.textContent = 'Errore: ' + data.error;
            } else {
                el.textContent = data.content;
                el._loaded = true;
            }
        } catch (e) {
            el.textContent = 'Impossibile caricare LICENSE.txt: ' + e.message;
        }
    },

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    async loadDashboard() {
        await this.loadStats();
        await this.updateNextRunTimer();
        this.loadRecentDownloads();
    },
    
    async loadStats() {
        try {
            const res = await fetch(`${API_BASE}/api/stats?_t=` + Date.now());
            const stats = await res.json();
            
            document.getElementById('stat-series').textContent = stats.series_configured || 0;
            document.getElementById('stat-movies').textContent = stats.movies_configured || 0;
            document.getElementById('stat-downloads').textContent = stats.downloads || stats.total || 0;
            
            document.getElementById('stat-archive').textContent = stats.archive_size || stats.total || 0;
            
            // Collega il valore dello spazio libero! (Cerca varie chiavi comuni)
            const diskFree = stats.disk_free || stats.free_space || stats.disk_free_gb;
            if (diskFree !== undefined) {
                document.getElementById('disk-free-value').textContent = `${diskFree} GB`;
            }

            // Statistiche di consumo nella Dashboard
            if (stats.consumption) {
                const cons = stats.consumption;
                const archiveLabel = document.querySelector('#stat-archive').parentElement.querySelector('.stat-label');
                if (archiveLabel) {
                    archiveLabel.innerHTML = `${t('Consumo:')} <b>${cons.last_30_days_gb} GB</b> (30d)`;
                }
            }

            document.getElementById('stat-series-configured').textContent = stats.series_configured || 0;
            document.getElementById('stat-series-enabled').textContent = stats.series_enabled || 0;
            document.getElementById('stat-movies-ratio').textContent = `${stats.movies_configured || 0} / ${stats.movies || 0}`;
            document.getElementById('stat-last-activity').textContent = stats.last_activity ? this.formatDate(stats.last_activity) : 'N/A';

            // Stats fumetti — da /api/comics (stesso endpoint usato dalla tab Monitorati)
            try {
                const cRes = await fetch(`${API_BASE}/api/comics`);
                const cData = await cRes.json();
                const comicsCount = (cData.success && cData.comics) ? cData.comics.length : 0;
                const elC = document.getElementById('stat-comics-configured');
                if (elC) elC.textContent = comicsCount;
            } catch(_) {}
            try {
                const hRes = await fetch(`${API_BASE}/api/comics/history`);
                const hData = await hRes.json();
                const dlCount = (hData.success && hData.history) ? hData.history.length : 0;
                const elD = document.getElementById('stat-comics-downloads');
                if (elD) elD.textContent = dlCount;
            } catch(_) {}
        } catch (err) { console.error(err); }
    },

    async loadHealth() {
        try {
            const resp = await fetch(`${API_BASE}/api/health?_t=` + Date.now());
            const data = await resp.json();
            
            // Resource Cards
            if (data.system) {
                const sys = data.system;
                document.getElementById('health-cpu-val').textContent = `${sys.cpu_percent}%`;
                document.getElementById('health-cpu-bar').style.width = `${sys.cpu_percent}%`;
                document.getElementById('health-cpu-card').classList.toggle('warning', sys.cpu_percent > 80);

                document.getElementById('health-ram-val').textContent = `${sys.ram_percent}%`;
                document.getElementById('health-ram-bar').style.width = `${sys.ram_percent}%`;
                document.getElementById('health-ram-card').classList.toggle('warning', sys.ram_percent > 85);

                // Uptime format
                let s = sys.uptime_seconds;
                let d = Math.floor(s / 86400); s %= 86400;
                let h = Math.floor(s / 3600); s %= 3600;
                let m = Math.floor(s / 60);
                document.getElementById('health-uptime-val').textContent = `${d}g ${h}o ${m}m`;
            }

            // Disks
            const diskList = document.getElementById('health-disks-list');
            diskList.innerHTML = '';
            (data.disk || []).forEach(d => {
                const item = document.createElement('div');
                item.className = `h-list-item ${d.status}`;
                const trashExtra = (d.trash_content_gb !== undefined)
                    ? `<div style="font-size:0.75rem; color:var(--warning); margin-top:2px;">`
                    + `&#x1F5D1; ${d.trash_content_gb} GB ${t('nel cestino')} (${d.trash_file_count} ${t('file')})</div>`
                    : '';
                item.innerHTML = `
                    <div style="display:flex; flex-direction:column;">
                        <b>${d.label}</b>
                        <span style="font-size:0.75rem; opacity:0.7;">${d.path}</span>
                    </div>
                    <div style="text-align:right;">
                        <div>${d.free_gb} GB ${t('liberi')}</div>
                        <div style="font-size:0.75rem; font-weight:700;">${d.percent}% ${t('occupato')}</div>
                        ${trashExtra}
                    </div>
                `;
                diskList.appendChild(item);
            });

            // Indexers
            const idxList = document.getElementById('health-indexers-list');
            idxList.innerHTML = '';
            (data.indexers || []).forEach(i => {
                const item = document.createElement('div');
                item.className = 'h-list-item';
                item.innerHTML = `
                    <span>${i.name}</span>
                    <span class="status-badge ${i.status}">${i.status}</span>
                `;
                idxList.appendChild(item);
            });

            // Folders
            const foldList = document.getElementById('health-folders-list');
            foldList.innerHTML = '';
            (data.folders || []).forEach(f => {
                const item = document.createElement('div');
                item.className = 'h-list-item';
                const status = f.writable ? `<span class="status-badge ok">${t('Scrivibile')}</span>` : `<span class="status-badge error">${t('Sola Lettura')}</span>`;
                item.innerHTML = `
                    <span style="font-size:0.8rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:200px;">${f.path}</span>
                    ${status}
                `;
                foldList.appendChild(item);
            });

            // Services
            const servList = document.getElementById('health-services-list');
            if (servList) {
                servList.innerHTML = '';
                if (!data.services || data.services.length === 0) {
                    servList.innerHTML = `<div class="h-list-item">${t('Nessun servizio rilevato')}</div>`;
                } else {
                    data.services.forEach(s => {
                        const item = document.createElement('div');
                        item.className = 'h-list-item';
                        item.innerHTML = `
                            <span>${s.name}</span>
                            <span class="status-badge ${s.ok ? 'ok' : 'error'}">${s.status}</span>
                        `;
                        servList.appendChild(item);
                    });
                }
            }

            // Logs
            const logList = document.getElementById('health-logs-list');
            if (logList) {
                logList.innerHTML = '';
                if (!data.logs || data.logs.length === 0) {
                    logList.innerHTML = `<div class="h-list-item">${t('Nessun errore rilevato')}</div>`;
                } else {
                    data.logs.forEach(l => {
                        const item = document.createElement('div');
                        item.style.padding = '4px 0';
                        item.style.borderBottom = '1px solid var(--border)';
                        item.style.color = 'var(--danger)';
                        item.textContent = l;
                        logList.appendChild(item);
                    });
                }
            }

            // Consumption Stats (Tab Health)
            fetch(`${API_BASE}/api/stats?_t=` + Date.now()).then(r => r.json()).then(sData => {
                if (sData.consumption) {
                    const c = sData.consumption;
                    document.getElementById('health-cons-7').textContent = `${c.last_7_days_gb} GB`;
                    document.getElementById('health-cons-30').textContent = `${c.last_30_days_gb} GB`;
                    document.getElementById('health-cons-total').textContent = `${c.total_gb} GB`;
                }
            });

        } catch (err) {
            console.error('Errore caricamento health:', err);
        }
    },

    async updateNextRunTimer() {
        try {
            const res = await fetch(`${API_BASE}/api/last_cycle`);
            if(!res.ok) throw new Error();
            const data = await res.json();
            const timerEl = document.getElementById('next-run-timer');
            const textEl = document.getElementById('timer-text');
            
            if(data && data.generated_at) {
                // Legge i secondi di refresh dal server. Se manca, usa 7200 (2 ore)
                const refreshMs = (data.refresh_interval || 7200) * 1000;
                const nextRun = new Date(new Date(data.generated_at).getTime() + refreshMs);
                
                // Ferma eventuali timer precedenti per non accavallarli
                if (this._countdownInterval) clearInterval(this._countdownInterval);
                
                // Aggiorna il testo ogni singolo secondo
                this._countdownInterval = setInterval(() => {
                    const now = new Date();
                    const diff = nextRun - now;
                    
                    if(diff > 0) {
                        // Calcola minuti e secondi rimanenti
                        const m = Math.floor(diff / 60000);
                        const s = Math.floor((diff % 60000) / 1000);
                        if(textEl) textEl.textContent = `${m}m ${s}s`;
                        timerEl.title = `Prossima scansione alle ${nextRun.toLocaleTimeString('it-IT')}`;
                    } else {
                        if(textEl) textEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ' + t('In corso...');
                        timerEl.title = t("Scansione in corso");
                        clearInterval(this._countdownInterval);
                    }
                }, 1000);
            } else { 
                if(textEl) textEl.textContent = t('Avvio...'); 
            }
        } catch (e) { console.warn('runNow UI:', e); }
    },
    
    async loadRecentDownloads() {
        try {
            const res = await fetch(`${API_BASE}/api/recent-downloads`);
            const data = await res.json();
            const container = document.getElementById('dashboard-logs');

            const TYPE_META = {
                episode: { icon: 'fa-tv',            color: '#3b82f6', label: t('Serie TV')  },
                movie:   { icon: 'fa-film',           color: '#f59e0b', label: t('Film')      },
                comic:   { icon: 'fa-book-open-reader', color: '#10b981', label: t('Fumetto') },
                default: { icon: 'fa-satellite-dish', color: '#6b7280', label: t('Altro')    },
            };

            // Pattern geometrici unici per tipo — simula un "poster" senza immagine
            const PATTERNS = {
                episode: (c) => `
                    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;">
                        <defs>
                            <linearGradient id="ge${c}" x1="0%" y1="0%" x2="100%" y2="100%">
                                <stop offset="0%" style="stop-color:#1e3a5f;stop-opacity:1"/>
                                <stop offset="100%" style="stop-color:#0f1115;stop-opacity:1"/>
                            </linearGradient>
                        </defs>
                        <rect width="100%" height="100%" fill="url(#ge${c})"/>
                        <circle cx="80%" cy="20%" r="60" fill="#3b82f6" opacity="0.12"/>
                        <circle cx="10%" cy="80%" r="40" fill="#1d4ed8" opacity="0.15"/>
                        <line x1="0" y1="40%" x2="100%" y2="60%" stroke="#3b82f6" stroke-width="0.5" opacity="0.2"/>
                        <line x1="0" y1="60%" x2="100%" y2="40%" stroke="#3b82f6" stroke-width="0.5" opacity="0.15"/>
                    </svg>`,
                movie: (c) => `
                    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;">
                        <defs>
                            <linearGradient id="gm${c}" x1="0%" y1="100%" x2="100%" y2="0%">
                                <stop offset="0%" style="stop-color:#3b1a00;stop-opacity:1"/>
                                <stop offset="100%" style="stop-color:#0f1115;stop-opacity:1"/>
                            </linearGradient>
                        </defs>
                        <rect width="100%" height="100%" fill="url(#gm${c})"/>
                        <rect x="15%" y="15%" width="70%" height="70%" rx="2" fill="none" stroke="#f59e0b" stroke-width="0.5" opacity="0.2"/>
                        <rect x="25%" y="25%" width="50%" height="50%" rx="1" fill="none" stroke="#f59e0b" stroke-width="0.5" opacity="0.15"/>
                        <circle cx="50%" cy="50%" r="30" fill="#f59e0b" opacity="0.06"/>
                    </svg>`,
                comic: (c) => `
                    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;">
                        <defs>
                            <linearGradient id="gc${c}" x1="100%" y1="0%" x2="0%" y2="100%">
                                <stop offset="0%" style="stop-color:#064e3b;stop-opacity:1"/>
                                <stop offset="100%" style="stop-color:#0f1115;stop-opacity:1"/>
                            </linearGradient>
                            <pattern id="dots${c}" x="0" y="0" width="12" height="12" patternUnits="userSpaceOnUse">
                                <circle cx="6" cy="6" r="1" fill="#10b981" opacity="0.2"/>
                            </pattern>
                        </defs>
                        <rect width="100%" height="100%" fill="url(#gc${c})"/>
                        <rect width="100%" height="100%" fill="url(#dots${c})"/>
                    </svg>`,
                default: (c) => `
                    <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" style="position:absolute;inset:0;">
                        <rect width="100%" height="100%" fill="#151922"/>
                        <circle cx="50%" cy="50%" r="40" fill="#6b7280" opacity="0.08"/>
                    </svg>`,
            };

            const buildCard = (item, idx) => {
                const meta = TYPE_META[item.type] || TYPE_META.default;
                const pat  = (PATTERNS[item.type] || PATTERNS.default)(idx);
                const safeMag = item.magnet ? this.escapeJs(item.magnet) : '';
                const safeTitle = this.escapeHtml(item.title);
                const safeJs = this.escapeJs(item.title);
                const posterUrl = item.poster_url || '';

                // Navigazione al click sulla card
                let navAction = '';
                if (item.type === 'episode' || item.type === 'pack') {
                    const sid  = item.series_id  || 0;
                    const sname = this.escapeJs(item.series_name || item.title.split(' — ')[0]);
                    navAction = `app.showEpisodes(${sid}, '${sname}')`;
                } else if (item.type === 'movie') {
                    navAction = `app.switchView('radarr')`;
                } else if (item.type === 'comic') {
                    navAction = `app.switchView('comics')`;
                }

                const scoreHtml = item.quality_score
                    ? `<span style="background:rgba(0,0,0,.4);padding:1px 7px;border-radius:4px;font-size:.72rem;color:#fbbf24;white-space:nowrap;">★ ${item.quality_score}</span>`
                    : '';
                const removedHtml = item.removed
                    ? `<span style="font-size:.68rem;padding:2px 6px;border-radius:4px;background:var(--danger);color:#fff;"><i class="fa-solid fa-box-archive"></i> ${t('Rimosso')}</span>`
                    : '';
                const dlBtn = safeMag
                    ? `<button onclick="event.stopPropagation();app._promptNoRename('${safeMag}','',true,'${this.escapeJs(item.title)}')" title="${t('Scarica')}" style="background:rgba(59,130,246,.9);border:none;color:#fff;border-radius:5px;padding:5px 10px;cursor:pointer;font-size:.78rem;flex-shrink:0;"><i class="fa-solid fa-download"></i></button>`
                    : '';
                const cpBtn = safeMag
                    ? `<button onclick="event.stopPropagation();app.copyMagnet('${safeMag}')" title="${t('Copia Magnet')}" style="background:rgba(255,255,255,.1);border:none;color:#fff;border-radius:5px;padding:5px 10px;cursor:pointer;font-size:.78rem;flex-shrink:0;"><i class="fa-regular fa-copy"></i></button>`
                    : '';

                // Poster: immagine reale o pattern SVG
                const posterHtml = posterUrl
                    ? `<img src="${posterUrl}" alt="" style="width:100%;height:100%;object-fit:cover;display:block;" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
                       <div style="display:none;position:absolute;inset:0;align-items:center;justify-content:center;">${pat}<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;"><i class="fa-solid ${meta.icon}" style="font-size:1.4rem;color:${meta.color};opacity:.5;"></i></div></div>`
                    : `${pat}<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;"><i class="fa-solid ${meta.icon}" style="font-size:1.4rem;color:${meta.color};opacity:.5;"></i></div>`;

                return `<div onclick="${navAction}" style="position:relative;display:flex;align-items:stretch;border-radius:8px;overflow:hidden;background:var(--bg-card);border:1px solid var(--border);cursor:${navAction ? 'pointer' : 'default'};transition:transform .15s,border-color .15s;margin-bottom:6px;" onmouseenter="this.style.borderColor='${meta.color}55';this.style.background='var(--bg-hover)'" onmouseleave="this.style.borderColor='var(--border)';this.style.background='var(--bg-card)'">
                    <!-- Poster laterale -->
                    <div style="position:relative;width:70px;min-height:95px;flex-shrink:0;overflow:hidden;background:var(--bg-input);">
                        ${posterHtml}
                    </div>
                    <!-- Info -->
                    <div style="flex:1;padding:10px 12px;min-width:0;display:flex;flex-direction:column;justify-content:center;gap:4px;">
                        <div style="font-size:.88rem;font-weight:700;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;" title="${safeTitle}">${safeTitle}</div>
                        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                            <span style="font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:4px;border:1px solid ${meta.color}60;color:${meta.color};text-transform:uppercase;letter-spacing:.04em;">${meta.label}</span>
                            ${scoreHtml}
                            ${removedHtml}
                            <span style="font-size:.72rem;color:var(--text-muted);">${this.formatDate(item.date)}</span>
                        </div>
                        ${navAction ? `<div style="font-size:.68rem;color:var(--text-muted);opacity:.6;"><i class="fa-solid fa-arrow-right" style="font-size:.6rem;"></i> ${item.type === 'comic' ? t('Fumetti') : item.type === 'movie' ? t('Film') : t('TV Series')}</div>` : ''}
                    </div>
                    <!-- Azioni -->
                    <div style="display:flex;align-items:center;gap:6px;padding:0 12px;flex-shrink:0;">
                        ${dlBtn}${cpBtn}
                    </div>
                </div>`;
            };

            const renderSection = (label, icon, items, emptyMsg) => {
                let html = `<div style="font-size:.7rem;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.08em;font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:6px;"><i class="fa-solid ${icon}"></i>${label}</div>`;
                if (items && items.length > 0) {
                    html += `<div style="margin-bottom:16px;">`;
                    items.forEach((item, i) => html += buildCard(item, i));
                    html += `</div>`;
                } else {
                    html += `<p style="color:var(--text-muted);font-size:.85rem;margin-bottom:16px;">${emptyMsg}</p>`;
                }
                return html;
            };

            container.innerHTML =
                renderSection(t('Aggiunti al Client'),         'fa-cloud-arrow-down', data.downloads, t('Nessun download recente.')) +
                renderSection(t('Ultimi rilevamenti (Archivio)'), 'fa-radar',           data.found,     t('Nessuna release trovata di recente.'));

        } catch (e) {
            console.error(e);
            document.getElementById('dashboard-logs').innerHTML = `<p style="color:var(--danger);">${t('Errore caricamento dati.')}</p>`;
        }
    },
    
    // ========================================================================
    // DISCOVERY (ESPLORA)
    // ========================================================================

    // Stato interno della discovery
    _discovery: {
        mediaType: 'movie',
        category:  'trending',
        inList:    { series: new Map(), movies: new Map() }, // traccia lo stato: 'configured' o 'downloaded'
    },

    async loadDiscovery(type = 'movie', category = null) {
        // Aggiorna tipo media
        this._discovery.mediaType = type;
        if (category) this._discovery.category = category;

        // Sincronizza tab media type
        document.getElementById('discover-btn-movie')?.classList.toggle('active', type === 'movie');
        document.getElementById('discover-btn-tv')?.classList.toggle('active', type === 'tv');

        // Sincronizza tab categoria
        document.querySelectorAll('.discover-cat-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.cat === this._discovery.category);
        });

        // Svuota la barra ricerca quando si cambia categoria non-search
        if (this._discovery.category !== 'search') {
            const sb = document.getElementById('discover-search-input');
            if (sb) sb.value = '';
        }

        const container = document.getElementById('discovery-grid');
        const loader    = document.getElementById('discovery-loading');
        if (!container || !loader) return;

        container.innerHTML = '';
        showEl(loader);

        try {
            // Carica i titoli già in lista (serie + film) per il badge
            await this._loadDiscoveryInList();

            const cat   = this._discovery.category;
            const query = document.getElementById('discover-search-input')?.value.trim() || '';
            let url = `${API_BASE}/api/tmdb/discover?type=${type}&category=${cat}`;
            if (cat === 'search' && query) url += `&q=${encodeURIComponent(query)}`;

            const res  = await fetch(url);
            const data = await res.json();
            hideEl(loader);

            if (!data.success) {
                container.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--danger);">${this.escapeHtml(data.error)}</div>`;
                return;
            }
            if (!data.results.length) {
                container.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:3rem 0;"><i class="fa-solid fa-circle-info" style="font-size:2rem;opacity:.3;display:block;margin-bottom:.75rem;"></i>${t('Nessun risultato trovato')}</div>`;
                return;
            }

            container.innerHTML = data.results.map(item => this._renderDiscoveryCard(item, type)).join('');

        } catch (e) {
            hideEl(loader);
            container.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--danger);">${t('Errore di connessione')}</div>`;
        }
    },

    _normalizeListTitle(t) {
        // Rimuove caratteri speciali eliminati dai nomi torrent (: ! ? ' " , ; .)
        return (t || '').toLowerCase().trim().replace(/[:\!\?\.'"',;]/g, '').replace(/\s+/g, ' ').trim();
    },

    async _loadDiscoveryInList() {
        try {
            // Chiamate parallele per Configurazione, Film dal DB e Serie completate
            const [resConf, resMov, resSerComp] = await Promise.all([
                fetch(`${API_BASE}/api/config`).catch(() => null),
                fetch(`${API_BASE}/api/movies`).catch(() => null),
                fetch(`${API_BASE}/api/series/completeness`).catch(() => null)
            ]);

            const dataConf = resConf ? await resConf.json() : { series: [], movies: [] };
            const dataMov = resMov ? await resMov.json() : [];
            const dataSerComp = resSerComp ? await resSerComp.json() : {};

            this._discovery.inList.series = new Map();
            this._discovery.inList.movies = new Map();

            // 1. Segna tutti quelli configurati come "In lista"
            (dataConf.series || []).forEach(s => this._discovery.inList.series.set(this._normalizeListTitle(s.name), 'configured'));
            (dataConf.movies || []).forEach(m => this._discovery.inList.movies.set(this._normalizeListTitle(m.name), 'configured'));

            // 2. Sovrascrive con "Scaricato" se il DB dei film conferma il download
            (dataMov || []).forEach(m => {
                if (m.downloaded) {
                    this._discovery.inList.movies.set(this._normalizeListTitle(m.name), 'downloaded');
                }
            });

            // 3. Sovrascrive con "Scaricato" per le serie TV giunte al 100%
            for (const [name, isCompleted] of Object.entries(dataSerComp)) {
                if (isCompleted) {
                    this._discovery.inList.series.set(this._normalizeListTitle(name), 'downloaded');
                }
            }
        } catch(e) { console.error("Errore _loadDiscoveryInList:", e); }
    },

    _getListStatus(title, type) {
        const t = this._normalizeListTitle(title);
        const map = type === 'tv' ? this._discovery.inList.series : this._discovery.inList.movies;
        return map.has(t) ? map.get(t) : null;
    },

    _renderDiscoveryCard(item, type) {
        const posterUrl  = item.poster ? `https://image.tmdb.org/t/p/w342${item.poster}` : '';
        const title      = this.escapeHtml(item.title);
        const year       = item.year || '';
        const vote       = item.vote ? item.vote.toFixed(1) : '-';
        const overview   = this.escapeHtml(item.overview);
        const safeTitle  = this.escapeJs(item.title);
        const safeYear   = this.escapeJs(year);
        const tmdbLink   = `https://www.themoviedb.org/${type}/${item.id}?language=it-IT`;
        const listStatus = this._getListStatus(item.title, type);
        const action     = type === 'movie'
            ? `app.quickAddMovie('${safeTitle}', '${safeYear}')`
            : `app.quickAddSeries('${safeTitle}')`;

        // Badge e Pulsanti Dinamici
        let inListBadge = '';
        let addBtn = '';

        if (listStatus === 'downloaded') {
            inListBadge = `<div style="position:absolute;bottom:8px;left:8px;background:rgba(16,185,129,0.92);color:#fff;padding:3px 8px;border-radius:4px;font-size:0.72rem;font-weight:700;display:flex;align-items:center;gap:4px;"><i class="fa-solid fa-check-double"></i> ${t('Scaricato')}</div>`;
            addBtn = `<button class="btn btn-success btn-small" style="flex:1;justify-content:center;cursor:default;background:rgba(16,185,129,0.15);border-color:rgba(16,185,129,0.5);color:#34d399;" disabled><i class="fa-solid fa-check-double"></i> ${t('Scaricato')}</button>`;
        } else if (listStatus === 'configured') {
            inListBadge = `<div style="position:absolute;bottom:8px;left:8px;background:rgba(59,130,246,0.92);color:#fff;padding:3px 8px;border-radius:4px;font-size:0.72rem;font-weight:700;display:flex;align-items:center;gap:4px;"><i class="fa-solid fa-list"></i> ${t('In lista')}</div>`;
            addBtn = `<button class="btn btn-primary btn-small" style="flex:1;justify-content:center;opacity:.7;cursor:default;" disabled><i class="fa-solid fa-check"></i> ${t('In lista')}</button>`;
        } else {
            addBtn = `<button class="btn btn-primary btn-small" style="flex:1;justify-content:center;" onclick="${action}"><i class="fa-solid fa-plus"></i> ${t('Aggiungi')}</button>`;
        }

        return `
            <div class="card" style="display:flex;flex-direction:column;overflow:hidden;background:var(--bg-secondary);border:1px solid var(--border);transition:transform .2s,border-color .2s;" onmouseenter="this.style.transform='translateY(-3px)';this.style.borderColor='var(--primary)'" onmouseleave="this.style.transform='';this.style.borderColor='var(--border)'">
                <div style="position:relative;padding-top:150%;background:#1e293b;">
                    ${item.poster
                        ? `<img src="${posterUrl}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;" loading="lazy">`
                        : `<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#475569;"><i class="fa-solid fa-image fa-2x"></i></div>`}
                    <div style="position:absolute;top:8px;right:8px;background:rgba(0,0,0,0.8);color:#fbbf24;padding:3px 8px;border-radius:4px;font-weight:bold;font-size:0.82rem;"><i class="fa-solid fa-star"></i> ${vote}</div>
                    ${inListBadge}
                    <!-- Overlay trama on hover -->
                    <div class="disc-overview-overlay" style="position:absolute;inset:0;background:rgba(15,23,42,0.93);padding:12px;overflow-y:auto;opacity:0;transition:opacity .22s;pointer-events:none;display:flex;align-items:flex-start;">
                        <p style="margin:0;font-size:0.8rem;color:#cbd5e1;line-height:1.5;">${overview}</p>
                    </div>
                </div>
                <div style="padding:12px;flex:1;display:flex;flex-direction:column;justify-content:space-between;gap:10px;">
                    <div>
                        <h4 style="margin:0;font-size:1rem;line-height:1.25;color:var(--text-primary);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;" title="${title}">${title}</h4>
                        <span style="font-size:0.82rem;color:var(--text-muted);">${year}</span>
                    </div>
                    <div style="display:flex;gap:5px;width:100%;">
                        ${addBtn}
                        <a href="${tmdbLink}" target="_blank" class="btn btn-secondary btn-small" style="flex:0 0 auto;justify-content:center;text-decoration:none;display:flex;align-items:center;" title="Apri su TMDB"><i class="fa-solid fa-circle-info"></i></a>
                    </div>
                </div>
            </div>`;
    },

    // Ricerca live con debounce
    _discoverySearchTimer: null,
    discoverySearchKeyup(e) {
        clearTimeout(this._discoverySearchTimer);
        const q = e.target.value.trim();
        if (q.length === 0) {
            // torna a trending se svuota la barra
            this._discovery.category = 'trending';
            document.querySelectorAll('.discover-cat-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.cat === 'trending');
            });
            this.loadDiscovery(this._discovery.mediaType, 'trending');
            return;
        }
        if (q.length < 2) return;
        this._discoverySearchTimer = setTimeout(() => {
            this._discovery.category = 'search';
            document.querySelectorAll('.discover-cat-btn').forEach(b => b.classList.remove('active'));
            this.loadDiscovery(this._discovery.mediaType, 'search');
        }, 450);
    },

    quickAddMovie(title, year) {
        this.showMoviesEditor();
        const cleanTitle = title.replace(/[:\!\?\.'"',;]/g, '').replace(/\s+/g, ' ').trim();
        document.getElementById('movie-name').value = cleanTitle;
        document.getElementById('movie-year').value = year;
    },

    quickAddSeries(title) {
        this.showSeriesEditor();
        const cleanTitle = title.replace(/[:\!\?\.'"',;]/g, '').replace(/\s+/g, ' ').trim();
        document.getElementById('series-name').value = cleanTitle;
    },
    
    
    
    startSystemStatsPoll() {
    if (this._isPollingStats) return; 
    this._isPollingStats = true;

    const poll = async () => {
        if (!this._isPollingStats) return; // Se è stato fermato, esci

        try {
            await this.fetchSystemStats();
        } catch (e) {
            console.error("Errore polling:", e);
        } finally {
            // Pianifica il PROSSIMO ciclo solo DOPO che questo è finito
            sysChartPollId = setTimeout(poll, 5000);
        }
    };
    
    poll(); // Avvia il primo ciclo
},

    initSystemChart() {
        const ctx = document.getElementById('systemChart');
        if (!ctx) return;

        if (sysChartInst) { sysChartInst.destroy(); sysChartInst = null; }

        // Reset dimensioni inline scritte da Chart.js in init precedenti
        // (altrimenti offsetHeight rimane 0 anche con la view visibile)
        ctx.style.height = '';
        ctx.style.width = '';
        ctx.removeAttribute('height');
        ctx.removeAttribute('width');

        Chart.defaults.color = '#94a3b8';
        Chart.defaults.font.family = "'Inter', sans-serif";

        sysChartInst = new Chart(ctx, {
            type: 'line',
            data: {
                labels: sysData.labels,
                datasets: [
                    { label: 'CPU', data: sysData.cpu, borderColor: '#3b82f6', backgroundColor: 'transparent', tension: 0.5, cubicInterpolationMode: 'monotone', pointRadius: 0, yAxisID: 'y' },
                    { label: 'RAM', data: sysData.ram, borderColor: '#10b981', backgroundColor: 'transparent', tension: 0.5, cubicInterpolationMode: 'monotone', pointRadius: 0, yAxisID: 'y' },
                    { label: 'Disco', data: sysData.disk, borderColor: '#f59e0b', backgroundColor: 'transparent', tension: 0.5, cubicInterpolationMode: 'monotone', pointRadius: 0, borderDash: [5, 5], yAxisID: 'y' },
                    { label: 'RAM Disk', data: sysData.ramdisk, borderColor: '#8b5cf6', backgroundColor: 'rgba(139,92,246,0.1)', tension: 0.5, cubicInterpolationMode: 'monotone', pointRadius: 0, fill: true, yAxisID: 'y' },
                    { label: 'DL', data: sysData.dl, borderColor: '#ffffff', backgroundColor: 'rgba(255, 255, 255, 0.1)', tension: 0.5, cubicInterpolationMode: 'monotone', fill: true, pointRadius: 0, yAxisID: 'ySpeed' },
                    { label: 'UL', data: sysData.ul, borderColor: '#f43f5e', backgroundColor: 'transparent', tension: 0.5, cubicInterpolationMode: 'monotone', pointRadius: 0, yAxisID: 'ySpeed' }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                scales: {
                    x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                    y: { 
                        type: 'linear', display: true, position: 'left', min: 0, max: 100, 
                        grid: { color: 'rgba(255, 255, 255, 0.05)' }, title: { display: true, text: 'Risorse di sistema (%)', color: '#64748b' }
                    },
                    ySpeed: {
                        type: 'linear', display: true, position: 'right', min: 0,
                        grid: { drawOnChartArea: false }, title: { display: true, text: 'Rete (MB/s)', color: '#64748b' }
                    }
                },
                plugins: {
                    legend: { display: false }, // <-- SPEGNE LA LEGENDA NATIVA "BALLERINA"
                    tooltip: { mode: 'index', intersect: false }
                },
                interaction: { mode: 'nearest', axis: 'x', intersect: false }
            }
        });
    },

    async fetchSystemStats() {
        try {
            const [sysRes, netRes] = await Promise.all([
                fetch(`${API_BASE}/api/system/stats`).catch(() => null),
                fetch(`${API_BASE}/api/torrents/stats`).catch(() => null)
            ]);

            let sysInfo = { success: false, cpu: 0, ram: 0, disk: 0 };
            if (sysRes && sysRes.ok) sysInfo = await sysRes.json();

            let dlSpeedMB = 0, ulSpeedMB = 0;
            let netInfo = null; // <-- Dichiariamo la variabile qui fuori
            if (netRes && netRes.ok) {
                netInfo = await netRes.json(); // <-- Rimosso 'const'
                dlSpeedMB = (netInfo.dl_rate || 0) / 1048576; 
                ulSpeedMB = (netInfo.ul_rate || 0) / 1048576;
            }
            
            if (sysInfo.success || netRes) {
                const now = new Date();
                const timeLabel = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`;

                sysData.labels.push(timeLabel);
                sysData.cpu.push(sysInfo.cpu || 0);
                sysData.ram.push(sysInfo.ram || 0);
                sysData.disk.push(sysInfo.disk || 0);
                sysData.ramdisk.push(sysInfo.ramdisk || 0);
                sysData.dl.push(dlSpeedMB.toFixed(2));
                sysData.ul.push(ulSpeedMB.toFixed(2));

                if (sysData.labels.length > MAX_SYS_POINTS) {
                    Object.values(sysData).forEach(arr => arr.shift());
                }

               if (sysChartInst) {
                    // Aggiorna la nuova legenda HTML fissa
                    const elCpu = document.getElementById('leg-cpu');
                    if(elCpu) elCpu.textContent = `${sysInfo.cpu}%`;

                    const elRam = document.getElementById('leg-ram');
                    if(elRam) elRam.textContent = `${sysInfo.ram}% (${sysInfo.ram_mb || 0} MB)`;

                    const elDisk = document.getElementById('leg-disk');
                    if(elDisk) elDisk.textContent = `${sysInfo.disk}%`;

                    const elRamDisk = document.getElementById('leg-ramdisk');
                    if(elRamDisk) {
                        if (sysInfo.ramdisk_gb > 0 || sysInfo.ramdisk > 0 || sysInfo.ramdisk_total_gb > 0) {
                            // Qui formattiamo il testo: Usato GB / Totale GB
                            elRamDisk.textContent = `${sysInfo.ramdisk}% (${sysInfo.ramdisk_gb || 0} GB / ${sysInfo.ramdisk_total_gb || 0} GB)`;
                            elRamDisk.parentElement.style.opacity = '1';
                        } else {
                            elRamDisk.textContent = `0% (0 GB / 0 GB)`;
                            elRamDisk.parentElement.style.opacity = '0.3';
                        }
                    }

                    const elDl = document.getElementById('leg-dl');
                    if(elDl) elDl.textContent = `${dlSpeedMB.toFixed(2)} MB/s`;

                    const elUl = document.getElementById('leg-ul');
                    if(elUl) elUl.textContent = `${ulSpeedMB.toFixed(2)} MB/s`;

                    const elUptime = document.getElementById('sys-uptime');
                    if(elUptime && sysInfo.uptime) elUptime.innerHTML = `<i class="fa-solid fa-bolt"></i> ${t('Uptime Motore')}: <strong style="color:var(--text-primary);">${sysInfo.uptime}</strong>`;

                    // Popola il Traffico Totale di Sessione dal Backend Python (Persistente!)
                    if (sysInfo && sysInfo.total_dl_bytes !== undefined) {
                        const totDlGb = sysInfo.total_dl_bytes / 1073741824;
                        const totUlGb = sysInfo.total_ul_bytes / 1073741824;
                        
                        const elTotDl = document.getElementById('leg-tot-dl');
                        const elTotUl = document.getElementById('leg-tot-ul');
                        
                        // Formattazione intelligente: mostra MB se piccolo, GB se grande
                        if (elTotDl) elTotDl.textContent = totDlGb < 1 ? `${(sysInfo.total_dl_bytes / 1048576).toFixed(2)} MB` : `${totDlGb.toFixed(2)} GB`;
                        if (elTotUl) elTotUl.textContent = totUlGb < 1 ? `${(sysInfo.total_ul_bytes / 1048576).toFixed(2)} MB` : `${totUlGb.toFixed(2)} GB`;
                    }

                    if (currentView === 'charts') sysChartInst.update('none');
                }
            }
        } catch (e) {
            console.error('Errore stats:', e);
        }
    },
    
    // ========================================================================
    // SERIES
    // ========================================================================
    async loadSeries(searchQuery = '') {
        if (this._seriesAbortCtrl) this._seriesAbortCtrl.abort();
        this._seriesAbortCtrl = new AbortController();
        const signal = this._seriesAbortCtrl.signal;
        try {
            const configRes = await fetch(`${API_BASE}/api/config`, { signal });
            const config = await configRes.json();
            let series = config.series || [];
            series = series.slice().sort((a,b) => (a.name||'').toLowerCase().localeCompare((b.name||'').toLowerCase()));
            
            if (searchQuery) series = series.filter(s => s.name.toLowerCase().includes(searchQuery.toLowerCase()));
            
            const dbRes = await fetch(`${API_BASE}/api/series`, { signal });
            const dbSeries = await dbRes.json();
            const dbMap = {};
            dbSeries.forEach(s => { dbMap[s.name.toLowerCase()] = s; });
            
            const prevSelected = new Set([...document.querySelectorAll('.series-checkbox:checked')].map(c => c.dataset.name));

            const container = document.getElementById('series-list');
            container.className = 'data-table series-table';
            
            let html = `
                <div id="series-bulk-bar" style="display:none; align-items:center; gap:0.75rem; padding:0.75rem 1.25rem; background:rgba(37,99,235,0.08); border-bottom:1px solid var(--border); flex-wrap: wrap;">
                    <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;font-weight:600;">
                        <input type="checkbox" id="series-select-all" style="width:1.1rem;height:1.1rem;cursor:pointer;" onchange="app.toggleSelectAllSeries(this.checked)">
                        <span>Tutto</span>
                    </label>
                    <span id="series-selected-count" style="color:var(--text-secondary); font-size:0.85rem;"></span>
                    
                    <div style="flex:1;"></div>
                    
                    <button class="btn btn-small btn-success" onclick="app.bulkSeriesAction('enable')" title="Abilita Selezionate"><i class="fa-solid fa-check"></i> Abilita</button>
                    <button class="btn btn-small btn-warning" onclick="app.bulkSeriesAction('disable')" title="Disabilita Selezionate"><i class="fa-solid fa-ban"></i> Disabilita</button>
                    
                    <div style="width:1px; height:24px; background:var(--border); margin:0 0.5rem;"></div>
                    
                    <select id="bulk-series-quality" style="padding:0.4rem 0.75rem; border-radius:0.4rem; border:1px solid var(--border); background:var(--bg-input); color:var(--text-primary); font-size:0.85rem; cursor:pointer;">
                        <option value="720p">720p</option>
                        <option value="720p+">720p+</option>
                        <option value="720p-1080p">720p-1080p</option>
                        <option value="1080p+">1080p+</option>
                        <option value="2160p">4K (2160p)</option>
                    </select>
                    <button class="btn btn-small btn-primary" onclick="app.bulkSeriesAction('quality')" data-i18n-title="Applica Qualità" title="Applica Qualità"><i class="fa-solid fa-video"></i></button>

                    <div style="width:1px; height:24px; background:var(--border); margin:0 0.5rem;"></div>

                    <select id="bulk-series-language" style="padding:0.4rem 0.75rem; border-radius:0.4rem; border:1px solid var(--border); background:var(--bg-input); color:var(--text-primary); font-size:0.85rem; cursor:pointer;">
                    </select>
                    <button class="btn btn-small btn-primary" onclick="app.bulkSeriesAction('language')" title="Applica Lingua"><i class="fa-solid fa-language"></i></button>
                    
                    <div style="width:1px; height:24px; background:var(--border); margin:0 0.5rem;"></div>
                    
                    <button class="btn btn-small btn-danger" onclick="app.bulkSeriesAction('delete')" title="Rimuovi Selezionate"><i class="fa-solid fa-trash"></i></button>
                </div>
                <div class="table-row table-header">
                    <div>${t('Serie TV')} / ${t('Archivio')}</div><div>${t('Qualità')}</div><div>${t('Lingua')}</div>
                    <div class="col-center">Ep.</div><div class="col-center">${t('Ultimo')}</div><div style="text-align:right">${t('Stato')} / ${t('AZIONI')}</div>
                </div>            
            `;
            
            series.forEach(s => {
                const dbData = dbMap[s.name.toLowerCase()];
                const episodesCount = dbData ? (dbData.episodes_count || 0) : 0;
                const lastEp = dbData && dbData.last_season ? `S${String(dbData.last_season).padStart(2,'0')}E${String(dbData.last_episode).padStart(2,'0')}` : '-';
                const seriesId = dbData ? dbData.id : 0;
                
                const enabledIcon = s.enabled ? '<i class="fa-solid fa-check"></i>' : '<i class="fa-solid fa-xmark"></i>';
                const enabledClass = s.enabled ? 'badge-success' : 'badge-secondary';
                const arch = this.escapeHtml(s.archive_path || '');
                const lang = (s.language || app._primaryLang || 'ita').toUpperCase();
                const wasSel = prevSelected.has(s.name);

                html += `
                    <div class="table-row">
                        <div class="series-and-archive" style="display:flex; flex-direction:row; align-items:center; gap:0.5rem;">
                            <input type="checkbox" class="series-checkbox" data-name="${this.escapeHtml(s.name)}" style="width:1rem;height:1rem;cursor:pointer;flex-shrink:0;" ${wasSel ? 'checked' : ''} onchange="app._onSeriesCheckChange()">
                            <div style="display:flex; flex-direction:column; min-width:0;">
                                <div class="series-name-row">
                                    <strong title="${this.escapeHtml(s.name)}">${this.escapeHtml(s.name)}</strong>
                                    ${arch ? `<span class="series-archive-inline" title="${arch}"><i class="fa-regular fa-folder-open"></i>${arch}</span>` : ''}
                                </div>
                            </div>
                        </div>
                        <div><span class="badge badge-info">${this.escapeHtml(s.quality||'')}</span></div>
                        <div><small>${lang}</small></div>
                        <div class="col-center">${episodesCount}</div>
                        <div class="col-center"><small>${lastEp}</small></div>
                        <div class="table-actions series-actions-desktop">
                            <span class="badge ${enabledClass} series-status-badge" data-series-name="${this.escapeHtml(s.name)}" title="${s.enabled ? 'In corso / Monitorata' : 'Disabilitata'}">${enabledIcon}</span>
                            <button class="btn btn-small btn-secondary" title="Dettagli Serie" onclick="app.showEpisodes(${seriesId}, '${this.escapeJs(s.name)}')"><i class="fa-solid fa-list-ul"></i></button>
                            <button class="btn btn-small btn-secondary" title="Scansione Cartella" onclick="app.scanSeriesPath(${seriesId}, '${this.escapeJs(s.name)}')"><i class="fa-solid fa-folder-tree"></i></button>
                            <button class="btn btn-small btn-danger" title="Elimina" onclick="app.deleteSeriesFromConfig('${this.escapeJs(s.name)}', ${seriesId})"><i class="fa-solid fa-trash-can"></i></button>
                        </div>
                        <div class="series-kebab-wrapper">
                            <span class="badge ${enabledClass} series-status-badge" data-series-name="${this.escapeHtml(s.name)}" title="${s.enabled ? 'In corso / Monitorata' : 'Disabilitata'}">${enabledIcon}</span>
                            <button class="btn btn-small btn-secondary series-kebab-btn" title="Azioni" onclick="app.toggleSeriesKebab(this, ${seriesId}, '${this.escapeJs(s.name)}')"><i class="fa-solid fa-ellipsis-vertical"></i></button>
                        </div>
                    </div>
                `;
            });
            container.innerHTML = html;
            this._onSeriesCheckChange();
            this.checkSeriesCompleteness();
        } catch (err) { console.error(err); }
    },
    
    _onSeriesCheckChange() {
        const all = document.querySelectorAll('.series-checkbox');
        const checked = document.querySelectorAll('.series-checkbox:checked');
        const selAll = document.getElementById('series-select-all');
        if (selAll) {
            selAll.indeterminate = checked.length > 0 && checked.length < all.length;
            selAll.checked = all.length > 0 && checked.length === all.length;
        }
        const countEl = document.getElementById('series-selected-count');
        if (countEl) countEl.textContent = checked.length > 0 ? `${checked.length} ${t('selezionate')}` : '';
        showIf(document.getElementById('series-bulk-bar'), checked.length > 0);
    },

    toggleSelectAllSeries(checked) {
        document.querySelectorAll('.series-checkbox').forEach(cb => cb.checked = checked);
        this._onSeriesCheckChange();
    },
    
    async checkSeriesCompleteness() {
        try {
            const res = await fetch(`${API_BASE}/api/series/completeness`);
            const completeness = await res.json();

            document.querySelectorAll('.series-status-badge').forEach(badge => {
                const name = badge.dataset.seriesName;
                const entry = completeness[name];
                if (!entry) return;
                if (entry.is_completed) {
                    badge.classList.remove('badge-success', 'badge-warning');
                    badge.style.background = 'linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(52, 211, 153, 0.3))';
                    badge.style.color = '#34d399';
                    badge.style.borderColor = 'rgba(52, 211, 153, 0.5)';
                    badge.style.boxShadow = '0 0 12px rgba(52, 211, 153, 0.25)';
                    badge.innerHTML = '<i class="fa-solid fa-check-double"></i>';
                    badge.title = 'Serie Completata: terminata su TMDB e collezione al 100%!';
                } else if (entry.is_ended) {
                    badge.classList.remove('badge-success', 'badge-warning');
                    badge.style.background = 'rgba(245,158,11,.15)';
                    badge.style.color = '#f59e0b';
                    badge.style.borderColor = 'rgba(245,158,11,.4)';
                    badge.style.boxShadow = '0 0 8px rgba(245,158,11,.2)';
                    badge.innerHTML = '<i class="fa-solid fa-flag-checkered"></i>';
                    badge.title = 'Serie terminata su TMDB — episodi mancanti';
                }
            });
        } catch(e) { console.error('Errore durante il controllo completezza', e); }
    },

    async bulkSeriesAction(action) {
        const names = [...document.querySelectorAll('.series-checkbox:checked')].map(cb => cb.dataset.name);
        if (!names.length) return;
        
        if (action === 'delete' && !confirm(`${t('Vuoi davvero eliminare')} ${names.length} ${t('serie TV?')}`)) return;

        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            
            if (action === 'delete') {
                config.series = config.series.filter(s => !names.includes(s.name));
            } else if (action === 'quality') {
                const newQuality = document.getElementById('bulk-series-quality').value;
                config.series.forEach(s => {
                    if (names.includes(s.name)) s.quality = newQuality;
                });
            } else if (action === 'language') {
                let newLang = document.getElementById('bulk-series-language').value;
                if (newLang === 'custom') {
                    newLang = prompt(t('Inserisci la lingua personalizzata (es. ita,eng):'), '');
                    if (!newLang) return; 
                }
                config.series.forEach(s => {
                    if (names.includes(s.name)) s.language = newLang.toLowerCase().trim();
                });
            } else {
                config.series.forEach(s => {
                    if (names.includes(s.name)) s.enabled = (action === 'enable');
                });
            }
            
            await fetch(`${API_BASE}/api/config/series`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ series: config.series })
            });
            
            this.showToast(`${t('Action applied to')} ${names.length} serie!`, 'success');
            this.loadSeries(); 
        } catch (e) {
            this.showToast(t('Error during bulk update'), 'error');
        }
    },

    async editSeriesPath(id, name, currentPath) {
        const newPath = prompt(`${t('Modifica percorso per')} "${name}":`, currentPath);
        if (newPath === null || newPath === currentPath) return;

        if (id && id !== 0) {
            await fetch(`${API_BASE}/api/series/${id}/path`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ archive_path: newPath })
            });
        } else {
            try {
                const res = await fetch(`${API_BASE}/api/config`);
                const config = await res.json();
                const seriesIndex = config.series.findIndex(s => s.name === name);
                
                if (seriesIndex !== -1) {
                    config.series[seriesIndex].archive_path = newPath;
                    await fetch(`${API_BASE}/api/config/series`, {
                        method: 'POST', headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({ series: config.series })
                    });
                    this.showToast(t('Path updated in config'), 'success');
                }
            } catch (e) { console.error(e); }
        }
        this.loadSeries();
    },

    async scanSeriesPath(id, name) {
        if(confirm(`${t('Scansionare la cartella di')} "${name}"?`)) {
            try {
                if (!id || id === 0) {
                    this.showToast(t('Running general archive scan...'), 'info');
                    await fetch(`${API_BASE}/api/scan-all-archives`, {method:'POST'});
                    await this.loadSeries();
                    this.showToast(t('Scan completed'), 'success');
                } else {
                    await fetch(`${API_BASE}/api/series/${id}/scan-archive`, {method:'POST'}); 
                    this.showEpisodes(id, name);
                    await this.loadSeries();
                }
            } catch(e) {
                this.showToast(t('Scan error'), 'error');
            }
        } 
    },

    // ========================================================================
    // MODIFICA SERIE
    // ========================================================================
    async editSeries(seriesId, seriesName) {
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            
            const series = config.series.find(s => s.name === seriesName);
            if (!series) {
                this.showToast(t('Series not found in config'), 'error');
                return;
            }
            
            const modal = document.getElementById('edit-series-modal');
            if (!modal) return;

            const titleEl = document.getElementById('edit-series-title');
            if (titleEl) {
                titleEl.innerHTML = `<i class="fa-solid fa-pen"></i> Modifica: ${this.escapeHtml(seriesName)}`;
            }
            
            const setFieldValue = (id, value) => {
                const field = document.getElementById(id);
                if (!field) return false;
                if (field.type === 'checkbox') field.checked = value;
                else field.value = value || '';
                return true;
            };
            
            if (!setFieldValue('edit-series-id', seriesId || 0)) return;
            if (!setFieldValue('edit-series-original-name', seriesName)) return;
            if (!setFieldValue('edit-series-name', series.name || '')) return;
            if (!setFieldValue('edit-series-seasons', series.seasons || '1+')) return;
            if (!setFieldValue('edit-series-language', (series.language || app._primaryLang || 'ita').toLowerCase())) return;
            if (!setFieldValue('edit-series-archive-path', series.archive_path || '')) return;
            if (!setFieldValue('edit-series-aliases', (series.aliases || []).join(', '))) return;
            if (!setFieldValue('edit-series-quality', series.quality || '720p-1080p')) return;
            
            const isEnabled = series.enabled === true || series.enabled === 'yes';
            if (!setFieldValue('edit-series-enabled', isEnabled)) return;

            // Campi aggiuntivi — SEMPRE resettati all'apertura per evitare
            // che valori di una serie precedente rimangano nel modal
            setFieldValue('edit-series-tmdb-id',  series.tmdb_id  || '');
            setFieldValue('edit-series-timeframe', series.timeframe != null ? series.timeframe : 0);
            setFieldValue('edit-series-season-subfolders', !!series.season_subfolders);

            // Subtitle preset
            const subPresetEl  = document.getElementById('edit-series-subtitle-preset');
            const subCustomEl  = document.getElementById('edit-series-subtitle-custom');
            const subVal       = series.subtitle || '';
            const subPresets   = ['', ...EXTTO_LANGUAGES.map(l => l.code), ...EXTTO_LANGUAGES.filter(l=>l.code!=='eng').map(l=>`${l.code},eng`), 'custom'];
            if (subPresetEl) {
                subPresetEl.value = subPresets.includes(subVal) ? subVal : 'custom';
                if (subPresetEl.value === 'custom' && subCustomEl) subCustomEl.value = subVal;
            }

            // Reset risultati TMDB rimasti da ricerca precedente
            const tmdbResults = document.getElementById('tmdb-results-edit-series-name');
            if (tmdbResults) tmdbResults.style.display = 'none';

            modal.classList.add('active');
            
        } catch (err) { console.error(err); }
    },

    // Chiamata dal pulsante "Modifica Serie" dentro la view Dettagli Serie (extto)
    editSeriesFromDetail() {
        const seriesId   = this.currentSeriesId;
        const seriesName = this.currentSeriesName;
        if (!seriesName) {
            this.showToast(t('No series loaded'), 'error');
            return;
        }
        this.editSeries(seriesId, seriesName);
    },

    // Chiamata dal pulsante "Modifica Film" dentro la view Dettagli Film (radarr)
    editMovieFromDetail() {
        const movieName = this.currentMovieName;
        if (!movieName) {
            this.showToast(t('No movies loaded'), 'error');
            return;
        }
        this.editMovie(movieName);
    },

    // ------------------------------------------------------------------
    // PANNELLI MODIFICA INLINE
    // ------------------------------------------------------------------

    toggleSeriesEditPanel() {
        const panel = document.getElementById('extto-edit-panel');
        const btn   = document.getElementById('extto-edit-btn');
        if (!panel) return;
        const isOpen = panel.style.display === 'block';
        if (isOpen) {
            panel.style.display = 'none';
            if (btn) { btn.classList.remove('btn-primary'); btn.classList.add('btn-secondary'); }
        } else {
            panel.style.display = 'block';
            if (btn) { btn.classList.remove('btn-secondary'); btn.classList.add('btn-primary'); }
            this._populateSeriesInlinePanel();
            panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    },

    async _populateSeriesInlinePanel() {
        const seriesId   = this.currentSeriesId;
        const seriesName = this.currentSeriesName;
        if (!seriesName) return;  // seriesId può essere 0 per serie appena aggiunte
        try {
            const res    = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            const series = config.series.find(s => s.name === seriesName);
            if (!series) { this.showToast(t('Series not found in config'), 'error'); return; }

            const set = (id, val) => {
                const el = document.getElementById(id);
                if (!el) return;
                if (el.type === 'checkbox') el.checked = val;
                else el.value = val || '';
            };
            set('extto-edit-id',           seriesId);
            set('extto-edit-original-name', seriesName);
            set('extto-edit-name',          series.name || '');
            set('extto-edit-seasons',       series.seasons || '1+');
            set('extto-edit-quality',       series.quality || '720p-1080p');
            set('extto-edit-timeframe',     series.timeframe || 0);
            set('extto-edit-archive-path',  series.archive_path || '');
            set('extto-edit-aliases',       (series.aliases || []).join(', '));
            set('extto-edit-enabled',       series.enabled === true || series.enabled === 'yes');

            // Lingua
            const lang = (series.language || app._primaryLang || 'ita').toLowerCase();
            const langSel = document.getElementById('extto-edit-language');
            if (langSel) {
                const knownLangs = EXTTO_LANGUAGES.map(l => l.code);
                if (knownLangs.includes(lang)) {
                    langSel.value = lang;
                    hideEl('extto-edit-language-custom');
                } else {
                    langSel.value = 'custom';
                    const customEl = document.getElementById('extto-edit-language-custom');
                    if (customEl) { showEl(customEl); customEl.value = lang; }
                }
            }

            // Sottotitoli
            _populateSubtitleWidget(
                'extto-edit-subtitle-preset',
                'extto-edit-subtitle-custom',
                series.subtitle || ''
            );
        } catch (err) { console.error(err); }
    },

    async saveSeriesInline(event) {
        event.preventDefault();
        const seriesId      = document.getElementById('extto-edit-id').value;
        const originalName  = document.getElementById('extto-edit-original-name').value;
        const langVal       = document.getElementById('extto-edit-language').value;
        const langCustom    = document.getElementById('extto-edit-language-custom').value;
        const updatedSeries = {
            name:         document.getElementById('extto-edit-name').value.trim(),
            seasons:      document.getElementById('extto-edit-seasons').value.trim(),
            quality:      document.getElementById('extto-edit-quality').value,
            language:     langVal === 'custom' ? langCustom.trim() : langVal,
            timeframe:    parseInt(document.getElementById('extto-edit-timeframe').value) || 0,
            archive_path: document.getElementById('extto-edit-archive-path').value.trim(),
            aliases:      document.getElementById('extto-edit-aliases').value.split(',').map(a => a.trim()).filter(a => a),
            enabled:      document.getElementById('extto-edit-enabled').checked,
            tmdb_id:      document.getElementById('extto-edit-tmdb-id')?.value || '',
            subtitle: (() => {
                const p = document.getElementById('extto-edit-subtitle-preset')?.value || '';
                return p === 'custom' ? (document.getElementById('extto-edit-subtitle-custom')?.value.trim() || '') : p;
            })(),
        };
        if (!updatedSeries.name || !updatedSeries.seasons) {
            this.showToast(t('Name and seasons are required'), 'error');
            return;
        }
        try {
            const saveRes = await fetch(`${API_BASE}/api/series/${seriesId}/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedSeries)
            });
            const saveData = await saveRes.json();
            if (saveData.success) {
                this.showToast(t('Series updated!'), 'success');
                this.toggleSeriesEditPanel();
                if (seriesId && seriesId !== '0' && updatedSeries.name !== originalName) {
                    try { await fetch(`${API_BASE}/api/series/${seriesId}/rename`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_name: updatedSeries.name }) }); } catch(e) {}
                }
                // Ricarica dettagli per aggiornare ID TMDB e testata
                this.currentSeriesName = updatedSeries.name;
                await this.showEpisodes(seriesId, updatedSeries.name);
            } else { this.showToast(t('Errore salvataggio') + ': ' + (saveData.error || ''), 'error'); }
        } catch(err) { this.showToast(t('Save error'), 'error'); }
    },

    toggleMovieEditPanel() {
        const panel = document.getElementById('radarr-edit-panel');
        const btn   = document.getElementById('radarr-edit-btn');
        if (!panel) return;
        const isOpen = panel.style.display === 'block';
        if (isOpen) {
            panel.style.display = 'none';
            if (btn) { btn.classList.remove('btn-primary'); btn.classList.add('btn-secondary'); }
        } else {
            panel.style.display = 'block';
            if (btn) { btn.classList.remove('btn-secondary'); btn.classList.add('btn-primary'); }
            this._populateMovieInlinePanel();
            panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    },

    async _populateMovieInlinePanel() {
        const movieName = this.currentMovieName;
        if (!movieName) return;
        try {
            const res    = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            const movie  = config.movies.find(m => m.name === movieName);
            if (!movie) { this.showToast(t('Movie not found'), 'error'); return; }

            document.getElementById('radarr-edit-original-name').value = movieName;
            document.getElementById('radarr-edit-name').value          = movie.name || '';
            document.getElementById('radarr-edit-year').value          = movie.year || '';
            document.getElementById('radarr-edit-quality').value       = movie.quality || '720p-1080p';
            document.getElementById('radarr-edit-enabled').checked     = movie.enabled === true || movie.enabled === 'yes';

            const lang    = (movie.language || app._primaryLang || '').toLowerCase();
            const langSel = document.getElementById('radarr-edit-language');
            if (langSel) {
                const knownLangs = EXTTO_LANGUAGES.map(l => l.code);
                if (knownLangs.includes(lang)) {
                    langSel.value = lang;
                    hideEl('radarr-edit-language-custom');
                } else {
                    langSel.value = 'custom';
                    const customEl = document.getElementById('radarr-edit-language-custom');
                    if (customEl) { showEl(customEl); customEl.value = lang; }
                }
            }

            // Sottotitoli
            _populateSubtitleWidget(
                'radarr-edit-subtitle-preset',
                'radarr-edit-subtitle-custom',
                movie.subtitle || ''
            );
        } catch(err) { console.error(err); }
    },

    async saveMovieInline(event) {
        event.preventDefault();
        const originalName = document.getElementById('radarr-edit-original-name').value;
        const langVal      = document.getElementById('radarr-edit-language').value;
        const langCustom   = document.getElementById('radarr-edit-language-custom').value;
        const updatedMovie = {
            name:     document.getElementById('radarr-edit-name').value.trim(),
            year:     document.getElementById('radarr-edit-year').value.trim(),
            quality:  document.getElementById('radarr-edit-quality').value,
            language: langVal === 'custom' ? langCustom.trim() : langVal,
            enabled:  document.getElementById('radarr-edit-enabled').checked,
            subtitle: (() => {
                const p = document.getElementById('radarr-edit-subtitle-preset')?.value || '';
                return p === 'custom' ? (document.getElementById('radarr-edit-subtitle-custom')?.value.trim() || '') : p;
            })(),
        };
        if (!updatedMovie.name) { this.showToast(t('Name is required'), 'error'); return; }
        try {
            const res    = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            const idx    = config.movies.findIndex(m => m.name === originalName);
            if (idx === -1) { this.showToast(t('Movie not found'), 'error'); return; }
            config.movies[idx] = { ...config.movies[idx], ...updatedMovie };
            const saveRes = await fetch(`${API_BASE}/api/config/movies`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ movies: config.movies })
            });
            if (saveRes.ok) {
                this.showToast(t('Film aggiornato!'), 'success');
                this.toggleMovieEditPanel();
                this.currentMovieName = updatedMovie.name;
                this.showMovieDetails(updatedMovie.name);
            } else { this.showToast(t('Save error'), 'error'); }
        } catch(err) { this.showToast(t('Save error'), 'error'); }
    },

    async saveAllConfig() {
        this.showToast(t('Saving all configuration...'), 'info');
        
        const s = this._configData?.settings || {};
        const v = id => document.getElementById(id)?.value ?? '';
        const cb = id => document.getElementById(id)?.checked ?? false;

        const clients = ['libtorrent', 'qbittorrent', 'transmission', 'aria2'];
        clients.forEach(c => s[`${c}_enabled`] = 'no');
        const activeBtn = document.querySelector('.client-btn.active');
        const activeClient = activeBtn ? activeBtn.getAttribute('onclick').match(/'([^']+)'/)[1] : 'libtorrent';
        s[`${activeClient}_enabled`] = 'yes';

        const allDays = [0,1,2,3,4,5,6];
        const checkedDays = allDays.filter(d => cb(`sched-day-${d}`));
        const schedDaysStr = checkedDays.join(',');

        const tg = id => this._getToggle(id);
        Object.assign(s, {
            libtorrent_dir: v('lt-dir'),
            libtorrent_sequential: tg('lt-sequential'),
            libtorrent_temp_dir: v('lt-temp-dir'),
            libtorrent_ramdisk_enabled:      tg('lt-ramdisk-enabled'),
            libtorrent_ramdisk_dir:          v('lt-ramdisk-dir'),
            libtorrent_ramdisk_threshold_gb: v('lt-ramdisk-threshold') || '3.5',
            libtorrent_ramdisk_margin_gb:    v('lt-ramdisk-margin')    || '0.5',
            libtorrent_interface: v('lt-interface'),
            libtorrent_paused: tg('lt-paused'),
            libtorrent_port_min: v('lt-port-min'),
            libtorrent_port_max: v('lt-port-max'),
            libtorrent_dl_limit: String(parseInt(v('lt-dl-limit'))||0),
            libtorrent_ul_limit: String(parseInt(v('lt-ul-limit'))||0),
            libtorrent_sched_enabled: tg('lt-sched-enabled'),
            libtorrent_sched_start: v('lt-sched-start'),
            libtorrent_sched_end: v('lt-sched-end'),
            libtorrent_sched_dl_limit: String(parseInt(v('lt-sched-dl'))||0),
            libtorrent_sched_ul_limit: String(parseInt(v('lt-sched-ul'))||0),
            libtorrent_sched_days: schedDaysStr,
            libtorrent_connections_limit: v('lt-conn-limit'),
            libtorrent_upload_slots: v('lt-upload-slots'),
            libtorrent_stop_at_ratio: tg('lt-stop-at-ratio'),
            libtorrent_seed_ratio: v('lt-seed-ratio'),
            libtorrent_seed_time_days: v('lt-seed-time-days'),
            libtorrent_active_downloads: v('lt-active-downloads'),
            libtorrent_active_seeds: v('lt-active-seeds'),
            libtorrent_active_limit: v('lt-active-limit'),
            libtorrent_slow_dl_threshold: v('lt-slow-dl'),
            libtorrent_slow_ul_threshold: v('lt-slow-ul'),
            libtorrent_preallocate: tg('lt-preallocate'),
            libtorrent_disable_cow:        tg('lt-disable-cow'),
            libtorrent_cache_size:         v('lt-cache-size')     || '0',
            libtorrent_max_queued_disk_mb: v('lt-queue-disk-mb')  || '4',
            libtorrent_send_buffer_kb:     v('lt-send-buffer-kb') || '512',
            libtorrent_max_peer_list:      v('lt-max-peer-list')  || '200',
            libtorrent_incomplete_ext: v('lt-incomplete-ext'),
            libtorrent_encryption: v('lt-encryption'),
            libtorrent_dht: tg('lt-dht'),
            libtorrent_pex: tg('lt-pex'),
            libtorrent_lsd: tg('lt-lsd'),
            libtorrent_upnp: tg('lt-upnp'),
            libtorrent_natpmp: tg('lt-natpmp'),
            libtorrent_announce_to_all: tg('lt-announce-all'),
            libtorrent_proxy_type: v('lt-proxy-type'),
            libtorrent_proxy_host: v('lt-proxy-host'),
            libtorrent_proxy_port: v('lt-proxy-port'),
            libtorrent_proxy_username: v('lt-proxy-user'),
            libtorrent_proxy_password: v('lt-proxy-pass'),
            libtorrent_extra_trackers: v('lt-extra-trackers'),
            libtorrent_ipfilter_url: v('lt-ipfilter-url'),
            libtorrent_ipfilter_autoupdate: tg('lt-ipfilter-autoupdate'),
            refresh_interval: v('setting-refresh_interval')
        });

        Object.assign(s, {
            qbittorrent_url: v('qbt-url'), qbittorrent_username: v('qbt-user'), qbittorrent_password: v('qbt-pass'), qbittorrent_category: v('qbt-category'), qbittorrent_paused: tg('qbt-paused'),
            transmission_url: v('tr-url'), transmission_username: v('tr-user'), transmission_password: v('tr-pass'), transmission_paused: tg('tr-paused'),
            aria2_rpc_url: v('ar-rpc-url'), aria2_rpc_secret: v('ar-secret'), aria2_dir: v('ar-dir'), aria2c_path: v('ar-path'),
            aria2_max_connection: v('ar-max-conn'), aria2_split: v('ar-split'), aria2_dl_limit: v('ar-dl-limit'), aria2_ul_limit: v('ar-ul-limit')
        });

        document.querySelectorAll('[id^="setting-"]').forEach(i => {
            s[i.id.replace('setting-', '')] = i.value;
        });

        s.url = v('urls-list').split('\n').filter(x=>x.trim());
        s.blacklist = v('blacklist-list').split('\n').filter(x=>x.trim());
        s.content_filter = v('content-filter-list').split('\n').filter(x=>x.trim());
        s.wantedlist = v('wantedlist-list').split('\n').filter(x=>x.trim());
        s.custom_score = v('customscore-list').split('\n').filter(x=>x.trim());
        s.auto_remove_completed = document.getElementById('auto-remove-completed')?.checked ? 'yes' : 'no';

        // Motori di ricerca web — legge i toggle attuali per non sovrascrivere con il valore in cache
        {
            const _ws = [];
            const _cb = id => document.getElementById(id)?.checked;
            if (_cb('websearch-bitsearch'))    _ws.push('bitsearch');
            if (_cb('websearch-tpb'))          _ws.push('tpb');
            if (_cb('websearch-knaben'))       _ws.push('knaben');
            if (_cb('websearch-btdig'))        _ws.push('btdig');
            if (_cb('websearch-limetorrents')) _ws.push('limetorrents');
            if (_cb('websearch-torrentz2'))    _ws.push('torrentz2');
            if (_cb('websearch-torrentscsv'))  _ws.push('torrentscsv');
            // Aggiorna solo se la tab Integrazioni è stata caricata (almeno un elemento esiste nel DOM)
            if (document.getElementById('websearch-bitsearch')) s.websearch_engines = _ws.join(',');
        }

        // Salva la lingua TMDB scegliendo tra la tendina o il campo di testo
        const tmdbSelect = document.getElementById('tmdb_lang_select');
        if (tmdbSelect) {
            const tmdbCustom = document.getElementById('tmdb_lang_custom');
            s.tmdb_language = tmdbSelect.value === 'custom'
                ? (tmdbCustom?.value.trim() || 'it-IT')
                : tmdbSelect.value;
        }

        try {
            // Verifica porte prima di salvare
            const newWebPort    = parseInt(s.web_port    || v('setting-web_port'))    || 0;
            const newEnginePort = parseInt(s.engine_port || v('setting-engine_port')) || 0;
            const currSettings  = this._configData?.settings || {};
            const currWeb    = parseInt(currSettings.web_port    || 5000);
            const currEngine = parseInt(currSettings.engine_port || 8889);

            if ((newWebPort && newWebPort !== currWeb) || (newEnginePort && newEnginePort !== currEngine)) {
                const portCheck = await fetch(`${API_BASE}/api/config/check-ports`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        web_port:          newWebPort,
                        engine_port:       newEnginePort,
                        current_web_port:  currWeb,
                        current_engine_port: currEngine,
                    })
                });
                const portResult = await portCheck.json();
                if (!portResult.ok && portResult.conflicts?.length > 0) {
                    const msgs = portResult.conflicts.map(c => {
                        const who = c.process && c.process !== 'sconosciuto'
                            ? ` (in uso da: ${c.process}${c.pid ? ` PID ${c.pid}` : ''})`
                            : '';
                        const role = c.role === 'web_port' ? 'UI Web' : 'Motore';
                        return `• Porta ${c.port} (${role})${who}`;
                    }).join('\n');
                    this.showToast(t('Ports already in use — save cancelled'), 'error');
                    alert(`⚠️ ${t('Porta non aperta — controlla firewall o abilita UPnP')}:\n\n${msgs}`);
                    return;
                }
            }

            await fetch(`${API_BASE}/api/config/settings`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({settings: s})
            });
            
            if (activeClient === 'libtorrent') {
                await fetch(`${API_BASE}/api/torrents/apply_settings`, {method:'POST'}).catch(()=>{});
            }
            
            // Salva anche i punteggi
            await this.saveScoresSettings();
            
            // Salva le regole tag → cartella
            await this.saveTagDirRules();
            
            this.showToast(t('All configuration saved successfully!'), 'success');
            // Improvement 6: clear dirty indicators for all config tabs after save
            ['settings','settings-advanced','scores','notifications','advanced','urls','integrazioni'].forEach(t2 => this.clearDirty(t2));
        } catch(e) {
            this.showToast(t('Error during save.'), 'error');
        }
    },

    async saveSeriesChanges(event) {
        event.preventDefault();
        
        const seriesId = document.getElementById('edit-series-id').value;
        const originalName = document.getElementById('edit-series-original-name').value;
        
        const updatedSeries = {
            name: document.getElementById('edit-series-name').value.trim(),
            seasons: document.getElementById('edit-series-seasons').value.trim(),
            language: (() => { const s = document.getElementById('edit-series-language'); return s.value === 'custom' ? (document.getElementById('edit-series-language-custom')?.value.trim() || '') : s.value; })(),
            quality: document.getElementById('edit-series-quality').value,
            enabled: document.getElementById('edit-series-enabled').checked,
            archive_path: document.getElementById('edit-series-archive-path').value.trim(),
            timeframe: parseInt(document.getElementById('edit-series-timeframe').value) || 0,
            aliases: document.getElementById('edit-series-aliases').value.split(',').map(a => a.trim()).filter(a => a),
            tmdb_id: document.getElementById('edit-series-tmdb-id')?.value || '',
            season_subfolders: document.getElementById('edit-series-season-subfolders')?.checked || false,
            subtitle: (() => {
                const p = document.getElementById('edit-series-subtitle-preset')?.value || '';
                return p === 'custom' ? (document.getElementById('edit-series-subtitle-custom')?.value.trim() || '') : p;
            })(),
        };
        
        if (!updatedSeries.name || !updatedSeries.seasons) {
            this.showToast(t('Name and seasons are required'), 'error');
            return;
        }
        
        try {
            // Usa endpoint diretto per ID — evita roundtrip che può perdere aliases e altri campi
            const saveRes = await fetch(`${API_BASE}/api/series/${seriesId}/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedSeries)
            });
            const saveData = await saveRes.json();

            if (saveData.success) {
                this.showToast(t('Series updated successfully!'), 'success');
                this.closeModal('edit-series-modal');

                // Rinomina nel DB operativo se il nome è cambiato
                if (seriesId && seriesId !== '0' && updatedSeries.name !== originalName) {
                    try {
                        await fetch(`${API_BASE}/api/series/${seriesId}/rename`, {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ new_name: updatedSeries.name })
                        });
                    } catch (e) { console.warn('series rename:', e); }
                }

                // Ricarica sempre la view per aggiornare ID TMDB, aliases e altri campi in testata
                if (currentView === 'series') this.loadSeries();
                else this.showEpisodes(seriesId, updatedSeries.name || originalName);
            } else {
                this.showToast(t('Errore salvataggio') + ': ' + (saveData.error || ''), 'error');
            }
        } catch (err) {
            this.showToast(t('Error during save'), 'error');
        }
    },

    // ========================================================================
    // EPISODES
    // ========================================================================
    // ========================================================================
    // EXTTO STYLE VIEW
    // ========================================================================

    /** Wrapper usato da calendario e wanted-list: ricava il nome dalla lista serie cached. */
    async loadExtToDetails(seriesId) {
        const cfg = this._configData;
        const series = cfg?.series || [];
        const match = series.find(s => String(s.tmdb_id) === String(seriesId) || String(s.id) === String(seriesId));
        const name = match?.name || `Serie #${seriesId}`;
        return this.showEpisodes(seriesId, name);
    },

    async showEpisodes(seriesId, seriesName) {
        // Nascondiamo tutto e mostriamo la vista EXTTO
        this.switchView('extto');
        
        document.getElementById('extto-title').textContent = seriesName;
        document.getElementById('extto-plot').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Analisi file e download metadati in corso...';
        document.getElementById('extto-seasons').innerHTML = '';
        
        this.currentSeriesId = seriesId;
        this.currentSeriesName = seriesName;

        // Chiudi il pannello modifica se era aperto da una serie precedente
        const editPanel = document.getElementById('extto-edit-panel');
        if (editPanel) editPanel.style.display = 'none';
        const editBtn = document.getElementById('extto-edit-btn');
        if (editBtn) { editBtn.classList.remove('btn-primary'); editBtn.classList.add('btn-secondary'); }

        // Serie senza ID DB = appena aggiunta da config, motore non ancora girato
        if (!seriesId || seriesId === 0 || seriesId === '0') {
            document.getElementById('extto-plot').textContent = '';
            document.getElementById('extto-seasons').innerHTML = `
                <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:2rem;text-align:center;color:var(--text-muted);">
                    <i class="fa-solid fa-clock fa-2x" style="opacity:.4;display:block;margin-bottom:1rem;"></i>
                    <p style="margin:0 0 .5rem;font-size:1rem;color:var(--text-secondary);">${t('Serie aggiunta correttamente in')} <code>series.txt</code>.</p>
                    <p style="margin:0;font-size:.88rem;">${t('I dettagli e gli episodi appariranno dopo il')} <strong>${t('primo ciclo del motore')}</strong>.<br>${t('Usa il pannello')} <strong>${t('Modifica Serie')}</strong> ${t('per aggiustare le impostazioni nel frattempo.')}.</p>
                </div>`;
            // Abilita comunque il pannello modifica per poter editare subito
            if (editBtn) {
                editBtn.disabled = false;
                editBtn.title = 'Modifica le impostazioni di questa serie';
            }
            // Carica i metadati TMDB comunque se disponibili
            try {
                const cfgRes = await fetch(`${API_BASE}/api/config`);
                const cfg = await cfgRes.json();
                const series = cfg.series.find(s => s.name === seriesName);
                if (series) {
                    document.getElementById('extto-title').textContent = series.name;
                    document.getElementById('extto-year').innerHTML = `<i class="fa-regular fa-calendar"></i> —`;
                    document.getElementById('extto-network').style.display = 'none';
                    document.getElementById('extto-path').textContent = series.archive_path || '';
                }
            } catch(_) {}
            return;
        }

        try {
            const res = await fetch(`${API_BASE}/api/series/${seriesId}/extto-details`);
            const data = await res.json();
            
            if (!data.success) {
                document.getElementById('extto-plot').textContent = t('Errore:') + ' ' + data.error;
                return;
            }

            // Popola Header
            // Popola Header
            const meta = data.meta;
            
            // Applica il link diretto a TMDB se abbiamo l'ID
            const tmdbBtn = document.getElementById('extto-tmdb-btn');
            if (tmdbBtn) {
                if (meta.tmdb_id) {
                    tmdbBtn.onclick = () => window.open(`https://www.themoviedb.org/tv/${meta.tmdb_id}?language=it-IT`, '_blank');
                } else {
                    tmdbBtn.onclick = () => window.open(`https://www.themoviedb.org/search/tv?query=${encodeURIComponent(seriesName)}`, '_blank');
                }
            }
            
            // Badge nella vista dettaglio — tre stati:
            // ✅ COMPLETATA  = serie finita su TMDB + tutti gli episodi scaricati
            // 🏁 TERMINATA   = serie finita su TMDB, ma mancano ancora episodi
            // (nessun badge) = serie in corso
            let titleHtml = this.escapeHtml(seriesName);
            if (data.is_completed) {
                titleHtml += ` <span class="badge" style="background:linear-gradient(135deg,rgba(16,185,129,.15),rgba(52,211,153,.3));color:#34d399;border:1px solid rgba(52,211,153,.5);box-shadow:0 0 12px rgba(52,211,153,.25);margin-left:12px;font-size:0.78rem;vertical-align:middle;text-transform:uppercase;letter-spacing:.5px;padding:.25rem .6rem;"
                    title="Serie ufficialmente terminata e collezione completa al 100%!"><i class="fa-solid fa-check-double"></i> Serie Completata</span>`;
            } else if (data.is_ended) {
                // Calcola quanti episodi mancano per mostrarlo nel tooltip
                let totExp = 0, totDown = 0;
                (data.seasons || []).forEach(s => { if (s.monitored !== false) { totExp += s.total_episodes; totDown += s.downloaded_episodes; } });
                const missing = totExp - totDown;
                const missingTxt = missing > 0 ? ` — ${missing} ${t('Mancante')}` : '';
                titleHtml += ` <span class="badge" style="background:rgba(245,158,11,.15);color:#f59e0b;border:1px solid rgba(245,158,11,.4);margin-left:12px;font-size:0.78rem;vertical-align:middle;text-transform:uppercase;letter-spacing:.5px;padding:.25rem .6rem;"
                    title="${t('La serie è ufficialmente terminata su TMDB')} (${this.escapeHtml(data.tmdb_status || 'Ended')})${missingTxt}"><i class="fa-solid fa-flag-checkered"></i> ${t('Real (Definitivo)')}${missingTxt ? ` (${missing})` : ''}</span>`;
            }
            document.getElementById('extto-title').innerHTML = titleHtml;

            if (meta.poster) {
                document.getElementById('extto-poster').innerHTML = `<img src="https://image.tmdb.org/t/p/w342${meta.poster}" style="width:100%; height:100%; object-fit:cover; border-radius:8px;">`;
            }
            if (meta.backdrop) {
                document.getElementById('extto-backdrop').style.backgroundImage = `url('https://image.tmdb.org/t/p/w1280${meta.backdrop}')`;
            }
            document.getElementById('extto-year').innerHTML = `<i class="fa-regular fa-calendar"></i> ${meta.year || 'N/A'}`;
            const _network = meta.network || '';
            const _netEl = document.getElementById('extto-network');
            if (_network) {
                _netEl.innerHTML = `<i class="fa-solid fa-tv"></i> ${this.escapeHtml(_network)}`;
                _netEl.style.display = '';
            } else {
                _netEl.style.display = 'none';
            }
            
            // Pulisce i vecchi badge ID rimasti appesi dai click precedenti
            const badgeContainer = document.getElementById('extto-network').parentNode;
            badgeContainer.querySelectorAll('.dynamic-id-badge').forEach(b => b.remove());
            
            if (meta.tmdb_id) {
                const idBadge = document.createElement('span');
                idBadge.className = 'badge badge-secondary dynamic-id-badge'; // <-- Aggiunta classe per poterlo identificare e cancellare
                idBadge.style.fontFamily = 'monospace';
                idBadge.innerHTML = `<i class="fa-solid fa-fingerprint"></i> ID: ${meta.tmdb_id}`;
                badgeContainer.appendChild(idBadge);
            }
            document.getElementById('extto-plot').textContent = meta.overview || t('Nessuna trama disponibile.');
            const _pathEl = document.getElementById('extto-path');
            const _archivePath = data.archive_path || '';
            if (_archivePath) {
                _pathEl.textContent = _archivePath;
                _pathEl.style.display = '';
            } else {
                _pathEl.style.display = 'none';
            }
            // --- NUOVO: Disabilita il pulsante Cerca Mancanti se non serve ---
            let hasMissing = false;
            data.seasons.forEach(season => {
                // Controlla se c'è almeno una stagione MONITORATA a cui mancano episodi
                if (season.monitored !== false && season.downloaded_episodes < season.total_episodes) {
                    hasMissing = true;
                }
            });

            const searchMissingBtn = document.querySelector('button[onclick="app.searchMissingForSeries()"]');
            if (searchMissingBtn) {
                if (hasMissing) {
                    searchMissingBtn.disabled = false;
                    searchMissingBtn.style.opacity = '1';
                    searchMissingBtn.style.cursor = 'pointer';
                    searchMissingBtn.title = 'Cerca tutti gli episodi mancanti in background';
                } else {
                    searchMissingBtn.disabled = true;
                    searchMissingBtn.style.opacity = '0.4';
                    searchMissingBtn.style.cursor = 'not-allowed';
                    searchMissingBtn.title = t('Tutti gli episodi delle stagioni monitorate sono già stati scaricati!');
                }
            }
            // -----------------------------------------------------------------

            // Popola le Stagioni (Accordion)
            let html = '';
            data.seasons.forEach(season => {
                const sId = `season-${season.season}`;
                const sizeStr = season.total_size_bytes > 0 ? this._fmtBytes(season.total_size_bytes) : '';
                const progressColor = season.downloaded_episodes === season.total_episodes ? 'var(--success)' : 'var(--warning)';
                
                // Intestazione Stagione con Toggle EXTTO-Style
                const isMonitored = season.monitored !== false;
                const bookmarkIcon = isMonitored ? 'fa-solid fa-bookmark' : 'fa-regular fa-bookmark';
                const bookmarkColor = isMonitored ? 'var(--primary-light)' : 'var(--text-muted)';
                const opacity = isMonitored ? '1' : '0.5';

                html += `
                <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 1rem; overflow: hidden; opacity: ${opacity}; transition: opacity 0.3s;">
                    <div style="padding: 1rem 1.5rem; background: rgba(0,0,0,0.2); display: flex; align-items: center; justify-content: space-between; user-select: none;">
                        <div style="display:flex; align-items:center; gap: 15px;">
                            
                            <div onclick="app.toggleSeason(${this.currentSeriesId}, ${season.season})" title="${isMonitored ? 'Ignora Stagione (Spegni Ricerca)' : 'Monitora Stagione (Accendi Ricerca)'}" style="cursor:pointer; padding:5px;">
                                <i class="${bookmarkIcon}" style="color:${bookmarkColor}; font-size:1.4rem; transition: color 0.2s;"></i>
                            </div>
                            
                            <h3 style="margin:0; font-size:1.2rem; cursor:pointer;" onclick="document.getElementById('${sId}').style.display = document.getElementById('${sId}').style.display === 'none' ? 'block' : 'none';">${t('Stagione')} ${season.season}</h3>
                            <span style="background:${progressColor}; color:#000; padding:2px 8px; border-radius:4px; font-weight:bold; font-size:0.8rem;">${season.downloaded_episodes} / ${season.total_episodes}</span>
                            <span style="color:var(--text-muted); font-size:0.9rem;">${sizeStr}</span>
                        </div>
                        <i class="fa-solid fa-chevron-down" style="color:var(--text-muted); cursor:pointer;" onclick="document.getElementById('${sId}').style.display = document.getElementById('${sId}').style.display === 'none' ? 'block' : 'none';"></i>
                    </div>
                    
                    <div id="${sId}" style="display: ${season.season === data.seasons[0].season && isMonitored ? 'block' : 'none'};">
                        <div style="display:grid; grid-template-columns: 50px minmax(0, 1fr) 110px 85px 90px 190px; gap: 15px; padding: 0.75rem 1.5rem; border-bottom: 1px solid var(--border); font-size: 0.8rem; font-weight: bold; color: var(--text-secondary); text-transform: uppercase;">
                            <div>${t('EP')}</div><div>${t('Titolo')}</div><div style="text-align:right;">${t('Stato')}</div><div style="text-align:right;">${t('Dim.')}</div><div style="text-align:right;">${t('Aggiunto al client')}</div><div style="text-align:center;">${t('Azioni')}</div>
                        </div>`;
                
                // Righe Episodi
                season.episodes.forEach(ep => {
                    const epSize = ep.size > 0 ? this._fmtBytes(ep.size) : '-';
                    let statusBadge = '';
                    if (ep.status === 'Mancante') statusBadge = `<span style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> ${t('Mancante')}</span>`;
                    else if (ep.status === 'Scaricato') statusBadge = `<span style="color:var(--success);"><i class="fa-solid fa-check"></i> ${t('Scaricati')}</span>`;
                    else if (ep.status === 'In archivio') statusBadge = `<span style="color:#a78bfa;" title="${t('Presente nell\'archivio')}"><i class="fa-solid fa-box-archive"></i> ${t('Archivio')}</span>`;
                    else if (ep.status === 'In feed') statusBadge = `<span style="color:#f97316;" title="${t('Trovato nel feed ma non ancora scaricato (qualità o lingua non soddisfatte)')}"><i class="fa-solid fa-satellite-dish"></i> ${t('Feed')}</span>`;
                    else if (ep.status === 'In scarico' || ep.status === 'In Coda') statusBadge = `<span style="color:#38bdf8;" title="${t('Torrent inviato al client, download in corso')}"><i class="fa-solid fa-arrow-down-to-line fa-bounce"></i> ${t('download')}</span>`;
                    else if (ep.status === 'NAS') statusBadge = `<span style="color:#34d399;" title="${t('File presente sul NAS')}"><i class="fa-solid fa-house-signal"></i> NAS</span>`;
                    else if (ep.status === 'In DB') statusBadge = `<span style="color:var(--info);"><i class="fa-solid fa-database"></i> DB</span>`;
                    else statusBadge = `<span style="color:var(--info);"><i class="fa-solid fa-clock"></i> In DB</span>`;

                    const feedBtn = `<button class="btn btn-small" style="background:rgba(249,115,22,.15);color:#f97316;border:1px solid rgba(249,115,22,.4);" title="Mostra risultati trovati nel feed" onclick="app.showFeedMatches(${season.season}, ${ep.episode}, this); event.stopPropagation();"><i class="fa-solid fa-list-check"></i></button>`;

                    const dbBtns = ep.id ? `
                        <button class="btn btn-small btn-primary" title="Cerca versioni alternative (Jackett/Prowlarr, motori web, archivio)" onclick="app.searchMissingEpisode(${season.season}, ${ep.episode}); event.stopPropagation();"><i class="fa-solid fa-magnifying-glass"></i></button>
                        <button class="btn btn-small btn-secondary" title="Rimetti in coda automatica (azzera score)" onclick="app.redownloadEpisode(${ep.id}); event.stopPropagation();"><i class="fa-solid fa-rotate-left"></i></button>
                        <button class="btn btn-small btn-danger" title="Elimina dal DB" onclick="app.forceMissingEpisode(${ep.id}); event.stopPropagation();"><i class="fa-solid fa-trash"></i></button>
                        ${feedBtn}
                    ` : `
                        <button class="btn btn-small btn-primary" title="Cerca ora su tutti gli indexer e motori configurati" onclick="app.searchMissingEpisode(${season.season}, ${ep.episode}); event.stopPropagation();"><i class="fa-solid fa-magnifying-glass"></i></button>
                        ${feedBtn}
                    `;

                    // Titolo principale: file rinominato se disponibile, altrimenti titolo DB
                    const displayTitle = ep.file_name
                        ? ep.file_name.replace(/\.[^.]+$/, '') // rimuove estensione
                        : ep.title;
                    // Titolo secondario: titolo torrent originale solo se diverso dal principale
                    const subTitle = ep.file_name && ep.title && ep.title !== displayTitle ? ep.title : null;

                    let detailsHtml = [];
                    if (ep.score > 0) detailsHtml.push(`<span>${t('SCORE')} ${t('Qualità')}: <span style="color:var(--info);">${ep.score}</span></span>`);
                    if (subTitle) detailsHtml.push(`<span style="color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; flex:1;" title="${this.escapeHtml(subTitle)}"><i class="fa-solid fa-file-video"></i> ${this.escapeHtml(subTitle)}</span>`);

                    let detailsStr = detailsHtml.length > 0 ? `<small style="color:var(--text-muted); display:flex; gap:10px; align-items:center; margin-top:2px;">${detailsHtml.join('<span style="opacity:0.3;">|</span>')}</small>` : '';

                    const dlDate = ep.downloaded_at ? this.formatDate(ep.downloaded_at) : '';
                    html += `
                        <div class="table-row" style="display:grid; grid-template-columns: 50px minmax(0, 1fr) 110px 85px 90px 190px; gap: 15px; padding: 1rem 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.05); align-items:center;">
                            <div style="font-weight:bold;">${ep.episode}</div>
                            <div style="display:flex; flex-direction:column; overflow:hidden; min-width:0; padding-right:10px;">
                                <span style="font-weight:600; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${this.escapeHtml(displayTitle)}">${this.escapeHtml(displayTitle)}</span>
                                ${detailsStr}
                            </div>
                            <div style="font-size:0.85rem; font-weight:600; text-align:right;">${statusBadge}</div>
                            <div style="font-family:monospace; color:var(--text-secondary); font-size:0.85rem; text-align:right;">${epSize}</div>
                            <div style="font-family:monospace; color:var(--text-muted); font-size:0.78rem; text-align:right;" title="Aggiunto al client">${dlDate}</div>
                            <div style="display:flex; gap:5px; justify-content:center; align-items:center;">
                                ${dbBtns}
                            </div>
                        </div>`;
                });
                
                html += `</div></div>`;
            });
            
            document.getElementById('extto-seasons').innerHTML = html;

            // --- Ultimi 5 risultati trovati (feed matches globali della serie) ---
            try {
                const lfRes = await fetch(`${API_BASE}/api/series/${seriesId}/last-found?limit=5`);
                const lastFound = await lfRes.json();
                const lfContainer = document.getElementById('extto-last-found');
                if (lfContainer) {
                    if (!lastFound || lastFound.length === 0) {
                        lfContainer.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;padding:1rem;"><i class="fa-solid fa-circle-info"></i> Nessun risultato trovato finora per questa serie.</div>`;
                    } else {
                        const failLabels = {
                            'downloaded':    ['var(--success)',  'fa-circle-down',    t('Scaricati')],
                            'below_quality': ['var(--warning)',  'fa-circle-xmark',   t('Qualità bassa')],
                            'above_quality': ['#a78bfa',         'fa-circle-xmark',   t('Qualità troppo alta')],
                            'lang_mismatch': ['var(--danger)',   'fa-language',       t('Lingua errata')],
                            'blacklisted':   ['var(--danger)',   'fa-ban',            t('Blacklist')],
                        };
                        let lfHtml = lastFound.map(m => {
                            const [color, icon, label] = failLabels[m.fail_reason] || ['var(--info)', 'fa-clock', m.fail_reason || t('In Attesa')];
                            const epStr = `S${String(m.season).padStart(2,'0')}E${String(m.episode).padStart(2,'0')}`;
                            const dateStr = m.found_at ? new Date(m.found_at).toLocaleString('it-IT', {day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
                            const lfActionBtn = m.magnet
                                ? `<button class="btn btn-primary btn-small" style="padding:3px 8px;font-size:.78rem;white-space:nowrap;"
                                    title="Aggiunge questo torrent al client configurato"
                                    onclick="app.addMagnetFromFeed('${this.escapeJs(m.magnet)}', '${this.escapeJs(m.title)}');event.stopPropagation();">
                                    <i class="fa-solid fa-download"></i> Scarica
                                  </button>`
                                : '';
                            return `<div style="display:grid;grid-template-columns:70px minmax(0,1fr) 140px 90px 90px;gap:8px;align-items:center;padding:.55rem 1rem;border-bottom:1px solid rgba(255,255,255,0.05);font-size:0.82rem;">
                                <div style="font-family:monospace;color:var(--text-secondary);font-weight:bold;">${epStr}</div>
                                <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-primary);" title="${this.escapeHtml(m.title)}">${this.escapeHtml(m.title)}</div>
                                <div style="text-align:right;"><span style="color:${color};"><i class="fa-solid ${icon}"></i> ${label}</span></div>
                                <div style="text-align:right;color:var(--text-muted);white-space:nowrap;font-size:.78rem;">${dateStr}</div>
                                <div style="text-align:right;">${lfActionBtn}</div>
                            </div>`;
                        }).join('');
                        lfContainer.innerHTML = `
                            <div style="display:grid;grid-template-columns:70px minmax(0,1fr) 140px 90px 90px;gap:8px;padding:.4rem 1rem;font-size:0.75rem;font-weight:bold;color:var(--text-secondary);text-transform:uppercase;border-bottom:1px solid var(--border);">
                                <div>${t('Episodio')}</div><div>${t('Titolo')}</div>
                                <div style="text-align:right;" title="Esito del filtraggio automatico">Esito <i class="fa-solid fa-circle-question" style="opacity:.6;"></i></div>
                                <div style="text-align:right;">Data</div><div></div>
                            </div>${lfHtml}`;
                    }
                }
            } catch(_) {}
            // --------------------------------------------------------------------

        } catch (err) {
            console.error(err);
            document.getElementById('extto-plot').textContent = t('Errore di connessione');
        }
    },

    // Nuova funzione per forzare la ricerca di un episodio mancante
    // Nuova funzione per forzare la ricerca di un episodio mancante
    searchMissingEpisode(season, episode) {
        document.getElementById('manual-search-modal').classList.add('active');
        const q = `${this.currentSeriesName} S${String(season).padStart(2,'0')}E${String(episode).padStart(2,'0')} ita`;
        document.getElementById('manual-search-input').value = q;
        this.performManualSearch();
    },
    
    async toggleSeason(seriesId, seasonNum) {
        try {
            const res = await fetch(`${API_BASE}/api/series/${seriesId}/toggle-season`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({season: seasonNum})
            });
            const data = await res.json();
            if (data.success) {
                // Ricarica la vista per mostrare l'animazione di spegnimento/accensione
                this.showEpisodes(seriesId, this.currentSeriesName);
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (e) {
            this.showToast(t('Server communication error'), 'error');
        }
    },

    async searchMissingForSeries() {
        if(!confirm(`${t('Cerca mancanti')}: "${this.currentSeriesName}"?`)) return;
        
        try {
            const res = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/search-missing`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (e) {
            this.showToast(t('Server communication error'), 'error');
        }
    },
    
    // Parsa un titolo release e restituisce badge HTML con info tecniche
    _movieTechBadges(title) {
        if (!title) return '';
        const t = title;
        let badges = '';

        // Risoluzione
        if (/2160p|4K|UHD/i.test(t))       badges += `<span class="badge" style="background:rgba(139,92,246,.2);color:#a78bfa;border:1px solid rgba(139,92,246,.4);font-size:.65rem;padding:.15rem .4rem;">4K</span> `;
        else if (/1080p/i.test(t))          badges += `<span class="badge badge-info" style="font-size:.65rem;padding:.15rem .4rem;">1080p</span> `;
        else if (/720p/i.test(t))           badges += `<span class="badge badge-secondary" style="font-size:.65rem;padding:.15rem .4rem;">720p</span> `;

        // Fonte / streaming
        if (/DSNP|Disney\+?/i.test(t))      badges += `<span class="badge" style="background:rgba(0,103,196,.25);color:#60a5fa;border:1px solid rgba(0,103,196,.5);font-size:.65rem;padding:.15rem .4rem;">DSNP</span> `;
        else if (/AMZN|Amazon/i.test(t))    badges += `<span class="badge" style="background:rgba(255,153,0,.2);color:#fbbf24;border:1px solid rgba(255,153,0,.4);font-size:.65rem;padding:.15rem .4rem;">AMZN</span> `;
        else if (/NF|Netflix/i.test(t))     badges += `<span class="badge" style="background:rgba(229,9,20,.2);color:#f87171;border:1px solid rgba(229,9,20,.4);font-size:.65rem;padding:.15rem .4rem;">NF</span> `;
        else if (/ATVP|Apple/i.test(t))     badges += `<span class="badge" style="background:rgba(99,99,102,.25);color:#d1d5db;border:1px solid rgba(99,99,102,.4);font-size:.65rem;padding:.15rem .4rem;">ATVP</span> `;
        else if (/BluRay|BDRip|BDREMUX/i.test(t)) badges += `<span class="badge" style="background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(16,185,129,.3);font-size:.65rem;padding:.15rem .4rem;">BluRay</span> `;
        else if (/WEB-DL|WEBDL/i.test(t))  badges += `<span class="badge" style="background:rgba(59,130,246,.15);color:#93c5fd;border:1px solid rgba(59,130,246,.3);font-size:.65rem;padding:.15rem .4rem;">WEB-DL</span> `;
        else if (/WEBRip/i.test(t))         badges += `<span class="badge" style="background:rgba(59,130,246,.1);color:#93c5fd;border:1px solid rgba(59,130,246,.2);font-size:.65rem;padding:.15rem .4rem;">WEBRip</span> `;

        // HDR / DV
        if (/Dolby.?Vision|DV\b|\bDV\b/i.test(t)) badges += `<span class="badge" style="background:rgba(124,58,237,.25);color:#c4b5fd;border:1px solid rgba(124,58,237,.5);font-size:.65rem;padding:.15rem .4rem;">DV</span> `;
        if (/HDR10\+/i.test(t))             badges += `<span class="badge" style="background:rgba(245,158,11,.2);color:#fcd34d;border:1px solid rgba(245,158,11,.4);font-size:.65rem;padding:.15rem .4rem;">HDR10+</span> `;
        else if (/HDR/i.test(t))            badges += `<span class="badge" style="background:rgba(245,158,11,.15);color:#fcd34d;border:1px solid rgba(245,158,11,.3);font-size:.65rem;padding:.15rem .4rem;">HDR</span> `;

        // Codec video
        if (/[Hh]\.?265|HEVC|[Hh]265/i.test(t))  badges += `<span class="badge" style="background:rgba(16,185,129,.1);color:#6ee7b7;border:1px solid rgba(16,185,129,.2);font-size:.65rem;padding:.15rem .4rem;">H.265</span> `;
        else if (/[Hh]\.?264|AVC|[Hh]264/i.test(t)) badges += `<span class="badge" style="background:rgba(100,116,139,.2);color:#94a3b8;border:1px solid rgba(100,116,139,.3);font-size:.65rem;padding:.15rem .4rem;">H.264</span> `;
        else if (/AV1/i.test(t))            badges += `<span class="badge" style="background:rgba(16,185,129,.1);color:#6ee7b7;border:1px solid rgba(16,185,129,.2);font-size:.65rem;padding:.15rem .4rem;">AV1</span> `;

        // Audio
        if (/DTS-HD|DTS.HD/i.test(t))       badges += `<span class="badge" style="background:rgba(6,182,212,.15);color:#67e8f9;border:1px solid rgba(6,182,212,.3);font-size:.65rem;padding:.15rem .4rem;">DTS-HD</span> `;
        else if (/\bDTS\b/i.test(t))        badges += `<span class="badge" style="background:rgba(6,182,212,.1);color:#67e8f9;border:1px solid rgba(6,182,212,.2);font-size:.65rem;padding:.15rem .4rem;">DTS</span> `;
        else if (/TrueHD/i.test(t))         badges += `<span class="badge" style="background:rgba(6,182,212,.15);color:#67e8f9;border:1px solid rgba(6,182,212,.3);font-size:.65rem;padding:.15rem .4rem;">TrueHD</span> `;
        else if (/EAC3|E-AC3|DDP/i.test(t)) badges += `<span class="badge" style="background:rgba(6,182,212,.1);color:#67e8f9;border:1px solid rgba(6,182,212,.2);font-size:.65rem;padding:.15rem .4rem;">EAC3</span> `;
        else if (/\bAAC\b/i.test(t))        badges += `<span class="badge" style="background:rgba(6,182,212,.08);color:#67e8f9;border:1px solid rgba(6,182,212,.15);font-size:.65rem;padding:.15rem .4rem;">AAC</span> `;
        else if (/\bAC3\b/i.test(t))        badges += `<span class="badge" style="background:rgba(6,182,212,.08);color:#67e8f9;border:1px solid rgba(6,182,212,.15);font-size:.65rem;padding:.15rem .4rem;">AC3</span> `;

        // Canali audio
        if (/7\.1/i.test(t))                badges += `<span class="badge" style="background:rgba(100,116,139,.15);color:#94a3b8;border:1px solid rgba(100,116,139,.25);font-size:.65rem;padding:.15rem .4rem;">7.1</span> `;
        else if (/5\.1/i.test(t))           badges += `<span class="badge" style="background:rgba(100,116,139,.15);color:#94a3b8;border:1px solid rgba(100,116,139,.25);font-size:.65rem;padding:.15rem .4rem;">5.1</span> `;

        return badges.trim();
    },

    parseEpisodeTitle(title) {
        const parts = {};
        if (/2160p|4K/i.test(title)) parts.resolution = '4K';
        else if (/1080p/i.test(title)) parts.resolution = '1080p';
        else if (/720p/i.test(title)) parts.resolution = '720p'; else parts.resolution = 'SD';
        
        if (/BluRay|BDRip/i.test(title)) parts.source = 'BluRay';
        else if (/WEB-DL|WEBDL|WEBRip/i.test(title)) parts.source = 'WEB';
        else if (/HDTV/i.test(title)) parts.source = 'HDTV'; else parts.source = '-';

        if (/x265|HEVC/i.test(title)) parts.codec = 'HEVC';
        else if (/x264|AVC/i.test(title)) parts.codec = 'x264'; else parts.codec = '-';

        if (/ITA/i.test(title) && /ENG/i.test(title)) parts.audio = 'ITA-ENG';
        else if (/ITA/i.test(title)) parts.audio = 'ITA'; else parts.audio = '-';
        return parts;
    },

    // ========================================================================
    // MOVIES
    // ========================================================================
    _movieSortCol: 'name',
    _movieSortAsc: true,

    _setMovieSort(col) {
        if (this._movieSortCol === col) {
            this._movieSortAsc = !this._movieSortAsc;
        } else {
            this._movieSortCol = col;
            this._movieSortAsc = true;
        }
        this.loadMoviesConfig();
    },

    async loadMovies() { await this.loadMoviesConfig(); await this.loadMoviesDownloaded(); },
    switchMoviesTab(tab) {
        document.querySelectorAll('#view-movies .tab-btn').forEach(btn => btn.classList.remove('active'));
        if (tab === 'config') {
            document.querySelector('[data-tab="movies-config"]').classList.add('active');
            showEl('movies-config-list');
            hideEl('movies-downloaded-list');
        } else {
            document.querySelector('[data-tab="movies-downloaded"]').classList.add('active');
            hideEl('movies-config-list');
            showEl('movies-downloaded-list');
        }
    },
    async loadMoviesConfig() {
        try {
            const [configRes, downloadedRes, feedStatusRes] = await Promise.all([
                fetch(`${API_BASE}/api/config`),
                fetch(`${API_BASE}/api/movies`).catch(() => null),
                fetch(`${API_BASE}/api/movies/feed-status`).catch(() => null),
            ]);
            const config = await configRes.json();

            // Costruisci set dei film già scaricati (join per nome, case-insensitive)
            // + mappa nome -> titolo release per i badge tecnici
            let downloadedNames = new Set();
            let downloadedTitles = {};
            let feedNames = new Set();
            if (feedStatusRes && feedStatusRes.ok) {
                const feedList = await feedStatusRes.json();
                if (Array.isArray(feedList)) feedList.forEach(n => feedNames.add((n || '').toLowerCase()));
            }
            if (downloadedRes && downloadedRes.ok) {
                const downloaded = await downloadedRes.json();
                if (Array.isArray(downloaded)) {
                    downloaded.forEach(m => {
                        const key = (m.name || '').toLowerCase();
                        downloadedNames.add(key);
                        if (m.title) downloadedTitles[key] = m.title;
                    });
                }
            }

            const container = document.getElementById('movies-config-list');

            // Icone dinamiche per l'ordinamento
            const nameIcon = this._movieSortCol === 'name' ? (this._movieSortAsc ? ' ▲' : ' ▼') : ' ⇅';
            const yearIcon = this._movieSortCol === 'year' ? (this._movieSortAsc ? ' ▲' : ' ▼') : ' ⇅';

            let html = `<div class="table-row table-header">
                <div style="cursor:pointer; display:flex; align-items:center;" onclick="app._setMovieSort('name')" title="${t('Ordina per Nome')}">${t('Titolo')} <span style="font-size:0.7em; margin-left:5px; color:var(--text-muted);">${nameIcon}</span></div>
                <div class="col-center" style="cursor:pointer; display:flex; align-items:center; justify-content:center;" onclick="app._setMovieSort('year')" title="${t('Ordina per Anno')}">${t('Anno')} <span style="font-size:0.7em; margin-left:5px; color:var(--text-muted);">${yearIcon}</span></div>
                <div class="col-center">${t('Qualità')}</div><div class="col-center">${t('Lingua')}</div><div class="col-center">${t('AZIONI')}</div>
            </div>`;
            
            if (!config.movies || config.movies.length === 0) {
                html += `<div class="table-row" style="justify-content:center; color:var(--text-muted);">${t('No movies loaded')}</div>`;
            } else {
                // Ordina l'array prima di stamparlo (alfabetico di default)
                config.movies.sort((a, b) => {
                    let valA = a[this._movieSortCol] || '';
                    let valB = b[this._movieSortCol] || '';
                    
                    if (this._movieSortCol === 'name') {
                        valA = valA.toString().toLowerCase();
                        valB = valB.toString().toLowerCase();
                    } else if (this._movieSortCol === 'year') {
                        valA = parseInt(valA) || 0;
                        valB = parseInt(valB) || 0;
                    }
                    
                    if (valA < valB) return this._movieSortAsc ? -1 : 1;
                    if (valA > valB) return this._movieSortAsc ? 1 : -1;
                    return 0;
                });

                config.movies.forEach(m => { const key = (m.name || '').toLowerCase();
                    const isDownloaded = downloadedNames.has(key);
                    const releaseTitle = downloadedTitles[key] || '';
                    const techBadges = isDownloaded ? this._movieTechBadges(releaseTitle) : '';
                    const downloadedBadge = isDownloaded
                        ? `<span class="badge badge-success" style="margin-left:.4rem;" title="${t('Già scaricato — ancora in lista di ricerca')}"><i class="fa-solid fa-check"></i> ${t('Scaricati')}</span>`
                        : '';
                    html += `<div class="table-row" style="${isDownloaded ? 'opacity:.75;' : ''}">
                        <div style="display:flex; flex-direction:column; gap:.25rem; min-width:0;">
                            <div><strong>${this.escapeHtml(m.name)}</strong>${downloadedBadge}</div>
                            ${techBadges ? `<div style="display:flex; flex-wrap:wrap; gap:.2rem;">${techBadges}</div>` : ''}
                        </div>
                        <div class="col-center">${this.escapeHtml(m.year||'*')}</div>
                        <div class="col-center"><span class="badge badge-info">${this.escapeHtml(m.quality||'')}</span></div>
                        <div class="col-center"><small style="text-transform:uppercase;">${this.escapeHtml(m.language||'*')}</small></div>
                        <div class="table-actions" style="justify-content:center;">
                            <button class="btn btn-small btn-primary" onclick="app.showMovieDetails('${this.escapeJs(m.name)}')" title="Dettagli Film"><i class="fa-solid fa-list"></i></button>
                            ${(()=>{const hasFeed=feedNames.has((m.name||'').toLowerCase());const clr=hasFeed?'#22c55e':'#ef4444';const bg=hasFeed?'rgba(34,197,94,.15)':'rgba(239,68,68,.15)';const brd=hasFeed?'rgba(34,197,94,.4)':'rgba(239,68,68,.4)';const tip=hasFeed?t('Feed: risultati trovati — clicca per vederli'):t('Feed: nessun risultato ancora trovato');return `<button class="btn btn-small" style="background:${bg};color:${clr};border:1px solid ${brd};" title="${tip}" onclick="app.showMovieDetails('${this.escapeJs(m.name)}');setTimeout(()=>{const s=document.getElementById('movie-feed-section');if(s)s.scrollIntoView({behavior:'smooth',block:'nearest'});},800);event.stopPropagation();"><i class="fa-solid fa-satellite-dish"></i></button>`;})()}
                            <button class="btn btn-small btn-danger" onclick="app.deleteMovieFromConfig('${this.escapeJs(m.name)}')" title="Interrompi ricerca e rimuovi"><i class="fa-solid fa-trash-can"></i></button>
                        </div>
                    </div>`;
                });
            }
            container.innerHTML = html;
        } catch(e) { console.error("Errore loadMoviesConfig:", e); }
    },

    async loadMoviesDownloaded() {
        try {
            const [res, feedRes] = await Promise.all([
                fetch(`${API_BASE}/api/movies`),
                fetch(`${API_BASE}/api/movies/feed-status`).catch(() => null)
            ]);
            const movies = await res.json();
            let feedNames = new Set();
            try {
                if (feedRes && feedRes.ok) {
                    const feedList = await feedRes.json();
                    if (Array.isArray(feedList)) feedList.forEach(n => feedNames.add((n || '').toLowerCase()));
                }
            } catch(_) {}
            const container = document.getElementById('movies-downloaded-list');

            let html = `<div class="table-row table-header"><div>${t('Titolo')}</div><div class="col-center">${t('Data')}</div><div class="col-center">${t('AZIONI')}</div></div>`;

            if (!movies || movies.length === 0) {
                html += `<div class="table-row" style="justify-content:center; color:var(--text-muted);">${t('No movies loaded')}</div>`;
            } else {
                movies.forEach(m => {
                    const techBadges = this._movieTechBadges(m.title || '');
                    html += `<div class="table-row">
                        <div style="display:flex; flex-direction:column; gap:.25rem; min-width:0;">
                            <div><strong>${this.escapeHtml(m.name)}</strong></div>
                            <small style="color:var(--text-muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:300px;" title="${this.escapeHtml(m.title||'')}">${this.escapeHtml(m.title||'')}</small>
                            ${techBadges ? `<div style="display:flex; flex-wrap:wrap; gap:.2rem;">${techBadges}</div>` : ''}
                        </div>
                        <div class="col-center"><small>${m.downloaded_at ? this.formatDate(m.downloaded_at) : '-'}</small></div>
                        <div class="table-actions" style="justify-content:center;">
                            <button class="btn btn-small btn-primary" onclick="app.showMovieDetails('${this.escapeJs(m.name)}')" title="Dettagli Film"><i class="fa-solid fa-list"></i></button>
                            ${(()=>{const hasFeed=feedNames.has((m.name||'').toLowerCase());const clr=hasFeed?'#22c55e':'#ef4444';const bg=hasFeed?'rgba(34,197,94,.15)':'rgba(239,68,68,.15)';const brd=hasFeed?'rgba(34,197,94,.4)':'rgba(239,68,68,.4)';const tip=hasFeed?t('Feed: risultati trovati — clicca per vederli'):t('Feed: nessun risultato ancora trovato');return `<button class="btn btn-small" style="background:${bg};color:${clr};border:1px solid ${brd};" title="${tip}" onclick="app.showMovieDetails('${this.escapeJs(m.name)}');setTimeout(()=>{const s=document.getElementById('movie-feed-section');if(s)s.scrollIntoView({behavior:'smooth',block:'nearest'});},800);event.stopPropagation();"><i class="fa-solid fa-satellite-dish"></i></button>`;})()}
                            <button class="btn btn-small btn-danger" onclick="app.deleteMovie(${m.id})" title="Elimina dallo storico del database"><i class="fa-solid fa-trash-can"></i></button>
                        </div>
                    </div>`;
                });
            }
            container.innerHTML = html;
        } catch(e) { console.error("Errore loadMoviesDownloaded:", e); }
    },

    async editMovie(movieName) {
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            const movie = config.movies.find(m => m.name === movieName);
            
            if (!movie) {
                this.showToast(t('Movie not found'), 'error');
                return;
            }
            
            document.getElementById('edit-movie-original-name').value = movieName;
            document.getElementById('edit-movie-name').value = movie.name || '';
            document.getElementById('edit-movie-year').value = movie.year || '';
            document.getElementById('edit-movie-language').value = (movie.language || app._primaryLang || 'ita').toLowerCase();
            document.getElementById('edit-movie-quality').value = movie.quality || '720p-1080p';
            document.getElementById('edit-movie-enabled').checked = movie.enabled === true || movie.enabled === 'yes';
            _populateSubtitleWidget('edit-movie-subtitle-preset', 'edit-movie-subtitle-custom', movie.subtitle || '');
            
            document.getElementById('edit-movie-modal').classList.add('active');
        } catch (err) { console.error(err); }
    },

    async saveMovieChanges(event) {
        event.preventDefault();
        
        const originalName = document.getElementById('edit-movie-original-name').value;
        const updatedMovie = {
            name: document.getElementById('edit-movie-name').value.trim(),
            year: document.getElementById('edit-movie-year').value.trim(),
            language: (() => { const s = document.getElementById('edit-movie-language'); return s.value === 'custom' ? (document.getElementById('edit-movie-language-custom')?.value.trim() || '') : s.value; })(),
            quality: document.getElementById('edit-movie-quality').value,
            enabled: document.getElementById('edit-movie-enabled').checked,
            subtitle: (() => {
                const p = document.getElementById('edit-movie-subtitle-preset')?.value || '';
                return p === 'custom' ? (document.getElementById('edit-movie-subtitle-custom')?.value.trim() || '') : p;
            })(),
        };
        
        if (!updatedMovie.name) {
            this.showToast(t('Name is required'), 'error');
            return;
        }
        
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            const movieIndex = config.movies.findIndex(m => m.name === originalName);
            
            if (movieIndex === -1) {
                this.showToast(t('Movie not found'), 'error');
                return;
            }
            
            config.movies[movieIndex] = { ...config.movies[movieIndex], ...updatedMovie };
            
            const saveRes = await fetch(`${API_BASE}/api/config/movies`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ movies: config.movies })
            });
            
            if (saveRes.ok) {
                this.showToast(t('Film aggiornato!'), 'success');
                this.closeModal('edit-movie-modal');
                if (currentView === 'radarr') this.showMovieDetails(updatedMovie.name || originalName);
                else this.loadMoviesConfig();
            } else {
                this.showToast(t('Save error'), 'error');
            }
        } catch (err) {
            this.showToast(t('Save error'), 'error');
        }
    },

    // ========================================================================
    // ARCHIVE & MANUAL
    // ========================================================================
    // --- ARCHIVIO: stato selezione ---
    _archiveSelection: new Set(),   // Set di item.id (numeri)
    _archivePage: 0,
    _archiveQuery: '',
    
    // --- NUOVO: RICERCA TMDB DA ARCHIVIO ---
    // --- NUOVO: RICERCA TMDB DA ARCHIVIO (VERSIONE POTENZIATA) ---
    cleanTitleForTMDB(rawTitle) {
        // 1. Rimuove eventuali tag tra parentesi quadre ovunque si trovino (es. [Yameii], [Jackett RSS...])
        let title = rawTitle.replace(/\[.*?\]/g, '').trim();

        // 2. Rimuove l'estensione del file se presente per sbaglio
        title = title.replace(/\.(mkv|mp4|avi|ts)$/i, '');

        // 3. Il "Mietitore": taglia tutto da Stagioni, Anni (con o senza parentesi), Risoluzioni o Tag Tecnici
        // Spiegazione Regex:
        // [.\s] -> preceduto da punto o spazio
        // (?:S\d{1,2}|\d{1,2}x\d{1,2}) -> Cerca S01, S01E01, 1x01
        // | \(\d{4}\) | [.\s](?:19\d{2}|20\d{2})(?=[.\s]|$) -> Cerca (2022) oppure 2022 "nudo"
        // | 2160p|1080p|720p|480p|4k|bluray|web-dl|webrip|hdtv|ita|eng -> Cerca tag tecnici
        const cutOffRegex = /(.*?)(?:[.\s](?:[Ss]\d{1,2}|\d{1,2}x\d{1,3})|\(\d{4}\)|[.\s](?:19\d{2}|20\d{2})(?=[.\s]|$)|[.\s](?:2160p|1080p|720p|480p|4k|bluray|web-dl|webrip|hdtv|ita|eng))/i;

        const match = title.match(cutOffRegex);
        if (match && match[1]) {
            title = match[1];
        }

        // 4. Pulizia finale: converte punti e underscore in spazi e rimuove spazi doppi.
        // NON sostituisce i trattini normali per preservare titoli come "9-1-1" o "Spider-Man".
        return title.replace(/[._]/g, ' ').replace(/\s+/g, ' ').trim();
    },

    searchArchiveOnTMDB(rawTitle) {
        const cleanTitle = this.cleanTitleForTMDB(rawTitle);
        // Apre una nuova scheda del browser puntando alla ricerca globale di TMDB
        const url = `https://www.themoviedb.org/search?query=${encodeURIComponent(cleanTitle)}&language=it-IT`;
        window.open(url, '_blank');
    },
    // ----------------------------------------

    async loadArchive(page = 0, query = '') {
        this._archivePage = page;
        this._archiveQuery = query;
        this._archiveSelection = new Set();
        this._archiveUpdateBatchBar();
        if (this._archiveAbortCtrl) this._archiveAbortCtrl.abort();
        this._archiveAbortCtrl = new AbortController();
        try {
            const res = await fetch(`${API_BASE}/api/archive?page=${page}&q=${encodeURIComponent(query)}`, { signal: this._archiveAbortCtrl.signal });
            const data = await res.json();
            const container = document.getElementById('archive-list');
            const q = query.replace(/'/g, "\\'");
            let html = `<div class="table-row table-header">
                <div></div>
                <div>Titolo</div>
                <div>Data</div>
                <div style="text-align:right">Azioni</div>
            </div>`;
            data.items.forEach((item) => {
                const safeTitle = this.escapeHtml(item.title);
                const safeMagnet = this.escapeHtml(item.magnet || '');
                html += `<div class="table-row archive-row" data-id="${item.id}" data-magnet="${safeMagnet}">
                    <div>
                        <input type="checkbox" class="archive-cb" data-id="${item.id}"
                               onchange="app._archiveOnCheck(${item.id}, this.checked)">
                    </div>
                    <div class="col-title" title="${safeTitle}">
                        <small>${safeTitle}</small>
                    </div>
                    <div><small>${this.formatDate(item.added_at)}</small></div>
                    <div class="table-actions">
                        <button class="btn btn-small" style="background:#0ea5e9; color:white; border:none;"
                                onclick="app.searchArchiveOnTMDB('${this.escapeJs(item.title)}')"
                                title="Cerca info su TMDB"><i class="fa-solid fa-magnifying-glass"></i> TMDB</button>
                        <button class="btn btn-small btn-success"
                                onclick="app._promptNoRename(document.querySelector('.archive-row[data-id=\\'${item.id}\\']').dataset.magnet, '', true, '${this.escapeJs(item.title)}')"
                                title="Scarica"><i class="fa-solid fa-download"></i></button>
                        <button class="btn btn-small btn-secondary"
                                onclick="app.copyMagnet(document.querySelector('.archive-row[data-id=\\'${item.id}\\']').dataset.magnet)"
                                title="Copia magnet"><i class="fa-regular fa-copy"></i></button>
                        <button class="btn btn-small btn-danger"
                                onclick="app._archiveDeleteOne(${item.id})"
                                title="Elimina dall'archivio"><i class="fa-solid fa-trash"></i></button>
                    </div>
                </div>`;
            });
            if (data.items.length === 0) {
                html += `<div style="padding:30px; text-align:center; color:var(--text-muted);">${t('Nessun risultato')}</div>`;
            }
            container.innerHTML = html;
            // Paginazione
            const totalPages = Math.max(1, data.pages);
            document.getElementById('archive-pagination').innerHTML = `
                <button ${page===0?'disabled':''} onclick="app.loadArchive(${page-1}, '${q}')" class="btn btn-small btn-secondary"><i class="fa-solid fa-chevron-left"></i></button>
                <span style="margin:0 10px; font-weight:700;">${t('Pag')} ${page+1} / ${totalPages} &nbsp;<small style="color:var(--text-muted)">(${data.total} ${t('totali')})</small></span>
                <button ${page>=totalPages-1?'disabled':''} onclick="app.loadArchive(${page+1}, '${q}')" class="btn btn-small btn-secondary"><i class="fa-solid fa-chevron-right"></i></button>
            `;
            // Mostra barra batch se c'erano selezioni (reload di pagina)
            this._archiveUpdateBatchBar();
        } catch (err) { console.error('loadArchive:', err); }
    },

    _archiveOnCheck(id, checked) {
        if (checked) this._archiveSelection.add(id);
        else this._archiveSelection.delete(id);
        this._archiveUpdateBatchBar();
        // Aggiorna stato "select all"
        const cbs = document.querySelectorAll('.archive-cb');
        const allChecked = cbs.length > 0 && [...cbs].every(cb => cb.checked);
        const selectAll = document.getElementById('archive-select-all');
        if (selectAll) {
            selectAll.checked = allChecked;
            selectAll.indeterminate = !allChecked && this._archiveSelection.size > 0;
        }
    },

    archiveToggleAll(checked) {
        document.querySelectorAll('.archive-cb').forEach(cb => {
            cb.checked = checked;
            const id = parseInt(cb.dataset.id);
            if (checked) this._archiveSelection.add(id);
            else this._archiveSelection.delete(id);
        });
        this._archiveUpdateBatchBar();
    },

    _archiveUpdateBatchBar() {
        const n = this._archiveSelection.size;
        const bar = document.getElementById('archive-batch-bar');
        const countEl = document.getElementById('archive-sel-count');
        if (!bar) return;
        showEl(bar);   // sempre visibile
        if (countEl) countEl.textContent = n === 0 ? t('Nessuna selezione') : `${n} ${t('selezionati')}`;
        ['archive-btn-dl','archive-btn-copy','archive-btn-del'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.disabled = n === 0;
        });
    },

    async archiveBatchDownload() {
        const ids = [...this._archiveSelection];
        if (!ids.length) return;
        try {
            const res = await fetch(`${API_BASE}/api/archive/batch-download`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ids})
            });
            const data = await res.json();
            if (!data.success) { this.showToast(t('Errore:') + ' ' + data.error, 'error'); return; }
            let ok = 0;
            // Chiedi no_rename una volta sola per tutto il batch
            const noRenameBatch = data.items.length > 0
                ? await new Promise(resolve => {
                    this._nrPending = null;
                    const cb = document.getElementById('no-rename-confirm-flag');
                    if (cb) cb.checked = false;
                    const el = document.getElementById('no-rename-confirm-title');
                    if (el) el.textContent = `${data.items.length} torrent selezionati`;
                    document.getElementById('no-rename-confirm-modal').classList.add('active');
                    this._nrBatchResolve = resolve;
                })
                : false;
            for (const item of data.items) {
                if (item.magnet) { await this.sendMagnetToClient(item.magnet, '', true, noRenameBatch); ok++; }
            }
            this.showToast(`${t('Sent')} ${ok} ${t('magnets to client')}`, 'success');
        } catch(e) { this.showToast(t('Batch download error'), 'error'); }
    },

    async archiveBatchCopy() {
        const ids = [...this._archiveSelection];
        if (!ids.length) return;
        try {
            const res = await fetch(`${API_BASE}/api/archive/batch-download`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ids})
            });
            const data = await res.json();
            if (!data.success) { this.showToast(t('Errore:') + ' ' + data.error, 'error'); return; }
            const magnets = data.items.map(i => i.magnet).filter(Boolean).join('\n');
            const n = data.items.length;
            if (navigator.clipboard) {
                navigator.clipboard.writeText(magnets)
                    .then(() => this.showToast(`${n} ${t('magnets copied (one per line)')}`, 'success'))
                    .catch(() => { this._copyFallback(magnets); this.showToast(`${n} ${t('magnets copied (one per line)')}`, 'success'); });
            } else {
                this._copyFallback(magnets);
                this.showToast(`${n} ${t('magnets copied (one per line)')}`, 'success');
            }
        } catch(e) { this.showToast(t('Copy error'), 'error'); }
    },

    async archiveBatchDelete() {
        const ids = [...this._archiveSelection];
        if (!ids.length) return;
        if (!confirm(`${t('Elimina')} ${ids.length}?`)) return;
        try {
            const res = await fetch(`${API_BASE}/api/archive/delete`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ids})
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(`${t('Deleted')} ${data.deleted} ${t('items')}`, 'success');
                this._archiveSelection.clear();
                this.loadArchive(this._archivePage, this._archiveQuery);
            } else {
                this.showToast(t('Errore:') + ' ' + data.error, 'error');
            }
        } catch(e) { this.showToast(t('Deletion error'), 'error'); }
    },

    async _archiveDeleteOne(id) {
        if (!confirm(t('Eliminare questo elemento dall\'archivio?'))) return;
        try {
            const res = await fetch(`${API_BASE}/api/archive/delete`, {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ids: [id]})
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t('Item deleted'), 'success');
                this.loadArchive(this._archivePage, this._archiveQuery);
            } else {
                this.showToast(t('Errore:') + ' ' + data.error, 'error');
            }
        } catch(e) { this.showToast(t('Deletion error'), 'error'); }
    },

    async performManualSearch() {
        const query = document.getElementById('manual-search-input').value;
        if (!query) return;
        showEl('manual-search-loading');
        document.getElementById('manual-search-results').innerHTML = '';
        try {
            const res = await fetch(`${API_BASE}/api/manual-search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            hideEl('manual-search-loading');
            let html = '';
            if(!data.success) html = `<div style="padding:20px; text-align:center; color:var(--danger);">${this.escapeHtml(data.error)}</div>`;
            else if(!data.results || data.results.length===0) html = `<div style="padding:20px; text-align:center;">${t('Nessun risultato')}</div>`;
            else {
                data.results.forEach(item => {
                    let rejectHtml = '';
                    let titleStyle = '';
                    let btnClass = 'btn-success';
                    let btnIcon = 'fa-download';
                    let btnTitle = 'Scarica';

                    // Se il server ha segnalato dei motivi di scarto
                    if (item.rejections && item.rejections.length > 0) {
                        const reasons = item.rejections.join(' | ');
                        rejectHtml = `<span class="rejection-badge" title="Scartato: ${this.escapeHtml(reasons)}">
                                        <i class="fa-solid fa-circle-xmark"></i> SCARTATO
                                      </span>`;
                        titleStyle = 'color:var(--text-muted); opacity:0.6;';
                        btnClass = 'btn-warning';
                        btnIcon = 'fa-bolt';
                        btnTitle = 'Forza Download (Ignora Regole)';
                    }

                    html += `<div class="table-row" style="display:grid; grid-template-columns: 5fr 3fr 70px 100px; gap: 15px; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.05); padding: 10px 0;">
                        <div title="${this.escapeHtml(item.title)}" style="word-break: break-word;">
                            <div style="display:flex; align-items:flex-start;">
                                ${rejectHtml}
                                <strong style="${titleStyle}">${this.escapeHtml(item.title)}</strong>
                            </div>
                            <small style="color:var(--text-muted);">${item.size||''}</small>
                        </div>
                        <div style="color:var(--text-secondary); font-size:0.9rem;">${this.escapeHtml(item.source)}</div>
                        <div style="text-align:center;"><span class="badge badge-info">${item.score||0}</span></div>
                        <div class="table-actions" style="justify-content: flex-end;">
                            <button class="btn btn-small ${btnClass}" onclick="app.downloadManual(decodeURIComponent('${encodeURIComponent(item.magnet)}'))" title="${btnTitle}">
                                <i class="fa-solid ${btnIcon}"></i>
                            </button>
                        </div>
                    </div>`;
                });
            }
            document.getElementById('manual-search-results').innerHTML = html;
        } catch(e) { hideEl('manual-search-loading'); this.showToast(t('Search error'),'error'); }
    },
    
    filterManualResults(term) {
        term = term.toLowerCase();
        document.querySelectorAll('#manual-search-results .table-row').forEach(row => {
            // Controlla se il testo della riga contiene quello che hai digitato
            const text = row.textContent.toLowerCase();
            showIf(row, text.includes(term));
        });
    },

    async performManualSearchEd2k() {
        const query = document.getElementById('manual-search-input').value.trim();
        if (!query) { this.showToast(t('Inserisci un termine di ricerca'), 'warning'); return; }

        const btn     = document.getElementById('ms-ed2k-btn');
        const resBox  = document.getElementById('ms-ed2k-results');
        const loading = document.getElementById('ms-ed2k-loading');

        if (btn) btn.disabled = true;
        if (resBox) resBox.innerHTML = '';
        if (loading) loading.style.display = 'block';

        try {
            const res  = await fetch(`${API_BASE}/api/manual-search-ed2k?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            if (loading) loading.style.display = 'none';
            if (btn) btn.disabled = false;

            if (!data.success) {
                if (resBox) resBox.innerHTML = `<div style="padding:12px;color:var(--danger);font-size:0.85rem;">
                    <i class="fa-solid fa-circle-xmark"></i> ${this.escapeHtml(data.error)}
                </div>`;
                return;
            }

            const results = data.results || [];
            if (results.length === 0) {
                if (resBox) resBox.innerHTML = `<div style="padding:12px;color:var(--text-muted);font-size:0.85rem;text-align:center;">
                    ${t('Nessun risultato')}
                </div>`;
                return;
            }

            let html = `<div style="font-size:0.75rem;color:var(--text-muted);padding:4px 0 6px;border-bottom:1px solid var(--border);margin-bottom:4px;">
                <strong style="color:var(--primary-light);">${results.length}</strong> ${t('risultati eD2k')}
                <span style="margin-left:8px;opacity:0.6;">${t('ordinati per sorgenti')}</span>
            </div>`;

            results.forEach(item => {
                const name     = this.escapeHtml(item.name || '');
                const size     = item.size_str || item.size || '';
                const sources  = item.sources || 0;
                const idx      = item.idx;
                const srcColor = sources >= 10 ? 'var(--success)' : sources >= 3 ? 'var(--warning)' : 'var(--danger)';

                html += `<div style="display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;
                              padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
                    <div style="min-width:0;">
                        <div style="font-size:0.82rem;color:var(--text-primary);
                             white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${name}">${name}</div>
                        <div style="font-size:0.73rem;color:var(--text-muted);">${size}</div>
                    </div>
                    <div style="font-size:0.78rem;font-weight:700;color:${srcColor};white-space:nowrap;text-align:right;">
                        <i class="fa-solid fa-users" style="font-size:0.65rem;"></i> ${sources}
                    </div>
                    <div>
                        <button class="btn btn-small btn-success" title="${t('Scarica via aMule')}"
                            onclick="app.downloadEd2kResult(${idx}, this)">
                            <i class="fa-solid fa-download"></i>
                        </button>
                    </div>
                </div>`;
            });

            if (resBox) resBox.innerHTML = html;

        } catch(e) {
            if (loading) loading.style.display = 'none';
            if (btn) btn.disabled = false;
            if (resBox) resBox.innerHTML = `<div style="padding:12px;color:var(--danger);font-size:0.85rem;">${t('Errore di rete')}</div>`;
        }
    },

    async downloadEd2kResult(idx, btnEl) {
        if (btnEl) { btnEl.disabled = true; btnEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>'; }
        try {
            const res  = await fetch(`${API_BASE}/api/manual-search-ed2k-download`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({idx})
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t('Inviato ad aMule!'), 'success');
                if (btnEl) { btnEl.innerHTML = '<i class="fa-solid fa-check"></i>'; btnEl.classList.replace('btn-success','btn-secondary'); }
            } else {
                this.showToast(data.error || t('aMule non ha accettato il download'), 'error');
                if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = '<i class="fa-solid fa-download"></i>'; }
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
            if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = '<i class="fa-solid fa-download"></i>'; }
        }
    },

    async downloadManual(magnet, noRename = false) { await this.sendMagnetToClient(magnet, '', true, noRename); this.closeModal('manual-search-modal'); },

    async loadWanted() {
        const tbody = document.getElementById('wanted-table-body');
        if (!tbody) return;
        try {
            const res = await fetch(`${API_BASE}/api/series/all-missing`);
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            
            if (!data || data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:2rem; color:var(--text-muted);">Tutti gli episodi sono presenti nell\'archivio! 🎉</td></tr>';
                return;
            }
            
            tbody.innerHTML = data.map(item => `
                <tr>
                    <td>
                        <div style="font-weight:600; color:var(--primary-light); cursor:pointer;" onclick="app.loadExtToDetails(${item.series_id})">
                            ${this.escapeHtml(item.series_name)}
                        </div>
                    </td>
                    <td style="text-align:center; font-family:var(--font-mono); color:var(--text-secondary);">
                        S${String(item.season).padStart(2,'0')}E${String(item.episode).padStart(2,'0')}
                    </td>
                    <td style="text-align:right;">
                        <button class="btn btn-secondary btn-small" onclick="app.manualEpisodeSearch(${item.series_id}, '${this._esc(item.series_name)}', ${item.season}, ${item.episode})" title="Cerca questo episodio ora">
                            <i class="fa-solid fa-magnifying-glass"></i>
                        </button>
                    </td>
                </tr>
            `).join('');
        } catch(e) { 
            tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding:2rem; color:var(--danger);">Errore caricamento: ${e.message}</td></tr>`;
        }
    },

    async loadCalendar() {
        const grid = document.getElementById('calendar-grid');
        if (!grid) return;
        try {
            const res = await fetch(`${API_BASE}/api/series/calendar`);
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            
            if (!data || data.length === 0) {
                grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; padding:2rem; color:var(--text-muted);">${t('Nessun episodio in uscita nei prossimi giorni.')}</div>`;
                return;
            }
            
            grid.innerHTML = data.map(item => {
                const dateObj = new Date(item.air_date);
                const day = dateObj.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
                const weekday = dateObj.toLocaleDateString('it-IT', { weekday: 'short' }).toUpperCase();
                
                return `
                <div class="calendar-card">
                    <div class="cal-date">
                        <span class="cal-day">${day}</span>
                        <span class="cal-weekday">${weekday}</span>
                    </div>
                    <div class="cal-info">
                        <div class="cal-series" onclick="app.loadExtToDetails(${item.series_id})">${this.escapeHtml(item.series_name)}</div>
                        <div class="cal-ep">S${String(item.season).padStart(2,'0')}E${String(item.episode).padStart(2,'0')} - ${this.escapeHtml(item.name || 'TBA')}</div>
                    </div>
                </div>
                `;
            }).join('');
        } catch(e) {
            grid.innerHTML = `<div style="grid-column:1/-1; text-align:center; padding:2rem; color:var(--danger);">Errore caricamento calendario: ${e.message}</div>`;
        }
    },

    manualEpisodeSearch(sid, name, s, e) {
        document.getElementById('manual-search-modal').classList.add('active');
        document.getElementById('manual-search-input').value = `${name} S${String(s).padStart(2,'0')}E${String(e).padStart(2,'0')}`;
        this.performManualSearch();
    },

    // ========================================================================
    // CONFIGURATION
    // ========================================================================
    setupConfigTabs() {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tab = btn.dataset.tab;
                if (!tab) return;
                document.querySelectorAll('.config-panel').forEach(p => p.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const panel = document.getElementById(`config-${tab}`);
                if (panel) panel.classList.add('active');
            });
        });
    },

    async loadConfig() {
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const config = await res.json();
            this._configData = config;
            const s = config.settings || {};
            this._populateClientSelector(s);
            this._updateLtBadge();
            this._populateLtForm(s);
            this._populateExternalForms(s);
            this.renderNotifications(s);
            this.renderAdvanced(s);
            // Popola i checkbox websearch subito — evita reset se il tab Integrazioni non viene aperto prima di salvare
            {
                const _wse = (s.websearch_engines || '').split(',').map(e => e.trim().toLowerCase()).filter(Boolean);
                const _ck = (id, key) => { const el = document.getElementById(id); if (el) el.checked = _wse.includes(key); };
                _ck('websearch-bitsearch',    'bitsearch');
                _ck('websearch-tpb',          'tpb');
                _ck('websearch-knaben',       'knaben');
                _ck('websearch-btdig',        'btdig');
                _ck('websearch-limetorrents', 'limetorrents');
                _ck('websearch-torrentz2',    'torrentz2');
                _ck('websearch-torrentscsv',  'torrentscsv');
            }
            this.renderUrlsAndFilters(config);
            this.renderBrowserHandlers();
            // Carica esempio formato rinomina dopo che il DOM è pronto
            this._renameFormatExamples = null; // reset cache
            const initFmt = (s.rename_format || 'base');
            setTimeout(() => this._updateRenameFormatExample(initFmt), 50);
            let refVal = parseInt(s.refresh_interval) || 120;
            if (refVal > 1000) refVal = Math.round(refVal / 60); // Migrazione dal vecchio config
            document.getElementById('setting-refresh_interval').value = refVal;
            // Improvements 5 & 6: accordion persistence + dirty tracking
            setTimeout(() => {
                this.initAccordionPersistence();
                this.initDirtyTracking();
            }, 100);
        } catch(e) { console.error(e); }
    },

    async _fetchLtStats() {
        try {
            const r = await fetch(`${API_BASE}/api/torrents/stats`);
            if (!r.ok) return null;
            return await r.json();
        } catch { return null; }
    },

    async _updateLtBadge() {
        const badge = document.getElementById('lt-install-badge');
        if (!badge) return;
        const stats = await this._fetchLtStats();
        const isInstalled = stats ? (stats.lt_installed === true || stats.available === true) : null;
        badge.style.display = (isInstalled === false) ? 'inline' : 'none';
    },

    _populateClientSelector(s) {
        const clients = [
            { id: 'libtorrent',   label: 'libtorrent (embedded)', icon: 'fa-microchip',     key: 'libtorrent_enabled'   },
            { id: 'qbittorrent',  label: 'qBittorrent',           icon: 'fa-network-wired', key: 'qbittorrent_enabled'  },
            { id: 'transmission', label: 'Transmission',          icon: 'fa-network-wired', key: 'transmission_enabled' },
            { id: 'aria2',        label: 'aria2',                 icon: 'fa-terminal',      key: 'aria2_enabled'        },
        ];
        const container = document.getElementById('client-selector');
        if (!container) return;
        container.innerHTML = clients.map(c => {
            const active = (s[c.key] || 'no') === 'yes';
            return `<div class="client-btn${active?' active':''}" onclick="app.selectClient('${c.id}')">
                <i class="fa-solid ${c.icon}"></i>
                <span>${c.label}</span>
                ${active ? `<span class="client-active-badge">${t('Attivo')}</span>` : ''}
            </div>`;
        }).join('');
        const activeClient = clients.find(c => (s[c.key] || 'no') === 'yes');
        this._showClientForm(activeClient ? activeClient.id : null);
    },

    selectClient(clientId) {
        if (!this._configData) return;
        if (clientId === 'libtorrent') this._updateLtBadge();
        const s = this._configData.settings || {};
        ['libtorrent_enabled','qbittorrent_enabled','transmission_enabled','aria2_enabled']
            .forEach(k => s[k] = 'no');
        const keyMap = {libtorrent:'libtorrent_enabled', qbittorrent:'qbittorrent_enabled',
                        transmission:'transmission_enabled', aria2:'aria2_enabled'};
        if (keyMap[clientId]) s[keyMap[clientId]] = 'yes';
        this._configData.settings = s;
        this._populateClientSelector(s);
        this._showClientForm(clientId);
    },

    _showClientForm(clientId) {
        hideEl(document.querySelectorAll('.client-form'));
        if (clientId) {
            const form = document.getElementById(`form-${clientId}`);
            if (form) showEl(form);
        }
    },

    _populateLtForm(s) {
        const get = (k, def='') => s[`libtorrent_${k}`] !== undefined ? s[`libtorrent_${k}`] : def;
        const set = (id, val) => { const el = document.getElementById(id); if(el) el.value = val; };
        
        this._fetchLtStats().then(stats => {
            const badge = document.getElementById('lt-status-badge');
            if (badge) {
                const isInstalled = stats && (stats.lt_installed === true || stats.available === true);
                if (isInstalled) {
                    const ver = stats.libtorrent_version || stats.lt_version || stats.version;
                    const verText = ver ? ` v${ver}` : '';
                    
                    badge.className = 'badge badge-success';
                    badge.innerHTML = `<i class="fa-solid fa-check"></i> ${t('Installato')}${verText}`;
                } else {
                    badge.className = 'badge badge-warning';
                    badge.textContent = t('Non installato (Richiede: pip install libtorrent)');
                }
            }
        });
        this._setToggle('lt-sequential', get('sequential', 'no'));
        set('lt-dir',           get('dir', '/downloads'));
        set('lt-temp-dir',      get('temp_dir', ''));
        this._setToggle('lt-ramdisk-enabled', get('ramdisk_enabled', 'no'));
        set('lt-ramdisk-dir',       get('ramdisk_dir',       ''));
        set('lt-ramdisk-threshold', get('ramdisk_threshold_gb', '3.5'));
        set('lt-ramdisk-margin',    get('ramdisk_margin_gb',    '0.5'));
        this.toggleRamdisk();
        this._setToggle('lt-paused', get('paused', 'no'));
        const parseLimit = (val) => { let v = parseInt(val)||0; return v > 1000000 ? Math.round(v/1024) : v; };
        
        set('lt-port-min',      get('port_min', '6881'));
        set('lt-port-max',      get('port_max', '6891'));
        set('lt-dl-limit',      parseLimit(get('dl_limit','0')));
        set('lt-ul-limit',      parseLimit(get('ul_limit','0')));
        this._setToggle('lt-sched-enabled', get('sched_enabled','no'));
        set('lt-sched-start',   get('sched_start','23:00'));
        set('lt-sched-end',     get('sched_end','08:00'));
        set('lt-sched-dl',      parseLimit(get('sched_dl_limit','0')));
        set('lt-sched-ul',      parseLimit(get('sched_ul_limit','0')));
        const schedDays = String(get('sched_days', '')).split(',').map(d => d.trim()).filter(Boolean);
        for (let d = 0; d <= 6; d++) {
            const cb = document.getElementById(`sched-day-${d}`);
            if (cb) cb.checked = schedDays.length === 0 || schedDays.includes(String(d));
        }
        this.toggleScheduler();
        set('lt-conn-limit',    get('connections_limit', '200'));
        set('lt-upload-slots',  get('upload_slots', '4'));
        this._setToggle('lt-stop-at-ratio', get('stop_at_ratio', 'no'));
        set('lt-seed-ratio',    get('seed_ratio', '0'));
        set('lt-seed-time-days', get('seed_time_days', get('seed_time', '0')));
        set('lt-active-downloads', get('active_downloads', '3'));
        set('lt-active-seeds',     get('active_seeds', '3'));
        set('lt-active-limit',     get('active_limit', '5'));
        set('lt-slow-dl',          get('slow_dl_threshold', '2'));
        set('lt-slow-ul',          get('slow_ul_threshold', '2'));
        this._setToggle('lt-preallocate', get('preallocate', 'no'));
        this._setToggle('lt-disable-cow', get('disable_copy_on_write', 'no'));

        set('lt-cache-size',     get('cache_size',          ''));
        set('lt-queue-disk-mb',  get('max_queued_disk_mb',  ''));
        set('lt-send-buffer-kb', get('send_buffer_kb',      ''));
        set('lt-max-peer-list',  get('max_peer_list',       ''));
        fetch(`${API_BASE}/api/system/lt_mem_suggest`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d && d.total_mb) { const lbl = document.getElementById('lt-mem-total-label'); if (lbl) lbl.textContent = `${(d.total_mb/1024).toFixed(1)} GB`; } })
            .catch(() => {});
        set('lt-encryption',    get('encryption', '1'));
        this._setToggle('lt-dht',          get('dht', 'yes'));
        this._setToggle('lt-pex',          get('pex', 'yes'));
        this._setToggle('lt-lsd',          get('lsd', 'yes'));
        this._setToggle('lt-upnp',         get('upnp', 'yes'));
        this._setToggle('lt-natpmp',       get('natpmp', 'yes'));
        this._setToggle('lt-announce-all', get('announce_to_all', 'no'));
        set('lt-proxy-type',    get('proxy_type', 'none'));
        set('lt-proxy-host',    get('proxy_host', ''));
        set('lt-proxy-port',    get('proxy_port', '1080'));
        set('lt-proxy-user',    get('proxy_username', ''));
        set('lt-proxy-pass',    get('proxy_password', ''));
        set('lt-extra-trackers',get('extra_trackers', ''));
        set('lt-ipfilter-url',        get('ipfilter_url', ''));
        this._setToggle('lt-ipfilter-autoupdate', get('ipfilter_autoupdate', 'no'));
        this._updateIpFilterStatus();
        this.toggleProxyFields();
        this.loadTagDirRules();
    },

    _populateExternalForms(s) {
        const set = (id, val) => { const el = document.getElementById(id); if(el) el.value = val || ''; };
        set('qbt-url',      s.qbittorrent_url      || 'http://localhost:8080');
        set('qbt-user',     s.qbittorrent_username  || 'admin');
        set('qbt-pass',     s.qbittorrent_password  || '');
        set('qbt-category', s.qbittorrent_category  || 'tv');
        this._setToggle('qbt-paused', s.qbittorrent_paused || 'no');
        set('tr-url',       s.transmission_url      || 'http://localhost:9091/transmission/rpc');
        set('tr-user',      s.transmission_username  || '');
        set('tr-pass',      s.transmission_password  || '');
        this._setToggle('tr-paused', s.transmission_paused || 'no');
        set('ar-rpc-url',   s.aria2_rpc_url    || '');
        set('ar-secret',    s.aria2_rpc_secret  || '');
        set('ar-dir',       s.aria2_dir         || '');
        set('ar-path',      s.aria2c_path       || 'aria2c');
    
    // --- NUOVI CAMPI ---
        set('ar-max-conn',  s.aria2_max_connection || '16');
        set('ar-split',     s.aria2_split || '16');
        set('ar-dl-limit',  s.aria2_dl_limit || '0');
        set('ar-ul-limit',  s.aria2_ul_limit || '0');
    },

    toggleScheduler() {
        const el = document.getElementById('lt-sched-enabled');
        const enabled = el ? (el.type === 'checkbox' ? el.checked : el.value === 'yes') : false;
        ['sched-start-group','sched-end-group','sched-limits-group'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.opacity = enabled ? '1' : '0.4';
        });
        ['lt-sched-start','lt-sched-end','lt-sched-dl','lt-sched-ul'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.disabled = !enabled;
        });
    },

    toggleProxyFields() {
        const t = document.getElementById('lt-proxy-type')?.value || 'none';
        const vis = t !== 'none' ? '' : 'none';
        ['lt-proxy-host-group','lt-proxy-port-group','lt-proxy-user-group','lt-proxy-pass-group']
            .forEach(id => { const el = document.getElementById(id); if(el) el.style.display = vis; });
    },

    toggleRamdisk() {
        const rdEl = document.getElementById('lt-ramdisk-enabled');
        const enabled = rdEl ? (rdEl.type === 'checkbox' ? rdEl.checked : rdEl.value === 'yes') : false;
        const fields  = document.getElementById('lt-ramdisk-fields');
        if (fields) fields.style.display = enabled ? '' : 'none';
        if (!enabled) this._clearRamdiskStatus();
    },

    _clearRamdiskStatus() {
        const s = document.getElementById('lt-ramdisk-status');
        const i = document.getElementById('lt-ramdisk-info');
        if (s) { s.style.display = 'none'; s.innerHTML = ''; }
        if (i)   i.style.display = 'none';
    },

    async checkRamdisk() {
        const path = document.getElementById('lt-ramdisk-dir')?.value?.trim();
        const statusEl = document.getElementById('lt-ramdisk-status');
        const infoEl   = document.getElementById('lt-ramdisk-info');
        const btn      = document.getElementById('lt-ramdisk-check-btn');

        if (!path) {
            if (statusEl) {
                statusEl.style.display = '';
                statusEl.innerHTML = `<span style="color:var(--warning);">⚠️ ${t('Inserisci prima il percorso del RAM disk.')}</span>`;
            }
            return;
        }

        // Spinner durante la verifica
        if (btn) btn.disabled = true;
        if (statusEl) {
            statusEl.style.display = '';
            statusEl.innerHTML = `<span style="color:var(--text-secondary);"><i class="fa-solid fa-spinner fa-spin"></i> ${t('Verifica in corso...')}</span>`;
        }

        try {
            const res  = await fetch(`/api/ramdisk_check?path=${encodeURIComponent(path)}`);
            const data = await res.json();

            if (!data.ok) {
                // Errore bloccante
                statusEl.innerHTML = `<span style="color:var(--danger);">❌ ${data.error}</span>`;
                if (infoEl) infoEl.style.display = 'none';
            } else {
                // Tutto ok (o ok con avviso)
                let badge = '';
                if (data.warning) {
                    badge = `<span style="color:var(--warning);">⚠️ ${t('Scrivibile, ma')}: ${data.warning}</span>`;
                } else {
                    badge = `<span style="color:var(--success);">✅ ${t('RAM disk OK')} — <code>${data.mount_type}</code></span>`;
                }
                statusEl.innerHTML = badge;

                // Info strip spazio
                if (infoEl) {
                    document.getElementById('rd-total').textContent = data.total_gb + ' GB';
                    document.getElementById('rd-used').textContent  = data.used_gb  + ' GB';
                    document.getElementById('rd-free').textContent  = data.free_gb  + ' GB';
                    const fsEl = document.getElementById('rd-fstype');
                    if (fsEl) fsEl.textContent = `(${data.mount_type})`;
                    infoEl.style.display = '';
                }
            }
        } catch (e) {
            if (statusEl) statusEl.innerHTML = `<span style="color:var(--danger);">❌ ${t('Errore di rete')}: ${e.message}</span>`;
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    async saveLtSettings() {
        if (!this._configData) return;
        const s = this._configData.settings || {};
        const v = id => document.getElementById(id)?.value ?? '';
        const tg = id => this._getToggle(id);
        const allDays = [0,1,2,3,4,5,6];
        const checkedDays = allDays.filter(d => document.getElementById(`sched-day-${d}`)?.checked);
        const schedDaysStr = checkedDays.join(',');
        Object.assign(s, {
            libtorrent_enabled:            'yes',
            libtorrent_dir:                v('lt-dir'),
            libtorrent_sequential:         tg('lt-sequential'),
            libtorrent_temp_dir:           v('lt-temp-dir'),
            libtorrent_ramdisk_enabled:    tg('lt-ramdisk-enabled'),
            libtorrent_ramdisk_dir:        v('lt-ramdisk-dir'),
            libtorrent_ramdisk_threshold_gb: v('lt-ramdisk-threshold') || '3.5',
            libtorrent_ramdisk_margin_gb:    v('lt-ramdisk-margin')    || '0.5',
            libtorrent_interface:          v('lt-interface'),
            libtorrent_paused:             tg('lt-paused'),
            libtorrent_port_min:           v('lt-port-min'),
            libtorrent_port_max:           v('lt-port-max'),
            libtorrent_dl_limit:           String(parseInt(v('lt-dl-limit'))||0),
            libtorrent_ul_limit:           String(parseInt(v('lt-ul-limit'))||0),
            libtorrent_sched_enabled:      tg('lt-sched-enabled'),
            libtorrent_sched_start:        v('lt-sched-start'),
            libtorrent_sched_end:          v('lt-sched-end'),
            libtorrent_sched_dl_limit:     String(parseInt(v('lt-sched-dl'))||0),
            libtorrent_sched_ul_limit:     String(parseInt(v('lt-sched-ul'))||0),
            libtorrent_sched_days:         schedDaysStr,
            libtorrent_connections_limit:  v('lt-conn-limit'),
            libtorrent_upload_slots:       v('lt-upload-slots'),
            libtorrent_stop_at_ratio:      tg('lt-stop-at-ratio'),
            libtorrent_seed_ratio:         v('lt-seed-ratio'),
            libtorrent_seed_time_days:     v('lt-seed-time-days'),
            libtorrent_active_downloads:   v('lt-active-downloads'),
            libtorrent_active_seeds:       v('lt-active-seeds'),
            libtorrent_active_limit:       v('lt-active-limit'),
            libtorrent_slow_dl_threshold:  v('lt-slow-dl'),
            libtorrent_slow_ul_threshold:  v('lt-slow-ul'),
            libtorrent_preallocate:        tg('lt-preallocate'),
            libtorrent_disable_cow:        tg('lt-disable-cow'),
            libtorrent_encryption:         v('lt-encryption'),
            libtorrent_dht:                tg('lt-dht'),
            libtorrent_pex:                tg('lt-pex'),
            libtorrent_lsd:                tg('lt-lsd'),
            libtorrent_upnp:               tg('lt-upnp'),
            libtorrent_natpmp:             tg('lt-natpmp'),
            libtorrent_announce_to_all:    tg('lt-announce-all'),
            libtorrent_proxy_type:         v('lt-proxy-type'),
            libtorrent_proxy_host:         v('lt-proxy-host'),
            libtorrent_proxy_port:         v('lt-proxy-port'),
            libtorrent_proxy_username:     v('lt-proxy-user'),
            libtorrent_proxy_password:     v('lt-proxy-pass'),
            libtorrent_extra_trackers:     v('lt-extra-trackers'),
            libtorrent_ipfilter_url:       v('lt-ipfilter-url'),
            libtorrent_ipfilter_autoupdate: tg('lt-ipfilter-autoupdate'),
            libtorrent_cache_size:          v('lt-cache-size')     || '0',
            libtorrent_max_queued_disk_mb:  v('lt-queue-disk-mb')  || '4',
            libtorrent_send_buffer_kb:      v('lt-send-buffer-kb') || '512',
            libtorrent_max_peer_list:       v('lt-max-peer-list')  || '200',
            refresh_interval: v('setting-refresh_interval'),
        });
        await this._saveFullConfig({settings: s});
        await fetch(`${API_BASE}/api/torrents/apply_settings`, {method:'POST'});
        await this.saveTagDirRules();
        this.showToast(t('Libtorrent settings saved and applied'), 'success');
        this.clearDirty('settings');
        this.clearDirty('settings-advanced');
    },

    // =========================================================================
    // TAG DIR RULES
    // =========================================================================
    _tagDirRules: [],

    async loadTagDirRules() {
        try {
            const res = await fetch(`${API_BASE}/api/tag-dir-rules`);
            this._tagDirRules = res.ok ? (await res.json()) : [];
        } catch(e) {
            this._tagDirRules = [];
        }
        // Carica i tag esistenti dal DB per popolare i select
        let knownTags = [];
        try {
            const tr = await fetch(`${API_BASE}/api/torrent-tags`);
            if (tr.ok) {
                const data = await tr.json();
                // data è {hash: tag} — estrai tag unici non vuoti
                knownTags = [...new Set(Object.values(data).filter(Boolean))].sort();
            }
        } catch(e) {}
        this._tagDirKnownTags = knownTags;
        this._renderTagDirRulesTable();
    },

    _renderTagDirRulesTable() {
        const container = document.getElementById('tag-dir-rules-table');
        if (!container) return;
        const rules = this._tagDirRules || [];
        const knownTags = this._tagDirKnownTags || [];
        // Tag presenti nelle regole + quelli noti dal DB, unificati
        const allTags = [...new Set([
            'Film', 'Serie TV', 'Fumetti', 'Manuale',
            ...knownTags,
            ...rules.map(r => r.tag).filter(Boolean)
        ])].sort();

        if (rules.length === 0) {
            container.innerHTML = `<div style="color:var(--text-muted);font-size:.84rem;padding:.4rem 0;">Nessuna regola configurata. Le cartelle globali vengono usate per tutti i tag.</div>`;
            return;
        }

        const rows = rules.map((rule, i) => {
            const tagOpts = allTags.map(tag =>
                `<option value="${this.escapeHtml(tag)}" ${rule.tag === tag ? 'selected' : ''}>${this.escapeHtml(tag)}</option>`
            ).join('');
            return `<div style="display:grid;grid-template-columns:160px 1fr 1fr 36px;gap:6px;align-items:center;padding:.35rem 0;border-bottom:1px solid rgba(255,255,255,.06);">
                <select class="form-input" style="font-size:.82rem;padding:4px 6px;" onchange="app._tagDirRules[${i}].tag=this.value">
                    ${tagOpts}
                </select>
                <div style="display:flex;gap:4px;">
                    <input type="text" class="form-input" style="flex:1;font-size:.82rem;padding:4px 6px;" placeholder="Cartella temp (default globale)" value="${this.escapeHtml(rule.temp_dir||'')}"
                        oninput="app._tagDirRules[${i}].temp_dir=this.value"
                        onfocus="this._origVal=this.value"
                    >
                    <button type="button" class="btn btn-secondary btn-small" title="Sfoglia" onclick="app.openDirBrowser('__tdr_temp_${i}__')"><i class="fa-regular fa-folder-open"></i></button>
                </div>
                <div style="display:flex;gap:4px;">
                    <input type="text" class="form-input" style="flex:1;font-size:.82rem;padding:4px 6px;" placeholder="Cartella finale (default globale)" value="${this.escapeHtml(rule.final_dir||'')}"
                        oninput="app._tagDirRules[${i}].final_dir=this.value"
                    >
                    <button type="button" class="btn btn-secondary btn-small" title="Sfoglia" onclick="app.openDirBrowser('__tdr_final_${i}__')"><i class="fa-regular fa-folder-open"></i></button>
                </div>
                <button type="button" class="btn btn-small" style="background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3);padding:4px 8px;" onclick="app._removeTagDirRule(${i})"><i class="fa-solid fa-trash"></i></button>
            </div>`;
        }).join('');

        const header = `<div style="display:grid;grid-template-columns:160px 1fr 1fr 36px;gap:6px;padding:.25rem 0;font-size:.75rem;font-weight:600;color:var(--text-secondary);text-transform:uppercase;">
            <div>Tag</div><div>Cartella temporanea</div><div>Cartella definitiva</div><div></div>
        </div>`;
        container.innerHTML = header + rows;

        // Intercetta openDirBrowser per i campi __tdr_*__
        this._tdrDirBrowserActive = true;
    },

    addTagDirRule() {
        if (!this._tagDirRules) this._tagDirRules = [];
        const knownTags = this._tagDirKnownTags || [];
        const allTags = [...new Set(['Film', 'Serie TV', 'Fumetti', 'Manuale', ...knownTags])];
        // Suggerisci il primo tag non ancora usato
        const usedTags = new Set(this._tagDirRules.map(r => r.tag));
        const nextTag = allTags.find(t => !usedTags.has(t)) || '';
        this._tagDirRules.push({ tag: nextTag, temp_dir: '', final_dir: '' });
        this._renderTagDirRulesTable();
    },

    _removeTagDirRule(i) {
        this._tagDirRules.splice(i, 1);
        this._renderTagDirRulesTable();
    },

    async saveTagDirRules() {
        try {
            const rules = (this._tagDirRules || []).filter(r => r.tag && r.tag.trim());
            await fetch(`${API_BASE}/api/tag-dir-rules`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(rules)
            });
        } catch(e) {
            console.warn('saveTagDirRules:', e);
        }
    },

    async suggestLtMemSettings() {
        const btn = document.getElementById('lt-mem-suggest-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>'; }
        try {
            const r = await fetch(`${API_BASE}/api/system/lt_mem_suggest`);
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const d = await r.json();
            const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
            const lbl = document.getElementById('lt-mem-total-label');
            if (lbl) lbl.textContent = d.total_mb ? `${(d.total_mb/1024).toFixed(1)} GB` : '—';
            set('lt-cache-size',     d.cache_size  ?? 0);
            set('lt-queue-disk-mb',  d.queue_mb    ?? 4);
            set('lt-send-buffer-kb', d.send_kb     ?? 512);
            set('lt-max-peer-list',  d.peer_list   ?? 200);
            const _cacheMb = d.cache_mb ?? Math.round((d.cache_size ?? 0) * 16 / 1024);
            const _cacheStr = _cacheMb > 0 ? ` (cache ${_cacheMb} MB, write-buf ${d.queue_mb ?? 4} MB)` : ` (cache off, write-buf ${d.queue_mb ?? 4} MB)`;
            this.showToast(t('Valori suggeriti per NFS/NAS') + _cacheStr + ' — ' + t('salva per renderli effettivi'), 'info');
        } catch(e) {
            this.showToast(t('Errore nel calcolo dei valori suggeriti'), 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> ' + t('Suggerisci valori');
            }
        }
    },

    async reapplyLtSettings() {
        try {
            const res = await fetch(`${API_BASE}/api/torrents/apply_settings`, {method:'POST'});
            if (!res.ok) throw new Error('HTTP ' + res.status);
            this.showToast(t('Settings reapplied to session'), 'success');
        } catch(e) {
            this.showToast(t('Error reapplying settings') + ': ' + e.message, 'error');
        }
    },
    
    switchMaintTab(tab) {
        document.querySelectorAll('#view-maintenance .tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`#view-maintenance [data-tab="${tab}"]`).classList.add('active');
        
        if (tab === 'db') {
            showEl('maint-db-panel');
            showEl('btn-maint-refresh'); 
            hideEl('maint-i18n-panel');
        } else {
            hideEl('maint-db-panel');
            hideEl('btn-maint-refresh'); 
            showEl('maint-i18n-panel');
            
            // --- MODIFICA: Carica l'editor automaticamente se passi alla tab Traduzioni ---
            if (!this._i18n_loaded_once) {
                this.i18nInit();
                this._i18n_loaded_once = true;
            }
        }
    },

    switchConfigTab(tab) {
        document.querySelectorAll('.config-panel').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
        // Map logical tab names to panel IDs
        const panelId = tab === 'settings-advanced' ? 'config-settings-advanced'
                      : tab === 'integrazioni'       ? 'config-integrazioni'
                      : `config-${tab}`;
        const panel = document.getElementById(panelId);
        if (panel) panel.classList.add('active');

        // Usiamo 'app.' per evitare l'errore "this.loadScoresSettings is not a function"
        if (tab === 'scores') app.loadScoresSettings();
        if (tab === 'integrazioni') { app.traktInit(); app.jellyfinInit(); app.plexInit(); app.indexerInit(); }
        // Legacy support for direct calls
        if (tab === 'trakt')       { app.traktInit(); }
        if (tab === 'mediaserver') { app.jellyfinInit(); app.plexInit(); }
        // Accordion persistence after tab switch
        app.initAccordionPersistence();
    },

    // =========================================================================
    // IMPROVEMENT 1 — Toggle switch helpers
    // =========================================================================
    _getToggle(id) {
        const el = document.getElementById(id);
        if (!el) return 'no';
        if (el.type === 'checkbox') return el.checked ? (el.value || 'yes') : 'no';
        return el.value; // fallback for non-converted selects
    },
    _setToggle(id, val) {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') {
            el.checked = (val === 'yes' || val === 'true' || val === '1' || val === true);
        } else {
            el.value = val;
        }
    },

    // =========================================================================
    // IMPROVEMENT 5 — Accordion persistence
    // =========================================================================
    initAccordionPersistence() {
        document.querySelectorAll('#view-config details[id]').forEach(d => {
            // avoid double-binding
            if (d._accordionBound) return;
            d._accordionBound = true;
            const key = 'accordion_' + d.id;
            const saved = localStorage.getItem(key);
            if (saved !== null) d.open = saved === '1';
            d.addEventListener('toggle', () => {
                localStorage.setItem(key, d.open ? '1' : '0');
            });
        });
    },

    // =========================================================================
    // IMPROVEMENT 6 — Dirty tracking
    // =========================================================================
    initDirtyTracking() {
        document.querySelectorAll('#view-config input, #view-config select, #view-config textarea').forEach(el => {
            if (el._dirtyBound) return;
            el._dirtyBound = true;
            el.addEventListener('change', () => {
                const tabContent = el.closest('.config-panel');
                if (!tabContent) return;
                const tabId = tabContent.id;
                // Map panel ID back to tab data-tab value
                const tabKey = tabId === 'config-settings-advanced' ? 'settings-advanced'
                             : tabId === 'config-integrazioni'       ? 'integrazioni'
                             : tabId.replace('config-', '');
                const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabKey}"]`);
                if (tabBtn) tabBtn.classList.add('dirty');
            });
        });
    },
    clearDirty(tabId) {
        const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
        if (tabBtn) tabBtn.classList.remove('dirty');
    },

    renderNotifications(settings) {
        const notifContainer = document.getElementById('notifications-form');
        if (!notifContainer) return;
        const tgToken = settings['telegram_bot_token'] || '';
        const maskedToken = tgToken.length > 8
            ? '•'.repeat(tgToken.length - 4) + tgToken.slice(-4)
            : tgToken;
        notifContainer.innerHTML = `
            <div class="card" style="margin-bottom:20px;">
                <div class="card-header">Telegram</div>
                <div class="card-body" style="display:grid; grid-template-columns: 100px 1fr 1fr; gap:15px;">
                    ${this.renderField({key:'notify_telegram', label:'Attivo', type:'select', options:['yes','no']}, settings)}
                    <div class="form-group">
                        <label>${t('Token Bot')} <span style="color:var(--text-muted); font-size:0.8rem;">(${t('salvato nel Database')})</span></label>
                        <div style="position:relative; display:flex; gap:0.5rem;">
                            <input type="password" id="setting-telegram_bot_token" value="${this._esc(tgToken)}"
                                style="flex:1; font-family:var(--font-mono); width:100%;">
                            <button type="button" class="btn btn-small btn-secondary" style="flex-shrink:0;"
                                onclick="const i=document.getElementById('setting-telegram_bot_token'); i.type=i.type==='password'?'text':'password'; this.innerHTML=i.type==='password'?'<i class=\\'fa-solid fa-eye\\'></i>':'<i class=\\'fa-solid fa-eye-slash\\'></i>';"
                                title="Mostra/nascondi token"><i class="fa-solid fa-eye"></i></button>
                        </div>
                    </div>
                    ${this.renderField({key:'telegram_chat_id', label:'Chat ID', type:'text'}, settings)}
                </div>
            </div>
            <div class="card">
                <div class="card-header">Email</div>
                <div class="card-body" style="display:grid; grid-template-columns: 100px 1.5fr 1fr 1fr 1fr; gap:15px;">
                    ${this.renderField({key:'notify_email', label:'Attivo', type:'select', options:['yes','no']}, settings)}
                    ${this.renderField({key:'email_smtp', label:'SMTP', type:'text'}, settings)}
                    ${this.renderField({key:'email_from', label:'From', type:'text'}, settings)}
                    ${this.renderField({key:'email_to', label:'To', type:'text'}, settings)}
                    ${this.renderField({key:'email_password', label:'Pass', type:'password'}, settings)}
                </div>
            </div>`;
    },

    renderBrowserHandlers() {
        const el = document.getElementById('browser-handlers-card');
        if (!el) return;
        const base = API_BASE;

        // Il one-liner bash contiene $f che NON deve essere interpolato da JS —
        // lo costruiamo come stringa normale (non template literal) e lo iniettiamo dopo.
        const files = ['extto-magnet', 'extto-torrent', 'extto-magnet.desktop', 'extto-torrent.desktop', 'install.sh'];
        const dlParts = files.map(f =>
            'curl -fsSL "' + base + '/api/browser-handlers/download?file=' + encodeURIComponent(f) + '" -o "' + f + '"'
        ).join(' && \\\n  ');
        const oneliner = 'cd /tmp && mkdir -p extto-handlers && cd extto-handlers && \\\n  ' +
                         dlParts + ' && \\\n  chmod +x extto-magnet extto-torrent install.sh && bash install.sh';

        const uninstall = 'sudo rm -f /usr/local/bin/extto-magnet /usr/local/bin/extto-torrent && \\\n' +
                          'rm -f ~/.local/share/applications/extto-magnet.desktop \\\n' +
                          '      ~/.local/share/applications/extto-torrent.desktop && \\\n' +
                          'update-desktop-database ~/.local/share/applications';

        const btnHtml = files.map(f => {
            const icon = f.endsWith('.desktop') ? 'fa-file-lines'
                       : f === 'install.sh'     ? 'fa-terminal'
                       : 'fa-file-code';
            return '<a href="' + base + '/api/browser-handlers/download?file=' + encodeURIComponent(f) + '" ' +
                   'download="' + f + '" class="btn btn-secondary" ' +
                   'style="display:flex;align-items:center;gap:7px;justify-content:center;font-size:0.8rem;padding:8px 10px;text-decoration:none;">' +
                   '<i class="fa-solid ' + icon + '"></i> ' + f + '</a>';
        }).join('');

        el.innerHTML =
            '<div class="card-header" style="display:flex;align-items:center;gap:10px;">' +
                '<i class="fa-solid fa-magnet" style="color:var(--accent);"></i>' +
                ' ' + t('Handler Magnet & Torrent per Browser') +
            '</div>' +
            '<div class="card-body">' +
                '<p style="color:var(--text-secondary);margin:0 0 14px;font-size:0.88rem;line-height:1.6;">' +
                    t('Scarica i 5 file qui sotto, mettili nella stessa cartella e lancia install.sh dal terminale. Da quel momento ogni click su un link magnet: o su un file .torrent nel browser invierà automaticamente il download a EXTTO.') +
                '</p>' +

                '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:10px;margin-bottom:18px;">' +
                    btnHtml +
                '</div>' +

                '<details style="margin-top:4px;">' +
                    '<summary style="cursor:pointer;font-size:0.83rem;color:var(--text-muted);user-select:none;">' +
                        '<i class="fa-solid fa-terminal" style="margin-right:5px;"></i>' +
                        t('Comando rapido — scarica e installa in un colpo') +
                    '</summary>' +
                    '<div style="margin-top:10px;">' +
                        '<p style="font-size:0.82rem;color:var(--text-secondary);margin:0 0 8px;">' +
                            t('Esegui questo nel terminale del tuo PC (non sul server):') +
                        '</p>' +
                        '<div style="position:relative;">' +
                            '<pre id="bh-oneliner" style="background:var(--bg-main);border:1px solid var(--border);border-radius:var(--radius-md);padding:10px 14px;font-size:0.78rem;overflow-x:auto;white-space:pre;margin:0;padding-right:48px;"></pre>' +
                            '<button onclick="navigator.clipboard.writeText(document.getElementById(\'bh-oneliner\').textContent).then(()=>app.showToast(t(\'Copiato!\'),\'success\'))" ' +
                                    'class="btn btn-small btn-secondary" ' +
                                    'style="position:absolute;top:6px;right:6px;" title="' + t('Copia negli appunti') + '">' +
                                '<i class="fa-regular fa-copy"></i>' +
                            '</button>' +
                        '</div>' +
                        '<p style="font-size:0.78rem;color:var(--text-muted);margin-top:8px;">' +
                            '<i class="fa-solid fa-circle-info" style="margin-right:4px;"></i>' +
                            t('Richiede: curl, python3, xdg-utils. Dopo l\'installazione riavvia il browser.') +
                        '</p>' +
                    '</div>' +
                '</details>' +

                '<details style="margin-top:10px;">' +
                    '<summary style="cursor:pointer;font-size:0.83rem;color:var(--text-muted);user-select:none;">' +
                        '<i class="fa-solid fa-trash-can" style="margin-right:5px;"></i>' +
                        t('Disinstallazione') +
                    '</summary>' +
                    '<div style="margin-top:10px;position:relative;">' +
                        '<pre id="bh-uninstall" style="background:var(--bg-main);border:1px solid var(--border);border-radius:var(--radius-md);padding:10px 14px;font-size:0.78rem;overflow-x:auto;white-space:pre;margin:0;padding-right:48px;"></pre>' +
                        '<button onclick="navigator.clipboard.writeText(document.getElementById(\'bh-uninstall\').textContent).then(()=>app.showToast(t(\'Copiato!\'),\'success\'))" ' +
                                'class="btn btn-small btn-secondary" ' +
                                'style="position:absolute;top:6px;right:6px;" title="' + t('Copia negli appunti') + '">' +
                            '<i class="fa-regular fa-copy"></i>' +
                        '</button>' +
                    '</div>' +
                '</details>' +

                '<div style="margin-top:14px;padding:10px 14px;background:var(--bg-main);border-radius:var(--radius-md);border-left:3px solid var(--accent);font-size:0.8rem;color:var(--text-secondary);line-height:1.6;">' +
                    '<strong>' + t('Come funziona') + ':</strong> ' +
                    t('Il browser chiama extto-magnet / extto-torrent via xdg-open, che manda una POST a EXTTO. L\'URL del server è già scritto negli script scaricati. Per cambiarlo dopo l\'installazione:') +
                    ' <code>sudo nano /usr/local/bin/extto-magnet</code>.' +
                '</div>' +
            '</div>';

        // Inietta i testi bash DOPO aver creato il DOM (evita escaping HTML)
        const preOneliner   = el.querySelector('#bh-oneliner');
        const preUninstall  = el.querySelector('#bh-uninstall');
        if (preOneliner)  preOneliner.textContent  = oneliner;
        if (preUninstall) preUninstall.textContent = uninstall;
    },

    renderAdvanced(settings) {
        const advContainer = document.getElementById('advanced-form');
        if (!advContainer) return;

        const categories = [
            {
                title: '📂 ' + t('Rinomina & Spostamento') + ' (' + t('solo libtorrent integrato') + ')',
                fields: [
                    {k:'rename_episodes',    l:'Rinomina Episodi',          t:'select', o:['yes','no'],
                     d:'Dopo il download rinomina i file nel formato scelto sotto, usando i titoli ufficiali TMDB. Richiede tmdb_api_key configurata. Funziona solo con il client libtorrent integrato.'},
                    {k:'rename_format',      l:'Formato Rinomina',          t:'custom',
                     d:'Scegli lo stile del nome file. Standard e Completo richiedono pymediainfo installato sul server.'},
                    {k:'move_episodes',      l:'Sposta Episodi (NAS)',      t:'select', o:['yes','no'],
                     d:"A fine download sposta automaticamente i file nel 'Percorso Archivio' se lo hai configurato per quella serie. Funziona solo con il client libtorrent integrato."},
                    {k:'archive_root',       l:'Radice Archivio (NAS)',     t:'path',
                     d:"Cartella principale per l'individuazione automatica delle serie. Se non specifichi un percorso per una serie, EXTTO la cercherà qui."},
                ]
            },
            {
                title: `🧹 ${t('Pulizia & Eliminazione')}`,
                rows: [
                    [
                        {k:'cleanup_upgrades',       l:'Pulizia Auto Upgrade',  t:'select', o:['no','yes'],   flex:1,
                         d:"Se abilitato, quando arriva una versione di qualità superiore i file precedenti vengono spostati in trash. Se arriva una versione peggiore di una già esistente, viene scartata direttamente in trash."},
                        {k:'cleanup_action',         l:'Azione Pulizia',        t:'select', o:['move','delete'], flex:1,
                         d:"Definisce se i file obsoleti devono essere spostati nel cestino (@trash_path) o eliminati fisicamente (Irreversibile)."},
                        {k:'cleanup_min_score_diff', l:'Diff. Minima Score',    t:'number', flex:1,
                         d:"Differenza minima di score tra il nuovo file e quello esistente per attivare la pulizia. 0 = qualsiasi upgrade. 500 = solo salti significativi (es. 720p→4K)."},
                    ],
                    [
                        {k:'trash_path',             l:'Cartella Trash',           t:'path',   flex:2,
                         d:"Percorso assoluto della cartella dove spostare i file obsoleti. Viene creata automaticamente se non esiste. Esempio: /mnt/nas/trash"},
                        {k:'trash_retention_days',   l:'Giorni Retention Cestino', t:'number', flex:1,
                         d:"Elimina automaticamente i file nel cestino più vecchi di N giorni ad ogni ciclo. Lascia vuoto per non cancellare mai."},
                    ],
                ]
            },
            {
                title: `⚙️ ${t('Automazione & Ricerca')}`,
                rows: [
                    [
                        {k:'gap_filling',        l:'Gap Fill',                  t:'select', o:['yes','no'], flex:1,
                         d:"Cerca automaticamente le puntate precedenti mancanti quando arriva un nuovo episodio. Esempio: se arriva S03E05 ma mancano E01–E04, li cercherà tramite tutti gli indexer configurati. Richiede TMDB configurato."},
                        {k:'amule_gap_fallback', l:'Fallback Gap Fill (aMule)', t:'select', o:['no','yes'], flex:1,
                         d:"Se il Gap Fill non trova nulla tramite gli indexer configurati, tenta automaticamente una ricerca sulla rete eD2k globale usando aMule."},
                        {k:'default_language',   l:'Lingua Default Contenuti',  t:'custom_deflang', flex:2,
                         d:'Lingua audio proposta automaticamente per nuove serie e film aggiunti senza specificarla. Usata anche come filtro di fallback nella ricerca manuale. Lascia vuoto per nessun filtro.'},
                        {k:'jackett_timeout',    l:'Timeout Indexer (sec)',     t:'number', flex:1,
                         d:'Tempo massimo di attesa per le risposte da Jackett/Prowlarr. Alzalo a 45 o 60 se hai tracker lenti (Read timed out). Default: 30.'},
                    ],
                    [
                        {k:'min_free_space_gb',  l:'Spazio Minimo (GB)',        t:'number', flex:1,
                         d:'Soglia di sicurezza disco. Se lo spazio libero scende sotto questo valore (GB), EXTTO blocca i nuovi download per evitare di riempire il disco. Consigliato: almeno 20-50 GB.'},
                        {k:'max_age_days',       l:'Max Age (giorni)',           t:'number', flex:1,
                         d:'Ignora i torrent più vecchi di N giorni. 0 = nessun filtro. Esempio: 365 = ignora torrent caricati oltre 1 anno fa.'},
                        {k:'stop_on_old_page_threshold', l:'Stop Scraping (%)', t:'number', flex:1,
                         d:"Ottimizzazione: se una pagina ha più del X% di torrent già in archivio, smette di scorrere le pagine successive. Esempio: 0.8 = ferma all'80%. Range: 0.0–1.0."},
                    ],
                ]
            },
            {
                title: `🔍 ${t('Logging & Debug')}`,
                rows: [
                    [
                        {k:'debug_duplicates',       l:'Debug Duplicati',  t:'select', o:['no','yes'], flex:1,
                         d:"Aggiunge ai log ogni torrent scartato perché già in archivio. Molto verboso su archivi grandi — usalo solo per diagnosticare problemi specifici."},
                        {k:'debug_blacklisted',      l:'Debug Blacklist',  t:'select', o:['no','yes'], flex:1,
                         d:"Mostra nei log i torrent scartati perché il titolo contiene una parola della Blacklist (cam, ts...). Utile per verificare che le regole di esclusione funzionino."},
                        {k:'debug_quality_rejected', l:'Debug Qualità',    t:'select', o:['no','yes'], flex:1,
                         d:"Mostra nei log i torrent scartati perché la qualità non corrisponde a quella richiesta (es: cercavi 1080p, trovato solo 480p). Aiuta a capire perché una serie non viene scaricata."},
                        {k:'debug_size_rejected',    l:'Debug Dimensione', t:'select', o:['no','yes'], flex:1,
                         d:'Mostra nei log i torrent scartati per dimensione anomala (troppo piccoli o grandi). Utile per diagnosticare falsi negativi su episodi con encoding insolito.'},
                    ],
                ]
            },
            {
                title: `📦 ${t('Database & Archivio')}`,
                rows: [
                    [
                        {k:'archive_cleanup_enabled', l:'Pulizia Archivio Auto',    t:'select', o:['no','yes'], flex:1,
                         d:"Abilita la pulizia automatica dell'archivio al termine di ogni ciclo. Rimuove record più vecchi di 'Età Max Archivio' mantenendo almeno 'Min. Record da Mantenere' voci."},
                        {k:'archive_max_age_days',    l:'Età Max Archivio (giorni)', t:'number', flex:1,
                         d:"Età massima in giorni dei record nell'archivio prima di essere eliminati dalla pulizia automatica. Default: 365. Richiede 'Pulizia Archivio Auto = yes'."},
                        {k:'archive_keep_min',        l:'Min. Record da Mantenere',  t:'number', flex:1,
                         d:"Numero minimo di record da mantenere anche dopo la pulizia. Evita di svuotare completamente l'archivio se tutti i record sono vecchi. Default: 10000."},
                    ],
                ]
            }
        ];

        let html = '';
        categories.forEach(cat => {
            let bodyHtml;
            if (cat.rows) {
                const rowsHtml = cat.rows.map(row => {
                    const fieldsHtml = row.map(f => {
                        const inner = this.renderField({key:f.k, label:f.l, type:f.t, options:f.o, desc:f.d}, settings);
                        return inner.replace('<div class="form-group">', `<div class="form-group" style="flex:${f.flex||1}; min-width:120px; margin:0;">`);
                    }).join('');
                    return `<div style="display:flex; gap:.75rem; align-items:flex-end; flex-wrap:wrap; margin-bottom:.75rem;">${fieldsHtml}</div>`;
                }).join('');
                bodyHtml = `<div class="lt-compact">${rowsHtml}</div>`;
            } else {
                bodyHtml = `<div class="lt-compact form-grid">${cat.fields.map(f => this.renderField({key:f.k, label:f.l, type:f.t, options:f.o, desc:f.d}, settings)).join('')}</div>`;
            }
            html += `<div class="card" style="margin-bottom:1.5rem; grid-column: 1 / -1;">
                        <div class="card-header">${cat.title}</div>
                        <div class="card-body">${bodyHtml}</div>
                    </div>`;
        });

        advContainer.innerHTML = html;

        // Campo speciale per TMDB Language
        const tmdbLang = settings['tmdb_language'] || 'it-IT';
        const presets = ['it-IT', 'en-US', 'es-ES', 'fr-FR', 'de-DE'];
        const isCustom = !presets.includes(tmdbLang);
        const selectVal = isCustom ? 'custom' : tmdbLang;

        html += `
            <div class="form-group" style="grid-column: 1 / -1; margin-top:1.5rem;">
                <label>${t('Lingua TMDB')} <span class="tip" data-tip="${t('Lingua usata da TMDB per i titoli degli episodi e le descrizioni. it-IT = Italiano, en-US = Inglese. Scegli \'Personalizzata\' per un codice BCP-47 diverso.')}"></span></label>
                <select id="tmdb_lang_select" class="form-control" onchange="document.getElementById('tmdb_lang_custom').style.display = this.value === 'custom' ? 'block' : 'none'">
                    <option value="it-IT" ${selectVal==='it-IT'?'selected':''}>${t('Italiano (it-IT)')}</option>
                    <option value="en-US" ${selectVal==='en-US'?'selected':''}>${t('Inglese (en-US)')}</option>
                    <option value="es-ES" ${selectVal==='es-ES'?'selected':''}>${t('Spagnolo (es-ES)')}</option>
                    <option value="fr-FR" ${selectVal==='fr-FR'?'selected':''}>${t('Francese (fr-FR)')}</option>
                    <option value="de-DE" ${selectVal==='de-DE'?'selected':''}>${t('Tedesco (de-DE)')}</option>
                    <option value="custom" ${selectVal==='custom'?'selected':''}>${t('Personalizzata...')}</option>
                </select>
                <input type="text" id="tmdb_lang_custom" class="form-control" style="margin-top:8px; display:${isCustom?'block':'none'};" value="${isCustom ? this.escapeHtml(tmdbLang) : ''}" placeholder="Es: ja-JP (Codice lingua TMDB)">
            </div>
        `;

        advContainer.innerHTML = html;
    },
    
    renderNetworkPorts(settings) {
        const el = document.getElementById('maintenance-network-ports');
        if (!el) return;
        const webPort = settings['web_port'] || '5000';
        const enginePort = settings['engine_port'] || '8889';
        el.innerHTML = `
            <div class="card" style="grid-column: 1 / -1; border: 1px solid var(--danger); margin-top:1.5rem;">
                <div class="card-header" style="color: var(--danger); background: rgba(239,68,68,0.1);">
                    <i class="fa-solid fa-network-wired"></i> ${t('Porte di Rete EXTTO (Richiede Riavvio Globale)')}
                </div>
                <div class="card-body" style="padding-left:1.5rem; padding-right:1.5rem;">
                    <div style="display:grid; grid-template-columns: 320px 1fr; gap:2rem; align-items:start; min-width:0;">

                        <!-- Colonna sinistra: campi -->
                        <div style="display:flex; flex-direction:column; gap:1.5rem;">
                            <p style="font-size:0.85rem; color:var(--text-secondary); margin:0;">
                                <strong>${t('Attenzione')}:</strong> ${t('Modificare queste porte cambia il modo in cui il sistema comunica.')}
                                ${t('Dopo aver salvato, l\'interfaccia smetterà di funzionare. Riavvia extto manualmente.')}
                            </p>
                            <div class="form-group" style="margin:0;">
                                <label>${t('Porta')} Web UI (Browser) <span class="tip" data-tip="${t('La porta a cui ti connetti dal browser (http://tuo-ip:PORTA). Default: 5000')}"></span></label>
                                <input type="number" id="setting-web_port" class="form-input" value="${this._esc(webPort)}" placeholder="5000" min="1024" max="65535">
                            </div>
                            <div class="form-group" style="margin:0;">
                                <label>${t('Porta')} API (${t('Motore Interno')}) <span class="tip" data-tip="${t('La porta nascosta usata dal motore in background per comunicare con l\'interfaccia. Default: 8889')}"></span></label>
                                <input type="number" id="setting-engine_port" class="form-input" value="${this._esc(enginePort)}" placeholder="8889" min="1024" max="65535">
                            </div>
                        </div>

                        <!-- Colonna destra: istruzioni e reset -->
                        <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:var(--radius-md); padding:1rem; font-size:0.82rem; display:flex; flex-direction:column; gap:0.75rem; min-width:0; overflow:hidden;">
                            <div style="font-weight:700; color:var(--text-primary); margin-bottom:2px;">
                                <i class="fa-solid fa-terminal" style="color:var(--danger);"></i> ${t('Dopo aver salvato')}
                            </div>
                            <code style="display:block; padding:6px 10px; background:rgba(0,0,0,0.3); color:var(--danger); border-radius:4px;">sudo systemctl restart extto</code>

                            <div style="border-top:1px solid var(--border); padding-top:0.75rem;">
                                <div style="font-weight:700; color:var(--warning); margin-bottom:6px;">
                                    <i class="fa-solid fa-triangle-exclamation"></i> ${t('Se le porte non funzionano')}
                                </div>
                                <div style="color:var(--text-muted); line-height:1.5;">
                                    ${t('Ripristina i default direttamente sul server con questi comandi SQL:')}
                                </div>
                                <code style="display:block; margin-top:6px; padding:8px 10px; background:rgba(0,0,0,0.3); color:#86efac; border-radius:4px; line-height:1.8; font-size:0.78rem;">sqlite3 extto_config.db<br>UPDATE settings SET value='5000'<br>&nbsp;&nbsp;WHERE key='web_port';<br>UPDATE settings SET value='8889'<br>&nbsp;&nbsp;WHERE key='engine_port';<br>.quit</code>
                                <div style="color:var(--text-muted); margin-top:6px; line-height:1.5;">
                                    ${t('Poi riavvia:')} <code style="color:var(--danger);">sudo systemctl restart extto</code>
                                </div>
                            </div>

                            <div style="border-top:1px solid var(--border); padding-top:0.75rem; color:var(--text-muted); line-height:1.5;">
                                <i class="fa-solid fa-circle-info" style="color:var(--info);"></i>
                                ${t('Porte valide: 1024–65535. Non usare porte già occupate da altri servizi.')}
                            </div>
                        </div>

                    </div>
                </div>
            </div>`;
    },

    async loadConfigForMaintenance() {
        try {
            const res = await fetch(`${API_BASE}/api/config`);
            const data = await res.json();
            if (data.settings) {
                this.renderNetworkPorts(data.settings);
                const trashEl = document.getElementById('trash-path-display');
                if (trashEl) trashEl.textContent = data.settings.trash_path || '(non configurata)';
            }
        } catch(e) {}
        await this.amuleLoadMaintConfig(); // ★ v45
    },

    // ── AMULE MANUTENZIONE ★ v45 ─────────────────────────────────────────────

    async amuleLoadMaintConfig() {
        try {
            const r = await fetch(`${API_BASE}/api/amule/config`);
            if (!r.ok) return;
            const s = await r.json();
            const _set = (id, val) => {
                const el = document.getElementById(id);
                if (!el) return;
                if (el.type === 'checkbox') el.checked = (String(val).toLowerCase() === 'yes' || val === true || val === '1');
                else el.value = val || '';
            };
            _set('amule-cfg-enabled',       s.amule_enabled);
            _set('amule-cfg-host',          s.amule_host);
            _set('amule-cfg-port',          s.amule_port);
            _set('amule-cfg-password',      s.amule_password);
            _set('amule-cfg-service',       s.amule_service);
            _set('amule-cfg-conf-path',     s.amule_conf_path);
            _set('amule-cfg-gap-fill-ed2k', s.gap_fill_ed2k);
            // Campi letti live da amule.conf
            _set('amule-cfg-tcp',       s.amule_tcp_port);
            _set('amule-cfg-udp',       s.amule_udp_port);
            _set('amule-cfg-incoming',  s.amule_incoming);
            _set('amule-cfg-temp',      s.amule_temp);
            this.amuleCheckServiceStatus(false);
            // Punto 3: carica limiti di banda da amule.conf
            try {
                const bwR = await fetch(`${API_BASE}/api/amule/bandwidth`);
                if (bwR.ok) {
                    const bw = await bwR.json();
                    _set('amule-cfg-dl-limit', bw.max_download_kbs ?? 0);
                    _set('amule-cfg-ul-limit', bw.max_upload_kbs   ?? 0);
                }
            } catch(e) { /* non critico */ }
        } catch(e) { console.warn('amuleLoadMaintConfig:', e); }
    },

    async amuleSaveBandwidth() {
        const _getNum = (id) => { const el = document.getElementById(id); return el ? (parseInt(el.value, 10) || 0) : 0; };
        const dl = _getNum('amule-cfg-dl-limit');
        const ul = _getNum('amule-cfg-ul-limit');
        try {
            const r = await fetch(`${API_BASE}/api/amule/bandwidth`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ max_download_kbs: dl, max_upload_kbs: ul })
            });
            const d = await r.json();
            if (d.service_running) {
                const svc = d.service || 'amule-daemon';
                this.showToast('⚠️ Ferma amuled prima di salvare la banda', 'warn');
                this._amuleMaintMsg(
                    `⚠️ <strong>amuled è in esecuzione.</strong><br>` +
                    `Fermalo prima: <code>sudo systemctl stop ${svc}</code>`,
                    'warn'
                );
                return;
            }
            if (d.ok) {
                this.showToast('Limiti banda salvati ✓', 'success');
                this._amuleMaintMsg('✅ ' + (d.message || 'Limiti salvati'), 'success');
            } else {
                this._amuleMaintMsg('❌ ' + (d.error || 'Errore'), 'error');
            }
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    async amuleSaveConfig() {
        const _get = (id) => {
            const el = document.getElementById(id);
            if (!el) return '';
            return el.type === 'checkbox' ? (el.checked ? 'yes' : 'no') : el.value.trim();
        };
        const payload = {
            // Connessione EC → DB
            amule_enabled:      _get('amule-cfg-enabled'),
            amule_host:         _get('amule-cfg-host')      || 'localhost',
            amule_port:         _get('amule-cfg-port')      || '4712',
            amule_password:     _get('amule-cfg-password'),
            amule_service:      _get('amule-cfg-service')   || 'amule-daemon',
            amule_conf_path:    _get('amule-cfg-conf-path'),
            gap_fill_ed2k:      _get('amule-cfg-gap-fill-ed2k'),
            // Porte e cartelle → scritti in amule.conf
            amule_tcp_port:  _get('amule-cfg-tcp'),
            amule_udp_port:  _get('amule-cfg-udp'),
            amule_incoming:  _get('amule-cfg-incoming'),
            amule_temp:      _get('amule-cfg-temp'),
        };
        try {
            const r = await fetch(`${API_BASE}/api/amule/config`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
            });
            const d = await r.json();
            if (d.service_running) {
                // Servizio attivo: blocco con istruzioni chiare
                this.showToast('⚠️ Ferma amuled prima di salvare', 'warn');
                this._amuleMaintMsg(
                    `⚠️ <strong>amuled è in esecuzione.</strong> Le modifiche a amule.conf verrebbero sovrascritte al riavvio.<br>` +
                    `Fermalo prima: <code>sudo systemctl stop ${payload.amule_service}</code><br>` +
                    `Poi torna qui e salva di nuovo.`,
                    'warn'
                );
                this.amuleCheckServiceStatus(false);
                return;
            }
            if (d.ok || d.success) {
                this.showToast('Configurazione aMule salvata ✓', 'success');
                this._amuleMaintMsg('✅ Salvato.' + (d.warning ? ' ⚠ ' + d.warning : ''), d.warning ? 'warn' : 'success');
                this.amuleCheckServiceStatus(false);
            } else {
                this.showToast('Errore: ' + (d.error||''), 'error');
                this._amuleMaintMsg('❌ ' + (d.error||'Errore'), 'error');
            }
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },


    async amuleCheckServiceStatus(showFeedback = true) {
        try {
            const r = await fetch(`${API_BASE}/api/amule/status-service`);
            const d = await r.json();
            const badge = document.getElementById('amule-service-badge');
            if (badge) {
                if (d.active) {
                    badge.style.background = 'rgba(239,68,68,0.18)';
                    badge.style.color      = 'var(--danger)';
                    badge.innerHTML = `<i class="fa-solid fa-circle" style="font-size:0.55rem;"></i> ${d.service}: attivo — ferma prima di salvare`;
                } else {
                    badge.style.background = 'rgba(16,185,129,0.18)';
                    badge.style.color      = 'var(--success)';
                    badge.innerHTML = `<i class="fa-solid fa-circle-stop" style="font-size:0.55rem;"></i> ${d.service}: fermo — pronto per le modifiche`;
                }
            }
            if (showFeedback) {
                this._amuleMaintMsg(
                    d.active
                        ? `⚠️ ${d.service} è in esecuzione. Fermalo con <code>sudo systemctl stop ${d.service}</code> prima di salvare le impostazioni.`
                        : `✅ ${d.service} è fermo. Puoi modificare e salvare le impostazioni.`,
                    d.active ? 'warn' : 'success'
                );
            }
        } catch(e) {
            const badge = document.getElementById('amule-service-badge');
            if (badge) { badge.style.background = ''; badge.style.color = 'var(--text-muted)'; badge.innerHTML = '<i class="fa-solid fa-circle" style="font-size:0.55rem;"></i> Stato sconosciuto'; }
        }
    },

    async amuleInstallEd2kHandler() {
        try {
            const r = await fetch(`${API_BASE}/api/amule/ed2k-install`);
            const d = await r.json();
            if (!d.success) { this.showToast(d.error || 'Errore', 'error'); return; }
            // Mostra comandi di installazione
            const msg = `<div style="font-size:0.82rem;">
                <p>Esegui questi comandi per registrare <code>ed2k://</code> come protocol handler:</p>
                <pre style="background:var(--bg-main);padding:8px;border-radius:4px;overflow:auto;font-size:0.75rem;max-height:200px;">${this._esc(d.install_cmds)}</pre>
                <p style="color:var(--text-muted);margin-top:6px;">Dopo l'installazione, cliccare su un link ed2k:// nel browser lo invierà automaticamente ad aMule.</p>
            </div>`;
            this._amuleMaintMsg(msg, 'info');
        } catch(e) { this.showToast('Errore installazione handler', 'error'); }
    },

    async amuleGenerateService() {
        try {
            const r = await fetch(`${API_BASE}/api/amule/generate-service`);
            const d = await r.json();
            if (!d.success) { this.showToast(d.error||'Errore', 'error'); return; }
            const blob = new Blob([d.content], { type: 'text/plain' });
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href = url; a.download = d.filename; a.click();
            URL.revokeObjectURL(url);
            // Istruzioni complete per la migrazione da system a user service
            this._amuleMaintMsg(`
                ✅ <strong>${d.filename}</strong> scaricato.<br>
                <span style="font-size:0.8rem;line-height:1.7;">
                <strong>Migrazione da system service a user service:</strong><br>
                <code style="display:block;background:var(--bg-main);padding:4px 8px;border-radius:4px;margin:4px 0;font-size:0.74rem;white-space:pre-wrap;">sudo systemctl stop amule-daemon
sudo systemctl disable amule-daemon
mkdir -p ~/.config/systemd/user/
cp ~/Downloads/${d.filename} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ${d.filename.replace('.service','')}</code>
                Poi usa i pulsanti <strong>Avvia</strong> / <strong>Ferma</strong> qui sopra.
                </span>`, 'success');
        } catch(e) { this.showToast('Errore generazione .service', 'error'); }
    },

    _amuleMaintMsg(html, type = 'info') {
        const colors = { success:'var(--success)', error:'var(--danger)', warn:'var(--warning)', info:'var(--text-muted)' };
        const color  = colors[type] || colors.info;
        ['amule-maint-msg', 'amule-settings-msg'].forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            el.style.color = color;
            el.innerHTML   = html;
            clearTimeout(el._t);
            el._t = setTimeout(() => { el.innerHTML = ''; }, 8000);
        });
    },

    renderUrlsAndFilters(config) {
        const s = config.settings || {};
        const urlsEl = document.getElementById('urls-list');
        if (urlsEl) urlsEl.value = (Array.isArray(s.url) ? s.url.join('\n') : s.url) || '';
        const blacklistEl = document.getElementById('blacklist-list');
        if (blacklistEl) blacklistEl.value = (Array.isArray(s.blacklist) ? s.blacklist.join('\n') : s.blacklist) || '';
        const contentFilterEl = document.getElementById('content-filter-list');
        if (contentFilterEl) contentFilterEl.value = (Array.isArray(s.content_filter) ? s.content_filter.join('\n') : s.content_filter) || '';
        const wantedEl = document.getElementById('wantedlist-list');
        if (wantedEl) wantedEl.value = (Array.isArray(s.wantedlist) ? s.wantedlist.join('\n') : s.wantedlist) || '';
        const customEl = document.getElementById('customscore-list');
        if (customEl) customEl.value = (Array.isArray(s.custom_score) ? s.custom_score.join('\n') : s.custom_score) || '';
    },

    renderField(f, settings) {
        const val = (settings[f.key] !== undefined && settings[f.key] !== null) ? settings[f.key] : '';
        const infoIcon = f.desc ? ` <span class="tip" data-tip="${this._esc(t(f.desc))}"></span>` : '';

        if (f.type === 'select') {
            return `<div class="form-group"><label>${t(f.label)}${infoIcon}</label><select class="form-input" id="setting-${f.key}">${f.options.map(o=>`<option value="${o}"${val===o?' selected':''}>${o}</option>`).join('')}</select></div>`;
        } else if (f.type === 'password') {
            return `<div class="form-group">
                <label>${f.label}${infoIcon}</label>
                <div style="display:flex; gap:0.5rem;">
                    <input type="password" class="form-input" id="setting-${f.key}" value="${this._esc(val)}" style="flex:1; font-family:var(--font-mono);">
                    <button type="button" class="btn btn-secondary" style="flex-shrink:0; padding:0 1rem;"
                        onclick="const i=document.getElementById('setting-${f.key}'); i.type=i.type==='password'?'text':'password'; this.innerHTML=i.type==='password'?'<i class=\\'fa-solid fa-eye\\'></i>':'<i class=\\'fa-solid fa-eye-slash\\'></i>';"
                        title="Mostra/Nascondi"><i class="fa-solid fa-eye"></i></button>
                </div>
            </div>`;
        } else if (f.type === 'custom' && f.key === 'rename_format') {
            return this._renderRenameFormatField(val);
        } else if (f.type === 'custom_deflang') {
            // Select lingua default contenuti — costruito da EXTTO_LANGUAGES
            const opts = EXTTO_LANGUAGES.map(l =>
                `<option value="${l.code}"${val===l.code?' selected':''}>${l.label} (${l.code})</option>`
            ).join('');
            return `<div class="form-group">
                <label>${f.label}${infoIcon}</label>
                <select class="form-input" id="setting-default_language">
                    <option value=""${!val?' selected':''}>— Nessun filtro —</option>
                    ${opts}
                </select>
            </div>`;
        }
        if (f.type === 'path') {
            return `<div class="form-group">
                <label>${t(f.label)}${infoIcon}</label>
                <div style="display:flex; gap:8px;">
                    <input type="text" class="form-input" id="setting-${f.key}" value="${this._esc(val)}" style="flex:1;">
                    <button type="button" class="btn btn-secondary btn-small" title="Sfoglia cartelle del server"
                        onclick="event.stopPropagation(); app.openDirBrowser('setting-${f.key}')">
                        <i class="fa-regular fa-folder-open"></i>
                    </button>
                </div>
            </div>`;
        }
        return `<div class="form-group"><label>${t(f.label)}${infoIcon}</label><input type="${f.type}" class="form-input" id="setting-${f.key}" value="${this._esc(val)}"></div>`;
    },

    _renderRenameFormatField(currentVal) {
        const fmt = currentVal || 'base';
        const tmpl = (this._configData && this._configData.settings && this._configData.settings.rename_template) 
                     ? this._configData.settings.rename_template 
                     : '{Serie} - {Stagione}{Episodio} - {Titolo} [{Risoluzione}][{HDR}][{Lingue}]';

        const presets = [
            { id: 'base',     icon: 'fa-file',         label: t('Base'),
              desc: 'Serie - S01E01 - Titolo.mkv',
              note: t('Semplice e pulito.') },
            { id: 'standard', icon: 'fa-file-video',   label: t('Standard'),
              desc: 'Serie (Anno) - S01E01 - Titolo [Qualità][Codec].mkv',
              note: t('Tutte le info tecniche.') },
            { id: 'completo', icon: 'fa-film',         label: t('Completo'),
              desc: 'Serie (Anno) - S01E01 - Titolo [Qualità][Audio][Codec][Lingue].mkv',
              note: t('Tutte le info tecniche.') },
            { id: 'custom',   icon: 'fa-pen-to-square', label: t('Personalizzato'),
              desc: t('Personalizzato'),
              note: t('Massima flessibilità.') },
        ];
        const cards = presets.map(p => {
            const active = fmt === p.id;
            return `<label style="display:flex; flex-direction:column; gap:6px; padding:10px 12px;
                        border:2px solid ${active ? 'var(--primary)' : 'var(--border)'};
                        border-radius:8px; cursor:pointer; background:${active ? 'rgba(99,102,241,0.08)' : 'var(--bg-input)'};
                        transition:border-color .15s, background .15s;"
                    onclick="app._selectRenameFormat('${p.id}')">
                <div style="display:flex; align-items:center; gap:8px;">
                    <input type="radio" name="rename_format_radio" id="rfmt-${p.id}" value="${p.id}" ${active?'checked':''} style="accent-color:var(--primary);">
                    <i class="fa-solid ${p.icon}" style="color:${active?'var(--primary)':'var(--text-muted)'}; width:14px;"></i>
                    <span style="font-weight:600; font-size:0.88rem;">${p.label}</span>
                </div>
                <span style="font-family:var(--font-mono); font-size:0.85rem; color:var(--text-secondary); word-break:break-all;">${p.desc}</span>
                <span style="font-size:0.75rem; color:var(--text-muted);">${p.note}</span>
            </label>`;
        }).join('');

        return `<div class="form-group" style="grid-column:1/-1;">
            <label>${t('Formato Rinomina')} <span class="tip" data-tip="${t('Scegli lo stile del nome file. I formati estesi estraggono i dati tecnici usando mediainfo.')}"></span></label>
            <input type="hidden" id="setting-rename_format" value="${this._esc(fmt)}">
            <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:6px;">
                ${cards}
            </div>
            
            <div id="rename-custom-box" style="display:${fmt === 'custom' ? 'block' : 'none'}; margin-top:10px; padding:10px; background:var(--bg-main); border-radius:6px; border:1px solid var(--border);">
                <label style="font-size:0.85rem; color:var(--text-muted); margin-bottom:5px; display:block;">${t('Personalizzato')}:</label>
                <input type="text" id="setting-rename_template" class="form-control" value="${this._esc(tmpl)}" oninput="app._updateRenameFormatExample('custom')">
                <div style="font-size:0.8rem; color:var(--text-secondary); margin-top:6px; font-family:var(--font-mono);">
                    Tag: <span style="color:var(--primary-light);">{Serie}, {Anno}, {Stagione}, {Episodio}, {Titolo}, {Risoluzione}, {VideoCodec}, {Audio}, {Canali}, {HDR}, {Lingue}</span>
                </div>
                <div style="font-size:0.8rem; color:var(--text-muted); margin-top:8px; background:rgba(255,255,255,0.03); padding:8px 10px; border-radius:4px; border-left: 3px solid var(--warning);">
                    <i class="fa-solid fa-lightbulb" style="color:var(--warning);"></i> ${t('Semplice e pulito.')}: <code>[]</code> / <code>()</code>
                </div>
            </div>

            <div id="rename-format-example" style="margin-top:10px; padding:8px 12px; background:var(--bg-secondary);
                border-radius:6px; border:1px solid var(--border); font-family:var(--font-mono);
                font-size:0.9rem; color:var(--text-secondary); word-break:break-all;">
                <span style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase; letter-spacing:.5px;">${t('Anteprima Rinomina TMDB')}:</span><br>
                <span id="rename-format-example-text" style="color:var(--text-primary); font-weight: 600;"></span>
                <span id="rename-format-mediainfo-warn" style="display:none; color:var(--warning); font-size:0.7rem; margin-left:8px;">
                    <i class="fa-solid fa-triangle-exclamation"></i> pymediainfo non disponibile sul server
                </span>
            </div>
        </div>`;
    },

    _renameFormatExamples: null,

    async _selectRenameFormat(fmt) {
        const fmtEl = document.getElementById('setting-rename_format');
        if (fmtEl) fmtEl.value = fmt;
        document.querySelectorAll('[name="rename_format_radio"]').forEach(r => r.checked = r.value === fmt);
        
        const box = document.getElementById('rename-custom-box');
        if (box) showIf(box, fmt === 'custom');

        // Aggiorna stile card
        ['base','standard','completo','custom'].forEach(f => {
            const lbl = document.getElementById(`rfmt-${f}`)?.closest('label');
            if (!lbl) return;
            const active = f === fmt;
            lbl.style.borderColor  = active ? 'var(--primary)' : 'var(--border)';
            lbl.style.background   = active ? 'rgba(99,102,241,0.08)' : 'var(--bg-input)';
            const icon = lbl.querySelector('i');
            if (icon) icon.style.color = active ? 'var(--primary)' : 'var(--text-muted)';
        });
        await this._updateRenameFormatExample(fmt);
    },

    async _updateRenameFormatExample(fmt) {
        try {
            const tmplInput = document.getElementById('setting-rename_template');
            const tmpl = tmplInput ? tmplInput.value : '';
            const res  = await fetch(`${API_BASE}/api/rename-format-preview?tmpl=${encodeURIComponent(tmpl)}`);
            const data = await res.json();
            if (data.success) {
                this._renameFormatExamples = data.examples;
                this._mediainfoAvailable = data.mediainfo_available;
            }
        } catch(e) { return; }

        const exEl  = document.getElementById('rename-format-example-text');
        const warnEl = document.getElementById('rename-format-mediainfo-warn');
        if (exEl && this._renameFormatExamples) {
            exEl.textContent = this._renameFormatExamples[fmt] || '';
        }
        if (warnEl) {
            const needsMediainfo = fmt === 'standard' || fmt === 'completo';
            showIf(warnEl, needsMediainfo && !this._mediainfoAvailable);
        }
    },

    // ========================================================================
    // ACTIONS & SAVING
    // ========================================================================
    async _saveFullConfig(overrides = {}) {
        const cfg = this._configData || {};
        const settings = Object.assign({}, cfg.settings || {}, overrides.settings || {});
        const series   = overrides.series !== undefined ? overrides.series : (cfg.series || []);
        const res = await fetch(`${API_BASE}/api/config`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({settings, series})
        });
        const json = await res.json();
        if (json.success && this._configData) {
            this._configData.settings = settings;
            if (overrides.series !== undefined) this._configData.series = series;
        }
        return json;
    },

    async testNotification() {
        this.showToast(t('Sending test...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/test-notification`, {method: 'POST'});
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
            } else {
                this.showToast(data.error || t('Notification configuration error'), 'error');
            }
        } catch (e) { console.error(e); }
    },
    
    async saveArchiveCreds() {
        const lines = document.getElementById('archive-cred-list').value.split('\n').filter(x=>x);
        try {
             const res = await fetch(`${API_BASE}/api/config`); const c = await res.json(); c.settings.archive_cred = lines;
             await this._saveFullConfig({settings: c.settings});
             this.showToast(t('Credentials Saved'), 'success');
        } catch(e){}
    },
  
    async addAllFromArchive() {
        const root = document.getElementById('archive-root-select')?.value;
        try {
            const res = await fetch(`${API_BASE}/api/config/add_all_from_archive`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root})});
            const d = await res.json();
            this.showToast(t(d.message) || t('Done'), 'success');
        } catch(e){}
    },
    
    async submitMagnet(e) {
        e.preventDefault();
        const m = document.getElementById('magnet-input').value.trim();
        const f = document.getElementById('torrent-file').files[0];
        const dn = document.getElementById('magnet-download-now').checked;
        const sp = document.getElementById('add-magnet-save-path') ? document.getElementById('add-magnet-save-path').value.trim() : '';
        const noRename = document.getElementById('magnet-no-rename')?.checked || false;
        
        if(!m && !f) return this.showToast(t('Inserisci un link o scegli un file'), 'error');
        
        if(f) {
            try {
                this.showToast(t('Caricamento .torrent in corso...'), 'info');
                const b64 = await this.fileToBase64(f);
                const res = await fetch(`${API_BASE}/api/upload-torrent`, {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({filename:f.name, data:b64, download_now:dn, save_path:sp})
                });
                const d = await res.json();
                this.showToast(d.success ? t('Torrent caricato!') : d.error, d.success ? 'success' : 'error');
                if (d.success) {
                    if (d.hash && noRename) await this._saveNoRename(d.hash, true);
                    if (this._torrentPollId !== null) await this.loadTorrents();
                }
            } catch(err) {
                this.showToast(t('Errore caricamento file'), 'error');
            }
        } else {
            const tag = document.getElementById('magnet-tag')?.value?.trim() || '';
            await this.sendMagnetToClient(m, sp, dn, noRename, tag);
        }
        
        this.closeModal('add-magnet-modal');
        document.getElementById('magnet-form').reset();
    },
    
    async sendMagnetToClient(magnet, savePath = '', downloadNow = true, noRename = false, tag = '') {
        try {
            if (!this._configData) {
                const confRes = await fetch(`${API_BASE}/api/config`);
                this._configData = await confRes.json();
            }

            if (magnet.startsWith('http://') || magnet.startsWith('https://')) {
                this.showToast(t('Scaricamento file .torrent in corso...'), 'info');
                const res = await fetch(`${API_BASE}/api/fetch-url`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: magnet})
                });
                const data = await res.json();
                
                if (data.success) {
                    const upRes = await fetch(`${API_BASE}/api/upload-torrent`, {
                        method:'POST', headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({filename: data.filename, data: data.data, download_now: downloadNow, save_path: savePath})
                    });
                    const upData = await upRes.json();
                    if (upData.success) {
                        this.showToast(t('Torrent aggiunto con successo!'), 'success');
                        if (upData.hash) await this._saveTag(upData.hash, tag || 'Manuale');
                        if (upData.hash && noRename) await this._saveNoRename(upData.hash, true);
                        if (this._torrentPollId !== null) await this.loadTorrents();
                    } else {
                        this.showToast(upData.error || t('Errore invio torrent'), 'error');
                    }
                } else if (data.is_magnet) {
                    return this.sendMagnetToClient(data.magnet, savePath, downloadNow, noRename, tag); 
                } else {
                    this.showToast(data.error || t('Errore download URL'), 'error');
                }
                return; 
            }

            const ltEnabled = this._configData?.settings?.libtorrent_enabled === 'yes';
            if (ltEnabled) {
                const res = await fetch(`${API_BASE}/api/torrents/add`, {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({magnet, save_path: savePath, tag: tag || 'Manuale'})
                });
                if (res.ok) {
                    const d = await res.json();
                    if (d.ok) {
                        this.showToast(t('Torrent inviato a libtorrent'), 'success');
                        const _h = d.hash || this._hashFromMagnet(magnet);
                        await this._saveTag(_h, tag || 'Manuale');
                        if (_h && noRename) await this._saveNoRename(_h, true);
                        if (this._torrentPollId !== null) await this.loadTorrents();
                        return;
                    }
                }
            }
            
            const res = await fetch(`${API_BASE}/api/send-magnet`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({magnet, save_path: savePath, tag: tag || 'Manuale'})
            });
            const d = await res.json();
            if (d.success) {
                const _h = this._hashFromMagnet(magnet);
                await this._saveTag(_h, tag || 'Manuale');
                if (_h && noRename) await this._saveNoRename(_h, true);
                if (this._torrentPollId !== null) await this.loadTorrents();
            }
            this.showToast(t(d.message || d.error), d.success ? 'success' : 'error');
        } catch(e) {
            this.showToast(t('Errore di comunicazione col server'), 'error');
        }
    },

    // Series/Movie Logic
    async deleteSeriesFromConfig(name, seriesId) {
        if (!confirm(`${t('Eliminare la serie')} "${name}"?\n\n${t('Verranno rimossi dal DB anche gli episodi e i feed correlati.')}`)) return;
        try {
            let res;
            if (seriesId && seriesId > 0) {
                res = await fetch(`${API_BASE}/api/series/${seriesId}`, { method: 'DELETE' });
            } else {
                res = await fetch(`${API_BASE}/api/series/by-name/${encodeURIComponent(name)}`, { method: 'DELETE' });
            }
            const data = await res.json();
            if (!data.success) {
                this.showToast(t('Errore eliminazione') + ': ' + (data.error || ''), 'error');
                return;
            }
            this.showToast(`${t('Series')} "${name}" ${t('deleted')}`, 'success');
            this.loadSeries();
        } catch(e) {
            this.showToast(t('Deletion error'), 'error');
        }
    },
    async deleteMovieFromConfig(name) {
        if (!confirm(`${t('Eliminare')} ${name}?`)) return;
        try {
            const r = await fetch(`${API_BASE}/api/config`);
            if (!r.ok) throw new Error('Errore lettura config');
            const c = await r.json();
            c.movies = c.movies.filter(m => m.name !== name);
            const saveRes = await fetch(`${API_BASE}/api/config/movies`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({movies:c.movies})});
            if (!saveRes.ok) throw new Error(t('Save error'));
            this.loadMovies();
            this.showToast(t('Film eliminato'), 'success');
        } catch (err) {
            this.showToast(t(t('Deletion error')) + ': ' + err.message, 'error');
        }
    },
    async deleteMovie(id) { if(confirm(t('Eliminare?'))) { await fetch(`${API_BASE}/api/movies/${id}`, {method:'DELETE'}); this.loadMovies(); } },
    
    // Episode actions
    async downloadEpisode(id) { const ep=this.currentEpisodes.find(e=>e.id===id); if(ep?.magnet_link) this._promptNoRename(ep.magnet_link, '', true, ep.title || ep.magnet_link.substring(0,60)); },
    async copyEpisodeMagnet(id) { const ep=this.currentEpisodes.find(e=>e.id===id); if(ep?.magnet_link) this.copyMagnet(ep.magnet_link); },
    async redownloadEpisode(id) {
        if (!confirm(t('Riscaricare?'))) return;
        try {
            const res = await fetch(`${API_BASE}/api/episodes/${id}/redownload`, {method:'POST'});
            if (!res.ok) throw new Error('HTTP ' + res.status);
            this.showEpisodes(this.currentSeriesId, this.currentSeriesName);
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },
    async ignoreEpisode(id) {
        if (!confirm(t('Forzare mancante?'))) return;
        try {
            const res = await fetch(`${API_BASE}/api/episodes/${id}/ignore`, {method:'POST'});
            if (!res.ok) throw new Error('HTTP ' + res.status);
            this.showEpisodes(this.currentSeriesId, this.currentSeriesName);
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },
    async forceMissingEpisode(id) {
        if (!confirm(t('Forzare mancante?'))) return;
        try {
            const res = await fetch(`${API_BASE}/api/episodes/${id}/force-missing`, {method:'POST'});
            if (!res.ok) throw new Error('HTTP ' + res.status);
            this.showEpisodes(this.currentSeriesId, this.currentSeriesName);
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },

    async showFeedMatches(season, episode, btn) {
        // Rimuove eventuali popup/overlay precedenti
        document.querySelectorAll('.fmp-popup, .fmp-overlay').forEach(p => p.remove());

        const epStr = `S${String(season).padStart(2,'0')}E${String(episode).padStart(2,'0')}`;

        // Overlay scuro
        const overlay = document.createElement('div');
        overlay.className = 'fmp-overlay';
        document.body.appendChild(overlay);

        const popup = document.createElement('div');
        popup.className = 'fmp-popup';
        popup.innerHTML = `
        <div class="fmp-header">
            <span class="fmp-header-title">
                <i class="fa-solid fa-satellite-dish"></i>Feed matches — ${epStr}
            </span>
            <button id="fmp-close" class="fmp-close-btn">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>
        <div id="fmp-body" class="fmp-body">
            <div class="fmp-empty"><i class="fa-solid fa-spinner fa-spin"></i> ${t('Caricamento...')}</div>
        </div>`;
        document.body.appendChild(popup);

        const closeFmp = () => document.querySelectorAll('.fmp-popup, .fmp-overlay').forEach(p => p.remove());
        popup.querySelector('#fmp-close').addEventListener('click', closeFmp);
        overlay.addEventListener('click', closeFmp);

        try {
            const res = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/feed-matches/${season}/${episode}`);
            const matches = await res.json();
            const body = popup.querySelector('#fmp-body');
            if (!matches || matches.length === 0) {
                body.innerHTML = `<div class="fmp-empty">${t('Nessun risultato')}</div>`;
                return;
            }
            const failLabels = {
                'downloaded':    ['var(--success)',  'fa-circle-down',  'Scaricato'],
                'below_quality': ['var(--warning)',  'fa-circle-xmark', t('Qualità bassa')],
                'above_quality': ['#a78bfa',         'fa-circle-xmark', t('Qualità troppo alta')],
                'lang_mismatch': ['var(--danger)',   'fa-language',     t('Lingua errata')],
                'blacklisted':   ['var(--danger)',   'fa-ban',          t('Blacklist')],
            };

            // Header tabella
            body.innerHTML = `
                <div class="fmp-table-header">
                    <div>${t('Titolo')}</div>
                    <div class="fmp-table-header-esito">${t('RISULTATO')}</div>
                    <div class="fmp-table-header-score">${t('SCORE')}</div>
                    <div class="fmp-table-header-action">${t('AZIONI')}</div>
                </div>`;

            // Righe costruite con DOM per evitare problemi di escape nelle stringhe magnet
            matches.forEach(m => {
                const [color, icon, label] = failLabels[m.fail_reason] || ['var(--info)', 'fa-clock', m.fail_reason || '?'];
                const dateStr = m.found_at ? new Date(m.found_at).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';

                const row = document.createElement('div');
                row.className = 'fmp-row';

                const titleCol = document.createElement('div');
                titleCol.className = 'fmp-col-title';
                titleCol.title = m.title || '';
                titleCol.innerHTML = `<div class="fmp-date">${dateStr}</div>`;
                const titleSpan = document.createElement('span');
                titleSpan.textContent = m.title || '';
                titleCol.appendChild(titleSpan);

                const esitoCol = document.createElement('div');
                esitoCol.className = 'fmp-col-esito';
                esitoCol.innerHTML = `<span style="color:${color};font-size:.85rem;"><i class="fa-solid ${icon}"></i> ${label}</span>`;

                const scoreCol = document.createElement('div');
                scoreCol.className = 'fmp-col-score';
                scoreCol.textContent = m.quality_score ?? '-';

                const actionCol = document.createElement('div');
                actionCol.className = 'fmp-col-action';
                if (m.magnet) {
                    const dlBtn = document.createElement('button');
                    dlBtn.className = 'btn btn-primary fmp-dl-btn';
                    dlBtn.title = 'Aggiunge questo torrent al client configurato';
                    dlBtn.innerHTML = '<i class="fa-solid fa-download"></i> Scarica';
                    dlBtn.addEventListener('click', () => {
                        app.addMagnetFromFeed(m.magnet, m.title);
                        closeFmp();
                    });
                    actionCol.appendChild(dlBtn);
                } else {
                    actionCol.innerHTML = '<span style="color:var(--text-muted);font-size:.85rem;">—</span>';
                }

                row.appendChild(titleCol);
                row.appendChild(esitoCol);
                row.appendChild(scoreCol);
                row.appendChild(actionCol);
                body.appendChild(row);
            });

        } catch(e) {
            popup.querySelector('#fmp-body').innerHTML = `<div class="fmp-empty fmp-error">Errore: ${e.message}</div>`;
        }
    },
    
    // --- NUOVO: POLLING PER LA RINOMINA ---
    _renamePollInterval: null,

    startRenamePolling(btnSelector, defaultText) {
        if (this._renamePollInterval) clearInterval(this._renamePollInterval);
        
        const btn = document.querySelector(btnSelector);
        if (!btn) return;

        this._renamePollInterval = setInterval(async () => {
            try {
                const res = await fetch(`${API_BASE}/api/rename-progress`);
                const data = await res.json();
                
                if (data.status === 'working') {
                    // Mostra: 🔄 3/15 - Analisi file...
                    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${data.current}/${data.total} - ${this.escapeHtml(data.msg)}`;
                } else if (data.status === 'idle') {
                    // Ha finito, ferma il timer
                    clearInterval(this._renamePollInterval);
                    btn.innerHTML = defaultText;
                    btn.disabled = false;
                }
            } catch (e) {
                clearInterval(this._renamePollInterval);
                btn.innerHTML = defaultText;
                btn.disabled = false;
            }
        }, 1000); // Chiede al server ogni secondo esatto
    },
    // ---------------------------------------

    async renamePreviewForceAll() {
        this.closeRenameModal();
        const btnSelector = 'button[onclick="app.bulkRenameEpisodes()"]';
        const btn = document.querySelector(btnSelector);
        const originalHtml = btn ? btn.innerHTML : '';
        if (btn) { btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Riprocesso...'; btn.disabled = true; }
        this.showToast(t('Reprocessing all files...'), 'info');
        this.startRenamePolling(btnSelector, originalHtml);
        try {
            const res = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/rename-preview?force=1`);
            const data = await res.json();
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }
            if (!data.success) { this.showToast(data.error, 'error'); return; }
            this._lastRenamePreview = data.preview;
            if (data.preview.length === 0) {
                this.showToast(t('Tutti i file sono già nel formato corretto!'), 'success');
                return;
            }
            let html = `<div class="table-row table-header" style="display:grid; grid-template-columns: 1fr 1fr; padding: 1rem;">
                <div>${t('Nome Attuale (Vecchio)')}</div><div>${t('Nuovo Nome (TMDB)')}</div>
            </div>`;
            data.preview.forEach(item => {
                html += `<div class="table-row" style="display:grid; grid-template-columns: 1fr 1fr; gap:15px; padding: 1rem;">
                    <div style="color:var(--danger); word-break:break-all; font-family:var(--font-mono); font-size:0.85rem;"><i class="fa-solid fa-file-video"></i> ${this.escapeHtml(item.old)}</div>
                    <div style="color:var(--success); word-break:break-all; font-family:var(--font-mono); font-size:0.85rem; font-weight:700;"><i class="fa-solid fa-arrow-right"></i> ${this.escapeHtml(item.new)}</div>
                </div>`;
            });
            document.getElementById('rename-preview-list').innerHTML = html;
            const _execBtn1 = document.getElementById('rename-execute-btn');
            if (_execBtn1) { _execBtn1.disabled = false; _execBtn1.innerHTML = `<i class="fa-solid fa-check"></i> ${t('Esegui Rinomina')}`;}
            document.getElementById('rename-preview-modal').classList.add('active');
        } catch(e) {
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }
            this.showToast(t('Errore riprocessamento'), 'error');
        }
    },

    async bulkRenameEpisodes() {
        const btnSelector = 'button[onclick="app.bulkRenameEpisodes()"]';
        const btn = document.querySelector(btnSelector);
        let originalHtml = '';
        if (btn) {
            originalHtml = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Inizializzazione...';
            btn.disabled = true;
        }

        this.showToast(t('Analysis started. Please wait...'), 'info');
        
        // AVVIA IL CONTATORE VISIVO!
        this.startRenamePolling(btnSelector, originalHtml);
        
        try {
            const res = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/rename-preview`);
            const data = await res.json();
            
            // Ferma il polling preventivamente in caso di errori rapidi
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }

            if (!data.success) {
                this.showToast(data.error, 'error');
                return;
            }
            
            if (data.mediainfo_warning) {
                this.showToast('⚠️ ' + data.mediainfo_warning, 'warning');
            }

            const alreadyOk = data.already_ok || [];

            // Se ci sono file già ok, chiedi SUBITO prima di mostrare il modal
            if (alreadyOk.length > 0 && data.preview.length > 0) {
                const forceAll = confirm(`🔍 ${t('Analysis started. Please wait...')}:\n\n✅ ${alreadyOk.length} ${t('Tutti i file sono già rinominati correttamente.')}\n✏️  ${data.preview.length} ${t('Esegui Rinomina')}\n\n[OK] = ${t('Tutto')}\n[${t('Annulla')}] = ${data.preview.length}`);
                if (forceAll) {
                    // I dati sono già disponibili dalla prima analisi: non serve una seconda chiamata.
                    // I file "già ok" vengono aggiunti in testa alla preview con old==new
                    // così appaiono nella lista ma rename-execute li salta (os.rename su stesso path è no-op).
                    const alreadyOkAsItems = (data.already_ok || []).map(f => ({ old: f, new: f }));
                    data.preview = [...alreadyOkAsItems, ...data.preview];
                    data.already_ok = [];  // spostati in preview, svuota per non mostrarli doppi
                }
            } else if (data.preview.length === 0) {
                if (alreadyOk.length > 0) {
                    const forceRedo = confirm(`✅ ${t('Tutti i file sono già rinominati correttamente.')}\n\n${t('Esegui Rinomina')}?`);
                    if (!forceRedo) {
                        this.showToast(t('Tutti i file sono già rinominati correttamente.'), 'success');
                        return;
                    }
                    this.startRenamePolling(btnSelector, originalHtml);
                    const res2 = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/rename-preview?force=1`);
                    if (this._renamePollInterval) clearInterval(this._renamePollInterval);
                    const data2 = await res2.json();
                    if (!data2.success) { this.showToast(data2.error, 'error'); return; }
                    this._lastRenamePreview = data2.preview;
                    Object.assign(data, data2);
                } else {
                    this.showToast(t('Tutti i file sono già rinominati in modo perfetto!'), 'success');
                    return;
                }
            }

            this._lastRenamePreview = data.preview; // <--- AGGIUNGI QUESTA RIGA
            
            let html = '';
            if (data.mediainfo_warning) {
                html += `<div style="background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.4);border-radius:6px;padding:10px 14px;margin-bottom:12px;color:#f59e0b;font-size:0.85rem;">
                    <i class="fa-solid fa-triangle-exclamation"></i> <b>Attenzione:</b> ${this.escapeHtml(data.mediainfo_warning)}
                </div>`;
            }
            if (alreadyOk.length > 0) {
                html += `<div style="background:rgba(37,99,235,.1);border:1px solid rgba(37,99,235,.3);border-radius:6px;padding:10px 14px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <span style="color:var(--text-secondary);font-size:0.85rem;"><i class="fa-solid fa-circle-check" style="color:var(--success);margin-right:6px;"></i>${alreadyOk.length} ${t('Tutti i file sono già rinominati correttamente.')}</span>
                    <button class="btn btn-small btn-secondary" onclick="app.renamePreviewForceAll()" style="white-space:nowrap;"><i class="fa-solid fa-arrows-rotate"></i> ${t('Esegui Rinomina')}</button>
                </div>`;
            }

            html += `<div class="table-row table-header" style="display:grid; grid-template-columns: 1fr 1fr; padding: 1rem;">
                <div>${t('Nome Attuale (Vecchio)')}</div><div>${t('Nuovo Nome (TMDB)')}</div>
            </div>`;
            
            data.preview.forEach(item => {
                html += `<div class="table-row" style="display:grid; grid-template-columns: 1fr 1fr; gap:15px; padding: 1rem;">
                    <div style="color:var(--danger); word-break:break-all; font-family:var(--font-mono); font-size:0.85rem;">
                        <i class="fa-solid fa-file-video"></i> ${this.escapeHtml(item.old)}
                    </div>
                    <div style="color:var(--success); word-break:break-all; font-family:var(--font-mono); font-size:0.85rem; font-weight:700;">
                        <i class="fa-solid fa-arrow-right"></i> ${this.escapeHtml(item.new)}
                    </div>
                </div>`;
            });
            
            document.getElementById('rename-preview-list').innerHTML = html;
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }
            const _execBtn2 = document.getElementById('rename-execute-btn');
            if (_execBtn2) { _execBtn2.disabled = false; _execBtn2.innerHTML = `<i class="fa-solid fa-check"></i> ${t('Esegui Rinomina')}`;}
            document.getElementById('rename-preview-modal').classList.add('active');
            
        } catch (e) {
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }
            this.showToast(t('Error generating preview'), 'error');
        }
    },

    

    
    async confirmBulkRename() {
        // Usa il selettore esatto per trovare il bottone blu dentro il popup
        const btnSelector = 'button[onclick="app.confirmBulkRename()"]';
        const btn = document.querySelector(btnSelector);
        let originalHtml = '';
        if (btn) {
            originalHtml = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Avvio Rinomina...';
            btn.disabled = true;
        }

        this.showToast(t('Physical renaming in progress...'), 'info');
        
        // AVVIA IL CONTATORE VISIVO!
        this.startRenamePolling(btnSelector, originalHtml);
        
        try {
            const res = await fetch(`${API_BASE}/api/series/${this.currentSeriesId}/rename-execute`, { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preview: this._lastRenamePreview || [] })
            });
            const data = await res.json();
            
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            this.closeRenameModal();
            
            if (data.success) {
                let msg;
                if (data.renamed !== undefined) {
                    // Nuovo formato strutturato
                    if (data.removed > 0) {
                        msg = `${data.renamed} ${t('file rinominati')}. ${t('Rimossi')} ${data.removed} ${t('doppioni inferiori/cadaveri')}!`;
                    } else {
                        msg = `${data.renamed} ${t('file rinominati con successo')}!`;
                    }
                } else {
                    // Fallback per backend non ancora aggiornato
                    msg = t(data.message) || data.message;
                }
                this.showToast(msg, 'success');
                this.showEpisodes(this.currentSeriesId, this.currentSeriesName);
            } else {
                this.showToast(data.error, 'error');
            }
        } catch (e) {
            if (this._renamePollInterval) clearInterval(this._renamePollInterval);
            if (btn) { btn.innerHTML = originalHtml; btn.disabled = false; }
            this.showToast(t('Server communication error'), 'error');
        }
    },

    // downloadFromArchive e copyFromArchive ora gestiti inline nelle righe
    copyMagnet(m) {
        if (!m) { this.showToast(t('Nessun magnet disponibile'), 'error'); return; }
        if (navigator.clipboard) {
            navigator.clipboard.writeText(m).then(() => this.showToast(t('Magnet copied to clipboard'), 'success'))
                .catch(() => this._copyFallback(m));
        } else {
            this._copyFallback(m);
        }
    },
    _copyFallback(text) {
        const t = document.createElement('textarea');
        t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
        document.body.appendChild(t); t.focus(); t.select();
        try { document.execCommand('copy'); this.showToast(t('Magnet copied to clipboard'), 'success'); }
        catch(e) { this.showToast(t('Copy failed'), 'error'); }
        document.body.removeChild(t);
    },

    // Modals
    showSeriesEditor() {
        // Reset lingua al valore default prima di aprire (evita che rimanga quello dell'ultima serie)
        const langSel = document.getElementById('series-language-preset');
        if (langSel) {
            const defLang = app._primaryLang || '';
            if (defLang && [...langSel.options].some(o => o.value === defLang)) {
                langSel.value = defLang;
            } else if (langSel.options.length > 0) {
                langSel.value = langSel.options[0].value;
            }
            const customEl = document.getElementById('series-language-custom');
            if (customEl) hideEl(customEl);
        }
        // Reset sottotitoli
        const subSel = document.getElementById('series-subtitle-preset');
        if (subSel) {
            subSel.value = '';
            const subCustom = document.getElementById('series-subtitle-custom');
            if (subCustom) hideEl(subCustom);
        }
        document.getElementById('series-editor-modal').classList.add('active');
    },
    showMoviesEditor() {
        // Reset lingua al valore default prima di aprire (come showSeriesEditor)
        const langSel = document.getElementById('movie-language-preset');
        if (langSel) {
            const defLang = app._primaryLang || '';
            if (defLang && [...langSel.options].some(o => o.value === defLang)) {
                langSel.value = defLang;
            } else if (langSel.options.length > 0) {
                langSel.value = langSel.options[0].value;
            }
            // Nascondi campo custom
            const customEl = document.getElementById('movie-language-custom');
            if (customEl) hideEl(customEl);
        }
        // Reset sottotitoli
        const subSel = document.getElementById('movie-subtitle-preset');
        if (subSel) {
            subSel.value = '';
            const subCustom = document.getElementById('movie-subtitle-custom');
            if (subCustom) hideEl(subCustom);
        }
        document.getElementById('movie-editor-modal').classList.add('active');
    },

    // Popola un <select> con tag da tag_dir_rules + tag storici da torrent_meta
    async _populateTagSelect(sel, selectedTag) {
        if (!sel) return;
        try {
            const [rulesRes, tagsRes] = await Promise.all([
                fetch(`${API_BASE}/api/tag-dir-rules`).then(r => r.json()).catch(() => []),
                fetch(`${API_BASE}/api/torrent-tags`).then(r => r.json()).catch(() => ({}))
            ]);
            const rules = Array.isArray(rulesRes) ? rulesRes : [];
            this._tagDirRules = rules;
            const historicTags = [...new Set(Object.values(tagsRes).filter(Boolean))].sort();
            const ruleTagSet = new Set(rules.map(r => r.tag));
            sel.innerHTML = '<option value="">— Nessun tag —</option>';
            rules.forEach(r => {
                if (!r.tag) return;
                const opt = document.createElement('option');
                opt.value = r.tag;
                opt.textContent = r.tag;
                if (r.final_dir) opt.title = '→ ' + r.final_dir;
                sel.appendChild(opt);
            });
            historicTags.forEach(t => {
                if (ruleTagSet.has(t)) return;
                const opt = document.createElement('option');
                opt.value = t; opt.textContent = t;
                sel.appendChild(opt);
            });
            sel.value = selectedTag || '';
        } catch(e) {}
    },

    showAddMagnetModal() {
        document.getElementById('add-magnet-modal').classList.add('active');
        const sel = document.getElementById('magnet-tag');
        const hint = document.getElementById('magnet-tag-hint');
        if (hint) hint.textContent = '';
        this._populateTagSelect(sel, '');
    },
    showAddMagnetTorrent() { this.showAddMagnetModal(); },
    _onMagnetTagChange(tag) {
        const hint = document.getElementById('magnet-tag-hint');
        const spInput = document.getElementById('add-magnet-save-path');
        if (!tag) {
            if (hint) hint.textContent = '';
            return;
        }
        const rule = (this._tagDirRules || []).find(r => r.tag === tag);
        if (!rule) {
            if (hint) hint.textContent = '';
            return;
        }
        // Se c'è una temp_dir, preimposta la cartella di download temporanea
        if (rule.temp_dir && spInput && !spInput.value.trim()) {
            spInput.value = rule.temp_dir;
        }
        // Hint informativo
        if (hint) {
            const parts = [];
            if (rule.temp_dir) parts.push('Temp: ' + rule.temp_dir);
            if (rule.final_dir) parts.push('Finale: ' + rule.final_dir);
            hint.textContent = parts.length ? parts.join('  →  ') : '';
        }
    },


    fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = error => reject(error);
        });
    },

    async addMagnetFromFeed(magnet, title) {
        if (!magnet) { this.showToast('Magnet non disponibile', 'error'); return; }
        this._promptNoRename(magnet, '', true, title || '', 'Film');
    },
    handleLanguageChange(prefix) {
        // Mappa prefix → id select e id input custom
        const map = {
            'series':      ['series-language-preset',  'series-language-custom'],
            'movie':       ['movie-language-preset',   'movie-language-custom'],
            'edit-series': ['edit-series-language',    'edit-series-language-custom'],
            'edit-movie':  ['edit-movie-language',     'edit-movie-language-custom'],
            'extto-edit': ['extto-edit-language',    'extto-edit-language-custom'],
            'radarr-edit': ['radarr-edit-language',    'radarr-edit-language-custom'],
        };
        const ids = map[prefix];
        if (!ids) return;
        const v = document.getElementById(ids[0])?.value;
        const customEl = document.getElementById(ids[1]);
        if (customEl) showIf(customEl, v === 'custom');
    },
    handleMovieLanguageChange() { this.handleLanguageChange('movie'); },

    handleSubtitleChange(prefix) {
        const selectId = `${prefix}-subtitle-preset`;
        const customId = `${prefix}-subtitle-custom`;
        const v = document.getElementById(selectId)?.value;
        const customEl = document.getElementById(customId);
        if (customEl) showIf(customEl, v === 'custom');
    },
    // === DIR BROWSER ===
    _dirBrowserTarget: null,
    _dirBrowserPath: '/',

    openDirBrowser(targetInputId, evt) {
        if (evt) evt.stopPropagation();
        this._dirBrowserTarget = targetInputId;
        let currentVal = '/';
        const tdrMatch = targetInputId.match(/^__tdr_(temp|final)_(\d+)__$/);
        if (tdrMatch) {
            const tipo = tdrMatch[1];
            const idx  = parseInt(tdrMatch[2]);
            currentVal = (this._tagDirRules?.[idx]?.[tipo === 'temp' ? 'temp_dir' : 'final_dir']) || '/';
        } else {
            currentVal = document.getElementById(targetInputId)?.value || '/';
        }
        document.getElementById('dir-browser-modal').classList.add('active');
        this._loadDirBrowser(currentVal || '/');
    },

    browseArchivePath() {
        this.openDirBrowser('series-archive-path');
    },

    closeDirBrowser() {
        document.getElementById('dir-browser-modal').classList.remove('active');
    },

    confirmDirBrowser() {
        if (this._dirBrowserTarget) {
            // Gestione speciale per le regole tag-dir: id virtuale __tdr_{tipo}_{idx}__
            const tdrMatch = this._dirBrowserTarget.match(/^__tdr_(temp|final)_(\d+)__$/);
            if (tdrMatch) {
                const tipo = tdrMatch[1];
                const idx  = parseInt(tdrMatch[2]);
                if (this._tagDirRules && this._tagDirRules[idx] !== undefined) {
                    if (tipo === 'temp')  this._tagDirRules[idx].temp_dir  = this._dirBrowserPath;
                    if (tipo === 'final') this._tagDirRules[idx].final_dir = this._dirBrowserPath;
                    this._renderTagDirRulesTable();
                }
            } else {
                const el = document.getElementById(this._dirBrowserTarget);
                if (el) el.value = this._dirBrowserPath;
            }
        }
        this.closeDirBrowser();
    },

    async createDirBrowser() {
        const input = document.getElementById('dir-browser-newname');
        const name  = (input?.value || '').trim();
        if (!name) {
            this.showToast(t('Inserisci un nome per la nuova cartella'), 'warning');
            return;
        }
        const btn = document.getElementById('dir-browser-mkdir-btn');
        if (btn) btn.disabled = true;
        try {
            const res  = await fetch('/api/mkdir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent: this._dirBrowserPath, name })
            });
            const data = await res.json();
            if (data.success) {
                if (input) input.value = '';
                await this._loadDirBrowser(data.path);
                this.showToast('📁 ' + t('Cartella creata') + ': ' + name, 'success');
            } else {
                this.showToast('❌ ' + data.error, 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    async _loadDirBrowser(path) {
        const listEl = document.getElementById('dir-browser-list');
        const pathEl = document.getElementById('dir-browser-path');
        listEl.innerHTML = `<div style="padding:16px; color:var(--text-muted);">${t('Caricamento...')}</div>`;
        try {
            const res = await fetch(`/api/browse_dir?path=${encodeURIComponent(path)}`);
            const data = await res.json();
            if (!data.success) {
                listEl.innerHTML = `<div style="padding:16px; color:var(--danger);">${data.error}</div>`;
                return;
            }
            this._dirBrowserPath = data.path;
            pathEl.textContent = data.path;
            let html = '';
            // Pulsante "Seleziona questa cartella" sempre visibile in cima
            html += `<div style="padding:6px 10px;border-bottom:1px solid var(--border);background:var(--bg-hover);">
                <button type="button" class="btn btn-primary btn-small" style="width:100%;"
                    onclick="app.confirmDirBrowser()">
                    <i class="fa-solid fa-check"></i> Seleziona: <code style="font-size:.75rem;">${data.path}</code>
                </button>
            </div>`;
            if (data.parent !== null) {
                html += `<div class="dir-browser-item" onclick="app._loadDirBrowser('${data.parent.replace(/'/g, "\\'")}')">
                    <i class="fa-solid fa-arrow-up" style="color:var(--text-muted); width:16px;"></i>
                    <span style="color:var(--text-muted);">.. (su)</span>
                </div>`;
            }
            for (const dir of data.dirs) {
                const fullPath = (data.path === '/' ? '' : data.path) + '/' + dir;
                html += `<div class="dir-browser-item" onclick="app._loadDirBrowser('${fullPath.replace(/'/g, "\\'")}')">
                    <i class="fa-solid fa-folder" style="color:#f5a623; width:16px;"></i>
                    <span>${dir}</span>
                </div>`;
            }
            if (!data.dirs.length) {
                html += `<div style="padding:8px 16px; color:var(--text-muted); font-size:0.85rem;">${t('Nessuna sottocartella')}</div>`;
            }
            listEl.innerHTML = html;
        } catch (e) {
            listEl.innerHTML = `<div style="padding:16px; color:var(--danger);">${t('Errore di rete')}</div>`;
        }
    },
    addUrlTemplate(t) {
        const el = document.getElementById('urls-list');
        let url = '';
        if (t === 'extto') {
            const u = prompt('Nome utente ExtTo (lascia vuoto per feed globale):');
            if (u === null) return;
            url = u.trim() ? `https://extto.org/browse/?filter=u=${encodeURIComponent(u.trim())}` : 'https://extto.org/browse/';
        } else if (t === 'corsaro') {
            const u = prompt('Nome utente Il Corsaro Nero (lascia vuoto per feed globale):');
            if (u === null) return;
            url = u.trim() ? `https://ilcorsaronero.link/user/${encodeURIComponent(u.trim())}` : 'https://ilcorsaronero.link/';
        } else if (t === 'knaben') {
            url = 'https://rss.knaben.org/ita///1337x|eztv|showunsafe|hidex:';
        } else if (t === 'rss') {
            const u = prompt('Incolla l\'URL del feed RSS:');
            if (!u || !u.trim()) return;
            url = u.trim();
        } else if (t === 'tgx') {
            const u = prompt('Nome utente TorrentGalaxy (es. MIRCrewRS):');
            if (!u || !u.trim()) return;
            url = `https://torrentgalaxy.one/get-posts/user:${encodeURIComponent(u.trim())}/`;
        }
        if (url) el.value += (el.value ? '\n' : '') + url;
    },
    
    // Save Series & Movie (Adding New)
    async saveSeries(e) {
        e.preventDefault();
        const lang = document.getElementById('series-language-preset').value;
        const subPreset = document.getElementById('series-subtitle-preset').value;
        const s = {
            name: document.getElementById('series-name').value, seasons: document.getElementById('series-seasons').value,
            archive_path: document.getElementById('series-archive-path').value, quality: document.getElementById('series-quality').value,
            timeframe: parseInt(document.getElementById('series-timeframe').value)||0,
            language: lang === 'custom' ? document.getElementById('series-language-custom').value : lang,
            subtitle: subPreset === 'custom' ? (document.getElementById('series-subtitle-custom')?.value.trim() || '') : (subPreset || ''),
            enabled: document.getElementById('series-enabled').checked,
            aliases: document.getElementById('series-aliases').value.split(',').map(a => a.trim()).filter(a => a),
            tmdb_id: document.getElementById('series-tmdb-id')?.value || '',
            season_subfolders: document.getElementById('series-season-subfolders')?.checked || false,
        };
        try {
            const r = await fetch(`${API_BASE}/api/config`);
            if (!r.ok) throw new Error('Errore lettura config');
            const c = await r.json();
            c.series.push(s);
            const saveRes = await fetch(`${API_BASE}/api/config/series`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({series:c.series})});
            const saveData = await saveRes.json();
            if (!saveData.success) throw new Error(saveData.error || t('Save error'));
            this.closeModal('series-editor-modal');
            this.loadSeries();
            this.showToast(t('Serie aggiunta con successo!'), 'success');
        } catch (err) {
            this.showToast(t('Errore salvataggio serie') + ': ' + err.message, 'error');
        }
    },
    async saveMovie(e) {
        e.preventDefault();
        const lang = document.getElementById('movie-language-preset').value;
        const subPreset = document.getElementById('movie-subtitle-preset').value;
        const m = {
            name: document.getElementById('movie-name').value, year: document.getElementById('movie-year').value,
            enabled: document.getElementById('movie-enabled').checked,
            quality: document.getElementById('movie-quality').value,
            language: lang === 'custom' ? document.getElementById('movie-language-custom').value : lang,
            subtitle: subPreset === 'custom' ? (document.getElementById('movie-subtitle-custom')?.value.trim() || '') : (subPreset || ''),
        };
        try {
            const r = await fetch(`${API_BASE}/api/config`);
            if (!r.ok) throw new Error('Errore lettura config');
            const c = await r.json();
            c.movies.push(m);
            const saveRes = await fetch(`${API_BASE}/api/config/movies`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({movies:c.movies})});
            const saveData = await saveRes.json();
            if (!saveData.success) throw new Error(saveData.error || t('Save error'));
            this.closeModal('movie-editor-modal');
            this.loadMovies();
            this.showToast(t('Film aggiunto con successo!'), 'success');
        } catch (err) {
            this.showToast(t('Errore salvataggio film') + ': ' + err.message, 'error');
        }
    },

    // Logs
    startLogStream() {
        if(this.logSrc) this.logSrc.close();
        this.logSrc = new EventSource(`${API_BASE}/api/logs/stream`);
        this.logSrc.onmessage = e => { const d=JSON.parse(e.data); if(d.line) this.appendLog(d.line); };
    },
    async loadLogs() {
        const n = document.getElementById('log-lines-input').value;
        const res = await fetch(`${API_BASE}/api/logs?lines=${n}`); const data = await res.json();
        document.getElementById('logs-container').innerHTML = data.logs.map(l => this.fmtLog(l)).join('');
    },
    appendLog(l) {
        const c = document.getElementById('logs-container');
        const div = document.createElement('div'); div.innerHTML = this.fmtLog(l);
        const f = document.getElementById('log-search')?.value.toLowerCase();
        if(f && !l.toLowerCase().includes(f)) hideEl(div);
        c.appendChild(div); if(autoScroll) c.scrollTop = c.scrollHeight;
    },
    fmtLog(l) {
        let cls = 'log-line'; if(l.includes('ERROR')) cls+=' error'; else if(l.includes('WARNING')) cls+=' warning'; else if(l.includes('SUCCESS')||l.includes('✅')) cls+=' success';
        return `<div class="${cls}">${this.escapeHtml(l)}</div>`;
    },
    toggleAutoScroll() { autoScroll = !autoScroll; this.showToast(`AutoScroll ${autoScroll?'ON':'OFF'}`); document.getElementById('autoscroll-icon').innerHTML = autoScroll ? '<i class="fa-solid fa-arrow-down"></i>' : '<i class="fa-solid fa-pause"></i>'; },
    clearLogs() { document.getElementById('logs-container').innerHTML = ''; },

    // ── ATTIVITÀ ──────────────────────────────────────────────────────────
    _activityPollInterval: null,

    async loadActivity() {
        const container = document.getElementById('view-activity');
        if (!container) return;

        // Avvia polling automatico ogni 60s
        if (this._activityPollInterval) clearInterval(this._activityPollInterval);
        this._activityPollInterval = setInterval(() => {
            if (currentView === 'activity') this._refreshActivity();
        }, 60000);

        await this._refreshActivity();
    },

    async _refreshActivity() {
        try {
            const res = await fetch(`${API_BASE}/api/recent-downloads`);
            const data = await res.json();
            this._renderActivityStats(data.stats || {});
            this._renderActivityEvents(data.events || []);
        } catch(e) {
            console.error('loadActivity error:', e);
        }
    },

    _renderActivityStats(stats) {
        const dl  = document.getElementById('activity-stat-dl');
        const cy  = document.getElementById('activity-stat-cy');
        const err = document.getElementById('activity-stat-err');
        if (dl)  dl.textContent  = stats.downloads_7d ?? '—';
        if (cy)  cy.textContent  = stats.cycles_7d    ?? '—';
        if (err) err.textContent = stats.errors_7d    ?? '—';
        if (err) err.style.color = (stats.errors_7d > 0) ? 'var(--danger)' : '';
    },

    _renderActivityEvents(events) {
        const el = document.getElementById('activity-events');
        if (!el) return;
        if (!events.length) {
            el.innerHTML = `<div style="padding:1rem 1.25rem;color:var(--text-secondary);font-size:0.85rem;">${t('Nessun evento recente.')}</div>`;
            return;
        }
        const typeLabel = { episode: t('Serie TV'), movie: t('Film'), comic: t('Fumetto'), pack: t('Season Pack') };
        el.innerHTML = events.map((e, i) => {
            const border = i < events.length - 1 ? 'border-bottom:1px solid var(--border);' : '';
            const date   = e.date ? this.formatDate(e.date) : '—';
            if (e.kind === 'download') {
                const label = typeLabel[e.type] || e.type;
                const colors = {
                    episode: 'background:rgba(34,197,94,.12);color:#16a34a',
                    movie:   'background:rgba(59,130,246,.12);color:#2563eb',
                    comic:   'background:rgba(245,158,11,.12);color:#d97706',
                };
                const badge = colors[e.type] || 'background:var(--bg-secondary);color:var(--text-secondary)';
                const score = e.quality_score ? `<span style="font-size:0.75rem;color:var(--text-secondary);margin-left:6px;">score ${e.quality_score}</span>` : '';
                return `<div style="display:flex;align-items:center;gap:12px;padding:10px 1.25rem;${border}">
                    <span style="font-size:0.72rem;padding:2px 8px;border-radius:6px;${badge};font-weight:600;flex-shrink:0;">${label}</span>
                    <span style="font-size:0.875rem;color:var(--text-primary);flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${this.escapeHtml(e.title)}${score}</span>
                    <span style="font-size:0.75rem;color:var(--text-secondary);flex-shrink:0;">${date}</span>
                </div>`;
            } else {
                const dl  = e.downloads || 0;
                const err = e.errors    || 0;
                const sc  = e.scraped   || 0;
                let desc = `${sc} ${t('trovati')}`;
                if (dl > 0)  desc += ` &middot; <b style="color:#16a34a">${dl} ${t('scaricati')}</b>`;
                else         desc += ` &middot; <span style="color:var(--text-secondary);">${t('nessun download')}</span>`;
                if (err > 0) desc += ` &middot; <span style="color:var(--danger);">&#9888; ${err} ${t('errori')}</span>`;
                const bstyle = err > 0
                    ? 'background:rgba(239,68,68,.12);color:#dc2626'
                    : 'background:var(--bg-secondary);color:var(--text-secondary)';
                return `<div style="display:flex;align-items:center;gap:12px;padding:10px 1.25rem;${border}">
                    <span style="font-size:0.72rem;padding:2px 8px;border-radius:6px;${bstyle};font-weight:600;flex-shrink:0;">${t('ciclo')}</span>
                    <span style="font-size:0.875rem;color:var(--text-secondary);flex:1;">${desc}</span>
                    <span style="font-size:0.75rem;color:var(--text-secondary);flex-shrink:0;">${date}</span>
                </div>`;
            }
        }).join('');
    },

    _renderActivityDownloads() {},
    _renderActivityCycles() {},
    // ─────────────────────────────────────────────────────────────────────
    async detectNetworkInterfaces() {
        try {
            const listDiv = document.getElementById('lt-interface-list');
            const itemsDiv = document.getElementById('lt-interface-items');
            
            showEl(listDiv);
            itemsDiv.innerHTML = '<small style="color:var(--text-secondary)">Rilevamento in corso...</small>';
            
            const resp = await fetch(`${API_BASE}/api/network/interfaces`);
            const data = await resp.json();
            const interfaces = data.interfaces || {};
            
            if (Object.keys(interfaces).length === 0) {
                itemsDiv.innerHTML = `<small style="color:var(--error)">${t('Nessuna interfaccia rilevata')}</small>`;
                return;
            }
            
            itemsDiv.innerHTML = Object.entries(interfaces).map(([name, info]) => {
                const typeIcon = info.type === 'VPN' ? '🔒' : 
                                info.type === 'Ethernet' ? '🌐' : 
                                info.type === 'WiFi' ? '📶' : '🔌';
                const typeColor = info.type === 'VPN' ? 'var(--success)' : 'var(--text-secondary)';
                return `
                    <div style="display:flex; align-items:center; gap:0.5rem; padding:0.5rem; margin-bottom:0.25rem; background:rgba(255,255,255,0.05); border-radius:4px; cursor:pointer;" 
                         onclick="document.getElementById('lt-interface').value='${name}'; document.getElementById('lt-interface-list').style.display='none'; app.showToast('${typeIcon} ${name} selezionata', 'success');">
                        <span style="font-size:1.2rem;">${typeIcon}</span>
                        <div style="flex:1;">
                            <strong style="color:${typeColor}">${name}</strong>
                            <small style="color:var(--text-secondary); margin-left:0.5rem;">${info.type} — ${info.ip}</small>
                        </div>
                        <i class="fa-solid fa-check" style="color:var(--success); font-size:1.2rem;"></i>
                    </div>
                `;
            }).join('');
            
            this.showToast(`${Object.keys(interfaces).length} ${t('interfaces detected')}`, 'success');
        } catch (e) { console.warn('detectInterfaces:', e); }
    },
    closeModal(id) { document.getElementById(id).classList.remove('active'); },
    closeRenameModal() {
        // Chiude il modal E rimette il pulsante Esegui in stato disabilitato,
        // così alla prossima apertura non risulta mai già attivo.
        this.closeModal('rename-preview-modal');
        const execBtn = document.getElementById('rename-execute-btn');
        if (execBtn) { execBtn.disabled = true; execBtn.innerHTML = `<i class="fa-solid fa-check"></i> ${t('Esegui Rinomina')}`; }
    },
    
    // ========================================================================
    // MOBILE UI — Drawer, QuickBar, Kebab Serie
    // ========================================================================

    toggleMobileDrawer() {
        const drawer = document.getElementById('mobile-drawer');
        const backdrop = document.getElementById('mobile-drawer-backdrop');
        const btn = document.getElementById('nav-more-btn');
        const isOpen = drawer.classList.contains('open');
        if (isOpen) {
            this.closeMobileDrawer();
        } else {
            drawer.classList.add('open');
            backdrop.classList.add('open');
            if (btn) btn.classList.add('active');
        }
    },

    closeMobileDrawer() {
        document.getElementById('mobile-drawer').classList.remove('open');
        document.getElementById('mobile-drawer-backdrop').classList.remove('open');
        const btn = document.getElementById('nav-more-btn');
        if (btn) btn.classList.remove('active');
    },

    _updateQuickBar() {
        const bar = document.getElementById('mobile-quick-bar');
        if (!bar) return;
        // Visibile su tutte le view su mobile (gestito via CSS per dashboard),
        // nascondiamo solo se il drawer è aperto
    },

    _updateDrawerActive(view) {
        // Marca active nel drawer se la view corrente è secondaria
        document.querySelectorAll('.mobile-drawer-item').forEach(item => {
            item.classList.toggle('active', item.dataset.view === view);
        });
        // Se view secondaria: attiva il btn "Altro" nella bottom nav
        const secondaryViews = ['movies','discovery','comics','config','maintenance','charts','manual','license'];
        const moreBtn = document.getElementById('nav-more-btn');
        if (moreBtn) moreBtn.classList.toggle('active', secondaryViews.includes(view));
    },

    toggleSeriesKebab(btn, seriesId, seriesName) {
        // Chiudi tutti gli altri menu aperti
        document.querySelectorAll('.series-kebab-menu').forEach(m => {
            if (m !== btn._kebabMenu) m.remove();
        });

        if (btn._kebabMenu && document.body.contains(btn._kebabMenu)) {
            btn._kebabMenu.remove();
            btn._kebabMenu = null;
            return;
        }

        const menu = document.createElement('div');
        menu.className = 'series-kebab-menu';
        menu.innerHTML = `
            <button onclick="app.showEpisodes(${seriesId}, '${this.escapeJs(seriesName)}'); app._closeKebab()">
                <i class="fa-solid fa-list-ul"></i> Dettagli
            </button>
            <button onclick="app.scanSeriesPath(${seriesId}, '${this.escapeJs(seriesName)}'); app._closeKebab()">
                <i class="fa-solid fa-folder-tree"></i> Scansione
            </button>
            <button class="kebab-danger" onclick="app.deleteSeriesFromConfig('${this.escapeJs(seriesName)}', ${sid}); app._closeKebab()">
                <i class="fa-solid fa-trash-can"></i> Elimina
            </button>
        `;

        // Posizione relativa al bottone
        const rect = btn.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.style.top = (rect.bottom + 4) + 'px';
        menu.style.zIndex = '9999';

        document.body.appendChild(menu);
        btn._kebabMenu = menu;

        // Chiudi click fuori
        const closeOutside = (e) => {
            if (!menu.contains(e.target) && e.target !== btn) {
                menu.remove();
                btn._kebabMenu = null;
                document.removeEventListener('click', closeOutside);
            }
        };
        setTimeout(() => document.addEventListener('click', closeOutside), 10);
    },

    _closeKebab() {
        document.querySelectorAll('.series-kebab-menu').forEach(m => m.remove());
    },

showToast(m, t='info') { const d=document.createElement('div'); d.className=`toast ${t}`; d.innerHTML=(t==='success'?'<i class="fa-solid fa-check"></i> ':t==='error'?'<i class="fa-solid fa-xmark"></i> ':'<i class="fa-solid fa-info"></i> ')+m; document.getElementById('toast-container').appendChild(d); setTimeout(()=>d.remove(),3000); },
    escapeHtml(t) { 
        if(t === null || t === undefined) return ''; 
        return String(t).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;"); 
    },
    escapeAttr(t) { return this.escapeHtml(t); },
    escapeJs(t) {
    if (t === null || t === undefined) return '';
    // Correzione: evitiamo escapeHtml che rompeva i singoli apici nei tag onclick
    return String(t).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    },
    formatDate(s) { if(!s) return '-'; return new Date(s).toLocaleString('it-IT'); },
    restartScrape() { fetch(`${API_BASE}/api/run-now`, {method:'POST'}).then(()=>this.showToast(t('Scan started'), 'success')); },

    // ========================================================================
    // TORRENT MANAGER (libtorrent embedded)
    // ========================================================================
    _torrentPollId: null,
    _removeTorrentHash: null,
    
    async forceRefreshTorrents() {
        this.showToast(t('Updating list...'), 'info');
        await this.loadTorrents();
    },
    
    startTorrentPoll() {
        // Evita di avviare loop multipli se l'utente clicca velocemente sui tab
        if (this._isPollingTorrents) return;
        this._isPollingTorrents = true;

        const poll = async () => {
            // Se nel frattempo l'utente ha cambiato vista, ferma il ciclo
            if (!this._isPollingTorrents) return;

            try {
                await this.loadTorrents();
            } catch (e) {
                console.error("Errore durante il polling dei torrent:", e);
            } finally {
                // Schedula la prossima chiamata ESATTAMENTE 2 secondi dopo 
                // che la precedente ha finito di caricare (successo o errore che sia)
                if (this._isPollingTorrents) {
                    this._torrentPollId = setTimeout(poll, 2000);
                }
            }
        };

        poll(); // Avvia il primo ciclo
    },

    stopTorrentPoll() {
        this._isPollingTorrents = false;
        if (this._torrentPollId) {
            clearTimeout(this._torrentPollId);
            this._torrentPollId = null;
        }
    },

    async loadTorrents() {
        if (this._torrentsAbortCtrl) this._torrentsAbortCtrl.abort();
        this._torrentsAbortCtrl = new AbortController();
        const signal = this._torrentsAbortCtrl.signal;
        try {
            // Stats dal motore LT (può fallire — non blocca i download HTTP/Mega)
            let s = { available: false, dl_rate: 0, ul_rate: 0, active: 0, paused: 0 };
            try {
                const sRes = await fetch(`${API_BASE}/api/torrents/stats`, { signal });
                s = await sRes.json();
            } catch(_) {}

            fetch(`${API_BASE}/api/torrent-tags`)
                .then(r => r.json())
                .then(tags => { 
                    torrentTagsDb = tags; 
                    this._updateTagFilterDropdown(); 
                }).catch(()=>{});

            const unavail = document.getElementById('torrent-unavailable');
            const list    = document.getElementById('torrent-list');

            // Anche se LT non è disponibile, carichiamo comunque i download HTTP/Mega
            // dal proxy Flask che li inietta in /api/torrents
            if (!s.available) {
                showEl(unavail);
                hideEl('torrent-empty');
                hideEl('torrent-bulk-bar');
                // NON fare return — continua per caricare download HTTP/Mega
            } else {
                hideEl(unavail);
            }

            document.getElementById('tstat-dl').textContent    = this._fmtRate(s.dl_rate);
            document.getElementById('tstat-ul').textContent    = this._fmtRate(s.ul_rate);
            document.getElementById('tstat-count').textContent =
                `${s.active ?? 0} ${t('attivi')}` + (s.paused ? ` / ${s.paused} ${t('in pausa')}` : '');
            const connEl = document.getElementById('tstat-conn');
            if (connEl) {
                if (s.is_listening) {
                    const upnpTip = s.upnp_active ? ' UPnP ✓' : '';
                    connEl.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--success)"></i> :${s.listen_port}${upnpTip}`;
                    connEl.title = `Porta ${s.listen_port} aperta${s.upnp_active ? ' (UPnP attivo)' : ''}`;
                    connEl.style.color = '';
                } else {
                    connEl.innerHTML = `<i class="fa-solid fa-circle-xmark" style="color:var(--error,#f87171)"></i> offline`;
                    connEl.title = 'Porta non aperta — controlla firewall o abilita UPnP';
                }
            }

            // Usa il proxy Flask (/api/torrents) che inietta anche ACTIVE_HTTP_DOWNLOADS
            // (download HTTP/Mega diretti) oltre ai torrent del motore libtorrent
            const tRes   = await fetch(`${API_BASE}/api/torrents`);
            const tData  = await tRes.json();
            const torrents = (Array.isArray(tData) ? tData : (tData.torrents || [])).map(t => {
                // Intercetta i dati a prescindere da come li chiama libtorrent
                const dl = t.total_done || t.all_time_download || t.total_download || t.downloaded || 0;
                const ul = t.total_uploaded || t.all_time_upload || t.total_upload || t.uploaded || 0;
                return {
                    ...t,
                    _dlBytes: dl,
                    _ulBytes: ul,
                    ratio: typeof t.ratio === 'number' ? t.ratio : (dl > 0 ? ul / dl : 0),
                };
            });

            

            this._torrentLastData = torrents;
            this._renderTorrentRows(torrents);
        } catch(e) {
            console.error("Errore in loadTorrents:", e);
            // Mostra il toast solo se il pannello "non disponibile" è già visibile
            // (condizione corretta: !== 'none' significa che il div è visibile/block)
            // e solo una volta ogni 30 secondi per evitare spam durante il polling
            const unavail = document.getElementById('torrent-unavailable');
            const now = Date.now();
            if (unavail && unavail.style.display !== 'none') {
                if (!this._ltErrorToastTs || (now - this._ltErrorToastTs) > 30000) {
                    this._ltErrorToastTs = now;
                    this.showToast(t('Errore di comunicazione con libtorrent'), "warning");
                }
            }
        }
    },

    // --- NUOVA FUNZIONE: GESTIONE LIMITI VELOCITA GLOBALI ---
    async setGlobalSpeedLimits() {
        const dl = parseInt(document.getElementById('quick-dl-limit').value) || 0;
        const ul = parseInt(document.getElementById('quick-ul-limit').value) || 0;

        this.showToast(t('Applicazione limiti in corso...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/set-speed-limits`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dl_kbps: dl, ul_kbps: ul })
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
                // Aggiorniamo anche i campi nei settings visivi se siamo su libtorrent
                const dlInput = document.getElementById('lt-dl-limit');
                const ulInput = document.getElementById('lt-ul-limit');
                if (dlInput) dlInput.value = dl;
                if (ulInput) ulInput.value = ul;
            } else {
                this.showToast(data.error || t('Errore durante l\'applicazione'), 'error');
            }
        } catch (e) {
            this.showToast(t('Errore di rete con il server'), 'error');
        }
    },
    // --------------------------------------------------------

    async pauseTorrent(hash) {
        await fetch(`${API_BASE}/api/torrents/pause`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hash})
        });
        await this.loadTorrents();
    },
    async recheckTorrent(hash, btnElement) {
        const tObj = this._torrentLastData?.find(x => x.hash === hash);
        if (!tObj) return;

        if(!confirm(`${t('Recheck')}?`)) return;
        
        let origHtml = '';
        if (btnElement) {
            origHtml = btnElement.innerHTML;
            btnElement.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            btnElement.disabled = true;
        }

        try {
            await fetch(`${API_BASE}/api/torrents/recheck`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({hash}) });
            this.showToast(t('Controllo file avviato'), 'info');
            
            tObj.state = 'Controllo File';
            tObj.paused = false; 
            this._renderTorrentRows(this._torrentLastData);
            
            setTimeout(() => {
                this.loadTorrents();
            }, 1000);
        } catch(e) { 
            this.showToast(t('Errore di rete durante il recheck'), 'error'); 
            if (btnElement) { btnElement.innerHTML = origHtml; btnElement.disabled = false; }
        }
    },
    
    async resumeTorrent(hash) {
        await fetch(`${API_BASE}/api/torrents/resume`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hash})
        });
        await this.loadTorrents();
    },

    removeTorrent(hash, name) {
        this._removeTorrentHash = hash;
        document.getElementById('remove-torrent-name').textContent    = name;
        document.getElementById('remove-torrent-delfiles').checked     = false;
        document.getElementById('remove-torrent-modal').classList.add('active');
    },

    async confirmRemoveTorrent() {
        const hash         = this._removeTorrentHash;
        const delete_files = document.getElementById('remove-torrent-delfiles').checked;
        this.closeModal('remove-torrent-modal');
        try {
            const res = await fetch(`${API_BASE}/api/torrents/remove`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({hash, delete_files})
            });
            if (!res.ok) throw new Error();
            
            this.showToast(t('Torrent rimosso'), 'success');
            await this._cleanTagOnRemove([hash]);
            await this.loadTorrents();
        } catch(e) {
            this.showToast(t('Engine busy, try again shortly'), 'error');
        }
    },

    // --- INIZIO NUOVE FUNZIONI MENU A TENDINA SINGOLO ---
    async directRemoveTorrent(hash, withFiles) {
        if (withFiles && !confirm(t('Attenzione: Selezionando questa opzione cancellerai fisicamente i file multimediali dal tuo hard disk. Non è possibile annullare l\'operazione.'))) return;
        document.getElementById('single-remove-drop')?.remove();
        try {
            // Gli scarichi diretti (HTTP/Mega) hanno hash fittizio: instradati verso l'engine
            // via /api/torrents/remove che gestisce correttamente i prefissi http_/mega_

            const res = await fetch(`${API_BASE}/api/torrents/remove`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({hash: hash, delete_files: withFiles})
            });
            if (!res.ok) throw new Error();
            
            this.showToast(withFiles ? t('Torrent e file eliminati') : t('Torrent rimosso'), 'success');
            await this._cleanTagOnRemove([hash]);
            await this.loadTorrents();
        } catch(e) {
            this.showToast(t('Engine busy, try again shortly'), 'error');
        }
    },

    _toggleSingleRemoveDropdown(caretBtn, hash) {
        const existing = document.getElementById('single-remove-drop');
        if (existing) { existing.remove(); return; }

        const drop = document.createElement('div');
        drop.id = 'single-remove-drop';
        const rect = caretBtn.closest('.single-remove-wrap').getBoundingClientRect();
        drop.style.cssText = `
            position:fixed;z-index:9999;
            top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;
            background:var(--bg-secondary,#1e2130);
            border:1px solid var(--border-color,rgba(255,255,255,.15));
            border-radius:8px;min-width:195px;overflow:hidden;
            box-shadow:0 8px 28px rgba(0,0,0,0.6);`;

        const items = [
            { icon:'fa-solid fa-trash', label:t('Rimuovi'), color:'#f87171',
              desc:'Mantieni i file sul disco',
              fn: () => this.directRemoveTorrent(hash, false) },
            { icon:'fa-solid fa-trash-can', label:t('Rimuovi ed Elimina'), color:'#ef4444',
              desc:'Cancella anche i file scaricati',
              fn: () => this.directRemoveTorrent(hash, true) },
        ];
        items.forEach((item, i) => {
            const btn = document.createElement('button');
            btn.style.cssText = `display:flex;align-items:center;gap:10px;width:100%;
                padding:10px 14px;background:none;border:none;
                ${i < items.length-1 ? 'border-bottom:1px solid rgba(255,255,255,.07);' : ''}
                color:#e2e8f0;cursor:pointer;text-align:left;`;
            btn.innerHTML = `
                <i class="${item.icon}" style="color:${item.color};width:16px;text-align:center;flex-shrink:0"></i>
                <div>
                  <div style="font-size:.84rem;font-weight:600">${item.label}</div>
                  <div style="font-size:.72rem;color:rgba(255,255,255,.4);margin-top:1px">${item.desc}</div>
                </div>`;
            btn.onmouseenter = () => btn.style.background = 'rgba(255,255,255,.06)';
            btn.onmouseleave = () => btn.style.background = 'none';
            btn.onclick = (e) => { e.stopPropagation(); drop.remove(); item.fn(); };
            drop.appendChild(btn);
        });
        document.body.appendChild(drop);
        const close = (e) => {
            if (!drop.contains(e.target) && !caretBtn.contains(e.target)) {
                drop.remove();
                document.removeEventListener('click', close);
                document.removeEventListener('scroll', close, true);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', close);
            document.addEventListener('scroll', close, true);
        }, 0);
    },
    // --- FINE NUOVE FUNZIONI ---
    async submitAddMagnetTorrent() {
        const inputStr  = document.getElementById('add-magnet-torrent-input').value.trim();
        const save_path = document.getElementById('add-magnet-torrent-path').value.trim();
        const noRename  = document.getElementById('add-magnet-torrent-no-rename')?.checked || false;
        
        if (!inputStr) { this.showToast(t('Inserisci un link o URL'), 'error'); return; }

        if (inputStr.startsWith('http://') || inputStr.startsWith('https://')) {
            this.showToast(t('Downloading .torrent file...'), 'info');
            try {
                const res = await fetch(`${API_BASE}/api/fetch-url`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: inputStr})
                });
                const data = await res.json();
                
                if (data.success) {
                    const upRes = await fetch(`${API_BASE}/api/upload-torrent`, {
                        method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({filename: data.filename, data: data.data, download_now: true})
                    });
                    const upData = await upRes.json();
                    
                    if (upData.success) {
                        this.showToast(t('Torrent added from URL!'), 'success');
                        this.closeModal('add-magnet-torrent-modal');
                        if (upData.hash) await this._saveTag(upData.hash, 'Manuale');
                        if (upData.hash && noRename) await this._saveNoRename(upData.hash, true);
                        await this.loadTorrents();
                    } else {
                        this.showToast(upData.error || t('Errore invio torrent'), 'error');
                    }
                } else {
                    this.showToast(data.error || t('Error downloading from URL'), 'error');
                }
            } catch (e) { console.error('addTorrentFromURL:', e); this.showToast(t('Errore imprevisto'), 'error'); }
        } else {
            const res  = await fetch(`${API_BASE}/api/torrents/add`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({magnet: inputStr, save_path})
            });
            const data = await res.json();
            
            if (data.ok) {
                this.showToast(t('Torrent aggiunto!'), 'success');
                this.closeModal('add-magnet-torrent-modal');
                const _h = data.hash || this._hashFromMagnet(inputStr);
                await this._saveTag(_h, 'Manuale');
                if (_h && noRename) await this._saveNoRename(_h, true);
                await this.loadTorrents();
            } else {
                this.showToast(t('Errore aggiunta torrent'), 'error');
            }
        }
    },

    _torrentStateBadge(state, paused, error, isDone) {
        const _bs = 'max-width:100%;overflow:hidden;text-overflow:ellipsis;display:inline-block;white-space:nowrap;vertical-align:middle;';

        // Mappa stato interno (italiano dal backend) → chiave i18n tradotta
        const _STATE_I18N = {
            'in scarico':           t('In Scarico'),
            'in scarico (fermo)':   t('In Scarico (Fermo)'),
            'in seeding':           t('In Seeding'),
            'seeding (fermo)':      t('Seeding (Fermo)'),
            'in pausa':             t('In Pausa'),
            'in coda (dl)':         t('In Coda (DL)'),
            'in coda (seeding)':    t('In Coda (Seeding)'),
            'terminato':            t('Terminato'),
            'salvato':              t('Salvato'),
            'attesa metadati':      t('Attesa Metadati'),
            'allocazione spazio':   t('Allocazione Spazio'),
            'controllo file':       t('Controllo File'),
            'controllo dati':       t('Controllo Dati'),
            'controllo (100%)':     t('Controllo (100%)'),
            'errore':               t('Errore'),
        };
        // Abbreviazioni per stati lunghi — il titolo (tooltip) mostra il testo completo
        const _SHORT = {
            'attesa metadati':      t('Metadati...'),
            'allocazione spazio':   t('Alloc...'),
            'controllo file':       t('Check...'),
            'controllo dati':       t('Check...'),
            'controllo (100%)':     t('Check...'),
            'checking files':       t('Check...'),
            'checking resume data': t('Check...'),
            'allocating':           t('Alloc...'),
            'downloading metadata': t('Meta...'),
        };
        const sLower = (state || '').toLowerCase();
        const translatedState = _STATE_I18N[sLower] || state;
        const _stateShort = _SHORT[sLower] || translatedState;

        if (error)  return `<span class="badge" style="background:rgba(239,68,68,.15);color:#f87171;${_bs}" title="${this._esc(error)}">${t('Errore')}</span>`;

        let s = sLower;
        let colorClass = 'badge-secondary'; // Default Grigio

        // Colori per la logica avanzata (stile qBittorrent) — usa sempre stringhe italiane del backend
        if (s.includes('terminato') || s.includes('salvato') || s.includes('saved')) {
            // Sfondo con gradiente verde pieno, testo bianco e una leggera ombra per leggibilità
            return `<span class="badge" style="background: linear-gradient(90deg, #10b981, #34d399); color: #ffffff; border: 1px solid #059669; text-shadow: 0 1px 1px rgba(0,0,0,0.3); ${_bs}" title="${this._esc(translatedState)}"><i class="fa-solid fa-check-double"></i> ${this._esc(_stateShort)}</span>`;
        } else if (s.includes('scarico') && !s.includes('fermo')) {
            colorClass = 'badge-info';         // Azzurro — In Scarico attivo
        } else if (s.includes('seeding') && !s.includes('fermo')) {
            colorClass = 'badge-success';      // Verde — In Seeding attivo
        } else if (s.includes('seeding') && s.includes('fermo')) {
            colorClass = 'badge-secondary';    // Grigio — Seeding fermo
        } else if (s.includes('pausa')) {
            colorClass = 'badge-secondary';    // Grigio — In Pausa manuale
        } else if (s.includes('fermo') || s.includes('coda') || s.includes('attesa')) {
            colorClass = 'badge-warning';      // Arancione — Stallo, In Coda, Metadati
        } else if (s.includes('controllo') || s.includes('allocazione')) {
            colorClass = 'badge-warning';      // Arancione — operazioni disco
        } else if (s.includes('errore')) {
            colorClass = 'badge-danger';       // Rosso
        } else if (paused && isDone) {
            // Fallback di sicurezza se arriva "paused" inglese su un file al 100%
            return `<span class="badge" style="background:rgba(16,185,129,.15);color:var(--success);border:1px solid var(--success);${_bs}"><i class="fa-solid fa-check-double"></i> ${t('Terminato')}</span>`;
        } else if (paused) {
            // Fallback per "paused" generico (non dovuto alla coda)
            colorClass = 'badge-secondary';
        }

        return `<span class="badge ${colorClass}" style="${_bs}" title="${this._esc(translatedState)}">${this._esc(_stateShort)}</span>`;
    },

    _fmtRate(bps) {
        if (!bps || bps < 1) return '0 B/s';
        if (bps < 1024)      return `${bps} B/s`;
        if (bps < 1048576)   return `${(bps/1024).toFixed(1)} KB/s`;
        return `${(bps/1048576).toFixed(2)} MB/s`;
    },

    _fmtBytes(b) {
        if (b === 0 || b === '0') return '0 B';
        if (!b || b < 0)      return '—';
        if (b < 1024)         return `${b} B`;
        if (b < 1048576)      return `${(b/1024).toFixed(0)} KB`;
        if (b < 1073741824)   return `${(b/1048576).toFixed(1)} MB`;
        return `${(b/1073741824).toFixed(2)} GB`;
    },

    _fmtEta(sec) {
        if (sec === -2) return '<i class="fa-solid fa-house-laptop" style="color:var(--success); opacity:0.9;" title="File archiviato nel NAS"></i> NAS';
        if (sec < 0)    return '—';
        if (sec < 60)   return `${sec}s`;
        if (sec < 3600) return `${Math.floor(sec/60)}m ${sec%60}s`;
        const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
        return `${h}h ${m}m`;
    },

    _esc(s) {
        return String(s||'')
            .replace(/&/g,'&amp;').replace(/</g,'&lt;')
            .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    },

    // ========================================================================
    // IP FILTER (libtorrent)
    // ========================================================================
    async _updateIpFilterStatus() {
        const statusEl = document.getElementById('lt-ipfilter-status');
        if (!statusEl) return;
        try {
            const r = await fetch(`${API_BASE}/api/torrents/ipfilter_status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            
            if (!r.ok) { statusEl.textContent = '—'; return; }
            const d = await r.json();
            
            if (d.active) {
                let html = `<span style="color:var(--success)"><i class="fa-solid fa-shield-halved"></i> Attivo — ${d.rules_count?.toLocaleString() || '?'} regole</span>`;
                
                if (d.url) {
                    html += `<br><small style="color:var(--text-secondary); word-break: break-all; margin-top: 0.2rem; display: block;"><strong>Lista:</strong> ${d.url}</small>`;
                }
                
                if (d.last_updated) {
                    html += `<small style="color:var(--text-muted); display: block;">Aggiornato: ${new Date(d.last_updated*1000).toLocaleString('it-IT')}</small>`;
                }
                
                statusEl.innerHTML = html;
            } else {
                statusEl.innerHTML = '<span style="color:var(--text-muted)"><i class="fa-solid fa-shield-slash"></i> Non caricato</span>';
            }
        } catch { statusEl.textContent = '—'; }
    },

    async updateIpFilter() {
        const url = document.getElementById('lt-ipfilter-url')?.value?.trim();
        if (!url) { this.showToast(t('Inserisci un URL per la blocklist'), 'error'); return; }
        const statusEl = document.getElementById('lt-ipfilter-status');
        if (statusEl) statusEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Download in corso...';
        try {
            const r = await fetch(`${API_BASE}/api/torrents/ipfilter_update`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            });
            const d = await r.json();
            if (d.ok) {
                this.showToast(`${t('Update Blocklist Now')}: ${d.rules_count?.toLocaleString() || '?'}`, 'success');
            } else {
                this.showToast(`${t('Errore:')} ${d.error || t('Errore di rete')}`, 'error');
            }
        } catch(e) {}
        this._updateIpFilterStatus();
    },

    // ========================================================================
    // TORRENT SORT
    // ========================================================================
    _torrentSortCol: 'name',
    _torrentSortAsc: true,
    _torrentLastData: [],

    _sortTorrents(torrents) {
        if (!this._torrentSortCol) return torrents;
        const col = this._torrentSortCol;
        const asc = this._torrentSortAsc;
        return [...torrents].sort((a, b) => {
            let va = a[col], vb = b[col];
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return asc ? -1 : 1;
            if (va > vb) return asc ? 1 : -1;
            return 0;
        });
    },

    _setTorrentSort(col) {
        if (this._torrentSortCol === col) {
            this._torrentSortAsc = !this._torrentSortAsc;
        } else {
            this._torrentSortCol = col;
            this._torrentSortAsc = true;
        }
        this._renderTorrentRows(this._torrentLastData);
    },

    _renderTorrentRows(torrents) {
        const list  = document.getElementById('torrent-list');
        const empty = document.getElementById('torrent-empty');
        
        const prevSelected = new Set([...document.querySelectorAll('.torrent-checkbox:checked')].map(c => c.dataset.hash));

        list.querySelectorAll('.torrent-row').forEach(r => r.remove());
        
        list.querySelectorAll('[data-sort-col]').forEach(th => {
            const col = th.dataset.sortCol;
            const ico = th.querySelector('.sort-icon');
            if (!ico) return;
            if (col === this._torrentSortCol) {
                ico.textContent = this._torrentSortAsc ? ' ▲' : ' ▼';
            } else {
                ico.textContent = ' ⇅';
            }
        });
        
        // Assicurati che torrents sia un array valido prima di continuare
        if (!Array.isArray(torrents)) torrents = [];
        
        showIf(empty, torrents.length === 0);
        
        let filteredTorrents = torrents;
        if (currentTagFilter !== 'all') {
            if (currentTagFilter === 'untagged') {
                filteredTorrents = torrents.filter(torr => !torrentTagsDb[torr.hash] || String(torrentTagsDb[torr.hash]).trim() === '');
            } else {
                filteredTorrents = torrents.filter(torr => torrentTagsDb[torr.hash] === currentTagFilter);
            }
        }
        
        const sorted = this._sortTorrents(filteredTorrents);
        
        sorted.forEach(torr => {
            try { 
                const row = document.createElement('div');
                row.className = 'table-row torrent-row';
                row.dataset.hash = torr.hash || '';
                
                const fileOnDisk = torr.physical_file_found === true;
                const rawPct = Math.min(100, Math.max(0, (torr.progress || 0) * 100));
                
                const stateStr = torr.state || '';
                const _stLower = stateStr.toLowerCase();
                const terminalState = _stLower.includes('seeding') || _stLower.includes('finished');
                
                const isActiveDownload = rawPct > 0 && rawPct < 100 && !terminalState && !torr.paused;
                const fileOnDiskFinal  = fileOnDisk && !isActiveDownload;
                const pct    = fileOnDiskFinal ? 100 : rawPct;
                
                const total_s = torr.total_size || 0;
                const down_s = torr.downloaded || 0;
                
                const isDone = pct >= 100 || terminalState
                            || (torr.paused && total_s > 0 && down_s >= total_s)
                            || fileOnDiskFinal;

                let displayState = stateStr;
                if (fileOnDiskFinal) {
                    // Rimuove suffisso " [F]" (Forzato) prima del matching, per evitare
                    // che "In Seeding [F]" venga classificato come stato non finale
                    const _stBase = _stLower.replace(/\s*\[f\]\s*$/i, '').trim();
                    const keepStates = ['in pausa', 'in coda (dl)', 'in coda (seeding)', 'errore'];
                    const isExplicitState = keepStates.some(k => _stBase.includes(k));
                    // Match su "contiene" invece di "uguale": copre "seeding (fermo)",
                    // "in seeding", "seeding (completato)", eventuali varianti future
                    const isFinalState = _stBase.includes('seeding')
                                      || _stBase.includes('finished')
                                      || _stBase.includes('terminato')
                                      || _stBase.includes('salvato');
                    if (!isExplicitState && !isFinalState) {
                        displayState = 'salvato';
                    }
                }
                
                let etaStr = '—';
                if (fileOnDiskFinal) {
                    etaStr = this._fmtEta(-2);
                } else if (torr.eta > 0) {
                    etaStr = this._fmtEta(torr.eta);
                } else if (isDone) {
                    etaStr = fileOnDiskFinal 
                        ? `<span style="cursor:help;" title="${t('File Archiviato')}"><i class="fa-solid fa-check-double"></i></span>` 
                        : `<span style="cursor:help;" title="${t('File presente sul NAS')}"><i class="fa-solid fa-check"></i></span>`;
                }

                const etaStyle = isDone && (torr.eta || 0) <= 0
                    ? 'color:var(--success); font-size:1.1rem; font-weight:700;'
                    : 'color:var(--text-secondary);';
                    
                let ratio  = (typeof torr.ratio === 'number') ? torr.ratio : ((torr.total_done || 0) > 0 ? ((torr.total_uploaded || 0) / torr.total_done) : 0);
                if (ratio > 999) ratio = 999.99;
                if (isNaN(ratio) || !isFinite(ratio)) ratio = 0.0;
                let ratioHtml = ratio.toFixed(2);
                if (torr.is_infinite) ratioHtml += ' <i class="fa-solid fa-infinity" style="color:var(--primary-light); font-size:0.75rem; margin-left:3px;" title="Seeding Infinito"></i>';
                
                const wasSel = prevSelected.has(torr.hash);

                let displayName = torr.name || '';
                let sourceHtml = '';
                
                const tagMatch = displayName.match(/\s+\[(ExtTo[^\]]*|Corsaro[^\]]*|Jackett[^\]]*)\]$/i);
                
                if (tagMatch) {
                    sourceHtml = `<span style="color:var(--info); font-weight:600; margin-right:8px; border-right:1px solid rgba(255,255,255,0.1); padding-right:8px;" title="Sorgente"><i class="fa-solid fa-satellite-dish"></i> ${this._esc(tagMatch[1])}</span>`;
                    displayName = displayName.replace(tagMatch[0], ''); 
                } else {
                    sourceHtml = `<span style="color:var(--text-muted); font-weight:600; margin-right:8px; border-right:1px solid rgba(255,255,255,0.1); padding-right:8px;" title="Sorgente"><i class="fa-solid fa-hand-pointer"></i> Manuale</span>`;
                }
                
                const myTag = torrentTagsDb[torr.hash];
                if (myTag && String(myTag).trim() !== '') {
                    sourceHtml += `<span class="badge badge-secondary" style="margin-right:8px; padding:0.2rem 0.5rem; font-size:0.65rem; background:rgba(255,255,255,0.1); text-transform: none; letter-spacing: normal;"><i class="fa-solid fa-tag"></i> ${this._esc(myTag)}</span>`;
                }
                
                let sizeStr = this._fmtBytes(torr.total_size);
                if (!torr.total_size || torr.total_size === 0 || sizeStr === '—') {
                    if (fileOnDiskFinal) {
                        sizeStr = '<span style="opacity:0.8; color:var(--success);"><i class="fa-solid fa-file-circle-check"></i> File archiviato e rinominato</span>';
                    } else {
                        sizeStr = '<span style="opacity:0.6;"><i class="fa-solid fa-spinner fa-spin"></i> Metadati in attesa...</span>';
                    }
                }
                
                // --- LOGICA COLORI BARRA PROGRESSO ---
                let isChecking = stateStr.toLowerCase().includes('controllo');
                let barColor = 'linear-gradient(90deg, var(--primary), var(--info))'; // Blu default
                
                if (isChecking) {
                    barColor = 'linear-gradient(90deg, #8b5cf6, #d946ef)'; // Viola
                } else if (torr.error) {
                    barColor = 'linear-gradient(90deg, #ef4444, #f87171)'; // Rosso
                } else if (isDone) {
                    barColor = 'linear-gradient(90deg, #10b981, #34d399)'; // Verde — prima di paused!
                } else if (torr.paused) {
                    barColor = 'linear-gradient(90deg, #6b7280, #9ca3af)'; // Grigio
                } else if (isActiveDownload) {
                    // --- EFFETTO DINAMICO PERCENTUALE ---
                    const hueStart = Math.floor((pct / 100) * 120); 
                    const hueEnd = Math.min(hueStart + 25, 120); 
                    barColor = `linear-gradient(90deg, hsl(${hueStart}, 90%, 35%), hsl(${hueEnd}, 100%, 45%))`;
                }
                
                let stripeClass = (isChecking || isActiveDownload) ? ' downloading' : '';
                // -------------------------------------

                // --- PROTEZIONE AZIONI CRITICHE ---
                // Calcoliamo isBusy PRIMA di aprire la stringa HTML
                let isBusy = stateStr.toLowerCase().includes('controllo') || 
                             stateStr.toLowerCase().includes('allocazione') || 
                             stateStr.toLowerCase().includes('spostamento') || 
                             stateStr.toLowerCase().includes('mov');
                             
                let disableTrash = isBusy ? 'disabled style="opacity:0.4; cursor:not-allowed; box-shadow:none;"' : '';
                let trashTitle = isBusy ? 'Operazione su disco in corso...' : 'Rimuovi (mantiene i file)';
                // ----------------------------------

                row.innerHTML = `
                    <div class="torrent-name" title="${this._esc(displayName)}">
                        <label style="display:flex;align-items:flex-start;gap:0.5rem;cursor:pointer;">
                            <input type="checkbox" class="torrent-checkbox" data-hash="${torr.hash || ''}" style="margin-top:3px;width:1rem;height:1rem;cursor:pointer;flex-shrink:0;" onchange="app._onTorrentCheckChange()">
                            <span style="display:flex;flex-direction:column;gap:0.15rem;min-width:0;">
                                <span class="torrent-name-text">${this._esc(displayName)}</span>
                                <span class="torrent-size" style="display:flex; align-items:center;">${sourceHtml}${sizeStr}</span>
                            </span>
                        </label>
                    </div>
                    <div style="display:flex; align-items:center; justify-content:center; overflow:hidden; min-width:0;">${this._torrentStateBadge(displayState, torr.paused, torr.error, isDone)}</div>
                    
                    <div style="display:flex; flex-direction:column; justify-content:center; min-width:0;">
                        <div class="torrent-progress-cell" style="display:flex; align-items:center; gap:8px;">
                            <div style="flex:1; height:6px; background:var(--bg-hover); border-radius:999px; overflow:hidden;">
                                <div class="torrent-progress-fill${stripeClass}" style="width:${pct}%; height:100%; background:${barColor}; border-radius:999px;"></div>
                            </div>
                            <span style="font-family:var(--font-mono); font-size:0.75rem; color:var(--text-secondary); width:42px; text-align:right;">${pct.toFixed(1)}%</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; gap:1rem; font-family:var(--font-mono); font-size:0.65rem; color:var(--text-muted); opacity:1; padding-right:50px; margin-top:3px; white-space:nowrap;">
                            <span title="Dati scaricati fisicamente finora" style="white-space:nowrap;"><i class="fa-solid fa-arrow-down" style="color:var(--success); font-size:0.7rem; vertical-align:middle;"></i> ${this._fmtBytes(torr._dlBytes)}</span>
                            <span title="Dati inviati (Seeding) finora" style="white-space:nowrap;"><i class="fa-solid fa-arrow-up" style="color:var(--warning); font-size:0.7rem; vertical-align:middle;"></i> ${this._fmtBytes(torr._ulBytes)}</span>
                        </div>
                    </div>
                    
                    <div style="text-align:center; font-variant-numeric:tabular-nums; font-family:var(--font-mono); font-size:0.8rem; color:var(--success); white-space:nowrap; min-width:72px;">${this._fmtRate(torr.dl_rate)}</div>
                    <div style="text-align:center; font-variant-numeric:tabular-nums; font-family:var(--font-mono); font-size:0.8rem; color:var(--warning); white-space:nowrap; min-width:72px;">${this._fmtRate(torr.ul_rate)}</div>
                    <div style="text-align:center; font-variant-numeric:tabular-nums; font-family:var(--font-mono); font-size:0.8rem; ${etaStyle}">${etaStr}</div>
                    <div style="text-align:center; font-variant-numeric:tabular-nums; font-family:var(--font-mono); font-size:0.8rem; color:var(--text-secondary); white-space:nowrap;">${torr.num_seeds || 0}S/${torr.num_peers || 0}P</div>
                    <div style="text-align:center; font-variant-numeric:tabular-nums; font-family:var(--font-mono); font-size:0.85rem; font-weight:600; color:var(--text-primary);">${ratioHtml}</div>
                    
                    <div class="torrent-actions" style="display:flex; gap:3px; justify-content:center;">
                        <button class="btn btn-small btn-primary" onclick="app.showTorrentDetails('${torr.hash}')" title="Dettagli Torrent"><i class="fa-solid fa-circle-info"></i></button>
                        <button class="btn btn-small btn-secondary" onclick="app.recheckTorrent('${torr.hash}', this)" title="Forza Recheck"><i class="fa-solid fa-stethoscope"></i></button>
                        ${torr.paused
                            ? `<button class="btn btn-small btn-secondary" onclick="app.resumeTorrent('${torr.hash}')"><i class="fa-solid fa-play"></i></button>`
                            : `<button class="btn btn-small btn-secondary" onclick="app.pauseTorrent('${torr.hash}')"><i class="fa-solid fa-pause"></i></button>`
                        }
                        <div style="position:relative;display:inline-flex;border-radius:6px;overflow:hidden;box-shadow:0 0 0 1px var(--danger);flex-shrink:0;min-width:max-content;" class="single-remove-wrap">
                            <button class="btn btn-small btn-danger" style="border-radius:0;border:none;box-shadow:none;padding:0 10px;flex-shrink:0;" onclick="app.directRemoveTorrent('${torr.hash}', false)" title="${trashTitle}" ${disableTrash}><i class="fa-solid fa-trash"></i></button><div style="width:1px;background:rgba(255,255,255,.2);align-self:stretch;flex-shrink:0;"></div><button class="btn btn-small btn-danger" style="border-radius:0;border:none;box-shadow:none;padding:0 8px;flex-shrink:0;" title="Scegli modalità di rimozione" onclick="app._toggleSingleRemoveDropdown(this, '${torr.hash}')" ${disableTrash}><i class="fa-solid fa-chevron-down" style="font-size:.75em;"></i></button>
                        </div>
                    </div>`;
                
                if (wasSel) {
                    row.querySelector('.torrent-checkbox').checked = true;
                }
                
                list.appendChild(row);
            } catch(e) {
                console.error("Errore renderizzazione riga torrent:", e, torr);
            }
        });
        
        showIf(document.getElementById('torrent-bulk-bar'), torrents.length > 0);
        this._onTorrentCheckChange();
    },
    
    _onTorrentCheckChange() {
        const all = document.querySelectorAll('.torrent-checkbox');
        const checked = document.querySelectorAll('.torrent-checkbox:checked');
        const selAll = document.getElementById('torrent-select-all');
        if (selAll) {
            selAll.indeterminate = checked.length > 0 && checked.length < all.length;
            selAll.checked = all.length > 0 && checked.length === all.length;
        }
        const countEl = document.getElementById('torrent-selected-count');
        if (countEl) countEl.textContent = checked.length > 0 ? `${checked.length} ${t('selezionati')}` : '';
    },

    toggleSelectAllTorrents(checked) {
        document.querySelectorAll('.torrent-checkbox').forEach(cb => cb.checked = checked);
        this._onTorrentCheckChange();
    },

    async bulkPauseTorrents() {
        const hashes = this._getSelectedHashes();
        if (!hashes.length) { this.showToast(t('Nessun torrent selezionato'), 'warning'); return; }
        try {
            const results = await Promise.all(hashes.map(h => fetch(`${API_BASE}/api/torrents/pause`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hash:h})
            })));
            if (results.some(r => !r.ok)) throw new Error('Uno o più torrent non risposto');
            this.showToast(`${hashes.length} ${t('torrent messi in pausa')}`, 'success');
            await this.loadTorrents();
        } catch(e) { this.showToast(t('Errore pausa torrent') + ': ' + e.message, 'error'); }
    },

    async bulkResumeTorrents() {
        const hashes = this._getSelectedHashes();
        if (!hashes.length) { this.showToast(t('Nessun torrent selezionato'), 'warning'); return; }
        try {
            const results = await Promise.all(hashes.map(h => fetch(`${API_BASE}/api/torrents/resume`, {
                method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hash:h})
            })));
            if (results.some(r => !r.ok)) throw new Error('Uno o più torrent non risposto');
            this.showToast(`${hashes.length} ${t('torrent ripresi')}`, 'success');
            await this.loadTorrents();
        } catch(e) { this.showToast(t('Errore ripresa torrent') + ': ' + e.message, 'error'); }
    },

    async bulkRecheckTorrents() {
        const hashes = this._getSelectedHashes();
        if (!hashes.length) { this.showToast(t('Nessun torrent selezionato'), 'warning'); return; }
        if (!confirm(`${t('Avviare il recheck su')} ${hashes.length} ${t('torrent?')}\n(${t('Quelli attualmente in pausa verranno messi in controllo e poi ri-messi in pausa automaticamente')})`)) return;
        
        const btn = document.querySelector('button[onclick="app.bulkRecheckTorrents()"]');
        let origHtml = '';
        if (btn) {
            origHtml = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            btn.disabled = true;
        }

        try {
            await Promise.all(hashes.map(async h => {
                await fetch(`${API_BASE}/api/torrents/recheck`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({hash:h}) });
                const tObj = this._torrentLastData?.find(x => x.hash === h);
                if (tObj) { tObj.state = 'Controllo File'; tObj.paused = false; }
            }));
            
            this.showToast(`${t('Recheck avviato su')} ${hashes.length} torrent`, 'info');
            this._renderTorrentRows(this._torrentLastData);

            setTimeout(() => {
                this.loadTorrents();
                if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
            }, 1000);
        } catch(e) { 
            this.showToast(t('Errore avvio ciclo:') + ' ' + e.message, 'error'); 
            if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
        }
    },

    async bulkRemoveTorrents(withFiles = false) {
        const hashes = this._getSelectedHashes();
        if (!hashes.length) { this.showToast(t('Nessun torrent selezionato'), 'warning'); return; }
        // Chiudi dropdown se aperto
        document.getElementById('bulk-remove-drop')?.remove();
        
        try {
            const responses = await Promise.all(hashes.map(h => fetch(`${API_BASE}/api/torrents/remove`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body:JSON.stringify({hash:h, delete_files: withFiles})
            })));
            
            // Se anche una sola richiesta fallisce, lancia l'errore
            if (responses.some(r => !r.ok)) throw new Error();

            const msg = withFiles
                ? `${hashes.length} torrent rimossi + file eliminati`
                : `${hashes.length} torrent rimossi`;
            this.showToast(msg, 'success');
            await this._cleanTagOnRemove(hashes);
            await this.loadTorrents();
        } catch(e) {
            this.showToast(t('Engine busy, try again shortly'), 'error');
        }
    },

    _toggleBulkRemoveDropdown(caretBtn) {
        // Toggle dropdown
        const existing = document.getElementById('bulk-remove-drop');
        if (existing) { existing.remove(); return; }

        const drop = document.createElement('div');
        drop.id = 'bulk-remove-drop';
        const rect = caretBtn.closest('#bulk-remove-wrap').getBoundingClientRect();
        drop.style.cssText = `
            position:fixed;z-index:9999;
            top:${rect.bottom + 4}px;right:${window.innerWidth - rect.right}px;
            background:var(--bg-secondary,#1e2130);
            border:1px solid var(--border-color,rgba(255,255,255,.15));
            border-radius:8px;min-width:195px;overflow:hidden;
            box-shadow:0 8px 28px rgba(0,0,0,0.6);`;

        const items = [
            { icon:'fa-solid fa-trash', label:t('Rimuovi'), color:'#f87171',
              desc:'Mantieni i file sul disco',
              fn: () => this.bulkRemoveTorrents(false) },
            { icon:'fa-solid fa-trash-can', label:t('Rimuovi ed Elimina'), color:'#ef4444',
              desc:'Cancella anche i file scaricati',
              fn: () => this.bulkRemoveTorrents(true) },
        ];
        items.forEach((item, i) => {
            const btn = document.createElement('button');
            btn.style.cssText = `display:flex;align-items:center;gap:10px;width:100%;
                padding:10px 14px;background:none;border:none;
                ${i < items.length-1 ? 'border-bottom:1px solid rgba(255,255,255,.07);' : ''}
                color:#e2e8f0;cursor:pointer;text-align:left;`;
            btn.innerHTML = `
                <i class="${item.icon}" style="color:${item.color};width:16px;text-align:center;flex-shrink:0"></i>
                <div>
                  <div style="font-size:.84rem;font-weight:600">${item.label}</div>
                  <div style="font-size:.72rem;color:rgba(255,255,255,.4);margin-top:1px">${item.desc}</div>
                </div>`;
            btn.onmouseenter = () => btn.style.background = 'rgba(255,255,255,.06)';
            btn.onmouseleave = () => btn.style.background = 'none';
            btn.onclick = (e) => { e.stopPropagation(); drop.remove(); item.fn(); };
            drop.appendChild(btn);
        });
        document.body.appendChild(drop);
        const close = (e) => {
            if (!drop.contains(e.target) && !caretBtn.contains(e.target)) {
                drop.remove();
                document.removeEventListener('click', close);
                document.removeEventListener('scroll', close, true);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', close);
            document.addEventListener('scroll', close, true);
        }, 0);
    },
    
    filterTorrentsByTag(tag) {
        currentTagFilter = tag;
        if (this._torrentLastData) this._renderTorrentRows(this._torrentLastData);
    },

    _updateTagFilterDropdown() {
        const select = document.getElementById('torrent-tag-filter');
        if (!select) return;
        
        // Protezione contro i tag vuoti o corrotti
        const uniqueTags = [...new Set(Object.values(torrentTagsDb).filter(Boolean))].sort();
        
        let html = `<option value="all">🏷️ ${t('Tutti i Tag')}</option><option value="untagged">${t('Senza Tag')}</option>`;
        uniqueTags.forEach(tag => {
            html += `<option value="${this._esc(tag)}" ${currentTagFilter === tag ? 'selected' : ''}>${this._esc(tag)}</option>`;
        });
        select.innerHTML = html;
        
        // Se il filtro attuale non esiste più, torna a 'Tutti i tag'
        if (currentTagFilter !== 'all' && currentTagFilter !== 'untagged' && !uniqueTags.includes(currentTagFilter)) {
            currentTagFilter = 'all';
        }
        select.value = currentTagFilter; 
    },

   bulkSetTorrentTags() {
        const hashes = this._getSelectedHashes();
        if (!hashes.length) { this.showToast(t('Nessun torrent selezionato'), 'warning'); return; }

        // Raccoglie tutti i tag salvati
        const tagSet = new Set(Object.values(torrentTagsDb).filter(Boolean));
        const filterSel = document.getElementById('torrent-tag-filter');
        if (filterSel) {
            [...filterSel.options].forEach(opt => {
                if (opt.value && opt.value !== 'all' && opt.value !== 'untagged') {
                    tagSet.add(opt.value);
                }
            });
        }
        const uniqueTags = [...tagSet].sort();
        
        // Disegna i bottoncini (badge) per ogni tag
        const container = document.getElementById('existing-tags-container');
        if (container) {
            container.innerHTML = uniqueTags.map(tg => `
                <span class="badge badge-secondary" style="padding: 6px 10px; display: inline-flex; align-items: center; gap: 8px; font-size: 0.8rem; border: 1px solid rgba(255,255,255,0.1); text-transform: none; letter-spacing: normal;">
                    <span style="cursor:pointer; font-weight:600;" onclick="document.getElementById('set-tag-input').value='${this._esc(tg)}'">${this._esc(tg)}</span>
                    <i class="fa-solid fa-xmark" style="cursor:pointer; color: var(--danger); padding-left: 4px; border-left: 1px solid rgba(255,255,255,0.2);" title="Elimina definitivamente questo Tag" onclick="app.deleteSpecificTag('${this._esc(tg)}')"></i>
                </span>
            `).join('');
        }
        
        document.getElementById('set-tag-input').value = '';
        document.getElementById('set-tag-modal').classList.add('active');
    },

    // ── Helper: pulisce dal JSON il tag di un hash rimosso,
    //    ma solo se esiste un altro hash con lo stesso tag (altrimenti lo lascia come segnaposto)
    // Disinneschiamo l'automazione: i tag non si cancellano mai da soli
    async _cleanTagOnRemove(hashes) {
        return; // Non fa assolutamente nulla. I tag vivono per sempre.
    },

    // Nuova funzione per cancellare un singolo tag quando premi la "X"
    async deleteSpecificTag(tag) {
        if (!confirm(`${t('Vuoi eliminare definitivamente il tag')} "${tag}" ${t('da tutti i torrent e dalla memoria?')}`)) return;

        const payload = {};
        let hasChanges = false;
        
        // Trova tutti i torrent (vivi e passati) che hanno questo tag
        for (const [h, t] of Object.entries(torrentTagsDb)) {
            if (t === tag) {
                payload[h] = ''; // Comanda al server di svuotarlo
                delete torrentTagsDb[h]; // Lo cancella dalla memoria locale
                hasChanges = true;
            }
        }

        try {
            if (hasChanges) {
                await fetch(`${API_BASE}/api/torrent-tags`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            }
            
            this.showToast(`Tag "${tag}" eliminato`, 'success');
            
            // Aggiorna in tempo reale la grafica
            this._updateTagFilterDropdown();
            this.bulkSetTorrentTags(); // Ridisegna i bottoncini rimasti
            if (this._torrentLastData) this._renderTorrentRows(this._torrentLastData); // Aggiorna la lista principale
            
        } catch(e) {
            this.showToast(t('Errore durante l\'eliminazione del tag'), 'error');
        }
    },

    // ── Helper: estrae info_hash da magnet link
    _hashFromMagnet(magnet) {
        if (!magnet) return null;
        const m = (magnet || '').match(/btih:([a-fA-F0-9]{40})/i);
        return m ? m[1].toLowerCase() : null;
    },

    // ── Helper: salva tag nel torrent_tags.json via API.
    // Mantiene UN solo record per tag: se il tag esiste già in torrentTagsDb
    // (con qualsiasi hash) aggiorna solo la mappa locale senza chiamare l'API,
    // così torrent_tags.json resta compatto (N righe = N tag distinti).
    async _saveTag(hash, tag) {
        if (!hash || !tag) return;
        
        // Aggiorna sempre la mappa locale per il filtraggio UI
        torrentTagsDb[hash] = tag;
        this._updateTagFilterDropdown();
        
        // Comunica SEMPRE il nuovo tag al Database del server
        try {
            await fetch(`${API_BASE}/api/torrent-tags`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ [hash]: tag })
            });
        } catch (_) { /* tag save: non critico */ }
    },

    async _saveNoRename(hash, value) {
        if (!hash) return;
        try {
            await fetch(`${API_BASE}/api/torrent-no-rename`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ [hash]: !!value })
            });
        } catch (_) { /* no-rename save: non critico */ }
    },

    // Mostra il mini-modal di conferma con checkbox no-rename per flussi senza modal proprio
    _promptNoRename(magnet, savePath = '', downloadNow = true, title = '', tag = '') {
        this._nrPending = { magnet, savePath, downloadNow, tag };
        const el = document.getElementById('no-rename-confirm-title');
        if (el) el.textContent = title || magnet.substring(0, 80) + '…';
        const cb = document.getElementById('no-rename-confirm-flag');
        if (cb) cb.checked = false;
        // Popola select tag
        const sel = document.getElementById('nr-confirm-tag');
        this._populateTagSelect(sel, tag).then(() => this._onNrConfirmTagChange(sel?.value || ''));
        document.getElementById('no-rename-confirm-modal').classList.add('active');
    },

    _onNrConfirmTagChange(tag) {
        const hint = document.getElementById('nr-confirm-tag-hint');
        if (!tag) { if (hint) hint.textContent = ''; return; }
        const rule = (this._tagDirRules || []).find(r => r.tag === tag);
        if (!rule) { if (hint) hint.textContent = ''; return; }
        if (hint) {
            const parts = [];
            if (rule.temp_dir) parts.push('Temp: ' + rule.temp_dir);
            if (rule.final_dir) parts.push('Finale: ' + rule.final_dir);
            hint.textContent = parts.join('  →  ');
        }
    },

    async _noRenameConfirmOk() {
        const noRename = document.getElementById('no-rename-confirm-flag')?.checked || false;
        this.closeModal('no-rename-confirm-modal');
        // Caso batch: risolve la Promise
        if (this._nrBatchResolve) {
            const resolve = this._nrBatchResolve;
            this._nrBatchResolve = null;
            resolve(noRename);
            return;
        }
        // Caso singolo
        if (!this._nrPending) return;
        const { magnet, savePath, downloadNow } = this._nrPending;
        const selectedTag = document.getElementById('nr-confirm-tag')?.value?.trim()
                         || this._nrPending.tag || '';
        this._nrPending = null;
        await this.sendMagnetToClient(magnet, savePath, downloadNow, noRename, selectedTag);
    },

    async confirmSetTorrentTags() {
        const hashes = this._getSelectedHashes();
        const tag = document.getElementById('set-tag-input').value.trim();
        const payload = {};
        hashes.forEach(h => payload[h] = tag);

        try {
            await fetch(`${API_BASE}/api/torrent-tags`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            
            hashes.forEach(h => {
                if (tag) torrentTagsDb[h] = tag;
                else delete torrentTagsDb[h];
            });
            
            this.showToast(t('Tag aggiornati con successo'), 'success');
            this.closeModal('set-tag-modal');
            this._updateTagFilterDropdown();
            if (this._torrentLastData) this._renderTorrentRows(this._torrentLastData);
        } catch (e) {
            this.showToast(t('Errore salvataggio tag'), 'error');
        }
    },
    
    // ========================================================================
    // DATABASE MAINTENANCE
    // ========================================================================
    async loadDbInfo(showFeedback = false) {
        if (showFeedback) this.showToast(t('Lettura database in corso...'), 'info');
        
        try {
            const res = await fetch(`${API_BASE}/api/db/info`);
            const data = await res.json();
            if(data.success) {
                document.getElementById('db-series-size').textContent = `${data.series_db.size_mb} MB`;
                document.getElementById('db-series-frag').textContent = `${data.series_db.frag}% frammentato`;
                
                document.getElementById('db-archive-size').textContent = `${data.archive_db.size_mb} MB`;
                document.getElementById('db-archive-frag').textContent = `${data.archive_db.frag}% frammentato`;
                document.getElementById('db-archive-count').textContent = new Intl.NumberFormat('it-IT').format(data.archive_count);

                if (data.config_db) {
                    const cfgSize = document.getElementById('db-config-size');
                    const cfgFrag = document.getElementById('db-config-frag');
                    if (cfgSize) cfgSize.textContent = `${data.config_db.size_mb} MB`;
                    if (cfgFrag) cfgFrag.textContent = `${data.config_db.frag}% frammentato`;
                }

                if (showFeedback) this.showToast(t('Statistiche del database aggiornate!'), 'success');
            }
        } catch(e) {}
    },

    async runDbAction(action) {
        if(!confirm(`Sei sicuro di voler eseguire ${action} su tutti i database? \nL'operazione potrebbe bloccare il sistema per qualche secondo se i file sono molto grandi.`)) return;
        
        this.showToast(`${t('Esecuzione')} ${action} ${t('in corso...')}`, "info");
        try {
            const res = await fetch(`${API_BASE}/api/db/action`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: action, target: 'both'})
            });
            const data = await res.json();
            if(data.success) {
                this.showToast(t(data.message), "success");
                this.loadDbInfo(); 
            } else {
                this.showToast(data.error, "error");
            }
        } catch(e) {}
    },

    async rescoreEpisodes() {
        if (!confirm(t('Ricalcola Score Episodi'))) return;
        this.showToast(t('Ricalcolo score episodi in corso...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/db/rescore-episodes`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(`✅ ${t(data.message)}`, 'success');
            } else {
                this.showToast(`❌ ${data.error}`, 'error');
            }
        } catch(e) {
            this.showToast(`❌ ${t("Errore:")} ${e.message}`, 'error');
        }
    },

    // ────────────────────────────────────────────────────────────────────────
    // SCORES & QUALITY SETTINGS (v33+)
    // ────────────────────────────────────────────────────────────────────────
    // ────────────────────────────────────────────────────────────────────────
    // SCORES & QUALITY SETTINGS (Punteggi Flat Additivi)
    // ────────────────────────────────────────────────────────────────────────
    _scoreDefaults: {
        bonus_dv: 300,
        bonus_real: 100,
        bonus_proper: 75,
        bonus_repack: 50,
        res_pref: {'2160p': 1000, '1080p': 500, '720p': 250, '576p': 50, '480p': 0, '360p': 0, 'unknown': 0},
        source_pref: {'bluray': 300, 'webdl': 200, 'webrip': 150, 'hdtv': 50, 'dvdrip': 20, 'unknown': 0},
        codec_pref: {'h265': 200, 'x265': 200, 'hevc': 200, 'h264': 50, 'x264': 50, 'avc': 50, 'unknown': 0},
        audio_pref: {'truehd': 150, 'dts-hd': 120, 'dts': 100, 'ddp': 80, 'ac3': 50, '5.1': 50, 'aac': 30, 'mp3': 10, 'unknown': 0},
        group_pref: {'mircrew': 50, 'nahom': 30, 'TheBlackKing': 30, 'unknown': 0}
    },

    _updateBonusLangLabel(lang) {
        const langUp = (lang || 'ITA').toUpperCase();
        const el1 = document.getElementById('label-bonus-ita');
        if (el1) el1.textContent = t('Bonus Lingua') + ' (' + langUp + ')';
        const el2 = document.getElementById('label-sim-lang');
        if (el2) el2.textContent = langUp;
    },

    async loadScoresSettings() {
        try {
            // Aggiungiamo un timestamp per evitare che il browser usi dati vecchi (Cache Busting)
            const r = await fetch(`${API_BASE}/api/scores/settings?_t=` + Date.now());
            const cfg = await r.json();
            
            this._currentScores = cfg;

            // Aggiorna i label "Bonus Lingua" con la lingua effettiva
            this._updateBonusLangLabel(this._primaryLang);

            // 1. Popola i Bonus fissi
            const setVal = (id, val) => { const el = document.getElementById(id); if(el) el.value = val !== undefined ? val : 0; };
            setVal('score-bonus-ita', cfg.bonus_ita);
            setVal('score-bonus-dv', cfg.bonus_dv);
            setVal('score-bonus-real', cfg.bonus_real);
            setVal('score-bonus-proper', cfg.bonus_proper);
            setVal('score-bonus-repack', cfg.bonus_repack);
            
            const autoRmEl = document.getElementById('auto-remove-completed');
            if (autoRmEl) autoRmEl.checked = cfg.auto_remove_completed || false;

            // 2. RENDER DINAMICO: Disegna i riquadri con i dati reali del file
            // Usiamo Object.keys().length per verificare che la mappa non sia vuota
            const render = (id, map, pref) => {
                if (map && Object.keys(map).length > 0) {
                    this._renderScoreMap(id, map, pref);
                } else {
                    // Fallback: se il file fosse vuoto, usa i default per non lasciare i box neri
                    this._renderScoreMap(id, this._scoreDefaults[`${pref}_pref`], pref);
                }
            };

            render('score-res-map-container', cfg.res_pref, 'res');
            render('score-source-map-container', cfg.source_pref, 'source');
            render('codec-map-container', cfg.codec_pref, 'codec'); // Assicurati che l'ID sia corretto
            render('score-audio-map-container', cfg.audio_pref, 'audio');
            render('score-group-map-container', cfg.group_pref, 'group');

            // 3. AGGIORNAMENTO SIMULATORE
            // Dobbiamo forzare il simulatore a ricaricare le nuove liste
            this._populateSimSelectors();
            this.updateScoreSimulation();

        } catch(e) { 
            console.error('loadScoresSettings error:', e); 
            this.showToast(t('Errore nel caricamento dei punteggi'), 'error');
        }
    },

    resetScoresToDefault() {
        if(!confirm(t('Ripristinare tutti i punteggi ai valori predefiniti di fabbrica?'))) return;
        this._currentScores = JSON.parse(JSON.stringify(this._scoreDefaults));
        this.loadScoresSettingsFromObject(this._currentScores);
        this.saveScoresSettings();
    },

    loadScoresSettingsFromObject(cfg) {
        document.getElementById('score-bonus-dv').value = cfg.bonus_dv;
        document.getElementById('score-bonus-real').value = cfg.bonus_real;
        document.getElementById('score-bonus-proper').value = cfg.bonus_proper;
        document.getElementById('score-bonus-repack').value = cfg.bonus_repack;
        
        this._renderScoreMap('score-res-map-container', cfg.res_pref, 'res');
        this._renderScoreMap('score-source-map-container', cfg.source_pref, 'source');
        this._renderScoreMap('codec-map-container', cfg.codec_pref, 'codec');
        this._renderScoreMap('score-audio-map-container', cfg.audio_pref, 'audio');
        this._renderScoreMap('score-group-map-container', cfg.group_pref, 'group');
        this.updateScoreSimulation();
    },

    updateScoreSimulation() {
        const simRes = document.getElementById('sim-res');
        const simSource = document.getElementById('sim-source');
        const simCodec = document.getElementById('sim-codec');
        const simAudio = document.getElementById('sim-audio');
        const simGroup = document.getElementById('sim-group'); // Aggiunto gruppo
        
        if(!simRes.options.length) this._populateSimSelectors();

        const resVal = parseInt(simRes.value) || 0;
        const sourceVal = parseInt(simSource.value) || 0;
        const codecVal = parseInt(simCodec.value) || 0;
        const audioVal = parseInt(simAudio.value) || 0;
        const groupVal = parseInt(simGroup ? simGroup.value : 0) || 0; // Aggiunto gruppo
        const dvVal = document.getElementById('sim-dv').checked ? (parseInt(document.getElementById('score-bonus-dv').value) || 300) : 0;

        // Somma totale aggiornata
        const total = resVal + sourceVal + codecVal + audioVal + groupVal + dvVal;
        document.getElementById('score-sim-total').textContent = total.toLocaleString('it-IT');
        
        // Costruzione testo riassuntivo aggiornata
        let breakdown = `<span>Risoluzione: ${resVal}</span>`;
        if(sourceVal) breakdown += `<span>+ Sorgente: ${sourceVal}</span>`;
        if(codecVal) breakdown += `<span>+ Codec: ${codecVal}</span>`;
        if(audioVal) breakdown += `<span>+ Audio: ${audioVal}</span>`;
        if(groupVal) breakdown += `<span>+ Gruppo: ${groupVal}</span>`; // Aggiunto gruppo
        if(dvVal) breakdown += `<span>+ DV: ${dvVal}</span>`;
        
        document.getElementById('score-sim-breakdown').innerHTML = breakdown;
    },

    _populateSimSelectors() {
        const fill = (el, containerId) => {
            const e = document.getElementById(el);
            if (!e) return;
            e.innerHTML = '';
            document.querySelectorAll(`#${containerId} .score-map-row`).forEach(row => {
                let label, val;
                // Gestisce le righe standard
                if (row.querySelector('label')) {
                    label = row.querySelector('label').textContent;
                    val = row.querySelector('input').value;
                } 
                // Gestisce le nuove righe dinamiche dei gruppi
                else if (row.classList.contains('custom-group-row')) {
                    label = row.querySelector('.custom-group-name').value;
                    val = row.querySelector('.custom-group-score').value;
                }
                
                if (label && label.trim() !== '') {
                    e.innerHTML += `<option value="${val}">${label}</option>`;
                }
            });
        };
        
        fill('sim-res', 'score-res-map-container');
        fill('sim-source', 'score-source-map-container');
        fill('sim-codec', 'codec-map-container');
        fill('sim-audio', 'score-audio-map-container');
        
        // Aggiungiamo anche il popolamento dei gruppi custom nel simulatore, se non c'era!
        const simGroup = document.getElementById('sim-group');
        if (simGroup) {
            simGroup.innerHTML = '<option value="0">Nessuno</option>';
            document.querySelectorAll('#score-group-map-container .custom-group-row').forEach(row => {
                const label = row.querySelector('.custom-group-name').value;
                const val = row.querySelector('.custom-group-score').value;
                if (label && label.trim() !== '') {
                    simGroup.innerHTML += `<option value="${val}">${label}</option>`;
                }
            });
        }
    },

    _renderScoreMap(containerId, map, prefix) {
        const container = document.getElementById(containerId);
        if(!container || !map) return;
        container.innerHTML = '';

        // Ordina e disegna QUALSIASI chiave arrivi dal server
        Object.keys(map).sort((a, b) => (parseInt(map[b]) || 0) - (parseInt(map[a]) || 0)).forEach(k => {
            if (prefix === 'group' && k === 'unknown') return; // Ignora unknown nei gruppi
            
            if (prefix === 'group') {
                this.addCustomGroupRow(k, map[k]);
            } else {
                const div = document.createElement('div');
                div.className = 'score-map-row';
                div.style.display = 'flex'; // layout, non visibilità — .score-map-row già imposta flex in CSS
                
                div.innerHTML = `
                    <label style="font-size:0.85rem;">${k}</label>
                    <input type="number" data-prefix="${prefix}" data-key="${k}" value="${map[k]}" 
                           class="score-map-input form-input" style="width:80px; text-align:center;"
                           oninput="app.updateScoreSimulation()">
                `;
                container.appendChild(div);
            }
        });
    },

    addCustomGroupRow(name = '', score = 0) {
        const container = document.getElementById('score-group-map-container');
        if (!container) return;
        
        const rowId = 'group_row_' + Date.now() + Math.floor(Math.random() * 1000);
        
        const div = document.createElement('div');
        div.className = 'score-map-row custom-group-row';
        div.id = rowId;
        div.style.display = 'flex'; // layout, non visibilità
        
        div.innerHTML = `
            <input type="text" class="form-input custom-group-name" placeholder="Nome Gruppo (es. MIRCrew)" value="${this._esc(name)}" style="flex: 1; font-size: 0.85rem; padding: 0.25rem 0.5rem;" oninput="app.updateScoreSimulation()">
            <input type="number" class="form-input custom-group-score" value="${score}" style="width: 80px; text-align: center; padding: 0.25rem 0.5rem;" oninput="app.updateScoreSimulation()">
            <button type="button" class="btn btn-sm btn-danger" onclick="document.getElementById('${rowId}').remove(); app.updateScoreSimulation();" title="Rimuovi" style="padding: 4px 8px; flex-shrink: 0;">
                <i class="fa-solid fa-trash"></i>
            </button>
        `;
        container.appendChild(div);
    },
    
    async saveScoresSettings() {
        try {
            const data = {
                bonus_dv: parseInt(document.getElementById('score-bonus-dv').value) || 0,
                bonus_real: parseInt(document.getElementById('score-bonus-real').value) || 0,
                bonus_proper: parseInt(document.getElementById('score-bonus-proper').value) || 0,
                bonus_repack: parseInt(document.getElementById('score-bonus-repack').value) || 0,
                auto_remove_completed: document.getElementById('auto-remove-completed')?.checked || false,
                res_pref: {}, source_pref: {}, codec_pref: {}, audio_pref: {}, group_pref: {}
            };

            // Raccoglie i dati dai riquadri standard (Risoluzione, Sorgente, Codec, Audio)
            document.querySelectorAll('.score-map-input').forEach(input => {
                const prefix = input.dataset.prefix;
                const key = input.dataset.key;
                const val = parseInt(input.value) || 0;
                if(prefix === 'res') data.res_pref[key] = val;
                else if(prefix === 'source') data.source_pref[key] = val;
                else if(prefix === 'codec') data.codec_pref[key] = val;
                else if(prefix === 'audio') data.audio_pref[key] = val;
            });
            
            // Raccoglie i dati DALLE RIGHE DINAMICHE DEI GRUPPI
            document.querySelectorAll('.custom-group-row').forEach(row => {
                let name = row.querySelector('.custom-group-name').value.trim();
                let score = parseInt(row.querySelector('.custom-group-score').value) || 0;
                
                if (name) {
                    // Rimuove gli spazi interni dal nome prima di salvare
                    name = name.replace(/\s+/g, '');
                    data.group_pref[name] = score;
                }
            });

            const r = await fetch(`${API_BASE}/api/scores/settings`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(data)
            });
            const j = await r.json();
            this.showToast(j.success ? t('Punteggi salvati!') : (t('Errore') + ': ' + j.error), j.success ? 'success' : 'error');
            
            this._populateSimSelectors();
            this.updateScoreSimulation();
        } catch(e) { this.showToast(t('Errore salvataggio punteggi'), 'error'); }
    },

    // ────────────────────────────────────────────────────────────────────────
    // BACKUP
    // ────────────────────────────────────────────────────────────────────────
    onCloudTypeChange() {
        const typeEl = document.getElementById('backup-cloud-type');
        const type = typeEl ? typeEl.value : 'none';
        
        const container = document.getElementById('cloud-fields');
        const helpMsg = document.getElementById('cloud-help-msg');
        const helpText = document.getElementById('cloud-help-text');
        
        const hostGroup = document.getElementById('cloud-host-group');
        const hostLabel = document.getElementById('cloud-host-label');
        const hostInput = document.getElementById('backup-cloud-host');
        
        const userLabel = document.getElementById('cloud-user-label');
        const passLabel = document.getElementById('cloud-pass-label');
        const pathLabel = document.getElementById('cloud-path-label');

        if (!container) return;
        
        if (type === 'none') {
            hideEl(container);
            return;
        }
        
        showEl(container);
        showEl(helpMsg);
        
        // Reset defaults
        showEl(hostGroup);
        showEl('cloud-auth-group');
        
        if (type === 'ftp') {
            helpText.innerHTML = "Usa <strong>Host</strong> (es. ftp.nas.com), <strong>Utente</strong> e <strong>Password</strong>. Il percorso deve essere relativo alla root FTP.";
            hostLabel.textContent = "Host FTP";
            hostInput.placeholder = "ftp.example.com";
            userLabel.textContent = "Utente FTP";
            passLabel.textContent = "Password FTP";
            pathLabel.textContent = "Percorso Remoto (es. /backups)";
        } 
        else if (type === 'dropbox') {
            helpText.innerHTML = "Crea una 'App' su Dropbox Console, ottieni un <strong>Access Token</strong> e inseriscilo nel campo Password. Lascia vuoti Host e Utente.";
            hideEl(hostGroup);
            userLabel.textContent = "(non usato)";
            passLabel.textContent = "Dropbox Access Token";
            pathLabel.textContent = "Percorso (es. /EXTTO_Backups)";
        }
        else if (type === 'gdrive') {
            helpText.innerHTML = "Inserisci l'<strong>ID della cartella</strong> nel campo Percorso. Inserisci il <strong>Refresh Token</strong> nel campo Password. Richiede client_id e client_secret.";
            hostLabel.textContent = "Google Client ID";
            hostInput.placeholder = "xxx.apps.googleusercontent.com";
            userLabel.textContent = "Google Client Secret";
            passLabel.textContent = "Refresh Token";
            pathLabel.textContent = "Folder ID (es. 1abc...)";
        }
        else if (type === 'onedrive') {
            helpText.innerHTML = "Inserisci il <strong>Refresh Token</strong> nel campo Password. Host e Utente servono per Client ID e Secret.";
            hostLabel.textContent = "Microsoft Client ID";
            hostInput.placeholder = "guid-id-xxx";
            userLabel.textContent = "Client Secret";
            passLabel.textContent = "Refresh Token";
            pathLabel.textContent = "Percorso (es. /Backups)";
        }
    },

    async loadBackupSettings() {
        try {
            const r = await fetch(`${API_BASE}/api/backup/settings?_t=` + Date.now());
            const cfg = await r.json();
            const dirEl  = document.getElementById('backup-dir');
            const retEl  = document.getElementById('backup-retention');
            const schedEl = document.getElementById('backup-schedule');
            if (dirEl)   dirEl.value   = cfg.backup_dir  || '';
            if (retEl)   retEl.value   = cfg.retention   || 5;
            if (schedEl) schedEl.value = cfg.schedule    || 'manual';

            // Cloud settings
            const cType = cfg.cloud_type || 'none';
            const cTypeEl = document.getElementById('backup-cloud-type');
            if (cTypeEl) {
                cTypeEl.value = cType;
                this.onCloudTypeChange();
            }
            
            const hostEl = document.getElementById('backup-cloud-host');
            const userEl = document.getElementById('backup-cloud-user');
            const passEl = document.getElementById('backup-cloud-pass');
            const pathEl = document.getElementById('backup-cloud-path');
            
            if (hostEl) hostEl.value = cfg.cloud_host || '';
            if (userEl) userEl.value = cfg.cloud_user || '';
            if (passEl) passEl.value = cfg.cloud_pass || '';
            if (pathEl) pathEl.value = cfg.cloud_path || '/';

            // Checkbox invio Telegram
            const tgChk = document.getElementById('backup-send-telegram');
            if (tgChk) tgChk.checked = cfg.send_telegram === true || cfg.send_telegram === 'true';

            await this.loadBackupList();
        } catch(e) { console.error('loadBackupSettings', e); }
    },

    async saveBackupSettings() {
        try {
            const data = {
                backup_dir: document.getElementById('backup-dir')?.value?.trim() || '',
                retention:  parseInt(document.getElementById('backup-retention')?.value || '5'),
                schedule:   document.getElementById('backup-schedule')?.value || 'manual',
                cloud_type: document.getElementById('backup-cloud-type')?.value || 'none',
                cloud_host: document.getElementById('backup-cloud-host')?.value || '',
                cloud_user: document.getElementById('backup-cloud-user')?.value || '',
                cloud_pass: document.getElementById('backup-cloud-pass')?.value || '',
                cloud_path:    document.getElementById('backup-cloud-path')?.value || '/',
                send_telegram: document.getElementById('backup-send-telegram')?.checked || false
            };
            const r = await fetch(`${API_BASE}/api/backup/settings`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(data)
            });
            const j = await r.json();
            this.showToast(j.success ? t('Impostazioni backup salvate!') : (t('Errore') + ': ' + j.error), j.success ? 'success' : 'error');
        } catch(e) { this.showToast(t('Errore salvataggio impostazioni'), 'error'); }
    },

    async runBackup() {
        const btn = document.getElementById('backup-run-btn');
        const status = document.getElementById('backup-status');
        const sendTg = document.getElementById('backup-send-telegram')?.checked || false;
        if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${t('Backup in corso...')}`; }
        if (status) status.textContent = t('Creazione ZIP in corso, attendere…');
        try {
            const r = await fetch(`${API_BASE}/api/backup/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skip_notify: sendTg }) });
            const j = await r.json();
            if (j.success) {
                const msg = `✅ ${t('Backup creato')}: ${j.filename} (${j.zip_mb} MB, ${j.files} file, ${j.kept} ${t('backup conservati')})`;
                if (status) status.textContent = msg;
                this.showToast(t('Backup completato!'), 'success');
                await this.loadBackupList();

                // Se la spunta "Invia ZIP a gruppo Telegram" è attiva, invia subito
                if (sendTg && j.path) {
                    if (status) status.textContent = t('Backup creato, invio su Telegram in corso…');
                    if (btn) btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${t('Invio Telegram…')}`;
                    try {
                        const tgRes = await fetch(`${API_BASE}/api/backup/send-telegram`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: j.path })
                        });
                        const tgJ = await tgRes.json();
                        if (!tgJ.success) {
                            if (status) status.textContent = msg + ` — ⚠️ ${t('Errore Telegram')}: ` + tgJ.error;
                            this.showToast(t('Errore invio Telegram') + ': ' + tgJ.error, 'error');
                        } else if (tgJ.job_id) {
                            if (status) status.textContent = msg + ' — 📤 ' + t('Invio Telegram in corso...');
                            this._pollTelegramJob(
                                tgJ.job_id,
                                m => { if (status) status.textContent = msg + ' — ✅ ' + t('Inviato su Telegram!'); this.showToast('✅ ' + t(m), 'success'); },
                                e => { if (status) status.textContent = msg + ' — ⚠️ ' + e; this.showToast('❌ ' + e, 'error'); }
                            );
                        }
                    } catch(tgE) {
                        this.showToast(t('Errore rete Telegram'), 'error');
                    }
                }
            } else {
                if (status) status.textContent = `❌ ${t('Errore')}: ` + j.error;
                this.showToast(t('Errore backup') + ': ' + j.error, 'error');
            }
        } catch(e) {
            if (status) status.textContent = `❌ ${t('Errore di rete')}: ` + e.message;
            this.showToast(t('Errore di rete'), 'error');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = `<i class="fa-solid fa-box-archive"></i> ${t('Esegui Backup Ora')}`; }
        }
    },

    async loadBackupList() {
        try {
            const r = await fetch(`${API_BASE}/api/backup/list`);
            const j = await r.json();
            const container = document.getElementById('backup-list');
            if (!container) return;
            if (!j.backups || j.backups.length === 0) {
                container.innerHTML = `<span style="color:var(--text-muted);font-size:0.8rem;">${t('Nessun backup trovato.')}</span>`;
                return;
            }
            container.innerHTML = j.backups.map((b, i) => `
                <div style="display:flex;align-items:center;gap:0.5rem;padding:0.35rem 0.5rem;background:var(--bg-input);border-radius:0.4rem;font-size:0.82rem;">
                    <i class="fa-solid fa-file-zipper" style="color:var(--primary-light);flex-shrink:0;"></i>
                    <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${this.escapeHtml(b.path)}">${this.escapeHtml(b.name)}</span>
                    <span style="color:var(--text-muted);flex-shrink:0;">${b.size_mb} MB</span>
                    <span style="color:var(--text-muted);flex-shrink:0;font-size:0.75rem;">${b.date}</span>
                    ${i === 0 ? '<span style="background:rgba(59,130,246,0.2);color:#60a5fa;border-radius:3px;padding:0 4px;font-size:0.7rem;flex-shrink:0;">ULTIMO</span>' : ''}
                    <button class="btn btn-small" style="padding: 4px 8px; margin-left: auto; flex-shrink: 0; font-size: 0.75rem; background: rgba(14,165,233,0.15); color: #38bdf8; border: 1px solid rgba(14,165,233,0.3);" title="Invia questo backup su Telegram" onclick="app.sendBackupToTelegram('${this.escapeJs(b.path)}')">
                        <i class="fa-brands fa-telegram"></i> Invia
                    </button>
                </div>
            `).join('');
        } catch(e) { console.error('loadBackupList', e); }
    },


    // Polling helper per job upload Telegram asincrono
    async _pollTelegramJob(jobId, onSuccess, onError) {
        const MAX_POLLS = 100;  // 100 x 3s = 5 minuti massimo
        for (let i = 0; i < MAX_POLLS; i++) {
            await new Promise(r => setTimeout(r, 3000));
            try {
                const r = await fetch(`${API_BASE}/api/backup/send-telegram/status?job=${jobId}`);
                const d = await r.json();
                if (!d.success) { onError(d.error || 'Errore polling'); return; }
                if (d.status === 'ok')    { onSuccess(d.message); return; }
                if (d.status === 'error') { onError(d.message);   return; }
                // status === 'pending': continua polling
            } catch(e) {
                onError(e.message);
                return;
            }
        }
        onError("Timeout: l'upload ha impiegato troppo tempo");
    },

    async sendBackupToTelegram(path) {
        if(!confirm(t('Vuoi inviare questo backup di EXTTO al gruppo Telegram configurato?'))) return;
        this.showToast(t('Upload su Telegram avviato in background...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/backup/send-telegram`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path })
            });
            const data = await res.json();
            if (!data.success) {
                this.showToast(data.error || t('Errore'), 'error');
                return;
            }
            // Upload asincrono: fai polling finché non completa
            this.showToast(t('Upload in corso... riceverai una notifica al termine'), 'info');
            this._pollTelegramJob(
                data.job_id,
                msg => this.showToast('✅ ' + t(msg), 'success'),
                err => this.showToast('❌ ' + err,    'error')
            );
        } catch(e) {
            this.showToast(t("Errore di rete durante l'invio"), 'error');
        }
    },


    // ────────────────────────────────────────────────────────────────────────
    // TORRENT: PULISCI COMPLETATI
    // ────────────────────────────────────────────────────────────────────────
    // ────────────────────────────────────────────────────────────────────────
    // TORRENT: PULISCI COMPLETATI
    // ────────────────────────────────────────────────────────────────────────
    async removeCompletedTorrents() {
        try {
            let torrentCount = 0;
            let httpCount = 0;

            // 1. Pulisci torrent completati (via endpoint Flask che gestisce libtorrent)
            try {
                const res = await fetch(`${API_BASE}/api/torrents/remove_completed`, { method: 'POST' });
                if (res.ok) {
                    const d = await res.json();
                    torrentCount = d.removed ?? 0;
                }
            } catch(e) { /* libtorrent non disponibile */ }

            // 2. Pulisci download HTTP/Mega completati (ACTIVE_HTTP_DOWNLOADS)
            try {
                const res2 = await fetch(`${API_BASE}/api/http-downloads/remove-completed`, { method: 'POST' });
                if (res2.ok) {
                    const d2 = await res2.json();
                    httpCount = d2.removed ?? 0;
                }
            } catch(e) { /* ignora */ }

            const total = torrentCount + httpCount;
            if (total === 0) {
                this.showToast(t('Nessun download completato da rimuovere.'), 'info');
            } else {
                const parts = [];
                if (torrentCount > 0) parts.push(`${torrentCount} torrent`);
                if (httpCount > 0)    parts.push(`${httpCount} download HTTP/Mega`);
                this.showToast(`${t('Rimossi')}: ${parts.join(', ')}.`, 'success');
            }

            await this.loadTorrents();

        } catch(e) {
            this.showToast(t('Errore durante la pulizia'), 'error');
        }
    },               


    setPruneDays(v) {
        const el = document.getElementById('prune-days-input');
        if (el) el.value = v;
    },

    async cleanTrash() {
        if (!confirm(t('Eliminare definitivamente i file nel cestino più vecchi del numero di giorni configurato?'))) return;
        this.showToast(t('Pulizia cestino in corso...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/maintenance/clean-trash`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
            } else {
                this.showToast(data.error || t('Errore durante la pulizia'), 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di connessione'), 'error');
        }
    },

    async pruneArchive() {
        const el = document.getElementById('prune-days-input');
        let days = el ? parseInt(el.value, 10) : NaN;
        if (isNaN(days) || days < 0) {
            this.showToast(t('Inserire un numero di giorni valido (>= 0).'), 'warning');
            return;
        }

        // Carica anteprima prima di chiedere conferma
        let preview = null;
        try {
            const pr = await fetch(`${API_BASE}/api/db/prune/preview`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({days})
            });
            const pd = await pr.json();
            if (pd.success) preview = pd;
        } catch(e) {}

        const confirmed = await this._pruneConfirmModal(days, preview);
        if (!confirmed) return;

        this.showToast(t('Pulizia in corso...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/db/prune`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({days: String(days)})
            });
            const data = await res.json();
            if(data.success) {
                this.showToast(data.message, 'success');
                if((data.deleted || 0) > 1000) {
                    setTimeout(() => {
                        if(confirm(t('Hai eliminato molti record. Vuoi eseguire un VACUUM ora per recuperare i Megabyte sul disco?'))) {
                            this.runDbAction('VACUUM');
                        } else {
                            this.loadDbInfo();
                        }
                    }, 1000);
                } else {
                    this.loadDbInfo();
                }
            } else {
                this.showToast(data.error, 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    _pruneConfirmModal(days, preview) {
        return new Promise(resolve => {
            const existing = document.getElementById('prune-preview-modal');
            if (existing) existing.remove();

            let bodyHtml = '';
            if (preview) {
                const pct = preview.total > 0 ? Math.round(preview.to_delete / preview.total * 100) : 0;
                bodyHtml = `<p style="margin:0 0 0.8rem 0;">
                    Verranno eliminati <strong>${preview.to_delete.toLocaleString()}</strong> record
                    su <strong>${preview.total.toLocaleString()}</strong> totali (${pct}%)<br>
                    <span style="font-size:0.82rem;color:var(--text-muted);">Soglia: più vecchi del ${preview.cutoff_date}</span>
                </p>`;
                if (preview.sources && preview.sources.length) {
                    bodyHtml += `<div style="font-size:0.83rem;background:var(--bg-secondary);border-radius:6px;padding:0.5rem 0.75rem;margin-bottom:0.75rem;">
                        <strong style="display:block;margin-bottom:0.25rem;">Per fonte:</strong>`;
                    preview.sources.forEach(s => {
                        bodyHtml += `<div style="display:flex;justify-content:space-between;gap:1rem;">
                            <span style="color:var(--text-secondary)">${this.escapeHtml(s.source)}</span>
                            <span>${s.count.toLocaleString()}</span>
                        </div>`;
                    });
                    bodyHtml += `</div>`;
                }
                if (preview.samples && preview.samples.length) {
                    bodyHtml += `<div style="font-size:0.78rem;color:var(--text-muted);">
                        <strong>I più vecchi:</strong><br>`;
                    preview.samples.forEach(s => {
                        const title = s.title.length > 58 ? s.title.slice(0,58) + '…' : s.title;
                        bodyHtml += `<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                            title="${this.escapeHtml(s.title)}">${s.added_at} — ${this.escapeHtml(title)}</div>`;
                    });
                    bodyHtml += `</div>`;
                }
                if (preview.to_delete === 0) {
                    bodyHtml = `<p style="color:var(--text-muted);font-style:italic;">
                        Nessun record da eliminare con questa soglia (${days} giorni).</p>`;
                }
            } else {
                bodyHtml = `<p>Eliminare i record più vecchi di <strong>${days} giorni</strong>?</p>`;
            }

            const modal = document.createElement('div');
            modal.id = 'prune-preview-modal';
            modal.className = 'modal active';
            modal.innerHTML = `
                <div class="modal-content" style="max-width:480px;">
                    <div class="modal-header">
                        <h3 style="margin:0;font-size:1rem;">
                            <i class="fa-solid fa-broom" style="color:var(--warning);margin-right:8px;"></i>
                            Anteprima Pulizia Archivio
                        </h3>
                        <button class="modal-close" id="prune-modal-close">&times;</button>
                    </div>
                    <div class="modal-body">${bodyHtml}</div>
                    <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:10px;">
                        <button class="btn btn-secondary" id="prune-modal-cancel">Annulla</button>
                        <button class="btn btn-warning" id="prune-modal-confirm"
                            ${preview && preview.to_delete === 0 ? 'disabled' : ''}>
                            <i class="fa-solid fa-trash"></i>
                            ${preview && preview.to_delete > 0 ? `Elimina ${preview.to_delete.toLocaleString()} record` : 'Elimina'}
                        </button>
                    </div>
                </div>`;
            document.body.appendChild(modal);

            const cleanup = result => { modal.remove(); resolve(result); };
            document.getElementById('prune-modal-close').onclick  = () => cleanup(false);
            document.getElementById('prune-modal-cancel').onclick = () => cleanup(false);
            document.getElementById('prune-modal-confirm').onclick = () => cleanup(true);
            modal.addEventListener('click', e => { if (e.target === modal) cleanup(false); });
        });
    },

    async pruneByKeywordSearch() {
        const el  = document.getElementById('prune-keyword-input');
        const raw = el ? el.value.trim() : '';
        if (!raw) { this.showToast(t('Inserisci almeno una parola chiave'), 'warning'); return; }

        const resBox = document.getElementById('prune-keyword-result');
        if (resBox) resBox.innerHTML = `<div style="color:var(--text-muted);padding:0.5rem 0;"><i class="fa-solid fa-spinner fa-spin"></i> ${t('Ricerca in corso...')}</div>`;

        try {
            const res  = await fetch(`${API_BASE}/api/db/prune-keyword`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({keywords: raw, limit: 500})
            });
            const data = await res.json();
            if (!data.success) {
                if (resBox) resBox.innerHTML = `<span style="color:var(--danger)">❌ ${data.error}</span>`;
                return;
            }
            this._pruneKwRenderResults(data, resBox);
        } catch(e) {
            if (resBox) resBox.innerHTML = `<span style="color:var(--danger)">${t('Errore di rete')}</span>`;
        }
    },

    pruneAddScriptFilter(token) {
        const el = document.getElementById('prune-keyword-input');
        if (!el) return;
        const cur = el.value.trim();
        if (!cur.includes(token)) el.value = cur ? cur + ', ' + token : token;
        el.focus();
    },

    contentFilterAddScript(token) {
        const el = document.getElementById('content-filter-list');
        if (!el) return;
        const lines = el.value.split('\n').map(l => l.trim()).filter(Boolean);
        if (!lines.includes(token)) { lines.push(token); el.value = lines.join('\n'); }
        el.focus();
    },

    _pruneKwRenderResults(data, resBox) {
        if (!resBox) return;
        const rows  = data.rows || [];
        const total = data.total || 0;
        const ret   = data.returned || rows.length;
        const _SCRIPT_TOKENS = ['[cjk]','[cirillico]','[arabo]','[ebraico]','[thai]','[non-latino]'];
        const kws = (data.keywords || []).map(k => {
            const isScript = _SCRIPT_TOKENS.includes(k.toLowerCase());
            return isScript
                ? `<code style="background:rgba(99,102,241,0.2);padding:1px 5px;border-radius:3px;"><i class="fa-solid fa-globe" style="font-size:.7em;margin-right:2px;"></i>${this.escapeHtml(k)}</code>`
                : `<code style="background:rgba(239,68,68,0.15);padding:1px 5px;border-radius:3px;">${this.escapeHtml(k)}</code>`;
        }).join(' ');

        if (total === 0) {
            resBox.innerHTML = `<div style="color:var(--success);padding:0.4rem 0;">✅ ${t('Nessun record trovato')} ${t('per')} ${kws}</div>`;
            return;
        }

        const truncNote = ret < total
            ? `<span style="color:var(--warning);font-size:0.78rem;"> — ${t('mostrati')} ${ret} ${t('di')} ${total}</span>` : '';

        let html = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;flex-wrap:wrap;gap:0.4rem;">
            <div style="font-size:0.88rem;">
                <strong style="color:var(--warning);">${total}</strong> ${t('record trovati')} ${t('per')} ${kws}${truncNote}
            </div>
            <div style="display:flex;gap:0.4rem;align-items:center;flex-wrap:wrap;">
                <label style="font-size:0.8rem;color:var(--text-muted);cursor:pointer;display:flex;align-items:center;gap:4px;">
                    <input type="checkbox" id="pkw-check-all" onchange="app._pruneKwToggleAll(this.checked)"
                        style="width:14px;height:14px;accent-color:var(--danger);">
                    ${t('Seleziona tutti')}
                </label>
                <button class="btn btn-small btn-secondary" onclick="app._pruneKwToggleAll(false)">
                    <i class="fa-solid fa-square"></i> ${t('Deseleziona tutti')}
                </button>
                <button class="btn btn-small btn-danger" id="pkw-btn-delete" onclick="app.pruneByKeywordDeleteSelected()" disabled>
                    <i class="fa-solid fa-trash"></i> <span id="pkw-del-label">${t('Elimina selezionati')}</span>
                </button>
            </div>
        </div>
        <div style="max-height:320px;overflow-y:auto;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg-main);">
        <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
        <thead style="position:sticky;top:0;background:var(--bg-secondary);z-index:2;">
            <tr>
                <th style="width:32px;padding:6px 8px;"></th>
                <th style="padding:6px 8px;text-align:left;color:var(--text-muted);font-weight:600;">${t('Titolo')}</th>
                <th style="width:100px;padding:6px 8px;text-align:right;color:var(--text-muted);font-weight:600;">${t('Data')}</th>
                <th style="width:32px;padding:6px 4px;"></th>
            </tr>
        </thead>
        <tbody id="pkw-tbody">`;

        rows.forEach(row => {
            const safe = row.title.replace(/</g,'&lt;').replace(/>/g,'&gt;');
            const date = (row.added_at || '').substring(0, 10);
            html += `<tr data-id="${row.id}" style="border-bottom:1px solid rgba(255,255,255,0.04);">
                <td style="padding:5px 8px;">
                    <input type="checkbox" class="pkw-row-cb" data-id="${row.id}"
                        onchange="app._pruneKwUpdateCount()"
                        style="width:14px;height:14px;accent-color:var(--danger);cursor:pointer;">
                </td>
                <td style="padding:5px 8px;color:var(--text-primary);max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${safe}">${safe}</td>
                <td style="padding:5px 8px;color:var(--text-muted);text-align:right;white-space:nowrap;">${date}</td>
                <td style="padding:5px 4px;text-align:center;">
                    <button class="btn btn-small" style="padding:1px 6px;background:transparent;border:none;color:var(--text-muted);cursor:pointer;"
                        title="${t('Rimuovi dalla lista')}" onclick="app._pruneKwRemoveRow(${row.id})">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </td>
            </tr>`;
        });

        html += `</tbody></table></div>
        <div id="pkw-footer" style="font-size:0.78rem;color:var(--text-muted);margin-top:0.35rem;"></div>`;

        resBox.innerHTML = html;
        this._pruneKwUpdateCount();
    },

    _pruneKwToggleAll(checked) {
        document.querySelectorAll('.pkw-row-cb').forEach(cb => cb.checked = checked);
        const master = document.getElementById('pkw-check-all');
        if (master) master.checked = checked;
        this._pruneKwUpdateCount();
    },

    _pruneKwUpdateCount() {
        const checked = document.querySelectorAll('.pkw-row-cb:checked');
        const total   = document.querySelectorAll('.pkw-row-cb');
        const btn     = document.getElementById('pkw-btn-delete');
        const lbl     = document.getElementById('pkw-del-label');
        const footer  = document.getElementById('pkw-footer');
        const master  = document.getElementById('pkw-check-all');
        const n = checked.length;
        if (btn)  btn.disabled = n === 0;
        if (lbl)  lbl.textContent = n > 0 ? `${t('Elimina')} ${n} ${t('selezionati')}` : t('Elimina selezionati');
        if (footer) footer.textContent = n > 0 ? `${n} / ${total.length} ${t('selezionati')}` : '';
        if (master) master.checked = total.length > 0 && n === total.length;
    },

    _pruneKwRemoveRow(id) {
        const row = document.querySelector(`#pkw-tbody tr[data-id="${id}"]`);
        if (row) { row.remove(); this._pruneKwUpdateCount(); }
    },

    async pruneByKeywordDeleteSelected() {
        const checked = [...document.querySelectorAll('.pkw-row-cb:checked')];
        if (!checked.length) return;
        const ids = checked.map(cb => parseInt(cb.dataset.id));

        if (!confirm(`${t('Eliminare definitivamente')} ${ids.length} ${t('record selezionati dall\'archivio?')}\n\n${t('L\'operazione non è reversibile.')}`)) return;

        const btn = document.getElementById('pkw-btn-delete');
        if (btn) btn.disabled = true;
        this.showToast(t('Eliminazione in corso...'), 'info');

        try {
            const res  = await fetch(`${API_BASE}/api/db/prune-by-ids`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids})
            });
            const data = await res.json();
            if (data.success) {
                const deleted = data.deleted || 0;
                this.showToast(`${t('Eliminati')} ${deleted} ${t('record')}`, 'success');
                // Rimuove le righe eliminate dalla tabella
                checked.forEach(cb => {
                    const row = cb.closest('tr');
                    if (row) row.remove();
                });
                this._pruneKwUpdateCount();
                const footer = document.getElementById('pkw-footer');
                if (footer) footer.innerHTML = `<span style="color:var(--success)">✅ ${t('Eliminati')} ${deleted} ${t('record')}.</span>`;
                if (deleted > 500) {
                    setTimeout(() => {
                        if (confirm(t('Hai eliminato molti record. Vuoi eseguire un VACUUM ora per recuperare i Megabyte sul disco?'))) {
                            this.runDbAction('VACUUM');
                        } else { this.loadDbInfo(); }
                    }, 800);
                } else { this.loadDbInfo(); }
            } else {
                this.showToast(data.error || t('Errore eliminazione'), 'error');
                if (btn) btn.disabled = false;
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
            if (btn) btn.disabled = false;
        }
    },
    
    _getSelectedHashes() {
        return [...document.querySelectorAll('.torrent-checkbox:checked')].map(c => c.dataset.hash);
    },

    async searchTMDB(type, inputId, yearInputId = null) {
        const input = document.getElementById(inputId);
        const query = input.value.trim();
        if (!query) {
            this.showToast('Inserisci prima un pezzo di titolo da cercare!', 'warning');
            return;
        }

        const dropdown = document.getElementById(`tmdb-results-${inputId}`);
        dropdown.innerHTML = '<div style="padding:10px; text-align:center; color:var(--text-primary);"><i class="fa-solid fa-spinner fa-spin"></i> Ricerca su TMDB...</div>';
        dropdown.style.display = 'block';

        try {
            const res = await fetch(`${API_BASE}/api/tmdb/search?type=${type}&q=${encodeURIComponent(query)}`);
            const data = await res.json();

            if (!data.success) {
                dropdown.innerHTML = `<div style="padding:10px; color:var(--danger);">${this.escapeHtml(data.error)}</div>`;
                setTimeout(() => { dropdown.style.display = 'none'; }, 4000);
                return;
            }

            if (data.results.length === 0) {
                dropdown.innerHTML = `<div style="padding:10px; color:var(--text-muted);">${t('Nessun risultato trovato')}</div>`;
                setTimeout(() => { dropdown.style.display = 'none'; }, 3000);
                return;
            }

            dropdown.innerHTML = data.results.map(r => `
                <div class="tmdb-result-item" onclick="app.selectTMDBResult('${inputId}', '${this.escapeJs(r.title)}', '${yearInputId}', '${r.year}', '${r.id}')">
                    <strong style="color:var(--text-primary);">${this.escapeHtml(r.title)}</strong> <span style="color:var(--primary);">${r.year ? `(${r.year})` : ''}</span><br>
                    <small style="color:var(--text-muted)">Titolo originale: ${this.escapeHtml(r.original_title || '')}</small>
                </div>
            `).join('');

            const closeDropdown = (e) => {
                if (!dropdown.contains(e.target) && e.target !== input && !e.target.closest('.btn-secondary')) {
                    dropdown.style.display = 'none';
                    document.removeEventListener('click', closeDropdown);
                }
            };
            setTimeout(() => document.addEventListener('click', closeDropdown), 100);

        } catch (e) {
            dropdown.innerHTML = `<div style="padding:10px; color:var(--danger);">${t('Errore di connessione al server')}</div>`;
            setTimeout(() => { dropdown.style.display = 'none'; }, 3000);
        }
    },

    selectTMDBResult(inputId, title, yearInputId, year, tmdbId) {
        document.getElementById(inputId).value = title;
        if (yearInputId && year && yearInputId !== 'null') {
            const yearInput = document.getElementById(yearInputId);
            if (yearInput) yearInput.value = year;
        }
        
        // Estrae il prefisso (es: "series" da "series-name") e popola l'ID nascosto
        let prefix = inputId.replace('-name', '');
        const idInput = document.getElementById(prefix + '-tmdb-id');
        if (idInput && tmdbId) {
            idInput.value = tmdbId;
            app.showToast(`TMDB ID [${tmdbId}] ${t('agganciato con successo!')}`, 'success');
        }
        
        document.getElementById(`tmdb-results-${inputId}`).style.display = 'none';
    },

    async checkSourcesHealth() {
        const container = document.getElementById('sources-health-list');
        container.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-secondary);"><i class="fa-solid fa-spinner fa-spin fa-2x"></i><br><br>Test di connessione in corso (potrebbe richiedere circa 5-10 secondi se alcuni siti sono lenti)...</div>';
        
        try {
            const res = await fetch(`${API_BASE}/api/sources/health`);
            const data = await res.json();
            
            if (!data.success) {
                container.innerHTML = `<div style="color:var(--danger); padding:10px;">${this.escapeHtml(data.error)}</div>`;
                return;
            }
            
            let html = `<div style="display:grid; grid-template-columns: 2fr 3fr 90px 120px; gap:15px; font-weight:bold; font-size:0.8rem; color:var(--text-secondary); border-bottom:1px solid var(--border); padding-bottom:8px; margin-bottom:8px; text-transform:uppercase;">
                <div>Sorgente</div><div>URL</div><div>Latenza</div><div style="text-align:right;">Stato</div>
            </div>`;
            
            data.sources.forEach(s => {
                let statusBadge = '';
                if (s.status === 'online') statusBadge = `<span class="badge badge-success"><i class="fa-solid fa-check"></i> Online</span>`;
                else if (s.status === 'timeout') statusBadge = `<span class="badge badge-warning"><i class="fa-solid fa-clock"></i> Lento</span>`;
                else statusBadge = `<span class="badge badge-danger"><i class="fa-solid fa-xmark"></i> Offline</span>`;
                
                let pingColor = 'var(--text-primary)';
                if (s.ping !== '-' && s.ping > 2000) pingColor = 'var(--warning)';
                if (s.ping !== '-' && s.ping > 5000) pingColor = 'var(--danger)';
                
                html += `<div style="display:grid; grid-template-columns: 2fr 3fr 90px 120px; gap:15px; align-items:center; padding:10px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.9rem;">
                    <div style="font-weight:600; color:var(--text-primary);">${this.escapeHtml(s.name)}</div>
                    <div style="color:var(--text-secondary); font-family:var(--font-mono); font-size:0.8rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${this.escapeHtml(s.url)}">${this.escapeHtml(s.url)}</div>
                    <div style="font-family:var(--font-mono); font-weight:600; color:${pingColor};">${s.ping !== '-' ? s.ping + ' ms' : '-'}</div>
                    <div style="text-align:right; display:flex; flex-direction:column; align-items:flex-end;">
                        ${statusBadge}
                        ${s.error ? `<small style="color:var(--warning); font-size:0.75rem; margin-top:4px;">${this.escapeHtml(s.error)}</small>` : ''}
                    </div>
                </div>`;
            });
            container.innerHTML = html;
        } catch(e) {
            container.innerHTML = `<div style="color:var(--danger); padding:10px;">${t('Errore di rete. Server non raggiungibile.')}</div>`;
        }
    },
    // =========================================================================
    // FUMETTI — GETCOMICS
    // =========================================================================

    _comics: {
        tab: 'explore',
        searchTimer: null,
        monitored: [],
    },

    comicsSetTab(tab) {
        this._comics.tab = tab;
        ['explore','monitored','history','weekly'].forEach(t => {
            const btn   = document.getElementById(`comics-tab-${t}`);
            const panel = document.getElementById(`comics-panel-${t}`);
            if (btn)   btn.classList.toggle('active', t === tab);
            if (panel) showIf(panel, t === tab);
        });
        if (tab === 'monitored') this.comicsLoadMonitored();
        if (tab === 'history')   this.comicsLoadHistory();
        if (tab === 'weekly')    this.comicsLoadWeekly();
    },

    comicsSearchKeyup(e) {
        clearTimeout(this._comics.searchTimer);
        const q = e.target.value.trim();
        if (q.length < 2) {
            document.getElementById('comics-search-grid').innerHTML = '';
            return;
        }
        this._comics.searchTimer = setTimeout(() => this.comicsSearch(q), 450);
    },

    async comicsSearch(q) {
        const grid    = document.getElementById('comics-search-grid');
        const loading = document.getElementById('comics-search-loading');
        grid.innerHTML = '';
        showEl(loading);
        try {
            const res  = await fetch(`${API_BASE}/api/comics/search?q=${encodeURIComponent(q)}`);
            const data = await res.json();
            hideEl(loading);
            if (!data.success) {
                grid.innerHTML = `<p style="color:var(--danger)">${data.error}</p>`;
                return;
            }
            if (!data.results.length) {
                grid.innerHTML = `<p style="color:var(--text-muted);grid-column:1/-1;text-align:center;padding:2rem;">${t('Nessun risultato trovato.')}</p>`;
                return;
            }
            await this.comicsRefreshMonitored();
            const inList = new Set(this._comics.monitored.map(c => c.tag_url));
            grid.innerHTML = data.results.map(r => this._comicsRenderCard(r, inList)).join('');
            // Check link disponibili in background per ogni card
            data.results.forEach(r => r.url && this._comicsCheckAndActivateButtons(r.url));
        } catch(err) {
            hideEl(loading);
        }
    },

    _comicsRenderCard(r, inList) {
        if (!inList) inList = new Set();
        const already = inList.has(r.tag_url);
        const cover = r.cover_url
            ? `<img src="${r.cover_url}" alt="" loading="lazy" style="width:100%;aspect-ratio:2/3;object-fit:cover;border-radius:8px 8px 0 0;" onerror="this.style.display='none'">`
            : `<div style="width:100%;aspect-ratio:2/3;background:var(--bg-tertiary);border-radius:8px 8px 0 0;display:flex;align-items:center;justify-content:center;"><i class="fa-solid fa-book fa-3x" style="color:var(--text-muted)"></i></div>`;
        const pub  = r.publisher ? `<span class="badge badge-info" style="font-size:.7rem;padding:2px 7px;margin-bottom:.2rem;">${r.publisher}</span>` : '';
        const meta = [r.year, r.size].filter(Boolean).join(' · ');

        // Riga verde "In lista" separata dal corpo
        const inListBar = already
            ? `<div style="background:rgba(16,185,129,.12);border-top:1px solid rgba(52,211,153,.25);padding:4px 10px;font-size:.7rem;font-weight:700;color:#34d399;display:flex;align-items:center;gap:4px;"><i class="fa-solid fa-check-circle"></i> In lista</div>`
            : '';

        // Se già in lista: solo Scarica; altrimenti Monitora + Scarica
        const btnAdd = already ? ''
            : `<button class="btn btn-primary btn-small" style="flex:1;justify-content:center;font-size:.75rem;padding:4px 6px;" onclick='app.comicsShowAddModal(${JSON.stringify(r).replace(/"/g,"&quot;")})'><i class="fa-solid fa-plus"></i> Monitora</button>`;
        const cardId = `card-${btoa(r.url).replace(/[^a-zA-Z0-9]/g,'').slice(-12)}`;
        const btnInfo = r.url ? `<a href="${r.url}" target="_blank" rel="noopener" class="btn btn-small" style="padding:4px 8px;background:rgba(99,102,241,.15);color:#818cf8;border:1px solid rgba(99,102,241,.35);flex-shrink:0;display:flex;align-items:center;justify-content:center;" title="Apri pagina GetComics"><i class="fa-solid fa-circle-info"></i></a>` : '';
        // Pulsanti torrent/mega: inizialmente spinner, vengono aggiornati dopo check-links
        const btnTorrent = `<button id="${cardId}-tor" class="btn btn-secondary btn-small" style="flex:1;justify-content:center;font-size:.75rem;padding:4px 6px;" disabled title="Verifica in corso..." data-title="${this.escapeAttr(r.title)}"><i class="fa-solid fa-spinner fa-spin"></i></button>`;
        const btnMega    = `<button id="${cardId}-mega" class="btn btn-small" style="flex:1;justify-content:center;font-size:.75rem;padding:4px 6px;background:transparent;color:var(--text-muted);border:1px solid rgba(255,255,255,.12);" disabled title="Verifica in corso..." data-title="${this.escapeAttr(r.title)}"><i class="fa-solid fa-spinner fa-spin"></i></button>`;

        return `<div class="disc-card" style="display:flex;flex-direction:column;background:var(--bg-secondary);border-radius:8px;border:1px solid var(--border-color);transition:transform .2s,border-color .2s;" onmouseenter="this.style.transform='translateY(-3px)';this.style.borderColor='var(--primary)'" onmouseleave="this.style.transform='';this.style.borderColor='var(--border-color)'">
          <div style="position:relative;border-radius:8px 8px 0 0;overflow:hidden;">${cover}</div>
          <div style="padding:10px 12px;display:flex;flex-direction:column;gap:5px;flex:1;">
            ${pub}
            <div style="font-weight:600;font-size:.88rem;line-height:1.25;color:var(--text-primary);">${r.title}</div>
            ${meta ? `<div style="font-size:.73rem;color:var(--text-muted);">${meta}</div>` : ''}
            ${r.description ? `<div style="font-size:.73rem;color:var(--text-secondary);line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;">${r.description}</div>` : ''}
          </div>
          ${inListBar}
          <div style="padding:6px 10px;display:flex;flex-wrap:wrap;gap:5px;">${btnInfo}${btnAdd}${btnTorrent}${btnMega}</div>
        </div>`;
    },
    
    _comicsLinkCache: new Map(),

    async _comicsCheckAndActivateButtons(url) {
        const cardId = `card-${btoa(url).replace(/[^a-zA-Z0-9]/g,'').slice(-12)}`;
        const torBtn  = document.getElementById(`${cardId}-tor`);
        const megaBtn = document.getElementById(`${cardId}-mega`);
        if (!torBtn && !megaBtn) return;
        // Leggi il titolo dal data-title impostato al render della card
        const cardTitle = (megaBtn || torBtn)?.dataset?.title || '';
        // Usa cache se disponibile
        let d = this._comicsLinkCache.get(url);
        if (!d) {
            try {
                const res = await fetch(`${API_BASE}/api/comics/check-links?url=${encodeURIComponent(url)}`);
                d = await res.json();
                if (d.success) this._comicsLinkCache.set(url, d);
            } catch(e) { d = {}; }
        }
        // Pulsante Torrent/Magnet
        if (torBtn) {
            if (d.has_torrent) {
                torBtn.disabled = false;
                torBtn.innerHTML = '<i class="fa-solid fa-magnet"></i> Torrent';
                torBtn.title = 'Scarica via torrent/magnet';
                torBtn.style.opacity = '';
                torBtn.onclick = () => this.comicsDownloadDirect(url);
            } else {
                torBtn.innerHTML = '<i class="fa-solid fa-magnet"></i>';
                torBtn.title = 'Nessun torrent/magnet disponibile';
                torBtn.style.opacity = '0.3';
            }
        }
        // Pulsante Mega — adattivo:
        //   Mega+DDL → dropdown con scelta
        //   solo Mega → Mega diretto
        //   solo DDL  → "⬇ Scarica" verde (download HTTP diretto)
        //   nessuno   → grigio disabilitato
        if (megaBtn) {
            const hasMega = d.has_mega;
            const hasDdl  = d.has_ddl;
            const megaOk  = d.megatools_ok !== false; // true se non specificato (retrocompat)
            // Rimuovi dropdown precedente se esiste
            const oldDrop = document.getElementById(`${cardId}-mega-drop`);
            if (oldDrop) oldDrop.remove();

            if (hasMega && hasDdl) {
                // Entrambi disponibili → dropdown appendato al body (evita clip da overflow:hidden)
                megaBtn.disabled = false;
                megaBtn.style.opacity = '';
                megaBtn.style.background = 'rgba(99,102,241,.12)';
                megaBtn.style.color = '#818cf8';
                megaBtn.style.border = '1px solid rgba(129,140,248,.35)';
                megaBtn.innerHTML = '<i class="fa-solid fa-download"></i> Scarica <i class="fa-solid fa-caret-down" style="font-size:0.75em;margin-left:2px"></i>';
                megaBtn.title = 'Scegli metodo di download';
                megaBtn.onclick = (e) => {
                    e.stopPropagation();
                    // Toggle: se già aperto chiudi
                    const existing = document.getElementById(`${cardId}-mega-drop`);
                    if (existing) { existing.remove(); return; }

                    const drop = document.createElement('div');
                    drop.id = `${cardId}-mega-drop`;
                    // Posizione fixed calcolata dal bounding rect del bottone
                    const rect = megaBtn.getBoundingClientRect();
                    drop.style.cssText = `
                        position:fixed;z-index:9999;
                        top:${rect.bottom + 4}px;left:${rect.left}px;
                        background:var(--bg-secondary,#1e2130);
                        border:1px solid var(--border-color,rgba(255,255,255,.15));
                        border-radius:8px;min-width:175px;overflow:hidden;
                        box-shadow:0 8px 28px rgba(0,0,0,0.6);`;
                    const items = [
                        { icon:'fa-brands fa-m', label:'Mega', color: megaOk ? '#ef5350' : 'rgba(255,255,255,.25)',
                          disabled: !megaOk,
                          tooltip: megaOk ? '' : 'Installa megatools: apt install megatools',
                          fn: () => megaOk && this.comicsDownloadMega(url, d.mega_link, cardTitle) },
                        { icon:'fa-solid fa-download', label:t('Download Diretto'), color:'#22c55e',
                          disabled: false, tooltip: '',
                          fn: () => this.comicsDownloadHttpDirect(url, d.ddl_link, cardTitle) },
                    ];
                    items.forEach((item, i) => {
                        const btn = document.createElement('button');
                        btn.style.cssText = `display:flex;align-items:center;gap:10px;width:100%;
                            padding:11px 16px;background:none;border:none;
                            ${i < items.length-1 ? 'border-bottom:1px solid rgba(255,255,255,.07);' : ''}
                            color:${item.disabled ? 'rgba(255,255,255,.3)' : '#e2e8f0'};
                            cursor:${item.disabled ? 'not-allowed' : 'pointer'};
                            font-size:0.84rem;white-space:nowrap;`;
                        if (item.tooltip) btn.title = item.tooltip;
                        const iconHtml = item.icon === 'fa-brands fa-m'
                            ? `<span style="color:${item.color};width:18px;text-align:center;font-weight:800;font-size:1em;display:inline-block">M</span>`
                            : `<i class="${item.icon}" style="color:${item.color};width:18px;text-align:center;font-size:1em"></i>`;
                        btn.innerHTML = `${iconHtml}${item.label}${item.disabled ? ' <span style="font-size:0.75em;opacity:.6">(non disponibile)</span>' : ''}`;
                        if (!item.disabled) {
                            btn.onmouseenter = () => btn.style.background = 'rgba(255,255,255,.07)';
                            btn.onmouseleave = () => btn.style.background = 'none';
                        }
                        btn.onclick = (ev) => { ev.stopPropagation(); if (!item.disabled) { drop.remove(); item.fn(); } };
                        drop.appendChild(btn);
                    });
                    document.body.appendChild(drop);
                    // Chiudi cliccando fuori o scrollando
                    const closeHandler = (ev) => {
                        if (!drop.contains(ev.target) && ev.target !== megaBtn) {
                            drop.remove();
                            document.removeEventListener('click', closeHandler);
                            document.removeEventListener('scroll', closeHandler, true);
                        }
                    };
                    setTimeout(() => {
                        document.addEventListener('click', closeHandler);
                        document.addEventListener('scroll', closeHandler, true);
                    }, 0);
                };
            } else if (hasMega) {
                // Solo Mega
                if (megaOk) {
                    // megatools installato → bottone abilitato, outline rosso
                    megaBtn.disabled = false;
                    megaBtn.style.opacity = '';
                    megaBtn.style.background = 'rgba(239,83,80,.12)';
                    megaBtn.style.color = '#ef5350';
                    megaBtn.style.border = '1px solid rgba(239,83,80,.4)';
                    megaBtn.innerHTML = '<span style="font-weight:800;font-size:1em;letter-spacing:-.5px">M</span> Mega';
                    megaBtn.title = 'Scarica da Mega';
                    megaBtn.onclick = () => this.comicsDownloadMega(url, d.mega_link, cardTitle);
                } else {
                    // megatools NON installato → bottone disabilitato con tooltip install
                    megaBtn.disabled = true;
                    megaBtn.style.opacity = '0.45';
                    megaBtn.style.background = 'rgba(239,83,80,.06)';
                    megaBtn.style.color = '#ef5350';
                    megaBtn.style.border = '1px solid rgba(239,83,80,.2)';
                    megaBtn.innerHTML = '<span style="font-weight:800;font-size:1em;letter-spacing:-.5px">M</span> Mega';
                    megaBtn.title = 'megatools non installato — esegui: apt install megatools';
                    megaBtn.onclick = null;
                }
            } else if (hasDdl) {
                // Solo DDL → outline verde
                megaBtn.disabled = false;
                megaBtn.style.opacity = '';
                megaBtn.style.background = 'rgba(34,197,94,.1)';
                megaBtn.style.color = '#4ade80';
                megaBtn.style.border = '1px solid rgba(74,222,128,.35)';
                megaBtn.innerHTML = '<i class="fa-solid fa-download"></i> Scarica';
                megaBtn.title = 'Download diretto HTTP';
                megaBtn.onclick = () => this.comicsDownloadHttpDirect(url, d.ddl_link, cardTitle);
            } else {
                // Nessun link disponibile
                megaBtn.disabled = true;
                megaBtn.style.background = 'transparent';
                megaBtn.style.color = 'rgba(255,255,255,.2)';
                megaBtn.style.border = '1px solid rgba(255,255,255,.08)';
                megaBtn.innerHTML = '<span style="font-weight:800">M</span>';
                megaBtn.title = 'Nessun link disponibile';
                megaBtn.style.opacity = '';
            }
        }
    },

    // Download diretto HTTP (con tracciamento in ACTIVE_HTTP_DOWNLOADS via Flask)
    async comicsDownloadHttpDirect(postUrl, ddlLink, title) {
        if (!ddlLink) {
            const res = await fetch(`${API_BASE}/api/comics/check-links?url=${encodeURIComponent(postUrl)}`);
            const d   = await res.json();
            ddlLink   = d.ddl_link;
        }
        if (!ddlLink) { this.showToast(t('Link download non trovato'), 'error'); return; }
        this.showToast(t('Avvio download diretto...'), 'info');
        try {
            const res  = await fetch(`${API_BASE}/api/comics/download-direct`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url: ddlLink, title: title || 'fumetto' })
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message) || t('Download avviato!'), 'success');
            } else {
                this.showToast(data.error || t('Errore download'), 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    async comicsDownloadMega(postUrl, megaLink, title) {
        if (!megaLink) {
            const res = await fetch(`${API_BASE}/api/comics/check-links?url=${encodeURIComponent(postUrl)}`);
            const d   = await res.json();
            megaLink  = d.mega_link;
        }
        if (!megaLink) { this.showToast(t('Link Mega non trovato'), 'error'); return; }
        this.showToast(t('Avvio download Mega...'), 'info');
        try {
            const res  = await fetch(`${API_BASE}/api/comics/download-mega`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ mega_url: megaLink, title: title || 'fumetto' })
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message) || t('Download Mega avviato!'), 'success');
            } else {
                this.showToast(data.error || t('Errore download Mega'), 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    async comicsDownloadDirect(url) {
        this.showToast(t('Recupero file e invio al client...'), 'info');
        try {
            const res = await fetch(`${API_BASE}/api/comics/download-direct`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
            } else {
                this.showToast(data.error, 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    comicsShowAddModal(r) {
        const old = document.getElementById('comics-add-modal');
        if (old) old.remove();
        const today = new Date().toISOString().split('T')[0];
        const modal = document.createElement('div');
        modal.id = 'comics-add-modal';
        modal.className = 'modal active';
        modal.innerHTML = `
          <div class="modal-content" style="max-width:480px;">
            <div class="modal-header">
              <h3 style="margin:0; font-size:1.1rem; color:var(--primary-light);"><i class="fa-solid fa-book-open-reader"></i> Monitora Fumetto</h3>
              <button class="modal-close" onclick="document.getElementById('comics-add-modal').remove()">&times;</button>
            </div>
            <div class="modal-body" style="display:flex;flex-direction:column;gap:1.2rem;">
              <div style="display:flex;gap:1rem;align-items:flex-start; background:var(--bg-secondary); padding:10px; border-radius:8px; border:1px solid var(--border);">
                ${r.cover_url ? `<img src="${r.cover_url}" style="width:70px;border-radius:4px;flex-shrink:0;" onerror="this.style.display='none'">` : ''}
                <div>
                  <div style="font-weight:700;font-size:1rem;color:var(--text-primary); margin-bottom:5px;">${r.title}</div>
                  ${r.publisher ? `<div class="badge badge-info" style="font-size:.7rem;">${r.publisher}</div>` : ''}
                </div>
              </div>
              <div class="form-group" style="margin:0;">
                <label>${t('Scarica a partire dal')}</label>
                <input type="date" id="comics-add-fromdate" class="form-control" value="${today}">
              </div>
              <div class="form-group" style="margin:0;">
                <label>Cartella destinazione</label>
                <div style="display:flex; gap:8px;"><input type="text" id="comics-add-savepath" class="form-control" style="flex:1;" placeholder="es. /downloads/fumetti (vuoto = default client)"><button type="button" class="btn btn-secondary btn-small" title="Sfoglia cartelle del server" onclick="event.stopPropagation(); app.openDirBrowser('comics-add-savepath')"><i class="fa-regular fa-folder-open"></i></button></div>
              </div>
              <div style="font-size:.8rem;color:var(--text-muted);background:var(--bg-tertiary);padding:.75rem;border-radius:6px;">
                <i class="fa-solid fa-circle-info"></i> Tag intercettato: <code style="color:var(--primary-light);">${r.tag_url}</code>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" onclick="document.getElementById('comics-add-modal').remove()">Annulla</button>
              <button class="btn btn-primary" id="comics-add-confirm-btn"><i class="fa-solid fa-check"></i> Aggiungi</button>
            </div>
          </div>`;
        document.body.appendChild(modal);
        const confirmBtn = document.getElementById('comics-add-confirm-btn');
        if (confirmBtn) confirmBtn.addEventListener('click', () => {
            app.comicsAddConfirm(r);
        });
    },

    async comicsAddConfirm(r) {
        const fromDate = document.getElementById('comics-add-fromdate')?.value || '';
        const savePath = document.getElementById('comics-add-savepath')?.value || '';
        if (!fromDate) { this.showToast('Scegli una data di partenza', 'warning'); return; }
        try {
            const res  = await fetch(`${API_BASE}/api/comics`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    title: r.title, tag_url: r.tag_url,
                    cover_url: r.cover_url || '', publisher: r.publisher || '',
                    description: r.description || '',
                    from_date: fromDate, save_path: savePath,
                })
            });
            const data = await res.json();
            document.getElementById('comics-add-modal')?.remove();
            if (data.success) {
                this.showToast(`"${r.title}" ${t('Monitorati')}!`, 'success');
                await this.comicsRefreshMonitored();
                const q = document.getElementById('comics-search-input')?.value;
                if (q && q.length >= 2) this.comicsSearch(q);
            } else {
                this.showToast(t('Errore:') + ' ' + data.error, 'error');
            }
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
    },

    async comicsRefreshMonitored() {
        try {
            const res  = await fetch(`${API_BASE}/api/comics`);
            const data = await res.json();
            if (data.success) this._comics.monitored = data.comics;
        } catch(e) {}
    },

    async comicsLoadMonitored() {
        const el = document.getElementById('comics-monitored-list');
        el.innerHTML = '<p style="color:var(--text-muted);padding:1rem;"><i class="fa-solid fa-spinner fa-spin"></i> Caricamento...</p>';
        await this.comicsRefreshMonitored();
        const comics = this._comics.monitored;
        if (!comics.length) {
            el.innerHTML = `<div style="text-align:center;padding:4rem;color:var(--text-muted);"><i class="fa-solid fa-book fa-3x" style="margin-bottom:1rem;opacity:.4;display:block;"></i><p>${t('Nessun fumetto monitorato.')}<br>${t('Usa la tab')} <strong>${t('Esplora')}</strong> ${t('per aggiungerne.')}</p></div>`;
            return;
        }
        el.innerHTML = comics.map(c => {
            const enabled = c.enabled === 1;
            const lastChk = c.last_checked ? new Date(c.last_checked).toLocaleDateString('it-IT') : 'mai';
            return `<div class="card" style="margin-bottom:.75rem;">
              <div class="card-body" style="display:flex;gap:1rem;align-items:center;padding:1rem;">
                ${c.cover_url ? `<img src="${c.cover_url}" style="width:56px;height:84px;object-fit:cover;border-radius:4px;flex-shrink:0;" onerror="this.style.display='none'">` : `<div style="width:56px;height:84px;background:var(--bg-tertiary);border-radius:4px;flex-shrink:0;display:flex;align-items:center;justify-content:center;"><i class="fa-solid fa-book" style="color:var(--text-muted)"></i></div>`}
                <div style="flex:1;min-width:0;">
                  <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;">
                    <span style="font-weight:700;font-size:.95rem;">${c.title}</span>
                    ${c.publisher ? `<span class="badge badge-info">${c.publisher}</span>` : ''}
                    ${enabled ? `<span class="badge badge-success">${t('Attivo')}</span>` : `<span class="badge badge-secondary">${t('Disabilitato')}</span>`}
                  </div>
                  <div style="font-size:.8rem;color:var(--text-muted);margin-top:.3rem;">
                    Da: <strong>${c.from_date}</strong> &middot; ${t('Scaricati')}: <strong>${c.downloads_count || 0}</strong> &middot; Controllato: <strong>${lastChk}</strong>
                    ${c.save_path ? ` &middot; <code style="font-size:.75rem;">${c.save_path}</code>` : ''}
                  </div>
                </div>
                <div style="display:flex;gap:.4rem;flex-shrink:0;">
                  <button class="btn btn-small btn-secondary" title="Modifica" onclick="app.comicsEditModal(${c.id})"><i class="fa-solid fa-pen"></i></button>
                  <button class="btn btn-small ${enabled ? 'btn-warning' : 'btn-success'}" title="${enabled ? 'Disabilita' : 'Abilita'}" onclick="app.comicsToggle(${c.id}, ${enabled ? 0 : 1})"><i class="fa-solid ${enabled ? 'fa-pause' : 'fa-play'}"></i></button>
                  <button class="btn btn-small btn-danger" title="Rimuovi" onclick="app.comicsDelete(${c.id}, '${c.title.replace(/'/g,"\'")}')"><i class="fa-solid fa-trash"></i></button>
                </div>
              </div>
            </div>`;
        }).join('');
    },

    comicsEditModal(id) {
        const c = this._comics.monitored.find(x => x.id === id);
        if (!c) return;
        const old = document.getElementById('comics-edit-modal');
        if (old) old.remove();
        const modal = document.createElement('div');
        modal.id = 'comics-edit-modal';
        modal.className = 'modal active';
        modal.innerHTML = `
          <div class="modal-content" style="max-width:420px;">
            <div class="modal-header">
              <h3 style="margin:0; font-size:1.1rem; color:var(--primary-light);"><i class="fa-solid fa-pen"></i> Modifica Fumetto</h3>
              <button class="modal-close" onclick="document.getElementById('comics-edit-modal').remove()">&times;</button>
            </div>
            <div class="modal-body" style="display:flex;flex-direction:column;gap:1.2rem;">
              <div style="font-weight:600; color:var(--text-primary); text-align:center; padding-bottom:10px; border-bottom:1px solid rgba(255,255,255,0.05);">${c.title}</div>
              <div class="form-group" style="margin:0;">
                <label>${t('Scarica a partire dal')}</label>
                <input type="date" id="comics-edit-fromdate" class="form-control" value="${c.from_date}">
              </div>
              <div class="form-group" style="margin:0;">
                <label>Cartella destinazione</label>
                <div style="display:flex; gap:8px;"><input type="text" id="comics-edit-savepath" class="form-control" style="flex:1;" value="${c.save_path || ''}" placeholder="vuoto = default client"><button type="button" class="btn btn-secondary btn-small" title="Sfoglia cartelle del server" onclick="event.stopPropagation(); app.openDirBrowser('comics-edit-savepath')"><i class="fa-regular fa-folder-open"></i></button></div>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn btn-secondary" onclick="document.getElementById('comics-edit-modal').remove()">Annulla</button>
              <button class="btn btn-primary" onclick="app.comicsEditSave(${id})"><i class="fa-solid fa-floppy-disk"></i> Salva</button>
            </div>
          </div>`;
        document.body.appendChild(modal);
    },

    async comicsEditSave(id) {
        const fromDate = document.getElementById('comics-edit-fromdate')?.value || '';
        const savePath = document.getElementById('comics-edit-savepath')?.value || '';
        try {
            const res  = await fetch(`${API_BASE}/api/comics/${id}`, {
                method: 'PATCH', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ from_date: fromDate, save_path: savePath })
            });
            const data = await res.json();
            document.getElementById('comics-edit-modal')?.remove();
            if (data.success) { this.showToast(t('Comic updated'), 'success'); this.comicsLoadMonitored(); }
            else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
    },

    async comicsToggle(id, newEnabled) {
        try {
            const res  = await fetch(`${API_BASE}/api/comics/${id}`, {
                method: 'PATCH', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ enabled: newEnabled })
            });
            const data = await res.json();
            if (data.success) { this.showToast(newEnabled ? t('Re-enabled') : t('Disabled'), 'success'); this.comicsLoadMonitored(); }
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
    },

    async comicsDelete(id, title) {
        if (!confirm(`${t('Rimuovi')} "${title}"?`)) return;
        try {
            const res  = await fetch(`${API_BASE}/api/comics/${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) { this.showToast(t('Comic removed'), 'success'); this.comicsLoadMonitored(); }
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
    },

    async comicsRunCycle() {
        this.showToast(t('Comics cycle started in background...'), 'info');
        try { await fetch(`${API_BASE}/api/comics/cycle`, { method: 'POST' }); }
        catch(e) {}
    },

    async comicsLoadHistory() {
        const el = document.getElementById('comics-history-list');
        el.innerHTML = '<p style="color:var(--text-muted);padding:1rem;"><i class="fa-solid fa-spinner fa-spin"></i> Caricamento...</p>';
        try {
            const res  = await fetch(`${API_BASE}/api/comics/history`);
            const data = await res.json();
            if (!data.success) { el.innerHTML = `<p style="color:var(--danger)">${data.error}</p>`; return; }
            if (!data.history.length) {
                el.innerHTML = `<p style="text-align:center;padding:3rem;color:var(--text-muted);">${t('Nessun download ancora.')}</p>`;
                return;
            }
            
            // Tabella con CSS Grid aggiornata per fare spazio al pulsante (aggiunto "50px" in fondo)
            el.innerHTML = `
            <div class="data-table">
                <div class="table-row table-header" style="display:grid; grid-template-columns: 1fr 2fr 120px 100px; gap:15px; padding:0.75rem 1.5rem;">
                    <div>${t('Fumetto')}</div><div>${t('Titolo Download')}</div><div style="text-align:right;">${t('Data')}</div><div style="text-align:center;">${t('Azioni')}</div>
                </div>
                ${data.history.map(h => `
                <div class="table-row" style="display:grid; grid-template-columns: 1fr 2fr 120px 100px; gap:15px; padding:0.75rem 1.5rem; align-items:center;">
                    <div><span class="badge badge-secondary" style="font-weight:600; white-space:nowrap;">${this.escapeHtml(h.comic_title || '—')}</span></div>
                    <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                        <a href="${h.post_url}" target="_blank" style="color:var(--primary-light); text-decoration:none; font-weight:600;" title="${this.escapeHtml(h.title)}">${this.escapeHtml(h.title)}</a>
                    </div>
                    <div style="color:var(--text-muted); text-align:right; font-family:var(--font-mono); font-size:0.85rem;">
                        ${h.sent_at ? h.sent_at.slice(0,10) : '—'}
                    </div>
                    <div style="text-align:center; display:flex; gap:6px; justify-content:center;">
                        <button class="btn btn-small" style="background:rgba(249,115,22,.15);color:#f97316;border:1px solid rgba(249,115,22,.4);${h.magnet || h.torrent_url ? '' : 'opacity:.4;cursor:not-allowed;'}" title="${h.magnet || h.torrent_url ? 'Re-invia al client torrent' : 'Magnet non disponibile (vecchio record)'}" onclick="app.comicsResendHistory(${h.id}, '${this.escapeJs(h.title)}')" ${h.magnet || h.torrent_url ? '' : 'disabled'}>
                            <i class="fa-solid fa-rotate-right"></i>
                        </button>
                        <button class="btn btn-small btn-danger" title="Dimentica e permetti un nuovo scaricamento" onclick="app.comicsDeleteHistory(${h.id}, '${this.escapeJs(h.title)}')">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </div>
                </div>`).join('')}
            </div>`;
        } catch(e) { el.innerHTML = `<p style="color:var(--danger)">${t('Errore di rete')}</p>`; }
    },
    
    async comicsResendHistory(id, title) {
        try {
            const res  = await fetch(`${API_BASE}/api/comics/history/${id}/resend`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                this.showToast(`✅ ${t(data.message) || t('Inviato al client!')}`, 'success');
            } else {
                this.showToast(t('Errore:') + ' ' + (data.error || 'Invio fallito'), 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    async comicsDeleteHistory(id, title) {
        if (!confirm(`${t('Dimentica e permetti un nuovo scaricamento')} "${title}"?`)) return;
        try {
            const res = await fetch(`${API_BASE}/api/comics/history/${id}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                this.showToast(t('Removed from history!'), 'success');
                this.comicsLoadHistory(); // Ricarica la tabella istantaneamente
            } else {
                this.showToast(t('Errore:') + ' ' + data.error, 'error');
            }
        } catch (e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },
    
    _comicsWeeklyPage: 1,

    async comicsWeeklySaveSettings() {
        const enabledEl  = document.getElementById('comics-weekly-enabled');
        const fromDateEl = document.getElementById('comics-weekly-from-date');
        const enabled    = enabledEl?.checked ?? false;
        const fromDate   = fromDateEl?.value ?? '';
        try {
            const res  = await fetch(`${API_BASE}/api/comics/weekly/settings`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ weekly_enabled: enabled, weekly_from_date: fromDate })
            });
            const data = await res.json();
            if (data.success) this.showToast(t('Weekly pack settings saved!'), 'success');
            else              this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
    },

    async comicsLoadWeekly(page) {
        if (page !== undefined) this._comicsWeeklyPage = page;

        // ── Datepicker download manuale: default giovedì corrente ──
        const el = document.getElementById('comics-weekly-date');
        if (el && !el.value) {
            const today = new Date();
            const diff  = (today.getDay() >= 4) ? (today.getDay() - 4) : (today.getDay() + 3);
            const thu   = new Date(today);
            thu.setDate(today.getDate() - diff);
            el.value = thu.toISOString().split('T')[0];
        }

        // ── Carica impostazioni weekly ──────────────────────────────
        try {
            const sr  = await fetch(`${API_BASE}/api/comics/weekly/settings`);
            const cfg = sr.ok ? await sr.json() : { weekly_enabled: false, weekly_from_date: '' };
            const chk   = document.getElementById('comics-weekly-enabled');
            const fromEl= document.getElementById('comics-weekly-from-date');
            const lbl   = document.getElementById('comics-weekly-enabled-label');
            const track = chk?.parentElement?.querySelector('.wk-track');
            const knob  = chk?.parentElement?.querySelector('.wk-knob');
            if (chk) {
                chk.checked = !!cfg.weekly_enabled;
                if (track) track.style.background = cfg.weekly_enabled ? 'var(--primary)' : 'var(--border-color)';
                if (knob)  knob.style.transform   = cfg.weekly_enabled ? 'translateX(20px)' : 'translateX(2px)';
                if (lbl) { lbl.textContent = cfg.weekly_enabled ? t('Abilitato') : t('Disabilitato'); lbl.style.color = cfg.weekly_enabled ? 'var(--success)' : 'var(--text-muted)'; }
            }
            if (fromEl && !fromEl.value) fromEl.value = cfg.weekly_from_date || '';
        } catch(e) { /* ignora */ }

        // ── Carica storico con paginazione ──────────────────────────
        const listEl = document.getElementById('comics-weekly-list');
        listEl.innerHTML = `<p style="color:var(--text-muted); padding:1rem;"><i class="fa-solid fa-spinner fa-spin"></i> ${t('Caricamento storico...')}</p>`;
        try {
            const limit = 20;
            const res   = await fetch(`${API_BASE}/api/comics/weekly/list?page=${this._comicsWeeklyPage}&limit=${limit}`);
            const data  = await res.json();
            if (!data.success || !data.packs.length) {
                listEl.innerHTML = `<p style="color:var(--text-muted);text-align:center;padding:2rem;">${t('Nessun weekly pack nello storico.')}</p>`;
                return;
            }

            const { packs, page: curPage, pages: totalPages, total } = data;

            // Paginazione
            const paginationHtml = totalPages > 1 ? `
                <div style="display:flex; align-items:center; justify-content:center; gap:0.5rem; padding:0.75rem 1.5rem; border-top:1px solid var(--border);">
                    <button class="btn btn-small btn-secondary" ${curPage <= 1 ? 'disabled' : ''} onclick="app.comicsLoadWeekly(${curPage - 1})">
                        <i class="fa-solid fa-chevron-left"></i>
                    </button>
                    <span style="font-size:0.85rem; color:var(--text-secondary);">
                        ${t('Pagina')} <strong>${curPage}</strong> / ${totalPages}
                        <span style="color:var(--text-muted); font-size:0.8rem;">(${total} ${t('pack totali')})</span>
                    </span>
                    <button class="btn btn-small btn-secondary" ${curPage >= totalPages ? 'disabled' : ''} onclick="app.comicsLoadWeekly(${curPage + 1})">
                        <i class="fa-solid fa-chevron-right"></i>
                    </button>
                </div>` : '';

            listEl.innerHTML = `
                <div class="data-table">
                    <div class="table-row table-header" style="display:grid; grid-template-columns: 1fr 1fr 1fr 100px; gap:15px; padding:0.75rem 1.5rem;">
                        <div>${t('Data Pack')}</div><div>${t('Stato')}</div><div>${t('Trovato il')}</div><div style="text-align:right;">${t('Azioni')}</div>
                    </div>
                    ${packs.map(p => `
                    <div class="table-row" style="display:grid; grid-template-columns: 1fr 1fr 1fr 100px; gap:15px; padding:0.75rem 1.5rem; align-items:center;">
                        <div style="font-weight:600; font-family:var(--font-mono); color:var(--primary-light);">${p.pack_date}</div>
                        <div>${p.sent_at ? `<span class="badge badge-success"><i class="fa-solid fa-check"></i> ${t('Inviato')}</span>` : `<span class="badge badge-warning">${t('In attesa')}</span>`}</div>
                        <div style="color:var(--text-muted); font-size:0.85rem;">${p.found_at ? p.found_at.slice(0,10) : '—'}</div>
                        <div style="text-align:right;">
                            <button class="btn btn-small btn-primary" title="Forza un nuovo download di questo pack" onclick="app.comicsForceWeekly('${p.pack_date}')"><i class="fa-solid fa-download"></i></button>
                        </div>
                    </div>`).join('')}
                    ${paginationHtml}
                </div>
            `;
        } catch(e) { listEl.innerHTML = `<p style="color:var(--danger)">${t('Errore di rete')}</p>`; }
    },

    async comicsForceWeekly(dateStr) {
        this.showToast(`${t('Forced pack start')} ${dateStr}...`, 'info');
        try {
            const res = await fetch(`${API_BASE}/api/comics/weekly/send`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ date: dateStr })
            });
            const data = await res.json();
            if (data.success) {
                this.showToast(t(data.message), 'success');
                this.comicsLoadWeekly(); // Ricarica la tabella per mostrare "Inviato"
            } else {
                this.showToast(t('Errore:') + ' ' + data.error, 'error');
            }
        } catch(e) {
            this.showToast(t('Errore di rete'), 'error');
        }
    },

    async comicsSendWeekly() {
        const dateEl = document.getElementById('comics-weekly-date');
        const date   = dateEl?.value;
        if (!date) { this.showToast('Seleziona una data', 'warning'); return; }
        this.showToast(`${t('Searching weekly pack')} ${date}...`, 'info');
        try {
            const res  = await fetch(`${API_BASE}/api/comics/weekly/send`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ date })
            });
            const data = await res.json();
            if (data.success) { this.showToast(t(data.message), 'success'); this.comicsLoadWeekly(); }
            else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore di rete'), 'error'); }
   
 },
 
 
    async restartService() {
        if(!confirm(t('Vuoi davvero riavviare l\'EXTTO Engine?'))) return;
        
        this.showToast(t('Service restart in progress...'), 'info');
        
        try {
            // Chiama l'endpoint Python già esistente che gestisce systemctl
            await fetch(`${API_BASE}/api/restart-scrape`, { method: 'POST' });
            
            // Dato che il server si spegnerà, la fetch potrebbe andare in errore (è normale!)
            // Impostiamo un timer di 4 secondi per ricaricare forzatamente la pagina
            setTimeout(() => {
                window.location.reload();
            }, 4000);
            
        } catch(e) {
            // Se la connessione cade bruscamente (perché systemd ha killato il processo), è un buon segno!
            setTimeout(() => {
                window.location.reload();
            }, 4000);
        }
    },

    async showMovieDetails(movieName) {
        this.switchView('radarr');
        document.getElementById('radarr-title').textContent = movieName;
        document.getElementById('radarr-plot').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Analisi file e download metadati in corso...';
        document.getElementById('radarr-details').innerHTML = '';
        this.currentMovieName = movieName;
        this.currentMovieLang = null; // aggiornato dopo il fetch

        // Chiudi il pannello modifica se era aperto da un film precedente
        const editPanel = document.getElementById('radarr-edit-panel');
        if (editPanel) editPanel.style.display = 'none';
        const editBtn = document.getElementById('radarr-edit-btn');
        if (editBtn) { editBtn.classList.remove('btn-primary'); editBtn.classList.add('btn-secondary'); }
        
        try {
            const res = await fetch(`${API_BASE}/api/movies/details/${encodeURIComponent(movieName)}`);
            const data = await res.json();
            
            if (!data.success) {
                document.getElementById('radarr-plot').textContent = t('Errore:') + ' ' + data.error;
                return;
            }
            const meta = data.meta;
            
            const tmdbBtn = document.getElementById('radarr-tmdb-btn');
            if (tmdbBtn) {
                if (meta.tmdb_id) tmdbBtn.onclick = () => window.open(`https://www.themoviedb.org/movie/${meta.tmdb_id}?language=it-IT`, '_blank');
                else tmdbBtn.onclick = () => window.open(`https://www.themoviedb.org/search/movie?query=${encodeURIComponent(movieName)}`, '_blank');
            }
            
            let titleHtml = this.escapeHtml(meta.title || movieName);
            let badgeHtml = '';
            if (data.status === 'Scaricato') badgeHtml = `<span class="badge" style="background:rgba(16,185,129,.15);color:#34d399;border:1px solid rgba(52,211,153,.5);margin-left:12px;font-size:0.78rem;vertical-align:middle;text-transform:uppercase;padding:.25rem .6rem;"><i class="fa-solid fa-check"></i> Scaricato</span>`;
            else if (data.status === 'In Ricerca') badgeHtml = `<span class="badge" style="background:rgba(59,130,246,.15);color:#60a5fa;border:1px solid rgba(59,130,246,.5);margin-left:12px;font-size:0.78rem;vertical-align:middle;text-transform:uppercase;padding:.25rem .6rem;"><i class="fa-solid fa-radar"></i> In Ricerca</span>`;
            else badgeHtml = `<span class="badge badge-secondary" style="margin-left:12px;font-size:0.78rem;vertical-align:middle;text-transform:uppercase;padding:.25rem .6rem;"><i class="fa-solid fa-pause"></i> ${data.status}</span>`;
            
            document.getElementById('radarr-title').innerHTML = titleHtml + badgeHtml;
            document.getElementById('radarr-poster').innerHTML = meta.poster ? `<img src="https://image.tmdb.org/t/p/w342${meta.poster}" style="width:100%; height:100%; object-fit:cover; border-radius:8px;">` : `<i class="fa-solid fa-film fa-3x" style="color:var(--border);"></i>`;
            document.getElementById('radarr-backdrop').style.backgroundImage = meta.backdrop ? `url('https://image.tmdb.org/t/p/w1280${meta.backdrop}')` : `none`;
            document.getElementById('radarr-year').innerHTML = `<i class="fa-regular fa-calendar"></i> ${meta.year || 'N/A'}`;
            document.getElementById('radarr-plot').textContent = meta.overview;
            
            let html = `<div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 1rem; overflow: hidden;">
                <div style="padding: 1rem 1.5rem; background: rgba(0,0,0,0.2); border-bottom: 1px solid var(--border);">
                    <h3 style="margin:0; font-size:1.1rem;"><i class="fa-solid fa-hard-drive" style="color:var(--text-muted); margin-right:8px;"></i> File e Impostazioni</h3>
                </div><div style="padding: 1.5rem; display: flex; flex-direction: column; gap: 1rem;">`;
            
            if (data.db_info && data.db_info.magnet_link) {
                html += `<div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:1rem;">
                        <div>
                            <div style="font-weight:600; color:var(--text-primary); margin-bottom:0.25rem;">${t('File Scaricato')}</div>
                            <div style="font-family:monospace; color:var(--info); font-size:0.85rem; word-break:break-all;">${this.escapeHtml(data.db_info.title || data.db_info.name)}</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:0.85rem; color:var(--text-muted); margin-bottom:0.25rem;">Score: <strong style="color:var(--success);">${data.db_info.quality_score}</strong></div>
                            <div style="font-size:0.8rem; color:var(--text-muted);">${this.formatDate(data.db_info.downloaded_at)}</div>
                        </div>
                    </div>`;
            } else {
                html += `<div style="text-align:center; padding: 1rem; color:var(--text-muted); border-bottom:1px solid rgba(255,255,255,0.05);"><i class="fa-solid fa-file-video fa-2x" style="opacity:0.3; margin-bottom:10px;"></i><br>${t('Nessun download completato da rimuovere.')}</div>`;
            }
            
            if (data.cfg_info) {
                this.currentMovieLang = data.cfg_info.language || app._primaryLang || '';
                html += `<div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; margin-top:0.5rem;">
                        <div style="background:var(--bg-main); padding:1rem; border-radius:8px; border:1px solid var(--border); text-align:center;">
                            <div style="font-size:0.75rem; text-transform:uppercase; color:var(--text-muted); font-weight:800; letter-spacing:1px; margin-bottom:5px;">${t('Qualità')}</div>
                            <div style="font-weight:600; color:var(--primary-light);">${this.escapeHtml(data.cfg_info.quality)}</div>
                        </div>
                        <div style="background:var(--bg-main); padding:1rem; border-radius:8px; border:1px solid var(--border); text-align:center;">
                            <div style="font-size:0.75rem; text-transform:uppercase; color:var(--text-muted); font-weight:800; letter-spacing:1px; margin-bottom:5px;">${t('Lingua')}</div>
                            <div style="font-weight:600; color:var(--primary-light); text-transform:uppercase;">${this.escapeHtml((data.cfg_info.language || app._primaryLang || '').toUpperCase())}</div>
                        </div>
                        <div style="background:var(--bg-main); padding:1rem; border-radius:8px; border:1px solid var(--border); text-align:center;">
                            <div style="font-size:0.75rem; text-transform:uppercase; color:var(--text-muted); font-weight:800; letter-spacing:1px; margin-bottom:5px;">${t('Monitoraggio')}</div>
                            <div style="font-weight:600; color:${data.cfg_info.enabled ? 'var(--success)' : 'var(--warning)'};">${data.cfg_info.enabled ? t('Attivo') : t('Sospeso')}</div>
                        </div>
                    </div>`;
            }
            html += `</div></div>`;
            document.getElementById('radarr-details').innerHTML = html;

            // --- Feed film ---
            if (data.movie_name) {
                const feedContainer = document.getElementById('radarr-details');
                const feedSection = document.createElement('div');
                feedSection.id = 'movie-feed-section';
                feedSection.style.cssText = 'background:var(--bg-card);border:1px solid var(--border);border-radius:8px;margin-bottom:1rem;overflow:hidden;';
                feedSection.innerHTML = `
                    <div style="padding:.8rem 1.5rem;background:rgba(0,0,0,0.2);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;">
                        <h3 style="margin:0;font-size:1.1rem;"><i class="fa-solid fa-satellite-dish" style="color:#f97316;margin-right:8px;"></i> Migliori Trovati</h3>
                        ${data.has_feed ? '<span style="background:rgba(249,115,22,.15);color:#f97316;border:1px solid rgba(249,115,22,.4);padding:.1rem .5rem;border-radius:4px;font-size:.75rem;">In feed</span>' : ''}
                    </div>
                    <div id="movie-feed-body" style="padding:.5rem 0;"><div style="padding:1rem;color:var(--text-muted);font-size:.85rem;"><i class="fa-solid fa-spinner fa-spin"></i> Caricamento...</div></div>`;
                feedContainer.appendChild(feedSection);
                try {
                    const fRes = await fetch(`${API_BASE}/api/movies/feed-matches?name=${encodeURIComponent(data.movie_name || data.name)}`);
                    const fData = await fRes.json();
                    const fbody = document.getElementById('movie-feed-body');
                    if (!fbody) return;
                    if (!fData || fData.length === 0) {
                        fbody.innerHTML = `<div style="padding:1rem;color:var(--text-muted);font-size:.85rem;"><i class="fa-solid fa-circle-info"></i> ${t('Nessun risultato')}</div>`;
                        return;
                    }
                    const failLabels = {
                        'downloaded':    ['var(--success)', 'fa-circle-down', t('Scaricati')],
                        'lang_mismatch': ['var(--danger)',  'fa-language',    t('Lingua assente')],
                    };
                    const bonusLabel = (m) => {
                        if (m.lang_bonus >= 500) return `<span style="color:var(--success);font-size:.75rem;"><i class="fa-solid fa-circle-check"></i> +${m.lang_bonus}</span>`;
                        if (m.lang_bonus >= 200) return `<span style="color:var(--warning);font-size:.75rem;"><i class="fa-solid fa-closed-captioning"></i> +${m.lang_bonus}</span>`;
                        return `<span style="color:var(--text-muted);font-size:.75rem;">—</span>`;
                    };
                    let fHtml = fData.map(m => {
                        const [color, icon, label] = failLabels[m.fail_reason] || ['var(--info)', 'fa-clock', m.fail_reason || 'Trovato'];
                        const dateStr = m.found_at ? new Date(m.found_at).toLocaleString('it-IT', {day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
                        const dlBtn = m.magnet
                            ? `<button class="btn btn-primary btn-small" style="padding:3px 8px;font-size:.78rem;" onclick="app.addMagnetFromFeed('${this.escapeJs(m.magnet)}', '${this.escapeJs(m.title)}');event.stopPropagation();"><i class="fa-solid fa-download"></i> Scarica</button>`
                            : '';
                        return `<div style="display:grid;grid-template-columns:minmax(0,1fr) 110px 60px 100px 90px;gap:8px;align-items:center;padding:.55rem 1rem;border-bottom:1px solid rgba(255,255,255,.05);font-size:.82rem;">
                            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${this.escapeHtml(m.title)}">${this.escapeHtml(m.title)}</div>
                            <div style="text-align:right;"><span style="color:${color};"><i class="fa-solid ${icon}"></i> ${label}</span></div>
                            <div style="text-align:right;">${bonusLabel(m)}</div>
                            <div style="text-align:right;color:var(--text-muted);font-size:.78rem;">${dateStr}</div>
                            <div style="text-align:right;">${dlBtn}</div>
                        </div>`;
                    }).join('');
                    fbody.innerHTML = `
                        <div style="display:grid;grid-template-columns:minmax(0,1fr) 110px 60px 100px 90px;gap:8px;padding:.4rem 1rem;font-size:.75rem;font-weight:bold;color:var(--text-secondary);text-transform:uppercase;border-bottom:1px solid var(--border);">
                            <div>Titolo</div><div style="text-align:right;">Esito</div>
                            <div style="text-align:right;">Bonus</div>
                            <div style="text-align:right;">Data</div><div></div>
                        </div>${fHtml}`;
                } catch(_) {
                    const fb = document.getElementById('movie-feed-body');
                    if (fb) fb.innerHTML = `<div style="padding:1rem;color:var(--text-muted);font-size:.85rem;">${t('Errore di rete')}</div>`;
                }
            }

        } catch (err) { document.getElementById('radarr-plot').textContent = t('Errore:'); }
    },

    searchMissingMovie() {
        document.getElementById('manual-search-modal').classList.add('active');
        document.getElementById('manual-search-input').value = this.currentMovieName + ' ' + (this.currentMovieLang || app._primaryLang || '');
        this.performManualSearch();
    },

    // ========================================================================
    // DETTAGLI TORRENT AVANZATI
    // ========================================================================
    _tdPollId: null,
    _tdHash: null,

    showTorrentDetails(hash) {
        this._tdHash = hash;
        this._speedBuf = [];  // reset buffer grafico ad ogni apertura
        ['td-dl-limit','td-ul-limit','td-seed-ratio','td-seed-days'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.dataset.dirty = '';
        });
        document.getElementById('torrent-details-modal').classList.add('active');
        this.switchTdTab('general');
        this.loadTorrentDetails();
    },

    stopTorrentDetailsPoll() {
        this._tdHash = null;
        if (this._tdPollId) {
            clearTimeout(this._tdPollId);
            this._tdPollId = null;
        }
    },

    async loadTorrentDetails() {
        if (!this._tdHash) return;
        try {
            // Usiamo API_BASE per instradare la chiamata correttamente ed evitare timeout!
            const res = await fetch(`${API_BASE}/api/torrents/details`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({hash: this._tdHash})
            });
            const data = await res.json();
            
            if (data.success) {
                this._renderTorrentDetails(data);
            } else {
                document.getElementById('td-title').textContent = t("Errore di Lettura");
                document.getElementById('td-g-path').textContent = data.error || t("Impossibile recuperare le informazioni del file.");
            }
        } catch(e) {
            console.error('Errore lettura dettagli', e);
            document.getElementById('td-title').textContent = t("Errore di Rete");
            document.getElementById('td-g-path').textContent = e.message;
        } finally {
            if (this._tdHash) {
                // Ricarica automaticamente ogni 2 secondi mentre la finestra è aperta!
                this._tdPollId = setTimeout(() => this.loadTorrentDetails(), 2000);
            }
        }
    },

    switchTdTab(tab) {
        document.querySelectorAll('.td-tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`.td-tab-btn[data-tab="${tab}"]`).classList.add('active');
        hideEl(document.querySelectorAll('.td-panel'));
        showEl(`td-${tab}`);
        if (tab === 'peers') this.loadTorrentPeers();
    },

    async applyTorrentLimits() {
        const dl_kbps = parseInt(document.getElementById('td-dl-limit').value) || 0;
        const ul_kbps = parseInt(document.getElementById('td-ul-limit').value) || 0;
        const sr_val = document.getElementById('td-seed-ratio').value;
        const sd_val = document.getElementById('td-seed-days').value;
        const seed_ratio = sr_val !== '' ? parseFloat(sr_val) : -1;
        const seed_days = sd_val !== '' ? parseFloat(sd_val) : -1;
        const msg = document.getElementById('td-limit-msg');
        try {
            const res = await fetch(`${API_BASE}/api/torrents/set_limits`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({hash: this._tdHash, dl_kbps, ul_kbps, seed_ratio, seed_days})
            });
            const data = await res.json();
            msg.style.display = 'block';
            if (data.ok) {
                document.getElementById('td-dl-limit').dataset.dirty = '';
                document.getElementById('td-ul-limit').dataset.dirty = '';
                document.getElementById('td-seed-ratio').dataset.dirty = '';
                document.getElementById('td-seed-days').dataset.dirty = '';
                
                msg.style.color = 'var(--success)';
                msg.textContent = `✅ ${t('Regole salvate con successo')}`;
            } else {
                msg.style.color = 'var(--danger)';
                msg.textContent = `❌ ${t('Errore applicazione limiti')}`;
            }
            setTimeout(() => { msg.style.display = 'none'; }, 4000);
        } catch(e) {
            msg.style.display = 'block';
            msg.style.color = 'var(--danger)';
            msg.textContent = `❌ ${t('Errore:')} ${e.message}`;
        }
    },

    async loadTorrentPeers() {
        if (!this._tdHash) return;
        try {
            const res = await fetch(`${API_BASE}/api/torrents/peers`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({hash: this._tdHash})
            });
            const data = await res.json();
            if (data.success) this._renderPeers(data.peers);
        } catch(e) {}
    },

    _speedBuf: [],

    _drawSpeedChart(dlNow, ulNow) {
        const canvas = document.getElementById('td-speed-chart');
        if (!canvas) return;
        const buf = this._speedBuf || [];
        const W = canvas.offsetWidth || 600;
        const H = canvas.offsetHeight || 80;
        canvas.width = W;
        canvas.height = H;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, W, H);

        if (buf.length < 2) return;

        const maxVal = Math.max(...buf.map(p => Math.max(p.dl, p.ul)), 1);
        const N = buf.length;
        const stepX = W / (N - 1);

        const drawLine = (key, color) => {
            ctx.beginPath();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.lineJoin = 'round';
            buf.forEach((p, i) => {
                const x = i * stepX;
                const y = H - (p[key] / maxVal) * (H - 6) - 2;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            ctx.stroke();

            // Area fill semi-trasparente
            ctx.beginPath();
            ctx.fillStyle = color.replace(')', ', 0.08)').replace('var(--', 'rgba(').replace('success', '34,197,94').replace('warning', '251,191,36');
            // fallback semplice con rgba
            ctx.fillStyle = key === 'dl' ? 'rgba(34,197,94,0.08)' : 'rgba(251,191,36,0.08)';
            buf.forEach((p, i) => {
                const x = i * stepX;
                const y = H - (p[key] / maxVal) * (H - 6) - 2;
                i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
            });
            ctx.lineTo((N - 1) * stepX, H);
            ctx.lineTo(0, H);
            ctx.closePath();
            ctx.fill();
        };

        // Griglia orizzontale leggera
        ctx.strokeStyle = 'rgba(255,255,255,0.05)';
        ctx.lineWidth = 1;
        [0.25, 0.5, 0.75].forEach(f => {
            const y = H - f * (H - 6) - 2;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        });

        drawLine('ul', 'rgba(251,191,36,0.9)');
        drawLine('dl', 'rgba(34,197,94,0.9)');

        // Label velocità corrente
        const label = document.getElementById('td-speed-label');
        if (label) label.textContent = `⬇ ${this._fmtRate(dlNow)}  ⬆ ${this._fmtRate(ulNow)}`;
    },

    _peersSort: {col: 'dl_speed', dir: -1},  // default: DL decrescente

    _renderPeers(peers) {
        const el = document.getElementById('td-peers-list');
        if (!el) return;
        if (!peers || peers.length === 0) {
            el.innerHTML = `<div style="padding:40px 20px; text-align:center; color:var(--text-muted);"><i class="fa-solid fa-users fa-2x" style="opacity:0.3; margin-bottom:15px;"></i><br>${t('Nessun peer connesso.')}</div>`;
            return;
        }
        // Salva i peer per il re-sort
        this._peersData = peers;
        this._renderPeersSorted();
    },

    _sortPeers(col) {
        if (this._peersSort.col === col) {
            this._peersSort.dir *= -1;
        } else {
            this._peersSort.col = col;
            this._peersSort.dir = col === 'ip' || col === 'client' ? 1 : -1;
        }
        this._renderPeersSorted();
    },

    _renderPeersSorted() {
        const el = document.getElementById('td-peers-list');
        if (!el || !this._peersData) return;
        const peers = [...this._peersData];
        const {col, dir} = this._peersSort;
        peers.sort((a, b) => {
            let va = a[col], vb = b[col];
            if (typeof va === 'string') return dir * va.localeCompare(vb);
            return dir * (va - vb);
        });

        const arrow = (c) => c === col ? (dir > 0 ? ' ▲' : ' ▼') : '';
        const thStyle = 'cursor:pointer; user-select:none;';

        let html = `<div class="table-row table-header" style="display:grid; grid-template-columns: minmax(0,2fr) minmax(0,2fr) 90px 90px 70px 50px; padding:10px; width:100%;">
            <div style="${thStyle}" onclick="app._sortPeers('ip')">IP${arrow('ip')}</div>
            <div style="${thStyle}" onclick="app._sortPeers('client')">Client${arrow('client')}</div>
            <div style="${thStyle}; text-align:right;" onclick="app._sortPeers('dl_speed')">⬇ DL${arrow('dl_speed')}</div>
            <div style="${thStyle}; text-align:right;" onclick="app._sortPeers('ul_speed')">⬆ UL${arrow('ul_speed')}</div>
            <div style="${thStyle}; text-align:right;" onclick="app._sortPeers('progress')">%${arrow('progress')}</div>
            <div style="text-align:center;">Flag</div>
        </div>`;
        for (const p of peers) {
            html += `<div class="table-row" style="display:grid; grid-template-columns: minmax(0,2fr) minmax(0,2fr) 90px 90px 70px 50px; gap:8px; font-size:0.82rem; padding:8px 10px; align-items:center; border-bottom:1px solid rgba(255,255,255,0.05); width:100%;">
                <div style="font-family:var(--font-mono); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${this.escapeHtml(p.ip)}">${this.escapeHtml(p.ip)}</div>
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-secondary);" title="${this.escapeHtml(p.client)}">${this.escapeHtml(p.client)}</div>
                <div style="text-align:right; font-family:var(--font-mono); color:${p.dl_speed>0?'var(--success)':'var(--text-muted)'};">${this._fmtSpeed(p.dl_speed)}</div>
                <div style="text-align:right; font-family:var(--font-mono); color:${p.ul_speed>0?'var(--warning)':'var(--text-muted)'};">${this._fmtSpeed(p.ul_speed)}</div>
                <div style="text-align:right; font-family:var(--font-mono); color:var(--info);">${p.progress}%</div>
                <div style="text-align:center; font-family:var(--font-mono); font-size:0.75rem; color:var(--text-muted);">${this.escapeHtml(p.flags||'')}</div>
            </div>`;
        }
        html += `<div style="padding:8px 10px; font-size:0.75rem; color:var(--text-muted); text-align:right;">${peers.length} peer connessi</div>`;
        el.innerHTML = html;
    },

    _fmtSpeed(bps) {
        if (!bps || bps === 0) return '—';
        if (bps < 1024) return `${bps} B/s`;
        if (bps < 1048576) return `${(bps/1024).toFixed(1)} KB/s`;
        return `${(bps/1048576).toFixed(2)} MB/s`;
    },

    _renderTorrentDetails(d) {
        document.getElementById('td-title').textContent = d.name || d.hash;
        
        // --- 1. Aggiorna Tab Generale ---
        document.getElementById('td-g-path').textContent = d.save_path;
        document.getElementById('td-g-size').textContent = this._fmtBytes(d.total_size);
        document.getElementById('td-g-down').textContent = this._fmtBytes(d.downloaded);
        document.getElementById('td-g-up').textContent = this._fmtBytes(d.uploaded);
        document.getElementById('td-g-ratio').textContent = d.ratio;
        
        // Riusiamo la funzione badge esistente
        let isPaused = d.state === 'paused' || d.state === 'finished'; // Approssimazione visiva
        document.getElementById('td-g-state').innerHTML = this._torrentStateBadge(d.state, isPaused, null); 
        
        document.getElementById('td-g-pieces').textContent = `${d.pieces} frammenti da ${this._fmtBytes(d.piece_size)}`;
        document.getElementById('td-g-hash').textContent = d.hash;
        document.getElementById('td-g-conn').textContent = `${d.seeds} (${d.total_seeds}) Seeds / ${d.peers} (${d.total_peers}) Peers`;
        document.getElementById('td-g-time').textContent = `${this._fmtEta(d.active_time)} / ${this._fmtEta(d.seeding_time)}`;

        // Mostra limiti attuali nei campi input (byte/s → KB/s, -1 = nessun limite)
        const dlInput = document.getElementById('td-dl-limit');
        const ulInput = document.getElementById('td-ul-limit');
        const srInput = document.getElementById('td-seed-ratio');
        const sdInput = document.getElementById('td-seed-days');
        
        if (dlInput && !dlInput.matches(':focus') && !dlInput.dataset.dirty) dlInput.value = (d.dl_limit > 0) ? Math.round(d.dl_limit / 1024) : 0;
        if (ulInput && !ulInput.matches(':focus') && !ulInput.dataset.dirty) ulInput.value = (d.ul_limit > 0) ? Math.round(d.ul_limit / 1024) : 0;
        if (srInput && !srInput.matches(':focus') && !srInput.dataset.dirty) srInput.value = (d.seed_ratio >= 0) ? d.seed_ratio : '';
        if (sdInput && !sdInput.matches(':focus') && !sdInput.dataset.dirty) sdInput.value = (d.seed_days >= 0) ? d.seed_days : '';

        // --- Grafico velocità ---
        if (!this._speedBuf) this._speedBuf = [];
        this._speedBuf.push({dl: d.dl_rate || 0, ul: d.ul_rate || 0});
        if (this._speedBuf.length > 60) this._speedBuf.shift();  // max 60 campioni (~2 min)
        this._drawSpeedChart(d.dl_rate || 0, d.ul_rate || 0);

       // --- 2. Aggiorna Tab Tracker ---
        let trHtml = `<div class="table-row table-header" style="display:grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); padding: 10px; width: 100%;"><div>URL Server Tracker</div><div>Stato / Messaggio</div></div>`;
        d.trackers.forEach(tr => {
            let statusColor = tr.msg && tr.msg.toLowerCase().includes('fail') ? 'var(--danger)' : 'var(--success)';
            trHtml += `<div class="table-row" style="display:grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap:15px; font-size:0.85rem; padding:10px; border-bottom:1px solid rgba(255,255,255,0.05); width: 100%;">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text-primary);" title="${this.escapeHtml(tr.url)}">${this.escapeHtml(tr.url)}</div>
                <div style="color:${statusColor}; font-size:0.8rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${this.escapeHtml(tr.msg || 'In funzione')}">${this.escapeHtml(tr.msg || 'In funzione')}</div>
            </div>`;
        });
        if (d.trackers.length === 0) trHtml += `<div style="padding:40px 20px; text-align:center; color:var(--text-muted);"><i class="fa-solid fa-satellite-dish fa-2x" style="opacity:0.3; margin-bottom:15px;"></i><br>${t('Nessun tracker inserito.')}<br>${t('Il torrent usa la rete DHT decentralizzata.')}</div>`;
        document.getElementById('td-trackers-list').innerHTML = trHtml;

        // --- 3. Aggiorna Tab Files ---
        let fHtml = `<div class="table-row table-header" style="display:grid; grid-template-columns: minmax(0, 5fr) 100px 80px; padding: 10px; width: 100%;"><div>${t('Nome File nel Torrent')}</div><div style="text-align:right;">Dimensione</div><div style="text-align:right;">Stato</div></div>`;
        d.files.forEach(f => {
            const fileName = f.path.split(/[\/\\]/).pop();
            fHtml += `<div class="table-row" style="display:grid; grid-template-columns: minmax(0, 5fr) 100px 80px; gap:15px; font-size:0.85rem; padding:10px; align-items:center; border-bottom:1px solid rgba(255,255,255,0.05); width: 100%;">
                <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${this.escapeHtml(f.path)}"><i class="fa-solid fa-file" style="color:var(--text-muted); margin-right:8px;"></i>${this.escapeHtml(fileName)}</div>
                <div style="text-align:right; font-family:var(--font-mono); color:var(--text-secondary);">${this._fmtBytes(f.size)}</div>
                <div style="text-align:right; font-family:var(--font-mono); font-weight:bold; color:${f.progress >= 100 ? 'var(--success)' : 'var(--info)'};">${f.progress.toFixed(1)}%</div>
            </div>`;
        });
        
        if (d.files.length === 0) {
            // Selezioniamo il messaggio giusto in base allo stato del torrent
            if (d.state.includes('metadata') || d.total_size === 0) {
                fHtml += `<div style="padding:40px 20px; text-align:center; color:var(--text-muted);"><i class="fa-solid fa-satellite-dish fa-2x" style="opacity:0.3; margin-bottom:15px;"></i><br>${t('In Attesa')}<br><small>${t('Caricamento...')}</small></div>`;
            } else {
                fHtml += `<div style="padding:40px 20px; text-align:center; color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin fa-2x" style="opacity:0.3; margin-bottom:15px;"></i><br>${t('Caricamento...')}</div>`;
            }
        }
        document.getElementById('td-files-list').innerHTML = fHtml;
    },

    // =========================================================================
    // MOTORE TRADUZIONI UI
    // Carica il dizionario attivo dal DB e sostituisce tutti gli elementi
    // con attributo data-i18n="chiave" nel DOM.
    // =========================================================================

    async applyTranslations() {
        try {
            // Legge la lingua attiva
            const activeRes = await fetch('/api/i18n/active');
            const activeData = await activeRes.json();
            const lang = (activeData.lang || '').toLowerCase().trim();
            this._activeLang = lang;

            // Carica il dizionario della lingua attiva
            const dictRes = await fetch('/api/i18n/' + lang);
            const dictData = await dictRes.json();
            const dict = dictData.strings || {};

            // Salva sempre il dizionario per uso runtime
            app._i18nDict = dict;
            
            app._i18nDictNorm = {};
            for (const [k, v] of Object.entries(dict)) {
                app._i18nDictNorm[String(k).replace(/\s+/g, ' ').trim()] = v;
            }

            if (Object.keys(dict).length === 0) return;

            // --- MOTORE ROBUSTO ANTI-SPAZI VELOCE ---
            const getTrans = (key) => {
                if (!key) return null;
                if (dict[key]) return dict[key];
                let normKey = String(key).replace(/\s+/g, ' ').trim();
                return app._i18nDictNorm[normKey] || null;
            };

            // Applica le traduzioni a tutti gli elementi
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const trans = getTrans(el.getAttribute('data-i18n'));
                if (trans) el.textContent = trans;
            });

            // Fix pulsante Dona: se la lingua non è italiano usa "Donate"
            // (fallback in caso la traduzione non sia nel YAML)
            const donateLbl = document.querySelector('.btn-donate-label');
            if (donateLbl) {
                const donaTrans = getTrans('Dona');
                if (donaTrans && donaTrans !== 'Dona') {
                    donateLbl.textContent = donaTrans;
                } else if (lang && lang !== 'it' && lang !== 'ita') {
                    donateLbl.textContent = 'Donate';
                } else {
                    donateLbl.textContent = 'Dona';
                }
            }

            document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
                const trans = getTrans(el.getAttribute('data-i18n-placeholder'));
                if (trans) el.placeholder = trans;
            });

            document.querySelectorAll('[data-i18n-title]').forEach(el => {
                const trans = getTrans(el.getAttribute('data-i18n-title'));
                if (trans) el.title = trans;
            });

            // Applica ai Tooltip in modo infallibile
            document.querySelectorAll('[data-tip]').forEach(el => {
                let key = el.getAttribute('data-i18n-tip');
                if (!key) {
                    key = el.getAttribute('data-tip');
                    el.setAttribute('data-i18n-tip', key); 
                }
                const trans = getTrans(key);
                if (trans) el.setAttribute('data-tip', trans);
            });

        } catch(e) {
            console.warn('applyTranslations error:', e);
        }
    },
    
    async setUiLanguage(lang) {
        // Se le stringhe non sono ancora nel DB, importa automaticamente il YAML
        try {
            const check = await fetch('/api/i18n/' + lang);
            const checkData = await check.json();
            if (!checkData.count || checkData.count === 0) {
                await fetch('/api/i18n/import/' + lang, { method: 'POST' });
            }
        } catch(e) { /* ignora, procede comunque */ }

        fetch('/api/i18n/active', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lang })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                this.closeLangDropdown();
                location.reload();
            } else {
                this.showToast(t('Language change error: ') + data.error, 'error');
            }
        })
        .catch(e => this.showToast(t('Errore') + ': ' + e.message, 'error'));
    },

    // =========================================================================
    // LINGUE — inizializzazione select audio/sub e dropdown header UI
    // =========================================================================

    initLangSelects() {
        const primary = this._primaryLang || '';
        // Select audio (lingua obbligatoria serie/film)
        ['edit-series-language','edit-movie-language','extto-edit-language',
         'radarr-edit-language','series-language-preset','movie-language-preset',
         'bulk-series-language'].forEach(id => _fillLangSelect(id, { withCustom: true }));
        // Select sottotitoli (opzionali)
        ['edit-series-subtitle-preset','edit-movie-subtitle-preset',
         'extto-edit-subtitle-preset','radarr-edit-subtitle-preset',
         'series-subtitle-preset','movie-subtitle-preset']
            .forEach(id => _fillLangSelect(id, { withAny: true, withCombo: true, withCustom: true, primaryCode: primary }));
        // Costruisce il dropdown custom lingua UI nell'header
        this._buildLangDropdown();
    },

    _buildLangDropdown() {
        // Sostituisce il <select id="ui-lang-select"> con un div dropdown custom
        // per evitare il problema di contrasto delle <option> in Chrome dark mode
        const sel = document.getElementById('ui-lang-select');
        if (!sel || document.getElementById('lang-dropdown')) return;
        const wrap = document.createElement('div');
        wrap.id = 'lang-dropdown';
        wrap.style.cssText = 'position:relative; display:inline-block;';
        const btn = document.createElement('button');
        btn.id = 'lang-dropdown-btn';
        btn.title = 'Lingua interfaccia / UI language';
        btn.style.cssText = 'display:flex; align-items:center; gap:6px; background:var(--surface-raised); border:1px solid var(--border); color:var(--text-primary); border-radius:6px; padding:5px 10px; font-size:0.82rem; cursor:pointer; outline:none; white-space:nowrap;';
        btn.innerHTML = '<i class="fa-solid fa-globe" style="color:var(--primary);"></i><span id="lang-dropdown-label">…</span><i class="fa-solid fa-chevron-down" style="font-size:0.65rem; opacity:0.6; margin-left:2px;"></i>';
        btn.onclick = () => this.toggleLangDropdown();
        const menu = document.createElement('div');
        menu.id = 'lang-dropdown-menu';
        menu.style.cssText = 'display:none; position:absolute; top:calc(100% + 4px); left:0; min-width:140px; background:var(--surface-raised,#1e2535); border:1px solid var(--border); border-radius:6px; box-shadow:0 4px 16px rgba(0,0,0,0.5); z-index:9999; overflow:hidden;';
        wrap.appendChild(btn);
        wrap.appendChild(menu);
        // Inserisce nel contenitore fisso per evitare spostamento barra superiore
        const slot = document.getElementById('lang-dropdown-slot');
        if (slot) {
            slot.innerHTML = '';
            slot.appendChild(wrap);
        } else {
            sel.replaceWith(wrap);
        }

        // Carica le lingue disponibili:
        // - lingue già importate nel DB (tabella translations)
        // - lingue con YAML su disco in languages/ ma non ancora importate
        // - sempre 'ita' come master
        const _fallbackLangs = [{ code: 'ita', name: 'Italiano' }];
        const _langNames = Object.fromEntries(
            EXTTO_LANGUAGES.map(l => [l.code, l.label])
        );
        fetch('/api/i18n/languages')
            .then(r => r.json())
            .then(data => {
                const active = data.active || '';
                const inDb = new Set((data.languages || []).map(l => l.code));
                const langs = (data.languages || []).map(l => ({
                    code: l.code,
                    name: _langNames[l.code] || l.name
                }));
                (data.yaml_files || []).forEach(code => {
                    if (!inDb.has(code)) {
                        langs.push({ code, name: _langNames[code] || code.toUpperCase() });
                    }
                });
                if (!langs.find(l => l.code === 'ita')) langs.unshift({ code: 'ita', name: 'Italiano' });
                this._i18nFillLangDropdown(langs, active);
            })
            .catch(() => this._i18nFillLangDropdown(_fallbackLangs, 'ita'));
    },

    toggleLangDropdown() {
        const menu = document.getElementById('lang-dropdown-menu');
        if (!menu) return;
        if (menu.style.display === 'block') {
            this.closeLangDropdown();
        } else {
            menu.style.display = 'block';
            setTimeout(() => {
                document.addEventListener('click', (e) => {
                    if (!document.getElementById('lang-dropdown')?.contains(e.target)) {
                        this.closeLangDropdown();
                    }
                }, { once: true });
            }, 0);
        }
    },

    closeLangDropdown() {
        const menu = document.getElementById('lang-dropdown-menu');
        if (menu) menu.style.display = 'none';
    },

    _i18nFillLangDropdown(langs, activeCode) {
        const menu  = document.getElementById('lang-dropdown-menu');
        const label = document.getElementById('lang-dropdown-label');
        if (!menu) return;
        // Normalizza activeCode per confronto: se arriva 'it' dal DB vecchio,
        // lo equipara a 'ita' cercando la corrispondenza per prefisso.
        const _normCode = c => (c || '').toLowerCase().trim();
        const _codesMatch = (a, b) => {
            a = _normCode(a); b = _normCode(b);
            if (a === b) return true;
            // Confronto cross-formato: 'it'↔'ita', 'en'↔'eng', ecc.
            if (a.length === 2 && b.startsWith(a)) return true;
            if (b.length === 2 && a.startsWith(b)) return true;
            return false;
        };
        menu.innerHTML = '';
        langs.forEach(l => {
            const item = document.createElement('div');
            item.textContent  = l.name;
            item.dataset.code = l.code;
            item.style.cssText = 'padding:8px 14px; cursor:pointer; font-size:0.85rem; color:var(--text-primary); transition:background 0.15s;';
            const isActive = _codesMatch(l.code, activeCode);
            if (isActive) {
                item.style.background = 'rgba(59,130,246,0.25)';
                item.style.fontWeight  = '600';
                if (label) label.textContent = l.name;
            }
            item.addEventListener('mouseenter', () => { item.style.background = 'rgba(59,130,246,0.15)'; });
            item.addEventListener('mouseleave', () => { item.style.background = isActive ? 'rgba(59,130,246,0.25)' : ''; });
            item.addEventListener('click', () => this.setUiLanguage(l.code));
            menu.appendChild(item);
        });
    },

    // =========================================================================
    // I18N — Traduzioni interfaccia (DB-backed, editor in Manutenzione)
    // =========================================================================

    _i18n: {
        allKeys:     [],
        refStrings:  {},
        editStrings: {},
        dirty:       {},
        filterText:  '',
    },

    async i18nInit() {
        try {
            const r    = await fetch('/api/i18n/languages');
            const data = await r.json();
            const langs     = data.languages  || [];
            const yamlFiles = data.yaml_files || [];
            const active    = data.active     || 'it';
            this._i18nFillLangDropdown(langs, active);
            this._i18nFillSelect('i18n-active-lang', langs, active);
            this._i18nFillSelect('i18n-ref-lang',    langs, 'it');
            const editLang = langs.find(l => l.code !== 'it');
            this._i18nFillSelect('i18n-edit-lang', langs, editLang ? editLang.code : 'en');
            const yamlSel = document.getElementById('i18n-yaml-import-select');
            if (yamlSel) {
                yamlSel.innerHTML = '<option value="">— Importa da file YAML / Import YAML —</option>';
                yamlFiles.forEach(code => {
                    const opt = document.createElement('option');
                    opt.value = code;
                    opt.textContent = code.toUpperCase() + ' (' + code + '.yaml)';
                    yamlSel.appendChild(opt);
                });
            }
            await this.i18nLoadEditor();
        } catch(e) { console.error('i18nInit error', e); }
    },

    _i18nFillSelect(id, langs, selectedCode) {
        const sel = document.getElementById(id);
        if (!sel) return;
        sel.innerHTML = '';
        langs.forEach(l => {
            const opt = document.createElement('option');
            opt.value       = l.code;
            opt.textContent = l.name;
            if (l.code === selectedCode) opt.selected = true;
            sel.appendChild(opt);
        });
    },

    async i18nLoadEditor() {
        const refLang  = document.getElementById('i18n-ref-lang')?.value  || 'it';
        const editLang = document.getElementById('i18n-edit-lang')?.value || 'en';
        const refSel  = document.getElementById('i18n-ref-lang');
        const editSel = document.getElementById('i18n-edit-lang');
        const refName  = refSel?.options[refSel.selectedIndex]?.text  || refLang;
        const editName = editSel?.options[editSel.selectedIndex]?.text || editLang;
        const refH  = document.getElementById('i18n-ref-header');
        const editH = document.getElementById('i18n-edit-header');
        if (refH)  refH.textContent = '📖 ' + refName;
        if (editH) editH.textContent = '✏️ '  + editName;
        const body = document.getElementById('i18n-editor-body');
        if (body) body.innerHTML = '<tr><td colspan="3" style="padding:2rem;text-align:center;color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin"></i></td></tr>';
        try {
            const [rRef, rEdit] = await Promise.all([
                fetch('/api/i18n/' + refLang).then(r => r.json()),
                fetch('/api/i18n/' + editLang).then(r => r.json()),
            ]);
            this._i18n.refStrings  = rRef.strings  || {};
            this._i18n.editStrings = rEdit.strings || {};
            this._i18n.dirty       = {};
            this._i18n.allKeys     = Object.keys(this._i18n.refStrings).sort();
            this._i18nRenderTable();
            this._i18nUpdateStatus();
        } catch(e) {
            if (body) body.innerHTML = '<tr><td colspan="3" style="padding:1rem;color:var(--danger);">Errore / Error: ' + e.message + '</td></tr>';
        }
    },

    _i18nRenderTable() {
        const body = document.getElementById('i18n-editor-body');
        if (!body) return;
        const q    = (this._i18n.filterText || '').toLowerCase();
        const keys = this._i18n.allKeys.filter(k =>
            !q || k.toLowerCase().includes(q) ||
            (this._i18n.refStrings[k]  || '').toLowerCase().includes(q) ||
            (this._i18n.editStrings[k] || '').toLowerCase().includes(q)
        );
        if (keys.length === 0) {
            body.innerHTML = '<tr><td colspan="3" style="padding:1.5rem;text-align:center;color:var(--text-muted);">Nessuna stringa / No strings found</td></tr>';
            return;
        }
        body.innerHTML = '';
        keys.forEach(key => {
            const refVal  = this._i18n.refStrings[key] || '';
            const editVal = this._i18n.dirty[key] !== undefined ? this._i18n.dirty[key] : (this._i18n.editStrings[key] || '');
            const isDirty = this._i18n.dirty[key] !== undefined;
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border)';
            if (isDirty) tr.style.background = 'rgba(59,130,246,0.06)';
            const keyCell  = document.createElement('td');
            const refCell  = document.createElement('td');
            const editCell = document.createElement('td');
            keyCell.style.cssText  = 'padding:6px 12px;color:var(--text-secondary);font-family:monospace;font-size:0.8rem;vertical-align:middle;';
            refCell.style.cssText  = 'padding:6px 12px;color:var(--text-primary);vertical-align:middle;';
            editCell.style.cssText = 'padding:4px 8px;vertical-align:middle;';
            keyCell.textContent = key;
            refCell.textContent = refVal;
            const input = document.createElement('input');
            input.type        = 'text';
            input.className   = 'form-control';
            input.style.cssText = 'width:100%;font-size:0.85rem;' + (isDirty ? 'border-color:var(--primary);' : '');
            input.dataset.i18nKey = key;
            input.value       = editVal;
            input.placeholder = refVal;
            input.addEventListener('input', () => this.i18nOnInput(input, key));
            editCell.appendChild(input);
            tr.appendChild(keyCell); tr.appendChild(refCell); tr.appendChild(editCell);
            body.appendChild(tr);
        });
    },

    i18nOnInput(input, key) {
        this._i18n.dirty[key] = input.value;
        input.style.borderColor = 'var(--primary)';
        this._i18nUpdateStatus();
    },

    i18nFilter(text) {
        this._i18n.filterText = text;
        this._i18nRenderTable();
    },

    _i18nUpdateStatus() {
        const el = document.getElementById('i18n-status');
        if (!el) return;
        const total      = this._i18n.allKeys.length;
        const translated = Object.values(this._i18n.editStrings).filter(v => v && v.trim()).length;
        const edited     = Object.keys(this._i18n.dirty).length;
        el.textContent   = `${total} stringhe · ${translated} tradotte · ${edited} modifiche non salvate`;
        el.style.color   = edited > 0 ? 'var(--warning)' : 'var(--text-muted)';
    },

    async i18nSave() {
        const editLang = document.getElementById('i18n-edit-lang')?.value;
        if (!editLang) return;
        const dirty = this._i18n.dirty;
        if (Object.keys(dirty).length === 0) { this.showToast('Nessuna modifica / Nothing to save', 'info'); return; }
        try {
            const r    = await fetch('/api/i18n/' + editLang, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ strings: dirty }) });
            const data = await r.json();
            if (data.success) {
                Object.assign(this._i18n.editStrings, dirty);
                this._i18n.dirty = {};
                this._i18nRenderTable();
                this._i18nUpdateStatus();
                this.showToast(`${t('Salva')}: ${data.saved}`, 'success');
                
                // --- QUESTA RIGA AGGIORNA IL FILE .YAML SUL DISCO ---
                fetch('/api/i18n/export/' + editLang, { method: 'POST' }).catch(()=>{});
                
            } else { this.showToast(t('Errore:') + ' ' + data.error, 'error'); }
        } catch(e) { this.showToast(t('Errore:') + ' ' + e.message, 'error'); }
    },

    async i18nSetActive(lang) {
        try {
            const r    = await fetch('/api/i18n/active', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ lang }) });
            const data = await r.json();
            if (data.success) {
                const menu  = document.getElementById('lang-dropdown-menu');
                const label = document.getElementById('lang-dropdown-label');
                if (menu && label) {
                    menu.querySelectorAll('div[data-code]').forEach(el => {
                        const active = el.dataset.code === lang;
                        el.style.background = active ? 'rgba(59,130,246,0.25)' : '';
                        el.style.fontWeight  = active ? '600' : '';
                        if (active) label.textContent = el.textContent;
                    });
                }
                const sel2 = document.getElementById('i18n-active-lang');
                if (sel2) sel2.value = lang;
                this.showToast('Lingua attiva / Active: ' + lang.toUpperCase(), 'success');
            } else { this.showToast('Errore / Error: ' + data.error, 'error'); }
        } catch(e) { this.showToast('Errore / Error: ' + e.message, 'error'); }
    },

    async i18nExportYaml() {
        const editLang = document.getElementById('i18n-edit-lang')?.value;
        if (!editLang) return;
        try {
            const r    = await fetch('/api/i18n/export/' + editLang, { method: 'POST' });
            const data = await r.json();
            if (data.success) this.showToast(`languages/${editLang}.yaml (${data.exported})`, 'success');
            else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore:') + ' ' + e.message, 'error'); }
    },

    async i18nImportYaml() {
        const sel  = document.getElementById('i18n-yaml-import-select');
        const lang = sel?.value;
        if (!lang) { this.showToast(t('Inserisci prima un pezzo di titolo da cercare!'), 'warning'); return; }
        try {
            const r    = await fetch('/api/i18n/import/' + lang, { method: 'POST' });
            const data = await r.json();
            if (data.success) { this.showToast(`${t('Lingua aggiunta / Language added')}: ${lang}.yaml (${data.imported})`, 'success'); await this.i18nInit(); }
            else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore:') + ' ' + e.message, 'error'); }
    },

    async i18nDeleteLang() {
        const editLang = document.getElementById('i18n-edit-lang')?.value;
        if (!editLang || editLang === 'it') { this.showToast(t('Non puoi eliminare la lingua master (it)'), 'warning'); return; }
        if (!confirm(`${t('Elimina')} "${editLang}"?`)) return;
        try {
            const r    = await fetch('/api/i18n/' + editLang, { method: 'DELETE' });
            const data = await r.json();
            if (data.success) { this.showToast(`${t('Elimina')}: ${data.deleted}`, 'success'); await this.i18nInit(); }
            else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast(t('Errore:') + ' ' + e.message, 'error'); }
    },

    async i18nAddLanguage() {
        const code = prompt(t('Lingua interfaccia / UI language') + ' (es: es, fr, de):');
        if (!code?.trim()) return;
        const clean = code.trim().toLowerCase();
        if (!/^[a-z]{2,5}$/.test(clean)) { this.showToast(t('Codice non valido — usa 2-5 lettere'), 'error'); return; }
        const emptyStrings = {};
        this._i18n.allKeys.forEach(k => { emptyStrings[k] = ''; });
        try {
            const r    = await fetch('/api/i18n/' + clean, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ strings: emptyStrings }) });
            const data = await r.json();
            if (data.success) {
                this.showToast('Lingua aggiunta / Language added: ' + clean, 'success');
                await this.i18nInit();
                const editSel = document.getElementById('i18n-edit-lang');
                if (editSel) { editSel.value = clean; await this.i18nLoadEditor(); }
            } else this.showToast(t('Errore:') + ' ' + data.error, 'error');
        } catch(e) { this.showToast('Errore: ' + e.message, 'error'); }
    },

    // ── TRAKT ──────────────────────────────────────────────────────────────

    async traktInit() {
        try {
            const res  = await fetch('/api/trakt/status');
            const data = await res.json();
            if (data.error) return;

            const badge = document.getElementById('trakt-status-badge');
            if (badge) {
                badge.textContent = data.authenticated ? `✅ ${t('Connesso')}` : `⚠️ ${t('Non connesso')}`;
                badge.className   = `badge ${data.authenticated ? 'badge-success' : 'badge-warning'}`;
            }

            const loginBox = document.getElementById('trakt-login-box');
            const connBox  = document.getElementById('trakt-connected-box');
            if (loginBox) loginBox.style.display = data.authenticated ? 'none' : '';
            if (connBox)  connBox.style.display  = data.authenticated ? ''     : 'none';

            const expiresEl = document.getElementById('trakt-expires-info');
            if (expiresEl && data.expires_in_days !== null)
                expiresEl.textContent = t('Token valido per') + ' ' + data.expires_in_days + ' ' + t('giorni');

            const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = String(val); };
            setVal('trakt-import-quality',   data.import_quality);
            setVal('trakt-import-language',  data.import_language);
            setVal('trakt-calendar-days',    data.calendar_days);
            this._setToggle('trakt-scrobble-enabled', data.scrobble_enabled ? 'true' : 'false');
        } catch(e) { console.error('traktInit', e); }
    },

    async traktSaveCredentials() {
        const client_id     = (document.getElementById('trakt-client-id')?.value     || '').trim();
        const client_secret = (document.getElementById('trakt-client-secret')?.value || '').trim();
        if (!client_id) { this.showToast(t('Inserisci il Client ID'), 'error'); return; }
        try {
            const r = await fetch('/api/trakt/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({client_id, client_secret}),
            });
            const d = await r.json();
            if (d.ok) this.showToast(t('Credenziali Trakt salvate'), 'success');
            else this.showToast(t('Errore salvataggio') + ': ' + (d.error || ''), 'error');
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },

    async traktSaveOptions() {
        const quality  = document.getElementById('trakt-import-quality')?.value  || '720p+';
        const language = document.getElementById('trakt-import-language')?.value || 'ita';
        const days     = parseInt(document.getElementById('trakt-calendar-days')?.value) || 7;
        const scrobble = this._getToggle('trakt-scrobble-enabled') === 'true';
        try {
            const r = await fetch('/api/trakt/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({import_quality: quality, import_language: language,
                                      calendar_days: days, scrobble_enabled: scrobble}),
            });
            const d = await r.json();
            if (d.ok) this.showToast(t('Opzioni Trakt salvate'), 'success');
            else this.showToast(t('Errore') + ': ' + (d.error || ''), 'error');
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },

    async traktStartAuth() {
        await this.traktSaveCredentials();
        try {
            const res  = await fetch('/api/trakt/auth/start', {method: 'POST'});
            const data = await res.json();
            if (data.error) { this.showToast(data.error, 'error'); return; }

            const box    = document.getElementById('trakt-device-box');
            const codeEl = document.getElementById('trakt-user-code');
            const urlEl  = document.getElementById('trakt-verify-url');
            const msgEl  = document.getElementById('trakt-poll-msg');
            if (box)    box.style.display  = '';
            if (codeEl) codeEl.textContent = data.user_code;
            if (urlEl)  { urlEl.href = data.verification_url; urlEl.textContent = data.verification_url; }
            if (msgEl)  msgEl.textContent  = t('In attesa di autorizzazione...');

            const interval  = (data.interval || 5) * 1000;
            const expiresAt = Date.now() + (data.expires_in || 600) * 1000;

            const timer = setInterval(async () => {
                if (Date.now() >= expiresAt) {
                    clearInterval(timer);
                    if (msgEl) msgEl.textContent = '⏰ ' + t('Codice scaduto. Riprova.');
                    return;
                }
                try {
                    const pr    = await fetch('/api/trakt/auth/poll', {method: 'POST'});
                    const pdata = await pr.json();
                    if (msgEl) msgEl.textContent = pdata.message || '';
                    if (pdata.status === 'authorized') {
                        clearInterval(timer);
                        if (box) box.style.display = 'none';
                        this.showToast('\u2705 ' + t('Trakt collegato!'), 'success');
                        await this.traktInit();
                    } else if (pdata.status === 'expired' || pdata.status === 'denied') {
                        clearInterval(timer);
                    }
                } catch(e) { console.error('trakt poll', e); }
            }, interval);
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },

    async traktRevoke() {
        if (!confirm(t('Disconnettere EXTTO da Trakt?'))) return;
        try {
            await fetch('/api/trakt/auth/revoke', {method: 'POST'});
            this.showToast(t('Disconnesso da Trakt'), 'success');
            await this.traktInit();
        } catch(e) { this.showToast(t('Errore') + ': ' + e.message, 'error'); }
    },

    async traktLoadWatchlist() {
        const container = document.getElementById('trakt-watchlist-list');
        if (!container) return;
        container.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ' + t('Caricamento...');
        try {
            const res = await fetch('/api/trakt/watchlist');
            if (res.status === 401) {
                container.innerHTML = `<span style="color:var(--warning);">${t('Non autenticato su Trakt.')}</span>`;
                return;
            }
            const shows = await res.json();
            if (shows.error) { container.innerHTML = `<span style="color:var(--danger);">${this._esc(shows.error)}</span>`; return; }
            if (!shows.length) { container.innerHTML = `<span style="color:var(--text-muted);">${t('Watchlist vuota.')}</span>`; return; }
            container.innerHTML = shows.map(s => `
                <label style="display:flex;align-items:center;gap:.75rem;padding:.45rem 0;
                              border-bottom:1px solid var(--border);cursor:pointer;">
                    <input type="checkbox" class="trakt-wl-check" value="${this._esc(s.title)}"
                        ${s.in_extto ? 'disabled' : ''}
                        style="width:16px;height:16px;flex-shrink:0;accent-color:var(--primary);">
                    <span style="flex:1;${s.in_extto ? 'opacity:.5;' : ''}">
                        ${this._esc(s.title)}
                        ${s.year ? `<span style="color:var(--text-muted);font-size:.8rem;">(${s.year})</span>` : ''}
                    </span>
                    ${s.in_extto ? `<span style="color:var(--success);font-size:.78rem;white-space:nowrap;">\u2705 ${t('In EXTTO')}</span>` : ''}
                </label>`).join('');
        } catch(e) {
            container.innerHTML = `<span style="color:var(--danger);">${t('Errore')}: ${e.message}</span>`;
        }
    },

    async traktImportSelected() {
        const checks = document.querySelectorAll('.trakt-wl-check:checked');
        const titles = Array.from(checks).map(c => c.value);
        if (!titles.length) { this.showToast(t('Seleziona almeno una serie'), 'error'); return; }
        await this._traktDoImport({titles});
    },

    async traktImportAll() {
        await this._traktDoImport({skip_existing: true});
    },

    async _traktDoImport(body) {
        const resultEl = document.getElementById('trakt-import-result');
        if (resultEl) resultEl.textContent = t('Importazione in corso...');
        try {
            const res  = await fetch('/api/trakt/watchlist/import', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (data.error) { this.showToast(data.error, 'error'); return; }
            const msg = `${data.imported} ${t('importate')}, ${data.skipped} ${t('già presenti')}`;
            this.showToast(msg, 'success');
            if (resultEl) resultEl.textContent = msg;
            if (this._currentView === 'series') this.loadSeries();
            await this.traktLoadWatchlist();
        } catch(e) { this.showToast(t('Errore import') + ': ' + e.message, 'error'); }
    },

    async traktLoadCalendar() {
        const container = document.getElementById('trakt-calendar-list');
        if (!container) return;
        container.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> ' + t('Caricamento...');
        try {
            const days = document.getElementById('trakt-calendar-days')?.value || 7;
            const res  = await fetch(`/api/trakt/calendar?days=${days}`);
            if (res.status === 401) {
                container.innerHTML = `<span style="color:var(--warning);">${t('Non autenticato su Trakt.')}</span>`;
                return;
            }
            const data = await res.json();
            if (data.error) { container.innerHTML = `<span style="color:var(--danger);">${this._esc(data.error)}</span>`; return; }
            const episodes = data.episodes || [];
            if (!episodes.length) {
                container.innerHTML = `<span style="color:var(--text-muted);">${t('Nessun episodio in uscita nel periodo selezionato.')}</span>`;
                return;
            }
            const byDate = {};
            for (const ep of episodes) {
                const d = (ep.first_aired || '').slice(0, 10) || t('Data sconosciuta');
                if (!byDate[d]) byDate[d] = [];
                byDate[d].push(ep);
            }
            container.innerHTML = Object.entries(byDate).map(([date, eps]) => `
                <div style="margin-bottom:1rem;">
                    <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;color:var(--text-muted);
                                letter-spacing:.05em;margin-bottom:.4rem;padding-bottom:.25rem;
                                border-bottom:1px solid var(--border);">
                        \u{1F4C5} ${date}
                    </div>
                    ${eps.map(ep => `
                        <div style="display:flex;align-items:center;gap:.75rem;padding:.35rem 0;
                                    border-bottom:1px solid rgba(255,255,255,.04);">
                            <span style="flex:1;font-size:.9rem;">
                                <strong>${this._esc(ep.series_title)}</strong>
                                <span style="color:var(--text-muted);margin:0 .3rem;">
                                    S${String(ep.season).padStart(2,'0')}E${String(ep.episode).padStart(2,'0')}
                                </span>
                                ${ep.episode_title
                                    ? `<span style="color:var(--text-secondary);font-size:.82rem;">— ${this._esc(ep.episode_title)}</span>`
                                    : ''}
                            </span>
                            ${ep.in_extto
                                ? `<span style="color:var(--success);font-size:.78rem;white-space:nowrap;">\u2705 ${t('Monitorato')}</span>`
                                : `<span style="color:var(--text-muted);font-size:.78rem;white-space:nowrap;">— ${t('Non monitorato')}</span>`}
                        </div>`).join('')}
                </div>`).join('');
        } catch(e) {
            container.innerHTML = `<span style="color:var(--danger);">${t('Errore')}: ${e.message}</span>`;
        }
    },


    // ========================================================================
    // JELLYFIN / MEDIA SERVER
    // ========================================================================

    async jellyfinInit() {
        try {
            const res  = await fetch('/api/jellyfin/config');
            const data = await res.json();
            const urlEl = document.getElementById('jellyfin-url');
            const keyEl = document.getElementById('jellyfin-api-key');
            if (urlEl) urlEl.value = data.jellyfin_url     || '';
            if (keyEl) keyEl.value = data.jellyfin_api_key || '';
            const badge = document.getElementById('jellyfin-status-badge');
            if (badge) {
                if (data.jellyfin_url && data.jellyfin_api_key) {
                    badge.textContent = 'Configurato';
                    badge.className   = 'badge badge-success';
                } else {
                    badge.textContent = 'Non configurato';
                    badge.className   = 'badge badge-secondary';
                }
            }
        } catch(e) { console.error('jellyfinInit', e); }
    },

    async jellyfinSave() {
        const url    = (document.getElementById('jellyfin-url')?.value     || '').trim();
        const apiKey = (document.getElementById('jellyfin-api-key')?.value || '').trim();
        try {
            const res  = await fetch('/api/jellyfin/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ jellyfin_url: url, jellyfin_api_key: apiKey }),
            });
            const data = await res.json();
            if (data.success) {
                this.showToast('✅ Impostazioni Jellyfin salvate', 'success');
                await this.jellyfinInit();
            } else {
                this.showToast('❌ ' + (data.error || 'Errore salvataggio'), 'error');
            }
        } catch(e) { this.showToast('❌ Errore connessione', 'error'); }
    },

    async jellyfinTestRefresh() {
        const resultEl = document.getElementById('jellyfin-test-result');
        if (resultEl) resultEl.textContent = '⏳ Test in corso...';
        try {
            const res  = await fetch('/api/jellyfin/test-refresh', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                if (resultEl) { resultEl.textContent = '✅ Refresh OK'; resultEl.style.color = 'var(--success)'; }
                this.showToast('✅ Jellyfin Library Refresh inviato con successo', 'success');
            } else {
                if (resultEl) { resultEl.textContent = '❌ ' + (data.error || 'Errore'); resultEl.style.color = 'var(--danger)'; }
                this.showToast('❌ ' + (data.error || 'Errore'), 'error');
            }
        } catch(e) {
            if (resultEl) { resultEl.textContent = '❌ Errore connessione'; resultEl.style.color = 'var(--danger)'; }
            this.showToast('❌ Errore connessione', 'error');
        }
    },

    async plexInit() {
        try {
            const res  = await fetch('/api/plex/config');
            const data = await res.json();
            const urlEl   = document.getElementById('plex-url');
            const tokenEl = document.getElementById('plex-token');
            if (urlEl)   urlEl.value   = data.plex_url   || '';
            if (tokenEl) tokenEl.value = data.plex_token || '';
            const badge = document.getElementById('plex-status-badge');
            if (badge) {
                if (data.plex_url && data.plex_token) {
                    badge.textContent = 'Configurato';
                    badge.className   = 'badge badge-success';
                } else {
                    badge.textContent = 'Non configurato';
                    badge.className   = 'badge badge-secondary';
                }
            }
        } catch(e) { console.error('plexInit', e); }
    },

    async plexSave() {
        const url   = (document.getElementById('plex-url')?.value   || '').trim();
        const token = (document.getElementById('plex-token')?.value || '').trim();
        try {
            const res  = await fetch('/api/plex/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ plex_url: url, plex_token: token }),
            });
            const data = await res.json();
            if (data.success) {
                this.showToast('✅ Impostazioni Plex salvate', 'success');
                await this.plexInit();
            } else {
                this.showToast('❌ ' + (data.error || 'Errore salvataggio'), 'error');
            }
        } catch(e) { this.showToast('❌ Errore connessione', 'error'); }
    },

    async plexTestRefresh() {
        const resultEl = document.getElementById('plex-test-result');
        if (resultEl) resultEl.textContent = '⏳ Test in corso...';
        try {
            const res  = await fetch('/api/plex/test-refresh', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                if (resultEl) { resultEl.textContent = '✅ Refresh OK'; resultEl.style.color = 'var(--success)'; }
                this.showToast('✅ Plex Library Refresh inviato con successo', 'success');
            } else {
                if (resultEl) { resultEl.textContent = '❌ ' + (data.error || 'Errore'); resultEl.style.color = 'var(--danger)'; }
                this.showToast('❌ ' + (data.error || 'Errore'), 'error');
            }
        } catch(e) {
            if (resultEl) { resultEl.textContent = '❌ Errore connessione'; resultEl.style.color = 'var(--danger)'; }
            this.showToast('❌ Errore connessione', 'error');
        }
    },


    // ========================================================================
    // INDEXER (Jackett / Prowlarr / FlareSolverr) — tab Integrazioni
    // ========================================================================

    async indexerInit() {
        try {
            const res  = await fetch(`${API_BASE}/api/config`);
            const data = await res.json();
            const s    = data.settings || {};
            const set  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
            const setCb = (id, val) => { const el = document.getElementById(id); if (el) el.checked = (val === undefined || val === 'yes' || val === true); };
            set('indexer-jackett-url',    s.jackett_url);
            set('indexer-jackett-api',    s.jackett_api);
            setCb('indexer-jackett-archive', s.jackett_save_to_archive);
            set('indexer-prowlarr-url',   s.prowlarr_url);
            set('indexer-prowlarr-api',   s.prowlarr_api);
            setCb('indexer-prowlarr-archive', s.prowlarr_save_to_archive);
            set('indexer-flare-url',      s.flaresolverr_url);
            const jb = document.getElementById('indexer-status-badge');
            if (jb) {
                const ok = !!(s.jackett_url || s.prowlarr_url);
                jb.textContent = ok ? 'Configurato' : 'Non configurato';
                jb.className   = ok ? 'badge badge-success' : 'badge badge-secondary';
            }
            const fb = document.getElementById('flare-status-badge');
            if (fb) {
                fb.textContent = s.flaresolverr_url ? 'Configurato' : 'Non configurato';
                fb.className   = s.flaresolverr_url ? 'badge badge-success' : 'badge badge-secondary';
            }
            // Motori di ricerca web
            const engines = (s.websearch_engines || '').split(',').map(e => e.trim().toLowerCase()).filter(Boolean);
            const setCkw = (id, key) => { const el = document.getElementById(id); if (el) el.checked = engines.includes(key); };
            setCkw('websearch-bitsearch',    'bitsearch');
            setCkw('websearch-tpb',          'tpb');
            setCkw('websearch-knaben',       'knaben');
            setCkw('websearch-btdig',        'btdig');
            setCkw('websearch-limetorrents', 'limetorrents');
            setCkw('websearch-torrentz2',    'torrentz2');
            setCkw('websearch-torrentscsv',  'torrentscsv');
        } catch(e) { console.error('indexerInit', e); }
    },

    async indexerSave() {
        const v  = id => (document.getElementById(id)?.value || '').trim();
        const cb = id => document.getElementById(id)?.checked;
        try {
            const cfgRes = await fetch(`${API_BASE}/api/config`);
            const cfg    = await cfgRes.json();
            const s      = cfg.settings || {};
            s.jackett_url             = v('indexer-jackett-url');
            s.jackett_api             = v('indexer-jackett-api');
            s.jackett_save_to_archive = cb('indexer-jackett-archive') ? 'yes' : 'no';
            s.prowlarr_url            = v('indexer-prowlarr-url');
            s.prowlarr_api            = v('indexer-prowlarr-api');
            s.prowlarr_save_to_archive = cb('indexer-prowlarr-archive') ? 'yes' : 'no';
            s.flaresolverr_url        = v('indexer-flare-url');
            // Motori di ricerca web
            const engines = [];
            if (cb('websearch-bitsearch'))    engines.push('bitsearch');
            if (cb('websearch-tpb'))          engines.push('tpb');
            if (cb('websearch-knaben'))       engines.push('knaben');
            if (cb('websearch-btdig'))        engines.push('btdig');
            if (cb('websearch-limetorrents')) engines.push('limetorrents');
            if (cb('websearch-torrentz2'))    engines.push('torrentz2');
            if (cb('websearch-torrentscsv'))  engines.push('torrentscsv');
            s.websearch_engines = engines.join(',');
            const res  = await fetch(`${API_BASE}/api/config/settings`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ settings: s }),
            });
            const data = await res.json();
            if (data.success) {
                this.showToast('✅ Impostazioni Indexer salvate', 'success');
                await this.indexerInit();
            } else {
                this.showToast('❌ ' + (data.error || 'Errore salvataggio'), 'error');
            }
        } catch(e) { this.showToast('❌ Errore connessione', 'error'); }
    },

    async indexerTestFlare() {
        const resultEl = document.getElementById('flare-test-result');
        if (resultEl) { resultEl.textContent = '⏳ Test in corso...'; resultEl.style.color = 'var(--text-muted)'; }
        try {
            const res  = await fetch(`${API_BASE}/api/flaresolverr/test`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                const msg = `✅ OK — versione ${data.version}, pagina HTTP ${data.page_status}`;
                if (resultEl) { resultEl.textContent = msg; resultEl.style.color = 'var(--success)'; }
                this.showToast('✅ FlareSolverr raggiungibile e funzionante', 'success');
            } else {
                const msg = '❌ ' + (data.error || 'Errore');
                if (resultEl) { resultEl.textContent = msg; resultEl.style.color = 'var(--danger)'; }
                this.showToast(msg, 'error');
            }
        } catch(e) {
            const msg = '❌ Errore connessione';
            if (resultEl) { resultEl.textContent = msg; resultEl.style.color = 'var(--danger)'; }
            this.showToast(msg, 'error');
        }
    },

    // ========================================================================
    // AMULE / ED2K  ★ v45  —  ispirato ad amutorrent
    // ========================================================================

    _amuleCurrentTab: 'downloads',
    _amulePollId: null,
    _amuleIsPolling: false,
    _amuleSearchResults: [],
    _amuleSearchActive: false,      // true mentre la ricerca è in corso (non perdere al cambio tab)
    _amuleConnectedServer: null,
    _amuleCompletedCache: new Map(),
    _amuleSharedCache: null,        // cache condivisi {files, dirs, ts}
    _amuleSharedDirsDirty: false,   // true se cartelle cambiate → ricarica al prossimo switch
    _amuleSharedSortKey: 'file_name',
    _amuleSharedSortDir: 1,         // 1=asc, -1=desc
    
    // Pulisce i download completati dalla vista senza re-fetch
    amuleCleanCompleted() {
        this._amuleCompletedCache.clear();
        // Usa la lista live se disponibile, altrimenti fetch
        if (this._amuleLastDownloads !== undefined) {
            this._renderAmuleDownloads(this._amuleLastDownloads);
        } else {
            this.amuleLoadDownloads();
        }
        this.showToast('Download completati nascosti dalla vista', 'success');
    },

    amuleOnEnter() {
        this.amuleSwitchTab(this._amuleCurrentTab || 'downloads');
        this.amuleRefreshStatus();
    },

   amuleStartPoll() {
        if (this._amuleIsPolling) return;
        this._amuleIsPolling = true;
        const poll = async () => {
            if (!this._amuleIsPolling) return;
            try {
                // FIX: Usa API_BASE per evitare problemi di routing interno
                const r = await fetch(`${API_BASE}/api/amule/all`);
                if (r.ok) {
                    const all = await r.json();
                    if (all.status) {
                        this._applyAmuleStatus(all.status);
                        this._amulePushSpeed(all.status.dl_speed || 0, all.status.ul_speed || 0);
                        if (this._amuleCurrentTab === 'stats') this._amuleDrawSpeedChart();
                    }
                    if (this._amuleCurrentTab === 'downloads' && all.downloads !== undefined)
                        this._renderAmuleDownloads(all.downloads);
                    if (this._amuleCurrentTab === 'uploads' && all.uploads !== undefined)
                        this._renderAmuleUploads(all.uploads);
                } else {
                    await this.amuleRefreshStatus();
                    if (this._amuleCurrentTab === 'downloads') await this.amuleLoadDownloads();
                    if (this._amuleCurrentTab === 'uploads')   await this.amuleLoadUploads();
                }
            } catch(e) {}
            finally { 
                // Intervallo 10s: 3 connessioni EC per poll (Status+dl+ul),
                // sotto la soglia flood-protection di amuled
                if (this._amuleIsPolling) this._amulePollId = setTimeout(poll, 10000);
            }
        };
        poll();
    },

    amuleStopPoll() {
        this._amuleIsPolling = false;
        if (this._amulePollId) { clearTimeout(this._amulePollId); this._amulePollId = null; }
    },

    amuleSwitchTab(tab) {
        this._amuleCurrentTab = tab;
        document.querySelectorAll('.amule-tab-content').forEach(el => el.style.display = 'none');
        document.querySelectorAll('[data-amule-tab]').forEach(btn => btn.classList.remove('active'));
        const tabEl = document.getElementById(`amule-tab-${tab}`);
        if (tabEl) tabEl.style.display = '';
        const btnEl = document.querySelector(`[data-amule-tab="${tab}"]`);
        if (btnEl) btnEl.classList.add('active');

        const _loadIfEmpty = (id, fn) => {
            const c = document.getElementById(id);
            if (c && c.querySelector('.amule-loading, .amule-empty')) fn();
        };
        if (tab === 'downloads') _loadIfEmpty('amule-downloads-list', () => this.amuleLoadDownloads());
        if (tab === 'servers')   _loadIfEmpty('amule-servers-rows',   () => this.amuleLoadServers());
        // Condivisi: se cache presente la usa subito (nessun loader visibile),
        // poi aggiorna silenziosamente in background se il flag dirty è attivo.
        if (tab === 'shared') {
            if (this._amuleSharedCache) {
                // Renderizza subito dalla cache — nessun flash di caricamento
                this._renderAmuleShared(this._amuleSharedCache.files, this._amuleSharedCache.dirs);
                // Se dirty (nuovo completato), aggiorna in background senza loader
                if (this._amuleSharedDirsDirty) {
                    this._amuleSharedDirsDirty = false;
                    Promise.all([
                        fetch(`${API_BASE}/api/amule/shared?force=1`).then(r=>r.json()).catch(()=>({shared:[]})),
                        fetch(`${API_BASE}/api/amule/shared/dirs`).then(r=>r.json()).catch(()=>({dirs:[]})),
                    ]).then(([fr, dr]) => {
                        const files = fr.shared || [];
                        const dirs  = dr.dirs   || [];
                        this._amuleSharedCache = { files, dirs, ts: Date.now() };
                        if (this._amuleCurrentTab === 'shared')
                            this._renderAmuleShared(files, dirs);
                    }).catch(() => {});
                }
            } else {
                // Prima apertura: carica normalmente con loader
                this.amuleLoadShared(false);
            }
        }
        if (tab === 'uploads')  _loadIfEmpty('amule-uploads-rows',   () => this.amuleLoadUploads());
        if (tab === 'settings') this.amuleLoadMaintConfig();
        if (tab === 'log')      this.amuleLoadLog();
        if (tab === 'stats')    _loadIfEmpty('amule-stats-container', () => this.amuleLoadStats());
        // Punto 5: search — ripristina risultati esistenti senza azzerare, non fa nulla se ricerca in corso
        if (tab === 'search' && this._amuleSearchResults.length > 0 && !this._amuleSearchActive) {
            this._amuleRenderSearchResults(this._amuleSearchResults);
        }
    },

    async amuleRefreshStatus() {
        try {
            const r = await fetch(`${API_BASE}/api/amule/status`);
            if (!r.ok) { this._amuleNetError(); return; }
            const s = await r.json();
            if (s.error && s.error.includes('amulecmd non trovato')) return;
            this._applyAmuleStatus(s);
        } catch(e) { this._amuleNetError(); }
    },

    async amuleLoadDownloads() {
        try {
            const r    = await fetch(`${API_BASE}/api/amule/downloads`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            this._renderAmuleDownloads(data.downloads || []);
        } catch(e) {
            const c = document.getElementById('amule-downloads-list');
            if (c) c.innerHTML = `<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Impossibile connettersi ad aMule.</span></div>`;
        }
    },

    // ── Storico velocità eD2k (max 60 campioni × 10s = 10 min) ──────────────
    _amuleSpeedHistory: { dl: [], ul: [], labels: [] },
    _amuleSpeedChartInst: null,

    _amulePushSpeed(dlBps, ulBps) {
        const h = this._amuleSpeedHistory;
        const now = new Date();
        const label = `${now.getHours().toString().padStart(2,'0')}:${now.getMinutes().toString().padStart(2,'0')}:${now.getSeconds().toString().padStart(2,'0')}`;
        h.dl.push(dlBps / 1048576);
        h.ul.push(ulBps / 1048576);
        h.labels.push(label);
        if (h.dl.length > 60) { h.dl.shift(); h.ul.shift(); h.labels.shift(); }
    },

    _amuleDrawSpeedChart() {
        const wrap = document.getElementById('amule-speed-chart-wrap');
        if (!wrap) return;
        const h = this._amuleSpeedHistory;

        // Rimuovi chart precedente se il canvas è sparito (es. cambio tab)
        const existing = document.getElementById('amule-speed-chart');
        if (!existing) {
            if (this._amuleSpeedChartInst) {
                this._amuleSpeedChartInst.destroy();
                this._amuleSpeedChartInst = null;
            }
            return;
        }

        if (typeof Chart !== 'undefined') {
            const data = {
                labels: [...h.labels],
                datasets: [
                    { label: 'Upload',   data: [...h.ul], borderColor: '#10b981',
                      backgroundColor: 'rgba(16,185,129,0.1)', borderWidth: 1.5,
                      pointRadius: 0, fill: true, tension: 0.35 },
                    { label: 'Download', data: [...h.dl], borderColor: '#3b82f6',
                      backgroundColor: 'rgba(59,130,246,0.1)', borderWidth: 1.5,
                      pointRadius: 0, fill: true, tension: 0.35 },
                ]
            };
            if (this._amuleSpeedChartInst) {
                this._amuleSpeedChartInst.data = data;
                this._amuleSpeedChartInst.update('none');
                return;
            }
            // Prima creazione: il canvas DEVE avere dimensioni fisse dal CSS del wrapper
            this._amuleSpeedChartInst = new Chart(existing, {
                type: 'line',
                data,
                options: {
                    responsive: true,
                    maintainAspectRatio: false,  // altezza gestita dal wrapper
                    animation: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: { mode: 'index', intersect: false,
                            callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} MB/s` }
                        }
                    },
                    scales: {
                        x: { display: true, ticks: { maxTicksLimit: 6, color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
                        y: { display: true, min: 0, ticks: { color: '#64748b', font: { size: 10 }, callback: v => v.toFixed(1) } , grid: { color: 'rgba(255,255,255,0.04)' } }
                    }
                }
            });
        } else {
            // Sparkline SVG fallback
            const canvas = existing;
            const W = wrap.offsetWidth || 500, H = 100;
            canvas.width = W; canvas.height = H;
            const ctx = canvas.getContext('2d');
            if (!ctx || !h.dl.length) return;
            ctx.clearRect(0, 0, W, H);
            const draw = (data, color) => {
                if (!data.length) return;
                const max = Math.max(...data, 0.01);
                ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.5;
                data.forEach((v, i) => {
                    const x = (i / Math.max(data.length-1,1)) * W;
                    const y = H - (v/max)*(H-8) - 4;
                    i === 0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
                });
                ctx.stroke();
            };
            draw(h.ul, '#10b981'); draw(h.dl, '#3b82f6');
        }
    },

    _fmtSpeed(bps) {
        if (!bps) return '0 B/s';
        if (bps >= 1048576) return `${(bps/1048576).toFixed(2)} MB/s`;
        if (bps >= 1024)    return `${(bps/1024).toFixed(1)} KB/s`;
        return `${bps} B/s`;
    },

    async amuleLoadStats() {
        const rawContainer = document.getElementById('amule-stats-container');
        const cardsEl      = document.getElementById('amule-stat-cards');
        const dlListEl     = document.getElementById('amule-stats-dl-list');
        const ulListEl     = document.getElementById('amule-stats-ul-list');

        let stats = {}, allData = { status: {}, downloads: [], uploads: [] };
        try {
            const [sr, ar] = await Promise.all([
                fetch(`${API_BASE}/api/amule/statistics`).then(r => r.ok ? r.json() : {}),
                fetch(`${API_BASE}/api/amule/all`).then(r => r.ok ? r.json() : {}),
            ]);
            stats   = sr.stats || sr || {};
            allData = ar;
        } catch(e) {}

        const status    = allData.status    || {};
        const downloads = allData.downloads || [];
        const uploads   = allData.uploads   || [];

        this._amulePushSpeed(status.dl_speed || 0, status.ul_speed || 0);

        // ── KPI cards ──────────────────────────────────────────────────────
        let totalDl = '—', totalUl = '—', shared = '—';
        let maxDl = '—', maxUl = '—', avgDl = '—', avgUl = '—';
        for (const [k, v] of Object.entries(stats)) {
            const kl = k.toLowerCase();
            // Alias normalizzati prodotti da amule.py get_statistics()
            if      (kl === 'total download')    totalDl = v;
            else if (kl === 'total upload')      totalUl = v;
            else if (kl === 'shared files')      shared  = v;
            else if (kl === 'max download rate') maxDl   = v;
            else if (kl === 'max upload rate')   maxUl   = v;
            else if (kl === 'avg download rate') avgDl   = v;
            else if (kl === 'avg upload rate')   avgUl   = v;
        }

        const kpis = [
            { icon: 'fa-arrow-down',  color: '#3b82f6', label: 'Download live',  value: this._fmtSpeed(status.dl_speed || 0) },
            { icon: 'fa-arrow-up',    color: '#10b981', label: 'Upload live',    value: this._fmtSpeed(status.ul_speed || 0) },
            { icon: 'fa-server',      color: '#8b5cf6', label: 'Server',         value: status.server_name || '—' },
            { icon: 'fa-id-card',     color: status.high_id ? '#10b981' : '#f59e0b',
                                      label: 'ID',              value: status.high_id ? 'High ID' : (status.ed2k_connected ? 'Low ID' : 'Disconnesso') },
            { icon: 'fa-users',       color: '#06b6d4', label: 'Utenti rete',    value: status.ed2k_users ? String(status.ed2k_users) : (stats['ed2k_users_stat'] || '—') },
            { icon: 'fa-file',        color: '#f59e0b', label: 'File rete',      value: status.ed2k_files ? String(status.ed2k_files) : (stats['ed2k_files_stat'] || '—') },
            { icon: 'fa-download',    color: '#3b82f6', label: 'Scaricati (sessione)', value: String(totalDl) },
            { icon: 'fa-upload',      color: '#10b981', label: 'Caricati (sessione)',  value: String(totalUl) },
            { icon: 'fa-folder-open', color: '#8b5cf6', label: 'File condivisi', value: String(shared) },
            { icon: 'fa-gauge-high',  color: '#f59e0b', label: 'Max DL',         value: String(maxDl) },
            { icon: 'fa-gauge-high',  color: '#10b981', label: 'Max UL',         value: String(maxUl) },
            { icon: 'fa-chart-line',  color: '#64748b', label: 'Media DL',       value: String(avgDl) },
        ];

        if (cardsEl) {
            cardsEl.innerHTML = kpis.map(k => `
                <div class="amule-stat-kpi amule-stat-card">
                    <div class="amule-stat-kpi-label">
                        <i class="fa-solid ${k.icon}" style="color:${k.color};font-size:0.7rem;"></i>${k.label}
                    </div>
                    <div class="amule-stat-kpi-value">${this._esc(k.value)}</div>
                </div>`).join('');
        }

        // ── Mini download list ─────────────────────────────────────────────
        if (dlListEl) {
            const active = downloads.filter(d => (d.progress||0) < 100);
            dlListEl.innerHTML = !active.length
                ? '<span style="color:var(--text-muted);font-size:0.78rem;">Nessun download attivo</span>'
                : active.slice(0,6).map(d => `
                    <div class="amule-stats-dl-row">
                        <span class="amule-stats-dl-name" title="${this._esc(d.name)}">${this._esc(d.name)}</span>
                        <span class="amule-stats-dl-speed" style="color:#3b82f6;">${this._fmtSpeed(d.speed||0)}</span>
                        <span class="amule-stats-dl-pct">${d.progress||0}%</span>
                    </div>
                    <div class="amule-stats-pbar"><div class="amule-stats-pbar-fill" style="width:${Math.min(100,d.progress||0)}%;"></div></div>`).join('');
        }
        if (ulListEl) {
            ulListEl.innerHTML = !uploads.length
                ? '<span style="color:var(--text-muted);font-size:0.78rem;">Nessun upload attivo</span>'
                : uploads.slice(0,6).map(u => `
                    <div class="amule-stats-dl-row">
                        <span class="amule-stats-dl-name">${this._esc(u.file_name||'')}</span>
                        <span class="amule-stats-dl-speed" style="color:#10b981;">${this._fmtSpeed(u.ul_speed||0)}</span>
                        <span class="amule-stats-dl-pct" style="color:var(--text-muted);">(${u.queue_rank||0})</span>
                    </div>`).join('');
        }

        // ── Chart ──────────────────────────────────────────────────────────
        this._amuleDrawSpeedChart();

        // ── Raw stats ──────────────────────────────────────────────────────
        if (rawContainer) {
            rawContainer.innerHTML = !Object.keys(stats).length
                ? '<span style="color:var(--text-muted);">Nessuna statistica raw.</span>'
                : Object.entries(stats).map(([k,v]) =>
                    `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.04);">
                        <span style="color:var(--text-muted);">${this._esc(k)}</span>
                        <span style="font-family:var(--font-mono);color:var(--primary-light);">${this._esc(String(v))}</span>
                    </div>`).join('');
        }
    },


    async amuleLoadLog() {
        const container = document.getElementById('amule-log-container');
        if (!container) return;
        container.innerHTML = '<div class="amule-loading"><i class="fa-solid fa-spinner fa-spin"></i> Lettura log in corso...</div>';
        try {
            const r = await fetch(`${API_BASE}/api/amule/log?lines=200`);
            const data = await r.json();
            if (data.success) {
                if (!data.logs || data.logs.length === 0) {
                    container.innerHTML = `<div style="padding:1rem;color:var(--text-muted);">Il file di log è vuoto.</div>`;
                    return;
                }
                const LOG_SPAM = [
                    'accettata nuova connessione esterna',
                    'connessione al client: amulecmd',
                    'accesso consentito',
                    'connessione esterna chiusa',
                ];
                const filteredLogs = data.logs.filter(line => {
                    const ll = line.toLowerCase();
                    return !LOG_SPAM.some(s => ll.includes(s));
                });
                container.innerHTML = filteredLogs.map(line => {
                    let cls = 'log-line';
                    const lower = line.toLowerCase();
                    if (lower.includes('error') || lower.includes('err:')) cls += ' error';
                    else if (lower.includes('warn')) cls += ' warning';
                    else if (lower.includes('success') || lower.includes('connesso')) cls += ' success';
                    return `<div class="${cls}">${this.escapeHtml(line.trim())}</div>`;
                }).join('');
                container.scrollTop = container.scrollHeight; 
            } else {
                container.innerHTML = `<div style="padding:1rem;color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i> ${this.escapeHtml(data.error)}</div>`;
            }
        } catch(e) {
            container.innerHTML = `<div style="padding:1rem;color:var(--danger);">Errore di comunicazione per i log di aMule.</div>`;
        }
    },

    // ── NETWORK STATUS BAR ───────────────────────────────────────────────────


    // ── Helper: applica dati status alla UI (usato da poll consolidato) ───────
    _applyAmuleStatus(s) {
        this._amuleConnectedServer = s.server_address || null;
        this._amuleLastStatus = s;

        const ed2kDot  = document.getElementById('amule-ed2k-dot');
        const ed2kText = document.getElementById('amule-ed2k-text');
        if (ed2kDot && ed2kText) {
            if (s.ed2k_connected) {
                const hi = s.high_id;
                ed2kDot.className    = `amule-net-dot ${hi ? 'ok' : 'warn'}`;
                ed2kText.textContent = hi ? 'High ID' : 'Low ID';
                ed2kText.style.color = hi ? 'var(--success)' : 'var(--warning)';
            } else {
                ed2kDot.className    = 'amule-net-dot err';
                ed2kText.textContent = 'Disconnesso';
                ed2kText.style.color = 'var(--danger)';
            }
        }
        const kadDot  = document.getElementById('amule-kad-dot');
        const kadText = document.getElementById('amule-kad-text');
        if (kadDot && kadText) {
            if (s.kad_connected) {
                kadDot.className    = `amule-net-dot ${s.kad_firewalled ? 'warn' : 'ok'}`;
                kadText.textContent = s.kad_firewalled ? 'Firewalled' : 'OK';
                kadText.style.color = s.kad_firewalled ? 'var(--warning)' : 'var(--success)';
            } else {
                kadDot.className    = 'amule-net-dot err';
                kadText.textContent = 'Disconnesso';
                kadText.style.color = 'var(--danger)';
            }
        }
        const idEl = document.getElementById('amule-client-id');
        if (idEl) idEl.textContent = s.high_id ? 'High ID' : (s.ed2k_connected ? 'Low ID' : '—');

        const srvPill = document.getElementById('amule-server-pill');
        const srvName = document.getElementById('amule-server-name');
        const srvPing = document.getElementById('amule-server-ping');
        if (srvPill) {
            if (s.server_name) {
                srvPill.style.display = '';
                if (srvName) srvName.textContent = s.server_name;
                if (srvPing) srvPing.textContent = s.server_ping ? `${s.server_ping} ms` : '';
            } else { srvPill.style.display = 'none'; }
        }
        const dlRateEl = document.getElementById('amule-dl-rate');
        const ulRateEl = document.getElementById('amule-ul-rate');
        if (dlRateEl) dlRateEl.textContent = s.dl_speed > 0 ? this._fmtRate(s.dl_speed) : '—';
        if (ulRateEl) ulRateEl.textContent = s.ul_speed > 0 ? this._fmtRate(s.ul_speed) : '—';
        const usersEl = document.getElementById('amule-users-count');
        if (usersEl && s.ed2k_users > 0)
            usersEl.textContent = `${(s.ed2k_users / 1_000_000).toFixed(2)}M utenti`;
    },

    // ── Helper: render download list (usato da poll consolidato) ──────────────
    _renderAmuleDownloads(list) {
        const container = document.getElementById('amule-downloads-list');
        if (!container) return;

        // Salva la lista live (senza completati dalla cache) per amuleCleanCompleted
        this._amuleLastDownloads = list;

        // BUG2 FIX: Salva i completati nella memoria.
        // NON azzerare _amuleSharedCache qui — causa ricaricamento continuo al poll (ogni 4s).
        // Usa invece il flag _amuleSharedDirsDirty che viene controllato solo al cambio tab.
        list.forEach(d => {
            if (d.status === 'Completed' || parseFloat(d.progress) >= 100) {
                if (!this._amuleCompletedCache.has(d.hash)) {
                    // Nuovo completato: segnala che i condivisi vanno aggiornati
                    // al prossimo accesso al tab (lazy), NON subito
                    this._amuleSharedDirsDirty = true;
                    this._amuleCompletedCache.set(d.hash, d);
                }
            }
        });

        // 2. Unisci la lista attuale con la memoria
        const liveHashes = new Set(list.map(d => d.hash));
        const mergedList = [...list];
        for (const [hash, cachedItem] of this._amuleCompletedCache.entries()) {
            if (!liveHashes.has(hash)) {
                mergedList.push(cachedItem);
            }
        }
        list = mergedList; // Usa la lista unita per il rendering

        const badge = document.getElementById('amule-tab-badge-downloads');
        if (badge) badge.textContent = list.length || '';
        if (!list.length) {
            container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-inbox"></i><span>Nessun download in corso</span></div>`;
            return;
        }
        const statusColor = {
            'Downloading': 'var(--success)', 'Completed':  'var(--info)',
            'Paused':      'var(--warning)', 'Stopped':    'var(--text-muted)',
            'Searching':   'var(--warning)', 'Connecting': 'var(--text-muted)',
            'Error':       'var(--danger)',
        };
        // Usa amule-dl-grid: NOME | DIM. | PROGRESSO | STATO | VELOCITÀ | SORGENTI | azioni
        container.innerHTML = list.map(d => {
            // Progress: usa ratio grezzo se progress=0 ma ratio>0 (size non parsato)
            const ratio  = d.ratio || 0;
            const pct    = d.progress > 0 ? Math.min(d.progress, 100)
                         : ratio > 0      ? Math.min(ratio * 100, 100)
                         : 0;
            const color  = statusColor[d.status] || 'var(--text-muted)';
            const speed  = d.speed > 0 ? this._fmtRate(d.speed) : '—';
            // Dimensione: mostra stima con ~ se size non noto con certezza
            const sizeKnown = d.size_known !== false && d.size > 0;
            const sizeStr = d.size > 0
                ? (d.completed_size > 0 && d.completed_size < d.size
                    ? `${this._fmtBytes(d.completed_size)} / ${sizeKnown ? '' : '~'}${this._fmtBytes(d.size)}`
                    : `${sizeKnown ? '' : '~'}${this._fmtBytes(d.size)}`)
                : (d.completed_size > 0 ? this._fmtBytes(d.completed_size) : '—');
            // Sorgenti: xfer in verde / totale
            const src = (d.sources > 0 || d.sources_xfer > 0)
                ? `<span style="color:var(--success)">${d.sources_xfer||0}</span><span style="color:var(--text-muted)">/${d.sources||0}</span>`
                : '<span style="color:var(--text-muted)">—</span>';
            const bar = `<div style="position:relative;height:16px;background:var(--border);border-radius:3px;overflow:hidden;min-width:60px;">
                <div style="position:absolute;left:0;top:0;height:100%;width:${pct}%;background:${color};opacity:0.35;transition:width .6s;"></div>
                <span style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:600;color:var(--text-main);">${pct.toFixed(1)}%</span>
            </div>`;
            return `<div class="amule-row amule-dl-grid" title="${this._esc(d.name)}\nHash: ${d.hash||''}">
                <div class="amule-name" style="overflow:hidden;">
                    <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${this._esc(d.name)}</div>
                    ${d.priority ? `<div class="amule-sub" style="font-size:0.68rem;">${this._esc(d.priority)}</div>` : ''}
                </div>
                <div class="amule-mono" style="color:var(--text-muted);text-align:right;font-size:0.75rem;">${sizeStr}</div>
                <div style="padding:0 4px;">${bar}</div>
                <div class="amule-mono" style="color:${color};font-size:0.75rem;">${d.status}</div>
                <div class="amule-mono" style="color:${d.speed>0?'var(--success)':'var(--text-muted)'};text-align:right;">${speed}</div>
                <div class="amule-mono" style="text-align:right;">${src}</div>
                <div class="amule-acts">
                    <button class="amule-act" onclick="app.amulePause('${d.hash}')"  title="Pausa"><i class="fa-solid fa-pause"></i></button>
                    <button class="amule-act" onclick="app.amuleResume('${d.hash}')" title="Riprendi"><i class="fa-solid fa-play"></i></button>
                    <button class="amule-act" onclick="app.amuleCancel('${d.hash}','${this._esc(d.name)}')" title="Cancella" style="color:var(--danger);"><i class="fa-solid fa-trash"></i></button>
                </div>
            </div>`;
        }).join('');
    },

    // ── Helper: render upload list ─────────────────────────────────────────────
    _renderAmuleUploads(list) {
        const container = document.getElementById('amule-uploads-rows');
        if (!container) return;
        const badge = document.getElementById('amule-tab-badge-uploads');
        if (badge) badge.textContent = list.length || '';
        if (!list.length) {
            container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-arrow-up"></i><span>Nessun upload attivo.</span></div>`;
            return;
        }
        container.innerHTML = list.map(u => {
            const speed = u.ul_speed || u.up_speed || 0;
            return `<div class="amule-row amule-ul-grid">
                <div class="amule-name" title="${this._esc(u.file_name||u.name||'')}">${this._esc(u.file_name||u.name||'—')}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${this._esc(u.client_ip||u.user_name||'—')}</div>
                <div class="amule-mono" style="color:${speed>0?'var(--warning)':'var(--text-muted)'};">${speed>0?this._fmtRate(speed):'—'}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(u.upload_session||u.transferred||0)}</div>
                <div class="amule-mono" style="color:var(--text-muted);font-size:0.75rem;">${this._esc(u.software||'—')}</div>
            </div>`;
        }).join('');
    },

    

    _amuleNetError() {
        ['amule-ed2k-dot','amule-kad-dot'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.className = 'amule-net-dot err';
        });
        ['amule-ed2k-text','amule-kad-text'].forEach(id => {
            const el = document.getElementById(id);
            if (el) { el.textContent = 'N/D'; el.style.color = 'var(--danger)'; }
        });
    },

    // ── SCARICHI — progress bar con strisce animate ───────────────────────────

    

    async _amuleLoadDownloads_UNUSED() {
        const container = document.getElementById('amule-downloads-list');
        if (!container) return;
        try {
            const r    = await fetch(`${API_BASE}/api/amule/downloads`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            const list = data.downloads || [];

            const badge = document.getElementById('amule-tab-badge-downloads');
            if (badge) badge.textContent = list.length || '';
            const summary = document.getElementById('amule-dl-summary');
            if (summary) {
                const active = list.filter(d => !d.paused && parseFloat(d.progress) < 100).length;
                summary.textContent = active > 0 ? `${active} attivi` : '';
            }
            if (!list.length) {
                container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-inbox"></i><span>Nessun download in corso</span></div>`;
                return;
            }
            container.innerHTML = list.map(d => {
                const pct      = Math.min(100, Math.max(0, parseFloat(d.progress) || 0));
                const isDone   = pct >= 100;
                const isActive = !d.paused && !isDone;
                const fillCls  = isDone ? 'done' : (isActive ? 'active' : '');
                let stateCls = 'amule-state-q', stateLabel = 'In coda';
                if (isDone)        { stateCls = 'amule-state-done';  stateLabel = 'Completato'; }
                else if (d.paused) { stateCls = 'amule-state-pause'; stateLabel = 'In pausa'; }
                else if (d.state === 'error') { stateCls = 'amule-state-err'; stateLabel = 'Errore'; }
                else if (isActive) { stateCls = 'amule-state-dl';   stateLabel = 'Download'; }
                return `<div class="amule-row amule-dl-grid" title="${this._esc(d.name)}">
                    <div class="amule-name"><div>${this._esc(d.name || '—')}</div></div>
                    <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(d.total_size)}</div>
                    <div class="amule-prog-wrap">
                        <div class="amule-prog-pct">${pct.toFixed(1)}%</div>
                        <div class="amule-prog-bar"><div class="amule-prog-fill ${fillCls}" style="width:${pct}%"></div></div>
                    </div>
                    <div><span class="amule-state ${stateCls}">${stateLabel}</span></div>
                    <div class="amule-mono" style="color:var(--success);">${isActive ? this._fmtRate(d.dl_speed) : '—'}</div>
                    <div class="amule-mono" style="color:var(--info);">${d.sources || 0}</div>
                    <div class="amule-acts">
                        ${!d.paused && !isDone ? `<button class="amule-act amule-act-pause" onclick="app.amulePause('${d.hash}')" title="Pausa"><i class="fa-solid fa-pause"></i></button>` : ''}
                        ${d.paused  && !isDone ? `<button class="amule-act amule-act-resume" onclick="app.amuleResume('${d.hash}')" title="Riprendi"><i class="fa-solid fa-play"></i></button>` : ''}
                        <button class="amule-act amule-act-del" onclick="app.amuleCancel('${d.hash}','${this._esc(d.name)}')" title="Cancella"><i class="fa-solid fa-trash"></i></button>
                    </div>
                </div>`;
            }).join('');
        } catch(e) {
            container.innerHTML = `<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Impossibile connettersi ad aMule.</span></div>`;
        }
    },

    amuleShowAddLink() { document.getElementById('amule-add-modal').classList.add('active'); },

    async amuleSubmitLinks() {
        const raw   = document.getElementById('amule-add-link-input').value.trim();
        if (!raw) return;
        const links = raw.split('\n').map(l => l.trim())
            .filter(l => l.startsWith('ed2k://') || l.startsWith('magnet:'));
        if (!links.length) { this.showToast('Nessun link ed2k/magnet valido', 'error'); return; }
        let ok = 0, fail = 0;
        for (const link of links) {
            try {
                const r = await fetch(`${API_BASE}/api/amule/add`, {
                    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ link })
                });
                const d = await r.json();
                if (d.ok) {
                    ok++;
                    // Estrai nome file dal link ed2k per il log
                    const nameMatch = link.match(/\|file\|([^|]+)\|/);
                    const fname = nameMatch ? nameMatch[1] : link.slice(0,60)+'...';
                    console.log(`[aMule] Link aggiunto: ${fname}`);
                } else { fail++; }
            } catch(e) { fail++; }
        }
        this.closeModal('amule-add-modal');
        document.getElementById('amule-add-link-input').value = '';
        if (ok)   this.showToast(`✓ ${ok} link inviati ad aMule`, 'success');
        if (fail) this.showToast(`✗ ${fail} link non accettati`, 'error');
        // Ricarica dopo breve delay per dare tempo ad aMule di processare
        setTimeout(() => this.amuleLoadDownloads(), 1500);
    },

    async amulePause(hash) {
        try {
            await fetch(`${API_BASE}/api/amule/pause`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({hash}) });
            await this.amuleLoadDownloads();
        } catch(e) { this.showToast('Errore pausa', 'error'); }
    },
    async amuleResume(hash) {
        try {
            await fetch(`${API_BASE}/api/amule/resume`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({hash}) });
            await this.amuleLoadDownloads();
        } catch(e) { this.showToast('Errore ripresa', 'error'); }
    },
    async amuleCancel(hash, name) {
        if (!confirm(`Cancellare "${name}"?`)) return;
        try {
            const r = await fetch(`${API_BASE}/api/amule/cancel`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({hash}) });
            const d = await r.json();
            if (d.ok) { this.showToast('Download cancellato', 'success'); await this.amuleLoadDownloads(); }
            else this.showToast('Errore cancellazione', 'error');
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },
    async amulePauseAll() {
        const r = await fetch(`${API_BASE}/api/amule/downloads`).then(r=>r.json()).catch(()=>({downloads:[]}));
        for (const d of (r.downloads||[])) if (!d.paused && parseFloat(d.progress) < 100) await this.amulePause(d.hash);
    },
    async amuleResumeAll() {
        const r = await fetch(`${API_BASE}/api/amule/downloads`).then(r=>r.json()).catch(()=>({downloads:[]}));
        for (const d of (r.downloads||[])) if (d.paused) await this.amuleResume(d.hash);
    },

    // BUG3 FIX: Recupera file .part orfani dalla TempDir di aMule
    async amuleRecoverParts() {
        this.showToast('Scansione TempDir in corso...', 'info');
        try {
            const r = await fetch(`${API_BASE}/api/amule/recover-parts`);
            const d = await r.json();
            if (!d.ok) {
                this.showToast('❌ ' + (d.error || 'Errore recupero .part'), 'error');
                return;
            }
            const rec  = d.recovered?.length  || 0;
            const skip = d.skipped?.length     || 0;
            const errs = d.errors?.length      || 0;
            let msg = rec > 0
                ? `✅ Recuperati ${rec} file .part`
                : 'Nessun file .part orfano trovato';
            if (skip > 0) msg += ` · ${skip} già in coda`;
            if (errs > 0) msg += ` · ${errs} errori (vedi console)`;
            this.showToast(msg, rec > 0 ? 'success' : 'info');
            if (rec > 0) setTimeout(() => this.amuleLoadDownloads(), 2000);
            if (errs > 0) console.warn('amuleRecoverParts errors:', d.errors);
        } catch(e) {
            this.showToast('Errore comunicazione con il server', 'error');
        }
    },

    // ── RICERCA ──────────────────────────────────────────────────────────────

    async amuleStartSearch() {
        const query   = (document.getElementById('amule-search-query')?.value || '').trim();
        if (!query) { this.showToast('Inserisci un termine di ricerca', 'error'); return; }
        const network = document.getElementById('amule-search-network')?.value || 'global';
        const ext     = document.getElementById('amule-search-ext')?.value?.trim() || '';
        const prog    = document.getElementById('amule-search-progress');
        const res     = document.getElementById('amule-search-results');
        const empty   = document.getElementById('amule-search-empty');
        const btn     = document.getElementById('amule-search-btn');
        if (prog)  prog.style.display  = '';
        if (res)   res.style.display   = 'none';
        if (empty) empty.style.display = 'none';
        if (btn)   { btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Ricerca...'; }
        this._amuleSearchActive  = true;
        this._amuleSearchResults = []; // pulisce risultati precedenti
        try {
            const r = await fetch(`${API_BASE}/api/amule/search`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({ query, network, extension: ext })
            });
            const data = await r.json();
            this._amuleSearchResults = data.results || [];
            if (!this._amuleSearchResults.length && data.message) {
                this.showToast(data.message, 'warn');
            }
            this._amuleRenderSearchResults(this._amuleSearchResults);
        } catch(e) { this.showToast('Errore ricerca aMule', 'error'); }
        finally {
            this._amuleSearchActive = false;
            if (prog) prog.style.display = 'none';
            if (btn)  { btn.disabled = false; btn.innerHTML = 'Cerca'; }
        }
    },

    _amuleRenderSearchResults(results) {
        const rowsEl  = document.getElementById('amule-search-rows');
        const resEl   = document.getElementById('amule-search-results');
        const emptyEl = document.getElementById('amule-search-empty');
        const countEl = document.getElementById('amule-search-count');
        if (!rowsEl) return;
        if (!results.length) {
            if (resEl)   resEl.style.display   = 'none';
            if (emptyEl) emptyEl.style.display = '';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (countEl) countEl.textContent = `${results.length} risultati`;
        const srcColor = (n) => n >= 100 ? 'var(--success)' : n >= 20 ? 'var(--primary-light)' : 'var(--text-muted)';
        rowsEl.innerHTML = results.map((res, i) => `
            <div class="amule-row amule-result-grid" data-name="${this._esc(res.name).toLowerCase()}">
                <div class="amule-name" title="${this._esc(res.name)}">${this._esc(res.name)}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(res.size)}</div>
                <div style="display:flex;align-items:center;gap:3px;font-family:var(--font-mono);font-weight:700;color:${srcColor(res.sources||0)};">
                    ${res.sources||0} <i class="fa-solid fa-users" style="font-size:0.65rem;color:var(--text-muted);"></i>
                </div>
                <div class="amule-acts">
                    <button class="amule-act amule-act-dl" onclick="app.amuleDownloadResult(${i})" title="Scarica">
                        <i class="fa-solid fa-download"></i>
                    </button>
                </div>
            </div>`).join('');
        if (resEl) resEl.style.display = '';
    },

    amuleFilterSearchResults(term) {
        const q = (term || '').toLowerCase();
        document.querySelectorAll('#amule-search-rows .amule-row').forEach(row => {
            row.classList.toggle('amule-filter-hidden', q.length > 0 && !(row.dataset.name||'').includes(q));
        });
    },

    async amuleDownloadResult(idx) {
        const res = this._amuleSearchResults[idx];
        if (!res) { this.showToast('Risultato non trovato', 'error'); return; }
        try {
            const r = await fetch(`${API_BASE}/api/amule/search/download`, {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ idx: res.idx ?? idx })
            });
            const d = await r.json();
            if (d.ok) {
                this.showToast(`⬇ Avviato: ${res.name.slice(0,50)}`, 'success');
                setTimeout(() => this.amuleSwitchTab('downloads'), 1200);
            } else {
                this.showToast('aMule non ha accettato il download', 'error');
            }
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    // ── SERVER ───────────────────────────────────────────────────────────────

    async amuleLoadServers() {
        const container = document.getElementById('amule-servers-rows');
        if (!container) return;
        try {
            const r       = await fetch(`${API_BASE}/api/amule/servers`);
            if (!r.ok) throw new Error();
            const data    = await r.json();
            const servers = data.servers || (Array.isArray(data) ? data : []);
            
            // Punto 9: aggiorna sempre il campo met_url con quello letto live da aMule
            if (data.met_url) {
                const urlInput = document.getElementById('amule-server-met-url');
                if (urlInput) urlInput.value = data.met_url;
            }

            const badge   = document.getElementById('amule-tab-badge-servers');
            if (badge) badge.textContent = servers.length || '';
            if (!servers.length) {
                container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-server"></i><span>Nessun server. Importa un .met per aggiungerne.</span></div>`;
                return;
            }
            // Mostra totali rete (da ultimo status refresh)
            const totalBar = document.getElementById('amule-network-totals');
            if (totalBar && this._amuleLastStatus) {
                const s = this._amuleLastStatus;
                const u = s.ed2k_users ? (s.ed2k_users/1000000).toFixed(2)+'M utenti' : '';
                const f = s.ed2k_files ? (s.ed2k_files/1000000).toFixed(1)+'M file' : '';
                if (u || f) totalBar.textContent = `Rete: ${[u,f].filter(Boolean).join(' · ')}`;
            }
            container.innerHTML = servers.map(s => {
                const addr   = s.address || `${s.ip}:${s.port}`;
                const isConn = this._amuleConnectedServer && addr === this._amuleConnectedServer;
                return `<div class="amule-row amule-srv-grid ${isConn ? 'connected' : ''}">
                    <div class="amule-name">
                        <div style="display:flex;align-items:center;gap:5px;">
                            ${isConn ? '<span class="amule-net-dot ok" style="display:inline-block;flex-shrink:0;"></span>' : ''}
                            ${this._esc(s.name || '—')}
                        </div>
                        ${s.desc ? `<div class="amule-sub">${this._esc(s.desc)}</div>` : ''}
                    </div>
                    <div class="amule-mono" style="color:var(--text-muted);">${this._esc(addr)}</div>
                    <div class="amule-mono" style="color:var(--text-muted);">${s.users > 0 ? s.users.toLocaleString() : '—'}</div>
                    <div class="amule-mono" style="color:var(--text-muted);">${s.files > 0 ? s.files.toLocaleString() : '—'}</div>
                    <div class="amule-acts">
                        ${!isConn
                            ? `<button class="amule-act amule-act-conn" onclick="app.amuleConnectServer('${this._esc(s.ip)}',${s.port})" title="Connetti"><i class="fa-solid fa-power-off"></i></button>`
                            : `<span class="amule-state amule-state-done" style="font-size:0.65rem;">Connesso</span>`}
                    </div>
                </div>`;
            }).join('');
        } catch(e) {
            container.innerHTML = `<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Impossibile caricare i server.</span></div>`;
        }
    },

    async amuleConnectServer(ip, port) {
        try {
            const r = await fetch(`${API_BASE}/api/amule/server/connect`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ip, port }) });
            const d = await r.json();
            if (d.ok) { this.showToast(`Connessione a ${ip}:${port} avviata`, 'success'); setTimeout(() => { this.amuleRefreshStatus(); this.amuleLoadServers(); }, 1500); }
            else this.showToast('Connessione fallita', 'error');
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    async amuleUpdateServerMet() {
        const url = document.getElementById('amule-server-met-url')?.value?.trim();
        if (!url) { this.showToast('Inserisci un URL .met', 'error'); return; }
        this.showToast('Importazione server...', 'info');
        try {
            const r = await fetch(`${API_BASE}/api/amule/server/update`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ url }) });
            const d = await r.json();
            if (d.ok) { this.showToast('Lista server aggiornata', 'success'); setTimeout(() => this.amuleLoadServers(), 2500); }
            else this.showToast('Aggiornamento fallito', 'error');
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    // ── CONDIVISI ────────────────────────────────────────────────────────────

    sortShared(key) {
        if (this._amuleSharedSortKey === key) {
            this._amuleSharedSortDir *= -1;
        } else {
            this._amuleSharedSortKey = key;
            this._amuleSharedSortDir = key === 'file_name' ? 1 : -1;
        }
        if (this._amuleSharedCache)
            this._renderAmuleShared(this._amuleSharedCache.files, this._amuleSharedCache.dirs);
    },

    async amuleLoadShared(force = false) {
        const container = document.getElementById('amule-shared-rows');
        const dirsEl    = document.getElementById('amule-shared-dirs');
        if (!container) return;

        // Usa la cache se disponibile e non invalidata
        if (!force && this._amuleSharedCache && !this._amuleSharedDirsDirty) {
            this._renderAmuleShared(this._amuleSharedCache.files, this._amuleSharedCache.dirs);
            return;
        }

        container.innerHTML = '<div class="amule-loading"><i class="fa-solid fa-spinner fa-spin"></i> Caricamento file condivisi...</div>';
        if (dirsEl) dirsEl.innerHTML = '<div class="amule-loading"><i class="fa-solid fa-spinner fa-spin"></i></div>';

        // Polling: il backend carica i file in un thread separato per non bloccare.
        // Se risponde con loading:true, il client riprova ogni 2s fino a 60 tentativi (2 min).
        const self = this;
        const pollShared = async (attempt) => {
            attempt = attempt || 0;
            try {
                const qs = (force && attempt === 0) ? '?force=1' : '';
                const [filesRes, dirsRes] = await Promise.all([
                    fetch(API_BASE + '/api/amule/shared' + qs).then(function(r){ return r.json(); }).catch(function(){ return {shared:[], loading:false}; }),
                    fetch(API_BASE + '/api/amule/shared/dirs').then(function(r){ return r.json(); }).catch(function(){ return {dirs:[]}; }),
                ]);

                if (filesRes.loading) {
                    if (attempt < 60) {
                        container.innerHTML = '<div class="amule-loading"><i class="fa-solid fa-spinner fa-spin"></i> Caricamento in corso\u2026 (' + (attempt + 1) + ')</div>';
                        setTimeout(function(){ pollShared(attempt + 1); }, 2000);
                    } else {
                        container.innerHTML = '<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Timeout caricamento file condivisi.</span></div>';
                    }
                    return;
                }

                const files = filesRes.shared || [];
                const dirs  = dirsRes.dirs   || [];
                self._amuleSharedCache     = { files: files, dirs: dirs, ts: Date.now() };
                self._amuleSharedDirsDirty = false;
                self._renderAmuleShared(files, dirs);
            } catch(e) {
                container.innerHTML = '<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Errore caricamento.</span></div>';
            }
        };
        pollShared(0);
    },

    _renderAmuleShared(files, dirs) {
        const container = document.getElementById('amule-shared-rows');
        const dirsEl    = document.getElementById('amule-shared-dirs');
        if (!container) return;

        if (dirsEl) {
            if (dirs.length) {
                dirsEl.innerHTML = dirs.map(d => {
                    const path       = typeof d === 'object' ? d.path      : d;
                    const removable  = typeof d === 'object' ? d.removable !== false : true;
                    const isIncoming = typeof d === 'object' && d.source === 'incoming';
                    const icon = isIncoming
                        ? `<i class="fa-solid fa-folder-arrow-down" style="color:var(--success);flex-shrink:0;" title="Cartella Download"></i>`
                        : `<i class="fa-regular fa-folder" style="color:var(--warning);flex-shrink:0;"></i>`;
                    const recBadge = d.is_recursive
                        ? `<span class="amule-dir-badge" style="background:rgba(59,130,246,0.15);color:var(--primary-light);margin-left:5px;"><i class="fa-solid fa-sitemap"></i> Ricorsivo</span>`
                        : '';
                    const badge  = isIncoming ? `<span class="amule-dir-badge">Incoming</span>` : recBadge;
                    const action = removable
                        ? `<button class="amule-dir-remove" title="Rimuovi dalla condivisione"
                            onclick="app.amuleRemoveSharedFolder('${this._esc(path).replace(/'/g,"\\'")}')">
                            <i class="fa-solid fa-xmark"></i></button>`
                        : `<span class="amule-dir-fixed">fisso</span>`;
                    return `<div class="amule-dir-row">${icon}<span class="amule-dir-path">${this._esc(path)}${badge}</span>${action}</div>`;
                }).join('');
            } else {
                dirsEl.innerHTML = `<div style="padding:8px 12px;color:var(--text-muted);font-size:0.82rem;"><i class="fa-solid fa-circle-info"></i> Nessuna cartella condivisa aggiuntiva.</div>`;
            }
        }

        const badge = document.getElementById('amule-tab-badge-shared');
        if (badge) badge.textContent = files.length || '';
        if (!files.length) {
            container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-folder-open"></i><span>Nessun file condiviso trovato.</span></div>`;
            return;
        }

        // sort
        const sk = this._amuleSharedSortKey || 'file_name';
        const sd = this._amuleSharedSortDir || 1;
        const sorted = [...files].sort((a, b) => {
            let va = a[sk] ?? (typeof a[sk] === 'number' ? 0 : '');
            let vb = b[sk] ?? (typeof b[sk] === 'number' ? 0 : '');
            if (typeof va === 'string') return va.localeCompare(vb, undefined, {sensitivity:'base'}) * sd;
            return ((va || 0) - (vb || 0)) * sd;
        });
        // frecce intestazioni
        ['file_name','file_size','req_count','accepted_count','transferred','upload_speed'].forEach(k => {
            const el = document.getElementById(`amule-sort-arrow-${k}`);
            if (el) el.textContent = k === sk ? (sd > 0 ? '▲' : '▼') : '';
        });

        container.innerHTML = sorted.map(f => `
            <div class="amule-row amule-shared-grid" title="${this._esc(f.file_name||f.path||'')}">
                <div class="amule-name">
                    <div>${this._esc(f.file_name || '—')}</div>
                    ${f.path ? `<div class="amule-sub" style="font-size:0.72rem;">${this._esc(f.path)}</div>` : ''}
                </div>
                <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(f.file_size||0)}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${f.req_count ?? '—'}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${f.accepted_count ?? '—'}</div>
                <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(f.transferred||0)}</div>
                <div class="amule-mono" style="color:${(f.upload_speed||0)>0?'var(--warning)':'var(--text-muted)'};">${f.upload_speed>0?this._fmtRate(f.upload_speed):'—'}</div>
            </div>`).join('');
    },

    async amuleReloadShared() {
        try {
            this.showToast('Riscansione...', 'info');
            const r = await fetch(`${API_BASE}/api/amule/shared/reload`, { method:'POST' });
            const d = await r.json();
            if (d.ok) {
                this.showToast('Riscansione avviata', 'success');
                this._amuleSharedCache = null; // invalida cache
                setTimeout(() => this.amuleLoadShared(true), 2000);
            } else this.showToast('Errore riscansione', 'error');
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    async amuleAddSharedFolder() {
        const input = document.getElementById('amule-shared-path');
        const path  = input?.value?.trim();
        const cb = document.getElementById('amule-shared-recursive');
        const recursive = cb ? cb.checked : false;
        if (!path) { this.showToast('Inserisci un percorso', 'error'); return; }
        try {
            this.showToast('Aggiunta cartelle in corso...', 'info');
            const r = await fetch(`${API_BASE}/api/amule/shared/add`, {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({ path, recursive })
            });
            const d = await r.json();
            if (d.ok) {
                this.showToast(d.message || 'Cartelle aggiunte', 'success');
                if (input) input.value = '';
                if (cb) cb.checked = false;
                this._amuleSharedCache = null; // invalida cache → forza ricarica
                await this.amuleLoadShared(true);
            } else {
                this.showToast(d.error || 'Errore', 'error');
            }
        } catch(e) { this.showToast('Errore comunicazione', 'error'); }
    },

    async amuleRemoveSharedFolder(path) {
        if (!confirm(`Rimuovere "${path}" dalle cartelle condivise?`)) return;
        this.showToast('Rimozione in corso...', 'info');
        try {
            const r = await fetch(`${API_BASE}/api/amule/shared/remove`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path })
            });
            const d = await r.json();
            if (d.ok) {
                this.showToast('Cartella rimossa dalla condivisione', 'success');
                this._amuleSharedCache = null; // invalida cache
                await this.amuleLoadShared(true);
            } else {
                this.showToast(d.error || 'Errore durante la rimozione', 'error');
            }
        } catch(e) { this.showToast('Errore di comunicazione', 'error'); }
    },

    // ── UPLOAD ATTIVI ────────────────────────────────────────────────────────

    async amuleLoadUploads() {
        const container = document.getElementById('amule-uploads-rows');
        if (!container) return;
        try {
            const r       = await fetch(`${API_BASE}/api/amule/uploads`);
            if (!r.ok) throw new Error();
            const data    = await r.json();
            const uploads = data.uploads || [];
            const badge   = document.getElementById('amule-tab-badge-uploads');
            if (badge) badge.textContent = uploads.length || '';
            const summary = document.getElementById('amule-ul-summary');
            if (summary) {
                const totalUl = uploads.reduce((a, u) => a + (u.ul_speed||u.up_speed||0), 0);
                summary.textContent = uploads.length ? `${uploads.length} client · ↑ ${this._fmtRate(totalUl)}` : '';
            }
            if (!uploads.length) {
                container.innerHTML = `<div class="amule-empty"><i class="fa-solid fa-arrow-up"></i><span>Nessun upload attivo.</span></div>`;
                return;
            }
            container.innerHTML = uploads.map(u => {
                const speed = u.ul_speed || u.up_speed || 0;
                return `<div class="amule-row amule-ul-grid">
                    <div class="amule-name" title="${this._esc(u.file_name||u.name||'')}">${this._esc(u.file_name||u.name||'—')}</div>
                    <div class="amule-mono" style="color:var(--text-muted);">${this._esc(u.client_ip||u.user_name||'—')}</div>
                    <div class="amule-mono" style="color:${speed>0?'var(--warning)':'var(--text-muted)'};">${speed>0?this._fmtRate(speed):'—'}</div>
                    <div class="amule-mono" style="color:var(--text-muted);">${this._fmtBytes(u.upload_session||u.transferred||0)}</div>
                    <div class="amule-mono" style="color:var(--text-muted);font-size:0.75rem;">${this._esc(u.software||'—')}</div>
                </div>`;
            }).join('');
        } catch(e) {
            container.innerHTML = `<div class="amule-empty" style="color:var(--danger);"><i class="fa-solid fa-triangle-exclamation"></i><span>Impossibile caricare gli upload.</span></div>`;
        }
    },


    // --- GESTIONE SERVIZIO ARIA2 ---

    _aria2Log(msg, type = 'info') {
        const log = document.getElementById('aria2-maint-log');
        if (!log) return;
        log.style.display = 'block';
        const colors = { info: 'var(--text-secondary)', success: 'var(--success)', error: 'var(--danger)', warn: 'var(--warning)' };
        const icons  = { info: 'ℹ', success: '✔', error: '✖', warn: '⚠' };
        const ts = new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const line = document.createElement('div');
        line.style.color = colors[type] || colors.info;
        line.textContent = `[${ts}] ${icons[type] || 'ℹ'} ${msg}`;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
    },

    async aria2CheckServiceStatus() {
        try {
            const r = await fetch(`${API_BASE}/api/aria2/status-service`);
            const d = await r.json();
            const badge = document.getElementById('aria2-service-badge');
            if (badge) {
                if (d.active) {
                    badge.style.background = 'rgba(16,185,129,0.18)';
                    badge.style.color = 'var(--success)';
                    badge.innerHTML = `<i class="fa-solid fa-circle" style="font-size:0.55rem;"></i> Attivo`;
                    this._aria2Log('aria2c è in esecuzione.', 'success');
                } else {
                    badge.style.background = 'rgba(239,68,68,0.18)';
                    badge.style.color = 'var(--danger)';
                    badge.innerHTML = `<i class="fa-solid fa-circle-stop" style="font-size:0.55rem;"></i> Fermo`;
                    this._aria2Log('aria2c non è in esecuzione.', 'warn');
                }
            }
        } catch(e) {
            this._aria2Log(`Errore verifica stato: ${e.message}`, 'error');
            console.error("Errore stato aria2:", e);
        }
    },

    async aria2StartService() {
        this._aria2Log('Avvio aria2c in corso...', 'info');
        this.showToast("Avvio aria2c...", "info");
        try {
            const r = await fetch(`${API_BASE}/api/aria2/start`, { method: 'POST' });
            const d = await r.json();
            if (d.success) {
                this._aria2Log('aria2c avviato con successo. RPC in ascolto.', 'success');
                this.showToast("aria2c avviato!", "success");
                // Piccola attesa per dare tempo al processo di partire
                setTimeout(() => this.aria2CheckServiceStatus(), 1200);
            } else {
                const errMsg = d.error || 'errore sconosciuto';
                this._aria2Log(`Avvio fallito: ${errMsg}`, 'error');
                this.showToast("Errore: " + errMsg, "error");
            }
        } catch(e) {
            this._aria2Log(`Errore di rete: ${e.message}`, 'error');
            this.showToast("Errore di rete", "error");
        }
    },

    async aria2StopService() {
        if (!confirm("Vuoi davvero fermare aria2c?\nI download in corso verranno interrotti (riprenderanno al prossimo avvio).")) return;
        this._aria2Log('Invio segnale di stop ad aria2c...', 'warn');
        try {
            const r = await fetch(`${API_BASE}/api/aria2/stop`, { method: 'POST' });
            const d = await r.json();
            if (d.success !== false) {
                this._aria2Log('aria2c fermato.', 'warn');
                this.showToast("aria2c fermato", "info");
            } else {
                this._aria2Log(`Stop fallito: ${d.error || 'errore sconosciuto'}`, 'error');
            }
            setTimeout(() => this.aria2CheckServiceStatus(), 800);
        } catch(e) {
            this._aria2Log(`Errore di rete: ${e.message}`, 'error');
            this.showToast("Errore di rete", "error");
        }
    },

}; // chiude app object

// ========================================================================
// EVENT LISTENERS GLOBALI
// ========================================================================


// Chiudi cliccando fuori dalla finestra (sullo sfondo scuro)
// Usa e.target === e.currentTarget per assicurarsi che il click
// sia sul backdrop puro, non su un elemento figlio propagato
window.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal') && e.target === e.currentTarget) {
        // Non chiudere il dir-browser-modal se è aperto sopra
        const dirBrowser = document.getElementById('dir-browser-modal');
        if (dirBrowser && dirBrowser.classList.contains('active')) return;
        e.target.classList.remove('active');
        if (app.stopTorrentDetailsPoll) app.stopTorrentDetailsPoll();
    }
});

// Chiudi premendo il tasto ESC sulla tastiera
window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        // Se il dir-browser è aperto, ESC chiude quello per primo
        const dirBrowser = document.getElementById('dir-browser-modal');
        if (dirBrowser && dirBrowser.classList.contains('active')) {
            app.closeDirBrowser();
            return;
        }
        const activeModals = document.querySelectorAll('.modal.active');
        activeModals.forEach(modal => {
            modal.classList.remove('active');
        });
        if (app.stopTorrentDetailsPoll) app.stopTorrentDetailsPoll(); 
    }
});


document.addEventListener('DOMContentLoaded', () => app.init());

/* ══════════════════════════════════════════════════════════════════════
   MANUALE — funzioni globali
   ══════════════════════════════════════════════════════════════════════ */

function manualToggle(header) {
    const body = header.nextElementSibling;
    if (!body) return;
    const isOpen = body.classList.contains('open');
    document.querySelectorAll('.man-section-body').forEach(b => b.classList.remove('open'));
    document.querySelectorAll('.man-section-header').forEach(h => h.classList.remove('open'));
    if (!isOpen) {
        body.classList.add('open');
        header.classList.add('open');
    }
}

document.addEventListener('click', function(e) {
    const header = e.target.closest('.man-section-header');
    if (header) manualToggle(header);
});

function manualGoTo(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const header = el.querySelector('.man-section-header');
    const body   = el.querySelector('.man-section-body');
    if (header && body && !body.classList.contains('open')) {
        body.classList.add('open');
        header.classList.add('open');
    }
    setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

function manualSearch(query) {
    const q      = (query || '').trim().toLowerCase();
    const count  = document.getElementById('manual-search-count');
    const sections = document.querySelectorAll('.man-section');

    document.querySelectorAll('.man-highlight-text').forEach(el => {
        el.replaceWith(document.createTextNode(el.textContent));
    });

    if (!q) {
        count.textContent = '';
        sections.forEach(s => {
            showEl(s);
            s.classList.remove('man-highlight');
        });
        return;
    }

    let visible = 0;
    sections.forEach(sec => {
        const text  = sec.textContent.toLowerCase();
        const match = text.includes(q);
        showIf(sec, match);
        sec.classList.toggle('man-highlight', match);
        if (match) {
            visible++;
            const header = sec.querySelector('.man-section-header');
            const body   = sec.querySelector('.man-section-body');
            if (header && body && !body.classList.contains('open')) {
                body.classList.add('open');
                header.classList.add('open');
            }
            highlightInElement(sec, q);
        }
    });

    count.textContent = visible > 0
        ? `${visible} sezione${visible !== 1 ? 'i' : ''}`
        : 'Nessun risultato';
}

function highlightInElement(el, q) {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
    const nodes  = [];
    let node;
    while ((node = walker.nextNode())) nodes.push(node);

    nodes.forEach(textNode => {
        const parent = textNode.parentNode;
        if (!parent || parent.classList?.contains('man-highlight-text')) return;
        if (['SCRIPT', 'STYLE', 'CODE'].includes(parent.tagName)) return;

        const text  = textNode.textContent;
        const lower = text.toLowerCase();
        const idx   = lower.indexOf(q);
        if (idx === -1) return;

        const frag   = document.createDocumentFragment();
        let last     = 0;
        let searchFrom = 0;
        let found;
        while ((found = lower.indexOf(q, searchFrom)) !== -1) {
            if (found > last) frag.appendChild(document.createTextNode(text.slice(last, found)));
            const mark = document.createElement('mark');
            mark.className = 'man-highlight-text';
            mark.textContent = text.slice(found, found + q.length);
            frag.appendChild(mark);
            last = found + q.length;
            searchFrom = last;
        }
       if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
        parent.replaceChild(frag, textNode);
    });
} // chiude highlightInElement
// ASSICURATI CHE NON CI SIA UN'ALTRA } QUI SOTTO!
