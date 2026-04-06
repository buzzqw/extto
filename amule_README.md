# core/clients/amule/

Client aMule per EXTTO via protocollo EC (External Connections). ★ v45

## Struttura

```
core/clients/amule/
├── amule.py          — Client Python EC: AmuleClient + ECProtocol
├── README.md         — Questo file
└── config/           — Directory di configurazione di amuled
    ├── .gitkeep      — Mantiene la directory in git
    ├── amule.conf    — Generato da EXTTO (Web UI → Manutenzione → aMule)
    ├── server.met    — Lista server ed2k (gestita da amuled)
    └── known.met     — File conosciuti (gestita da amuled)
```

## Setup

### 1. Installa amuled

```bash
sudo apt install amule-daemon
```

### 2. Configura in EXTTO

Web UI → **Manutenzione** → sezione **aMule / ed2k**:

| Campo | Descrizione | Default |
|-------|-------------|---------|
| Abilitato | Attiva il client aMule in EXTTO | no |
| Host / IP | Indirizzo di amuled | localhost |
| Porta EC | Porta External Connections | 4712 |
| Password EC | Password impostata in aMule Preferenze → Controllo Remoto | — |
| TCP ed2k | Porta TCP in entrata (da aprire nel router per High ID) | 4662 |
| UDP Kad | Porta UDP Kad (da aprire nel router per High ID) | 4672 |
| Config Dir | Directory di configurazione amuled | `core/clients/amule/config` |
| Incoming | Cartella download completati | — |
| Temp | Cartella download temporanei | — |
| Utente sistema | Utente Linux con cui gira amuled | — |
| Nome servizio | Nome del servizio systemd | amuled |

### 3. Genera il file .service

Dal pulsante **Genera .service** in Manutenzione scarica `amuled.service` e installalo:

```bash
sudo cp amuled.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable amuled
sudo systemctl start amuled
```

### 4. Avvio manuale (senza systemd)

```bash
amuled --config-dir=/percorso/extto/core/clients/amule/config --full-gui=0
```

## High ID

Per ottenere High ID (velocità ottimale sulla rete ed2k):
- Aprire nel router: **TCP 4662** e **UDP 4672**
- Il client ID > 16.777.216 indica High ID

## Note

- Il file `amule.conf` viene generato/aggiornato da EXTTO ogni volta che si salva la configurazione
- aMule gestisce autonomamente `server.met` e `known.met` — non modificarli a mano
- I download ed2k appaiono nel Torrent Manager di EXTTO insieme ai download BitTorrent
