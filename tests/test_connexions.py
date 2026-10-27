"""Détection des téléphones connectés à partir des signaux BlueZ (sans Bluetooth)."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from dbus_fast import Message, MessageType, Variant

from lampe.ble import PeripheriqueBLE
from lampe.etat import Lampe, Stockage

APPAREIL = "/org/bluez/hci0/dev_6C_1E_E6_6D_D5_95"


def signal_nouvel_appareil(connecte: bool) -> Message:
    return Message(message_type=MessageType.SIGNAL, path="/", interface="org.freedesktop.DBus.ObjectManager",
                   member="InterfacesAdded", signature="oa{sa{sv}}",
                   body=[APPAREIL, {"org.bluez.Device1": {"Connected": Variant("b", connecte)}}])


def signal_changement(connecte: bool) -> Message:
    return Message(message_type=MessageType.SIGNAL, path=APPAREIL, interface="org.freedesktop.DBus.Properties",
                   member="PropertiesChanged", signature="sa{sv}as",
                   body=["org.bluez.Device1", {"Connected": Variant("b", connecte)}, []])


class TestConnexions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.journal: list[str] = []
        self.ble = PeripheriqueBLE(Lampe(Stockage(Path(self.dossier.name) / "e.json")),
                                   log=lambda m, niveau="info": self.journal.append(m))

        async def alias(*_):
            return "Pixel 11 Pro"
        self.ble.propriete = alias

    async def asyncTearDown(self):
        self.dossier.cleanup()

    async def traiter(self, message: Message):
        self.ble._sur_signal(message)
        await asyncio.sleep(0)  # laisse tourner la tâche créée par le signal
        await asyncio.sleep(0)

    async def test_premiere_connexion_via_interfaces_added(self):
        await self.traiter(signal_nouvel_appareil(True))
        self.assertEqual(list(self.ble.connectes.values()), ["Pixel 11 Pro (6C:1E:E6:6D:D5:95)"])
        self.assertIn("Client connecté : Pixel 11 Pro (6C:1E:E6:6D:D5:95)", self.journal)

    async def test_appareil_connu_via_properties_changed(self):
        await self.traiter(signal_changement(True))
        self.assertEqual(len(self.ble.connectes), 1)

    async def test_double_signal_un_seul_message(self):
        await self.traiter(signal_nouvel_appareil(True))
        await self.traiter(signal_changement(True))
        self.assertEqual(sum(m.startswith("Client connecté") for m in self.journal), 1)

    async def test_deconnexion_vide_la_liste_et_les_abonnements(self):
        await self.traiter(signal_nouvel_appareil(True))
        self.ble.caracteristiques[1].abonne = True
        await self.traiter(signal_changement(False))
        self.assertEqual(self.ble.connectes, {})
        self.assertFalse(any(c.abonne for c in self.ble.caracteristiques))

    async def test_appareil_annonce_mais_non_connecte_ignore(self):
        await self.traiter(signal_nouvel_appareil(False))
        self.assertEqual(self.ble.connectes, {})


if __name__ == "__main__":
    unittest.main()
