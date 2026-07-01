# Bewerbungs-Bot (Discord)

Gehört zusammen mit dem Dashboard-Repo, teilt sich dieselbe Postgres-Datenbank.

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
5. Fertig — Railway erkennt automatisch, dass es Python ist und startet über `Procfile`.

Slash Commands: `/setup-bewerbung` (postet den Bewerbungs-Button) und `/bewerbungen` (Liste der letzten Bewerbungen).
