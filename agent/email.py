"""Send the weekend guide via email (SMTP, HTML with embedded photo)."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Optional

import requests

from dotenv import load_dotenv

load_dotenv()

# Header photo URL (Unsplash, free to use) — downloaded and embedded in email
HEADER_PHOTO_URL = (
    "https://images.unsplash.com/photo-1544967082-d9d25d867d66?"
    "w=1200&q=80"
)


class EmailSender:
    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "")
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASS", "")
        self.from_addr = os.getenv("EMAIL_FROM", "")
        self.to_addr = os.getenv("EMAIL_TO", "")

    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.from_addr and self.to_addr)

    def send(self, subject: str, html_body: str, text_body: Optional[str] = None) -> Optional[str]:
        """Send the email as HTML with embedded photo and plain-text fallback."""
        if not self.is_configured():
            print("[email] SMTP not configured — printing message instead:\n")
            print(text_body or html_body)
            print("\n--- end of message ---\n")
            return None

        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg["Subject"] = subject

        # Plain text fallback
        msg.set_content(text_body or "See HTML version.")

        # HTML alternative
        msg.add_alternative(html_body, subtype="html")

        # Embed header photo as attachment with Content-ID
        try:
            r = requests.get(HEADER_PHOTO_URL, timeout=20)
            r.raise_for_status()
            # Attach image with Content-ID so HTML can reference it as cid:header-photo
            msg.get_payload()[1].add_related(
                r.content,
                maintype="image",
                subtype="jpeg",
                cid="header-photo",
            )
        except Exception as exc:
            print(f"[email] could not embed photo: {exc}")

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.user, self.password)
            server.send_message(msg)

        print(f"[email] sent to {self.to_addr}")
        return "sent"