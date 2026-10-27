"""Périphérique BLE via BlueZ (D-Bus système), avec dbus-fast.

Publie :
- une application GATT (org.bluez.GattManager1.RegisterApplication) ;
- une annonce LE (org.bluez.LEAdvertisingManager1.RegisterAdvertisement)
  avec l'UUID du service dans l'annonce et le nom dans la réponse de scan.
"""

import asyncio

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.errors import DBusError
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from . import gatt
from .etat import Lampe

BLUEZ = "org.bluez"
RACINE = "/fr/playmoweb/lampe"
CHEMIN_SERVICE = f"{RACINE}/service0"
CHEMIN_ANNONCE = "/fr/playmoweb/annonce0"  # hors de RACINE : pas dans l'arbre GATT


class ErreurBluez(Exception):
    pass


# --------------------------------------------------------------------------- objets exportés

class Caracteristique(ServiceInterface):
    def __init__(self, emulateur: "PeripheriqueBLE", index: int, uuid: str, flags: list[str]):
        super().__init__("org.bluez.GattCharacteristic1")
        self.emulateur = emulateur
        self.chemin = f"{CHEMIN_SERVICE}/char{index}"
        self.uuid = uuid
        self.flags = flags
        self.valeur = b""
        self.abonne = False

    @property
    def nom(self) -> str:
        return gatt.NOMS.get(self.uuid, self.uuid)

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":  # noqa: F821
        if "read" not in self.flags:
            raise DBusError("org.bluez.Error.NotPermitted", "Lecture non autorisée")
        return self.valeur

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}"):  # noqa: F821
        if "write" not in self.flags:
            raise DBusError("org.bluez.Error.NotPermitted", "Écriture non autorisée")
        self.emulateur.sur_ecriture(self, bytes(value), options)

    @method()
    def StartNotify(self):
        if self.abonne:
            return
        self.abonne = True
        self.emit_properties_changed({"Notifying": True})
        self.emulateur.sur_abonnement(self)

    @method()
    def StopNotify(self):
        if not self.abonne:
            return
        self.abonne = False
        self.emit_properties_changed({"Notifying": False})
        self.emulateur.log(f"Désabonnement de {self.nom}")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":  # noqa: F821
        return self.uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":  # noqa: F821
        return CHEMIN_SERVICE

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":  # noqa: F821
        return self.flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":  # noqa: F821
        return self.valeur

    @dbus_property(access=PropertyAccess.READ)
    def Notifying(self) -> "b":  # noqa: F821
        return self.abonne

    def notifier(self, octets: bytes) -> None:
        self.valeur = octets
        self.emit_properties_changed({"Value": octets})


class Service(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.GattService1")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":  # noqa: F821
        return gatt.SERVICE

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":  # noqa: F821
        return True


class Annonce(ServiceInterface):
    def __init__(self, emulateur: "PeripheriqueBLE"):
        super().__init__("org.bluez.LEAdvertisement1")
        self.emulateur = emulateur

    @method()
    def Release(self):
        self.emulateur.log("BlueZ a libéré l'annonce (Release)")

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":  # noqa: F821
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":  # noqa: F821
        return [gatt.SERVICE]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":  # noqa: F821
        return self.emulateur.lampe.nom

    @dbus_property(access=PropertyAccess.READ)
    def Discoverable(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def MinInterval(self) -> "u":  # noqa: F821
        return 100

    @dbus_property(access=PropertyAccess.READ)
    def MaxInterval(self) -> "u":  # noqa: F821
        return 150


# --------------------------------------------------------------------------- périphérique

class PeripheriqueBLE:
    def __init__(self, lampe: Lampe, adaptateur: str = "hci0", log=print,
                 alias_adaptateur: bool = False, delai_abonnement: float = 0.1):
        self.lampe = lampe
        self.adaptateur = adaptateur
        self.chemin_adaptateur = f"/org/bluez/{adaptateur}"
        self.log = log
        self.alias_adaptateur = alias_adaptateur
        self.delai_abonnement = delai_abonnement
        self.bus: MessageBus | None = None
        self.service = Service()
        self.caracteristiques = [
            Caracteristique(self, i, uuid, flags) for i, (uuid, flags) in enumerate(gatt.CARACTERISTIQUES)
        ]
        self.par_uuid = {c.uuid: c for c in self.caracteristiques}
        self.annonce = Annonce(self)
        self.annonce_active = False
        self.alias_origine: str | None = None
        self.appareils: dict[str, str] = {}
        self.connectes: dict[str, str] = {}  # chemin D-Bus -> nom, pour le tableau de bord
        self.adresse = ""

        lampe.notifier = self.notifier
        lampe.sur_renommage = lambda nom: asyncio.get_running_loop().create_task(self.renommer(nom))

    # ----------------------------------------------------------------- D-Bus bas niveau
    async def appel(self, chemin, interface, membre, signature="", corps=None):
        reponse = await self.bus.call(Message(
            destination=BLUEZ, path=chemin, interface=interface, member=membre,
            signature=signature, body=corps or [],
        ))
        if reponse.message_type == MessageType.ERROR:
            raise ErreurBluez(f"{interface}.{membre} : {reponse.error_name} {reponse.body}")
        return reponse.body

    async def propriete(self, chemin, interface, nom):
        corps = await self.appel(chemin, "org.freedesktop.DBus.Properties", "Get", "ss", [interface, nom])
        return corps[0].value

    async def definir_propriete(self, chemin, interface, nom, variant):
        await self.appel(chemin, "org.freedesktop.DBus.Properties", "Set", "ssv", [interface, nom, variant])

    # ----------------------------------------------------------------- démarrage
    async def lire_adaptateur(self) -> dict:
        """Connexion au bus et vérifications de l'adaptateur. Renvoie ses propriétés."""
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            corps = await self.appel(self.chemin_adaptateur, "org.freedesktop.DBus.Properties",
                                     "GetAll", "s", ["org.bluez.Adapter1"])
        except ErreurBluez as e:
            raise ErreurBluez(f"Adaptateur {self.adaptateur} introuvable via BlueZ ({e})") from e
        self.log(f"Connecté au bus système sous le nom {self.bus.unique_name}", niveau="debug")
        props = {k: v.value for k, v in corps[0].items()}
        self.adresse = props.get("Address", "")
        if not props.get("Powered"):
            raise ErreurBluez(f"L'adaptateur {self.adaptateur} est éteint. Lancez : bluetoothctl power on")
        return props

    def exporter(self) -> None:
        """Publie les objets GATT et l'annonce sur le bus (sans les enregistrer auprès de BlueZ)."""
        bus = self.bus
        # org.freedesktop.DBus.ObjectManager (GetManagedObjects) sur RACINE est fourni par dbus-fast.
        bus.export(CHEMIN_SERVICE, self.service)
        for c in self.caracteristiques:
            bus.export(c.chemin, c)
        bus.export(CHEMIN_ANNONCE, self.annonce)

    async def demarrer(self) -> None:
        self.exporter()
        await self.surveiller_connexions()

        if self.alias_adaptateur:
            self.alias_origine = await self.propriete(self.chemin_adaptateur, "org.bluez.Adapter1", "Alias")
            await self.definir_alias(self.lampe.nom)

        await self.appel(self.chemin_adaptateur, "org.bluez.GattManager1", "RegisterApplication",
                         "oa{sv}", [RACINE, {}])
        self.log(f"Application GATT enregistrée (service {gatt.SERVICE})")
        await self.enregistrer_annonce()

    async def enregistrer_annonce(self) -> None:
        await self.appel(self.chemin_adaptateur, "org.bluez.LEAdvertisingManager1", "RegisterAdvertisement",
                         "oa{sv}", [CHEMIN_ANNONCE, {}])
        self.annonce_active = True
        self.log(f"Annonce BLE active : nom « {self.lampe.nom} », UUID de service annoncé")

    async def retirer_annonce(self) -> None:
        if not self.annonce_active:
            return
        self.annonce_active = False
        try:
            await self.appel(self.chemin_adaptateur, "org.bluez.LEAdvertisingManager1",
                             "UnregisterAdvertisement", "o", [CHEMIN_ANNONCE])
        except ErreurBluez as e:
            self.log(f"Retrait de l'annonce : {e}")

    async def renommer(self, nom: str) -> None:
        await self.retirer_annonce()
        if self.alias_adaptateur:
            await self.definir_alias(nom)
        try:
            await self.enregistrer_annonce()
        except ErreurBluez as e:
            self.log(f"ERREUR : impossible de réannoncer après renommage : {e}")

    async def definir_alias(self, nom: str) -> None:
        try:
            await self.definir_propriete(self.chemin_adaptateur, "org.bluez.Adapter1", "Alias", Variant("s", nom))
            self.log(f"Alias de l'adaptateur (nom GAP 0x2A00) : {nom}")
        except ErreurBluez as e:
            self.log(f"Impossible de changer l'alias de l'adaptateur : {e}")

    async def arreter(self) -> None:
        if not self.bus or not self.bus.connected:
            return
        await self.retirer_annonce()
        try:
            await self.appel(self.chemin_adaptateur, "org.bluez.GattManager1", "UnregisterApplication",
                             "o", [RACINE])
        except ErreurBluez:
            pass
        if self.alias_origine is not None:
            await self.definir_alias(self.alias_origine)
        self.bus.disconnect()

    # ----------------------------------------------------------------- connexions
    async def surveiller_connexions(self) -> None:
        # Un appareil déjà connu signale sa connexion par PropertiesChanged ;
        # un appareil vu pour la première fois arrive par InterfacesAdded, déjà connecté.
        for regle in (
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',"
            "member='PropertiesChanged',arg0='org.bluez.Device1'",
            "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager',"
            "member='InterfacesAdded'",
        ):
            await self.bus.call(Message(
                destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[regle],
            ))
        self.bus.add_message_handler(self._sur_signal)

        # Appareils déjà connectés au lancement de l'émulateur
        reponse = await self.bus.call(Message(
            destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        ))
        if reponse.message_type == MessageType.METHOD_RETURN:
            for chemin, interfaces in reponse.body[0].items():
                appareil = interfaces.get("org.bluez.Device1")
                if (chemin.startswith(self.chemin_adaptateur + "/dev_") and appareil
                        and appareil.get("Connected") and appareil["Connected"].value):
                    await self._journal_connexion(chemin, True)

    def _sur_signal(self, message: Message):
        if message.message_type != MessageType.SIGNAL:
            return None
        if message.member == "InterfacesAdded":
            chemin, interfaces = message.body
            appareil = interfaces.get("org.bluez.Device1")
            if (chemin.startswith(self.chemin_adaptateur + "/dev_") and appareil
                    and appareil.get("Connected") and appareil["Connected"].value):
                asyncio.get_running_loop().create_task(self._journal_connexion(chemin, True))
            return None
        if (message.member != "PropertiesChanged"
                or not message.path.startswith(self.chemin_adaptateur + "/dev_")
                or message.body[0] != "org.bluez.Device1"):
            return None
        changes = message.body[1]
        if "Connected" in changes:
            asyncio.get_running_loop().create_task(
                self._journal_connexion(message.path, changes["Connected"].value))
        return None

    async def _journal_connexion(self, chemin: str, connecte: bool) -> None:
        adresse = chemin.rsplit("dev_", 1)[-1].replace("_", ":")
        try:
            nom = await self.propriete(chemin, "org.bluez.Device1", "Alias")
        except ErreurBluez:
            nom = self.appareils.get(chemin, adresse)
        self.appareils[chemin] = nom
        if connecte:
            if chemin in self.connectes:
                return  # déjà signalé (les deux signaux de BlueZ peuvent arriver)
            self.connectes[chemin] = f"{nom} ({adresse})"
            self.log(f"Client connecté : {nom} ({adresse})")
        else:
            self.connectes.pop(chemin, None)
            self.log(f"Client déconnecté : {nom} ({adresse})")
            if not self.connectes:
                # Plus aucun client : plus personne n'écoute les notifications
                for c in self.caracteristiques:
                    c.abonne = False

    def _client(self, options: dict) -> str:
        chemin = options.get("device")
        if chemin is None:
            return "BLE"
        chemin = chemin.value
        return self.appareils.get(chemin, chemin.rsplit("dev_", 1)[-1].replace("_", ":"))

    # ----------------------------------------------------------------- événements GATT
    def sur_ecriture(self, c: Caracteristique, octets: bytes, options: dict) -> None:
        client = self._client(options)
        self.log(f"Écriture {c.nom} par {client} : {octets!r}")
        if c.uuid == gatt.TOGGLE_LED:
            self.lampe.ecrire_toggle(octets, client=f"BLE {client}")
        elif c.uuid == gatt.DEVICE_NAME:
            try:
                self.lampe.renommer(octets)
            except ValueError as e:
                self.log(f"Nom refusé : {e}")
                raise DBusError("org.bluez.Error.InvalidValueLength", str(e)) from e
        elif c.uuid == gatt.WIFI:
            self.lampe.ecrire_wifi(octets)

    def sur_abonnement(self, c: Caracteristique) -> None:
        self.log(f"Abonnement aux notifications de {c.nom}")
        valeurs = self.lampe.valeurs_abonnement(c.uuid)
        if valeurs:
            asyncio.get_running_loop().create_task(self._envoyer_apres_delai(c, valeurs))

    async def _envoyer_apres_delai(self, c: Caracteristique, valeurs: list[bytes]) -> None:
        await asyncio.sleep(self.delai_abonnement)  # laisse le CCCD s'établir côté client
        for v in valeurs:
            self.notifier(c.uuid, v, initial=True)
            await asyncio.sleep(0.02)

    def notifier(self, uuid: str, octets: bytes, initial: bool = False) -> None:
        c = self.par_uuid[uuid]
        c.notifier(octets)
        if c.abonne:
            suffixe = " (valeur initiale après abonnement)" if initial else ""
            self.log(f"Notification {c.nom} -> {octets.decode('utf-8', 'replace')!r}{suffixe}")
        else:
            self.log(f"{c.nom} = {octets.decode('utf-8', 'replace')!r} (aucun abonné, rien d'envoyé)", niveau="debug")
