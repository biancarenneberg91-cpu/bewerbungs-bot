# Bewerbungs-Bot (Discord)

Gehört zusammen mit dem Dashboard-Repo, teilt sich dieselbe Postgres-Datenbank.

## Wie es funktioniert (Ticket-Flow)

1. Staff führt `/setup-bewerbung` aus -> postet einen Button im Channel
2. User klickt auf den Button -> Bot öffnet ein **privates Ticket** (eigener
   Channel, nur für den Bewerber + Team-Rolle sichtbar)
3. Der Bot stellt dort die Fragen eine nach der anderen, der User antwortet
   einfach per Chatnachricht
4. Stellt der User stattdessen eine Rückfrage (z.B. "Wie lange dauert das?"),
   erkennt der Bot das am Fragezeichen und beantwortet sie per KI, bevor die
   aktuelle Bewerbungsfrage wiederholt wird
5. Nach der letzten Frage läuft automatisch die KI-Auswertung, das Ergebnis
   landet als Embed im Review-Channel
6. Staff entscheidet über Annehmen / Interview / Ablehnen-Buttons — der
   Bewerber bekommt automatisch eine **Discord-Direktnachricht** mit dem
   Ergebnis. Hat der Bewerber DMs deaktiviert, postet der Bot die
   Nachricht zusätzlich (mit Erwähnung) direkt in seinem Ticket-Channel.
7. `/ticket-schliessen` löscht das Ticket wieder (als Admin, im Ticket-Channel ausführen)

## Wichtiger Setup-Schritt: Message Content Intent aktivieren

Damit der Bot die Chatnachrichten im Ticket lesen kann, muss im
[Discord Developer Portal](https://discord.com/developers/applications)
bei deiner Bot-Anwendung Folgendes aktiviert werden:

1. Deine Anwendung öffnen -> links auf "Bot"
2. Runterscrollen zu "Privileged Gateway Intents"
3. Schalter bei **"MESSAGE CONTENT INTENT"** aktivieren
4. Speichern

Ohne diesen Schritt bleibt der Bot im Ticket stumm, weil er die Nachrichten
des Bewerbers nicht lesen darf.

## Railway-Setup (einfach, kein "Root Directory" nötig)

1. Railway-Projekt öffnen (oder neu anlegen)
2. Falls noch nicht geschehen: "New" → "Provision PostgreSQL"
3. "New" → "GitHub Repo" → **dieses Repo** auswählen
4. Bei "Variables" eintragen:
   ```
   DISCORD_TOKEN=dein_bot_token
   GROQ_API_KEY=dein_groq_key
   REVIEW_CHANNEL_ID=channel_id
   STAFF_ROLE_ID=rollen_id
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   ```
   Bei `DATABASE_URL` auf "Add Reference" klicken und Postgres auswählen, statt selbst einzutippen.

   Optional, falls du die Ticket-Channels in eine bestimmte Kategorie sortieren willst:
   ```
   TICKET_CATEGORY_ID=kategorie_id
   ```
   (Kategorie-ID bekommst du genauso wie Channel-IDs per Rechtsklick -> "ID kopieren", Entwicklermodus muss an sein)
5. Fertig — Railway erkennt automatisch, dass es Python ist und startet über `Procfile`.

## Nötige Bot-Berechtigungen in Discord

Damit der Bot Ticket-Channels erstellen kann, braucht er beim Einladen (OAuth2 URL Generator) zusätzlich zu den bisherigen Rechten auch:
- **Manage Channels** (Kanäle verwalten)

Ohne diese Berechtigung schlägt das Erstellen des Tickets fehl.

Slash Commands:
- `/setup-bewerbung` — postet den Bewerbungs-Button
- `/bewerbungen` — Liste der letzten Bewerbungen
- `/ticket-schliessen` — löscht das aktuelle Ticket (im Ticket-Channel ausführen)
