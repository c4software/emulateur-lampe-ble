"""Journal horodaté et lecture des touches clavier (sans dépendance)."""

import asyncio
import os
import sys
import termios
import time
import tty

COULEURS = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, texte: str) -> str:
    return f"\033[{code}m{texte}\033[0m" if COULEURS else texte


class Journal:
    def __init__(self, verbeux: bool = False):
        self.verbeux = verbeux

    def __call__(self, message: str, niveau: str = "info") -> None:
        if niveau == "debug" and not self.verbeux:
            return
        heure = time.strftime("%H:%M:%S")
        if message.startswith("Notification"):
            message = _c("36", message)
        elif message.startswith(("Écriture", "HTTP")):
            message = _c("35", message)
        elif message.startswith(("Client", "Abonnement", "Désabonnement")):
            message = _c("34", message)
        elif message.startswith(("ERREUR", "Attention")):
            message = _c("31", message)
        elif niveau == "debug":
            message = _c("2", message)
        print(f"{_c('2', heure)} {message}\r", flush=True)

    def etat(self, lampe) -> None:
        led = _c("1;33", "● ALLUMÉE") if lampe.led else _c("2", "○ éteinte")
        self(f"[{lampe.nom}] LED {led} | compteur {lampe.compteur}")


AIDE = "Touches : [b] bouton physique  [e] état  [w] renvoyer la liste Wi-Fi  [h] aide  [q] quitter"


class Clavier:
    """Passe le terminal en mode cbreak et appelle ``action(touche)`` à chaque frappe."""

    def __init__(self, action):
        self.action = action
        self.ancien = None

    def demarrer(self) -> bool:
        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        self.ancien = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        asyncio.get_running_loop().add_reader(fd, self._lire)
        return True

    def _lire(self) -> None:
        touche = os.read(sys.stdin.fileno(), 1).decode("utf-8", "ignore").lower()
        if touche:
            self.action(touche)

    def arreter(self) -> None:
        if self.ancien is not None:
            fd = sys.stdin.fileno()
            asyncio.get_running_loop().remove_reader(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, self.ancien)
            self.ancien = None
