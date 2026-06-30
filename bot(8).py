"""
Bewerbungs-Bot - Discord Team-Bewerbungssystem mit KI-Auswertung
==================================================================
Zweiter Bot (eigenständig von NEXUS), für Team-Bewerbungen.

Flow:
1. Staff postet einen Bewerbungs-Button via /setup-bewerbung in einem Channel
2. User klickt -> 3 verbundene Modals (max. 5 Felder pro Modal, Discord-Limit)
3. Nach Modal 3: Antworten werden per Groq KI ausgewertet (Score + Empfehlung)
4. Embed mit allen Antworten + KI-Einschätzung landet im Review-Channel
5. Staff kann per Buttons annehmen / ablehnen / zum Interview einladen
6. Alle Bewerbungen werden in PostgreSQL gespeichert (siehe db.py), zusätzlich
   übers Web-Dashboard (separater Railway-Service) einsehbar

Stack: discord.py 2.7+, Groq API (Llama 3.1), PostgreSQL (asyncpg)
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import db

try:
    from groq import Groq
except ImportError:
    Groq = None

load_dotenv()

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
REVIEW_CHANNEL_ID = int(os.getenv("REVIEW_CHANNEL_ID", "0"))
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))  # Rolle, die Buttons nutzen darf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bewerbungs-bot")

groq_client = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None

intents = discord.Intents.default()
intents.message_content = False  # nicht nötig, alles läuft über Modals/Slash-Commands

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------------------------
# Storage läuft über db.py (PostgreSQL via asyncpg) - siehe init_db() in on_ready
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# KI-Auswertung (Groq / Llama 3.1)
# ---------------------------------------------------------------------------

async def evaluate_with_ai(answers: dict) -> dict:
    """Lässt die Bewerbung per KI bewerten. Gibt dict mit score, summary, empfehlung zurück."""
    if not groq_client:
        return {
            "score": None,
            "summary": "KI-Auswertung nicht verfügbar (kein GROQ_API_KEY konfiguriert).",
            "empfehlung": "Manuell prüfen",
        }

    prompt = f"""Du bewertest eine Team-Bewerbung für einen Discord-Roleplay-Server.
Beantworte AUSSCHLIESSLICH mit validem JSON, keine Markdown-Codeblöcke, kein Fließtext davor/danach.

Bewerbungsdaten:
Name: {answers.get('name')}
Alter: {answers.get('alter')}
Discord-Name: {answers.get('discord_name')}
Ingame-Name: {answers.get('ingame_name')}
Gewünschter Rang: {answers.get('rang')}
Motivation: {answers.get('motivation')}
Erfahrung: {answers.get('erfahrung')}
Verfügbare Stunden/Woche: {answers.get('stunden')}
Stärken: {answers.get('staerken')}
Schwächen: {answers.get('schwaechen')}
Warum gerade diese Person: {answers.get('warum_du')}
Regeln gelesen: {answers.get('regeln_gelesen')}

Gib JSON in genau diesem Format zurück:
{{
  "score": <Zahl 1-10>,
  "summary": "<2-3 Sätze Einschätzung auf Deutsch>",
  "staerken_erkannt": "<kurze Stichpunkte>",
  "risiken": "<kurze Stichpunkte, z.B. wenig Erfahrung oder wenig Zeit>",
  "empfehlung": "<einer von: Annehmen, Interview, Ablehnen>"
}}"""

    try:
        completion = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        raw = completion.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(raw)
        return result
    except Exception as e:
        log.error(f"KI-Auswertung fehlgeschlagen: {e}")
        return {
            "score": None,
            "summary": f"KI-Auswertung fehlgeschlagen ({e}). Bitte manuell prüfen.",
            "empfehlung": "Manuell prüfen",
        }


# ---------------------------------------------------------------------------
# Modals (3 verbundene Schritte wegen 5-Felder-Limit pro Modal)
# ---------------------------------------------------------------------------

class BewerbungModal3(discord.ui.Modal, title="Bewerbung - Teil 3/3"):
    staerken = discord.ui.TextInput(
        label="Was sind deine Stärken?", style=discord.TextStyle.paragraph, required=True, max_length=500
    )
    schwaechen = discord.ui.TextInput(
        label="Was sind deine Schwächen?", style=discord.TextStyle.paragraph, required=True, max_length=500
    )
    warum_du = discord.ui.TextInput(
        label="Warum sollten wir gerade dich annehmen?",
        style=discord.TextStyle.paragraph, required=True, max_length=500
    )
    regeln_gelesen = discord.ui.TextInput(
        label="Serverregeln gelesen & verstanden? (Ja/Nein)",
        style=discord.TextStyle.short, required=True, max_length=10, placeholder="Ja"
    )

    def __init__(self, answers: dict):
        super().__init__()
        self.answers = answers

    async def on_submit(self, interaction: discord.Interaction):
        self.answers.update({
            "staerken": self.staerken.value,
            "schwaechen": self.schwaechen.value,
            "warum_du": self.warum_du.value,
            "regeln_gelesen": self.regeln_gelesen.value,
        })
        await interaction.response.defer(ephemeral=True, thinking=True)
        await finalize_application(interaction, self.answers)


class BewerbungModal2(discord.ui.Modal, title="Bewerbung - Teil 2/3"):
    motivation = discord.ui.TextInput(
        label="Warum möchtest du ins Team?", style=discord.TextStyle.paragraph, required=True, max_length=500
    )
    erfahrung = discord.ui.TextInput(
        label="Welche Erfahrungen hast du bereits?", style=discord.TextStyle.paragraph, required=True, max_length=500
    )
    stunden = discord.ui.TextInput(
        label="Stunden/Woche verfügbar?", style=discord.TextStyle.short, required=True, max_length=50
    )

    def __init__(self, answers: dict):
        super().__init__()
        self.answers = answers

    async def on_submit(self, interaction: discord.Interaction):
        self.answers.update({
            "motivation": self.motivation.value,
            "erfahrung": self.erfahrung.value,
            "stunden": self.stunden.value,
        })
        view = discord.ui.View()
        button = discord.ui.Button(label="Weiter zu Teil 3/3", style=discord.ButtonStyle.primary)

        async def go_next(inner_interaction: discord.Interaction):
            await inner_interaction.response.send_modal(BewerbungModal3(self.answers))

        button.callback = go_next
        view.add_item(button)
        await interaction.response.send_message(
            "Teil 2 gespeichert. Klicke weiter für den letzten Teil:", view=view, ephemeral=True
        )


class BewerbungModal1(discord.ui.Modal, title="Bewerbung - Teil 1/3"):
    name = discord.ui.TextInput(label="Name", required=True, max_length=100)
    alter = discord.ui.TextInput(label="Alter", required=True, max_length=10)
    discord_name = discord.ui.TextInput(label="Discord-Name", required=True, max_length=100)
    ingame_name = discord.ui.TextInput(label="Ingame-Name", required=True, max_length=100)
    rang = discord.ui.TextInput(label="Für welchen Rang bewirbst du dich?", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        answers = {
            "name": self.name.value,
            "alter": self.alter.value,
            "discord_name": self.discord_name.value,
            "ingame_name": self.ingame_name.value,
            "rang": self.rang.value,
            "applicant_id": interaction.user.id,
            "applicant_tag": str(interaction.user),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        view = discord.ui.View()
        button = discord.ui.Button(label="Weiter zu Teil 2/3", style=discord.ButtonStyle.primary)

        async def go_next(inner_interaction: discord.Interaction):
            await inner_interaction.response.send_modal(BewerbungModal2(answers))

        button.callback = go_next
        view.add_item(button)
        await interaction.response.send_message(
            "Teil 1 gespeichert. Klicke weiter für Teil 2:", view=view, ephemeral=True
        )


# ---------------------------------------------------------------------------
# Abschluss: KI-Auswertung + Review-Post + Speichern
# ---------------------------------------------------------------------------

EMPFEHLUNG_COLOR = {
    "Annehmen": discord.Color.green(),
    "Interview": discord.Color.gold(),
    "Ablehnen": discord.Color.red(),
}


async def finalize_application(interaction: discord.Interaction, answers: dict):
    ai_result = await evaluate_with_ai(answers)
    app_id = await db.save_application(answers, ai_result)

    embed = build_review_embed(app_id, answers, ai_result)
    review_channel = interaction.client.get_channel(REVIEW_CHANNEL_ID)

    if review_channel:
        await review_channel.send(embed=embed, view=ReviewView(app_id))
    else:
        log.warning("REVIEW_CHANNEL_ID nicht gefunden - Bewerbung nur gespeichert.")

    await interaction.followup.send(
        "✅ Deine Bewerbung wurde eingereicht! Das Team meldet sich bei dir.", ephemeral=True
    )


def build_review_embed(app_id: int, a: dict, ai: dict) -> discord.Embed:
    empfehlung = ai.get("empfehlung", "Manuell prüfen")
    color = EMPFEHLUNG_COLOR.get(empfehlung, discord.Color.greyple())

    embed = discord.Embed(
        title=f"📋 Neue Bewerbung #{app_id} — {a.get('name')}",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Alter", value=a.get("alter", "-"), inline=True)
    embed.add_field(name="Discord", value=a.get("discord_name", "-"), inline=True)
    embed.add_field(name="Ingame", value=a.get("ingame_name", "-"), inline=True)
    embed.add_field(name="Rang", value=a.get("rang", "-"), inline=True)
    embed.add_field(name="Stunden/Woche", value=a.get("stunden", "-"), inline=True)
    embed.add_field(name="Regeln gelesen", value=a.get("regeln_gelesen", "-"), inline=True)
    embed.add_field(name="Motivation", value=a.get("motivation", "-")[:1024], inline=False)
    embed.add_field(name="Erfahrung", value=a.get("erfahrung", "-")[:1024], inline=False)
    embed.add_field(name="Stärken", value=a.get("staerken", "-")[:1024], inline=False)
    embed.add_field(name="Schwächen", value=a.get("schwaechen", "-")[:1024], inline=False)
    embed.add_field(name="Warum diese Person", value=a.get("warum_du", "-")[:1024], inline=False)

    score = ai.get("score")
    score_str = f"{score}/10" if score is not None else "n/a"
    embed.add_field(
        name=f"🤖 KI-Einschätzung — Score: {score_str} — Empfehlung: {empfehlung}",
        value=ai.get("summary", "-")[:1024],
        inline=False,
    )
    if ai.get("risiken"):
        embed.add_field(name="⚠️ Mögliche Risiken laut KI", value=str(ai["risiken"])[:1024], inline=False)

    embed.set_footer(text=f"Bewerber-ID: {a.get('applicant_id')}")
    return embed


# ---------------------------------------------------------------------------
# Review-Buttons (Annehmen / Interview / Ablehnen)
# ---------------------------------------------------------------------------

def staff_only():
    def predicate(interaction: discord.Interaction) -> bool:
        if STAFF_ROLE_ID == 0:
            return True
        return any(r.id == STAFF_ROLE_ID for r in getattr(interaction.user, "roles", []))
    return predicate


class ReviewView(discord.ui.View):
    def __init__(self, app_id: int):
        super().__init__(timeout=None)
        self.app_id = app_id

    async def _handle(self, interaction: discord.Interaction, status: str, label: str):
        if STAFF_ROLE_ID and not any(r.id == STAFF_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("Nur Team-Mitglieder können das.", ephemeral=True)
            return
        await db.update_application_status(self.app_id, status, str(interaction.user))
        embed = interaction.message.embeds[0]
        embed.add_field(name="Entscheidung", value=f"{label} von {interaction.user.mention}", inline=False)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="✅ Annehmen", style=discord.ButtonStyle.success, custom_id="bewerbung_annehmen")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "Angenommen", "Angenommen")

    @discord.ui.button(label="🎙️ Interview", style=discord.ButtonStyle.primary, custom_id="bewerbung_interview")
    async def interview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "Interview", "Zum Interview eingeladen")

    @discord.ui.button(label="❌ Ablehnen", style=discord.ButtonStyle.danger, custom_id="bewerbung_ablehnen")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "Abgelehnt", "Abgelehnt")


# ---------------------------------------------------------------------------
# Start-Button (persistent), den Staff im Channel postet
# ---------------------------------------------------------------------------

class StartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 Bewerbung starten", style=discord.ButtonStyle.primary, custom_id="bewerbung_start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BewerbungModal1())


# ---------------------------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="setup-bewerbung", description="Postet den Bewerbungs-Button in diesem Channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_bewerbung(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Team-Bewerbung",
        description="Klicke auf den Button unten, um dich für unser Team zu bewerben.\n"
                     "Die Bewerbung besteht aus 3 kurzen Schritten.",
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=StartView())
    await interaction.response.send_message("Bewerbungs-Button gepostet ✅", ephemeral=True)


@bot.tree.command(name="bewerbungen", description="Zeigt die letzten Bewerbungen mit Status")
@app_commands.checks.has_permissions(administrator=True)
async def list_bewerbungen(interaction: discord.Interaction, anzahl: int = 10):
    apps = await db.get_recent_applications(anzahl)
    if not apps:
        await interaction.response.send_message("Noch keine Bewerbungen vorhanden.", ephemeral=True)
        return
    lines = [
        f"#{a['id']} — {a.get('name')} — {a.get('rang')} — {a.get('status', 'Ausstehend')}"
        for a in apps
    ]
    await interaction.response.send_message("**Letzte Bewerbungen:**\n" + "\n".join(lines), ephemeral=True)


# ---------------------------------------------------------------------------
# Bot Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    bot.add_view(StartView())  # persistent view nach Neustart wiederherstellen
    try:
        await db.init_db()
        log.info("Datenbankverbindung & Schema bereit.")
    except Exception as e:
        log.error(f"Datenbank-Init fehlgeschlagen: {e}")
    try:
        synced = await bot.tree.sync()
        log.info(f"{len(synced)} Slash-Commands synchronisiert.")
    except Exception as e:
        log.error(f"Sync fehlgeschlagen: {e}")
    log.info(f"Eingeloggt als {bot.user} (ID: {bot.user.id})")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN fehlt in den Railway Environment Variables!")
    bot.run(DISCORD_TOKEN)
