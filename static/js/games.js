/**
 * EXTTO ARCADE — games.js  v5.1
 * Attivazione: triplo click sul logo Jira Tavernello in alto a sinistra
 * High score salvati in localStorage
 *
 * Giochi: Snake, Tetris, 2048, Tower Defence
 *
 * v5.0 — fix bug + nuove feature:
 *   - Fix: LS ora esposto come window._exttoLS (usato dagli oninput inline)
 *   - Fix: range torrette TD corretto (rimosso /100, default aggiornati)
 *   - Fix: commento TD duplicato rimosso
 *   - Fix: style.cssText doppio in legenda TD
 *   - Fix: 2048 game over con messaggio + rigioca
 *   - Nuovo: Snake velocità progressiva col punteggio
 *   - Nuovo: Tetris ghost piece (toggle in CFG) + preview pezzo successivo
 *   - Nuovo: TD tooltip torrette al hover (danno, range, rate, costo)
 *   - Nuovo: TD vendita torretta (click destro o tasto V)
 *   - Nuovo: TD UI rifatta — header stat bar, pannello torrette laterale, legenda integrata
 *   - Nuovo: TD 2 nuovi tipi torretta (Slow, AoE)
 *   - Nuovo: TD 2 nuovi tipi nemico (Regenerating, Shielded)
 */

;(function () {
'use strict';

// ── Storage ──────────────────────────────────────────────────────────────────
const LS = {
    get: k => { try { return JSON.parse(localStorage.getItem('extto_game_' + k)) } catch (e) { return null } },
    set: (k, v) => { try { localStorage.setItem('extto_game_' + k, JSON.stringify(v)) } catch (e) {} }
};
// Esponi LS globalmente per gli oninput inline nei pannelli CFG
window._exttoLS = LS;

// ── Trigger: triplo click sul logo ────────────────────────────────────────────
let _clickCount = 0, _clickTimer = null;

function initTrigger() {
    const candidates = [
        '.app-logo', '#app-logo', '.header-logo',
        '.nav-brand', '#nav-brand', 'header h1',
        '.sidebar-title', '#sidebar-title',
        '[class*="logo"]', '[id*="logo"]',
        'header .app-name', '.topbar-title'
    ];
    let target = null;
    for (const sel of candidates) {
        target = document.querySelector(sel);
        if (target) break;
    }
    if (!target) target = document.querySelector('header, .topbar, .navbar, nav');
    if (!target) {
        document.addEventListener('click', e => {
            if (e.clientY < 80 && e.clientX < 300) handleTripleClick();
        });
        return;
    }
    target.style.cursor = 'pointer';
    target.addEventListener('click', handleTripleClick);
}

function handleTripleClick() {
    _clickCount++;
    clearTimeout(_clickTimer);
    _clickTimer = setTimeout(() => { _clickCount = 0; }, 600);
    if (_clickCount >= 3) { _clickCount = 0; openArcade(); }
}

// ── CSS globale arcade ────────────────────────────────────────────────────────
function injectGlobalStyles() {
    if (document.getElementById('extto-arcade-style')) return;
    const style = document.createElement('style');
    style.id = 'extto-arcade-style';
    style.textContent = `
        #extto-arcade-overlay * { box-sizing: border-box; }
        .arcade-tab {
            padding: 5px 14px; font-size: 11px; border-radius: 4px;
            border: 1px solid #1e3a5f; background: transparent;
            color: #5a8ab0; cursor: pointer; font-family: monospace;
            transition: all 0.15s; letter-spacing: 0.5px;
        }
        .arcade-tab.active { background: #1e3a5f; color: #4a9eff; border-color: #4a9eff; }
        .arcade-tab:hover:not(.active) { background: #0f1e33; color: #7ab0d0; }
        .arcade-btn {
            padding: 7px 20px; background: #0d1e35; border: 1px solid #2a5a8f;
            color: #4a9eff; border-radius: 5px; cursor: pointer; font-family: monospace;
            font-size: 12px; transition: all 0.15s; letter-spacing: 0.3px;
        }
        .arcade-btn:hover { background: #1a3a5f; border-color: #4a9eff; }
        .arcade-btn:active { transform: scale(0.97); }
        .arcade-btn-green { border-color: #2adf8f; color: #2adf8f; }
        .arcade-btn-green:hover { background: #0a2f1f; border-color: #2adf8f; }
        .arcade-btn-red { border-color: #ff4a4a; color: #ff4a4a; }
        .arcade-btn-red:hover { background: #2f0a0a; }
        .arcade-btn-yellow { border-color: #ffdf4a; color: #ffdf4a; }
        .arcade-btn-yellow:hover { background: #2f2a0a; }
        .arcade-btn-sm { padding: 3px 10px; font-size: 11px; }
        .arcade-btn:disabled { opacity: 0.4; cursor: default; transform: none; }

        /* Stat badge */
        .arc-stat {
            display: flex; flex-direction: column; align-items: center;
            background: #060d18; border: 1px solid #1e3a5f; border-radius: 6px;
            padding: 5px 14px; min-width: 64px;
        }
        .arc-stat-label { font-size: 9px; color: #3a6a9a; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 2px; }
        .arc-stat-value { font-size: 16px; font-weight: 700; color: #4a9eff; font-variant-numeric: tabular-nums; }
        .arc-stat-value.green { color: #2adf8f; }
        .arc-stat-value.yellow { color: #ffdf4a; }
        .arc-stat-value.red { color: #ff4a4a; }

        /* Floating score pop */
        @keyframes scorePop {
            0%   { opacity:1; transform: translateY(0) scale(1); }
            100% { opacity:0; transform: translateY(-36px) scale(1.3); }
        }
        .score-pop {
            position: absolute; pointer-events: none; font-family: monospace;
            font-weight: 700; font-size: 13px; color: #2adf8f;
            animation: scorePop 0.7s ease-out forwards; z-index: 9;
        }

        /* Canvas flash overlay */
        @keyframes flashRed {
            0%,100% { opacity: 0; } 15%,45% { opacity: 1; }
        }
        .canvas-flash {
            position: absolute; inset: 0; background: rgba(255,50,50,0.18);
            pointer-events: none; border-radius: 2px;
            animation: flashRed 0.35s ease forwards;
        }

        /* TD Tooltip */
        #td-tower-tooltip {
            position: fixed; z-index: 99999; background: #060d18;
            border: 1px solid #2a5a8f; border-radius: 6px; padding: 8px 12px;
            font-family: monospace; font-size: 11px; color: #c0d8f0;
            pointer-events: none; display: none; min-width: 140px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.6);
        }
        #td-tower-tooltip .tt-title { font-size: 12px; font-weight: 700; margin-bottom: 5px; }
        #td-tower-tooltip .tt-row { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 2px; }
        #td-tower-tooltip .tt-key { color: #5a8ab0; }
        #td-tower-tooltip .tt-val { color: #4a9eff; font-weight: 600; }

        /* TD selected tower indicator */
        .td-tower-card {
            display: flex; flex-direction: column; align-items: center; gap: 3px;
            padding: 6px 10px; border-radius: 6px; border: 1px solid #1e3a5f;
            background: #060d18; cursor: pointer; transition: all 0.15s; min-width: 72px;
        }
        .td-tower-card:hover { border-color: #2a5a8f; background: #0a1525; }
        .td-tower-card.selected { border-color: #4a9eff; background: #0d1e35; }
        .td-tower-card .tc-icon { font-size: 18px; line-height: 1; }
        .td-tower-card .tc-name { font-size: 10px; color: #8ab0d0; letter-spacing: 0.5px; }
        .td-tower-card .tc-cost { font-size: 11px; color: #ffdf4a; font-weight: 600; }

        /* 2048 tile animation */
        @keyframes tileAppear {
            0%   { transform: scale(0.6); opacity: 0; }
            100% { transform: scale(1);   opacity: 1; }
        }
        .tile-new { animation: tileAppear 0.12s ease-out; }
    `;
    document.head.appendChild(style);
}

// ── Overlay principale ────────────────────────────────────────────────────────
let overlay = null, currentGame = null;

function openArcade() {
    if (overlay) { overlay.remove(); overlay = null; return; }
    injectGlobalStyles();

    overlay = document.createElement('div');
    overlay.id = 'extto-arcade-overlay';
    overlay.style.cssText = `
        position:fixed;inset:0;z-index:99999;
        background:rgba(2,6,14,0.97);
        display:flex;flex-direction:column;
        font-family:monospace;color:#c0d8f0;
    `;

    overlay.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;padding:9px 16px;background:#040c1a;border-bottom:1px solid #1a3050;flex-shrink:0">
            <span style="font-size:12px;font-weight:700;color:#4a9eff;letter-spacing:3px">// EXTTO ARCADE</span>
            <div id="arcade-tabs" style="display:flex;gap:3px;margin-left:16px">
                <button class="arcade-tab active" data-game="snake">SNAKE</button>
                <button class="arcade-tab" data-game="tetris">TETRIS</button>
                <button class="arcade-tab" data-game="2048">2048</button>
                <button class="arcade-tab" data-game="td">TOWER DEF</button>
            </div>
            <button id="arcade-close" class="arcade-btn arcade-btn-red arcade-btn-sm" style="margin-left:auto">✕ CHIUDI</button>
        </div>
        <div id="arcade-game" style="flex:1;display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:16px"></div>
    `;

    document.body.appendChild(overlay);
    document.getElementById('arcade-close').addEventListener('click', closeArcade);
    document.addEventListener('keydown', onEsc);
    document.getElementById('arcade-tabs').addEventListener('click', e => {
        const btn = e.target.closest('.arcade-tab');
        if (!btn) return;
        document.querySelectorAll('.arcade-tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        switchGame(btn.dataset.game);
    });

    switchGame('snake');
}

function closeArcade() {
    if (currentGame && currentGame.destroy) currentGame.destroy();
    currentGame = null;
    document.removeEventListener('keydown', onEsc);
    // Rimuovi tooltip TD se presente
    const tt = document.getElementById('td-tower-tooltip');
    if (tt) tt.remove();
    if (overlay) { overlay.remove(); overlay = null; }
}

function onEsc(e) { if (e.key === 'Escape') closeArcade(); }

function switchGame(name) {
    if (currentGame && currentGame.destroy) currentGame.destroy();
    currentGame = null;
    const tt = document.getElementById('td-tower-tooltip');
    if (tt) tt.remove();
    const arcadeGame = document.getElementById('arcade-game');
    if (!arcadeGame) return;
    arcadeGame.innerHTML = '';

    const wrap = document.createElement('div');

    if (name === 'td') {
        // TD: arcade-game diventa block, wrap prende 100% larghezza
        arcadeGame.style.display = 'block';
        arcadeGame.style.justifyContent = '';
        arcadeGame.style.alignItems = '';
        wrap.style.cssText = 'display:flex;flex-direction:column;align-items:stretch;gap:10px;width:100%';
        arcadeGame.appendChild(wrap);
        currentGame = new TowerDefence(wrap);
    } else {
        // Altri giochi: flex centrato come prima
        arcadeGame.style.display = 'flex';
        arcadeGame.style.justifyContent = 'center';
        arcadeGame.style.alignItems = 'flex-start';
        wrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;gap:10px;width:100%';
        arcadeGame.appendChild(wrap);
        if (name === 'snake')  currentGame = new SnakeGame(wrap);
        if (name === 'tetris') currentGame = new TetrisGame(wrap);
        if (name === '2048')   currentGame = new Game2048(wrap);
    }
}

// ── Utility: score pop ────────────────────────────────────────────────────────
function spawnScorePop(container, text, color = '#2adf8f') {
    const pop = document.createElement('div');
    pop.className = 'score-pop';
    pop.textContent = text;
    pop.style.color = color;
    pop.style.left = (Math.random() * 60 + 20) + '%';
    pop.style.top = '40%';
    container.style.position = 'relative';
    container.appendChild(pop);
    setTimeout(() => pop.remove(), 750);
}

// ═══════════════════════════════════════════════════════════════════════════
// SNAKE
// ═══════════════════════════════════════════════════════════════════════════
class SnakeGame {
    constructor(area) {
        this.area = area;
        this.hi = LS.get('snake_hi') || 0;
        this.running = false;
        this.interval = null;
        this.cfg = Object.assign({
            gridSize: 16,
            speed: 5,
        }, LS.get('sn_cfg') || {});
        this.CELL = Math.floor(352 / this.cfg.gridSize);
        this.COLS = this.cfg.gridSize;
        this.ROWS = this.cfg.gridSize;
        this.build();
    }
    build() {
        const W = this.COLS * this.CELL, H = this.ROWS * this.CELL;
        this.area.innerHTML = `
            <div style="display:flex;gap:8px;align-items:center">
                <div class="arc-stat"><div class="arc-stat-label">SCORE</div><div id="sn-score" class="arc-stat-value">0</div></div>
                <div class="arc-stat"><div class="arc-stat-label">HI</div><div id="sn-hi" class="arc-stat-value yellow">${this.hi}</div></div>
                <div class="arc-stat"><div class="arc-stat-label">LV</div><div id="sn-level" class="arc-stat-value green">1</div></div>
            </div>
            <div style="position:relative" id="sn-canvas-wrap">
                <canvas id="sn-canvas" width="${W}" height="${H}" style="border:1px solid #1e3a5f;display:block"></canvas>
                <div id="sn-overlay" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:rgba(4,8,16,0.93);border-radius:1px">
                    <div style="font-size:20px;color:#4a9eff;letter-spacing:2px;font-weight:700">SNAKE</div>
                    <div style="font-size:11px;color:#5a8ab0;text-align:center">Frecce / WASD &nbsp;·&nbsp; mangia i pacchetti<br><span style="color:#3a5a7a">La velocità aumenta col punteggio</span></div>
                    <button class="arcade-btn" id="sn-start">▶ INIZIA</button>
                </div>
            </div>
            <div style="display:flex;gap:6px;align-items:center">
                <button class="arcade-btn arcade-btn-sm" onclick="window._exttoSnake._toggleCfg()" style="border-color:#3a5a8a;color:#5a8ab0">⚙ CFG</button>
            </div>
            <div id="sn-cfg" style="display:none;background:#060d18;border:1px solid #1e3a5f;border-radius:6px;padding:10px;font-size:11px;width:360px;max-width:100%"></div>
        `;
        window._exttoSnake = this;
        document.getElementById('sn-start').addEventListener('click', () => this.start());
        this.ctx = document.getElementById('sn-canvas').getContext('2d');
        this.onKey = e => {
            const m = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0], KeyW: [0, -1], KeyS: [0, 1], KeyA: [-1, 0], KeyD: [1, 0] };
            if (m[e.code]) {
                e.preventDefault();
                const d = m[e.code];
                if (d[0] !== -this.dir[0] || d[1] !== -this.dir[1]) this.nextDir = d;
            }
        };
        document.addEventListener('keydown', this.onKey);
        this.drawGrid();
    }
    _speedMs() {
        // Velocità base da cfg, poi -4ms ogni 50 punti (cap a 80ms)
        const base = Math.round(270 - this.cfg.speed * 22);
        const bonus = Math.floor(this.score / 50) * 4;
        return Math.max(80, base - bonus);
    }
    _levelFromScore() { return Math.floor(this.score / 50) + 1; }
    start() {
        const newSize = this.cfg.gridSize;
        if (newSize !== this.COLS) {
            this.COLS = newSize; this.ROWS = newSize;
            this.CELL = Math.floor(352 / newSize);
            const cvs = document.getElementById('sn-canvas');
            if (cvs) { cvs.width = this.COLS * this.CELL; cvs.height = this.ROWS * this.CELL; }
        }
        const mid = Math.floor(this.COLS / 2);
        document.getElementById('sn-overlay').style.display = 'none';
        this.snake = [{ x: mid, y: mid }, { x: mid - 1, y: mid }, { x: mid - 2, y: mid }];
        this.dir = [1, 0]; this.nextDir = [1, 0];
        this.score = 0;
        this.running = true;
        this.spawnFood();
        clearInterval(this.interval);
        this.interval = setInterval(() => this.tick(), this._speedMs());
    }
    spawnFood() {
        do { this.food = { x: Math.floor(Math.random() * this.COLS), y: Math.floor(Math.random() * this.ROWS) }; }
        while (this.snake.some(s => s.x === this.food.x && s.y === this.food.y));
    }
    tick() {
        this.dir = this.nextDir;
        const head = { x: this.snake[0].x + this.dir[0], y: this.snake[0].y + this.dir[1] };
        if (head.x < 0 || head.x >= this.COLS || head.y < 0 || head.y >= this.ROWS || this.snake.some(s => s.x === head.x && s.y === head.y)) {
            this.gameOver(); return;
        }
        this.snake.unshift(head);
        const ate = head.x === this.food.x && head.y === this.food.y;
        if (ate) {
            this.score += 10;
            spawnScorePop(document.getElementById('sn-canvas-wrap'), '+10');
            this.spawnFood();
            // Ricalcola velocità ogni mangiata
            clearInterval(this.interval);
            this.interval = setInterval(() => this.tick(), this._speedMs());
        } else {
            this.snake.pop();
        }
        const lvl = this._levelFromScore();
        document.getElementById('sn-score').textContent = this.score;
        document.getElementById('sn-level').textContent = lvl;
        this.draw();
    }
    drawGrid() {
        const c = this.ctx, W = this.COLS * this.CELL, H = this.ROWS * this.CELL, CS = this.CELL;
        c.fillStyle = '#0a0f1a'; c.fillRect(0, 0, W, H);
        c.strokeStyle = '#0f1e33'; c.lineWidth = 0.5;
        for (let i = 0; i <= this.COLS; i++) { c.beginPath(); c.moveTo(i * CS, 0); c.lineTo(i * CS, H); c.stroke(); }
        for (let i = 0; i <= this.ROWS; i++) { c.beginPath(); c.moveTo(0, i * CS); c.lineTo(W, i * CS); c.stroke(); }
    }
    draw() {
        const c = this.ctx, CS = this.CELL;
        this.drawGrid();
        if (this.food) {
            c.fillStyle = '#4a9eff';
            c.beginPath(); c.roundRect(this.food.x * CS + 2, this.food.y * CS + 2, CS - 4, CS - 4, 3); c.fill();
            // Piccolo "glow"
            c.fillStyle = 'rgba(74,158,255,0.15)';
            c.fillRect(this.food.x * CS, this.food.y * CS, CS, CS);
        }
        this.snake.forEach((s, i) => {
            const t = i / this.snake.length;
            const g = Math.round(0xdf - t * 0x90), b = Math.round(0x8f - t * 0x60);
            c.fillStyle = i === 0 ? '#2adf8f' : `rgb(26,${g},${b})`;
            c.fillRect(s.x * CS + 1, s.y * CS + 1, CS - 2, CS - 2);
        });
    }
    gameOver() {
        clearInterval(this.interval);
        this.running = false;
        if (this.score > this.hi) { this.hi = this.score; LS.set('snake_hi', this.hi); document.getElementById('sn-hi').textContent = this.hi; }
        const ov = document.getElementById('sn-overlay');
        ov.style.display = 'flex';
        ov.innerHTML = `
            <div style="font-size:20px;color:#ff4a4a;font-weight:700">GAME OVER</div>
            <div style="font-size:12px;color:#5a8ab0">Score: ${this.score} &nbsp;·&nbsp; Livello: ${this._levelFromScore()}</div>
            <button class="arcade-btn" id="sn-start">↺ RIGIOCA</button>
        `;
        document.getElementById('sn-start').addEventListener('click', () => this.start());
    }
    _toggleCfg() {
        const p = document.getElementById('sn-cfg');
        if (!p) return;
        if (p.style.display !== 'none') { p.style.display = 'none'; return; }
        const c = this.cfg;
        const row = (key, label, min, max, step = 1) => `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <label style="width:140px;color:#5a8ab0;flex-shrink:0">${label}</label>
                <input type="range" min="${min}" max="${max}" step="${step}" value="${c[key]}" style="flex:1"
                    oninput="this.nextElementSibling.textContent=this.value;window._exttoSnake.cfg['${key}']=+this.value;window._exttoLS.set('sn_cfg',window._exttoSnake.cfg)">
                <span style="width:36px;text-align:right;color:#4a9eff">${c[key]}</span>
            </div>`;
        p.innerHTML = `
            <div style="color:#4a9eff;font-weight:700;margin-bottom:10px;font-size:12px">⚙ SNAKE CONFIG</div>
            ${row('gridSize', 'Dimensione griglia', 12, 24, 2)}
            ${row('speed', 'Velocità base (1=lento)', 1, 10)}
            <div style="font-size:10px;color:#2a5a7a;margin-top:4px">La velocità aumenta automaticamente ogni 50 punti. Riavvia per applicare le modifiche.</div>
        `;
        p.style.display = 'block';
    }
    destroy() { clearInterval(this.interval); document.removeEventListener('keydown', this.onKey); }
}

// ═══════════════════════════════════════════════════════════════════════════
// TETRIS
// ═══════════════════════════════════════════════════════════════════════════
class TetrisGame {
    constructor(area) {
        this.area = area;
        this.hi = LS.get('tetris_hi') || 0;
        this.cfg = Object.assign({
            cols: 10,
            speedStart: 500,
            ghostPiece: true,
        }, LS.get('tt_cfg') || {});
        this.COLS = this.cfg.cols;
        this.ROWS = 20;
        this.CELL = Math.min(22, Math.floor(280 / this.COLS));
        this.PIECES = [
            { s: [[1, 1, 1, 1]], c: '#4a9eff' },
            { s: [[1, 1], [1, 1]], c: '#2adf8f' },
            { s: [[1, 1, 1], [0, 1, 0]], c: '#ff9f4a' },
            { s: [[1, 1, 1], [1, 0, 0]], c: '#ff4a4a' },
            { s: [[1, 1, 1], [0, 0, 1]], c: '#c04aff' },
            { s: [[1, 1, 0], [0, 1, 1]], c: '#4affdf' },
            { s: [[0, 1, 1], [1, 1, 0]], c: '#ffdf4a' }
        ];
        this.bag = [];
        this.build();
    }
    _nextFromBag() {
        if (this.bag.length < 2) {
            const arr = [...this.PIECES].sort(() => Math.random() - 0.5);
            this.bag.push(...arr);
        }
        return this.bag.shift();
    }
    build() {
        const W = this.COLS * this.CELL, H = this.ROWS * this.CELL;
        const PW = 4 * this.CELL; // preview canvas width
        this.area.innerHTML = `
            <div style="display:flex;gap:8px;align-items:center">
                <div class="arc-stat"><div class="arc-stat-label">SCORE</div><div id="tt-score" class="arc-stat-value">0</div></div>
                <div class="arc-stat"><div class="arc-stat-label">RIGHE</div><div id="tt-lines" class="arc-stat-value green">0</div></div>
                <div class="arc-stat"><div class="arc-stat-label">HI</div><div id="tt-hi" class="arc-stat-value yellow">${this.hi}</div></div>
            </div>
            <div style="display:flex;gap:12px;align-items:flex-start">
                <div style="position:relative">
                    <canvas id="tt-canvas" width="${W}" height="${H}" style="border:1px solid #1e3a5f;display:block"></canvas>
                    <div id="tt-overlay" style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;background:rgba(4,8,16,0.93)">
                        <div style="font-size:20px;color:#4a9eff;font-weight:700;letter-spacing:2px">TETRIS</div>
                        <div style="font-size:11px;color:#5a8ab0;text-align:center">← → muovi &nbsp;·&nbsp; ↑ ruota &nbsp;·&nbsp; ↓ scendi<br>Spazio: hard drop</div>
                        <button class="arcade-btn" id="tt-start">▶ INIZIA</button>
                    </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:10px">
                    <div style="background:#060d18;border:1px solid #1e3a5f;border-radius:6px;padding:8px;text-align:center">
                        <div style="font-size:9px;color:#3a6a9a;letter-spacing:1px;margin-bottom:6px">PROSSIMO</div>
                        <canvas id="tt-preview" width="${PW}" height="${PW}" style="display:block"></canvas>
                    </div>
                    <div style="display:flex;flex-direction:column;gap:5px">
                        <button class="arcade-btn arcade-btn-sm" id="tt-cfg-btn" style="border-color:#3a5a8a;color:#5a8ab0">⚙ CFG</button>
                    </div>
                </div>
            </div>
            <div id="tt-cfg" style="display:none;background:#060d18;border:1px solid #1e3a5f;border-radius:6px;padding:10px;font-size:11px;width:360px;max-width:100%"></div>
        `;
        window._exttoTetris = this;
        document.getElementById('tt-start').addEventListener('click', () => this.start());
        document.getElementById('tt-cfg-btn').addEventListener('click', () => this._toggleCfg());
        this.ctx = document.getElementById('tt-canvas').getContext('2d');
        this.pctx = document.getElementById('tt-preview').getContext('2d');
        this.onKey = e => {
            if (!this.running) return;
            if (e.code === 'ArrowLeft')  { e.preventDefault(); this.move(-1); }
            if (e.code === 'ArrowRight') { e.preventDefault(); this.move(1); }
            if (e.code === 'ArrowUp')    { e.preventDefault(); this.rotate(); }
            if (e.code === 'ArrowDown')  { e.preventDefault(); this.drop(); }
            if (e.code === 'Space')      { e.preventDefault(); this.hardDrop(); }
        };
        document.addEventListener('keydown', this.onKey);
        this.drawEmpty();
    }
    start() {
        const newCols = this.cfg.cols;
        if (newCols !== this.COLS) {
            this.COLS = newCols;
            this.CELL = Math.min(22, Math.floor(280 / this.COLS));
            const cvs = document.getElementById('tt-canvas');
            if (cvs) { cvs.width = this.COLS * this.CELL; cvs.height = this.ROWS * this.CELL; }
        }
        document.getElementById('tt-overlay').style.display = 'none';
        this.board = Array.from({ length: this.ROWS }, () => Array(this.COLS).fill(null));
        this.score = 0; this.lines = 0; this.running = true;
        this.bag = [];
        this.nextPiece = this._nextFromBag();
        this.spawnPiece();
        clearInterval(this.interval);
        this.interval = setInterval(() => this.drop(), this.cfg.speedStart);
    }
    spawnPiece() {
        const p = this.nextPiece || this._nextFromBag();
        this.piece = { shape: p.s.map(r => [...r]), color: p.c, x: 3, y: 0 };
        this.nextPiece = this._nextFromBag();
        this.drawPreview();
        if (!this.fits(this.piece.shape, this.piece.x, this.piece.y)) this.gameOver();
    }
    drawPreview() {
        const c = this.pctx, CS = this.CELL, PW = 4 * CS;
        c.fillStyle = '#060d18'; c.fillRect(0, 0, PW, PW);
        if (!this.nextPiece) return;
        const s = this.nextPiece.s;
        const ox = Math.floor((4 - s[0].length) / 2) * CS;
        const oy = Math.floor((4 - s.length) / 2) * CS;
        c.fillStyle = this.nextPiece.c;
        s.forEach((row, r) => row.forEach((v, cc) => {
            if (v) c.fillRect(ox + cc * CS + 1, oy + r * CS + 1, CS - 2, CS - 2);
        }));
    }
    fits(shape, px, py) {
        for (let r = 0; r < shape.length; r++) for (let c = 0; c < shape[r].length; c++) {
            if (!shape[r][c]) continue;
            const nx = px + c, ny = py + r;
            if (nx < 0 || nx >= this.COLS || ny >= this.ROWS) return false;
            if (ny >= 0 && this.board[ny][nx]) return false;
        }
        return true;
    }
    _ghostY() {
        let gy = this.piece.y;
        while (this.fits(this.piece.shape, this.piece.x, gy + 1)) gy++;
        return gy;
    }
    move(dx) { if (this.fits(this.piece.shape, this.piece.x + dx, this.piece.y)) { this.piece.x += dx; this.draw(); } }
    rotate() {
        const s = this.piece.shape;
        const rs = s[0].map((_, i) => s.map(r => r[i]).reverse());
        if (this.fits(rs, this.piece.x, this.piece.y)) { this.piece.shape = rs; this.draw(); }
    }
    drop() {
        if (this.fits(this.piece.shape, this.piece.x, this.piece.y + 1)) { this.piece.y++; this.draw(); }
        else this.lock();
    }
    hardDrop() { while (this.fits(this.piece.shape, this.piece.x, this.piece.y + 1)) this.piece.y++; this.lock(); }
    lock() {
        this.piece.shape.forEach((row, r) => row.forEach((v, c) => {
            if (v && this.piece.y + r >= 0) this.board[this.piece.y + r][this.piece.x + c] = this.piece.color;
        }));
        let cleared = 0;
        for (let r = this.ROWS - 1; r >= 0; r--) {
            if (this.board[r].every(c => c)) { this.board.splice(r, 1); this.board.unshift(Array(this.COLS).fill(null)); cleared++; r++; }
        }
        if (cleared) {
            this.lines += cleared;
            const pts = cleared * 100 * cleared;
            this.score += pts;
            spawnScorePop(document.querySelector('#tt-canvas').parentElement, `+${pts}`);
        }
        document.getElementById('tt-score').textContent = this.score;
        document.getElementById('tt-lines').textContent = this.lines;
        clearInterval(this.interval);
        this.interval = setInterval(() => this.drop(), Math.max(80, this.cfg.speedStart - this.lines * 15));
        this.spawnPiece();
    }
    drawEmpty() {
        const c = this.ctx, W = this.COLS * this.CELL, H = this.ROWS * this.CELL, CS = this.CELL;
        c.fillStyle = '#0a0f1a'; c.fillRect(0, 0, W, H);
        c.strokeStyle = '#0f1e33'; c.lineWidth = 0.5;
        for (let i = 0; i <= this.COLS; i++) { c.beginPath(); c.moveTo(i * CS, 0); c.lineTo(i * CS, H); c.stroke(); }
        for (let i = 0; i <= this.ROWS; i++) { c.beginPath(); c.moveTo(0, i * CS); c.lineTo(W, i * CS); c.stroke(); }
    }
    draw() {
        const c = this.ctx, CS = this.CELL;
        this.drawEmpty();
        this.board.forEach((row, r) => row.forEach((col, cc) => {
            if (col) { c.fillStyle = col; c.fillRect(cc * CS + 1, r * CS + 1, CS - 2, CS - 2); }
        }));
        if (this.piece) {
            // Ghost piece
            if (this.cfg.ghostPiece) {
                const gy = this._ghostY();
                c.fillStyle = 'rgba(74,158,255,0.18)';
                this.piece.shape.forEach((row, r) => row.forEach((v, cc) => {
                    if (v) c.fillRect((this.piece.x + cc) * CS + 1, (gy + r) * CS + 1, CS - 2, CS - 2);
                }));
            }
            // Pezzo corrente
            c.fillStyle = this.piece.color;
            this.piece.shape.forEach((row, r) => row.forEach((v, cc) => {
                if (v) c.fillRect((this.piece.x + cc) * CS + 1, (this.piece.y + r) * CS + 1, CS - 2, CS - 2);
            }));
        }
    }
    gameOver() {
        clearInterval(this.interval); this.running = false;
        if (this.score > this.hi) { this.hi = this.score; LS.set('tetris_hi', this.hi); document.getElementById('tt-hi').textContent = this.hi; }
        const ov = document.getElementById('tt-overlay');
        ov.style.display = 'flex';
        ov.innerHTML = `
            <div style="font-size:20px;color:#ff4a4a;font-weight:700">GAME OVER</div>
            <div style="font-size:12px;color:#5a8ab0">Score: ${this.score} &nbsp;·&nbsp; Righe: ${this.lines}</div>
            <button class="arcade-btn" id="tt-start">↺ RIGIOCA</button>
        `;
        document.getElementById('tt-start').addEventListener('click', () => this.start());
    }
    _toggleCfg() {
        const p = document.getElementById('tt-cfg');
        if (!p) return;
        if (p.style.display !== 'none') { p.style.display = 'none'; return; }
        const c = this.cfg;
        const row = (key, label, min, max, step = 1) => `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <label style="width:180px;color:#5a8ab0;flex-shrink:0">${label}</label>
                <input type="range" min="${min}" max="${max}" step="${step}" value="${c[key]}" style="flex:1"
                    oninput="this.nextElementSibling.textContent=this.value;window._exttoTetris.cfg['${key}']=+this.value;window._exttoLS.set('tt_cfg',window._exttoTetris.cfg)">
                <span style="width:36px;text-align:right;color:#4a9eff">${c[key]}</span>
            </div>`;
        const checkbox = (key, label) => `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <label style="color:#5a8ab0;flex:1">${label}</label>
                <input type="checkbox" ${c[key] ? 'checked' : ''} style="width:16px;height:16px;cursor:pointer"
                    onchange="window._exttoTetris.cfg['${key}']=this.checked;window._exttoLS.set('tt_cfg',window._exttoTetris.cfg);window._exttoTetris.draw()">
            </div>`;
        p.innerHTML = `
            <div style="color:#4a9eff;font-weight:700;margin-bottom:10px;font-size:12px">⚙ TETRIS CONFIG</div>
            ${row('cols', 'Larghezza campo', 8, 14)}
            ${row('speedStart', 'Velocità iniziale (ms↓)', 200, 800, 50)}
            ${checkbox('ghostPiece', 'Ghost piece (proiezione)')}
            <div style="font-size:10px;color:#2a5a7a;margin-top:4px">Riavvia la partita per applicare cols e velocità.</div>
        `;
        p.style.display = 'block';
    }
    destroy() { clearInterval(this.interval); document.removeEventListener('keydown', this.onKey); }
}

// ═══════════════════════════════════════════════════════════════════════════
// 2048
// ═══════════════════════════════════════════════════════════════════════════
class Game2048 {
    constructor(area) {
        this.area = area;
        this.hi = LS.get('2048_hi') || 0;
        this.build();
    }
    build() {
        this.area.innerHTML = `
            <div style="display:flex;gap:8px;align-items:center">
                <div class="arc-stat"><div class="arc-stat-label">SCORE</div><div id="g2-score" class="arc-stat-value">0</div></div>
                <div class="arc-stat"><div class="arc-stat-label">HI</div><div id="g2-hi" class="arc-stat-value yellow">${this.hi}</div></div>
                <button class="arcade-btn arcade-btn-sm" id="g2-new" style="margin-left:8px">↺ NUOVO</button>
            </div>
            <div id="grid-2048" style="display:grid;grid-template-columns:repeat(4,76px);gap:7px;padding:10px;background:#1a1410;border-radius:8px;border:1px solid #2a2018"></div>
            <div style="font-size:11px;color:#2a5a7a">Frecce per unire i blocchi — raggiungi 2048!</div>
        `;
        const COLORS = {
            0: 'background:#2d241e;color:transparent',
            2: 'background:#eee4da;color:#776e65',
            4: 'background:#ede0c8;color:#776e65',
            8: 'background:#f2b179;color:#f9f6f2',
            16: 'background:#f59563;color:#f9f6f2',
            32: 'background:#f67c5f;color:#f9f6f2',
            64: 'background:#f65e3b;color:#f9f6f2',
            128: 'background:#edcf72;color:#f9f6f2',
            256: 'background:#edcc61;color:#f9f6f2',
            512: 'background:#edc850;color:#f9f6f2',
            1024: 'background:#edc53f;color:#f9f6f2',
            2048: 'background:#edc22e;color:#f9f6f2',
        };
        this.COLORS = COLORS;
        this.score = 0;
        this.board = Array.from({ length: 4 }, () => Array(4).fill(0));
        this.prevBoard = null;
        this.addTile(); this.addTile();
        this.onKey = e => {
            const m = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };
            if (m[e.code]) { e.preventDefault(); this.move(m[e.code]); }
        };
        document.addEventListener('keydown', this.onKey);
        document.getElementById('g2-new').addEventListener('click', () => this._reset());
        this.render();
    }
    _reset() {
        this.score = 0; this.board = Array.from({ length: 4 }, () => Array(4).fill(0));
        this.addTile(); this.addTile(); this.render();
        document.getElementById('g2-score').textContent = 0;
        // Rimuovi eventuale game over overlay
        const ov = document.getElementById('g2-overlay');
        if (ov) ov.remove();
    }
    addTile(isNew = false) {
        const empty = [];
        for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) if (!this.board[r][c]) empty.push([r, c]);
        if (!empty.length) return false;
        const [r, c] = empty[Math.floor(Math.random() * empty.length)];
        this.board[r][c] = Math.random() < 0.9 ? 2 : 4;
        return true;
    }
    move(dir) {
        const prev = JSON.stringify(this.board);
        const rot = b => b[0].map((_, i) => b.map(r => r[i]).reverse());
        const slideLeft = row => {
            let r = row.filter(v => v);
            for (let i = 0; i < r.length - 1; i++) if (r[i] === r[i + 1]) { this.score += r[i] * 2; r[i] *= 2; r.splice(i + 1, 1); }
            while (r.length < 4) r.push(0); return r;
        };
        let b = this.board;
        if (dir === 'up')    { b = rot(rot(rot(b))); b = b.map(slideLeft); b = rot(b); }
        else if (dir === 'down')  { b = rot(b); b = b.map(slideLeft); b = rot(rot(rot(b))); }
        else if (dir === 'left') { b = b.map(slideLeft); }
        else { b = b.map(r => [...slideLeft([...r].reverse())].reverse()); }
        this.board = b;
        const changed = JSON.stringify(this.board) !== prev;
        if (changed) this.addTile(true);
        if (this.score > this.hi) { this.hi = this.score; LS.set('2048_hi', this.hi); document.getElementById('g2-hi').textContent = this.hi; }
        document.getElementById('g2-score').textContent = this.score;
        this.render();
        if (!changed) return;
        // Controlla game over
        if (this._isGameOver()) this._showGameOver();
    }
    _isGameOver() {
        for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
            if (!this.board[r][c]) return false;
            if (c < 3 && this.board[r][c] === this.board[r][c + 1]) return false;
            if (r < 3 && this.board[r][c] === this.board[r + 1][c]) return false;
        }
        return true;
    }
    _showGameOver() {
        const container = document.getElementById('grid-2048').parentElement;
        const existing = document.getElementById('g2-overlay');
        if (existing) return;
        const ov = document.createElement('div');
        ov.id = 'g2-overlay';
        ov.style.cssText = 'background:#060d18;border:1px solid #2a3a5a;border-radius:8px;padding:24px 32px;text-align:center;margin-top:8px';
        ov.innerHTML = `
            <div style="font-size:18px;color:#ff4a4a;font-weight:700;margin-bottom:8px">GAME OVER</div>
            <div style="font-size:12px;color:#5a8ab0;margin-bottom:12px">Nessuna mossa possibile &nbsp;·&nbsp; Score: ${this.score}</div>
            <button class="arcade-btn" id="g2-restart">↺ RIGIOCA</button>
        `;
        container.appendChild(ov);
        document.getElementById('g2-restart').addEventListener('click', () => this._reset());
    }
    render() {
        const g = document.getElementById('grid-2048');
        if (!g) return;
        const C = this.COLORS;
        g.innerHTML = this.board.flat().map(v => {
            const s = C[v] || 'background:#3d3320;color:#ffdf4a';
            const fs = v >= 1024 ? '13px' : v >= 128 ? '16px' : '19px';
            return `<div style="${s};width:76px;height:76px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:${fs};font-weight:700;transition:background 0.08s">${v || ''}</div>`;
        }).join('');
    }
    destroy() { document.removeEventListener('keydown', this.onKey); }
}

/**
 * td_maps.js — Sistema mappe Tower Defence v1.0
 *
 * Architettura ispirata a BTD6, Kingdom Rush, PixelJunk Monsters.
 *
 * Ogni mappa è definita da:
 *   - layout:  funzione generatrice del grafo di celle percorribili
 *   - theme:   palette colori + decorazioni ambient + effetti suolo
 *   - paths:   1 o 2 percorsi principali (lane), eventuale loop secondario
 *   - checkpoints: celle speciali che danneggiano i nemici passanti
 *   - deco:    celle decorative (alberi, rocce, cristalli, fiamme)
 *
 * Interfaccia pubblica:
 *   TDMapSystem.generate(COLS, ROWS, wave) → MapResult
 *
 * MapResult {
 *   path       [c,r][]      percorso principale
 *   path2      [c,r][]|null secondo percorso (lane B)
 *   pathSet    Set<string>  tutte le celle percorribili (entrambe le lane)
 *   altPaths   [c,r][][]   segmenti decorativi altPath
 *   checkpoints Set<string> celle fortino
 *   deco       DecoTile[]   {c,r,type,variant}
 *   theme      Theme        palette e rendering hints
 *   label      string       nome mappa
 *   dual       boolean      true se 2 lane attive
 * }
 */

const TDMapSystem = (() => {
'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// TEMI VISIVI
// ─────────────────────────────────────────────────────────────────────────────
const THEMES = {
    forest: {
        name: '🌲 Foresta',
        ground:     '#3a7a3a',
        groundDark: '#2d5e2d',
        path:       [[195,170,100],[165,140,70]],   // [start rgb, end rgb]
        pathEdge:   'rgba(0,0,0,0.45)',
        alt:        '#6a7a50',
        deco: {
            tree:   { chance: 0.18, colors: ['#1a5a1a','#2a7a2a','#0f3f0f'], size: 0.7 },
            rock:   { chance: 0.05, colors: ['#6a6a5a','#7a7a6a'], size: 0.5 },
        },
        arrowColor: 'rgba(60,30,0,0.55)',
        ambientFx:  null,
    },
    volcano: {
        name: '🌋 Vulcano',
        ground:     '#4a2a1a',
        groundDark: '#3a1a0a',
        path:       [[140,90,60],[110,60,30]],
        pathEdge:   'rgba(255,80,0,0.3)',
        alt:        '#5a3a2a',
        deco: {
            rock:    { chance: 0.12, colors: ['#3a2010','#2a1008'], size: 0.6 },
            flame:   { chance: 0.06, colors: ['#ff6a00','#ff3300'], size: 0.45 },
        },
        arrowColor: 'rgba(200,80,0,0.5)',
        ambientFx:  'lava',
    },
    snow: {
        name: '❄️ Tundra',
        ground:     '#c8dcea',
        groundDark: '#a8c0d0',
        path:       [[200,210,220],[175,185,200]],
        pathEdge:   'rgba(100,140,180,0.4)',
        alt:        '#b0c8da',
        deco: {
            rock:  { chance: 0.08, colors: ['#8a9aaa','#6a7a8a'], size: 0.55 },
            pine:  { chance: 0.14, colors: ['#2a5a2a','#1a3a1a'], size: 0.65 },
        },
        arrowColor: 'rgba(60,80,120,0.5)',
        ambientFx:  'snow',
    },
    desert: {
        name: '🏜️ Deserto',
        ground:     '#c8a850',
        groundDark: '#a88830',
        path:       [[195,160,80],[175,140,60]],
        pathEdge:   'rgba(100,60,0,0.4)',
        alt:        '#b89840',
        deco: {
            cactus: { chance: 0.10, colors: ['#3a7a2a','#2a5a1a'], size: 0.6 },
            rock:   { chance: 0.08, colors: ['#a07040','#806030'], size: 0.5 },
        },
        arrowColor: 'rgba(100,60,0,0.5)',
        ambientFx:  'heat',
    },
    night: {
        name: '🌙 Notte',
        ground:     '#0f1a2a',
        groundDark: '#090f1a',
        path:       [[40,55,80],[30,45,65]],
        pathEdge:   'rgba(100,180,255,0.2)',
        alt:        '#1a2a3a',
        deco: {
            crystal: { chance: 0.10, colors: ['#2060a0','#4080c0'], size: 0.5 },
            rock:    { chance: 0.07, colors: ['#1a2a1a','#0f1a0f'], size: 0.55 },
        },
        arrowColor: 'rgba(80,160,255,0.45)',
        ambientFx:  'stars',
    },
    neon: {
        name: '⚡ Cyber',
        ground:     '#050a14',
        groundDark: '#030609',
        path:       [[20,40,70],[15,30,55]],
        pathEdge:   'rgba(0,220,255,0.35)',
        alt:        '#0a1020',
        deco: {
            crystal: { chance: 0.12, colors: ['#00aaff','#ff00aa'], size: 0.45 },
            flame:   { chance: 0.04, colors: ['#00ffaa','#00aaff'], size: 0.4 },
        },
        arrowColor: 'rgba(0,220,255,0.5)',
        ambientFx:  'grid',
    },
};

const THEME_KEYS = Object.keys(THEMES);

// ─────────────────────────────────────────────────────────────────────────────
// UTILITY
// ─────────────────────────────────────────────────────────────────────────────
const key   = (c, r)  => `${c},${r}`;
const rand  = (a, b)  => Math.floor(Math.random() * (b - a + 1)) + a;
const coinf = (p)     => Math.random() < p;

function shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
}

/** BFS distanza da src su un insieme di celle */
function bfsDist(src, cells) {
    const dist = new Map();
    dist.set(key(...src), 0);
    const q = [src];
    const dirs = [[1,0],[-1,0],[0,1],[0,-1]];
    for (let i = 0; i < q.length; i++) {
        const [cc, cr] = q[i], d = dist.get(key(cc, cr));
        for (const [dc, dr] of dirs) {
            const nc = cc+dc, nr = cr+dr, k = key(nc, nr);
            if (cells.has(k) && !dist.has(k)) { dist.set(k, d+1); q.push([nc, nr]); }
        }
    }
    return dist;
}

/** 
 * Trova il percorso PIÙ LUNGO possibile da entry a exit.
 * Strategia: DFS random con backtracking che esplora avidamente
 * le celle più lontane dall'uscita prima, garantendo percorsi lunghi.
 */
function greedyPath(entry, exit, cells) {
    const exitKey = key(...exit);
    const dirs = [[1,0],[-1,0],[0,1],[0,-1]];

    // Fase 1: BFS da exit per conoscere la distanza di ogni cella dall'uscita
    const distFromExit = bfsDist(exit, cells);
    if (!distFromExit.has(key(...entry))) return null;

    // Fase 2: DFS iterativo con backtracking
    // Sceglie sempre il vicino con distanza MAGGIORE dall'uscita (= si allontana prima)
    // con un po' di randomizzazione per varietà
    const path = [entry];
    const visited = new Set([key(...entry)]);
    let cur = entry;

    const maxSteps = cells.size * 4;
    for (let step = 0; step < maxSteps; step++) {
        if (cur[0] === exit[0] && cur[1] === exit[1]) break;

        // Trova vicini validi ordinati per distanza decrescente dall'exit
        const neighbors = [];
        for (const [dc, dr] of dirs) {
            const nc = cur[0]+dc, nr = cur[1]+dr, k = key(nc, nr);
            if (cells.has(k) && !visited.has(k)) {
                neighbors.push({ pos: [nc,nr], d: distFromExit.get(k) ?? 0 });
            }
        }

        if (neighbors.length === 0) {
            // Dead end: backtrack
            if (path.length <= 1) return null;
            visited.delete(key(...path.pop()));
            cur = path[path.length - 1];
            continue;
        }

        // Ordina: maggiore distanza prima, con shuffle random per i pari
        neighbors.sort((a,b) => {
            const dd = b.d - a.d;
            return dd !== 0 ? dd : (Math.random() - 0.5);
        });

        // Scegli il primo (più lontano), ma con 15% di probabilità scegli il secondo
        // per evitare di rimanere bloccati in pattern ripetitivi
        const pick = (neighbors.length > 1 && Math.random() < 0.15) ? neighbors[1] : neighbors[0];
        visited.add(key(...pick.pos));
        path.push(pick.pos);
        cur = pick.pos;
    }

    if (cur[0] !== exit[0] || cur[1] !== exit[1]) {
        // Non ha raggiunto l'uscita: prova il percorso più breve come fallback
        function bfsPath(src, dst) {
            const prev = new Map();
            const q = [src];
            prev.set(key(...src), null);
            while (q.length) {
                const c2 = q.shift();
                if (c2[0] === dst[0] && c2[1] === dst[1]) break;
                for (const [dc,dr] of dirs) {
                    const nc=c2[0]+dc, nr=c2[1]+dr, k=key(nc,nr);
                    if (cells.has(k) && !prev.has(k)) { prev.set(k,[...c2]); q.push([nc,nr]); }
                }
            }
            if (!prev.has(key(...dst))) return null;
            const p = [];
            let c2 = [...dst];
            while (c2) { p.unshift(c2); c2 = prev.get(key(...c2)); }
            return p;
        }
        const fallback = bfsPath(entry, exit);
        if (!fallback) return null;
        return fallback.length > 3 ? fallback : null;
    }

    return path.length > 3 ? path : null;
}

/** Spezza un set di celle in segmenti connessi di lunghezza ≥ minLen */
function segments(cellKeys, minLen = 3) {
    const remaining = new Set(cellKeys);
    const result = [];
    while (remaining.size > 0) {
        const start = [...remaining][0];
        const [sc, sr] = start.split(',').map(Number);
        const seg = [], q = [[sc, sr]], seen = new Set([start]);
        while (q.length) {
            const [cc, cr] = q.shift(); seg.push([cc, cr]);
            for (const [dc, dr] of [[1,0],[-1,0],[0,1],[0,-1]]) {
                const k = key(cc+dc, cr+dr);
                if (remaining.has(k) && !seen.has(k)) { seen.add(k); q.push([cc+dc, cr+dr]); }
            }
        }
        seg.forEach(([c,r]) => remaining.delete(key(c,r)));
        if (seg.length >= minLen) result.push(seg);
    }
    return result;
}

// ─────────────────────────────────────────────────────────────────────────────
// GENERATORE ISKALA — path organici casuali + selezione per valutazione
// Ispirato a: "Procedural generation of levels for a tower defense game"
//             Ida Iskala, Aalto University 2024
//
// Algoritmo:
//   1. Genera N candidati di path crescendo casualmente (pezzi + svolta)
//   2. Valuta ogni path con 3 funzioni: densità locale, densità globale, forma
//   3. Restituisce il path con score più vicino agli obiettivi di difficoltà
//
// Stili di difficoltà (wave crescenti):
//   EASY   → path denso, molte svolte (torre copre più nemici)
//   MEDIUM → bilanciato
//   HARD   → path lungo e rettilineo (torrette meno efficaci)
// ─────────────────────────────────────────────────────────────────────────────

// ── Stili path (da Iskala Table 1 / Figure 16) ───────────────────────────────
// Ogni stile definisce goals per f_tower, f_area, f_shape e p_turn
// f_tower ∈ [0,1]: densità locale (alto = denso = facile)
// f_area  ∈ [0,1]: copertura bounding box (alto = compatto)
// f_shape ∈ [0,1]: quanto è quadrato il BB (alto = quadrato = denso)
// p_turn  ∈ [0,1]: probabilità di svolta ad ogni passo
const PATH_STYLES = [
    { id:'dense',    label:'Denso',     gTower:0.22, gArea:0.30, gShape:0.85, pTurn:0.55, minPiece:2, maxPiece:5  },
    { id:'balanced', label:'Bilanciato',gTower:0.19, gArea:0.22, gShape:0.65, pTurn:0.42, minPiece:2, maxPiece:7  },
    { id:'open',     label:'Aperto',    gTower:0.16, gArea:0.15, gShape:0.45, pTurn:0.30, minPiece:3, maxPiece:10 },
    { id:'straight', label:'Rettilineo',gTower:0.14, gArea:0.12, gShape:0.20, pTurn:0.18, minPiece:4, maxPiece:14 },
];

/**
 * Genera un singolo path casuale pezzo per pezzo (approccio Iskala §4.1).
 * @param {number} C   - colonne griglia
 * @param {number} R   - righe griglia
 * @param {object} style - stile con pTurn, minPiece, maxPiece
 * @param {number} targetLen - lunghezza target in celle
 * @returns {[c,r][]|null} path o null se fallisce
 */
/**
 * Genera un path cella per cella con direzione biased (approccio Iskala §4.1).
 * 
 * Strategia:
 * - Cresce di 1 cella alla volta, scegliendo la direzione con peso bias:
 *   - inerzia: peso 3 per stessa direzione
 *   - pTurn: peso per svolte laterali
 *   - bias verso destra per incoraggiare raggiungere il bordo exit
 * - Quando bloccato: backtrack vero (rimuove le ultime celle)
 * - Si ferma quando raggiunge targetLen o non può più crescere
 */
function buildRandomPath(C, R, style, targetLen) {
    const { pTurn } = style;
    const DIRS = [[1,0],[0,1],[0,-1],[-1,0]]; // R, D, U, L

    const entryR = rand(1, R-2);
    const path = [[0, entryR]];
    const used = new Set([key(0, entryR)]);
    let dir = 0; // inizia sempre verso destra

    const maxBacktrack = Math.floor(targetLen * 0.4);
    let totalBacktracks = 0;

    while (path.length < targetLen) {
        const [cc, cr] = path[path.length-1];
        const opposite = (dir + 2) % 4;
        // Progress lungo la griglia: quando siamo lontani dal target,
        // aumenta il bias verso destra per raggiungere il bordo exit
        const progress = path.length / targetLen;

        // Raccogli direzioni valide (almeno 1 cella libera)
        const candidates = [];
        for (let d = 0; d < 4; d++) {
            const [dx, dy] = DIRS[d];
            const nc = cc+dx, nr = cr+dy;
            if (nc < 0 || nc >= C || nr < 0 || nr >= R) continue;
            if (used.has(key(nc, nr))) continue;
            // Peso base
            let w = 1;
            if (d === dir) w = 3;                       // inerzia
            else if (d === opposite) w = 0.15;          // quasi mai indietro
            else w = pTurn * 2;                         // svolta laterale
            // Bonus verso destra quando il path è abbastanza lungo
            if (d === 0 && progress > 0.6) w += 2.0;
            candidates.push({ d, nc, nr, w });
        }

        if (candidates.length === 0) {
            // Backtrack
            if (path.length <= 2 || totalBacktracks >= maxBacktrack) break;
            const removed = path.pop();
            used.delete(key(...removed));
            totalBacktracks++;
            if (path.length >= 2) {
                const prev = path[path.length-2], cur = path[path.length-1];
                const ddx = cur[0]-prev[0], ddy = cur[1]-prev[1];
                const found = DIRS.findIndex(([dx,dy]) => dx===ddx && dy===ddy);
                if (found >= 0) dir = found;
            }
            continue;
        }

        // Selezione pesata
        const totalW = candidates.reduce((s,c) => s+c.w, 0);
        let rnd = Math.random() * totalW;
        let chosen = candidates[candidates.length-1];
        for (const cand of candidates) { rnd -= cand.w; if (rnd <= 0) { chosen = cand; break; } }

        path.push([chosen.nc, chosen.nr]);
        used.add(key(chosen.nc, chosen.nr));
        dir = chosen.d;
    }

    const minLen = Math.max(8, Math.floor((C + R) * 0.6));
    return path.length >= minLen ? path : null;
}


/**
 * f_tower: densità locale — % di celle del path nel raggio medio di una torretta.
 * Piazza torrette virtuali ogni 3 celle e conta quante celle path cadono nel raggio.
 */
function evalFtower(path, towerRange = 3.0) {
    if (path.length === 0) return 0;
    const pathSet = new Set(path.map(([c,r]) => key(c,r)));
    const rSq = towerRange * towerRange;
    let totalInRange = 0, towerCount = 0;
    const maxTiles = Math.PI * rSq; // area massima cerchio

    for (let i = 0; i < path.length; i += 3) {
        const [tc, tr] = path[i];
        let inRange = 0;
        // Conta celle path nel raggio
        const rInt = Math.ceil(towerRange);
        for (let dc = -rInt; dc <= rInt; dc++) {
            for (let dr = -rInt; dr <= rInt; dr++) {
                if (dc*dc + dr*dr <= rSq && pathSet.has(key(tc+dc, tr+dr))) inRange++;
            }
        }
        totalInRange += inRange;
        towerCount++;
    }
    return towerCount > 0 ? totalInRange / (towerCount * maxTiles) : 0;
}

/**
 * f_area: densità globale — path.length / (W * H) del bounding box.
 */
function evalFarea(path) {
    if (path.length < 2) return 0;
    const cs = path.map(([c]) => c), rs = path.map(([,r]) => r);
    const W = Math.max(...cs) - Math.min(...cs) + 1;
    const H = Math.max(...rs) - Math.min(...rs) + 1;
    return path.length / (W * H);
}

/**
 * f_shape: quanto il BB è quadrato — H/W (con H ≤ W).
 */
function evalFshape(path) {
    if (path.length < 2) return 0;
    const cs = path.map(([c]) => c), rs = path.map(([,r]) => r);
    const W = Math.max(...cs) - Math.min(...cs) + 1;
    const H = Math.max(...rs) - Math.min(...rs) + 1;
    const mn = Math.min(W,H), mx = Math.max(W,H);
    return mx > 0 ? mn/mx : 0;
}

/**
 * Score composito: distanza dai goal (più vicino = meglio).
 * Peso maggiore su f_tower perché misura direttamente la difficoltà.
 */
function scoreCandidate(path, style) {
    const ft = evalFtower(path);
    const fa = evalFarea(path);
    const fs = evalFshape(path);
    const dt = Math.abs(ft - style.gTower);
    const da = Math.abs(fa - style.gArea);
    const ds = Math.abs(fs - style.gShape);
    return { score: 2*dt + da + ds, ft, fa, fs };
}

/**
 * Generatore principale Iskala: N candidati → seleziona il migliore.
 * Restituisce { path, pathSet, altPaths:[], entry, exit, style, evalScores }
 */
function generateIskaladPath(C, R, style, N = 100) {
    // Target: lungo abbastanza da essere interessante ma realisticamente raggiungibile.
    // Una singola corsa su griglia CxR può coprire al max ~(C+R)*4 celle senza ripassare.
    // Usiamo (C+R)*2 come target con pTurn medio — garantisce percorsi lunghi e variegati.
    const baseTarget = Math.floor((C + R) * (1.5 + Math.random() * 1.0));
    const targetLen  = Math.min(baseTarget, Math.floor(C * R * 0.65));

    let bestPath = null, bestScore = Infinity, bestEval = null;

    for (let attempt = 0; attempt < N; attempt++) {
        const path = buildRandomPath(C, R, style, targetLen);
        if (!path) continue;

        const { score, ft, fa, fs } = scoreCandidate(path, style);
        if (score < bestScore) {
            bestScore = score;
            bestPath  = path;
            bestEval  = { ft, fa, fs, score };
        }
        if (score < 0.04) break;  // abbastanza buono
    }

    if (!bestPath) return null;

    const pathSet = new Set(bestPath.map(([c,r]) => key(c,r)));
    return {
        path: bestPath,
        pathSet,
        altPaths: [],
        entry: bestPath[0],
        exit:  bestPath[bestPath.length-1],
        style,
        evalScores: bestEval,
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// DECORAZIONI
// ─────────────────────────────────────────────────────────────────────────────
function generateDeco(C, R, pathSet, theme) {
    const deco = [];
    for (let c=0; c<C; c++) for (let r=0; r<R; r++) {
        if (pathSet.has(key(c,r))) continue;
        for (const [type, cfg] of Object.entries(theme.deco)) {
            if (coinf(cfg.chance)) {
                const colorIdx = rand(0, cfg.colors.length-1);
                deco.push({ c, r, type, color: cfg.colors[colorIdx], size: cfg.size });
                break;
            }
        }
    }
    return deco;
}

function generateCheckpoints(path) {
    const cps = new Set();
    if (path.length < 12) return cps;
    const step = Math.floor(path.length / (2 + rand(0,2)));
    for (let i = step; i < path.length - step; i += step) {
        cps.add(key(...path[i]));
    }
    return cps;
}

// ─────────────────────────────────────────────────────────────────────────────
// GENERATE — punto di ingresso pubblico
// ─────────────────────────────────────────────────────────────────────────────

// Compatibilità: array LAYOUTS usato da _tierLabel
const LAYOUTS = PATH_STYLES.map(s => ({ id: s.id, label: s.label }));

/**
 * Genera una mappa completa.
 * @param {number} C     - colonne
 * @param {number} R     - righe
 * @param {number} wave  - ondata corrente (determina tema e difficoltà)
 * @returns MapResult
 */
function generate(C, R, wave = 1) {
    // ── Tema visivo: ciclo su tutte le wave, cambia ad ogni mappa ──
    const themeIdx = (wave - 1) % THEME_KEYS.length;
    const theme    = THEMES[THEME_KEYS[themeIdx]];

    // ── Stile path: difficoltà crescente con la wave ──
    // Wave  1-4 → Denso (facile)
    // Wave  5-9 → Bilanciato
    // Wave 10-15 → Aperto
    // Wave 16+  → Rettilineo (difficile)
    const styleIdx = wave <= 4 ? 0 : wave <= 9 ? 1 : wave <= 15 ? 2 : 3;
    const style    = PATH_STYLES[styleIdx];

    // ── Numero tentativi: più grandi = più tentativi (target più alto) ──
    const N = Math.min(150, 60 + Math.floor(C * R / 50));

    const result = generateIskaladPath(C, R, style, N);

    if (!result || !result.path || result.path.length < 6) {
        return fallbackSerpentine(C, R, theme, style);
    }

    const { path, pathSet } = result;
    const checkpoints = generateCheckpoints(path);
    const deco        = generateDeco(C, R, pathSet, theme);
    const evalLabel   = result.evalScores
        ? `ft=${result.evalScores.ft.toFixed(2)} fa=${result.evalScores.fa.toFixed(2)}`
        : '';

    return {
        path,
        path2:      null,
        pathSet,
        altPaths:   [],
        checkpoints,
        deco,
        theme,
        label:      `${style.label} — ${theme.name}`,
        dual:       false,
        layoutId:   style.id,
        evalLabel,
    };
}

/** Serpentina di fallback garantita */
function fallbackSerpentine(C, R, theme, style) {
    const cells = new Set();
    const add = (c,r) => { if(c>=0&&c<C&&r>=0&&r<R) cells.add(key(c,r)); };
    let r=1, goRight=true;
    while (r <= R-2) {
        for (let c=0; c<C; c++) add(c,r);
        const nr=r+2; if(nr>R-2) break;
        const connC = goRight ? C-1 : 0;
        add(connC, r+1);
        goRight=!goRight; r=nr;
        if (goRight) for(let c=1;c<C;c++) add(c,r);
        else         for(let c=0;c<C-1;c++) add(c,r);
    }
    const allC = [...cells].map(k=>k.split(',').map(Number));
    const onLeft  = allC.filter(([c])=>c===0).map(([,rv])=>rv).sort((a,b)=>a-b);
    const onRight = allC.filter(([c])=>c===C-1).map(([,rv])=>rv).sort((a,b)=>a-b);
    const entryR = onLeft[0]??1, exitR=onRight[onRight.length-1]??R-2;
    // BFS to build path
    const entry=[0,entryR], exit=[C-1,exitR];
    const prev=new Map(); prev.set(key(...entry),null);
    const q=[entry];
    for(let i=0;i<q.length;i++){
        const cur=q[i];
        if(cur[0]===exit[0]&&cur[1]===exit[1]) break;
        for(const [dc,dr] of [[1,0],[-1,0],[0,1],[0,-1]]){
            const nc=cur[0]+dc,nr=cur[1]+dr,k=key(nc,nr);
            if(cells.has(k)&&!prev.has(k)){prev.set(k,[...cur]);q.push([nc,nr]);}
        }
    }
    let path=[], cur=[...exit];
    while(cur){path.unshift(cur);cur=prev.get(key(...cur));}
    if(path.length<4) path=[[0,entryR],[C-1,exitR]];
    const pathSet=new Set(path.map(([c,r])=>key(c,r)));
    const checkpoints=generateCheckpoints(path);
    const deco=generateDeco(C,R,pathSet,theme);
    const sLabel = style?.label ?? 'Serpentina';
    return {
        path, path2:null, pathSet, altPaths:[],
        checkpoints, deco, theme,
        label:`${sLabel} — ${theme.name}`,
        dual:false, layoutId:'fallback',
    };
}


// Sostituisce tutta la logica di rendering mappa in games.js
// ─────────────────────────────────────────────────────────────────────────────
function drawMap(ctx, mapResult, CS, tick = 0) {
    const { path, path2, altPaths, checkpoints, deco, theme } = mapResult;
    const c = ctx;
    if (!path || path.length === 0) return;
    const COLS = mapResult.COLS || (Math.max(...path.map(p=>p[0])) + 2);
    const ROWS = mapResult.ROWS || (Math.max(...path.map(p=>p[1])) + 2);
    const W = COLS * CS, H = ROWS * CS;

    // ── Sfondo tema ──
    if (theme.ambientFx === 'grid') {
        // Sfondo cyber con griglia luminosa
        c.fillStyle = theme.ground; c.fillRect(0,0,W,H);
        c.strokeStyle = 'rgba(0,180,255,0.06)'; c.lineWidth = 1;
        for (let i=0; i<=COLS; i++) { c.beginPath(); c.moveTo(i*CS,0); c.lineTo(i*CS,H); c.stroke(); }
        for (let i=0; i<=ROWS; i++) { c.beginPath(); c.moveTo(0,i*CS); c.lineTo(W,i*CS); c.stroke(); }
    } else {
        // Pattern bicolore a scacchi
        for (let cc=0; cc<COLS; cc++) for (let rr=0; rr<ROWS; rr++) {
            c.fillStyle = (cc+rr)%2===0 ? theme.ground : theme.groundDark;
            c.fillRect(cc*CS, rr*CS, CS, CS);
        }
    }

    // ── Effetto ambientale neve (fiocchi) ──
    if (theme.ambientFx === 'snow') {
        c.fillStyle = 'rgba(220,235,255,0.35)';
        const snowSeed = Math.floor(tick / 4);
        for (let i=0; i<18; i++) {
            const sx = ((i*137 + snowSeed*3) % W);
            const sy = ((i*91  + snowSeed*7) % H);
            c.beginPath(); c.arc(sx, sy, 1.5, 0, Math.PI*2); c.fill();
        }
    }

    // ── AltPaths (percorsi decorativi) ──
    if (altPaths) altPaths.forEach(seg => {
        seg.forEach(([px,py]) => {
            c.fillStyle = theme.alt;
            c.fillRect(px*CS, py*CS, CS, CS);
            c.strokeStyle = 'rgba(0,0,0,0.15)'; c.lineWidth=1;
            c.strokeRect(px*CS+0.5, py*CS+0.5, CS-1, CS-1);
        });
    });

    // ── Decorazioni (alberi, rocce, cristalli, fiamme, cactus) ──
    if (deco) deco.forEach(d => {
        const x = d.c*CS + CS/2, y = d.r*CS + CS/2;
        const sz = d.size * CS * 0.42;
        c.fillStyle = d.color;
        switch (d.type) {
            case 'tree': case 'pine': case 'cactus':
                // Triangolo verde
                c.beginPath();
                c.moveTo(x, y - sz);
                c.lineTo(x + sz*0.7, y + sz*0.5);
                c.lineTo(x - sz*0.7, y + sz*0.5);
                c.closePath(); c.fill();
                // Tronco
                c.fillStyle = '#6a4020';
                c.fillRect(x-2, y+sz*0.45, 4, sz*0.35);
                break;
            case 'rock':
                c.beginPath(); c.ellipse(x, y+sz*0.2, sz*0.7, sz*0.5, 0, 0, Math.PI*2); c.fill();
                c.fillStyle = 'rgba(255,255,255,0.12)';
                c.beginPath(); c.ellipse(x-sz*0.2, y, sz*0.25, sz*0.18, -0.5, 0, Math.PI*2); c.fill();
                break;
            case 'crystal':
                c.beginPath();
                c.moveTo(x, y-sz); c.lineTo(x+sz*0.4, y); c.lineTo(x, y+sz*0.6); c.lineTo(x-sz*0.4, y);
                c.closePath(); c.fill();
                c.fillStyle = 'rgba(255,255,255,0.3)';
                c.beginPath(); c.moveTo(x-sz*0.1, y-sz); c.lineTo(x+sz*0.15, y); c.lineTo(x-sz*0.1, y); c.closePath(); c.fill();
                break;
            case 'flame': {
                const pulse = 0.8 + 0.2*Math.sin(tick*0.18 + d.c*1.3);
                c.globalAlpha = 0.7*pulse;
                c.fillStyle = d.color;
                c.beginPath();
                c.moveTo(x, y-sz*pulse);
                c.bezierCurveTo(x+sz*0.5, y-sz*0.3, x+sz*0.3, y+sz*0.2, x, y+sz*0.3);
                c.bezierCurveTo(x-sz*0.3, y+sz*0.2, x-sz*0.5, y-sz*0.3, x, y-sz*pulse);
                c.fill();
                c.globalAlpha = 1;
                break;
            }
        }
    });

    // ── Funzione per disegnare UN percorso ──
    function drawPath(pts, isSecondary = false) {
        if (!pts || pts.length === 0) return;
        const pLen = pts.length;
        pts.forEach(([px,py], i) => {
            const t = i / pLen;
            const [sr, sg, sb] = theme.path[0];
            const [er, eg, eb] = theme.path[1];
            const r2 = Math.round(sr + (er-sr)*t);
            const g2 = Math.round(sg + (eg-sg)*t);
            const b2 = Math.round(sb + (eb-sb)*t);
            c.fillStyle = isSecondary ? `rgba(${r2},${g2},${b2},0.75)` : `rgb(${r2},${g2},${b2})`;
            c.fillRect(px*CS, py*CS, CS, CS);
        });
        // Bordi
        c.strokeStyle = theme.pathEdge; c.lineWidth=1;
        pts.forEach(([px,py]) => c.strokeRect(px*CS+0.5, py*CS+0.5, CS-1, CS-1));

        // Frecce direzionali (ogni 2 passi)
        c.fillStyle = theme.arrowColor;
        for (let i=1; i<pts.length-1; i+=2) {
            const [c1p,r1p] = pts[i-1];
            const [c2p,r2p] = pts[Math.min(i+1, pts.length-1)];
            const mx = pts[i][0]*CS + CS/2, my = pts[i][1]*CS + CS/2;
            const adx=c2p-c1p, ady=r2p-r1p, len=Math.hypot(adx,ady)||1;
            const nx=adx/len, ny=ady/len;
            c.beginPath();
            c.moveTo(mx+nx*5, my+ny*5);
            c.lineTo(mx-ny*3, my+nx*3);
            c.lineTo(mx+ny*3, my-nx*3);
            c.fill();
        }
    }

    drawPath(path);
    if (path2) drawPath(path2, true);

    // ── Checkpoint (fortini) ──
    if (checkpoints) checkpoints.forEach(k2 => {
        const [cc,rr] = k2.split(',').map(Number);
        const pulse = 0.6 + 0.4*Math.abs(Math.sin(tick*0.07));
        c.save();
        c.globalAlpha = 0.7*pulse;
        c.fillStyle = '#ffd700';
        c.beginPath(); c.arc(cc*CS+CS/2, rr*CS+CS/2, CS*0.38, 0, Math.PI*2); c.fill();
        c.globalAlpha = 1;
        c.strokeStyle = '#ffaa00'; c.lineWidth=1.5;
        c.beginPath(); c.arc(cc*CS+CS/2, rr*CS+CS/2, CS*0.38, 0, Math.PI*2); c.stroke();
        // Stella
        c.fillStyle = '#fff'; c.font = `${Math.max(8, Math.round(CS*0.38))}px monospace`;
        c.textAlign='center'; c.textBaseline='middle';
        c.fillText('⚔', cc*CS+CS/2, rr*CS+CS/2+1);
        c.textBaseline='alphabetic';
        c.restore();
    });

    // ── IN / OUT labels ──
    const [sc2,sr2] = path[0], [ec2,er2] = path[path.length-1];
    c.textAlign='center'; c.font=`bold ${Math.max(9,Math.round(CS*0.4))}px monospace`;
    // Shadow
    c.fillStyle='rgba(0,0,0,0.7)';
    c.fillText('IN',  sc2*CS+CS/2+1, sr2*CS+CS/2+5);
    c.fillText('OUT', ec2*CS+CS/2+1, er2*CS+CS/2+5);
    // Text
    c.fillStyle='rgba(255,255,255,0.92)';
    c.fillText('IN',  sc2*CS+CS/2, sr2*CS+CS/2+4);
    c.fillStyle='rgba(255,100,100,0.92)';
    c.fillText('OUT', ec2*CS+CS/2, er2*CS+CS/2+4);

    // Seconda lane IN/OUT
    if (path2 && path2.length > 0) {
        const [se,re] = path2[0], [sx2,rx2] = path2[path2.length-1];
        c.fillStyle='rgba(0,0,0,0.7)';
        c.fillText('IN2',  se*CS+CS/2+1, re*CS+CS/2+5);
        c.fillText('OUT2', sx2*CS+CS/2+1, rx2*CS+CS/2+5);
        c.fillStyle='rgba(180,255,180,0.92)';
        c.fillText('IN2',  se*CS+CS/2, re*CS+CS/2+4);
        c.fillStyle='rgba(255,180,100,0.92)';
        c.fillText('OUT2', sx2*CS+CS/2, rx2*CS+CS/2+4);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// EXPORT
// ─────────────────────────────────────────────────────────────────────────────
return { generate, drawMap, THEMES, LAYOUTS };

})(); // end IIFE


// ═══════════════════════════════════════════════════════════════════════════
// TOWER DEFENCE
// ═══════════════════════════════════════════════════════════════════════════
class TowerDefence {
    constructor(area) {
        this.area = area;
        this.hi = LS.get('td_hi') || 0;
        const savedCfg = LS.get('td_cfg') || {};
        // mapSize minimo 3 — ignora valori vecchi salvati < 3
        if (savedCfg.mapSize && savedCfg.mapSize < 3) delete savedCfg.mapSize;
        this.cfg = Object.assign({
            startCredits: 300, carryoverPct: 50, livesStart: 20,
            mapSize: 3, mapCols: 0, mapRows: 0,
            basicDmg: 12, basicRate: 30, basicRange: 2.5, basicCost: 50,
            rapidDmg: 6,  rapidRate: 12, rapidRange: 2.0, rapidCost: 80,
            sniperDmg: 35, sniperRate: 70, sniperRange: 5.0, sniperCost: 120,
            slowDmg: 4,   slowRate: 25, slowRange: 2.2, slowCost: 90,  slowFactor: 0.45,
            aoeDmg: 18,   aoeRate: 50, aoeRange: 1.8, aoeCost: 150, aoeRadius: 1.2,
            critDmg: 10,  critRate: 28, critRange: 2.5, critCost: 110, critAura: 2.5, critBonus: 0.35,
            frostDmg: 5,  frostRate: 20, frostRange: 2.8, frostCost: 130, frostRadius: 1.8, frostFactor: 0.30,
            baseCritChance: 0.08, critMultiplier: 2.5,
            upgDmgMul:   [1, 1.5, 2.2],
            upgRateMul:  [1, 0.72, 0.52],
            upgRangeMul: [1, 1.25, 1.55],
            upgCostMul:  [0, 0.6, 1.0],
            enemyHpBase: 25, enemyHpWave: 12,
            enemySpeedBase: 26, enemySpeedWave: 3,
            creditsPerKill: 8, creditsPerWave: 60, creditsPerWaveWave: 12,
        }, savedCfg);
        const CELL_SIZE = 20;
        const SZ = { 1: { c: 18, r: 12 }, 2: { c: 28, r: 18 }, 3: { c: 38, r: 24 }, 4: { c: 50, r: 30 } };
        const sz = SZ[this.cfg.mapSize] || SZ[2];
        this.COLS = this.cfg.mapCols > 0 ? this.cfg.mapCols : sz.c;
        this.ROWS = this.cfg.mapRows > 0 ? this.cfg.mapRows : sz.r;
        this.CELL = CELL_SIZE;
        this.carryCredits = LS.get('td_carry') || 0;
        this.hoveredTower = null;
        this.sellMode = false;
        this.generateMap();
        this.build();
    }

    // ── Mappa ─────────────────────────────────────────────────────────────
    //
    // Sistema progressivo a 4 livelli basato sul numero di wave totali giocate:
    //
    //  Tier 1 (wave 1-3):   Serpentina garantita — massima circolarità,
    //                        percorso denso (70-80% celle), pochissimo spazio
    //                        libero per le torrette. Mappa "tutorial".
    //
    //  Tier 2 (wave 4-7):   Serpentina con variazioni — loop a U multipli,
    //                        piccole deviazioni randomizzate, ~55-65% celle.
    //
    //  Tier 3 (wave 8-12):  Percorso ibrido — struttura a serpentina come
    //                        scheletro + connessioni extra casuali che creano
    //                        loop irregolari, ~40-55% celle.
    //
    //  Tier 4 (wave 13+):   Generativo libero — labirinto DFS con path
    //                        greedy BFS-guidato, nessuna garanzia strutturale.
    //                        Percentuale celle libera e variabile.
    //
    // Il tier si aggiorna ad ogni rigenerazione della mappa (pulsante ↻ MAPPA
    // o all'avvio), leggendo this.wave. Le mappe tier 1-3 usano percorsi
    // costruiti programmaticamente — niente retry loop, risultato garantito.

    generateMap() {
        const result = TDMapSystem.generate(this.COLS, this.ROWS, this.wave || 1);
        this.path       = result.path;
        this.path2      = result.path2   || null;
        this.pathSet    = result.pathSet;
        this.altPaths   = result.altPaths;
        this.checkpoints= result.checkpoints;
        this.mapDeco    = result.deco;
        this.mapTheme   = result.theme;
        this.mapLabel   = result.label;
        this.dualLane   = result.dual;
        this._drawTick  = 0;  // tick per animazioni mappa
    }

    _mapTier() { return 1; }   // mantenuto per compatibilità
    _tierLabel() { return this.mapLabel || ''; }

    // ── Tower defs ────────────────────────────────────────────────────────
    _buildTowerDefs() {
        const c = this.cfg;
        this.TOWER_DEFS = {
            basic:  { cost: c.basicCost,  dmg: c.basicDmg,  range: c.basicRange,  rate: c.basicRate,  color: '#1a5a8f', barrel: '#4a9eff',  icon: '🔵', label: 'Base',   desc: 'Bilanciata' },
            rapid:  { cost: c.rapidCost,  dmg: c.rapidDmg,  range: c.rapidRange,  rate: c.rapidRate,  color: '#1a6f3f', barrel: '#2adf8f',  icon: '🟢', label: 'Rapid',  desc: 'Cadenza alta' },
            sniper: { cost: c.sniperCost, dmg: c.sniperDmg, range: c.sniperRange, rate: c.sniperRate, color: '#6f1a5a', barrel: '#ff4acf',  icon: '🟣', label: 'Sniper', desc: 'Lungo raggio' },
            slow:   { cost: c.slowCost,   dmg: c.slowDmg,   range: c.slowRange,   rate: c.slowRate,   color: '#4a6f1a', barrel: '#aaff4a',  icon: '🟡', label: 'Slow',   desc: 'Rallenta nemici' },
            aoe:    { cost: c.aoeCost,    dmg: c.aoeDmg,    range: c.aoeRange,    rate: c.aoeRate,    color: '#8f3a1a', barrel: '#ffaa4a',  icon: '🔴', label: 'AoE',    desc: 'Danno ad area' },
            crit:   { cost: c.critCost,   dmg: c.critDmg,   range: c.critRange,   rate: c.critRate,   color: '#6f5a00', barrel: '#ffd700',  icon: '⭐', label: 'Crit',   desc: 'Aura critico +crit chance', aura: c.critAura, critBonus: c.critBonus },
            frost:  { cost: c.frostCost,  dmg: c.frostDmg,  range: c.frostRange,  rate: c.frostRate,  color: '#0a3a5a', barrel: '#7adfff',  icon: '❄️', label: 'Frost',  desc: 'Rallenta area passiva', frostRadius: c.frostRadius, frostFactor: c.frostFactor },
        };
    }

    // ── Build UI ──────────────────────────────────────────────────────────
    build() {
        const W = this.COLS * this.CELL, H = this.ROWS * this.CELL;
        const startCred = this.carryCredits > 0
            ? Math.round(this.cfg.startCredits + this.carryCredits)
            : this.cfg.startCredits;

        this.area.innerHTML = `
            <div style="display:flex;flex-direction:column;gap:10px;width:fit-content;max-width:100%;margin:0 auto">
                
                <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;justify-content:space-between;background:#040c1a;border:1px solid #1a3050;border-radius:6px;padding:5px 10px;box-sizing:border-box">
                    <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
                        <div class="arc-stat" style="padding:4px 10px;min-width:50px"><div class="arc-stat-label">ONDATA</div><div id="td-wave" class="arc-stat-value">1</div></div>
                        <div class="arc-stat" style="padding:4px 10px;min-width:50px"><div class="arc-stat-label">VITE</div><div id="td-lives" class="arc-stat-value green">${this.cfg.livesStart}</div></div>
                        <div class="arc-stat" style="padding:4px 10px;min-width:56px"><div class="arc-stat-label">CREDITI</div><div id="td-credits" class="arc-stat-value yellow">${startCred}</div></div>
                        <div class="arc-stat" style="padding:4px 10px;min-width:50px"><div class="arc-stat-label">SCORE</div><div id="td-score" class="arc-stat-value">0</div></div>
                        <div class="arc-stat" style="padding:4px 10px;min-width:50px"><div class="arc-stat-label">HI</div><div id="td-hi" class="arc-stat-value yellow">${this.hi}</div></div>
                        <div class="arc-stat" style="padding:4px 8px" title="${this.mapLabel || ''}"><div class="arc-stat-label">MAPPA</div><div id="td-tier" class="arc-stat-value" style="font-size:9px;color:#5a8ab0;max-width:110px;overflow:hidden;white-space:nowrap">${this._tierLabel()}</div></div>
                    </div>
                    <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
                        <button class="arcade-btn arcade-btn-sm arcade-btn-green" id="td-btn-wave" style="height:34px;padding:0 12px">▶ ONDATA</button>
                        <button class="arcade-btn arcade-btn-sm arcade-btn-red" id="td-btn-stop" style="display:none;height:34px">■ STOP</button>
                        <button class="arcade-btn arcade-btn-sm arcade-btn-yellow" id="td-btn-newmap" style="height:34px;padding:0 10px">↻</button>
                        <div style="display:flex;align-items:center;gap:2px">
                            <span style="font-size:9px;color:#5a8ab0">⚡</span>
                            ${['0.5','1','2','3'].map(v=>`<button class="arcade-btn td-speed-btn arcade-btn-sm" data-speed="${v}" style="padding:2px 8px;${v==='1'?'border-color:#4a9eff;color:#4a9eff':''}">${v}x</button>`).join('')}
                        </div>
                        <button class="arcade-btn arcade-btn-sm" id="td-btn-cfg" style="border-color:#3a5a8a;color:#5a8ab0;height:34px;padding:0 10px">⚙</button>
                    </div>
                </div>

                <div id="td-canvas-wrap" style="position:relative;display:flex;justify-content:center;line-height:0;overflow-x:auto;max-width:100%">
                    </div>

                <div style="display:flex;gap:5px;align-items:center;background:#040c1a;border:1px solid #1a3050;border-radius:6px;padding:5px 10px;overflow-x:auto;box-sizing:border-box">
                    <div id="td-tower-cards" style="display:flex;gap:4px;flex-shrink:0"></div>
                    <div style="width:1px;background:#1e3a5f;align-self:stretch;flex-shrink:0;margin:0 3px"></div>
                    <button class="arcade-btn arcade-btn-sm" id="td-btn-sell" style="border-color:#ffaa4a;color:#ffaa4a;font-size:10px;white-space:nowrap;flex-shrink:0;padding:3px 10px">🔧 VENDI [V]</button>
                    <div style="width:1px;background:#1e3a5f;align-self:stretch;flex-shrink:0;margin:0 3px"></div>
                    <div style="display:flex;gap:6px;align-items:center;flex-shrink:0;font-size:10px;white-space:nowrap">
                        <span title="Normale" style="color:#ff3333">● Norm</span>
                        <span title="Veloce" style="color:#ff9f4a">● Vel</span>
                        <span title="Armato" style="color:#c04aff">■ Arm</span>
                        <span title="Boss" style="color:#ffdf4a">★ Boss</span>
                        <span title="Regen" style="color:#44ffaa">● Reg</span>
                        <span title="Scudo" style="color:#4af0ff">◆ Scu</span>
                        <span title="Crit aura" style="color:#ffd700">⭐ Crit</span>
                        <span title="Frost slow" style="color:#7adfff">❄ Frost</span>
                        <span style="color:#2a3a5a;margin-left:4px;font-size:9px">click=upgrade · DX=vendi</span>
                    </div>
                </div>
            </div>
        `;

        // Canvas — bordo colore tema mappa, block display
        const tdWrap = document.getElementById('td-canvas-wrap');
        const cvs = document.createElement('canvas');
        cvs.id = 'td-canvas'; cvs.width = W; cvs.height = H;
        const themeBorder = this.mapTheme?.pathEdge?.replace('rgba','rgb').replace(/,[^,)]+\)$/,')') || '#1e3a5f';
        cvs.style.cssText = `border:1px solid ${themeBorder};cursor:crosshair;display:block`;
        tdWrap.appendChild(cvs);

        window._exttoTD = this;

        this.ctx = document.getElementById('td-canvas').getContext('2d');
        this.towers = []; this.enemies = []; this.bullets = []; this.particles = [];
        this.credits = startCred; this.lives = this.cfg.livesStart;
        this.wave = 1; this.selected = 'basic'; this.waveRunning = false; this.waveStarted = false; this.speed = 1;
        this.spawnTimer = 0; this.toSpawn = 0; this.score = 0;
        this.carryCredits = 0; this.totalKills = 0; this.mapsPlayed = 1;
        this.hoveredTower = null; this.mouseCell = null; this.sellMode = false;
        this._drawTick = 0;
        this._buildTowerDefs();
        this._buildTowerCards();

        // Tooltip globale
        let tooltip = document.getElementById('td-tower-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'td-tower-tooltip';
            document.body.appendChild(tooltip);
        }

        // Canvas events
        cvs.addEventListener('click', e => {
            const r = e.target.getBoundingClientRect();
            const cx = Math.floor((e.clientX - r.left) / this.CELL);
            const cy = Math.floor((e.clientY - r.top) / this.CELL);
            this.placeTower(cx, cy);
        });
        cvs.addEventListener('contextmenu', e => {
            e.preventDefault();
            document.getElementById('td-upgrade-popup')?.remove();
            const r = e.target.getBoundingClientRect();
            const cx = Math.floor((e.clientX - r.left) / this.CELL);
            const cy = Math.floor((e.clientY - r.top) / this.CELL);
            this.sellTower(cx, cy);
        });
        cvs.addEventListener('mousemove', e => {
            const r = e.target.getBoundingClientRect();
            const cx = Math.floor((e.clientX - r.left) / this.CELL);
            const cy = Math.floor((e.clientY - r.top) / this.CELL);
            this.mouseCell = [cx, cy];
            this._showTooltip(cx, cy, e.clientX, e.clientY);
        });
        cvs.addEventListener('mouseleave', () => {
            const tt = document.getElementById('td-tower-tooltip');
            if (tt) tt.style.display = 'none';
            this.hoveredTower = null;
            this.mouseCell = null;
        });

        // Tasto V per vendere
        this.onKeyTD = e => {
            if (e.code === 'KeyV') {
                this.sellMode = !this.sellMode;
                const btn = document.getElementById('td-btn-sell');
                if (btn) {
                    btn.style.background = this.sellMode ? '#2f1500' : '';
                    btn.style.borderColor = this.sellMode ? '#ff6a00' : '#ffaa4a';
                    btn.style.color = this.sellMode ? '#ff6a00' : '#ffaa4a';
                    btn.textContent = this.sellMode ? '🔧 VENDI [attivo]' : '🔧 VENDI [V]';
                }
                if (this.sellMode) document.getElementById('td-canvas').style.cursor = 'pointer';
                else document.getElementById('td-canvas').style.cursor = 'crosshair';
            }
        };
        document.addEventListener('keydown', this.onKeyTD);

        document.getElementById('td-btn-sell').addEventListener('click', () => {
            this.onKeyTD({ code: 'KeyV' });
        });

        ['basic', 'rapid', 'sniper', 'slow', 'aoe', 'crit', 'frost'].forEach(t => {
            const btn = document.getElementById(`td-card-${t}`);
            if (btn) btn.addEventListener('click', () => this.selectTower(t));
        });
        document.getElementById('td-btn-wave').addEventListener('click', () => this.startWave());
        document.getElementById('td-btn-stop').addEventListener('click', () => this.stopGame());
        document.getElementById('td-btn-newmap').addEventListener('click', () => {
            if (this.waveRunning) return;
            clearInterval(this.gameLoop);
            this.carryCredits = Math.round(this.credits * (this.cfg.carryoverPct / 100));
            LS.set('td_carry', this.carryCredits);
            this._applySize();
            this.generateMap(); this.build();
        });
        // Gestione pulsanti velocità
document.querySelectorAll('.td-speed-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        this.setSpeed(Number(e.target.dataset.speed));
    });
});
        document.getElementById('td-btn-cfg').addEventListener('click', () => this._toggleCfg());

        this.selectTower('basic');
        this.gameLoop = setInterval(() => this.tick(), Math.round(50 / this.speed));
        this.draw();
    }

    _buildTowerCards() {
        const container = document.getElementById('td-tower-cards');
        if (!container) return;
        container.innerHTML = '';
        Object.entries(this.TOWER_DEFS).forEach(([type, def]) => {
            const card = document.createElement('div');
            card.className = 'td-tower-card';
            card.id = `td-card-${type}`;
            card.style.cssText = 'flex-direction:row;gap:6px;padding:5px 11px;min-width:0;align-items:center;flex-shrink:0';
            card.innerHTML = `
                <span style="font-size:17px;line-height:1">${def.icon}</span>
                <span style="display:flex;flex-direction:column;gap:1px">
                    <span style="font-size:10px;color:#8ab0d0;letter-spacing:0.3px;white-space:nowrap">${def.label}</span>
                    <span style="font-size:12px;color:#ffdf4a;font-weight:700">${def.cost}c</span>
                </span>
            `;

            // Tooltip d'acquisto al mouseover
            card.addEventListener('mouseenter', e => {
                const tt = document.getElementById('td-tower-tooltip');
                if (!tt) return;
                const canAfford = this.credits >= def.cost;
                const rps = def.rate ? (60 / def.rate).toFixed(1) : '—';
                // Stat speciali per tipo
                let special = '';
                if (type === 'slow')  special = `<div class="tt-row"><span class="tt-key">Rallenta</span><span class="tt-val" style="color:#aaff4a">${Math.round((1-def.slowFactor)*100)}%</span></div>`;
                if (type === 'aoe')   special = `<div class="tt-row"><span class="tt-key">Raggio AoE</span><span class="tt-val" style="color:#ff6a2a">${def.aoeRadius} celle</span></div>`;
                if (type === 'crit')  special = `<div class="tt-row"><span class="tt-key">Aura crit</span><span class="tt-val" style="color:#ffd700">+${Math.round(def.critBonus*100)}%</span></div>`;
                if (type === 'frost') special = `
                    <div class="tt-row"><span class="tt-key">Rallenta</span><span class="tt-val" style="color:#7adfff">${Math.round((1-def.frostFactor)*100)}%</span></div>
                    <div class="tt-row"><span class="tt-key">Raggio</span><span class="tt-val" style="color:#7adfff">${def.frostRadius} celle</span></div>`;

                tt.innerHTML = `
                    <div class="tt-title" style="color:${def.barrel}">${def.icon} ${def.label}
                        <span style="font-size:10px;color:${canAfford?'#2adf8f':'#ff4a4a'};margin-left:6px">${def.cost}c</span>
                    </div>
                    <div style="font-size:9px;color:#3a6a9a;margin-bottom:6px">${def.desc}</div>
                    <div class="tt-row"><span class="tt-key">Danno</span><span class="tt-val">${def.dmg}</span></div>
                    <div class="tt-row"><span class="tt-key">Raggio</span><span class="tt-val">${def.range} celle</span></div>
                    <div class="tt-row"><span class="tt-key">Cadenza</span><span class="tt-val">${rps} colpi/s</span></div>
                    ${special}
                    <div style="margin-top:5px;font-size:9px;color:${canAfford?'#2adf8f':'#ff4a4a'}">
                        ${canAfford ? '✓ Puoi acquistare' : `✗ Mancano ${def.cost - this.credits}c`}
                    </div>
                `;
                // Posiziona sopra la card
                const rect = card.getBoundingClientRect();
                tt.style.display = 'block';
                const ttH = tt.offsetHeight || 140;
                tt.style.left = rect.left + 'px';
                tt.style.top  = (rect.top - ttH - 8) + 'px';
                // Correzione se esce fuori schermo a destra
                const ttW = tt.offsetWidth || 160;
                if (rect.left + ttW > window.innerWidth - 8) {
                    tt.style.left = (window.innerWidth - ttW - 8) + 'px';
                }
            });

            card.addEventListener('mouseleave', () => {
                const tt = document.getElementById('td-tower-tooltip');
                if (tt && !this.hoveredTower) tt.style.display = 'none';
            });

            container.appendChild(card);
        });
    }

    _showTooltip(cx, cy, mx, my) {
        const tt = document.getElementById('td-tower-tooltip');
        if (!tt) return;
        const tower = this.towers.find(t => t.cx === cx && t.cy === cy);
        if (!tower) { tt.style.display = 'none'; this.hoveredTower = null; return; }
        this.hoveredTower = tower;
        const def = this.TOWER_DEFS[tower.type];
        const sellVal = Math.round(tower.cost * 0.6);
        const lv = tower.level || 1;
        const stars = '★'.repeat(lv) + '☆'.repeat(3 - lv);
        tt.innerHTML = `
            <div class="tt-title" style="color:${def.barrel}">${def.icon} ${def.label} <span style="color:#ffd700">${stars}</span></div>
            <div style="font-size:9px;color:#3a6a9a;margin-bottom:5px">${def.desc}</div>
            <div class="tt-row"><span class="tt-key">Danno</span><span class="tt-val">${tower.dmg}</span></div>
            <div class="tt-row"><span class="tt-key">Raggio</span><span class="tt-val">${tower.range.toFixed(1)} celle</span></div>
            <div class="tt-row"><span class="tt-key">Fuoco</span><span class="tt-val">${tower.rate} tick</span></div>
            ${tower.type === 'slow'  ? `<div class="tt-row"><span class="tt-key">Rallenta</span><span class="tt-val" style="color:#aaff4a">${Math.round((1-(tower.slowFactor||0.45))*100)}%</span></div>` : ''}
            ${tower.type === 'crit'  ? `<div class="tt-row"><span class="tt-key">Aura crit</span><span class="tt-val" style="color:#ffd700">+${Math.round((tower.critBonus||0)*100)}%</span></div>` : ''}
            ${tower.type === 'frost' ? `<div class="tt-row"><span class="tt-key">Frost %</span><span class="tt-val" style="color:#7adfff">${Math.round((1-(tower.frostFactor||0.3))*100)}%</span></div>` : ''}
            ${tower.type === 'frost' ? `<div class="tt-row"><span class="tt-key">Raggio frost</span><span class="tt-val" style="color:#7adfff">${(tower.frostRadius||1.8).toFixed(1)}</span></div>` : ''}
            ${tower.type === 'aoe'   ? `<div class="tt-row"><span class="tt-key">Raggio AoE</span><span class="tt-val">${(tower.aoeRadius||1.2).toFixed(1)} celle</span></div>` : ''}
            <div class="tt-row" style="margin-top:5px;padding-top:4px;border-top:1px solid #1e3a5f"><span class="tt-key">Vendita</span><span class="tt-val" style="color:#ffaa4a">${sellVal}c</span></div>
            <div style="font-size:9px;color:#3a5a7a;margin-top:4px">Click per upgrade · DX o [V] per vendere</div>
        `;
        tt.style.display = 'block';
        const tx = Math.min(mx + 14, window.innerWidth - 180);
        const ty = Math.min(my - 10, window.innerHeight - 240);
        tt.style.left = tx + 'px'; tt.style.top = ty + 'px';
    }

    _toggleCfg() {
        // Usa un modal overlay fisso invece di un pannello inline
        let modal = document.getElementById('td-cfg-modal');
        if (modal) { modal.remove(); return; }

        const c = this.cfg;
        const sl = (key, label, min, max, step = 1) => `
            <div style="margin-bottom:12px">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                    <span style="color:#8ab8d0;font-size:12px">${label}</span>
                    <span id="td-v-${key}" style="color:#4a9eff;font-size:12px;font-weight:600;min-width:36px;text-align:right">${c[key]}</span>
                </div>
                <input type="range" min="${min}" max="${max}" step="${step}" value="${c[key]}"
                    style="width:100%;accent-color:#4a9eff"
                    oninput="document.getElementById('td-v-${key}').textContent=this.value;window._exttoTD.cfg['${key}']=+this.value;window._exttoTD._buildTowerDefs();window._exttoTD._saveCfg()">
            </div>`;

        const sec = t => `
            <div style="color:#4a9eff;font-size:12px;font-weight:700;letter-spacing:1px;
                        text-transform:uppercase;padding:10px 0 8px;margin-bottom:6px;
                        border-bottom:2px solid #1e3a5f;grid-column:1/-1">${t}</div>`;

        const towerBlock = (name, color, keys) =>
            `<div style="background:#060d18;border:1px solid #1a3050;border-radius:8px;padding:12px">
                <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:${color};
                            margin-bottom:10px;text-transform:uppercase">${name}</div>
                ${keys.map(([k,l,mn,mx,st]) => sl(k,l,mn,mx,st)).join('')}
            </div>`;

        modal = document.createElement('div');
        modal.id = 'td-cfg-modal';
        modal.style.cssText = `
            position:fixed;inset:0;z-index:100001;
            background:rgba(1,4,10,0.82);
            display:flex;align-items:center;justify-content:center;
            padding:16px;
        `;

        modal.innerHTML = `
            <div style="background:#070f1e;border:1px solid #2a4a6f;border-radius:12px;
                        width:min(860px,96vw);max-height:90vh;display:flex;flex-direction:column;
                        box-shadow:0 8px 48px rgba(0,0,0,0.8);overflow:hidden">

                <!-- Header sticky -->
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:14px 20px;background:#050d1a;border-bottom:1px solid #1e3a5f;
                            flex-shrink:0">
                    <span style="color:#4a9eff;font-size:15px;font-weight:700;letter-spacing:1px">
                        ⚙ Configurazione Tower Defence
                    </span>
                    <div style="display:flex;gap:8px">
                        <button class="arcade-btn arcade-btn-sm" onclick="window._exttoTD._resetCfg()">↺ Reset</button>
                        <button class="arcade-btn arcade-btn-sm arcade-btn-red"
                            onclick="document.getElementById('td-cfg-modal').remove()">✕ Chiudi</button>
                    </div>
                </div>

                <!-- Corpo scrollabile -->
                <div style="overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:20px">

                    <!-- Riga 1: Generali + Mappa -->
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">

                        <div style="background:#060d18;border:1px solid #1a3050;border-radius:8px;padding:12px">
                            <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#4a9eff;
                                        margin-bottom:10px;text-transform:uppercase">Generali</div>
                            ${sl('startCredits','Crediti iniziali',100,500,25)}
                            ${sl('carryoverPct','Carry-over mappa %',0,100,5)}
                            ${sl('livesStart','Vite iniziali',5,50,5)}
                            ${sl('creditsPerKill','Crediti per kill',1,30)}
                            ${sl('creditsPerWave','Bonus fine ondata',20,200,10)}
                        </div>

                        <div style="background:#060d18;border:1px solid #1a3050;border-radius:8px;padding:12px">
                            <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#4a9eff;
                                        margin-bottom:10px;text-transform:uppercase">Dimensione mappa</div>
                            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px">
                                ${[['1','Piccola'],['2','Media'],['3','Grande'],['4','Enorme']].map(([v,l]) => `
                                <button class="arcade-btn" style="font-size:11px;padding:6px 0;
                                    ${c.mapSize==v&&!c.mapCols?'border-color:#4a9eff;color:#4a9eff':''}"
                                    onclick="if(window._exttoTD.waveRunning)return;
                                        window._exttoTD.cfg.mapSize=${v};
                                        window._exttoTD.cfg.mapCols=0;window._exttoTD.cfg.mapRows=0;
                                        window._exttoTD._saveCfg();window._exttoTD._applySize();
                                        clearInterval(window._exttoTD.gameLoop);
                                        window._exttoTD.generateMap();window._exttoTD.build();
                                        document.getElementById('td-cfg-modal')?.remove()">${l}</button>
                                `).join('')}
                            </div>
                            <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#4a9eff;
                                        margin-bottom:10px;text-transform:uppercase">Nemici</div>
                            ${sl('enemyHpBase','HP base',10,100,5)}
                            ${sl('enemyHpWave','HP +/ondata',5,40,5)}
                            ${sl('enemySpeedBase','Velocità base',10,60,2)}
                            ${sl('enemySpeedWave','Velocità +/ondata',0,10)}
                        </div>
                    </div>

                    <!-- Riga 2: Torrette base (4 colonne) -->
                    <div>
                        <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#5a8ab0;
                                    margin-bottom:10px;text-transform:uppercase">Torrette — Danno</div>
                        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
                            ${towerBlock('🔵 Base','#4a9eff',[
                                ['basicCost','Costo',20,150,5],
                                ['basicDmg','Danno',5,50,5],
                                ['basicRate','Tick fuoco',5,60,5],
                                ['basicRange','Raggio',1,8,0.5]
                            ])}
                            ${towerBlock('🟢 Rapid','#2adf8f',[
                                ['rapidCost','Costo',30,150,5],
                                ['rapidDmg','Danno',2,30,2],
                                ['rapidRate','Tick fuoco',5,30,5],
                                ['rapidRange','Raggio',1,6,0.5]
                            ])}
                            ${towerBlock('🟣 Sniper','#ff4acf',[
                                ['sniperCost','Costo',50,250,10],
                                ['sniperDmg','Danno',10,100,5],
                                ['sniperRate','Tick fuoco',20,120,5],
                                ['sniperRange','Raggio',2,12,0.5]
                            ])}
                            ${towerBlock('🟡 Slow','#aaff4a',[
                                ['slowCost','Costo',40,200,10],
                                ['slowDmg','Danno',0,20,2],
                                ['slowRate','Tick fuoco',10,60,5],
                                ['slowRange','Raggio',1,6,0.5],
                                ['slowFactor','Rallent.',0.1,0.9,0.05]
                            ])}
                            ${towerBlock('🔴 AoE','#ffaa4a',[
                                ['aoeCost','Costo',80,300,10],
                                ['aoeDmg','Danno',5,60,5],
                                ['aoeRate','Tick fuoco',20,100,5],
                                ['aoeRange','Raggio det.',1,6,0.5],
                                ['aoeRadius','Raggio area',0.5,4,0.25]
                            ])}
                        </div>
                    </div>

                    <!-- Riga 2b: Torrette speciali (3 colonne) -->
                    <div>
                        <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#5a8ab0;
                                    margin-bottom:10px;text-transform:uppercase">Torrette — Speciali</div>
                        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
                            ${towerBlock('⭐ Crit','#ffd700',[
                                ['critCost','Costo',80,250,10],
                                ['critDmg','Danno',5,40,5],
                                ['critRate','Tick fuoco',15,60,5],
                                ['critRange','Raggio',1,6,0.5],
                                ['critAura','Raggio aura',1,5,0.5],
                                ['critBonus','Bonus crit %',0.1,0.7,0.05]
                            ])}
                            ${towerBlock('❄️ Frost','#7adfff',[
                                ['frostCost','Costo',80,250,10],
                                ['frostDmg','Danno',0,20,2],
                                ['frostRate','Tick fuoco',10,60,5],
                                ['frostRange','Raggio',1,6,0.5],
                                ['frostRadius','Raggio frost',0.5,4,0.25],
                                ['frostFactor','Rallent. frost',0.1,0.8,0.05]
                            ])}
                            <div style="background:#060d18;border:1px solid #1a3050;border-radius:8px;padding:12px">
                                <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#ffd700;margin-bottom:10px;text-transform:uppercase">⭐ Critico Globale</div>
                                ${sl('baseCritChance','Chance base crit',0.02,0.30,0.02)}
                                ${sl('critMultiplier','Moltiplicatore x',1.5,5.0,0.1)}
                            </div>
                        </div>
                    <!-- Riga 3: Upgrade moltiplicatori -->
                    <div style="display:grid;grid-template-columns:1fr;gap:16px">
                        <div style="background:#060d18;border:1px solid #1a3050;border-radius:8px;padding:12px">
                            <div style="font-size:11px;font-weight:700;letter-spacing:1px;color:#4a9eff;margin-bottom:10px;text-transform:uppercase">▲ Upgrade (moltiplicatori)</div>
                            <div style="font-size:10px;color:#5a8ab0;margin-bottom:8px">Lv2 → Lv3 per danno, fuoco (minore=meglio), raggio, costo upgrade</div>
                            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;font-size:10px;color:#8ab0d0">
                                <div>Danno Lv2: <span style="color:#2adf8f">×${(this.cfg.upgDmgMul[1]||1.5).toFixed(2)}</span></div>
                                <div>Fuoco Lv2: <span style="color:#4a9eff">×${(this.cfg.upgRateMul[1]||0.72).toFixed(2)}</span></div>
                                <div>Raggio Lv2: <span style="color:#ffaa4a">×${(this.cfg.upgRangeMul[1]||1.25).toFixed(2)}</span></div>
                                <div>Danno Lv3: <span style="color:#2adf8f">×${(this.cfg.upgDmgMul[2]||2.2).toFixed(2)}</span></div>
                                <div>Fuoco Lv3: <span style="color:#4a9eff">×${(this.cfg.upgRateMul[2]||0.52).toFixed(2)}</span></div>
                                <div>Raggio Lv3: <span style="color:#ffaa4a">×${(this.cfg.upgRangeMul[2]||1.55).toFixed(2)}</span></div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        `;

        modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
        document.body.appendChild(modal);
    }
    _saveCfg() { LS.set('td_cfg', this.cfg); }
    _resetCfg() {
        LS.set('td_cfg', null);
        const def = {
            startCredits: 300, carryoverPct: 50, livesStart: 20, mapSize: 3,
            basicDmg: 12, basicRate: 30, basicRange: 2.5, basicCost: 50,
            rapidDmg: 6, rapidRate: 12, rapidRange: 2.0, rapidCost: 80,
            sniperDmg: 35, sniperRate: 70, sniperRange: 5.0, sniperCost: 120,
            slowDmg: 4, slowRate: 25, slowRange: 2.2, slowCost: 90, slowFactor: 0.45,
            aoeDmg: 18, aoeRate: 50, aoeRange: 1.8, aoeCost: 150, aoeRadius: 1.2,
            critDmg: 10, critRate: 28, critRange: 2.5, critCost: 110, critAura: 2.5, critBonus: 0.35,
            frostDmg: 5, frostRate: 20, frostRange: 2.8, frostCost: 130, frostRadius: 1.8, frostFactor: 0.30,
            baseCritChance: 0.08, critMultiplier: 2.5,
            upgDmgMul: [1, 1.5, 2.2], upgRateMul: [1, 0.72, 0.52], upgRangeMul: [1, 1.25, 1.55], upgCostMul: [0, 0.6, 1.0],
            enemyHpBase: 25, enemyHpWave: 12, enemySpeedBase: 26, enemySpeedWave: 3,
            creditsPerKill: 8, creditsPerWave: 60, creditsPerWaveWave: 12
        };
        Object.assign(this.cfg, def);
        this._buildTowerDefs();
        document.getElementById('td-cfg-modal')?.remove();
    }
    _applySize() {
        const CELL_SIZE = 20;
        const SZ = { 1: { c: 18, r: 12 }, 2: { c: 28, r: 18 }, 3: { c: 38, r: 24 }, 4: { c: 50, r: 30 } };
        const sz = SZ[this.cfg.mapSize] || SZ[2];
        this.COLS = this.cfg.mapCols > 0 ? this.cfg.mapCols : sz.c;
        this.ROWS = this.cfg.mapRows > 0 ? this.cfg.mapRows : sz.r;
        this.CELL = CELL_SIZE;
    }

    setSpeed(v) {
        this.speed = v;
        document.querySelectorAll('.td-speed-btn').forEach(b => {
            const active = +b.dataset.speed === v;
            b.style.borderColor = active ? '#4a9eff' : '#1e3a5f';
            b.style.color = active ? '#4a9eff' : '#5a8ab0';
        });
        clearInterval(this.gameLoop);
        this.gameLoop = setInterval(() => this.tick(), Math.round(50 / v));
    }

    selectTower(type) {
        this.selected = type;
        this.sellMode = false;
        // Aggiorna stile sell button
        const sb = document.getElementById('td-btn-sell');
        if (sb) { sb.style.background = ''; sb.style.borderColor = '#ffaa4a'; sb.style.color = '#ffaa4a'; sb.textContent = '🔧 VENDI [V]'; }
        document.getElementById('td-canvas').style.cursor = 'crosshair';
        document.querySelectorAll('.td-tower-card').forEach(c => c.classList.remove('selected'));
        const card = document.getElementById(`td-card-${type}`);
        if (card) card.classList.add('selected');
    }

    // ── Upgrade helpers ───────────────────────────────────────────────────
    _upgradeCost(tower) {
        const lv = tower.level || 1;
        if (lv >= 3) return null;
        const mul = this.cfg.upgCostMul[lv];  // lv=1→mul[1], lv=2→mul[2]
        return Math.round(tower.baseCost * mul);
    }

    _applyUpgradeStats(tower) {
        const lv = (tower.level || 1) - 1;  // 0-based index
        const def = this.TOWER_DEFS[tower.type];
        tower.dmg   = Math.round(def.dmg   * this.cfg.upgDmgMul[lv]);
        tower.rate  = Math.round(def.rate  * this.cfg.upgRateMul[lv]);
        tower.range = parseFloat((def.range * this.cfg.upgRangeMul[lv]).toFixed(2));
        // Upgrade poteri speciali
        if (tower.type === 'slow')  tower.slowFactor  = Math.max(0.15, this.cfg.slowFactor  - lv * 0.08);
        if (tower.type === 'crit')  tower.critBonus   = Math.min(0.80, (def.critBonus || this.cfg.critBonus)  + lv * 0.15);
        if (tower.type === 'frost') tower.frostFactor = Math.max(0.10, (def.frostFactor || this.cfg.frostFactor) - lv * 0.08);
        if (tower.type === 'frost') tower.frostRadius = parseFloat(((def.frostRadius || this.cfg.frostRadius) * this.cfg.upgRangeMul[lv]).toFixed(2));
        if (tower.type === 'aoe')   tower.aoeRadius   = parseFloat((this.cfg.aoeRadius * this.cfg.upgRangeMul[lv]).toFixed(2));
        if (tower.type === 'crit')  tower.aura        = parseFloat(((def.aura || this.cfg.critAura) * this.cfg.upgRangeMul[lv]).toFixed(2));
    }

    _openUpgradePopup(tower) {
        document.getElementById('td-upgrade-popup')?.remove();
        const lv = tower.level || 1;
        const cost = this._upgradeCost(tower);
        const def  = this.TOWER_DEFS[tower.type];
        const canAfford = cost !== null && this.credits >= cost;

        const popup = document.createElement('div');
        popup.id = 'td-upgrade-popup';
        popup.style.cssText = `
            position:fixed;z-index:100002;background:#060d18;border:1px solid #2a5a8f;
            border-radius:8px;padding:12px 16px;font-family:monospace;font-size:11px;
            color:#c0d8f0;min-width:180px;box-shadow:0 6px 24px rgba(0,0,0,0.7);
        `;

        const stars = '★'.repeat(lv) + '☆'.repeat(3 - lv);
        const nextLvStats = lv < 3 ? (() => {
            const ni = lv;  // next index (0-based)
            return {
                dmg:   Math.round(def.dmg   * this.cfg.upgDmgMul[ni]),
                rate:  Math.round(def.rate  * this.cfg.upgRateMul[ni]),
                range: (def.range * this.cfg.upgRangeMul[ni]).toFixed(1),
            };
        })() : null;

        popup.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-size:13px;font-weight:700;color:${def.barrel}">${def.icon} ${def.label}</span>
                <span style="color:#ffd700;font-size:13px">${stars}</span>
            </div>
            <div style="color:#5a8ab0;font-size:10px;margin-bottom:8px">${def.desc}</div>
            <div style="display:flex;flex-direction:column;gap:3px;margin-bottom:10px">
                <div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">Danno</span><span style="color:#4a9eff">${tower.dmg}${nextLvStats ? ` → <span style="color:#2adf8f">${nextLvStats.dmg}</span>` : ''}</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">Tick fuoco</span><span style="color:#4a9eff">${tower.rate}${nextLvStats ? ` → <span style="color:#2adf8f">${nextLvStats.rate}</span>` : ''}</span></div>
                <div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">Raggio</span><span style="color:#4a9eff">${tower.range.toFixed(1)}${nextLvStats ? ` → <span style="color:#2adf8f">${nextLvStats.range}</span>` : ''}</span></div>
                ${tower.type === 'slow'  ? `<div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">Rallent.</span><span style="color:#aaff4a">${Math.round((1-tower.slowFactor)*100)}%</span></div>` : ''}
                ${tower.type === 'crit'  ? `<div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">+Crit aura</span><span style="color:#ffd700">+${Math.round((tower.critBonus||0)*100)}%</span></div>` : ''}
                ${tower.type === 'frost' ? `<div style="display:flex;justify-content:space-between"><span style="color:#5a8ab0">Frost %</span><span style="color:#7adfff">${Math.round((1-(tower.frostFactor||0.3))*100)}%</span></div>` : ''}
            </div>
            ${cost !== null ? `
            <button id="td-upg-btn" class="arcade-btn arcade-btn-sm ${canAfford ? 'arcade-btn-green' : ''}"
                style="width:100%;margin-bottom:6px;${!canAfford ? 'opacity:0.5;cursor:default' : ''}">
                ▲ Upgrade Lv${lv+1} — ${cost}c
            </button>` : `<div style="color:#3a6a3a;text-align:center;margin-bottom:6px;font-size:10px">★ LIVELLO MASSIMO ★</div>`}
            <button id="td-upg-sell" class="arcade-btn arcade-btn-sm arcade-btn-yellow" style="width:100%">
                🔧 Vendi (${Math.round(tower.cost * 0.6)}c)
            </button>
            <div style="font-size:9px;color:#2a4a6a;text-align:center;margin-top:6px">Click fuori per chiudere</div>
        `;

        // Posiziona vicino alla torretta sul canvas
        const cvs = document.getElementById('td-canvas');
        if (cvs) {
            const r = cvs.getBoundingClientRect();
            let px = r.left + tower.cx * this.CELL + this.CELL + 4;
            let py = r.top  + tower.cy * this.CELL;
            if (px + 200 > window.innerWidth)  px = r.left + tower.cx * this.CELL - 200;
            if (py + 260 > window.innerHeight) py = window.innerHeight - 270;
            popup.style.left = px + 'px';
            popup.style.top  = py + 'px';
        }

        document.body.appendChild(popup);

        if (cost !== null && canAfford) {
            document.getElementById('td-upg-btn').addEventListener('click', () => {
                if (this.credits < cost) return;
                this.credits -= cost;
                tower.level = lv + 1;
                tower.cost  = (tower.baseCost || tower.cost) + cost;  // costo totale investito (per vendita)
                this._applyUpgradeStats(tower);
                document.getElementById('td-credits').textContent = this.credits;
                popup.remove();
                spawnScorePop(document.getElementById('td-canvas-wrap'), `▲ Lv${tower.level}`, '#ffd700');
            });
        }
        document.getElementById('td-upg-sell').addEventListener('click', () => {
            this.sellTowerObj(tower);
            popup.remove();
        });

        const closeOutside = e => {
            if (!popup.contains(e.target)) { popup.remove(); document.removeEventListener('mousedown', closeOutside); }
        };
        setTimeout(() => document.addEventListener('mousedown', closeOutside), 10);
    }

    placeTower(cx, cy) {
        if (this.sellMode) { this.sellTower(cx, cy); return; }
        if (cx < 0 || cx >= this.COLS || cy < 0 || cy >= this.ROWS) return;

        // Click su torretta esistente → apri popup upgrade
        const existing = this.towers.find(t => t.cx === cx && t.cy === cy);
        if (existing) { this._openUpgradePopup(existing); return; }

        if (this.pathSet.has(`${cx},${cy}`)) return;
        const def = this.TOWER_DEFS[this.selected];
        if (this.credits < def.cost) return;
        this.credits -= def.cost;
        const tower = {
            cx, cy, type: this.selected,
            level: 1, baseCost: def.cost,
            cost: def.cost, timer: 0, angle: 0,
            aoeRadius: this.cfg.aoeRadius,
            slowFactor: this.cfg.slowFactor,
            critBonus: def.critBonus || this.cfg.critBonus,
            frostRadius: def.frostRadius || this.cfg.frostRadius,
            frostFactor: def.frostFactor || this.cfg.frostFactor,
            aura: def.aura || this.cfg.critAura,
        };
        this._applyUpgradeStats(tower);
        this.towers.push(tower);
        document.getElementById('td-credits').textContent = this.credits;
    }

    sellTower(cx, cy) {
        const tower = this.towers.find(t => t.cx === cx && t.cy === cy);
        if (!tower) return;
        this.sellTowerObj(tower);
    }

    sellTowerObj(tower) {
        const idx = this.towers.indexOf(tower);
        if (idx === -1) return;
        const refund = Math.round(tower.cost * 0.6);
        this.towers.splice(idx, 1);
        this.credits += refund;
        document.getElementById('td-credits').textContent = this.credits;
        const wrap = document.getElementById('td-canvas-wrap');
        if (wrap) spawnScorePop(wrap, `+${refund}c`, '#ffaa4a');
    }

    startWave() {
        if (this.waveRunning) return;
        this.waveStarted = true; this.waveRunning = true;
        this.toSpawn = 5 + Math.floor(this.wave * 2.2);
        this.spawnTimer = 0;
        const b = document.getElementById('td-btn-wave');
        const s = document.getElementById('td-btn-stop');
        if (b) { b.textContent = '⏳ IN CORSO...'; b.disabled = true; }
        if (s) s.style.display = 'inline-block';
        // Log ondata: composizione nemica
        this._showWaveLog();
    }

    _showWaveLog() {
        const w = this.wave;
        const isBossWave = w % 5 === 0;
        const parts = [];
        if (w > 8)  parts.push('◆ Scudo');
        if (w > 6)  parts.push('⟳ Regen');
        if (w > 5)  parts.push('■ Armato');
        if (w > 3)  parts.push('▶ Veloce');
        if (isBossWave) parts.push('★ BOSS');
        const total = 5 + Math.floor(w * 2.2);
        const dual  = this.dualLane ? ' ⚡×2' : '';
        const msg   = `Ondata ${w} — ${total} nemici${dual}${parts.length ? ' · '+parts.join(' ') : ''}`;
        // Mostra banner sopra il canvas per 2.5s
        const wrap = document.getElementById('td-canvas-wrap');
        if (!wrap) return;
        const existing = document.getElementById('td-wave-log');
        if (existing) existing.remove();
        const el = document.createElement('div');
        el.id = 'td-wave-log';
        el.style.cssText = `
            position:absolute;top:6px;left:50%;transform:translateX(-50%);
            background:rgba(4,8,18,0.88);border:1px solid #2a5a8f;
            border-radius:5px;padding:4px 12px;font-family:monospace;
            font-size:10px;color:#8ab8d0;white-space:nowrap;
            z-index:10;pointer-events:none;
            animation:scorePop 2.5s ease-out forwards;
        `;
        el.textContent = msg;
        wrap.appendChild(el);
        setTimeout(() => el.remove(), 2600);
    }

    stopGame() {
        if (!this.waveRunning) return;
        this.waveRunning = false;
        this.enemies = []; this.bullets = []; this.toSpawn = 0;
        const btn = document.getElementById('td-btn-wave');
        const s = document.getElementById('td-btn-stop');
        if (btn) { btn.textContent = '▶ ONDATA'; btn.disabled = false; }
        if (s) s.style.display = 'none';
        this.wave = Math.max(1, this.wave - 1);
        const wd = document.getElementById('td-wave');
        if (wd) wd.textContent = this.wave;
    }

    _spawnEnemy(path) {
        if (!path || path.length === 0) return;
        const w = this.wave;
        const roll = Math.random();
        const isBoss     = (w % 5 === 0 && this.toSpawn === 1);
        const isShielded = !isBoss && w > 8  && roll < 0.12;
        const isRegen    = !isBoss && !isShielded && w > 6  && roll < 0.22;
        const isArmored  = !isBoss && !isShielded && !isRegen && w > 5 && roll < 0.35;
        const isFast     = !isBoss && !isShielded && !isRegen && !isArmored && w > 3 && roll < 0.5;

        const hpMult  = isBoss ? 5 : isShielded ? 3 : isArmored ? 2.5 : 1;
        const spdMult = isFast ? 1.7 : isBoss ? 0.6 : isShielded ? 0.85 : 1;
        const hp = Math.round((this.cfg.enemyHpBase + w * this.cfg.enemyHpWave) * hpMult);
        const spd = ((this.cfg.enemySpeedBase + w * this.cfg.enemySpeedWave) / 1000) * spdMult;
        const [sc, sr] = path[0];
        const enemy = {
            path,   // riferimento al percorso di questa lane
            progress: 0, hp, maxHp: hp, speed: spd,
            fast: isFast, armored: isArmored, boss: isBoss,
            regen: isRegen, shielded: isShielded,
            shield: isShielded ? Math.round(hp * 0.4) : 0,  // scudo assorbito separatamente
            regenTimer: 0,
            x: sc * this.CELL + this.CELL / 2,
            y: sr * this.CELL + this.CELL / 2,
            slowTimer: 0  // tick rimanenti di rallentamento
        };
        this.enemies.push(enemy);
    }

    tick() {
        if (this.waveRunning && this.toSpawn > 0) {
            this.spawnTimer++;
            if (this.spawnTimer >= 22) {
                this.spawnTimer = 0; this.toSpawn--;
                this._spawnEnemy(this.path);
                // Dual-lane: ogni 2 spawn, spawna anche sulla lane 2
                if (this.dualLane && this.path2 && this.toSpawn % 2 === 0) {
                    this._spawnEnemy(this.path2);
                }
            }
        }

        // ── Aura Frost passiva: tutti i nemici nel raggio di torrette frost rallentano sempre ──
        this.enemies.forEach(e => { e.frostActive = false; e.frostFactor = 1; });
        this.towers.forEach(t => {
            if (t.type !== 'frost') return;
            const tx = t.cx * this.CELL + this.CELL / 2, ty = t.cy * this.CELL + this.CELL / 2;
            const frostRSq = (t.frostRadius * this.CELL) ** 2;
            this.enemies.forEach(e => {
                const dx = e.x - tx, dy = e.y - ty;
                if (dx*dx + dy*dy <= frostRSq) {
                    e.frostActive = true;
                    // Prendi il frost factor più severo (più basso) tra le torri in range
                    const tf = t.frostFactor || this.cfg.frostFactor;
                    if (tf < e.frostFactor) e.frostFactor = tf;
                }
            });
        });

        // ── Calcola critChance effettiva per ogni nemico (base + aura crit) ──
        this.enemies.forEach(e => {
            e.critChance = this.cfg.baseCritChance;
        });
        this.towers.forEach(t => {
            if (t.type !== 'crit') return;
            const tx = t.cx * this.CELL + this.CELL / 2, ty = t.cy * this.CELL + this.CELL / 2;
            const auraSq = (t.aura * this.CELL) ** 2;
            this.enemies.forEach(e => {
                const dx = e.x - tx, dy = e.y - ty;
                if (dx*dx + dy*dy <= auraSq) e.critChance = Math.min(0.95, (e.critChance || 0) + (t.critBonus || 0));
            });
        });

        // ── Muovi nemici ──
        this.enemies = this.enemies.filter(e => {
            if (e.regen) {
                e.regenTimer++;
                if (e.regenTimer >= 60) { e.regenTimer = 0; e.hp = Math.min(e.maxHp, e.hp + Math.ceil(e.maxHp * 0.05)); }
            }
            // Frost passivo ha priorità sul slow timer
            let spdMul = 1;
            if (e.frostActive) spdMul = e.frostFactor;  // valore dalla torre più vicina
            else if (e.slowTimer > 0) { spdMul = this.cfg.slowFactor; e.slowTimer--; }

            e.progress += e.speed * spdMul;
            const idx = Math.floor(e.progress);
            if (idx >= e.path.length - 1) {
                this.lives--;
                const l = document.getElementById('td-lives');
                if (l) { l.textContent = this.lives; l.style.color = this.lives <= 5 ? '#ff4a4a' : '#2adf8f'; }
                this._flashCanvas();
                if (this.lives <= 0) { this.gameOver(); return false; }
                return false;
            }
            const [c1, r1] = e.path[idx], [c2, r2] = e.path[Math.min(idx + 1, e.path.length - 1)];
            const t = e.progress - idx;
            e.x = (c1 + (c2 - c1) * t) * this.CELL + this.CELL / 2;
            e.y = (r1 + (r2 - r1) * t) * this.CELL + this.CELL / 2;

            // ── Checkpoint damage ──
            if (this.checkpoints) {
                const ck = `${Math.round(c1 + (c2-c1)*t)},${Math.round(r1 + (r2-r1)*t)}`;
                if (this.checkpoints.has(ck) && e._lastCp !== ck) {
                    e._lastCp = ck;
                    const cpDmg = Math.round(e.maxHp * 0.12);
                    this._damageEnemy(e, cpDmg, false);
                    this._spawnParticles(e.x, e.y, 4, '#ffd700');
                }
            }
            return true;
        });

        // ── Torrette: aggiorna angolo ogni tick + sparo quando pronto ──
        this.towers.forEach(t => {
            if (t.type === 'frost') return;

            const tx = t.cx * this.CELL + this.CELL / 2, ty = t.cy * this.CELL + this.CELL / 2;
            const rangeSq = (t.range * this.CELL) ** 2;

            // Trova bersaglio prioritario (più avanzato nel path) nel range
            const targetObj = this.enemies.reduce((best, e) => {
                const dx = e.x - tx, dy = e.y - ty, dSq = dx*dx + dy*dy;
                if (dSq > rangeSq) return best;
                if (!best || e.progress > best.e.progress) return { e, dSq };
                return best;
            }, null);

            // Aggiorna angolo cannone ogni tick (tracking fluido)
            if (targetObj) {
                t.angle = Math.atan2(targetObj.e.y - ty, targetObj.e.x - tx);
            }

            // Sparo solo quando il timer è pronto
            t.timer++; if (t.timer < t.rate) return;

            if (t.type === 'aoe') {
                const aoeTarget = this.enemies.find(e => {
                    const dx = e.x - tx, dy = e.y - ty;
                    return (dx*dx + dy*dy) <= rangeSq;
                });
                if (aoeTarget) {
                    t.timer = 0;
                    const aoeRSq = ((t.aoeRadius || this.cfg.aoeRadius) * this.CELL) ** 2;
                    this.enemies.forEach(e => {
                        const dx = e.x - tx, dy = e.y - ty;
                        if ((dx*dx + dy*dy) <= aoeRSq) this._damageEnemy(e, t.dmg);
                    });
                    this.bullets.push({ x: tx, y: ty, target: aoeTarget, dmg: 0, speed: 12, color: t.barrel, aoe: true });
                }
            } else if (t.type === 'crit') {
                if (targetObj) {
                    t.timer = 0;
                    const forcedCrit = Math.random() < 0.55;
                    this.bullets.push({ x: tx, y: ty, target: targetObj.e, dmg: t.dmg, speed: 9, color: t.barrel, forcedCrit });
                }
            } else {
                if (targetObj) {
                    t.timer = 0;
                    this.bullets.push({ x: tx, y: ty, target: targetObj.e, dmg: t.dmg, speed: 9, color: t.barrel, slowTower: t.type === 'slow' });
                }
            }
        });

        // ── Proiettili ──
        this.bullets = this.bullets.filter(b => {
            if (!b.target || b.target.hp <= 0) return false;
            const dx = b.target.x - b.x, dy = b.target.y - b.y, d = Math.hypot(dx, dy);
            if (d < b.speed) {
                if (!b.aoe) {
                    if (b.slowTower) b.target.slowTimer = 40;
                    // Calcola critico
                    const critRoll = Math.random();
                    const isCrit = b.forcedCrit || (critRoll < (b.target.critChance || this.cfg.baseCritChance));
                    const finalDmg = isCrit ? Math.round(b.dmg * this.cfg.critMultiplier) : b.dmg;
                    this._damageEnemy(b.target, finalDmg, isCrit);
                }
                return false;
            }
            b.x += dx / d * b.speed; b.y += dy / d * b.speed;
            return true;
        });
        this.enemies = this.enemies.filter(e => e.hp > 0);

        // ── Aggiorna particelle ──
        if (this.particles) {
            this.particles = this.particles.filter(p => {
                p.age++;
                p.x += p.vx; p.y += p.vy; p.vy += 0.3;
                return p.age < p.life;
            });
        }

        if (this.waveRunning && this.toSpawn === 0 && this.enemies.length === 0) {
            this.waveRunning = false; this.wave++;
            this.credits += this.cfg.creditsPerWave + this.wave * this.cfg.creditsPerWaveWave;
            const wd = document.getElementById('td-wave'), cd = document.getElementById('td-credits');
            const btn = document.getElementById('td-btn-wave'), stopBtn = document.getElementById('td-btn-stop');
            if (wd) wd.textContent = this.wave;
            if (cd) cd.textContent = this.credits;
            if (stopBtn) stopBtn.style.display = 'none';
            if (btn) { btn.textContent = '▶ ONDATA'; btn.disabled = false; }
            if (this.score > this.hi) { this.hi = this.score; LS.set('td_hi', this.hi); const hd = document.getElementById('td-hi'); if (hd) hd.textContent = this.hi; }
            // Aggiorna label mappa
            const tierEl = document.getElementById('td-tier');
            if (tierEl) tierEl.textContent = this._tierLabel();
            // Ogni wave, rigenera la mappa automaticamente (nuova mappa = nuovo layout/difficoltà)
            if (this.wave > 1) {
                this._autoRegenMap();
            }
        }
        this.draw();
    }

    _damageEnemy(e, dmg, isCrit = false) {
        if (e.shield > 0) {
            const absorbed = Math.min(e.shield, dmg);
            e.shield -= absorbed; dmg -= absorbed;
        }
        e.hp -= dmg;
        if (e.hp <= 0) {
            const bonus = e.boss ? 50 : e.shielded ? 25 : e.armored ? 20 : e.regen ? 18 : this.cfg.creditsPerKill;
            const pts   = e.boss ? 100 : e.shielded ? 40 : e.armored ? 30 : e.regen ? 25 : 10;
            this.score += pts; this.credits += bonus;
            this.totalKills = (this.totalKills || 0) + 1;
            const cs = document.getElementById('td-credits'), ss = document.getElementById('td-score');
            if (cs) cs.textContent = this.credits;
            if (ss) ss.textContent = this.score;
            // Particelle di morte
            this._spawnParticles(e.x, e.y, e.boss ? 14 : e.armored ? 10 : 7,
                e.boss ? '#ffdf4a' : e.shielded ? '#4af0ff' : e.armored ? '#c04aff' : e.regen ? '#44ffaa' : '#ff4a4a');
        } else if (isCrit) {
            // Particelle critico (stelle dorate)
            this._spawnParticles(e.x, e.y, 5, '#ffd700', true);
        }
    }

    _spawnParticles(x, y, count, color, crit = false) {
        if (!this.particles) this.particles = [];
        for (let i = 0; i < count; i++) {
            const angle = Math.random() * Math.PI * 2;
            const speed = crit ? (1.5 + Math.random() * 3) : (0.8 + Math.random() * 2.5);
            this.particles.push({
                x, y,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed - (crit ? 2 : 0.5),
                color, age: 0,
                life: crit ? 18 : 22,
                r: crit ? 3 : (2 + Math.random() * 2),
                crit,
            });
        }
    }

    _autoRegenMap() {
        this.mapsPlayed = (this.mapsPlayed || 1) + 1;
        // Rimuovi overlay game over se presente
        document.getElementById('td-gameover-overlay')?.remove();
        // Animazione flash bianco sul canvas
        const wrap = document.getElementById('td-canvas-wrap');
        if (wrap) {
            const flash = document.createElement('div');
            flash.style.cssText = 'position:absolute;inset:0;background:rgba(255,255,255,0.18);pointer-events:none;border-radius:2px;transition:opacity 0.6s';
            wrap.appendChild(flash);
            setTimeout(() => { flash.style.opacity='0'; setTimeout(()=>flash.remove(), 600); }, 80);
        }
        // Rigenera mappa: le torrette si perdono (carry-over crediti parziale)
        const towerValue = this.towers.reduce((s, t) => s + Math.round(t.cost * 0.5), 0);
        this.carryCredits = Math.round((this.credits + towerValue) * (this.cfg.carryoverPct / 100));
        this.towers = []; this.bullets = []; this.enemies = []; this.particles = [];
        this.hoveredTower = null; this.mouseCell = null; this.sellMode = false;
        this._applySize();
        this.generateMap();
        // Ricostruisci solo canvas e path, non tutto il DOM
        const cvs = document.getElementById('td-canvas');
        if (cvs) {
            cvs.width  = this.COLS * this.CELL;
            cvs.height = this.ROWS * this.CELL;
            this.ctx = cvs.getContext('2d');
        }
        this.credits = Math.round(this.cfg.startCredits * 0.3 + this.carryCredits);
        const cd = document.getElementById('td-credits');
        if (cd) cd.textContent = this.credits;
        const tierEl = document.getElementById('td-tier');
        if (tierEl) tierEl.textContent = this._tierLabel();
        // Banner notifica cambio mappa
        spawnScorePop(wrap || document.body, `🗺 ${this.mapLabel}`, '#4a9eff');
        this.waveStarted = false;
        this.draw();
    }

    _flashCanvas() {
        const wrap = document.getElementById('td-canvas-wrap');
        if (!wrap) return;
        const flash = document.createElement('div');
        flash.className = 'canvas-flash';
        wrap.appendChild(flash);
        setTimeout(() => flash.remove(), 380);
    }

    draw() {
        const c = this.ctx, CS = this.CELL, W = this.COLS * CS, H = this.ROWS * CS;

        // ── Mappa (sfondo, percorso, deco, checkpoint) ──
        this._drawTick = (this._drawTick || 0) + 1;
        TDMapSystem.drawMap(c, {
            path: this.path, path2: this.path2,
            altPaths: this.altPaths, checkpoints: this.checkpoints,
            deco: this.mapDeco, theme: this.mapTheme,
            COLS: this.COLS, ROWS: this.ROWS,
        }, CS, this._drawTick);

        // Freccia pre-ondata (sopra la mappa)
        if (!this.waveStarted && this.path && this.path.length > 2) {
            c.save(); c.globalAlpha = 0.55;
            const p0 = this.path[0], pN = this.path[this.path.length-1];
            const grad = c.createLinearGradient(p0[0]*CS, p0[1]*CS, pN[0]*CS, pN[1]*CS);
            grad.addColorStop(0,'#00ff88'); grad.addColorStop(0.5,'#00aaff'); grad.addColorStop(1,'#ff4444');
            c.strokeStyle=grad; c.lineWidth=CS*0.28; c.lineCap='round'; c.lineJoin='round';
            c.beginPath(); c.moveTo(p0[0]*CS+CS/2, p0[1]*CS+CS/2);
            for (let i=1; i<this.path.length; i++) c.lineTo(this.path[i][0]*CS+CS/2, this.path[i][1]*CS+CS/2);
            c.stroke();
            c.globalAlpha=0.88; c.fillStyle='#fff';
            c.font=`bold ${Math.max(11,Math.round(CS*0.52))}px monospace`;
            c.textAlign='center';
            c.fillText('▶ AVVIA ONDATA', W/2, H/2);
            if (this.dualLane) {
                c.font=`${Math.max(9,Math.round(CS*0.38))}px monospace`;
                c.fillStyle='rgba(100,255,180,0.85)';
                c.fillText('⚡ MAPPA DUAL-LANE', W/2, H/2+CS);
            }
            c.restore();
        }

        // ── Aure frost (anello azzurro pulsante) ──
        this.towers.forEach(t => {
            if (t.type !== 'frost') return;
            const tx = t.cx * CS + CS / 2, ty = t.cy * CS + CS / 2;
            const fr = (t.frostRadius || this.cfg.frostRadius) * CS;
            c.save();
            c.globalAlpha = 0.10 + 0.05 * Math.sin(Date.now() / 400);
            c.fillStyle = '#7adfff';
            c.beginPath(); c.arc(tx, ty, fr, 0, Math.PI * 2); c.fill();
            c.globalAlpha = 0.35;
            c.strokeStyle = '#7adfff'; c.lineWidth = 1.5;
            c.beginPath(); c.arc(tx, ty, fr, 0, Math.PI * 2); c.stroke();
            c.restore();
        });

        // ── Aure crit (anello dorato pulsante) ──
        this.towers.forEach(t => {
            if (t.type !== 'crit') return;
            const tx = t.cx * CS + CS / 2, ty = t.cy * CS + CS / 2;
            const ar = (t.aura || this.cfg.critAura) * CS;
            c.save();
            c.globalAlpha = 0.08 + 0.04 * Math.sin(Date.now() / 300);
            c.fillStyle = '#ffd700';
            c.beginPath(); c.arc(tx, ty, ar, 0, Math.PI * 2); c.fill();
            c.globalAlpha = 0.30;
            c.strokeStyle = '#ffd700'; c.lineWidth = 1.5;
            c.setLineDash([4, 4]);
            c.beginPath(); c.arc(tx, ty, ar, 0, Math.PI * 2); c.stroke();
            c.setLineDash([]);
            c.restore();
        });

        // ── Anteprima range torretta selezionata (mouse su cella vuota) ──
        if (this.mouseCell && !this.pathSet.has(`${this.mouseCell[0]},${this.mouseCell[1]}`)) {
            const hasTower = this.towers.some(t => t.cx === this.mouseCell[0] && t.cy === this.mouseCell[1]);
            if (!hasTower) {
                const def = this.TOWER_DEFS[this.selected];
                if (def) {
                    const px = this.mouseCell[0] * CS + CS / 2, py = this.mouseCell[1] * CS + CS / 2;
                    c.save();
                    c.globalAlpha = 0.15; c.fillStyle = def.barrel;
                    c.beginPath(); c.arc(px, py, def.range * CS, 0, Math.PI * 2); c.fill();
                    c.globalAlpha = 0.4; c.strokeStyle = def.barrel; c.lineWidth = 1;
                    c.beginPath(); c.arc(px, py, def.range * CS, 0, Math.PI * 2); c.stroke();
                    // Anteprima cella
                    c.globalAlpha = 0.25; c.fillStyle = this.credits >= def.cost ? def.color : '#ff4a4a';
                    c.fillRect(this.mouseCell[0] * CS + 1, this.mouseCell[1] * CS + 1, CS - 2, CS - 2);
                    c.restore();
                }
            }
        }

        // ── Torrette ──
        this.towers.forEach(t => {
            const tx = t.cx * CS, ty = t.cy * CS;
            const def = this.TOWER_DEFS[t.type];
            const isHovered = this.hoveredTower === t;
            if (isHovered) {
                c.beginPath(); c.arc(tx + CS / 2, ty + CS / 2, t.range * CS, 0, Math.PI * 2);
                c.strokeStyle = 'rgba(255,255,255,0.25)'; c.lineWidth = 1; c.stroke();
                c.fillStyle = 'rgba(255,255,255,0.05)'; c.fill();
            }
            // Base torretta
            c.fillStyle = '#2a3a2a'; c.fillRect(tx + 2, ty + 2, CS - 4, CS - 4);
            c.fillStyle = def.color; c.fillRect(tx + 5, ty + 5, CS - 10, CS - 10);

            // Bordo livello (oro per lv2, bianco per lv3)
            const lv = t.level || 1;
            if (lv >= 2) {
                c.strokeStyle = lv === 3 ? '#ffffff' : '#ffd700';
                c.lineWidth = lv === 3 ? 2 : 1.5;
                c.strokeRect(tx + 3, ty + 3, CS - 6, CS - 6);
            }

            // Cannone ruotante (tranne frost che non spara)
            if (t.type !== 'frost') {
                c.save();
                c.translate(tx + CS / 2, ty + CS / 2);
                c.rotate(t.angle || 0);
                c.fillStyle = def.barrel;
                c.fillRect(-2, -CS * 0.42, 4, CS * 0.42);
                c.restore();
            }

            // Icone speciali
            c.font = '9px monospace'; c.textAlign = 'center'; c.fillStyle = '#fff';
            if (t.type === 'slow')  c.fillText('S',  tx + CS / 2, ty + CS - 3);
            if (t.type === 'aoe')   c.fillText('A',  tx + CS / 2, ty + CS - 3);
            if (t.type === 'crit')  c.fillText('★',  tx + CS / 2, ty + CS - 3);
            if (t.type === 'frost') c.fillText('❄',  tx + CS / 2, ty + CS / 2 + 4);

            // Stelle upgrade
            if (lv >= 2) {
                c.font = '7px monospace'; c.fillStyle = lv === 3 ? '#fff' : '#ffd700';
                c.fillText('★'.repeat(lv), tx + CS / 2, ty + 9);
            }
        });

        // ── Nemici ──
        this.enemies.forEach(e => {
            const r = e.boss ? 11 : e.shielded ? 9 : e.armored ? 9 : e.fast ? 6 : 8;
            if (e.boss) {
                c.fillStyle = '#ffdf4a'; c.beginPath();
                for (let i = 0; i < 5; i++) { const a = i * Math.PI * 2 / 5 - Math.PI / 2; i === 0 ? c.moveTo(e.x + Math.cos(a) * r, e.y + Math.sin(a) * r) : c.lineTo(e.x + Math.cos(a) * r, e.y + Math.sin(a) * r); }
                c.closePath(); c.fill(); c.strokeStyle = 'rgba(0,0,0,0.4)'; c.lineWidth = 1; c.stroke();
            } else if (e.shielded) {
                c.fillStyle = '#4af0ff';
                c.beginPath(); c.moveTo(e.x, e.y - r); c.lineTo(e.x + r, e.y); c.lineTo(e.x, e.y + r); c.lineTo(e.x - r, e.y); c.closePath(); c.fill();
                c.strokeStyle = 'rgba(200,255,255,0.6)'; c.lineWidth = 1.5; c.stroke();
                if (e.shield > 0) {
                    const bw = r * 2 + 4;
                    c.fillStyle = 'rgba(0,0,0,0.5)'; c.fillRect(e.x - bw / 2, e.y - r - 13, bw, 3);
                    c.fillStyle = '#4af0ff'; c.fillRect(e.x - bw / 2, e.y - r - 13, bw * (e.shield / (e.maxHp * 0.4)), 3);
                }
            } else if (e.regen) {
                c.fillStyle = '#44ffaa';
                c.beginPath(); c.arc(e.x, e.y, r, 0, Math.PI * 2); c.fill();
                c.strokeStyle = 'rgba(0,255,100,0.5)'; c.lineWidth = 2; c.stroke();
            } else if (e.armored) {
                c.fillStyle = '#c04aff'; c.fillRect(e.x - r, e.y - r, r * 2, r * 2);
                c.strokeStyle = 'rgba(0,0,0,0.4)'; c.lineWidth = 1; c.strokeRect(e.x - r, e.y - r, r * 2, r * 2);
            } else {
                c.fillStyle = e.fast ? '#ff9f4a' : '#ff3333';
                c.beginPath(); c.arc(e.x, e.y, r, 0, Math.PI * 2); c.fill();
                c.strokeStyle = 'rgba(0,0,0,0.4)'; c.lineWidth = 1; c.stroke();
            }
            // Barra HP
            const bw = r * 2 + 4, bh = 3;
            c.fillStyle = 'rgba(0,0,0,0.5)'; c.fillRect(e.x - bw / 2, e.y - r - 7, bw, bh);
            c.fillStyle = e.hp / e.maxHp > 0.5 ? '#44ff44' : '#ff4444';
            c.fillRect(e.x - bw / 2, e.y - r - 7, bw * (e.hp / e.maxHp), bh);
            // Slow ring
            if (e.slowTimer > 0 || e.frostActive) {
                c.strokeStyle = e.frostActive ? '#7adfff' : '#aaff4a'; c.lineWidth = 2;
                c.beginPath(); c.arc(e.x, e.y, r + 3, 0, Math.PI * 2); c.stroke();
            }
            // Crit aura ring (nemico sotto aura)
            if ((e.critChance || 0) > this.cfg.baseCritChance) {
                c.strokeStyle = 'rgba(255,215,0,0.5)'; c.lineWidth = 1;
                c.setLineDash([2, 2]);
                c.beginPath(); c.arc(e.x, e.y, r + 5, 0, Math.PI * 2); c.stroke();
                c.setLineDash([]);
            }
        });

        // ── Proiettili ──
        this.bullets.forEach(b => {
            c.fillStyle = b.color;
            c.beginPath(); c.arc(b.x, b.y, b.aoe ? 5 : 3, 0, Math.PI * 2); c.fill();
        });

        // ── Particelle ──
        if (this.particles) {
            this.particles.forEach(p => {
                const alpha = 1 - p.age / p.life;
                c.globalAlpha = alpha;
                c.fillStyle = p.color;
                if (p.crit) {
                    // Stelle dorate per critico
                    c.font = `${Math.round(p.r * 3)}px monospace`;
                    c.textAlign = 'center';
                    c.fillText('★', p.x, p.y);
                } else {
                    c.beginPath(); c.arc(p.x, p.y, p.r * (1 - p.age / p.life * 0.5), 0, Math.PI * 2); c.fill();
                }
            });
            c.globalAlpha = 1;
        }
    }

    gameOver() {
        clearInterval(this.gameLoop);
        LS.set('td_carry', 0);
        const s = document.getElementById('td-btn-stop');
        if (s) s.style.display = 'none';

        // Aggiorna hi-score
        if (this.score > this.hi) {
            this.hi = this.score; LS.set('td_hi', this.hi);
            const hd = document.getElementById('td-hi');
            if (hd) hd.textContent = this.hi;
        }

        // Overlay DOM sopra il canvas (più ricco del canvas testo)
        const wrap = document.getElementById('td-canvas-wrap');
        if (!wrap) return;
        const existing = document.getElementById('td-gameover-overlay');
        if (existing) existing.remove();

        const ov = document.createElement('div');
        ov.id = 'td-gameover-overlay';
        ov.style.cssText = `
            position:absolute;inset:0;background:rgba(2,5,12,0.93);
            display:flex;flex-direction:column;align-items:center;justify-content:center;
            gap:8px;font-family:monospace;border-radius:2px;z-index:20;
        `;

        const kills = this.totalKills || 0;
        const maps  = this.mapsPlayed || 1;
        const towerCount = this.towers ? this.towers.length : 0;
        const isNewHi = this.score >= this.hi;

        ov.innerHTML = `
            <div style="font-size:18px;color:#ff4a4a;font-weight:700;letter-spacing:2px">
                ☠ SERVER COMPROMESSO
            </div>
            <div style="font-size:11px;color:#3a6a9a;margin-bottom:4px">${this.mapLabel || ''}</div>
            <div style="display:flex;gap:12px;flex-wrap:wrap;justify-content:center;margin:4px 0">
                <div style="text-align:center">
                    <div style="font-size:20px;font-weight:700;color:#4a9eff">${this.score}</div>
                    <div style="font-size:9px;color:#3a6a9a;letter-spacing:1px">SCORE</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:20px;font-weight:700;color:#ffdf4a">${this.wave}</div>
                    <div style="font-size:9px;color:#3a6a9a;letter-spacing:1px">ONDATA</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:20px;font-weight:700;color:#2adf8f">${kills}</div>
                    <div style="font-size:9px;color:#3a6a9a;letter-spacing:1px">KILLS</div>
                </div>
                <div style="text-align:center">
                    <div style="font-size:20px;font-weight:700;color:#aa7aff">${maps}</div>
                    <div style="font-size:9px;color:#3a6a9a;letter-spacing:1px">MAPPE</div>
                </div>
            </div>
            ${isNewHi ? `<div style="font-size:11px;color:#ffd700;animation:scorePop 0.5s ease-out">★ NUOVO RECORD ★</div>` : `<div style="font-size:10px;color:#3a5a7a">Record: ${this.hi}</div>`}
            <div style="font-size:10px;color:#2a4a6a;margin-top:4px">Riapri il pannello per ricominciare</div>
        `;
        wrap.appendChild(ov);
    }

    destroy() {
        clearInterval(this.gameLoop);
        document.removeEventListener('keydown', this.onKeyTD);
        const tt = document.getElementById('td-tower-tooltip');
        if (tt) tt.remove();
        document.getElementById('td-upgrade-popup')?.remove();
        document.getElementById('td-cfg-modal')?.remove();
        document.getElementById('td-gameover-overlay')?.remove();
        document.getElementById('td-wave-log')?.remove();
    }
}

// ── Init ─────────────────────────────────────────────────────────────────────
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTrigger);
} else {
    initTrigger();
}

})();
