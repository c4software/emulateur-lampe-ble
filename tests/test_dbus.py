"""Test d'intégration de la couche D-Bus, sur le bus de SESSION.

On joue le rôle de BlueZ : un second client appelle StartNotify / WriteValue
sur les objets exportés et écoute les PropertiesChanged (= notifications).
Aucun adaptateur Bluetooth n'est utilisé. Test ignoré sans bus de session.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from lampe import gatt
from lampe.ble import CHEMIN_ANNONCE, CHEMIN_SERVICE, RACINE, PeripheriqueBLE
from lampe.etat import Lampe, Stockage


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "pas de bus de session")
class TestCoucheDBus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.lampe = Lampe(Stockage(Path(self.dossier.name) / "etat.json"), nom_defaut="PMW-TEST")
        self.ble = PeripheriqueBLE(self.lampe, log=lambda *a, **k: None, delai_abonnement=0.01)
        self.reannonces = []

        async def faux_renommer(nom):  # pas de BlueZ sur le bus de session
            self.reannonces.append(nom)
        self.ble.renommer = faux_renommer
        self.lampe.sur_renommage = lambda nom: asyncio.get_running_loop().create_task(self.ble.renommer(nom))

        self.ble.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self.ble.exporter()
        self.client = await MessageBus(bus_type=BusType.SESSION).connect()
        self.notifs = []
        self.client.add_message_handler(self._signal)
        await self.client.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
            member="AddMatch", signature="s",
            body=[f"type='signal',sender='{self.ble.bus.unique_name}',member='PropertiesChanged'"]))

    async def asyncTearDown(self):
        self.client.disconnect()
        self.ble.bus.disconnect()
        self.dossier.cleanup()

    def _signal(self, m: Message):
        if m.message_type == MessageType.SIGNAL and "Value" in m.body[1]:
            uuid = next(c.uuid for c in self.ble.caracteristiques if c.chemin == m.path)
            self.notifs.append((uuid, bytes(m.body[1]["Value"].value)))

    async def appel(self, uuid, membre, signature="", corps=None):
        chemin = self.ble.par_uuid[uuid].chemin
        r = await self.client.call(Message(
            destination=self.ble.bus.unique_name, path=chemin, interface="org.bluez.GattCharacteristic1",
            member=membre, signature=signature, body=corps or []))
        return r

    async def test_arbre_gatt(self):
        r = await self.client.call(Message(
            destination=self.ble.bus.unique_name, path=RACINE,
            interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects"))
        objets = r.body[0]
        self.assertNotIn(CHEMIN_ANNONCE, objets)
        self.assertEqual(objets[CHEMIN_SERVICE]["org.bluez.GattService1"]["UUID"].value, gatt.SERVICE)
        uuids = {o["org.bluez.GattCharacteristic1"]["UUID"].value: o["org.bluez.GattCharacteristic1"]["Flags"].value
                 for p, o in objets.items() if p != CHEMIN_SERVICE}
        self.assertEqual(uuids, dict(gatt.CARACTERISTIQUES))

    async def test_abonnement_puis_ecriture(self):
        await self.appel(gatt.LED_STATE, "StartNotify")
        await self.appel(gatt.TOGGLE_COUNT, "StartNotify")
        await asyncio.sleep(0.1)
        self.assertIn((gatt.LED_STATE, b"0"), self.notifs)      # valeur initiale envoyée
        self.assertIn((gatt.TOGGLE_COUNT, b"0"), self.notifs)
        self.notifs.clear()
        r = await self.appel(gatt.TOGGLE_LED, "WriteValue", "aya{sv}", [b"1", {}])
        self.assertEqual(r.message_type, MessageType.METHOD_RETURN)
        await asyncio.sleep(0.05)
        self.assertEqual(self.notifs, [(gatt.LED_STATE, b"1"), (gatt.TOGGLE_COUNT, b"1")])

    async def test_renommage_et_erreur(self):
        r = await self.appel(gatt.DEVICE_NAME, "WriteValue", "aya{sv}", ["PMW-Bob".encode(), {}])
        self.assertEqual(r.message_type, MessageType.METHOD_RETURN)
        await asyncio.sleep(0.05)
        self.assertEqual(self.reannonces, ["PMW-Bob"])
        annonce = await self.client.call(Message(
            destination=self.ble.bus.unique_name, path=CHEMIN_ANNONCE,
            interface="org.freedesktop.DBus.Properties", member="Get", signature="ss",
            body=["org.bluez.LEAdvertisement1", "LocalName"]))
        self.assertEqual(annonce.body[0], Variant("s", "PMW-Bob"))
        r = await self.appel(gatt.DEVICE_NAME, "WriteValue", "aya{sv}", [b"", {}])
        self.assertEqual(r.error_name, "org.bluez.Error.InvalidValueLength")

    async def test_ecriture_interdite_sur_notify(self):
        r = await self.appel(gatt.LED_STATE, "WriteValue", "aya{sv}", [b"1", {}])
        self.assertEqual(r.error_name, "org.bluez.Error.NotPermitted")


if __name__ == "__main__":
    unittest.main()
