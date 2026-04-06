#!/usr/bin/env python3
"""
extto_memcheck.py — Analisi memoria processo extto3.py in esecuzione.

Esegui come:
  python3 extto_memcheck.py
  python3 extto_memcheck.py <PID>   # se vuoi specificare il PID

Output:
  - RSS totale e breakdown per categoria (heap, stack, shared libs, mmap)
  - Top 15 mapping per dimensione RSS (identifica .so pesanti o mmap grandi)
  - Top 20 tipi Python per conteggio oggetti (richiede gdb installato)
"""
import sys, os, subprocess, re

def get_extto_pid():
    try:
        r = subprocess.run(['pgrep', '-f', 'extto3.py'], capture_output=True, text=True)
        pids = [p for p in r.stdout.strip().split() if p]
        return int(pids[0]) if pids else None
    except:
        return None

def rss_of(pid):
    try:
        with open(f'/proc/{pid}/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1])  # kB
    except:
        return 0

def smaps_summary(pid):
    """Legge /proc/PID/smaps_rollup per breakdown RSS per tipo mapping."""
    try:
        with open(f'/proc/{pid}/smaps_rollup') as f:
            return f.read()
    except:
        return ''

def top_maps(pid, n=15):
    """Legge /proc/PID/smaps e ritorna i mapping più pesanti per RSS."""
    entries = []
    try:
        with open(f'/proc/{pid}/smaps') as f:
            content = f.read()
        blocks = re.split(r'\n(?=[0-9a-f]+-[0-9a-f]+ )', content)
        for block in blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue
            header = lines[0]
            rss = 0
            for l in lines[1:]:
                m = re.match(r'Rss:\s+(\d+)', l)
                if m:
                    rss = int(m.group(1))
                    break
            if rss > 512:  # ignora mapping < 512kB
                entries.append((rss, header))
    except Exception as e:
        print(f"  (errore lettura smaps: {e})")
    entries.sort(reverse=True)
    return entries[:n]

def thread_count(pid):
    try:
        with open(f'/proc/{pid}/status') as f:
            for line in f:
                if line.startswith('Threads:'):
                    return int(line.split()[1])
    except:
        pass
    return 0

def fd_count(pid):
    try:
        return len(os.listdir(f'/proc/{pid}/fd'))
    except:
        return -1

def python_objects(pid):
    """Usa gdb per chiedere a Python i top objects per conteggio."""
    gdb_script = (
        'python\n'
        'import gc, collections\n'
        'gc.collect()\n'
        'counts = collections.Counter(type(o).__name__ for o in gc.get_objects())\n'
        'for name, n in counts.most_common(25):\n'
        '    print(f"{n:>8}  {name}")\n'
        'end\n'
        'quit'
    )
    try:
        r = subprocess.run(
            ['gdb', '-p', str(pid), '-batch', '-ex', gdb_script],
            capture_output=True, text=True, timeout=20
        )
        lines = [l for l in r.stdout.splitlines() if re.match(r'\s*\d+\s+\w', l)]
        return '\n'.join(lines[:25]) if lines else '(nessun output da gdb)'
    except FileNotFoundError:
        return '(gdb non trovato — installa con: sudo apt install gdb)'
    except subprocess.TimeoutExpired:
        return '(gdb timeout — processo occupato?)'
    except Exception as e:
        return f'(errore gdb: {e})'

def py_sizes(pid):
    """Stima dimensione oggetti Python più grandi via gdb."""
    gdb_script = (
        'python\n'
        'import gc, sys\n'
        'gc.collect()\n'
        'big = [(sys.getsizeof(o), type(o).__name__, repr(o)[:80]) for o in gc.get_objects()]\n'
        'big.sort(reverse=True)\n'
        'for sz, tp, rep in big[:20]:\n'
        '    print(f"{sz:>10}  {tp:<20}  {rep}")\n'
        'end\n'
        'quit'
    )
    try:
        r = subprocess.run(
            ['gdb', '-p', str(pid), '-batch', '-ex', gdb_script],
            capture_output=True, text=True, timeout=25
        )
        lines = [l for l in r.stdout.splitlines() if re.match(r'\s*\d+\s+\w', l)]
        return '\n'.join(lines[:20]) if lines else '(nessun output)'
    except Exception as e:
        return f'(errore: {e})'

# ── Main ─────────────────────────────────────────────────────────────────────

pid = int(sys.argv[1]) if len(sys.argv) > 1 else get_extto_pid()
if not pid:
    print("ERRORE: extto3.py non trovato in esecuzione.")
    print("Usa: python3 extto_memcheck.py <PID>")
    sys.exit(1)

print(f"╔══════════════════════════════════════════════════════╗")
print(f"║          extto_memcheck — analisi RAM processo       ║")
print(f"╚══════════════════════════════════════════════════════╝")
print(f"PID: {pid}")

rss = rss_of(pid)
print(f"RSS totale:  {rss:>8} kB  ({rss//1024} MB)")
print(f"Thread:      {thread_count(pid)}")
print(f"File aperti: {fd_count(pid)}")
print()

print("── smaps_rollup (breakdown categorie) ─────────────────")
smaps = smaps_summary(pid)
if smaps:
    for line in smaps.splitlines():
        line = line.strip()
        if any(k in line for k in ('Rss:','Pss:','Shared_Clean:','Shared_Dirty:',
                                    'Private_Clean:','Private_Dirty:','Anonymous:',
                                    'Swap:','Heap','Stack')):
            # converti kB in MB per leggibilità se > 1MB
            m = re.match(r'(\w+):\s+(\d+)\s+kB', line)
            if m:
                kb = int(m.group(2))
                suffix = f" ({kb//1024} MB)" if kb > 1024 else ""
                print(f"  {m.group(1):<20} {kb:>8} kB{suffix}")
            else:
                print(f"  {line}")
else:
    print("  (smaps_rollup non disponibile)")
print()

print("── Top 15 mapping per RSS ──────────────────────────────")
for rss_kb, header in top_maps(pid, 15):
    mb = f"({rss_kb//1024}MB)" if rss_kb > 1024 else ""
    print(f"  {rss_kb:>7} kB {mb:<6}  {header[:90]}")
print()

print("── Top 25 tipi oggetti Python (conta istanze) ──────────")
print(python_objects(pid))
print()

print("── Top 20 oggetti Python per dimensione (bytes) ────────")
print(py_sizes(pid))
print()
print("╔══════════════════════════════════════════════════════╗")
print("║  Fine analisi. Incolla l'output completo in chat.   ║")
print("╚══════════════════════════════════════════════════════╝")
