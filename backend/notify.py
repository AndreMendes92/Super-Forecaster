"""
notify.py — sends alert emails via free Gmail SMTP.
------------------------------------------------------
Needs two environment variables set on the backend (Render → Settings
→ Environment — see README.md for the full setup):

  GMAIL_ADDRESS       the Gmail address to send FROM
  GMAIL_APP_PASSWORD  a 16-character "app password" generated in your
                       Google Account (free, no credit card) — NOT
                       your normal Gmail password. See README.md.

If either is missing, send_alert_email() raises RuntimeError so the
caller can report a clear setup error instead of silently doing
nothing.
"""

import os
import smtplib
from email.mime.text import MIMEText

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465


def send_alert_email(to_email: str, subject: str, body: str) -> None:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set on the backend "
            "— alerts can't be emailed until they are. See README.md."
        )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to_email

    with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as server:
        server.login(address, app_password)
        server.sendmail(address, [to_email], msg.as_string())


def _fmt(value: float, unit: str) -> str:
    return f"{value:,.1f} index points" if unit == "index" else f"${value:,.0f}"


def build_alert_message(watch, current_value: float) -> tuple[str, str]:
    """Builds (subject, body) for a triggered watch."""
    unit = getattr(watch, "value_unit", "cad") or "cad"
    direction_word = "dropped below" if watch.direction == "below" else "risen above"
    subject = f"🏠 Price alert: {watch.label or watch.geography} has {direction_word} your target"
    index_note = (
        "\nNote: StatCan's New Housing Price Index tracks NEW-build "
        "prices, not resale/MLS averages — treat it as a market-direction "
        "signal, not an exact dollar figure.\n"
        if unit == "index" else ""
    )
    body = (
        f"Your saved housing price alert was triggered.\n\n"
        f"Location: {watch.geography}\n"
        f"Type: {watch.property_type or 'All types'}\n"
        f"Data source: {watch.data_source}\n"
        f"Your target: {watch.direction} {_fmt(watch.target_price, unit)}\n"
        f"Current value: {_fmt(current_value, unit)}\n"
        f"{index_note}\n"
        f"— Sent automatically by your Housing Price Tracker."
    )
    return subject, body
