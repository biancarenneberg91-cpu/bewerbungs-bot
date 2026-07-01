"""
Bewerbungs-Bot - Discord Team-Bewerbungssystem mit KI-Auswertung
==================================================================
Zweiter Bot (eigenständig von NEXUS), für Team-Bewerbungen.

Flow:
1. Staff postet einen Bewerbungs-Button via /setup-bewerbung in einem Channel
2. User klickt -> Bot öffnet ein privates Ticket (eigener Channel, nur für
   User + Staff sichtbar) und stellt dort die Fragen eine nach der anderen
3. User antwortet einfach per Chatnachricht im Ticket
4. Stellt der User stattdessen eine Frage (z.B. "Wie lange dauert das?"),
   beantwortet die KI sie direkt im Ticket und wiederholt danach die
   aktuelle Frage - der Bewerbungsfortschritt bleibt dabei erhalten
5. Nach der letzten Frage: Antworten werden per Groq KI ausgewertet
   (Score + Empfehlung)
6. Embed mit allen Antworten + KI-Einschätzung landet im Review-Channel
7. Staff kann per Buttons annehmen / ablehnen / zum Interview einladen
8. Alle Bewerbungen werden in PostgreSQL gespeichert (siehe db.py), zusätzlich
   übers Web-Dashboard einsehbar

Stack: discord.py 2.7+, Groq API (Llama 3.1), PostgreSQL (asyncpg)

WICHTIG: Für den Ticket-Flow muss im Discord Developer Portal unter
"Bot" -> "Privileged Gateway Intents" der Schalter "MESSAGE CONTENT INTENT"
aktiviert sein, sonst kann der Bot die Antworten im Ticket nicht lesen.
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
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))  # Rolle, die Tickets sieht + Buttons nutzen darf
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))  # optional: Kategorie für Ticket-Channels

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bewerbungs-bot")

groq_client = Groq(api_key=GROQ_API_KEY) if (Groq and GROQ_API_KEY) else None

intents = discord.Intents.default()
intents.message_content = True  # nötig, um Antworten im Ticket zu lesen

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------------------------
# Fragen-Katalog für den Ticket-Flow (Reihenfolge = Reihenfolge im Ticket)
# ---------------------------------------------------------------------------

QUESTIONS = [
    ("name", "Wie ist dein Name?"),
    ("alter", "Wie alt bist du?"),
    ("discord_name", "Wie ist dein Discord-Name?"),
    ("ingame_name", "Wie ist dein Ingame-Name?"),
    ("rang", "Für welchen Rang bewirbst du dich?"),
    ("motivation", "Warum möchtest du ins Team?"),
    ("erfahrung", "Welche Erfahrungen hast du bereits?"),
    ("stunden", "Wie viele Stunden pro Woche kannst du investieren?"),
    ("staerken", "Was sind deine Stärken?"),
    ("schwaechen", "Was sind deine Schwächen?"),
    ("warum_du", "Warum sollten wir gerade dich annehmen?"),
    ("regeln_gelesen", "Hast du die Serverregeln gelesen und verstanden? (Ja/Nein)"),
]

# Aktive Tickets im Arbeitsspeicher: channel_id -> Session-Dict
# {"applicant_id": int, "applicant_tag": str, "step": int, "answers": dict}
active_tickets: dict[int, dict] = {}


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


async def answer_applicant_question(question_text: str, current_field_question: str) -> str:
    """Beantwortet eine Zwischenfrage des Bewerbers im Ticket per KI, kurz und freundlich."""
    if not groq_client:
        return ("Ich kann deine Frage aktuell leider nicht per KI beantworten. "
                "Ein Team-Mitglied meldet sich dazu bei dir.")

    prompt = f"""Du bist ein freundlicher Discord-Bot, der Bewerber während einer laufenden
Team-Bewerbung begleitet. Der Bewerber hat gerade folgende Frage gestellt, statt die
aktuelle Bewerbungsfrage zu beantworten:

Frage des Bewerbers: "{question_text}"

Die aktuelle Bewerbungsfrage, die noch offen ist, lautet: "{current_field_question}"

Antworte kurz (max. 3 Sätze), freundlich, auf Deutsch. Wenn die Frage nichts mit der
Bewerbung zu tun hat oder du sie nicht sicher beantworten kannst, sag ehrlich, dass sich
ein Team-Mitglied dazu meldet. Gib NUR den Antworttext zurück, kein JSON, keine Anführungszeichen."""

    try:
        completion = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=200,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"KI-Rückfrage fehlgeschlagen: {e}")
        return "Dazu kann ich gerade keine sichere Antwort geben - ein Team-Mitglied meldet sich bei dir."


def looks_like_question(text: str) -> bool:
    """Einfache Heuristik: enthält ein '?' -> wird als Rückfrage behandelt statt als Antwort."""
    return "?" in text


# ---------------------------------------------------------------------------
# Ticket-Flow
# ---------------------------------------------------------------------------

async def create_ticket(interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user

    # Prüfen ob schon ein offenes Ticket existiert
    for channel_id, session in active_tickets.items():
        if session["applicant_id"] == user.id:
            channel = guild.get_channel(channel_id)
            if channel:
                await interaction.response.send_message(
                    f"Du hast schon eine laufende Bewerbung in {channel.mention}.", ephemeral=True
                )
                return

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if STAFF_ROLE_ID:
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
    safe_name = "".join(c for c in user.name.lower() if c.isalnum() or c == "-")[:20] or "bewerber"

    try:
        channel = await guild.create_text_channel(
            name=f"bewerbung-{safe_name}",
            overwrites=overwrites,
            category=category if isinstance(category, discord.CategoryChannel) else None,
            reason=f"Bewerbungsticket für {user}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Mir fehlt die Berechtigung, Kanäle zu erstellen. Bitte einem Admin Bescheid geben.",
            ephemeral=True,
        )
        return

    active_tickets[channel.id] = {
        "applicant_id": user.id,
        "applicant_tag": str(user),
        "step": 0,
        "answers": {},
    }

    await interaction.response.send_message(
        f"Deine Bewerbung läuft jetzt in {channel.mention} weiter ✅", ephemeral=True
    )

    intro = discord.Embed(
        title="📋 Team-Bewerbung",
        description=(
            f"Hallo {user.mention}! Ich stelle dir jetzt ein paar Fragen, eine nach der anderen.\n"
            "Antworte einfach hier im Chat. Falls du zwischendurch eine Frage hast, "
            "schreib sie einfach - ich beantworte sie, bevor es weitergeht."
        ),
        color=discord.Color.blurple(),
    )
    await channel.send(embed=intro)
    await channel.send(f"**Frage 1/{len(QUESTIONS)}:** {QUESTIONS[0][1]}")


async def handle_ticket_message(message: discord.Message):
    session = active_tickets.get(message.channel.id)
    if not session or message.author.id != session["applicant_id"]:
        return

    step = session["step"]
    key, question_text = QUESTIONS[step]
    content = message.content.strip()

    if looks_like_question(content):
        async with message.channel.typing():
            answer = await answer_applicant_question(content, question_text)
        await message.channel.send(answer)
        await message.channel.send(f"**Frage {step + 1}/{len(QUESTIONS)}:** {question_text}")
        return

    session["answers"][key] = content
    session["step"] += 1

    if session["step"] < len(QUESTIONS):
        next_key, next_question = QUESTIONS[session["step"]]
        await message.channel.send(f"**Frage {session['step'] + 1}/{len(QUESTIONS)}:** {next_question}")
    else:
        await finalize_ticket_application(message.channel, session)
        del active_tickets[message.channel.id]


async def finalize_ticket_application(channel: discord.TextChannel, session: dict):
    answers = session["answers"]
    answers["applicant_id"] = session["applicant_id"]
    answers["applicant_tag"] = session["applicant_tag"]
    answers["submitted_at"] = datetime.now(timezone.utc).isoformat()

    async with channel.typing():
        ai_result = await evaluate_with_ai(answers)
    app_id = await db.save_application(answers, ai_result)

    embed = build_review_embed(app_id, answers, ai_result)
    review_channel = channel.guild.get_channel(REVIEW_CHANNEL_ID)
    if review_channel:
        await review_channel.send(
            embed=embed,
            view=ReviewView(app_id, session["applicant_id"], channel.id),
        )
    else:
        log.warning("REVIEW_CHANNEL_ID nicht gefunden - Bewerbung nur gespeichert.")

    await channel.send(
        "✅ Danke, deine Bewerbung ist vollständig! Das Team meldet sich bald bei dir. "
        "Dieses Ticket bleibt offen, falls noch Rückfragen kommen."
    )


# ---------------------------------------------------------------------------
# Review-Embed + Review-Buttons (Annehmen / Interview / Ablehnen)
# ---------------------------------------------------------------------------

EMPFEHLUNG_COLOR = {
    "Annehmen": discord.Color.green(),
    "Interview": discord.Color.gold(),
    "Ablehnen": discord.Color.red(),
}


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


STATUS_MESSAGES = {
    "Angenommen": "🎉 Herzlichen Glückwunsch! Deine Bewerbung wurde **angenommen**. Das Team meldet sich bei dir mit den nächsten Schritten.",
    "Interview": "🎙️ Deine Bewerbung hat uns überzeugt - wir laden dich zu einem **Interview** ein. Das Team meldet sich bei dir, um einen Termin zu finden.",
    "Abgelehnt": "Leider müssen wir dir mitteilen, dass deine Bewerbung **abgelehnt** wurde. Danke trotzdem für dein Interesse und deine Zeit!",
}


class ReviewView(discord.ui.View):
    def __init__(self, app_id: int, applicant_id: int | None = None, ticket_channel_id: int | None = None):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.applicant_id = applicant_id
        self.ticket_channel_id = ticket_channel_id

    async def _notify_applicant(self, interaction: discord.Interaction, status: str):
        text = STATUS_MESSAGES.get(status)
        if not text or not self.applicant_id:
            return

        dm_sent = False
        try:
            user = await interaction.client.fetch_user(self.applicant_id)
            await user.send(text)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        if self.ticket_channel_id:
            ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)
            if ticket_channel:
                extra = "" if dm_sent else "\n*(Konnte dir zusätzlich keine DM schicken - Nachrichten evtl. deaktiviert.)*"
                try:
                    await ticket_channel.send(f"<@{self.applicant_id}> {text}{extra}")
                except (discord.Forbidden, discord.HTTPException):
                    pass

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
        await self._notify_applicant(interaction, status)

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
        await create_ticket(interaction)


# ---------------------------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="setup-bewerbung", description="Postet den Bewerbungs-Button in diesem Channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_bewerbung(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Team-Bewerbung",
        description="Klicke auf den Button unten, um dich für unser Team zu bewerben.\n"
                     "Es öffnet sich ein privates Ticket, in dem ich dir ein paar Fragen stelle.",
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


@bot.tree.command(name="ticket-schliessen", description="Schließt das aktuelle Bewerbungsticket")
@app_commands.checks.has_permissions(administrator=True)
async def close_ticket(interaction: discord.Interaction):
    active_tickets.pop(interaction.channel.id, None)
    await interaction.response.send_message("Ticket wird in 5 Sekunden gelöscht...")
    await asyncio.sleep(5)
    await interaction.channel.delete()


# ---------------------------------------------------------------------------
# Bot Events
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await handle_ticket_message(message)
    await bot.process_commands(message)


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
