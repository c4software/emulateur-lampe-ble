"""Table GATT de la lampe connectée (identique au firmware ESP32 du TP)."""

SERVICE = "795090c7-420d-4048-a24e-18e60180e23c"

TOGGLE_LED = "59b6bf7f-44de-4184-81bd-a0e3b30c919b"      # WRITE : "1" bascule la LED
LED_STATE = "d75167c8-e6f9-4f0b-b688-09d96e195f00"       # NOTIFY : "1" / "0"
TOGGLE_COUNT = "a877d87f-60bf-4ad5-ba61-56133b2cd9d4"    # NOTIFY : compteur en texte
DEVICE_NAME = "1497b8a8-64f8-11ee-8c99-0242ac120002"     # WRITE : nouveau nom (persistant)
WIFI = "10f83060-64f8-11ee-8c99-0242ac120002"            # WRITE + NOTIFY : Wi-Fi (simulé)

NOMS = {
    TOGGLE_LED: "TOGGLE_LED",
    LED_STATE: "LED_STATE",
    TOGGLE_COUNT: "TOGGLE_COUNT",
    DEVICE_NAME: "DEVICE_NAME",
    WIFI: "WIFI",
}

# (uuid, flags BlueZ) dans l'ordre de déclaration du service.
CARACTERISTIQUES = [
    (TOGGLE_LED, ["write"]),
    (LED_STATE, ["notify"]),
    (TOGGLE_COUNT, ["notify"]),
    (DEVICE_NAME, ["write"]),
    (WIFI, ["write", "notify"]),
]
