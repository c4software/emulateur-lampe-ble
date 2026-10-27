#!/usr/bin/env python3
"""Émulateur de la lampe connectée ESP32 : périphérique BLE réel via BlueZ."""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from lampe.console import AIDE, Clavier, Journal
from lampe.etat import Lampe, Stockage

DOSSIER = Path(__file__).resolve().parent


def analyser_arguments(argv=None):
    p = argparse.ArgumentParser(description="Émulateur BLE de la lampe connectée ESP32 (PMW-XXXX).")
    p.add_argument("--name", help="nom annoncé au démarrage (prioritaire sur le nom enregistré)")
    p.add_argument("--adapter", default="hci0", help="adaptateur Bluetooth (défaut : hci0)")
    p.add_argument("--state-file", type=Path, default=DOSSIER / "etat-lampe.json",
                   help="fichier JSON de persistance (défaut : etat-lampe.json)")
    p.add_argument("--http", type=int, default=8080, metavar="PORT",
                   help="port du tableau de bord web et des routes /status et /toggle (défaut : 8080)")
    p.add_argument("--no-http", action="store_true",
                   help="désactive le tableau de bord web et les routes HTTP")
    p.add_argument("--no-notify-on-subscribe", action="store_true",
                   help="ne pas envoyer l'état et le compteur juste après l'abonnement d'un client")
    p.add_argument("--alias-adapter", action="store_true",
                   help="renomme aussi l'adaptateur (nom GAP lu après connexion), restauré à l'arrêt")
    p.add_argument("--pattern-step", type=int, default=250, metavar="MS",
                   help="durée d'un pas de motif LED, en ms (défaut : 250)")
    p.add_argument("-v", "--verbose", action="store_true", help="journal détaillé")
    return p.parse_args(argv)


async def principal(args) -> int:
    from lampe.ble import ErreurBluez, PeripheriqueBLE
    from lampe.diffusion import Diffusion, JournalDiffuse
    from lampe.http import demarrer_http

    # L'état complet affiché par le tableau de bord web
    def instantane() -> dict:
        return {
            "nom": lampe.nom,
            "led": lampe.led,
            "compteur": lampe.compteur,
            "clients": sorted(ble.connectes.values()),
            "abonnements": [c.nom for c in ble.caracteristiques if c.abonne],
            "notifAbonnement": lampe.notifier_a_l_abonnement,
        }

    diffusion = Diffusion(instantane)
    journal = JournalDiffuse(Journal(args.verbose), diffusion)
    stockage = Stockage(args.state_file)
    lampe = Lampe(stockage, nom_force=args.name,
                  notifier_a_l_abonnement=not args.no_notify_on_subscribe,
                  duree_pas_motif=args.pattern_step / 1000)
    lampe.journal = journal
    ble = PeripheriqueBLE(lampe, args.adapter, log=journal, alias_adaptateur=args.alias_adapter)

    try:
        props = await ble.lire_adaptateur()
    except (ErreurBluez, OSError) as e:
        journal(f"ERREUR : {e}")
        return 1
    if not args.name and not stockage.lire().get("nom"):
        # Nom par défaut façon ESP32 : PMW- suivi des 4 derniers chiffres de l'adresse.
        lampe.nom = "PMW-" + props.get("Address", "00:00").replace(":", "")[-4:].upper()
    # Adresse matérielle du PC : dans le terminal seulement, pas sur le tableau de bord (souvent projeté)
    journal(f"Adaptateur {args.adapter} ({props.get('Address')}) allumé", niveau="terminal")
    journal(f"Adaptateur {args.adapter} allumé", niveau="tableau")

    arret = asyncio.Event()
    serveur_http = None
    clavier = None
    try:
        await ble.demarrer()
        if not args.no_http:
            serveur_http = await demarrer_http(lampe, args.http, journal, diffusion)

        def touche(t: str):
            if t == "b":
                lampe.appuyer_bouton()
                journal.etat(lampe)
            elif t == "e":
                journal.etat(lampe)
                abonnes = [c.nom for c in ble.caracteristiques if c.abonne]
                journal(f"Abonnements actifs : {', '.join(abonnes) or 'aucun'}")
            elif t == "w":
                lampe.envoyer_reseaux()
            elif t == "h":
                journal(AIDE)
            elif t == "q":
                arret.set()

        clavier = Clavier(touche)
        if clavier.demarrer():
            journal(AIDE)
        else:
            journal("Entrée standard non interactive : clavier désactivé (Ctrl+C pour quitter)")
        journal.etat(lampe)

        boucle = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            boucle.add_signal_handler(sig, arret.set)
        await arret.wait()
    except ErreurBluez as e:
        journal(f"ERREUR BlueZ : {e}")
        return 1
    finally:
        if clavier:
            clavier.arreter()
        if serveur_http:
            serveur_http.close()
        await ble.arreter()
        journal("Arrêt de l'émulateur")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal(analyser_arguments())))
