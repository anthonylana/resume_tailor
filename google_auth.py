"""Google OAuth helper. Run `python google_auth.py` once to log in."""
import os

# Some software sets SSLKEYLOGFILE to an unwritable path, which breaks all HTTPS calls.
os.environ.pop("SSLKEYLOGFILE", None)

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

# documents.readonly -> can READ any of your docs, can NEVER edit them
# drive.file         -> can only create/edit files THIS app creates
SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def _has_all_scopes(creds: Credentials) -> bool:
    granted = set(creds.scopes or [])
    return set(SCOPES).issubset(granted)


def get_credentials(interactive: bool = True) -> Credentials:
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        # Token created with older/fewer scopes -> force a new login
        if not _has_all_scopes(creds):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interactive:
            raise RuntimeError(
                "No valid Google token (or missing scopes). "
                "Run `python google_auth.py` first."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)

    TOKEN_FILE.write_text(creds.to_json())
    return creds


if __name__ == "__main__":
    get_credentials(interactive=True)
    print(f"Google authentication OK. Token saved to {TOKEN_FILE}")