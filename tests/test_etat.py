"""Tests de la logique de la lampe (sans Bluetooth).

Lancement : .venv/bin/python -m unittest discover -s tests -v
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from lampe import gatt
from lampe.etat import Lampe, Stockage


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.fichier = Path(self.dossier.name) / "etat.json"
        self.notifs: list[tuple[str, bytes]] = []
        self.renommages: list[str] = []

    def tearDown(self):
        self.dossier.cleanup()

    def lampe(self, **kw) -> Lampe:
        l = Lampe(Stockage(self.fichier), **kw)
        l.notifier = lambda uuid, octets: self.notifs.append((uuid, octets))
        l.sur_renommage = self.renommages.append
        return l


class TestToggle(Base):
    async def test_ecrire_1_bascule_et_notifie(self):
        l = self.lampe()
        l.ecrire_toggle(b"1")
        self.assertTrue(l.led)
        self.assertEqual(l.compteur, 1)
        self.assertEqual(self.notifs, [(gatt.LED_STATE, b"1"), (gatt.TOGGLE_COUNT, b"1")])
        l.ecrire_toggle(b"1")
        self.assertFalse(l.led)
        self.assertEqual(self.notifs[-2:], [(gatt.LED_STATE, b"0"), (gatt.TOGGLE_COUNT, b"2")])

    async def test_bouton_et_http(self):
        l = self.lampe()
        l.appuyer_bouton()
        self.assertEqual(l.statut(), {"state": "ON", "count": 1})
        l.basculer("HTTP")
        self.assertEqual(l.statut(), {"state": "OFF", "count": 2})

    async def test_toute_ecriture_bascule(self):
        # Comme le firmware : "0", une chaîne vide ou un texte quelconque basculent aussi la LED.
        l = self.lampe()
        for i, v in enumerate((b"0", b"", b"abc", b"\xff"), start=1):
            l.ecrire_toggle(v)
            self.assertEqual(l.compteur, i)
        self.assertFalse(l.led)  # 4 bascules : retour à l'état initial

    async def test_motif_restaure_et_ne_compte_pas(self):
        l = self.lampe(duree_pas_motif=0.001)
        l.ecrire_toggle(b"101")
        await asyncio.sleep(0.05)
        etats = [o for u, o in self.notifs if u == gatt.LED_STATE]
        self.assertEqual(etats, [b"1", b"0", b"1", b"0"])
        self.assertEqual((l.led, l.compteur), (False, 0))


class TestNom(Base):
    async def test_renommage_persistant(self):
        l = self.lampe(nom_defaut="PMW-1A2B")
        self.assertEqual(l.nom, "PMW-1A2B")
        l.renommer("PMW-Valentin\x00".encode())
        self.assertEqual(l.nom, "PMW-Valentin")
        self.assertEqual(self.renommages, ["PMW-Valentin"])
        self.assertEqual(json.loads(self.fichier.read_text())["nom"], "PMW-Valentin")
        # « Redémarrage » : nouvelle instance sur le même fichier.
        self.assertEqual(self.lampe(nom_defaut="PMW-1A2B").nom, "PMW-Valentin")

    async def test_nom_force_prioritaire(self):
        self.lampe().renommer(b"PMW-Stocke")
        self.assertEqual(self.lampe(nom_force="PMW-Force").nom, "PMW-Force")

    async def test_nom_invalide(self):
        l = self.lampe()
        for v in (b"", b"  ", b"\xff\xfe"):
            with self.assertRaises(ValueError):
                l.renommer(v)
        self.assertFalse(self.fichier.exists())

    async def test_utf8(self):
        self.assertEqual(self.lampe().renommer("PMW-Éloïse".encode()), "PMW-Éloïse")


class TestAbonnement(Base):
    async def test_valeurs_initiales_par_defaut(self):
        l = self.lampe()
        l.basculer("test")
        self.assertEqual(l.valeurs_abonnement(gatt.LED_STATE), [b"1"])
        self.assertEqual(l.valeurs_abonnement(gatt.TOGGLE_COUNT), [b"1"])

    async def test_option_desactivee(self):
        l = self.lampe(notifier_a_l_abonnement=False)
        self.assertEqual(l.valeurs_abonnement(gatt.LED_STATE), [])
        self.assertEqual(l.valeurs_abonnement(gatt.TOGGLE_COUNT), [])

    async def test_wifi_liste_a_l_abonnement(self):
        l = self.lampe(notifier_a_l_abonnement=False, reseaux=["A", "B"])
        self.assertEqual(l.valeurs_abonnement(gatt.WIFI), [b"A", b"B"])


class TestWifi(Base):
    async def test_identifiants_json_et_texte(self):
        l = self.lampe()
        l.ecrire_wifi(b'{"ssid": "Maison", "password": "secret"}')
        self.assertEqual(json.loads(self.fichier.read_text())["wifi"], {"ssid": "Maison", "motdepasse": "secret"})
        l.ecrire_wifi(b"Bureau;a;b")
        self.assertEqual(self.lampe().wifi, {"ssid": "Bureau", "motdepasse": "a;b"})

    async def test_autre_ecriture_renvoie_la_liste(self):
        l = self.lampe(reseaux=["X", "Y"])
        l.ecrire_wifi(b"scan")
        self.assertEqual(self.notifs, [(gatt.WIFI, b"X"), (gatt.WIFI, b"Y")])


if __name__ == "__main__":
    unittest.main()
