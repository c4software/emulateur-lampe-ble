"""Logique de la lampe, indépendante du Bluetooth (testable sans adaptateur).

La couche BLE (ble.py) et le serveur HTTP (http.py) ne font qu'appeler cette
classe. Les notifications sortent par le callback ``notifier(uuid, octets)``.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from . import gatt

# Réseaux Wi-Fi fictifs renvoyés par la caractéristique WIFI (simulation).
RESEAUX_SIMULES = [
    "Livebox-4F2A",
    "Freebox-8C1D",
    "Salle-TP-BLE",
    "eduroam",
    "SFR_WiFi_FON",
]

TAILLE_MAX_NOM = 29  # octets disponibles pour le nom dans la réponse de scan


class Stockage:
    """Petit fichier JSON qui joue le rôle de la mémoire flash de l'ESP32."""

    def __init__(self, chemin: Path):
        self.chemin = Path(chemin)

    def lire(self) -> dict:
        try:
            with open(self.chemin, encoding="utf-8") as f:
                donnees = json.load(f)
            return donnees if isinstance(donnees, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def ecrire(self, **valeurs) -> None:
        donnees = self.lire()
        donnees.update(valeurs)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.chemin.parent, prefix=".etat-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.chemin)


class Lampe:
    def __init__(
        self,
        stockage: Stockage,
        nom_defaut: str = "PMW-1A2B",
        nom_force: str | None = None,
        notifier_a_l_abonnement: bool = True,
        duree_pas_motif: float = 0.25,
        reseaux: list[str] | None = None,
    ):
        self.stockage = stockage
        self.led = False
        self.compteur = 0
        self.notifier_a_l_abonnement = notifier_a_l_abonnement
        self.duree_pas_motif = duree_pas_motif
        self.reseaux = list(RESEAUX_SIMULES if reseaux is None else reseaux)
        memoire = stockage.lire()
        self.nom = nom_force or memoire.get("nom") or nom_defaut
        self.wifi = memoire.get("wifi")
        self._motif: asyncio.Task | None = None

        # Branchements fournis par les couches BLE / console.
        self.notifier: Callable[[str, bytes], None] = lambda uuid, octets: None
        self.journal: Callable[[str], None] = lambda message: None
        self.sur_renommage: Callable[[str], None] = lambda nom: None

    # ---------------------------------------------------------------- LED
    def _octets_led(self) -> bytes:
        return b"1" if self.led else b"0"

    def _octets_compteur(self) -> bytes:
        return str(self.compteur).encode("utf-8")

    def _notifier_etat(self) -> None:
        self.notifier(gatt.LED_STATE, self._octets_led())
        self.notifier(gatt.TOGGLE_COUNT, self._octets_compteur())

    def basculer(self, source: str) -> None:
        """Bascule la LED, incrémente le compteur et notifie (comme le firmware)."""
        self._annuler_motif()
        self.led = not self.led
        self.compteur += 1
        self.journal(f"LED basculée ({source}) : {'ALLUMÉE' if self.led else 'éteinte'}, compteur = {self.compteur}")
        self._notifier_etat()

    def appuyer_bouton(self) -> None:
        self.basculer("bouton physique")

    def ecrire_toggle(self, octets: bytes, client: str = "BLE") -> None:
        # Comme le firmware : toute écriture bascule la LED, quelle que soit la valeur.
        # Seule exception (hypothèse) : une suite de 0 et de 1 de plusieurs caractères joue un motif.
        texte = octets.decode("utf-8", errors="replace").strip("\x00 \r\n")
        if len(texte) > 1 and set(texte) <= {"0", "1"}:
            self.journal(f"Motif reçu ({client}) : {texte}")
            self._annuler_motif()
            self._motif = asyncio.get_running_loop().create_task(self.jouer_motif(texte))
        else:
            self.basculer(client)

    async def jouer_motif(self, motif: str) -> None:
        """Animation de la LED (ex. SOS). Comportement supposé : le compteur ne bouge pas
        et la LED revient à son état initial à la fin."""
        initial = self.led
        try:
            for caractere in motif:
                self.led = caractere == "1"
                self.notifier(gatt.LED_STATE, self._octets_led())
                await asyncio.sleep(self.duree_pas_motif)
        finally:
            self.led = initial
        self.notifier(gatt.LED_STATE, self._octets_led())
        self.journal("Motif terminé")

    def _annuler_motif(self) -> None:
        if self._motif and not self._motif.done():
            self._motif.cancel()
        self._motif = None

    # ---------------------------------------------------------------- Nom
    def renommer(self, octets: bytes) -> str:
        """Change le nom annoncé et le sauvegarde. Lève ValueError si invalide."""
        try:
            nom = octets.decode("utf-8").strip("\x00 \r\n\t")
        except UnicodeDecodeError as e:
            raise ValueError("nom non UTF-8") from e
        if not nom:
            raise ValueError("nom vide")
        if len(nom.encode("utf-8")) > TAILLE_MAX_NOM:
            self.journal(f"Attention : nom de plus de {TAILLE_MAX_NOM} octets, il sera tronqué dans l'annonce")
        self.nom = nom
        self.stockage.ecrire(nom=nom)
        self.journal(f"Nouveau nom enregistré : {nom}")
        self.sur_renommage(nom)
        return nom

    # ---------------------------------------------------------------- Wi-Fi (simulé)
    def ecrire_wifi(self, octets: bytes) -> None:
        """Stub : accepte JSON {"ssid", "password"} ou "ssid;motdepasse".
        Toute autre écriture (ex. "scan") renvoie la liste des réseaux."""
        texte = octets.decode("utf-8", errors="replace").strip("\x00 \r\n")
        ssid = mdp = None
        try:
            donnees = json.loads(texte)
            if isinstance(donnees, dict):
                ssid = donnees.get("ssid")
                mdp = donnees.get("password", donnees.get("passwd", donnees.get("pass", "")))
        except json.JSONDecodeError:
            if ";" in texte:
                ssid, mdp = texte.split(";", 1)
        if ssid:
            self.wifi = {"ssid": ssid, "motdepasse": mdp or ""}
            self.stockage.ecrire(wifi=self.wifi)
            self.journal(f"Identifiants Wi-Fi enregistrés (simulé) : ssid={ssid!r}")
        else:
            self.journal(f"Écriture Wi-Fi {texte!r} : envoi de la liste des réseaux simulés")
            self.envoyer_reseaux()

    def envoyer_reseaux(self) -> None:
        for reseau in self.liste_reseaux():
            self.notifier(gatt.WIFI, reseau)

    def liste_reseaux(self) -> list[bytes]:
        return [r.encode("utf-8") for r in self.reseaux]

    # ---------------------------------------------------------------- Abonnements
    def valeurs_abonnement(self, uuid: str) -> list[bytes]:
        """Valeurs à notifier juste après l'abonnement d'un client (CCCD)."""
        if uuid == gatt.WIFI:
            return self.liste_reseaux()
        if not self.notifier_a_l_abonnement:
            return []
        if uuid == gatt.LED_STATE:
            return [self._octets_led()]
        if uuid == gatt.TOGGLE_COUNT:
            return [self._octets_compteur()]
        return []

    # ---------------------------------------------------------------- HTTP
    def statut(self) -> dict:
        return {"state": "ON" if self.led else "OFF", "count": self.compteur}
