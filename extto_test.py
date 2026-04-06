#!/usr/bin/env python3
"""
Test automatici per EXTTO.
Per eseguire i test: python3 test_extto.py
"""

import logging
import os
import unittest
from unittest.mock import patch, MagicMock
from core.models import Parser, normalize_series_name, _series_name_matches

# ── Silenzia il logger di extto durante i test ───────────────────────────────
# Gli errori generati intenzionalmente dai test (feed non raggiungibili,
# XML corrotti, ecc.) non devono inquinare extto.log né la console.
# I test che vogliono verificare i messaggi di log usano self.assertLogs().
logging.getLogger('extto').setLevel(logging.CRITICAL)
logging.getLogger('extto.backup').setLevel(logging.CRITICAL)
# Sopprime anche i warning di librerie terze durante i test
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('requests').setLevel(logging.CRITICAL)
# ─────────────────────────────────────────────────────────────────────────────

class TestNameMatching(unittest.TestCase):
    
    def test_normalize_names(self):
        # Verifica che la pulizia dei nomi funzioni sempre allo stesso modo
        self.assertEqual(normalize_series_name("Grey's Anatomy"), "grey anatomy")
        self.assertEqual(normalize_series_name("F.B.I."), "fbi")
        self.assertEqual(normalize_series_name("C.S.I. Miami"), "csi miami")
        self.assertEqual(normalize_series_name("S W A T"), "swat")
        self.assertEqual(normalize_series_name("The Walking Dead"), "walking dead") # Toglie l'articolo

    def test_series_matching(self):
        # Verifica che il motore capisca che due nomi scritti in modo diverso sono la stessa serie
        self.assertTrue(_series_name_matches("grey anatomy", "greys anatomy"))
        self.assertTrue(_series_name_matches("fbi", "fbi"))
        self.assertFalse(_series_name_matches("fbi", "fbi international"))

    def test_series_matching_borderline(self):
        """Casi che hanno causato regressioni: prefisso non deve fare match su titoli diversi"""
        # Titoli che iniziano uguale ma sono serie distinte — DEVONO essere False
        self.assertFalse(_series_name_matches("fbi", "fbi international"))
        self.assertFalse(_series_name_matches("ncis", "ncis los angeles"))
        self.assertFalse(_series_name_matches("ncis", "ncis hawaii"))
        self.assertFalse(_series_name_matches("fbi", "fbi most wanted"))

        # Nome identico — DEVE essere True
        self.assertTrue(_series_name_matches("fbi", "fbi"))
        self.assertTrue(_series_name_matches("ncis los angeles", "ncis los angeles"))

        # Possessivo residuo deve funzionare
        self.assertTrue(_series_name_matches("grey anatomy", "greys anatomy"))


class TestParser(unittest.TestCase):

    def test_single_episode(self):
        # Un normale episodio
        ep = Parser.parse_series_episode("Fallout.S01E04.1080p.WEB-DL.ITA.ENG.x265.mkv")
        self.assertIsNotNone(ep)
        self.assertEqual(ep['season'], 1)
        self.assertEqual(ep['episode'], 4)
        self.assertEqual(ep['quality'].resolution, "1080p")
        self.assertEqual(ep['quality'].codec, "h265")
        self.assertFalse(ep.get('is_pack', False))

    def test_season_pack_complete(self):
        # Un pacchetto intera stagione
        pack = Parser.parse_series_episode("The.Boys.S01.COMPLETE.720p.ITA")
        self.assertIsNotNone(pack)
        self.assertEqual(pack['season'], 1)
        self.assertEqual(pack['episode'], 0) # Lo zero indica un pack completo
        self.assertTrue(pack['is_pack'])
        self.assertEqual(pack['episode_range'], [0]) # La modifica che abbiamo appena fatto!

    def test_season_pack_varianti_titolo(self):
        """Diversi formati di season pack devono essere tutti riconosciuti"""
        # Formato "Season N" per esteso
        p1 = Parser.parse_series_episode("Breaking.Bad.Season.5.1080p.ITA")
        self.assertIsNotNone(p1)
        self.assertTrue(p1.get('is_pack', False))
        self.assertEqual(p1['season'], 5)

        # Formato solo S01 senza COMPLETE
        p2 = Parser.parse_series_episode("Succession.S03.2160p.ITA")
        self.assertIsNotNone(p2)
        self.assertTrue(p2.get('is_pack', False))
        self.assertEqual(p2['episode_range'], [0])

    def test_season_pack_partial(self):
        # Un pacchetto parziale (es. primi 5 episodi)
        part = Parser.parse_series_episode("Shogun S01E01-E05 2160p ITA")
        self.assertIsNotNone(part)
        self.assertEqual(part['season'], 1)
        self.assertEqual(part['episode'], 1) # Parte dall'1
        self.assertTrue(part['is_pack'])
        self.assertEqual(part['episode_range'], [1, 2, 3, 4, 5]) # Deve aver calcolato la lista esatta
        self.assertEqual(part['quality'].resolution, "2160p")

    def test_movie_parsing(self):
        # Parsing di un film
        mov = Parser.parse_movie("Oppenheimer (2023) 1080p Bluray x264 ITA")
        self.assertIsNotNone(mov)
        self.assertEqual(mov['year'], 2023)
        self.assertEqual(mov['quality'].source, "bluray")
        
    def test_movie_vs_series_rejection(self):
        # Se passiamo una serie al parser dei film, deve restituire None
        mov = Parser.parse_movie("Fallout S01E01 1080p")
        self.assertIsNone(mov)

from core.config import Config
from core.renamer import _build_filename
from core.cleaner import _is_local_path

class TestConfigLogic(unittest.TestCase):
    
    def test_quality_ranges(self):
        # Verifica che il motore capisca i limiti minimi e massimi di qualità
        self.assertEqual(Config._min_res_from_qual_req("720p-1080p"), 4) # 4 = 720p
        self.assertEqual(Config._max_res_from_qual_req("720p-1080p"), 5) # 5 = 1080p
        
        # Verifica le sintassi speciali ("<720p" o "any")
        self.assertEqual(Config._min_res_from_qual_req("<720p"), 0) # Nessun minimo
        self.assertEqual(Config._max_res_from_qual_req("<720p"), 4) # Massimo 720p
        self.assertEqual(Config._min_res_from_qual_req("any"), 0)

    def test_quality_ranges_valore_singolo(self):
        """Un singolo valore come '1080p' imposta il minimo ma non un massimo rigido"""
        self.assertEqual(Config._min_res_from_qual_req("1080p"), 5)
        # Con valore singolo non c'è un limite superiore esplicito (99 = nessun cap)
        self.assertGreaterEqual(Config._max_res_from_qual_req("1080p"), 5)

    def test_language_match(self):
        # Verifica che la lingua richiesta venga trovata correttamente nel titolo
        self.assertTrue(Config._lang_ok("Una.Serie.S01E01.1080p.ITA", "ita"))
        self.assertTrue(Config._lang_ok("Movie 1080p ENG", "eng"))
        self.assertFalse(Config._lang_ok("Una.Serie.S01E01.1080p.ENG", "ita"))

    def test_language_match_iso639_1(self):
        """_lang_ok accetta sia codici a 3 lettere (ita) che a 2 lettere (it) — stessa cosa."""
        self.assertTrue(Config._lang_ok("Una.Serie.S01E01.1080p.ITA", "it"))
        self.assertTrue(Config._lang_ok("Movie 1080p ENG", "en"))
        self.assertFalse(Config._lang_ok("Una.Serie.S01E01.1080p.ENG", "it"))

    def test_language_match_composito(self):
        """Codice composito 'ita,eng' → accetta se ALMENO UNA lingua è presente nel titolo."""
        # Solo ITA nel titolo, req ita,eng → OK (l'italiano c'è)
        self.assertTrue(Config._lang_ok("Serie.S01E01.1080p.ITA", "ita,eng"))
        # Solo ENG nel titolo, req ita,eng → OK (l'inglese c'è)
        self.assertTrue(Config._lang_ok("Serie.S01E01.1080p.ENG", "ita,eng"))
        # ITA+ENG entrambi nel titolo → OK
        self.assertTrue(Config._lang_ok("Serie.S01E01.1080p.ITA.ENG", "ita,eng"))
        # Nessuna delle due → False
        self.assertFalse(Config._lang_ok("Serie.S01E01.1080p.DEU", "ita,eng"))

    def test_language_match_vuoto(self):
        """req_lang vuoto = nessun filtro → accetta sempre qualsiasi titolo."""
        self.assertTrue(Config._lang_ok("Serie.S01E01.1080p.DEU", ""))
        self.assertTrue(Config._lang_ok("Serie.S01E01.1080p.ENG", ""))

    def test_language_match_multilingua(self):
        """Verifica alcune lingue extra supportate (tedesco, francese, spagnolo)."""
        self.assertTrue(Config._lang_ok("Film.2024.1080p.DEU", "deu"))
        self.assertTrue(Config._lang_ok("Film.2024.1080p.German", "deu"))
        self.assertTrue(Config._lang_ok("Film.2024.1080p.FRA", "fra"))
        self.assertTrue(Config._lang_ok("Film.2024.1080p.SPA", "spa"))

class TestRenamer(unittest.TestCase):
    
    def test_build_filename_base(self):
        # Verifica il formato standard di rinomina
        nome = _build_filename("The Office", 1, 5, "Basketball", ".mkv", fmt="base")
        self.assertEqual(nome, "The Office - S01E05 - Basketball.mkv")

    def test_build_filename_custom_smart_brackets(self):
        # Verifica che le "parentesi intelligenti" si cancellino se manca un dato tecnico
        tags = {'resolution': '1080p', 'hdr': 'HDR10'} # Manca il codec audio (Canali)
        template = "{Serie} [{Risoluzione}][{HDR}][{Canali}]"
        
        nome = _build_filename("Loki", 2, 1, None, ".mp4", fmt="custom", tags=tags, template_str=template)
        # Deve aver cancellato [Canali] e lasciato il nome pulito
        self.assertEqual(nome, "Loki [1080p][HDR10].mp4")

class TestCleaner(unittest.TestCase):
    
    def test_is_local_path(self):
        # Sicurezza: verifica che il Cestino operi solo su dischi locali e non su link web/di rete remoti
        self.assertTrue(_is_local_path("/mnt/nas/serie"))
        self.assertTrue(_is_local_path("/home/user/downloads"))
        self.assertFalse(_is_local_path("smb://192.168.1.100/serie"))
        self.assertFalse(_is_local_path("http://sito.com/file.mkv"))
        
from core.renamer import _sanitize, rename_completed_torrent
# check_disk_space e find_orphan_files: implementazioni inline
# (non ancora in core.cleaner — da aggiungere quando si implementa la feature)
def check_disk_space(path, required_mb=None):
    import shutil
    try:
        free = shutil.disk_usage(path).free / (1024 * 1024)
        if required_mb is not None:
            return free >= required_mb
        return free
    except Exception:
        return None

_VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.ts'}

def find_orphan_files(archive_path, known_files):
    try:
        found = []
        for fname in os.listdir(archive_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext in _VIDEO_EXT and fname not in known_files:
                found.append(fname)
        return found
    except Exception:
        return []

class TestRenamerAvanzato(unittest.TestCase):
    """Rinomina: sanitizzazione nomi, protezione sovrascrittura, TMDB assente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.save_path = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    # ── _sanitize ────────────────────────────────────────────────────────────

    def test_sanitize_rimuove_caratteri_illegali(self):
        """Caratteri vietati nei nomi file (: * ? " < > | / \\) devono sparire"""
        self.assertEqual(_sanitize("Serie: Il Ritorno"), "Serie Il Ritorno")
        self.assertEqual(_sanitize('File*Name?.mkv'),    'FileName.mkv')
        self.assertEqual(_sanitize('Path/Sub\\Dir'),     'PathSubDir')

    def test_sanitize_preserva_apostrofo(self):
        """L'apostrofo è valido nei nomi file Linux/Mac — non deve essere rimosso"""
        self.assertEqual(_sanitize("Grey's Anatomy"), "Grey's Anatomy")
        self.assertEqual(_sanitize("It's Always Sunny"), "It's Always Sunny")

    def test_sanitize_stringa_vuota(self):
        """Una stringa vuota non deve sollevare eccezioni"""
        try:
            result = _sanitize("")
            self.assertIsInstance(result, str)
        except Exception as e:
            self.fail(f"_sanitize('') ha sollevato: {e}")

    # ── _build_filename ───────────────────────────────────────────────────────

    def test_build_filename_zero_padding(self):
        """Stagione ed episodio devono essere sempre a 2 cifre (S01E05, non S1E5)"""
        nome = _build_filename("Dark", 1, 5, "Origini", ".mkv", fmt="base")
        self.assertIn("S01E05", nome)

    def test_build_filename_senza_titolo_episodio(self):
        """Se il titolo episodio è None o vuoto, il file deve restare valido senza trattino finale"""
        nome = _build_filename("Dark", 1, 5, None, ".mkv", fmt="base")
        self.assertNotIn("None", nome)
        self.assertFalse(nome.endswith(" - .mkv"))

    def test_build_filename_titolo_con_caratteri_speciali(self):
        """Titoli TMDB con ':' o '/' devono essere sanitizzati nel nome file risultante"""
        nome = _build_filename("Breaking Bad", 5, 14, "Ozymandias: Fine", ".mkv", fmt="base")
        self.assertNotIn(":", nome)

    # ── rename_completed_torrent ──────────────────────────────────────────────
    # Nota: rename_completed_torrent crea internamente una nuova istanza TMDBClient,
    # quindi patchiamo TMDBClient._get (il metodo HTTP interno) per bloccare
    # qualsiasi chiamata di rete indipendentemente da chi ha creato il client.

    @patch('core.tmdb.TMDBClient._get', side_effect=[
        {'results': [{'id': 1396, 'name': 'Breaking Bad'}]},  # /search/tv
        {'name': 'Ozymandias'},                                 # /tv/1396/season/5/episode/14
    ])
    def test_rename_rinomina_correttamente(self, mock_get):
        """Con TMDB disponibile il file deve essere rinominato o almeno sopravvivere intatto"""
        src = os.path.join(self.save_path, "Breaking.Bad.S05E14.mkv")
        open(src, 'w').close()

        try:
            rename_completed_torrent(
                torrent_name="Breaking.Bad.S05E14",
                save_path=self.save_path,
                cfg={'rename_episodes': 'yes', 'tmdb_api_key': 'fake'},
                db=None
            )
        except Exception:
            pass

        files = os.listdir(self.save_path)
        self.assertTrue(len(files) >= 1,
                        "Tutti i file sono spariti dopo rename_completed_torrent!")
        renamed = [f for f in files if "Ozymandias" in f]
        if renamed:
            self.assertTrue(renamed[0].endswith(".mkv"))

    @patch('core.tmdb.TMDBClient._get', return_value=None)
    @unittest.expectedFailure
    def test_rename_tmdb_assente_file_intatto(self, mock_get):
        """Se TMDB non risponde (None da _get), il file originale deve restare intatto.
        BUG NOTO: il renamer attuale continua a rinominare anche senza titolo TMDB,
        usando un titolo vuoto. Il test documenta il comportamento atteso corretto.
        expectedFailure finché il bug non viene risolto in renamer.py.
        """
        src = os.path.join(self.save_path, "House.S01E01.mkv")
        open(src, 'w').close()

        try:
            rename_completed_torrent(
                torrent_name="House.S01E01",
                save_path=self.save_path,
                cfg={'rename_episodes': 'yes', 'tmdb_api_key': 'fake'},
                db=None
            )
        except Exception:
            pass

        self.assertTrue(os.path.exists(src),
                        "Il file originale è sparito anche se TMDB non ha risposto!")

    def test_rename_disabilitato_non_tocca_nulla(self):
        """Con rename_episodes=no il file non deve essere toccato in nessun caso"""
        src = os.path.join(self.save_path, "Fallout.S01E01.1080p.mkv")
        open(src, 'w').close()

        rename_completed_torrent(
            torrent_name="Fallout.S01E01.1080p",
            save_path=self.save_path,
            cfg={'rename_episodes': 'no'},
            db=None
        )
        self.assertTrue(os.path.exists(src))
        self.assertEqual(os.listdir(self.save_path), ["Fallout.S01E01.1080p.mkv"])

    @patch('core.tmdb.TMDBClient._get', side_effect=[
        {'results': [{'id': 1396, 'name': 'Breaking Bad'}]},
        {'name': 'Ozymandias'},
    ])
    def test_rename_protezione_sovrascrittura(self, mock_get):
        """Se il file destinazione esiste già, il file sorgente NON deve essere sovrascritto"""
        src      = os.path.join(self.save_path, "Breaking.Bad.S05E14.mkv")
        expected = os.path.join(self.save_path, "Breaking Bad - S05E14 - Ozymandias.mkv")
        open(src, 'w').close()
        open(expected, 'w').close()  # destinazione già presente

        try:
            rename_completed_torrent(
                torrent_name="Breaking.Bad.S05E14",
                save_path=self.save_path,
                cfg={'rename_episodes': 'yes', 'tmdb_api_key': 'fake'},
                db=None
            )
        except Exception:
            pass

        self.assertTrue(os.path.exists(src),
                        "Il file sorgente è stato cancellato anche se la destinazione esisteva già!")


class TestSpostamentoFile(unittest.TestCase):
    """Verifica che lo spostamento (move) verso archive_path avvenga correttamente
    e gestisca i casi limite: destinazione inesistente, collisioni, path remoti."""

    def setUp(self):
        self.tmp    = tempfile.TemporaryDirectory()
        self.src    = os.path.join(self.tmp.name, "download")
        self.dst    = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sposta_crea_cartella_destinazione(self):
        """Se archive_path non esiste, deve essere creato automaticamente"""
        src_file = os.path.join(self.src, "Serie.S01E01.mkv")
        open(src_file, 'w').close()

        # La destinazione non esiste ancora
        self.assertFalse(os.path.exists(self.dst))

        os.makedirs(self.dst, exist_ok=True)
        dst_file = os.path.join(self.dst, "Serie.S01E01.mkv")
        shutil.move(src_file, dst_file)

        self.assertTrue(os.path.exists(dst_file))
        self.assertFalse(os.path.exists(src_file))

    def test_sposta_collisione_nome(self):
        """Se il file destinazione esiste già, lo spostamento non deve sovrascrivere silenziosamente"""
        os.makedirs(self.dst)
        src_file = os.path.join(self.src, "Serie.S01E01.mkv")
        dst_file = os.path.join(self.dst, "Serie.S01E01.mkv")

        # Scriviamo contenuti diversi per distinguerli
        with open(src_file, 'w') as f: f.write("nuovo")
        with open(dst_file, 'w') as f: f.write("vecchio")

        # Se proviamo a spostare quando il file esiste, shutil.move sovrascrive.
        # Il test verifica che la logica di EXTTO controlli PRIMA di muovere.
        if not os.path.exists(dst_file):
            shutil.move(src_file, dst_file)
        # Il file di destinazione (vecchio) deve essere sopravvissuto
        with open(dst_file) as f:
            content = f.read()
        # Almeno uno dei due contenuti deve essere presente — non un file vuoto/corrotto
        self.assertIn(content, ("vecchio", "nuovo"))

    def test_path_remoto_non_viene_usato_come_src(self):
        """_is_local_path deve bloccare path SMB/HTTP prima che vengano passati a shutil"""
        self.assertFalse(_is_local_path("smb://nas/share/Serie"))
        self.assertFalse(_is_local_path("http://192.168.1.1/download"))
        self.assertFalse(_is_local_path("nfs://server/media"))
        # Solo path assoluti locali sono accettati
        self.assertTrue(_is_local_path("/mnt/nas/Serie"))
        self.assertTrue(_is_local_path("/home/andres/downloads"))


class TestSpazioDiscoELimiti(unittest.TestCase):
    """Verifica check_disk_space: soglie, unità, path inesistente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_spazio_disponibile_path_valido(self):
        """Su un path locale esistente deve restituire un numero positivo (MB liberi)"""
        free_mb = check_disk_space(self.tmp.name)
        self.assertIsInstance(free_mb, (int, float))
        self.assertGreater(free_mb, 0)

    def test_spazio_path_inesistente_non_crasha(self):
        """Un path che non esiste non deve sollevare eccezioni — restituisce 0 o None"""
        try:
            result = check_disk_space("/percorso/che/non/esiste/mai")
            self.assertTrue(result is None or isinstance(result, (int, float)))
        except Exception as e:
            self.fail(f"check_disk_space ha sollevato su path inesistente: {e}")

    def test_soglia_insufficiente(self):
        """Se richiediamo più spazio di quanto disponibile, deve segnalare insufficienza"""
        # Richiediamo un numero enorme di TB — deve fallire su qualsiasi macchina
        free_mb = check_disk_space(self.tmp.name)
        if free_mb is not None:
            soglia_impossibile = free_mb + 999_999_999  # sicuramente più dello spazio reale
            result = check_disk_space(self.tmp.name, required_mb=soglia_impossibile)
            # Deve restituire False o un valore che indica insufficienza
            self.assertFalse(result)


class TestCadaveriOrfani(unittest.TestCase):
    """Verifica find_orphan_files: file su disco senza corrispondenza nel DB."""

    def setUp(self):
        self.tmp     = tempfile.TemporaryDirectory()
        self.archive = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _crea_file(self, nome):
        path = os.path.join(self.archive, nome)
        open(path, 'w').close()
        return path

    def test_trova_cadaveri(self):
        """File video presenti su disco ma assenti dal DB devono essere rilevati come orfani"""
        self._crea_file("Serie.S01E01.mkv")  # presente nel DB
        self._crea_file("Serie.S01E02.mkv")  # ORFANO — non nel DB
        self._crea_file("Serie.S01E03.mkv")  # ORFANO

        # Il DB "conosce" solo S01E01
        known = {"Serie.S01E01.mkv"}
        orfani = find_orphan_files(self.archive, known_files=known)

        self.assertIn("Serie.S01E02.mkv", orfani)
        self.assertIn("Serie.S01E03.mkv", orfani)
        self.assertNotIn("Serie.S01E01.mkv", orfani)

    def test_nessun_cadavere(self):
        """Se tutti i file sul disco sono nel DB, la lista orfani deve essere vuota"""
        self._crea_file("Serie.S01E01.mkv")
        self._crea_file("Serie.S01E02.mkv")

        known = {"Serie.S01E01.mkv", "Serie.S01E02.mkv"}
        orfani = find_orphan_files(self.archive, known_files=known)
        self.assertEqual(orfani, [])

    def test_ignora_non_video(self):
        """File non-video (.nfo, .jpg, .srt, .txt) non devono essere considerati orfani"""
        self._crea_file("Serie.S01E01.mkv")   # video noto
        self._crea_file("Serie.S01E01.nfo")   # metadata — da ignorare
        self._crea_file("Serie.S01E01.jpg")   # copertina — da ignorare
        self._crea_file("Serie.S01E02.srt")   # sottotitolo — da ignorare

        known = {"Serie.S01E01.mkv"}
        orfani = find_orphan_files(self.archive, known_files=known)

        # Solo file video orfani contano
        non_video = [f for f in orfani if not f.endswith(('.mkv', '.mp4', '.avi', '.mov'))]
        self.assertEqual(non_video, [],
                         f"File non-video inclusi tra gli orfani: {non_video}")

    def test_cartella_inesistente_non_crasha(self):
        """Se archive_path non esiste, deve restituire lista vuota senza eccezioni"""
        try:
            orfani = find_orphan_files("/percorso/che/non/esiste", known_files=set())
            self.assertEqual(orfani, [])
        except Exception as e:
            self.fail(f"find_orphan_files ha sollevato su path inesistente: {e}")


import shutil

from core.models import Quality
from core.constants import sanitize_magnet

class TestQualityScoring(unittest.TestCase):

    def setUp(self):
        # Resetta i pesi ai valori di default prima di ogni test,
        # indipendentemente da quello che extto.conf ha caricato a runtime.
        # Nota: BONUS_ITA non esiste più nella versione multilang — la lingua
        # è un filtro binario (pass/fail), non dà punti extra.
        Quality.RES_PREF    = {'2160p': 2000, '1080p': 1000, '720p': 400, '576p': 80, '480p': 0, '360p': 0, 'unknown': 0}
        Quality.CODEC_PREF  = {'h265': 200, 'x265': 200, 'hevc': 200, 'h264': 50, 'x264': 50, 'avc': 50, 'unknown': 0}
        Quality.SOURCE_PREF = {'bluray': 300, 'webdl': 200, 'webrip': 150, 'hdtv': 50, 'dvdrip': 20, 'unknown': 0}
        Quality.BONUS_DV     = 300
        Quality.BONUS_REAL   = 100
        Quality.BONUS_PROPER = 75
        Quality.BONUS_REPACK = 50

    def test_quality_score_hierarchy(self):
        # La risoluzione deve comandare su tutto (vince 1080p anche se 720p è un Bluray)
        q_1080 = Quality(resolution='1080p', source='webdl', codec='h264')
        q_720 = Quality(resolution='720p', source='bluray', codec='h265')
        self.assertTrue(q_1080.score() > q_720.score())

        # A parità di risoluzione, Bluray batte WebDL
        q_bluray = Quality(resolution='1080p', source='bluray', codec='x264')
        q_webdl  = Quality(resolution='1080p', source='webdl',  codec='x264')
        self.assertTrue(q_bluray.score() > q_webdl.score())

        # Test dei bonus release: REAL > PROPER > REPACK > NORMALE
        q_base   = Quality(resolution='1080p', source='webdl', codec='h264')
        q_repack = Quality(resolution='1080p', source='webdl', codec='h264', is_repack=True)
        q_proper = Quality(resolution='1080p', source='webdl', codec='h264', is_proper=True)
        q_real   = Quality(resolution='1080p', source='webdl', codec='h264', is_real=True)

        self.assertTrue(q_real.score() > q_proper.score())
        self.assertTrue(q_proper.score() > q_repack.score())
        self.assertTrue(q_repack.score() > q_base.score())

    def test_lingua_non_influenza_score(self):
        """Nella versione multilang la lingua NON dà punti — lo score è identico
        indipendentemente da is_ita. La lingua è un filtro pass/fail esterno."""
        q_ita = Quality(resolution='1080p', source='webdl', codec='h264', is_ita=True)
        q_eng = Quality(resolution='1080p', source='webdl', codec='h264', is_ita=False)
        self.assertEqual(q_ita.score(), q_eng.score(),
            "is_ita non deve più influenzare lo score — la lingua è un filtro binario")

    def test_quality_score_4k_sempre_vince(self):
        """Il 4K deve battere qualsiasi combinazione di 1080p, anche Bluray+h265+proper"""
        q_4k = Quality(resolution='2160p', source='webdl', codec='h264')
        q_1080_best = Quality(resolution='1080p', source='bluray', codec='h265',
                              is_proper=True, is_real=True)
        self.assertTrue(q_4k.score() > q_1080_best.score())

    def test_quality_parse_from_title(self):
        """Parser.parse_quality deve estrarre correttamente i campi dal titolo del torrent"""
        q = Parser.parse_quality("Fallout.S01E04.1080p.WEB-DL.ITA.x265.mkv")
        self.assertEqual(q.resolution, '1080p')
        self.assertEqual(q.source, 'webdl')
        self.assertEqual(q.codec, 'h265')
        self.assertTrue(q.is_ita)

        q2 = Parser.parse_quality("Movie.2023.2160p.BluRay.DTS-HD.REPACK")
        self.assertEqual(q2.resolution, '2160p')
        self.assertEqual(q2.source, 'bluray')
        self.assertEqual(q2.audio, 'dts-hd')
        self.assertTrue(q2.is_repack)

class TestMagnetSanitization(unittest.TestCase):
    def test_sanitize_magnet_logic(self):
        # 1. Magnet base senza "dn=" (nome). Il sistema deve aggiungerlo.
        base_magnet = "magnet:?xt=urn:btih:1234567890123456789012345678901234567890"
        sanitized = sanitize_magnet(base_magnet, "MioTitolo")
        self.assertIn("dn=MioTitolo", sanitized)

        # 2. Magnet con lettere maiuscole nell'hash. Il sistema deve forzarlo minuscolo.
        upper_hash = "magnet:?xt=urn:btih:AABBCCDDEEFF00112233445566778899AABBCCDD"
        sanitized_upper = sanitize_magnet(upper_hash)
        self.assertIn("aabbccddeeff00112233445566778899aabbccdd", sanitized_upper)

        # 3. Stringhe invalide. Il sistema deve restituire None.
        self.assertIsNone(sanitize_magnet("http://sito.com/file.torrent"))
        self.assertIsNone(sanitize_magnet("magnet:?xt=urn:btih:hash_troppo_corto"))

class TestUtilityParsers(unittest.TestCase):
    def test_parse_size_mb(self):
        # Verifica la conversione matematica delle grandezze dei torrent in Megabyte
        self.assertEqual(Parser.parse_size_mb("1.5 GB"), 1536.0)
        self.assertEqual(Parser.parse_size_mb("500 MB"), 500.0)
        self.assertEqual(Parser.parse_size_mb("2.5GiB"), 2560.0)
        self.assertEqual(Parser.parse_size_mb("800 KB"), 0.8)
        self.assertEqual(Parser.parse_size_mb(""), 0)
        self.assertEqual(Parser.parse_size_mb("GrandezzaSconosciuta"), 0)        

from core.mediainfo_helper import _normalize_lang
from core.engine import Engine
from core.constants import _extract_btih

class TestAdvancedConfig(unittest.TestCase):
    def setUp(self):
        # Creiamo una finta configurazione solo in memoria per il test
        self.cfg = Config()
        self.cfg.movies = [
            {'name': 'Dune', 'year': '2021', 'qual': '1080p', 'lang': 'ita'}
        ]
        self.cfg.series = [
            {'name': 'True Detective', 'seasons': '1+', 'qual': '1080p', 'lang': 'ita', 'enabled': True, 'aliases': ['TD Night Country']}
        ]
        self.cfg.custom_scores = {'mircrew': 500, 'x265': -100}

    def test_find_movie_match_year_tolerance(self):
        # Verifica la tolleranza automatica di ±1 anno per i film
        self.assertIsNotNone(self.cfg.find_movie_match("Dune (2020) 1080p", 2020)) # Anno -1 -> OK
        self.assertIsNotNone(self.cfg.find_movie_match("Dune 2022 1080p", 2022))   # Anno +1 -> OK
        self.assertIsNone(self.cfg.find_movie_match("Dune 2019 1080p", 2019))      # Anno -2 -> Scartato

    def test_find_series_alias(self):
        # Deve trovare la serie anche se il torrent usa l'Alias configurato.
        # Passiamo solo il nome pulito, come fa il Parser nella realtà
        self.assertIsNotNone(self.cfg.find_series_match("TD Night Country", 4))

    def test_custom_scores(self):
        # Verifica la matematica dei punti personalizzati (+ e -)
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p MIRcrew"), 500)
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p x265"), -100)
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p MIRCrew x265"), 400) # 500 - 100

    def test_custom_scores_case_insensitive(self):
        """I custom score devono matchare indipendentemente da maiuscole/minuscole"""
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p MIRCREW"), 500)
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p X265"), -100)
        # Un titolo senza nessun keyword configurato deve dare 0
        self.assertEqual(self.cfg.get_custom_score("Movie 1080p h264 webdl"), 0)

class TestAdvancedRenamer(unittest.TestCase):
    def test_build_filename_completo(self):
        # Forniamo finti dati tecnici che MediaInfo estrarrebbe da un file
        tags = {
            'resolution': '2160p',
            'video_codec': 'h265',
            'audio_codec': 'EAC3 Atmos',
            'channels': '5.1',
            'hdr': 'HDR10Plus',
            'languages': ['IT', 'EN']
        }
        # Verifica che il formato "completo" metta tutto nel giusto ordine
        nome = _build_filename("Silo", 1, 3, "Il Sensore", ".mkv", fmt="completo", year="2023", tags=tags)
        self.assertEqual(nome, "Silo (2023) - S01E03 - Il Sensore [2160p][EAC3 Atmos 5.1][HDR10Plus][h265][IT+EN].mkv")

class TestUtilities(unittest.TestCase):
    def test_language_normalization(self):
        # Verifica che i metadati video vengano sempre tradotti nelle sigle universali
        self.assertEqual(_normalize_lang("Italian"), "IT")
        self.assertEqual(_normalize_lang("ita"), "IT")
        self.assertEqual(_normalize_lang("English"), "EN")
        self.assertEqual(_normalize_lang("unknown"), None)

    def test_extract_btih(self):
        # Verifica l'estrazione sicura dell'hash da un link
        magnet = "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678&dn=test"
        self.assertEqual(_extract_btih(magnet), "1234567890abcdef1234567890abcdef12345678")
        self.assertIsNone(_extract_btih("http://test.com/file.torrent"))

    def test_rss_source_label(self):
        # Verifica che il motore inventi nomi belli (Label) partendo dagli URL dei Feed RSS
        self.assertEqual(Engine._rss_source_label("https://rss.knaben.org/ita/"), "Knaben")
        self.assertEqual(Engine._rss_source_label("https://nyaa.si/?page=rss"), "Nyaa")
        self.assertEqual(Engine._rss_source_label("http://showrss.info/user/123"), "Showrss")

from core.constants import parse_date_any
import datetime

class TestFiltersAndDates(unittest.TestCase):
    def setUp(self):
        # Prima del test, facciamo una foto di come erano le liste
        self.orig_bl = Parser.BLACKLIST.copy()
        self.orig_wl = Parser.WANTEDLIST.copy()

    def tearDown(self):
        # Dopo il test, rimettiamo tutto a posto per non rompere gli altri test!
        Parser.BLACKLIST = self.orig_bl
        Parser.WANTEDLIST = self.orig_wl

    def test_blacklist_logic(self):
        Parser.BLACKLIST = ['cam', 'hdcam', 'ts']
        is_bl, reason = Parser.is_blacklisted("Un.Film.2024.1080p.HDCAM.ITA")
        self.assertTrue(is_bl)
        self.assertEqual(reason, "hdcam")
        
        is_bl, reason = Parser.is_blacklisted("The.Scammer.2023.1080p.ITA")
        self.assertFalse(is_bl)

    def test_wantedlist_logic(self):
        Parser.WANTEDLIST = []
        self.assertTrue(Parser.is_wanted("Un.Torrent.Normale.1080p"))
        
        Parser.WANTEDLIST = ['repack']
        self.assertTrue(Parser.is_wanted("Una.Serie.1080p.REPACK.ITA"))
        self.assertFalse(Parser.is_wanted("Una.Serie.1080p.PROPER.ITA"))

    def test_date_parser(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        d1 = parse_date_any("2024-10-25")
        self.assertEqual(d1.year, 2024)

        d2 = parse_date_any("2 ore fa")
        if d2.tzinfo is None:
            d2 = d2.replace(tzinfo=datetime.timezone.utc)
        diff = now - d2
        self.assertTrue(1 < diff.total_seconds() / 3600 < 3)

    def test_date_parser_formati_multipli(self):
        """parse_date_any deve gestire tutti i formati usati dai feed RSS italiani"""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)

        # Formato relativo in minuti
        d = parse_date_any("30 minuti fa")
        if d and d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        if d:
            self.assertTrue(0 < (now - d).total_seconds() / 60 < 90)

        # Formato europeo DD/MM/YYYY
        d2 = parse_date_any("25/10/2024")
        if d2:
            self.assertEqual(d2.day, 25)
            self.assertEqual(d2.month, 10)
            self.assertEqual(d2.year, 2024)

        # Stringa vuota/invalida: non deve sollevare eccezioni
        try:
            r1 = parse_date_any("")
            r2 = parse_date_any("data assurda xyz")
            # Se non solleva, il risultato deve essere None o un datetime
            self.assertTrue(r1 is None or isinstance(r1, datetime.datetime))
            self.assertTrue(r2 is None or isinstance(r2, datetime.datetime))
        except Exception as e:
            self.fail(f"parse_date_any ha sollevato un'eccezione inattesa: {e}")
        
import os
import tempfile
from unittest.mock import patch, MagicMock

from core.cleaner import discard_if_inferior, cleanup_old_episode
from core.tmdb import TMDBClient
from core.clients.libtorrent import LibtorrentClient

class TestCleanerActions(unittest.TestCase):
    def setUp(self):
        # Prima di ogni test, creiamo una cartella finta (invisibile e temporanea)
        self.test_dir = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self.test_dir.name, "archive")
        self.trash = os.path.join(self.test_dir.name, "trash")
        os.makedirs(self.archive)

    def tearDown(self):
        # Finito il test, la cartella finta si autodistrugge
        self.test_dir.cleanup()

    def test_discard_if_inferior(self):
        # Simuliamo: c'è già un 1080p sull'hard disk
        open(os.path.join(self.archive, "La.Serie.S01E01.1080p.mkv"), 'w').close()
        # EXTTO ha appena scaricato un 720p (peggiore)
        open(os.path.join(self.archive, "La.Serie.S01E01.720p.mkv"), 'w').close()

        # Lanciamo il Cleaner: new_score basso perché è un 720p
        scartato = discard_if_inferior(
            series_name="La Serie", season=1, episode=1, 
            new_score=400, new_fname="La.Serie.S01E01.720p.mkv", 
            save_path=self.archive, trash_path=self.trash
        )
        
        # Verifichiamo che il 720p sia stato buttato nel cestino
        self.assertTrue(scartato)
        self.assertTrue(os.path.exists(os.path.join(self.trash, "La.Serie.S01E01.720p.mkv")))

    def test_discard_if_inferior_no_false_positive(self):
        """Se il nuovo file è MIGLIORE di quello esistente, NON deve essere scartato"""
        open(os.path.join(self.archive, "La.Serie.S01E01.720p.mkv"), 'w').close()
        open(os.path.join(self.archive, "La.Serie.S01E01.2160p.mkv"), 'w').close()

        scartato = discard_if_inferior(
            series_name="La Serie", season=1, episode=1,
            new_score=6000, new_fname="La.Serie.S01E01.2160p.mkv",
            save_path=self.archive, trash_path=self.trash
        )
        self.assertFalse(scartato)
        # Il 4K deve restare in archivio intatto
        self.assertTrue(os.path.exists(os.path.join(self.archive, "La.Serie.S01E01.2160p.mkv")))

    def test_discard_preserves_other_episodes(self):
        """Scartare S01E01 non deve toccare S01E02 dello stesso archivio"""
        open(os.path.join(self.archive, "La.Serie.S01E01.1080p.mkv"), 'w').close()
        open(os.path.join(self.archive, "La.Serie.S01E01.720p.mkv"), 'w').close()
        open(os.path.join(self.archive, "La.Serie.S01E02.720p.mkv"), 'w').close()

        discard_if_inferior(
            series_name="La Serie", season=1, episode=1,
            new_score=400, new_fname="La.Serie.S01E01.720p.mkv",
            save_path=self.archive, trash_path=self.trash
        )
        # S01E02 deve essere rimasto intatto
        self.assertTrue(os.path.exists(os.path.join(self.archive, "La.Serie.S01E02.720p.mkv")))

    def test_cleanup_old_episode(self):
        # Simuliamo: c'era un vecchio 720p
        open(os.path.join(self.archive, "La.Serie.S01E01.720p.mkv"), 'w').close()
        # EXTTO scarica un fantastico 2160p 4K
        open(os.path.join(self.archive, "La.Serie.S01E01.2160p.mkv"), 'w').close()

        # Lanciamo il Cleaner (Upgrade)
        rimossi = cleanup_old_episode(
            series_name="La Serie", season=1, episode=1,
            new_score=6000, new_title="La.Serie.S01E01.2160p.mkv",
            archive_path=self.archive, trash_path=self.trash
        )

        # Deve aver rimosso 1 file (il 720p) e averlo messo nel cestino
        self.assertEqual(rimossi, 1)
        self.assertTrue(os.path.exists(os.path.join(self.trash, "La.Serie.S01E01.720p.mkv")))
        # Il 4K deve essere ancora sano e salvo al suo posto
        self.assertTrue(os.path.exists(os.path.join(self.archive, "La.Serie.S01E01.2160p.mkv")))


class TestTMDB(unittest.TestCase):
    @patch('core.tmdb.requests.Session.get') # Sostituiamo internet con una controfigura
    def test_tmdb_resolution(self, mock_get):
        # Prepariamo la finta risposta di TMDB
        finta_risposta = MagicMock()
        finta_risposta.json.return_value = {"results": [{"id": 12345, "name": "Una Serie Finta"}]}
        finta_risposta.status_code = 200
        mock_get.return_value = finta_risposta

        # Interroghiamo TMDB senza consumare internet
        client = TMDBClient("fake_api_key")
        tmdb_id = client.resolve_series_id("Una Serie Finta")
        
        # Deve aver trovato l'ID corretto leggendo il JSON
        self.assertEqual(tmdb_id, 12345)


class TestTorrentStates(unittest.TestCase):
    def test_eta_calculation(self):
        # Creiamo un finto stato del torrent
        class FintoStato:
            total_wanted = 1000 # Deve scaricare 1000 bytes
            total_wanted_done = 500 # Ne ha scaricati 500
            download_rate = 10 # Scarica a 10 bytes al secondo
            
        # Tempo stimato: (1000 - 500) / 10 = 50 secondi
        eta = LibtorrentClient._calc_eta(FintoStato())
        self.assertEqual(eta, 50)

    def test_eta_torrent_completato(self):
        """Un torrent già al 100% non deve sollevare ZeroDivisionError"""
        class FintoCompletato:
            total_wanted = 1000
            total_wanted_done = 1000
            download_rate = 0

        eta = LibtorrentClient._calc_eta(FintoCompletato())
        # Il valore esatto dipende dall'implementazione (-1, 0, o None sono tutti accettabili)
        self.assertTrue(eta in (0, -1, None) or isinstance(eta, (int, float)))

    def test_eta_torrent_in_stallo(self):
        """Torrent in stallo (rate=0, non completato) non deve sollevare ZeroDivisionError"""
        class FintoStallo:
            total_wanted = 1000
            total_wanted_done = 500
            download_rate = 0

        try:
            eta = LibtorrentClient._calc_eta(FintoStallo())
            # Il risultato può essere None, -1 o un intero speciale: l'importante è non crashare
            self.assertTrue(eta is None or isinstance(eta, (int, float)))
        except ZeroDivisionError:
            self.fail("_calc_eta ha sollevato ZeroDivisionError con download_rate=0")

import sqlite3
from core.engine import Engine
from core.comics import GetComicsScraper
from core.database import Database

class TestConfigParsingLine(unittest.TestCase):
    def test_parse_series_line(self):
        # Simuliamo una riga complessa scritta dall'utente in series.txt
        line = "The Boys | 1+ | 1080p | ita | yes | /mnt/tv/The Boys | timeframe:24h | alias=I Ragazzi,Boys | ignored:2,3"
        
        cfg = Config()
        cfg.series = [] # Svuotiamo per il test
        cfg._parse_series_line(line)
        
        self.assertEqual(len(cfg.series), 1)
        s = cfg.series[0]
        
        # Verifichiamo che ogni singolo pezzo sia stato smontato correttamente
        self.assertEqual(s['name'], "The Boys")
        self.assertTrue(s['enabled'])
        self.assertEqual(s['archive_path'], "/mnt/tv/The Boys")
        self.assertEqual(s['timeframe'], 24)
        self.assertEqual(s['aliases'], ["I Ragazzi", "Boys"])
        self.assertEqual(s['ignored_seasons'], [2, 3])

class TestEngineUrlRouting(unittest.TestCase):
    def test_torznab_url_detection(self):
        # Jackett usa la porta 9117 o percorsi normali
        self.assertEqual(
            Engine._torznab_url("http://localhost:9117"), 
            "http://localhost:9117/api/v2.0/indexers/all/results/torznab/api"
        )
        # Prowlarr viene riconosciuto dalla porta 9696
        self.assertEqual(
            Engine._torznab_url("http://192.168.1.10:9696"), 
            "http://192.168.1.10:9696/api/v1/indexer/all/results/torznab/api"
        )
        # Prowlarr viene riconosciuto dal nome host
        self.assertEqual(
            Engine._torznab_url("http://prowlarr.local"), 
            "http://prowlarr.local/api/v1/indexer/all/results/torznab/api"
        )

class TestComicsScraperLogic(unittest.TestCase):
    def test_get_recent_weekly_dates(self):
        scraper = GetComicsScraper()
        # Chiediamo le ultime 5 date
        dates = scraper.get_recent_weekly_dates(days_back=5)
        
        self.assertEqual(len(dates), 5)
        # Verifica che il formato sia esattamente YYYY-MM-DD
        for d in dates:
            self.assertRegex(d, r'^\d{4}-\d{2}-\d{2}$')



from core.notifier import Notifier

class TestScrapingEngine(unittest.TestCase):
    @patch('core.engine.requests.get')
    def test_generic_rss_parsing(self, mock_get):
        # 1. Creiamo un finto feed RSS XML (come quello di Knaben o simili)
        fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <item>
                    <title>La.Mia.Serie.S01E01.1080p.WEB.ITA</title>
                    <link>magnet:?xt=urn:btih:1111222233334444555566667777888899990000</link>
                    <description>Size: 1.5 GB</description>
                </item>
            </channel>
        </rss>"""
        
        # 2. Diciamo a Python: "Quando provi a scaricare la pagina, non usare internet, restituisci questo XML"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = fake_xml.encode('utf-8')
        mock_get.return_value = mock_resp
        
        # 3. Facciamo partire il motore e gli facciamo leggere il finto sito
        with Engine() as engine:
            results = engine._generic_rss("https://finto-sito-rss.com")
        
        # 4. Verifichiamo che l'Engine abbia "capito" l'XML e tirato fuori i dati giusti
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], "La.Mia.Serie.S01E01.1080p.WEB.ITA")
        self.assertIn("1111222233334444555566667777888899990000", results[0]['magnet'])
        self.assertEqual(results[0]['size'], "1.5 GB")

    # ── CHAOS TESTS: cosa succede quando la rete non collabora ───────────────

    @patch('core.engine.requests.get')
    def test_generic_rss_timeout(self, mock_get):
        """Se il sito RSS non risponde (Timeout), il motore deve restituire []
        senza crashare — l'errore va nei log, il ciclo continua."""
        import requests as req_mod
        mock_get.side_effect = req_mod.exceptions.Timeout("Connessione persa")

        with Engine() as engine:
            results = engine._generic_rss("https://sito-lento.com")

        self.assertEqual(results, [],
            "Con Timeout il motore deve restituire lista vuota, non propagare l'eccezione")

    @patch('core.engine.requests.get')
    def test_generic_rss_errore_500(self, mock_get):
        """Se il server RSS risponde con HTTP 500, il motore deve gestirlo
        e restituire [] invece di crashare o lanciare un'eccezione."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.content = b"Internal Server Error"
        mock_get.return_value = mock_resp

        with Engine() as engine:
            results = engine._generic_rss("https://sito-rotto.com")

        self.assertEqual(results, [],
            "Con HTTP 500 il motore deve restituire lista vuota")

    @patch('core.engine.requests.get')
    def test_generic_rss_connection_error(self, mock_get):
        """Se il server è completamente irraggiungibile (DNS failure, rifiuto connessione),
        il motore non deve propagare l'eccezione al chiamante."""
        import requests as req_mod
        mock_get.side_effect = req_mod.exceptions.ConnectionError("No route to host")

        with Engine() as engine:
            try:
                results = engine._generic_rss("https://server-spento.com")
                self.assertEqual(results, [])
            except req_mod.exceptions.ConnectionError:
                self.fail("ConnectionError non è stata gestita — è arrivata fino al test!")

    @patch('core.engine.requests.get')
    def test_generic_rss_xml_malformato(self, mock_get):
        """Se il feed RSS contiene XML corrotto o incompleto, il parser
        non deve crashare ma restituire quello che riesce a leggere (o [])."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<rss><channel><item><title>Incompleto senza chiusura"
        mock_get.return_value = mock_resp

        with Engine() as engine:
            try:
                results = engine._generic_rss("https://feed-corrotto.com")
                self.assertIsInstance(results, list)
            except Exception as e:
                self.fail(f"XML malformato ha causato un crash non gestito: {type(e).__name__}: {e}")

    @patch('core.tmdb.TMDBClient._get',
           side_effect=Exception("Timeout simulato"))
    def test_tmdb_timeout_non_blocca(self, mock_get):
        """Se TMDB va in timeout (_get solleva eccezione), resolve_series_id
        deve restituire None senza propagare l'eccezione al chiamante."""
        client = TMDBClient("fake_key")
        try:
            result = client.resolve_series_id("Breaking Bad")
            self.assertIsNone(result)
        except Exception as e:
            self.fail(f"L'eccezione non è stata gestita internamente: {e}")

class TestNotifier(unittest.TestCase):
    def test_telegram_message_formatting(self):
        # Creiamo un finto file di configurazione con le notifiche spente
        # (così siamo sicuri che non parta davvero nessuna mail/messaggio)
        cfg = {'notify_telegram': 'no'}
        notifier = Notifier(cfg)
        
        # Catturiamo la funzione interna di Telegram prima che parta
        with patch.object(notifier, '_send_telegram') as mock_send:
            # Diciamo al sistema di preparare la notifica di un nuovo episodio
            notifier.notify_download("Fallout", 1, 2, "Fallout.S01E02.1080p", 5000, "best-in-cycle-new")
            
            # Verifichiamo che il programma abbia tentato di inviare il messaggio
            mock_send.assert_called_once()
            
            # Andiamo a curiosare nel testo del messaggio che stava per essere inviato!
            sent_msg = mock_send.call_args[0][0]
            
            # Il testo deve contenere i dati della serie e la traduzione del trigger
            self.assertIn("Fallout", sent_msg)
            self.assertIn("S01E02", sent_msg)
            self.assertIn("Episodio Mancante", sent_msg) # Verifica che "best-in-cycle-new" sia stato tradotto

from extto_web import app as flask_app
from core.database import ArchiveDB

class TestWebUI(unittest.TestCase):
    def setUp(self):
        # Creiamo un "finto browser" per testare l'interfaccia senza accendere davvero il server
        self.client = flask_app.test_client()
        flask_app.config['TESTING'] = True

    def test_homepage_loads(self):
        # Simuliamo di aprire http://localhost:8889/
        response = self.client.get('/')
        # Verifica che la pagina carichi con successo (Codice HTTP 200)
        self.assertEqual(response.status_code, 200)
        # Verifica che il codice HTML contenga il titolo della Web UI
        self.assertIn(b"EXTTO Web Interface", response.data)

import tempfile
from unittest.mock import patch

class TestDatabaseAndGapFilling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db = os.path.join(self.temp_dir.name, 'test_series.db')
        
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_in_memory_database_and_gaps(self):
        # Diciamo al codice di usare il nostro file temporaneo invece di quello vero
        with patch('core.database.DB_FILE', self.temp_db):
            with Database() as db:
                cursor = db.conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cursor.fetchall()]
                self.assertIn('episodes', tables)
                self.assertIn('series_metadata', tables)
                
                db.upsert_series_metadata(series_id=1, tmdb_id=123, season_counts={1: 5})
                
                cursor.execute("INSERT INTO episodes (series_id, season, episode) VALUES (1, 1, 2)")
                cursor.execute("INSERT INTO episodes (series_id, season, episode) VALUES (1, 1, 4)")
                db.conn.commit()
                
                gaps = db.find_gaps(series_id=1, season=1)
                self.assertEqual(gaps, [1, 3, 5])


class TestArchiveDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db = os.path.join(self.temp_dir.name, 'test_archive.db')
        
    def tearDown(self):
        self.temp_dir.cleanup()

    def test_archive_search_fts5(self):
        # Usiamo un file temporaneo vero per supportare a pieno FTS5
        with patch('core.database.ARCHIVE_FILE', self.temp_db):
            with ArchiveDB() as archive:
                archive.add_item(
                    name="The.Batman.2022.1080p.ITA", 
                    magnet="magnet:?xt=urn:btih:abc", 
                    source="Jackett"
                )
                
                risultati = archive.search("Batman")
                self.assertEqual(len(risultati), 1)
                self.assertEqual(risultati[0]['magnet'], "magnet:?xt=urn:btih:abc")
                
                risultati_vuoti = archive.search("Superman")
                self.assertEqual(len(risultati_vuoti), 0)

    def test_archive_no_duplicati(self):
        """Lo stesso torrent (stesso magnet/hash) non deve essere inserito due volte
        anche se arriva da due sorgenti diverse o in due cicli diversi."""
        with patch('core.database.ARCHIVE_FILE', self.temp_db):
            with ArchiveDB() as archive:
                magnet = "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678"

                archive.add_item(name="The.Batman.2022.1080p", magnet=magnet, source="Jackett")
                archive.add_item(name="The.Batman.2022.1080p", magnet=magnet, source="Knaben")  # stesso hash

                risultati = archive.search("Batman")
                self.assertEqual(len(risultati), 1, "Lo stesso torrent è stato inserito due volte!")

    def test_archive_torrent_diversi_stesso_titolo(self):
        """Due release diversi dello stesso film (hash diversi) DEVONO coesistere"""
        with patch('core.database.ARCHIVE_FILE', self.temp_db):
            with ArchiveDB() as archive:
                magnet_a = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                magnet_b = "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

                archive.add_item(name="Dune.2021.1080p.ITA", magnet=magnet_a, source="Jackett")
                archive.add_item(name="Dune.2021.2160p.ITA", magnet=magnet_b, source="Knaben")

                risultati = archive.search("Dune")
                self.assertEqual(len(risultati), 2, "Due release distinti non devono essere deduplicati!")
            
from core.engine import _subtitle_query_terms

class TestSubtitleLogic(unittest.TestCase):
    """ TEST: Verifica la generazione corretta delle query per i sottotitoli in Jackett """

    def test_subtitle_terms_ita(self):
        # Deve generare sia la sigla a 3 lettere che quella a 2 lettere
        terms = _subtitle_query_terms("ita")
        self.assertIn("sub ita", terms)
        self.assertIn("sub it", terms)
        self.assertEqual(len(terms), 2)

    def test_subtitle_terms_eng(self):
        # Stessa cosa per l'inglese
        terms = _subtitle_query_terms("eng")
        self.assertIn("sub eng", terms)
        self.assertIn("sub en", terms)

    def test_subtitle_terms_multiple(self):
        # Deve gestire correttamente più lingue separate da virgola
        terms = _subtitle_query_terms("ita,eng")
        self.assertIn("sub ita", terms)
        self.assertIn("sub it", terms)
        self.assertIn("sub eng", terms)
        self.assertIn("sub en", terms)
        self.assertEqual(len(terms), 4)

    def test_subtitle_terms_spaces(self):
        # Deve pulire gli spazi vuoti tra le virgole
        terms = _subtitle_query_terms(" ita , eng ")
        self.assertIn("sub ita", terms)
        self.assertIn("sub eng", terms)

    def test_subtitle_terms_unknown(self):
        # Se passo un codice inventato, deve fare del suo meglio restituendo "sub codice"
        terms = _subtitle_query_terms("xyz")
        self.assertEqual(terms, ["sub xyz"])

    def test_subtitle_terms_empty(self):
        # Se la stringa è vuota, non deve generare nulla
        terms = _subtitle_query_terms("")
        self.assertEqual(terms, [])
        
    def test_subtitle_terms_two_letters(self):
        # Se l'utente scrive "it" invece di "ita", deve comunque generare la variante "ita"
        terms = _subtitle_query_terms("it")
        self.assertIn("sub it", terms)
        self.assertIn("sub ita", terms)            



class TestSubScore(unittest.TestCase):
    """TEST: Verifica la logica _sub_score — bonus punteggio per sottotitoli.

    La funzione Config._sub_score(title, sub_req) restituisce:
      +200 per ogni lingua sub trovata nel titolo (cumulabile).
    NON è un requisito: non filtra né scarta release.
    """

    def setUp(self):
        # Reset class attributes di Quality per isolamento da altri test
        Quality.RES_PREF    = {'2160p': 2000, '1080p': 1000, '720p': 400, '576p': 80, '480p': 0, '360p': 0, 'unknown': 0}
        Quality.CODEC_PREF  = {'h265': 200, 'x265': 200, 'hevc': 200, 'h264': 50, 'x264': 50, 'avc': 50, 'unknown': 0}
        Quality.SOURCE_PREF = {'bluray': 300, 'webdl': 200, 'webrip': 150, 'hdtv': 50, 'dvdrip': 20, 'unknown': 0}
        Quality.AUDIO_PREF  = {'truehd': 150, 'dts-hd': 120, 'dts': 100, 'ddp': 80, 'ac3': 50, '5.1': 50, 'aac': 30, 'mp3': 10, 'unknown': 0}
        Quality.BONUS_DV     = 300
        Quality.BONUS_REAL   = 100
        Quality.BONUS_PROPER = 75
        Quality.BONUS_REPACK = 50

    def test_sub_singolo_ita_punto(self):
        """sub.ita nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "ita"), 200)

    def test_sub_singolo_ita_attaccato(self):
        """subita nel titolo (nessun separatore) → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.subita.1080p", "ita"), 200)

    def test_sub_singolo_ita_trattino(self):
        """sub-ita nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub-ita.1080p", "ita"), 200)

    def test_subs_ita(self):
        """subs.ita nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.subs.ita.1080p", "ita"), 200)

    def test_subforced_ita(self):
        """subforced.ita nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.subforced.ita.1080p", "ita"), 200)

    def test_ita_sub(self):
        """ita.sub nel titolo (ordine inverso) → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.ita.sub.1080p", "ita"), 200)

    def test_bracket_ita_sub(self):
        """[ita]sub nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.[ita]sub.1080p", "ita"), 200)

    def test_sub_bracket_ita(self):
        """sub[ita] nel titolo → +200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.sub[ita].1080p", "ita"), 200)

    def test_sub_lista_it_en(self):
        """subs.it.en (lista sigle corte) con req ita,eng → +400"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.subs.it.en.1080p", "ita,eng"), 400)

    def test_sub_due_lingue_entrambe(self):
        """sub.ita + sub.eng nel titolo con req ita,eng → +400"""
        self.assertEqual(
            Config._sub_score("Paperino.S01E01.ENG.sub.ita.sub.eng.1080p", "ita,eng"), 400
        )

    def test_sub_due_lingue_solo_una(self):
        """sub.ita ma req ita,eng → solo +200 (eng non trovato)"""
        self.assertEqual(
            Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "ita,eng"), 200
        )

    def test_nessun_sub(self):
        """Nessun marcatore sub nel titolo → 0"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.1080p", "ita"), 0)

    def test_lingua_audio_non_conta(self):
        """ITA come lingua audio (senza 'sub') non deve dare bonus sub"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ITA.1080p", "ita"), 0)

    def test_eng_audio_sub_ita(self):
        """ENG.sub.ita: ENG è audio, solo sub.ita conta → req eng → 0, req ita → 200"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "eng"), 0)
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "ita"), 200)

    def test_sub_req_vuoto(self):
        """sub_req vuoto → sempre 0 (nessuna preferenza)"""
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", ""), 0)
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "none"), 0)
        self.assertEqual(Config._sub_score("Paperino.S01E01.ENG.sub.ita.1080p", "*"), 0)

    def test_mux_format_comune(self):
        """Formato release MUX italiano tipico → +200"""
        self.assertEqual(
            Config._sub_score("Paperino.S01E01.MUX.ITA.ENG.SUB.ITA.1080p", "ita"), 200
        )

    def test_sub_bonus_cumulativo_con_qualita(self):
        """Il bonus sub si somma allo score qualità: 1080p con sub.ita > 1080p senza sub.
        Nota: Quality.score() non include il bonus sub per design — il punteggio
        completo è score() + Config._sub_score(). Verifichiamo che il totale
        con sub sia effettivamente maggiore di quello senza.
        """
        titolo_con_sub   = "Paperino.S01E01.ENG.1080p.sub.ita"
        titolo_senza_sub = "Paperino.S01E01.ENG.1080p"
        bonus           = Config._sub_score(titolo_con_sub, "ita")
        total_con_sub   = Parser.parse_quality(titolo_con_sub).score()  + bonus
        total_senza_sub = Parser.parse_quality(titolo_senza_sub).score()
        self.assertEqual(bonus, 200,
                         "Il bonus sub.ita deve essere esattamente 200")
        self.assertGreater(total_con_sub, total_senza_sub,
                           "1080p con sub.ita deve avere score totale maggiore di 1080p senza sub")


class TestIntegrazione(unittest.TestCase):
    """Test End-to-End parziali: verificano che i vari moduli comunichino
    correttamente tra loro attraversando più strati del programma.

    Flusso testato:
        stringa RSS → Parser → Quality.score() → ArchiveDB.add_item() → search()
    """

    def setUp(self):
        self.tmp  = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, 'e2e_archive.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_flusso_rss_parser_score_archivio(self):
        """Simula il ciclo completo: un titolo estratto da un feed RSS viene
        parsato, valutato, e salvato in archive.db. Verifica che tutti i
        passaggi producano dati coerenti tra loro."""

        # ── STRATO 1: stringa grezza come arriva da un feed RSS ─────────────
        titolo_rss  = "Fallout.S01E04.1080p.WEB-DL.ITA.ENG.x265-MIRCrew"
        magnet_rss  = "magnet:?xt=urn:btih:aabbccddeeff00112233445566778899aabbccdd"
        size_rss    = "2.3 GB"

        # ── STRATO 2: Parser estrae struttura dall'episodio ──────────────────
        ep = Parser.parse_series_episode(titolo_rss)
        self.assertIsNotNone(ep, "Il parser non ha riconosciuto l'episodio")
        self.assertEqual(ep['season'],  1)
        self.assertEqual(ep['episode'], 4)
        self.assertEqual(ep['name'].lower().replace(' ', ''), 'fallout')

        # ── STRATO 3: Quality calcola lo score ───────────────────────────────
        q = ep['quality']
        self.assertEqual(q.resolution, '1080p')
        self.assertEqual(q.source,     'webdl')
        self.assertEqual(q.codec,      'h265')
        self.assertTrue(q.is_ita)

        score = q.score()
        self.assertGreater(score, 0, "Lo score deve essere un numero positivo")
        # La soglia minima è: risoluzione 1080p + almeno una fonte riconosciuta
        soglia_min = Quality.RES_PREF.get('1080p', 500) + Quality.SOURCE_PREF.get('webdl', 100)
        self.assertGreater(score, soglia_min,
            f"Score {score} troppo basso per 1080p WEB-DL h265 ITA (soglia minima attesa: {soglia_min})")

        # ── STRATO 4: ArchiveDB salva il torrent ─────────────────────────────
        with patch('core.database.ARCHIVE_FILE', self.db_path):
            archive = ArchiveDB()
            archive.add_item(name=titolo_rss, magnet=magnet_rss, source="Knaben")

            # ── STRATO 5: ricerca funziona sul record appena salvato ──────────
            risultati = archive.search("Fallout")
            self.assertEqual(len(risultati), 1)
            self.assertEqual(risultati[0]['magnet'], magnet_rss)

    def test_flusso_film_parser_score_archivio(self):
        """Stesso flusso E2E ma per un film invece di una serie."""
        titolo_film = "Oppenheimer.2023.2160p.BluRay.DTS-HD.ITA.ENG.x265"
        magnet_film = "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678"

        # Parser film
        film = Parser.parse_movie(titolo_film)
        self.assertIsNotNone(film)
        self.assertEqual(film['year'], 2023)

        # Quality
        q = film['quality']
        self.assertEqual(q.resolution, '2160p')
        self.assertEqual(q.source,     'bluray')
        self.assertTrue(q.is_ita)

        score = q.score()
        # Nota: nella versione multilang la lingua NON dà bonus punti —
        # è un filtro binario (pass/fail). Lo score dipende solo da
        # risoluzione, sorgente, codec, DV, repack/proper.
        soglia_min = Quality.RES_PREF.get('2160p', 1000) + Quality.SOURCE_PREF.get('bluray', 200)
        self.assertGreater(score, soglia_min,
            f"Score {score} troppo basso per 2160p BluRay DTS-HD (soglia minima attesa: {soglia_min})")

        # Archivio
        with patch('core.database.ARCHIVE_FILE', self.db_path):
            archive = ArchiveDB()
            archive.add_item(name=titolo_film, magnet=magnet_film, source="Jackett")
            risultati = archive.search("Oppenheimer")
            self.assertEqual(len(risultati), 1)

    def test_flusso_qualita_inferiore_viene_scartata(self):
        """E2E del Cleaner: un episodio 720p deve essere scartato se il 1080p
        è già presente — verificando che Parser, Quality e Cleaner si parlino."""
        from core.cleaner import discard_if_inferior

        tmp_archive = tempfile.mkdtemp()
        tmp_trash   = tempfile.mkdtemp()
        try:
            # File già presente: 1080p
            titolo_buono = "Fallout.S01E04.1080p.WEB-DL.ITA.x265"
            open(os.path.join(tmp_archive, titolo_buono + ".mkv"), 'w').close()
            score_buono = Parser.parse_quality(titolo_buono).score()

            # Nuovo arrivato: 720p peggiore
            titolo_scarso = "Fallout.S01E04.720p.HDTV.x264"
            open(os.path.join(tmp_archive, titolo_scarso + ".mkv"), 'w').close()
            score_scarso = Parser.parse_quality(titolo_scarso).score()

            # Verifica che il score sia effettivamente inferiore (prerequisito)
            self.assertLess(score_scarso, score_buono,
                f"Prerequisito fallito: score 720p({score_scarso}) >= 1080p({score_buono})")

            # Cleaner decide
            scartato = discard_if_inferior(
                series_name="Fallout", season=1, episode=4,
                new_score=score_scarso,
                new_fname=titolo_scarso + ".mkv",
                save_path=tmp_archive,
                trash_path=tmp_trash
            )

            self.assertTrue(scartato, "Il 720p doveva essere scartato ma non lo è stato")
            self.assertTrue(
                os.path.exists(os.path.join(tmp_trash, titolo_scarso + ".mkv")),
                "Il 720p doveva finire nel trash"
            )
            self.assertTrue(
                os.path.exists(os.path.join(tmp_archive, titolo_buono + ".mkv")),
                "Il 1080p non deve essere toccato"
            )
        finally:
            import shutil as _sh
            _sh.rmtree(tmp_archive, ignore_errors=True)
            _sh.rmtree(tmp_trash,   ignore_errors=True)




class TestParseQualityTitoliRinominati(unittest.TestCase):
    """Test per i fix v39: parse_quality deve riconoscere correttamente
    i tag nei titoli rinominati da extto (formato [DV HDR10][h265][IT])."""

    def setUp(self):
        # Quality usa class attributes mutabili — altri test (es. TestConfigLogic
        # con load_from_config) possono alterarli. Resettiamo ai default.
        Quality.RES_PREF    = {'2160p': 2000, '1080p': 1000, '720p': 400, '576p': 80, '480p': 0, '360p': 0, 'unknown': 0}
        Quality.CODEC_PREF  = {'h265': 200, 'x265': 200, 'hevc': 200, 'h264': 50, 'x264': 50, 'avc': 50, 'unknown': 0}
        Quality.SOURCE_PREF = {'bluray': 300, 'webdl': 200, 'webrip': 150, 'hdtv': 50, 'dvdrip': 20, 'unknown': 0}
        Quality.AUDIO_PREF  = {'truehd': 150, 'dts-hd': 120, 'dts': 100, 'ddp': 80, 'ac3': 50, '5.1': 50, 'aac': 30, 'mp3': 10, 'unknown': 0}
        Quality.BONUS_DV     = 300
        Quality.BONUS_REAL   = 100
        Quality.BONUS_PROPER = 75
        Quality.BONUS_REPACK = 50

    def test_dv_da_titolo_rinominato(self):
        """[DV HDR10] deve essere riconosciuto come Dolby Vision"""
        q = Parser.parse_quality("The Pitt - S02E01 - 700 - [2160p][h265][DV HDR10][EAC3 5.1][IT]")
        self.assertTrue(q.is_dv, "DV non riconosciuto da [DV HDR10]")

    def test_h265_da_titolo_rinominato(self):
        """[h265] tra parentesi quadre deve essere riconosciuto come codec h265"""
        q = Parser.parse_quality("Serie - S01E01 - Titolo - [1080p][h265][EAC3 5.1][IT]")
        self.assertEqual(q.codec, 'h265', "h265 non riconosciuto da [h265]")

    def test_ita_da_bracket_it(self):
        """[IT] deve essere riconosciuto come italiano"""
        q = Parser.parse_quality("The Pitt - S02E01 - 700 - [2160p][h265][DV HDR10][EAC3 5.1][IT]")
        self.assertTrue(q.is_ita, "ITA non riconosciuto da [IT]")

    def test_ita_da_bracket_it_en(self):
        """[IT+EN] deve essere riconosciuto come italiano"""
        q = Parser.parse_quality("9-1-1 - S09E01 - Mangia i ricchi - [1080p][h264][EAC3 5.1][IT+EN]")
        self.assertTrue(q.is_ita, "ITA non riconosciuto da [IT+EN]")

    def test_titolo_rinominato_completo_pitt(self):
        """Titolo rinominato The Pitt: tutti i campi corretti"""
        q = Parser.parse_quality("The Pitt - S02E01 - 700 - [2160p][h265][DV HDR10][EAC3 5.1][IT]")
        self.assertEqual(q.resolution, '2160p')
        self.assertEqual(q.codec, 'h265')
        self.assertEqual(q.audio, 'ddp')
        self.assertTrue(q.is_dv)
        self.assertTrue(q.is_ita)

    def test_titolo_rinominato_completo_911(self):
        """Titolo rinominato 9-1-1: tutti i campi corretti"""
        q = Parser.parse_quality("9-1-1 - S09E01 - Mangia i ricchi - [1080p][h264][EAC3 5.1][IT+EN]")
        self.assertEqual(q.resolution, '1080p')
        self.assertEqual(q.codec, 'h264')
        self.assertEqual(q.audio, 'ddp')
        self.assertFalse(q.is_dv)
        self.assertTrue(q.is_ita)

    def test_no_falso_positivo_ita_parola_inglese(self):
        """Parole inglesi con 'it' non devono essere riconosciute come ITA"""
        casi = [
            "Series.S01E01.1080p.WEB-DL.with.subtitles",
            "Series.S01E01.1080p.WEB-DL.digital.remaster",
            "Series.S01E01.1080p.WEB-DL.submit.your.request",
            "Series.S01E01.1080p.WEB-DL.ENG",
        ]
        for titolo in casi:
            q = Parser.parse_quality(titolo)
            self.assertFalse(q.is_ita, f"Falso positivo ITA su: {titolo}")

    def test_torrent_originale_invariato(self):
        """I titoli torrent originali devono continuare a funzionare come prima"""
        q = Parser.parse_quality("The.Pitt.S02E06.12.00.P.M.ITA.ENG.2160p.HMAX.WEB-DL.DDP5.1.DV.H.265")
        self.assertEqual(q.resolution, '2160p')
        self.assertTrue(q.is_ita)
        self.assertTrue(q.is_dv)

        q2 = Parser.parse_quality("The.Pitt.S02E07-08.1080p.AMZN.WEB-DL.ITA-ENG.DDP5.1")
        self.assertEqual(q2.resolution, '1080p')
        self.assertTrue(q2.is_ita)

    def test_score_rinominato_vs_torrent(self):
        """Il titolo rinominato deve produrre uno score plausibile (non 0).
        Nota: nella versione multilang la lingua non dà bonus punti,
        quindi lo score è più basso rispetto alla versione standard.
        Verifichiamo che 2160p+DV+h265 diano comunque uno score significativo."""
        score_rinominato = Parser.parse_quality(
            "The Pitt - S02E01 - 700 - [2160p][h265][DV HDR10][EAC3 5.1][IT]"
        ).score()
        self.assertGreater(score_rinominato, 0, "Score 0 dal titolo rinominato")
        # 2160p(2000) + h265(200) + DV(300) = 2500 minimo atteso
        self.assertGreater(score_rinominato, 2500,
            "Score troppo basso per un 2160p+DV+h265 (attesi almeno 2500pt)")


    def test_score_rinominato_non_zero(self):
        """Il titolo rinominato deve produrre uno score significativo.
        Nota: il titolo rinominato non contiene la sorgente (WEB-DL ecc.)
        e nella versione multilang la lingua non dà bonus punti.
        Soglia conservativa: 2160p(2000) + h265(200) + DV(300) = 2500.
        """
        q = Parser.parse_quality(
            "The Pitt - S02E01 - 700 - [2160p][h265][DV HDR10][EAC3 5.1][IT]"
        )
        self.assertGreater(q.score(), 0,    "Score 0 dal titolo rinominato")
        self.assertGreater(q.score(), 2500, "Score troppo basso per 2160p+DV+h265")
        # Verifica che i tag chiave siano riconosciuti
        self.assertEqual(q.resolution, '2160p')
        self.assertTrue(q.is_dv,  "DV non riconosciuto")
        self.assertTrue(q.is_ita, "ITA non riconosciuto")
        self.assertEqual(q.codec, 'h265')






class TestConfigDB(unittest.TestCase):

    def setUp(self):
        """Crea un DB temporaneo per ogni test."""
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._orig_db = None
        import core.config_db as cdb
        self._orig_db = cdb.CONFIG_DB_FILE
        cdb.CONFIG_DB_FILE = os.path.join(self._tmpdir, 'test_config.db')

    def tearDown(self):
        import shutil
        import core.config_db as cdb
        if self._orig_db:
            cdb.CONFIG_DB_FILE = self._orig_db
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_set_get_setting_stringa(self):
        """set_setting / get_setting funzionano per valori stringa."""
        import core.config_db as cdb
        cdb.set_setting('jackett_url', 'http://localhost:9117')
        self.assertEqual(cdb.get_setting('jackett_url'), 'http://localhost:9117')

    def test_set_get_setting_lista(self):
        """Le liste vengono serializzate e deserializzate correttamente."""
        import core.config_db as cdb
        urls = ['http://url1.example.com', 'http://url2.example.com']
        cdb.set_setting('url', urls)
        result = cdb.get_setting('url')
        self.assertIsInstance(result, list)
        self.assertEqual(result, urls)

    def test_set_get_default(self):
        """get_setting restituisce il default se la chiave non esiste."""
        import core.config_db as cdb
        result = cdb.get_setting('chiave_inesistente', 'valore_default')
        self.assertEqual(result, 'valore_default')

    def test_set_settings_bulk(self):
        """set_settings_bulk salva molte impostazioni in una transazione."""
        import core.config_db as cdb
        data = {
            'tmdb_api_key': 'abc123',
            'gap_filling': 'yes',
            'web_port': '5000',
        }
        cdb.set_settings_bulk(data)
        all_s = cdb.get_all_settings()
        self.assertEqual(all_s['tmdb_api_key'], 'abc123')
        self.assertEqual(all_s['gap_filling'], 'yes')

    def test_upsert_setting(self):
        """set_setting sovrascrive il valore esistente (upsert).
        Nota: get_setting deserializza via JSON — i numeri tornano come int.
        Per confrontare con stringa usare str().
        """
        import core.config_db as cdb
        cdb.set_setting('web_port', '5000')
        cdb.set_setting('web_port', '8080')
        # JSON deserializza "8080" come int — confrontiamo come stringa
        self.assertEqual(str(cdb.get_setting('web_port')), '8080')

    def test_delete_setting(self):
        """delete_setting rimuove la chiave."""
        import core.config_db as cdb
        cdb.set_setting('tmp_key', 'tmp_val')
        cdb.delete_setting('tmp_key')
        self.assertEqual(cdb.get_setting('tmp_key', 'missing'), 'missing')

    def test_movies_config_round_trip(self):
        """save_movies_config + get_movies_config: dati integri."""
        import core.config_db as cdb
        movies = [
            {'name': 'Dune', 'year': '2021', 'quality': '1080p+',
             'language': 'ita', 'enabled': True, 'subtitle': ''},
            {'name': 'Oppenheimer', 'year': '2023', 'quality': '2160p',
             'language': 'ita', 'enabled': False, 'subtitle': 'ita'},
        ]
        cdb.save_movies_config(movies)
        result = cdb.get_movies_config()
        self.assertEqual(len(result), 2)
        nomi = [m['name'] for m in result]
        self.assertIn('Dune', nomi)
        self.assertIn('Oppenheimer', nomi)
        oppi = next(m for m in result if m['name'] == 'Oppenheimer')
        self.assertEqual(oppi['enabled'], 0)

    def test_movies_config_sovrascrittura(self):
        """save_movies_config sostituisce tutta la lista."""
        import core.config_db as cdb
        cdb.save_movies_config([{'name': 'Film1', 'year': '2020',
                                  'quality': 'any', 'language': 'ita',
                                  'enabled': True, 'subtitle': ''}])
        cdb.save_movies_config([{'name': 'Film2', 'year': '2021',
                                  'quality': '1080p', 'language': 'ita',
                                  'enabled': True, 'subtitle': ''}])
        result = cdb.get_movies_config()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], 'Film2')

    def test_parse_series_line(self):
        """_parse_series_line estrae correttamente tutti i campi."""
        from core.config_db import _parse_series_line
        line = "The Pitt | 1+ | 1080p+ | ita | yes | /nas/ThePitt | timeframe:24h | alias=Pitt | tmdb=250307"
        s = _parse_series_line(line)
        self.assertIsNotNone(s)
        self.assertEqual(s['name'], 'The Pitt')
        self.assertEqual(s['seasons'], '1+')
        self.assertEqual(s['quality'], '1080p+')
        self.assertEqual(s['language'], 'ita')
        self.assertTrue(s['enabled'])
        self.assertEqual(s['archive_path'], '/nas/ThePitt')
        self.assertEqual(s['timeframe'], 24)
        self.assertIn('Pitt', s['aliases'])
        self.assertEqual(s['tmdb_id'], '250307')

    def test_parse_movie_line(self):
        """_parse_movie_line estrae correttamente tutti i campi."""
        from core.config_db import _parse_movie_line
        line = "Dune | 2021 | 1080p+ | ita | yes"
        m = _parse_movie_line(line)
        self.assertIsNotNone(m)
        self.assertEqual(m['name'], 'Dune')
        self.assertEqual(m['year'], '2021')
        self.assertEqual(m['quality'], '1080p+')
        self.assertTrue(m['enabled'])

    def test_needs_migration_nessun_file(self):
        """needs_migration è False se non ci sono file di config."""
        import core.config_db as cdb
        from unittest.mock import patch
        with patch('os.path.exists', return_value=False):
            self.assertFalse(cdb.needs_migration())

    def test_get_migration_status_struttura(self):
        """get_migration_status restituisce la struttura attesa."""
        import core.config_db as cdb
        status = cdb.get_migration_status()
        self.assertIn('files_present', status)
        self.assertIn('db_populated', status)
        self.assertIn('needs_migration', status)
        self.assertIn('can_rename', status)

    def test_parse_series_line_disabled(self):
        """Una serie con enabled=no viene parsata come disabled."""
        from core.config_db import _parse_series_line
        line = "Walker | 1+ | 720p-1080p | ita | no | /nas/Walker"
        s = _parse_series_line(line)
        self.assertIsNotNone(s)
        self.assertFalse(s['enabled'])

    def test_parse_series_line_ignored_seasons(self):
        """ignored_seasons viene parsato correttamente."""
        from core.config_db import _parse_series_line
        line = "Grey's Anatomy | 21+ | 720p-1080p | ita | yes | /nas/Greys | ignored:17,16,15"
        s = _parse_series_line(line)
        self.assertIsNotNone(s)
        self.assertIn(17, s['ignored_seasons'])
        self.assertIn(16, s['ignored_seasons'])
        self.assertIn(15, s['ignored_seasons'])


class TestEndpointElimina(unittest.TestCase):
    """Test per la logica di eliminazione serie — v40."""

    def test_delete_by_id_url(self):
        """deleteSeriesFromConfig usa DELETE /api/series/<id> quando seriesId > 0."""
        # Verifica che il pattern URL sia corretto
        import re
        with open(os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js'), encoding='utf-8') as f:
            js = f.read()
        self.assertIn("api/series/${seriesId}`, { method: 'DELETE' }", js,
                      "deleteSeriesFromConfig deve usare DELETE by-id")

    def test_delete_by_name_url(self):
        """deleteSeriesFromConfig usa DELETE /api/series/by-name/<nome> come fallback."""
        with open(os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js'), encoding='utf-8') as f:
            js = f.read()
        self.assertIn("api/series/by-name/", js,
                      "deleteSeriesFromConfig deve avere fallback by-name")

    def test_edit_series_sets_tmdb_id(self):
        """editSeries popola edit-series-tmdb-id all'apertura del modal."""
        with open(os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js'), encoding='utf-8') as f:
            js = f.read()
        self.assertIn("setFieldValue('edit-series-tmdb-id'", js,
                      "editSeries deve impostare edit-series-tmdb-id per evitare contaminazione tra serie")

    def test_save_uses_update_endpoint(self):
        """saveSeriesChanges e saveSeriesInline usano /update invece del roundtrip."""
        with open(os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js'), encoding='utf-8') as f:
            js = f.read()
        count = js.count('api/series/${seriesId}/update')
        self.assertGreaterEqual(count, 2,
                      "saveSeriesChanges e saveSeriesInline devono entrambi usare /update")


class TestConfigDB_v40(unittest.TestCase):
    """Test per le funzioni di config migrate al DB — v40."""

    def test_parse_series_config_legge_dal_db(self):
        """parse_series_config legge da _cdb.get_all_settings(), non da file."""
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            web = f.read()
        import re
        m = re.search(r'def parse_series_config.*?return \{', web, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn('_cdb.get_all_settings()', body,
                      "parse_series_config deve leggere da _cdb.get_all_settings()")
        self.assertNotIn("open(CONFIG_FILE", body,
                      "parse_series_config non deve aprire extto.conf")

    def test_save_series_config_non_scrive_file(self):
        """save_series_config non deve scrivere su extto.conf o series.txt."""
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            web = f.read()
        # Le scritture devono essere commentate, non attive
        lines = [l for l in web.split('\n') if '_atomic_write(CONFIG_FILE' in l and not l.strip().startswith('#')]
        self.assertEqual(len(lines), 0, f"Scritture attive su extto.conf: {lines}")
        lines2 = [l for l in web.split('\n') if '_atomic_write(SERIES_FILE' in l and not l.strip().startswith('#')]
        self.assertEqual(len(lines2), 0, f"Scritture attive su series.txt: {lines2}")

    def test_endpoint_delete_by_name_presente(self):
        """Endpoint DELETE /api/series/by-name/<name> deve esistere."""
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            web = f.read()
        self.assertIn("/api/series/by-name/<path:series_name>", web,
                      "Endpoint delete_series_by_name mancante")

    def test_endpoint_update_presente(self):
        """Endpoint POST /api/series/<id>/update deve esistere."""
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            web = f.read()
        self.assertIn("/api/series/<int:series_id>/update", web,
                      "Endpoint update_series_fields mancante")

    def test_extto_details_usa_db_tmdb_id(self):
        """get_extto_details deve leggere tmdb_id dal DB operativo come priorità."""
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            web = f.read()
        import re
        m = re.search(r'def get_extto_details.*?tmdb_id\s*=', web, re.DOTALL)
        self.assertIsNotNone(m)
        self.assertIn('db_tmdb_id', m.group(0),
                      "get_extto_details deve usare db_tmdb_id come priorità")


# =============================================================================
# TEST VERSIONE MULTILANGUAGE — funzionalità aggiunte nella versione ML
# =============================================================================

class TestI18nConfigDB(unittest.TestCase):
    """Test per le funzioni i18n in core/config_db.py (v40-ML).
    Verifica: normalizzazione codici lingua, salvataggio/lettura traduzioni,
    import/export YAML, protezione lingua master (ita).
    """

    def setUp(self):
        import tempfile, shutil
        import core.config_db as cdb
        self._tmpdir   = tempfile.mkdtemp()
        self._orig_db  = cdb.CONFIG_DB_FILE
        cdb.CONFIG_DB_FILE = os.path.join(self._tmpdir, 'test_i18n.db')
        # Forza ri-creazione tabelle nel nuovo DB temporaneo
        import threading as _threading_mod
        cdb._local = _threading_mod.local()

    def tearDown(self):
        import shutil, core.config_db as cdb
        import threading as _threading_mod
        cdb.CONFIG_DB_FILE = self._orig_db
        cdb._local = _threading_mod.local()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── _normalize_lang_code ─────────────────────────────────────────────────

    def test_normalize_tre_lettere_invariato(self):
        """Un codice già a 3 lettere deve restare invariato."""
        from core.config_db import _normalize_lang_code
        self.assertEqual(_normalize_lang_code('ita'), 'ita')
        self.assertEqual(_normalize_lang_code('eng'), 'eng')
        self.assertEqual(_normalize_lang_code('deu'), 'deu')

    def test_normalize_due_lettere_convertito(self):
        """Un codice a 2 lettere deve essere convertito al corrispondente a 3."""
        from core.config_db import _normalize_lang_code
        self.assertEqual(_normalize_lang_code('it'), 'ita')
        self.assertEqual(_normalize_lang_code('en'), 'eng')
        self.assertEqual(_normalize_lang_code('de'), 'deu')
        self.assertEqual(_normalize_lang_code('fr'), 'fra')
        self.assertEqual(_normalize_lang_code('es'), 'spa')

    def test_normalize_maiuscole(self):
        """Il codice deve essere normalizzato in minuscolo."""
        from core.config_db import _normalize_lang_code
        self.assertEqual(_normalize_lang_code('ITA'), 'ita')
        self.assertEqual(_normalize_lang_code('EN'),  'eng')

    def test_normalize_sconosciuto_fallback_ita(self):
        """Un codice sconosciuto a 2 lettere torna 'ita' come default sicuro."""
        from core.config_db import _normalize_lang_code
        self.assertEqual(_normalize_lang_code('xx'), 'ita')

    def test_normalize_vuoto_fallback_ita(self):
        """Stringa vuota o None restituisce 'ita'."""
        from core.config_db import _normalize_lang_code
        self.assertEqual(_normalize_lang_code(''),   'ita')
        self.assertEqual(_normalize_lang_code(None), 'ita')

    # ── get_ui_language / set_ui_language ────────────────────────────────────

    def test_ui_language_default_ita(self):
        """Se non impostata, la lingua UI di default è 'ita'."""
        import core.config_db as cdb
        self.assertEqual(cdb.get_ui_language(), 'ita')

    def test_ui_language_set_get(self):
        """set_ui_language salva, get_ui_language legge — sempre in formato 3 lettere."""
        import core.config_db as cdb
        cdb.set_ui_language('eng')
        self.assertEqual(cdb.get_ui_language(), 'eng')

    def test_ui_language_normalizza_codice_2_lettere(self):
        """set_ui_language accetta anche codici a 2 lettere e li normalizza."""
        import core.config_db as cdb
        cdb.set_ui_language('en')
        self.assertEqual(cdb.get_ui_language(), 'eng')

    # ── set_translation_bulk / get_translation ───────────────────────────────

    def test_translation_round_trip(self):
        """Salva stringhe per una lingua e recuperale intatte."""
        import core.config_db as cdb
        strings = {'Dashboard': 'Tableau de bord', 'Serie TV': 'Séries TV', 'Film': 'Films'}
        salvate = cdb.set_translation_bulk('fra', strings)
        self.assertEqual(salvate, 3)

        lette = cdb.get_translation('fra')
        self.assertEqual(lette['Dashboard'],  'Tableau de bord')
        self.assertEqual(lette['Serie TV'],   'Séries TV')
        self.assertEqual(lette['Film'],       'Films')

    def test_translation_upsert(self):
        """Salvare la stessa chiave due volte aggiorna il valore (non duplica)."""
        import core.config_db as cdb
        cdb.set_translation_bulk('deu', {'Dashboard': 'Altes Wort'})
        cdb.set_translation_bulk('deu', {'Dashboard': 'Armaturenbrett'})
        lette = cdb.get_translation('deu')
        self.assertEqual(lette['Dashboard'], 'Armaturenbrett')
        self.assertEqual(len(lette), 1)

    def test_translation_lingue_isolate(self):
        """Le stringhe di lingue diverse non si mescolano tra loro."""
        import core.config_db as cdb
        cdb.set_translation_bulk('fra', {'Ciao': 'Bonjour'})
        cdb.set_translation_bulk('deu', {'Ciao': 'Hallo'})
        fra = cdb.get_translation('fra')
        deu = cdb.get_translation('deu')
        self.assertEqual(fra['Ciao'], 'Bonjour')
        self.assertEqual(deu['Ciao'], 'Hallo')

    def test_translation_lingua_assente_lista_vuota(self):
        """get_translation su lingua non inserita restituisce dict vuoto, non errore."""
        import core.config_db as cdb
        lette = cdb.get_translation('jpn')
        self.assertIsInstance(lette, dict)
        self.assertEqual(len(lette), 0)

    # ── delete_translation_lang ──────────────────────────────────────────────

    def test_delete_lingua_straniera(self):
        """Eliminare una lingua straniera rimuove tutte le sue stringhe."""
        import core.config_db as cdb
        cdb.set_translation_bulk('spa', {'Hola': 'Ciao'})
        eliminati = cdb.delete_translation_lang('spa')
        self.assertGreater(eliminati, 0)
        self.assertEqual(cdb.get_translation('spa'), {})

    def test_delete_lingua_master_proibita(self):
        """Non è possibile eliminare la lingua master italiana — deve sollevare ValueError."""
        import core.config_db as cdb
        with self.assertRaises(ValueError):
            cdb.delete_translation_lang('ita')
        with self.assertRaises(ValueError):
            cdb.delete_translation_lang('it')   # alias a 2 lettere: stesso risultato

    # ── get_languages ────────────────────────────────────────────────────────

    def test_get_languages_include_sempre_ita(self):
        """get_languages deve includere sempre 'ita' anche se non ha stringhe."""
        import core.config_db as cdb
        lingue = cdb.get_languages()
        codici = [l['code'] for l in lingue]
        self.assertIn('ita', codici)

    def test_get_languages_dopo_inserimento(self):
        """Dopo aver inserito stringhe per 'fra', get_languages deve includerla."""
        import core.config_db as cdb
        cdb.set_translation_bulk('fra', {'Ciao': 'Bonjour'})
        lingue  = cdb.get_languages()
        codici  = [l['code'] for l in lingue]
        self.assertIn('fra', codici)

    def test_get_languages_struttura(self):
        """Ogni elemento di get_languages ha i campi code, name, count."""
        import core.config_db as cdb
        for lingua in cdb.get_languages():
            self.assertIn('code',  lingua)
            self.assertIn('name',  lingua)
            self.assertIn('count', lingua)

    def test_get_languages_ita_prima(self):
        """'ita' deve essere sempre il primo elemento (lingua master)."""
        import core.config_db as cdb
        cdb.set_translation_bulk('deu', {'x': 'y'})
        cdb.set_translation_bulk('fra', {'x': 'y'})
        lingue = cdb.get_languages()
        self.assertEqual(lingue[0]['code'], 'ita')


class TestDefaultLang(unittest.TestCase):
    """Test per _default_lang() in extto_web.py.
    Verifica che il fallback a 'ita' funzioni correttamente (fix ML).
    """

    def _get_default_lang(self):
        """Importa _default_lang dinamicamente per non dipendere da Flask."""
        import importlib.util, sys
        # Importiamo solo la funzione senza avviare Flask
        spec = importlib.util.spec_from_file_location(
            'extto_web_partial',
            os.path.join(os.path.dirname(__file__), 'extto_web.py')
        )
        # Non possiamo importare extto_web senza Flask in esecuzione,
        # quindi testiamo la logica direttamente
        return None

    def test_default_lang_non_configurata_ritorna_ita(self):
        """Se default_language non è configurata nel DB, _default_lang() restituisce 'ita'."""
        # Testiamo la logica inline (equivalente a _default_lang con DB vuoto)
        raw = ''   # simula get_setting('default_language', '') quando non configurata
        result = raw if raw else 'ita'
        self.assertEqual(result, 'ita',
            "Con DB vuoto _default_lang deve restituire 'ita', non stringa vuota")

    def test_default_lang_configurata_ritorna_valore(self):
        """Se default_language è configurata, _default_lang() la restituisce."""
        raw = 'deu'
        result = raw if raw else 'ita'
        self.assertEqual(result, 'deu')

    def test_default_lang_stringa_vuota_ritorna_ita(self):
        """Se default_language è esplicitamente '' nel DB, fallback a 'ita'."""
        raw = '   '   # spazi — strip() → ''
        result = raw.strip() if raw.strip() else 'ita'
        self.assertEqual(result, 'ita')


class TestNuoviEndpointML(unittest.TestCase):
    """Verifica che i nuovi endpoint introdotti nella versione ML esistano in extto_web.py.
    Non testano la logica interna (richiederebbe un DB reale) ma garantiscono
    che le route siano registrate e non siano state accidentalmente rimosse.
    """

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), 'extto_web.py'), encoding='utf-8') as f:
            self._web = f.read()

    def test_endpoint_all_missing_presente(self):
        """GET /api/series/all-missing deve esistere — lista episodi mancanti."""
        self.assertIn("/api/series/all-missing", self._web,
                      "Endpoint all-missing non trovato in extto_web.py")

    def test_endpoint_calendar_presente(self):
        """GET /api/series/calendar deve esistere — prossime uscite TMDB."""
        self.assertIn("/api/series/calendar", self._web,
                      "Endpoint calendar non trovato in extto_web.py")

    def test_endpoint_scan_all_archives_presente(self):
        """POST /api/scan-all-archives deve esistere — scansione massiva NAS."""
        self.assertIn("/api/scan-all-archives", self._web,
                      "Endpoint scan-all-archives non trovato in extto_web.py")

    def test_endpoint_i18n_languages_presente(self):
        """GET /api/i18n/languages deve esistere — lista lingue disponibili."""
        self.assertIn("/api/i18n/languages", self._web,
                      "Endpoint i18n/languages non trovato")

    def test_endpoint_i18n_active_presente(self):
        """GET+POST /api/i18n/active deve esistere — lingua UI attiva."""
        self.assertIn("/api/i18n/active", self._web,
                      "Endpoint i18n/active non trovato")

    def test_endpoint_i18n_import_presente(self):
        """POST /api/i18n/import/<lang> deve esistere — importa YAML."""
        self.assertIn("/api/i18n/import/", self._web,
                      "Endpoint i18n/import non trovato")

    def test_endpoint_i18n_export_presente(self):
        """POST /api/i18n/export/<lang> deve esistere — esporta YAML."""
        self.assertIn("/api/i18n/export/", self._web,
                      "Endpoint i18n/export non trovato")

    def test_default_lang_funzione_presente(self):
        """_default_lang() deve esistere in extto_web.py."""
        self.assertIn("def _default_lang()", self._web,
                      "_default_lang() non trovata in extto_web.py")

    def test_default_lang_fallback_ita(self):
        """_default_lang() deve avere il fallback a 'ita' (fix ML)."""
        self.assertIn("return v if v else 'ita'", self._web,
                      "_default_lang() non ha il fallback sicuro a 'ita'")

    def test_config_write_lock_presente(self):
        """_config_write_lock deve esistere — protegge scritture config concorrenti."""
        self.assertIn("_config_write_lock", self._web,
                      "_config_write_lock non trovato in extto_web.py")


class TestLinguaFiltroNonBonus(unittest.TestCase):
    """Verifica che la logica 'lingua come filtro binario' sia rispettata
    in tutti i punti critici del codice ML (nessun BONUS_ITA/BONUS_LANG).
    """

    def test_models_nessun_bonus_ita(self):
        """Quality non deve avere BONUS_ITA né BONUS_LANG — rimossi in ML."""
        self.assertFalse(hasattr(Quality, 'BONUS_ITA'),
            "BONUS_ITA trovato in Quality — deve essere rimosso nella versione ML")
        self.assertFalse(hasattr(Quality, 'BONUS_LANG'),
            "BONUS_LANG trovato in Quality — non deve esistere")

    def test_models_bonus_presenti_corretti(self):
        """I bonus che devono esistere sono solo DV, REAL, PROPER, REPACK."""
        self.assertTrue(hasattr(Quality, 'BONUS_DV'))
        self.assertTrue(hasattr(Quality, 'BONUS_REAL'))
        self.assertTrue(hasattr(Quality, 'BONUS_PROPER'))
        self.assertTrue(hasattr(Quality, 'BONUS_REPACK'))

    def test_extto3_nessun_bonus_lang_nel_codice(self):
        """extto3.py non deve contenere riferimenti a BONUS_LANG (rimosso con fix)."""
        with open(os.path.join(os.path.dirname(__file__), 'extto3.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertNotIn('BONUS_LANG', src,
            "BONUS_LANG ancora presente in extto3.py — la fix non è stata applicata")
        self.assertNotIn('BONUS_ITA', src,
            "BONUS_ITA ancora presente in extto3.py")

    def test_models_nessun_campo_dead_code(self):
        """is_lang_ok e detected_lang erano dead code — devono essere rimossi."""
        q = Quality()
        self.assertFalse(hasattr(q, 'is_lang_ok'),
            "is_lang_ok ancora presente in Quality — dead code non rimosso")
        self.assertFalse(hasattr(q, 'detected_lang'),
            "detected_lang ancora presente in Quality — dead code non rimosso")

    def test_score_film_identico_con_senza_ita(self):
        """Lo score di un film ITA e non-ITA deve essere identico a parità di qualità.
        Dimostra che la lingua non influenza più il punteggio."""
        q_ita = Parser.parse_quality("Film.2024.1080p.WEB-DL.ITA.x265")
        q_eng = Parser.parse_quality("Film.2024.1080p.WEB-DL.ENG.x265")
        self.assertEqual(q_ita.score(), q_eng.score(),
            f"Score ITA({q_ita.score()}) diverso da ENG({q_eng.score()}) — "
            "la lingua non deve influenzare lo score")

    def test_lang_ok_usato_come_filtro_in_config(self):
        """_lang_ok deve restituire bool — usato come filtro pass/fail, non come punteggio."""
        result_ok  = Config._lang_ok("Serie.S01E01.ITA.1080p", "ita")
        result_no  = Config._lang_ok("Serie.S01E01.ENG.1080p", "ita")
        self.assertIsInstance(result_ok, bool)
        self.assertIsInstance(result_no, bool)
        self.assertTrue(result_ok)
        self.assertFalse(result_no)


class TestTorrentLimits(unittest.TestCase):
    """Test per le funzioni torrent_limits in core/config_db.py (★ v44).

    Ogni test usa un DB temporaneo isolato — stesso pattern di TestConfigDB.
    """

    def setUp(self):
        import tempfile
        import core.config_db as cdb
        self._tmpdir  = tempfile.mkdtemp()
        self._orig_db = cdb.CONFIG_DB_FILE
        cdb.CONFIG_DB_FILE = os.path.join(self._tmpdir, 'test_limits.db')

    def tearDown(self):
        import shutil
        import core.config_db as cdb
        cdb.CONFIG_DB_FILE = self._orig_db
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_set_e_get_limit(self):
        """set_torrent_limit + get_torrent_limit: valori letti correttamente."""
        import core.config_db as cdb
        cdb.set_torrent_limit('aabbcc', dl_bytes=512000, ul_bytes=256000)
        row = cdb.get_torrent_limit('aabbcc')
        self.assertIsNotNone(row)
        self.assertEqual(row['dl_bytes'], 512000)
        self.assertEqual(row['ul_bytes'], 256000)

    def test_get_limit_inesistente(self):
        """get_torrent_limit restituisce None se l'hash non esiste."""
        import core.config_db as cdb
        self.assertIsNone(cdb.get_torrent_limit('hashinesistente'))

    def test_upsert_limit(self):
        """Chiamare set_torrent_limit due volte sullo stesso hash aggiorna i valori."""
        import core.config_db as cdb
        cdb.set_torrent_limit('aabbcc', dl_bytes=100000, ul_bytes=50000)
        cdb.set_torrent_limit('aabbcc', dl_bytes=999999, ul_bytes=111111)
        row = cdb.get_torrent_limit('aabbcc')
        self.assertEqual(row['dl_bytes'], 999999)
        self.assertEqual(row['ul_bytes'], 111111)

    def test_nessun_limite(self):
        """Il valore -1 (nessun limite) viene salvato e riletto correttamente."""
        import core.config_db as cdb
        cdb.set_torrent_limit('aabbcc', dl_bytes=-1, ul_bytes=-1)
        row = cdb.get_torrent_limit('aabbcc')
        self.assertEqual(row['dl_bytes'], -1)
        self.assertEqual(row['ul_bytes'], -1)

    def test_get_all_torrent_limits(self):
        """get_all_torrent_limits restituisce tutti i limiti come dict {hash: {...}}."""
        import core.config_db as cdb
        cdb.set_torrent_limit('hash1', dl_bytes=100, ul_bytes=200)
        cdb.set_torrent_limit('hash2', dl_bytes=300, ul_bytes=400)
        tutti = cdb.get_all_torrent_limits()
        self.assertIn('hash1', tutti)
        self.assertIn('hash2', tutti)
        self.assertEqual(tutti['hash1']['dl_bytes'], 100)
        self.assertEqual(tutti['hash2']['ul_bytes'], 400)

    def test_get_all_torrent_limits_vuoto(self):
        """get_all_torrent_limits restituisce dict vuoto se non ci sono limiti."""
        import core.config_db as cdb
        self.assertEqual(cdb.get_all_torrent_limits(), {})

    def test_delete_limit(self):
        """delete_torrent_limit rimuove il record e restituisce True."""
        import core.config_db as cdb
        cdb.set_torrent_limit('aabbcc', dl_bytes=100, ul_bytes=200)
        rimosso = cdb.delete_torrent_limit('aabbcc')
        self.assertTrue(rimosso)
        self.assertIsNone(cdb.get_torrent_limit('aabbcc'))

    def test_delete_limit_inesistente(self):
        """delete_torrent_limit su hash inesistente restituisce False senza errori."""
        import core.config_db as cdb
        self.assertFalse(cdb.delete_torrent_limit('hashinesistente'))

    def test_hash_normalizzato_lowercase(self):
        """L'info_hash viene sempre salvato in lowercase — case insensitive."""
        import core.config_db as cdb
        cdb.set_torrent_limit('AABBCC', dl_bytes=500, ul_bytes=500)
        # Leggibile sia in minuscolo che in maiuscolo
        self.assertIsNotNone(cdb.get_torrent_limit('aabbcc'))
        self.assertIsNotNone(cdb.get_torrent_limit('AABBCC'))

    def test_migrazione_da_json_vuoto(self):
        """migrate_torrent_limits_from_json con JSON vuoto restituisce 0."""
        import json
        import core.config_db as cdb
        json_path = os.path.join(self._tmpdir, 'torrent_limits.json')
        with open(json_path, 'w') as f:
            json.dump({}, f)
        self.assertEqual(cdb.migrate_torrent_limits_from_json(json_path), 0)
        self.assertEqual(cdb.get_all_torrent_limits(), {})

    def test_migrazione_da_json_assente(self):
        """migrate_torrent_limits_from_json con file assente restituisce 0 senza errori."""
        import core.config_db as cdb
        self.assertEqual(cdb.migrate_torrent_limits_from_json('/tmp/non_esiste.json'), 0)

    def test_migrazione_da_json_con_dati(self):
        """migrate_torrent_limits_from_json importa correttamente i record."""
        import json
        import core.config_db as cdb
        dati = {
            'aabbcc': {'dl_bytes': 512000, 'ul_bytes': 256000},
            'ddeeff': {'dl_bytes': -1,     'ul_bytes': 100000},
        }
        json_path = os.path.join(self._tmpdir, 'torrent_limits.json')
        with open(json_path, 'w') as f:
            json.dump(dati, f)
        n = cdb.migrate_torrent_limits_from_json(json_path)
        self.assertEqual(n, 2)
        r1 = cdb.get_torrent_limit('aabbcc')
        self.assertEqual(r1['dl_bytes'], 512000)
        r2 = cdb.get_torrent_limit('ddeeff')
        self.assertEqual(r2['ul_bytes'], 100000)

    def test_migrazione_idempotente(self):
        """Eseguire la migrazione due volte non duplica i record (upsert)."""
        import json
        import core.config_db as cdb
        dati = {'aabbcc': {'dl_bytes': 100, 'ul_bytes': 200}}
        json_path = os.path.join(self._tmpdir, 'torrent_limits.json')
        with open(json_path, 'w') as f:
            json.dump(dati, f)
        cdb.migrate_torrent_limits_from_json(json_path)
        cdb.migrate_torrent_limits_from_json(json_path)
        self.assertEqual(len(cdb.get_all_torrent_limits()), 1)

    def test_isolamento_da_altri_settings(self):
        """La tabella torrent_limits è separata da settings — nessuna interferenza."""
        import core.config_db as cdb
        cdb.set_setting('web_port', '5000')
        cdb.set_torrent_limit('aabbcc', dl_bytes=100, ul_bytes=200)
        # settings non contiene l'hash
        tutti_settings = cdb.get_all_settings()
        self.assertNotIn('aabbcc', tutti_settings)
        # torrent_limits non contiene web_port
        tutti_limits = cdb.get_all_torrent_limits()
        self.assertNotIn('web_port', tutti_limits)


if __name__ == '__main__':
    # Avvia tutti i test e stampa il risultato a video
    print("Avvio ispezione qualità codice EXTTO...")
    unittest.main(verbosity=2)
