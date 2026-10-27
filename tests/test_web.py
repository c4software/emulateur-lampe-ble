"""Tests du tableau de bord web (sans Bluetooth) : page, bouton et flux en direct."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from lampe.diffusion import Diffusion, JournalDiffuse
from lampe.etat import Lampe, Stockage
from lampe.http import demarrer_http


async def requete(port: int, methode: str, chemin: str) -> bytes:
    lecteur, ecrivain = await asyncio.open_connection("127.0.0.1", port)
    ecrivain.write(f"{methode} {chemin} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await ecrivain.drain()
    donnees = await lecteur.read()
    ecrivain.close()
    return donnees


class TestTableauDeBord(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.lampe = Lampe(Stockage(Path(self.dossier.name) / "etat.json"))
        self.diffusion = Diffusion(lambda: {"nom": self.lampe.nom, "led": self.lampe.led,
                                            "compteur": self.lampe.compteur, "clients": [],
                                            "abonnements": [], "notifAbonnement": True})
        self.lampe.journal = JournalDiffuse(lambda m, niveau="info": None, self.diffusion)
        self.serveur = await demarrer_http(self.lampe, 0, lambda *a, **k: None, self.diffusion, hote="127.0.0.1")
        self.port = self.serveur.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.serveur.close()  # sans wait_closed : il attendrait la fin du flux en direct
        self.dossier.cleanup()

    async def test_page_servie(self):
        reponse = await requete(self.port, "GET", "/")
        self.assertIn(b"200 OK", reponse)
        self.assertIn(b"EventSource('/evenements')", reponse)

    async def test_bouton_bascule_la_led(self):
        reponse = await requete(self.port, "POST", "/bouton")
        self.assertIn(b"204", reponse)
        self.assertTrue(self.lampe.led)
        self.assertEqual(self.lampe.compteur, 1)

    async def test_flux_envoie_l_etat_puis_les_changements(self):
        lecteur, ecrivain = await asyncio.open_connection("127.0.0.1", self.port)
        ecrivain.write(b"GET /evenements HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await ecrivain.drain()

        async def evenement() -> dict:
            while True:
                ligne = (await asyncio.wait_for(lecteur.readline(), 2)).decode()
                if ligne.startswith("data: "):
                    return json.loads(ligne[6:])

        premier = await evenement()
        self.assertEqual((premier["type"], premier["led"]), ("etat", False))
        self.lampe.appuyer_bouton()
        recus = [await evenement(), await evenement()]
        self.assertIn("journal", [e["type"] for e in recus])
        self.assertIn(True, [e.get("led") for e in recus if e["type"] == "etat"])
        ecrivain.close()

    async def test_routes_de_la_carte_inchangees(self):
        reponse = await requete(self.port, "GET", "/status")
        self.assertIn(b'{"state": "OFF", "count": 0}', reponse)


if __name__ == "__main__":
    unittest.main()
