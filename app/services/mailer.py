import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailError(Exception):
    """Raised when Brevo email delivery fails."""
    pass


async def send_otp_email(to_email: str, code: str, purpose: str = "email_verification") -> None:
    """
    Send OTP verification email via Brevo v3 transactional email API.
    
    Security:
      - Never logs the OTP code or the API key.
      - Uses a 10-second request timeout.
      - Raises BrevoEmailError on any HTTP or transport failure.
    """
    # Allow mock dispatch in testing or local simulation
    if settings.TESTING or settings.BREVO_API_KEY.lower() in ("mock", "test", ""):
        logger.info("Email dispatch to %s for purpose %s (mock mode: no external request)", to_email, purpose)
        return

    purpose_label = "Email Verification" if purpose == "email_verification" else "Password Reset"
    subject = "Your AutoInvoice verification code"

    text_body = (
        f"AutoInvoice Ledger - {purpose_label}\n\n"
        f"Your one-time verification code is:\n\n"
        f"    {code}\n\n"
        f"This code will expire in 10 minutes.\n"
        f"If you did not request this verification, please disregard this transmission.\n\n"
        f"- AutoInvoice Verification Ledger"
    )

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>AutoInvoice Verification Code</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #F6F1E7; color: #1C1710; margin: 0; padding: 24px; }}
        .ledger-box {{ max-width: 480px; margin: 0 auto; background: #FDFBF5; border: 1px solid #E2D9C4; padding: 32px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        .header {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: #6E6455; border-bottom: 1px solid #E2D9C4; padding-bottom: 8px; margin-bottom: 20px; }}
        .title {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; color: #1C1710; }}
        .code-container {{ background: #ECE6D8; border: 1px dashed #6E6455; padding: 16px; text-align: center; margin: 24px 0; border-radius: 4px; }}
        .otp-code {{ font-family: 'Courier New', Courier, monospace; font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #1C1710; }}
        .footer {{ font-size: 12px; color: #6E6455; margin-top: 24px; border-top: 1px solid #E2D9C4; padding-top: 12px; }}
      </style>
    </head>
    <body>
      <div class="ledger-box">
        <div class="header">AutoInvoice Ledger &bull; Authentication Notice</div>
        <div class="title">{purpose_label}</div>
        <p>Use the following 6-digit authorization code to complete your verification:</p>
        <div class="code-container">
          <div class="otp-code">{code}</div>
        </div>
        <p style="font-size: 13px; color: #6E6455;">This authorization expires in <strong>10 minutes</strong>. Do not share this code with anyone.</p>
        <div class="footer">If you did not initiate this request, no action is needed.</div>
      </div>
    </body>
    </html>
    """

    payload = {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_body,
        "htmlContent": html_body,
    }

    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(BREVO_API_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.error(
                    "Brevo API error for recipient %s: status_code=%s response=%s",
                    to_email,
                    response.status_code,
                    response.text,
                )
                raise BrevoEmailError("Could not send email, use resend")
            logger.info("Successfully dispatched OTP email to %s via Brevo", to_email)
    except httpx.RequestError as exc:
        logger.error("Network error while connecting to Brevo for recipient %s: %s", to_email, exc)
        raise BrevoEmailError("Could not send email, use resend") from exc
