"""Serveur HTTP : le tableau de bord web, plus les routes /status et /toggle de la carte.

- ``/``         : tableau de bord (LED, compteur, clients, journal), mis à jour en direct ;
- ``/evenements``: flux Server-Sent Events utilisé par le tableau de bord ;
- ``/bouton``   : simule un appui sur le bouton physique (POST) ;
- ``/status`` et ``/toggle`` : routes et format repris du sujet « Télécommande Bluetooth & Réseau »
  (tp/android/view/app-ble-network.md) : {"state": "ON"|"OFF", "count": n}.
  Le code source du firmware n'étant pas disponible, la méthode HTTP attendue
  n'est pas connue : GET et POST sont acceptés sur ces deux routes.
"""

import asyncio
import json
from pathlib import Path

from .diffusion import Diffusion
from .etat import Lampe

PAGE = Path(__file__).resolve().parent / "web" / "index.html"


def _reponse(code: str, corps: bytes, type_contenu: str) -> bytes:
    return (
        f"HTTP/1.1 {code}\r\nContent-Type: {type_contenu}\r\n"
        f"Content-Length: {len(corps)}\r\nAccess-Control-Allow-Origin: *\r\n"
        "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
    ).encode() + corps


async def demarrer_http(lampe: Lampe, port: int, log, diffusion: Diffusion | None = None,
                        hote: str = "0.0.0.0") -> asyncio.base_events.Server:
    async def flux_evenements(ecrivain: asyncio.StreamWriter) -> None:
        ecrivain.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n"
            b"Cache-Control: no-store\r\nConnection: keep-alive\r\n\r\n"
        )
        file = diffusion.abonner()
        try:
            while True:
                try:
                    evenement = await asyncio.wait_for(file.get(), 15)
                    ecrivain.write(Diffusion.format_sse(evenement))
                except asyncio.TimeoutError:
                    ecrivain.write(b": ping\n\n")  # garde la connexion ouverte
                await ecrivain.drain()
        finally:
            diffusion.desabonner(file)

    async def gerer(lecteur: asyncio.StreamReader, ecrivain: asyncio.StreamWriter):
        try:
            ligne = (await asyncio.wait_for(lecteur.readline(), 5)).decode("latin-1").strip()
            while (await asyncio.wait_for(lecteur.readline(), 5)) not in (b"\r\n", b"\n", b""):
                pass
            parties = ligne.split()
            if not parties:
                return  # connexion ouverte à l'avance par le navigateur, sans requête
            methode, chemin = (parties[0], parties[1]) if len(parties) >= 2 else ("", "")
            route = chemin.split("?", 1)[0].rstrip("/") or "/"
            pair = ecrivain.get_extra_info("peername")
            client = pair[0] if pair else "?"

            # Tableau de bord (silencieux dans le journal)
            if route == "/favicon.ico":
                ecrivain.write(_reponse("204 No Content", b"", "image/x-icon"))
                await ecrivain.drain()
                return
            if diffusion and methode == "GET" and route == "/":
                ecrivain.write(_reponse("200 OK", PAGE.read_bytes(), "text/html; charset=utf-8"))
                await ecrivain.drain()
                return
            if diffusion and methode == "GET" and route == "/evenements":
                await flux_evenements(ecrivain)
                return
            if diffusion and methode == "POST" and route == "/bouton":
                lampe.appuyer_bouton()
                ecrivain.write(_reponse("204 No Content", b"", "text/plain"))
                await ecrivain.drain()
                return

            # Routes de la carte
            if methode not in ("GET", "POST"):
                code, corps = "405 Method Not Allowed", {"error": "method not allowed"}
            elif route == "/toggle":
                lampe.basculer(f"HTTP {client}")
                code, corps = "200 OK", lampe.statut()
            elif route == "/status":
                code, corps = "200 OK", lampe.statut()
            else:
                code, corps = "404 Not Found", {"error": "not found"}
            log(f"HTTP {methode} {chemin} depuis {client} -> {code.split()[0]}")
            ecrivain.write(_reponse(code, json.dumps(corps).encode(), "application/json"))
            await ecrivain.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            ecrivain.close()

    serveur = await asyncio.start_server(gerer, hote, port)
    if diffusion:
        log(f"Tableau de bord : http://localhost:{port}/")
    log(f"Routes de la carte : http://<ip du PC>:{port}/status et /toggle")
    return serveur
