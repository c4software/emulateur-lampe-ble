"""Diffusion en direct de l'état et du journal vers le tableau de bord web (Server-Sent Events)."""

import asyncio
import json
import time
from typing import Callable


class Diffusion:
    def __init__(self, instantane: Callable[[], dict]):
        # instantane() renvoie l'état complet à afficher (LED, compteur, nom, clients…)
        self.instantane = instantane
        self.abonnes: set[asyncio.Queue] = set()
        self.historique: list[dict] = []  # derniers messages du journal, pour un nouvel onglet

    def abonner(self) -> asyncio.Queue:
        file: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.abonnes.add(file)
        file.put_nowait(self._etat())
        for ligne in self.historique:
            file.put_nowait(ligne)
        return file

    def desabonner(self, file: asyncio.Queue) -> None:
        self.abonnes.discard(file)

    def _etat(self) -> dict:
        return {"type": "etat", **self.instantane()}

    def _envoyer(self, evenement: dict) -> None:
        for file in list(self.abonnes):
            try:
                file.put_nowait(evenement)
            except asyncio.QueueFull:
                self.abonnes.discard(file)  # onglet trop lent : il se reconnectera

    def publier_etat(self) -> None:
        if self.abonnes:
            self._envoyer(self._etat())

    def publier_journal(self, message: str) -> None:
        ligne = {"type": "journal", "heure": time.strftime("%H:%M:%S"), "message": message}
        self.historique = (self.historique + [ligne])[-100:]
        self._envoyer(ligne)

    @staticmethod
    def format_sse(evenement: dict) -> bytes:
        return f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n".encode("utf-8")


class JournalDiffuse:
    """Journal du terminal, recopié dans le tableau de bord (l'état est republié à chaque message)."""

    def __init__(self, journal, diffusion: Diffusion):
        self.journal = journal
        self.diffusion = diffusion

    def __call__(self, message: str, niveau: str = "info") -> None:
        self.journal(message, niveau)
        if niveau not in ("debug", "terminal"):
            self.diffusion.publier_journal(message)
        self.diffusion.publier_etat()

    def etat(self, lampe) -> None:
        self.journal.etat(lampe)
