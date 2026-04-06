#!/bin/bash
#
# EXTTO Professional Backup Script (GFS Rotation) - Physical Copies
#

# --- CONFIGURAZIONE PERCORSI ---
# Ricava automaticamente la cartella dove si trova questo script (la cartella da salvare)
BASE_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Se viene passato un parametro (es. /home/andres/copie), usalo come destinazione.
# Altrimenti, usa la cartella "backups" di default.
if [ -n "$1" ]; then
    BACKUP_ROOT="$1"
else
    BACKUP_ROOT="$BASE_DIR/backups"
fi

TIMESTAMP=$(date +"%Y-%m-%d--%H-%M")
FILENAME="backup-$TIMESTAMP.tar.gz"

# --- CONFIGURAZIONE RETENTION (Giorni) ---
RETENTION_DAILY=7
RETENTION_WEEKLY=30
RETENTION_MONTHLY=365
RETENTION_YEARLY=1825   # 5 anni

# Assicuriamoci che la struttura esista nella cartella di destinazione
mkdir -p "$BACKUP_ROOT/"{daily,weekly,monthly,yearly}

echo "[$(date)] --- Inizio Backup EXTTO ---"
echo "[$(date)] 📁 Destinazione backup: $BACKUP_ROOT"

TEMP_BACKUP="$BACKUP_ROOT/daily/$FILENAME"

## 1. CREAZIONE BACKUP
tar cfz "$TEMP_BACKUP" \
    --exclude='./backups' \
    --exclude='*__pycache__*' \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.log.*' \
    -C "$BASE_DIR" .

if [ $? -ne 0 ]; then
    echo "[$(date)] ❌ ERRORE: tar fallito, backup interrotto." >&2
    exit 1
fi

echo "[$(date)] ✓ Archivio creato: $FILENAME ($(du -sh "$TEMP_BACKUP" | cut -f1))"

# 2. LOGICA DI ROTAZIONE (GFS)
DAY_OF_WEEK=$(date +%-u)  # 1=Lun, 7=Dom
DAY_OF_MONTH=$(date +%-d) # 1-31
MONTH=$(date +%-m)        # 1-12

# Backup di fine settimana: il lunedì mattina cattura la domenica sera
if [ "$DAY_OF_WEEK" -eq 1 ]; then
    cp "$TEMP_BACKUP" "$BACKUP_ROOT/weekly/$FILENAME" \
        && echo "[$(date)] ✓ Copia fisica weekly creata" \
        || echo "[$(date)] ⚠ Copia fisica weekly fallita" >&2
fi

# Backup di fine mese: il primo del mese cattura il mese precedente
if [ "$DAY_OF_MONTH" -eq 1 ]; then
    cp "$TEMP_BACKUP" "$BACKUP_ROOT/monthly/$FILENAME" \
        && echo "[$(date)] ✓ Copia fisica monthly creata" \
        || echo "[$(date)] ⚠ Copia fisica monthly fallita" >&2

    # Primo di gennaio = chiusura dell'anno precedente
    if [ "$MONTH" -eq 1 ]; then
        cp "$TEMP_BACKUP" "$BACKUP_ROOT/yearly/$FILENAME" \
            && echo "[$(date)] ✓ Copia fisica yearly creata" \
            || echo "[$(date)] ⚠ Copia fisica yearly fallita" >&2
    fi
fi

# 3. PULIZIA AUTOMATICA (Retention)
# Usiamo N-1 per compensare l'arrotondamento per difetto di -mtime
find "$BACKUP_ROOT/daily"   -name "backup-*.tar.gz" -mtime +$((RETENTION_DAILY - 1))   -delete
find "$BACKUP_ROOT/weekly"  -name "backup-*.tar.gz" -mtime +$((RETENTION_WEEKLY - 1))  -delete
find "$BACKUP_ROOT/monthly" -name "backup-*.tar.gz" -mtime +$((RETENTION_MONTHLY - 1)) -delete
find "$BACKUP_ROOT/yearly"  -name "backup-*.tar.gz" -mtime +$((RETENTION_YEARLY - 1))  -delete

echo "[$(date)] ✓ Pulizia completata"
echo "[$(date)] --- Backup completato con successo! ---"
