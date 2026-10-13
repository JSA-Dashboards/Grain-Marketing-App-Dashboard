"""Sign-in emails. With no SMTP server configured, the link is printed to the log (for local testing)."""
import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger('grain.mail')


def send_login_link(to_email, name, link):
    body = (f"Hi {name or 'there'},\n\n"
            f"Here is your sign-in link for the Grain Marketing Desk:\n\n{link}\n\n"
            f"It works once and expires in {config.LOGIN_LINK_MINUTES} minutes. "
            "If you didn't ask to sign in, you can ignore this email.\n")
    if not config.SMTP_HOST:
        log.warning('SMTP_HOST not set; sign-in link for %s: %s', to_email, link)
        return
    msg = EmailMessage()
    msg['Subject'] = 'Your Grain Marketing Desk sign-in link'
    msg['From'] = config.SMTP_FROM
    msg['To'] = to_email
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
