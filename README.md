# Bewerbungs-Bot + Dashboard

Team-Bewerbungssystem für Discord mit KI-Auswertung (Groq/Llama 3.1) und Web-Dashboard —
analog zum NEXUS-Stack, aber als eigenständiges Projekt.

**Architektur: zwei Railway-Services + eine gemeinsame PostgreSQL-Datenbank**

```
bewerbungs-bot/
├── bot/            -> Discord-Bot-Service
│   ├── bot.py
│   ├── db.py
│   ├── requirements.txt
│   ├── Procfile
│   └── railway.json
└── dashboard/      -> Web-Dashboard-Service
    ├── app.py
    ├── templates/
    ├── requirements.txt
    ├── Procfile
    └── railway.json
```

Beide Services laufen unabhängig, teilen sich aber dieselbe Datenbank über `DATABASE_URL`.

## Setup

### 1. Bot im Discord Developer Portal anlegen
- https://discord.com/developers/applications -> New Application
- Bot -> Token kopieren
- Keine privilegierten Intents nötig (kein Message Content)
- OAuth2 -> URL Generator -> Scopes: `bot`, `applications.commands` -> Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`
- Bot über generierten Link auf den Server einladen

### 2. GitHub-Repo anlegen
- Dieses gesamte Verzeichnis (`bot/` + `dashboard/`) als ein GitHub-Repo pushen
- `.env` NICHT mit hochladen — liegt bereits in `.gitignore`

### 3. Railway-Projekt mit drei Komponenten aufbauen

**a) PostgreSQL-Datenbank**
- railway.app -> "New Project" -> "Provision PostgreSQL"
- Railway legt automatisch eine `DATABASE_URL`-Variable für dieses Plugin an

**b) Bot-Service**
- Im selben Projekt: "New" -> "GitHub Repo" -> dein Repo wählen
- Unter Settings -> "Root Directory" auf `bot` setzen (wichtig, sonst findet Railway die falschen Dateien)
- Unter "Variables" eintragen:
  ```
  DISCORD_TOKEN=dein_bot_token
  GROQ_API_KEY=dein_groq_key
  REVIEW_CHANNEL_ID=channel_id_fuer_review
  STAFF_ROLE_ID=rollen_id_die_entscheiden_darf
  DATABASE_URL=${{Postgres.DATABASE_URL}}
  ```
  Die letzte Zeile ist eine Railway-Variable-Referenz — verweist auf das DB-Plugin, kein Copy-Paste nötig (Railway bietet das per Dropdown an, wenn du "Add Reference" klickst)

**c) Dashboard-Service**
- Im selben Projekt nochmal: "New" -> "GitHub Repo" -> dasselbe Repo wählen
- "Root Directory" auf `dashboard` setzen
- Unter "Variables" eintragen:
  ```
  DASHBOARD_PASSWORD=ein_sicheres_passwort
  SECRET_KEY=langer_zufaelliger_string
  DATABASE_URL=${{Postgres.DATABASE_URL}}
  ```
- Unter "Settings" -> "Networking" -> "Generate Domain" klicken, um eine öffentliche URL fürs Dashboard zu bekommen

Damit hast du am Ende 3 Komponenten im selben Railway-Projekt: Postgres, Bot, Dashboard.

### 4. Channel-/Rollen-IDs herausfinden
Discord Einstellungen -> Erweitert -> Entwicklermodus an -> Rechtsklick auf Channel/Rolle -> "ID kopieren"

### 5. Updates
Push auf den verbundenen Branch -> beide Railway-Services redeployen automatisch, unabhängig voneinander.

## Benutzung

**In Discord:**
- `/setup-bewerbung` (als Admin) postet den Bewerbungs-Button in den aktuellen Channel
- User klicken "📝 Bewerbung starten" -> 3 Modal-Schritte (Discord erlaubt max. 5 Felder pro Modal)
- Nach Abschluss läuft automatisch die KI-Auswertung, das Ergebnis landet als Embed im Review-Channel
- Staff (mit `STAFF_ROLE_ID`) entscheidet über Annehmen / Interview / Ablehnen-Buttons — die KI gibt nur eine Einschätzung, nie die Entscheidung
- `/bewerbungen` zeigt eine Liste der letzten Bewerbungen direkt in Discord

**Im Dashboard:**
- Unter der generierten Railway-Domain mit `DASHBOARD_PASSWORD` einloggen
- Übersicht aller Bewerbungen, filterbar nach Status, mit KI-Score als Ring dargestellt
- Klick auf eine Bewerbung öffnet die Detailansicht mit allen Antworten + KI-Einschätzung
- Status auch direkt im Dashboard änderbar (wird genauso wie über Discord-Buttons in der DB gespeichert)

## Technische Hinweise

- Discord erlaubt max. 5 Eingabefelder pro Modal — daher 3 verbundene Modals (Teil 1/2/3), jeweils per "Weiter"-Button verknüpft
- Persistenter Start-Button übersteht Bot-Neustarts (`bot.add_view()` in `on_ready`)
- Datenbank: PostgreSQL über `asyncpg` (Bot) bzw. `psycopg2` (Dashboard) — Schema wird beim Bot-Start automatisch angelegt (`db.init_db()`), kein manuelles Migrations-Setup nötig
- Daten bleiben über Redeploys hinweg erhalten, da sie in der separaten Postgres-Komponente liegen, nicht im Service-Dateisystem
- KI-Modell: `llama-3.1-8b-instant` über Groq, liefert Score (1-10), Zusammenfassung, erkannte Stärken/Risiken und eine Empfehlung (Annehmen/Interview/Ablehnen) — rein als Signal, Entscheidung bleibt beim Team
- Fällt die KI-Auswertung aus (Rate Limit, kein Key), wird die Bewerbung trotzdem gespeichert und gepostet, nur ohne Score — manuelle Prüfung möglich
- Dashboard-Login ist ein einfacher Passwortschutz (kein Pro-User-Login) — reicht für ein kleines Team, kann bei Bedarf später auf Discord-OAuth umgestellt werden
