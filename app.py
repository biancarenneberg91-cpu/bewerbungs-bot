"""
Web-Dashboard für den Bewerbungs-Bot.
Separater Railway-Service, teilt sich die PostgreSQL-Datenbank mit dem Bot
über dieselbe DATABASE_URL (Railway-Variable-Referenz).
"""

import os
import functools
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash

DATABASE_URL = os.getenv("DATABASE_URL")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-bitte-in-railway-ueberschreiben")

app = Flask(__name__)
app.secret_key = SECRET_KEY


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if DASHBOARD_PASSWORD and pw == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Falsches Passwort.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    status_filter = request.args.get("status", "Alle")
    conn = get_conn()
    cur = conn.cursor()
    if status_filter != "Alle":
        cur.execute(
            "SELECT * FROM applications WHERE status = %s ORDER BY id DESC", (status_filter,)
        )
    else:
        cur.execute("SELECT * FROM applications ORDER BY id DESC")
    apps = cur.fetchall()

    cur.execute("SELECT status, COUNT(*) AS n FROM applications GROUP BY status")
    counts_raw = cur.fetchall()
    counts = {row["status"]: row["n"] for row in counts_raw}
    counts["Alle"] = sum(counts.values())
    conn.close()

    return render_template(
        "dashboard.html", apps=apps, counts=counts, status_filter=status_filter
    )


@app.route("/bewerbung/<int:app_id>")
@login_required
def detail(app_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM applications WHERE id = %s", (app_id,))
    application = cur.fetchone()
    conn.close()
    if not application:
        flash("Bewerbung nicht gefunden.")
        return redirect(url_for("dashboard"))
    return render_template("detail.html", a=application)


@app.route("/bewerbung/<int:app_id>/status", methods=["POST"])
@login_required
def set_status(app_id):
    new_status = request.form.get("status")
    if new_status not in ("Angenommen", "Interview", "Abgelehnt", "Ausstehend"):
        flash("Ungültiger Status.")
        return redirect(url_for("detail", app_id=app_id))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE applications SET status = %s, reviewed_by = %s, reviewed_at = %s WHERE id = %s",
        (new_status, "Dashboard", datetime.utcnow(), app_id),
    )
    conn.commit()
    conn.close()
    flash(f"Status auf '{new_status}' gesetzt.")
    return redirect(url_for("detail", app_id=app_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
