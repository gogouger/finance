import os
import smtplib
from email.message import EmailMessage
from uuid import uuid4


class FakeMailProvider:
    mode = "fake"

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, *, recipient: str, subject: str, body: str) -> str:
        message_id = f"fake-{uuid4()}"
        self.sent.append(
            {
                "id": message_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
            }
        )
        return message_id


class SMTPMailProvider:
    mode = "smtp"

    def __init__(self):
        self.host = os.environ["FINANCE_SMTP_HOST"]
        self.port = int(os.environ.get("FINANCE_SMTP_PORT", "587"))
        self.username = os.environ.get("FINANCE_SMTP_USERNAME")
        self.password = os.environ.get("FINANCE_SMTP_PASSWORD")
        self.sender = os.environ["FINANCE_MAIL_FROM"]

    def send(self, *, recipient: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
        return message["Message-ID"] or f"smtp-{uuid4()}"


def create_mail_provider():
    mode = os.environ.get("MAIL_MODE", "disabled").lower()
    if mode == "fake":
        return FakeMailProvider()
    if mode == "smtp":
        return SMTPMailProvider()
    return None
