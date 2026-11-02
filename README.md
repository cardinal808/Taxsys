# Taxsys leerlingenvervoer in Home Assistant

Haalt de ophaal- en brengtijden van het leerlingenvervoer (groepsvervoer) op uit het **Taxsys ouderportaal** en maakt ze beschikbaar als sensoren in Home Assistant.

Taxsys heeft geen publieke API. Dit script logt in op het ouderportaal (`account.taxsys.nl`) zoals een browser dat doet en leest de tabel *Vervoerschema* uit.

> ⚠️ Onofficieel. Niet gelieerd aan Taxsys / B.S. Automatisering of aan je vervoerder. Als het portaal verandert, kan dit stoppen met werken.

## Wat je krijgt

| Entiteit | Inhoud |
|---|---|
| `sensor.taxsys_taxi` | Status (`ok` / `geen ritten` / `fout`) met het hele weekschema in de attributen |
| `sensor.taxsys_taxi_ophalen` | O-tijd (ophaaltijd) van de ochtendrit van vandaag of de eerstvolgende schooldag |
| `sensor.taxsys_taxi_thuisbrengen` | A-tijd (aankomsttijd) van de middagrit |
| `binary_sensor.taxsys_taxi_venster` | Aan op werkdagen met een rit: 07:15 tot 15 min na de ophaaltijd, en 13:30 tot 15 min na de thuiskomst |

De ritsensoren hebben attributen voor `tijd`, `dag`, `naar`, `status`, `chauffeur`, `live_locatie` en `live_tijd`.

Het schema wordt elke 5 minuten ververst binnen de venstertijden en één keer om 06:30 op werkdagen. Buiten die tijden wordt er niet ververst.

## Installatie

1. Kopieer `taxsys.py` naar `/config/taxsys/taxsys.py`.
2. Kopieer `packages/taxsys.yaml` naar je packages-map. Packages moeten aan staan:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Voeg aan `/config/secrets.yaml` toe (helemaal links beginnen, zonder inspringing):
   ```yaml
   taxsys_username: 'jouw-gebruikersnaam-of-email'
   taxsys_password: 'jouw-wachtwoord'
   taxsys_company: 'G-XXX'
   taxsys_klantnr: '0000000'
   ```
4. Herstart Home Assistant.

### Waar vind ik `company` en `klantnr`?

- **company**: staat in de URL van de inlogpagina van je vervoerder, bijvoorbeeld `https://account.taxsys.nl/taxsys/?page=login&company=G-XXX`.
- **klantnr**: log in en kijk naar de URL van de pagina met het vervoerschema: `...?page=deelnemerMutatie&klantNr=0000000&company=G-XXX`.

## Dashboardkaart

`dashboard/taxsys_card.yaml` is een [Mushroom](https://github.com/piitaya/lovelace-mushroom) template card (via HACS). Hij toont bijvoorbeeld *Ophalen 08:00 · Thuis 14:20 · John* en is alleen zichtbaar als `binary_sensor.taxsys_taxi_venster` aan staat. De chauffeursnaam wordt uit `#### - NAAM` gehaald.

Voeg hem toe via **Kaart toevoegen → Handmatig** en plak de inhoud.

## Testen

Met de Terminal/SSH-add-on (daar zit standaard geen Python in):

```bash
apk add python3 py3-yaml
python3 /config/taxsys/taxsys.py | grep -o '"status": "[^"]*"\|"fout": [^,}]*'
```

`"status": "ok"` betekent dat inloggen en uitlezen werken.

## Problemen

- **Sensor `unavailable`**: Home Assistant kon het commando niet uitvoeren. Controleer het pad naar het script.
- **`fout: mapping values are not allowed here`**: er zit een fout in `secrets.yaml`, vaak inspringing. Het script probeert dan toch de `taxsys_`-regels te lezen.
- **`fout: inloggen mislukt`**: controleer je gebruikersnaam en wachtwoord.

## Licentie

[MIT](LICENSE)
