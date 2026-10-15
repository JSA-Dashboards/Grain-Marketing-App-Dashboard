"""Settings, read from environment variables (see hosted/.env.example)."""
import os


def _env(name, default=''):
    return os.environ.get(name, default).strip()


DATA_DIR = _env('DATA_DIR', '/data')
DB_PATH = os.path.join(DATA_DIR, 'grain.db')
ASSET_DIR = os.path.join(DATA_DIR, 'assets')
APP_HTML = _env('APP_HTML', '/app/web/grain-desk.html')   # the same page the Claude version runs

PUBLIC_URL = _env('PUBLIC_URL', 'http://localhost:8000').rstrip('/')  # used in sign-in emails
COOKIE_SECURE = PUBLIC_URL.startswith('https://')
SESSION_DAYS = int(_env('SESSION_DAYS', '30'))
LOGIN_LINK_MINUTES = int(_env('LOGIN_LINK_MINUTES', '20'))
MAX_DOC_BYTES = 256 * 1024
MAX_ASSET_BYTES = 20 * 1024 * 1024

# sign-in emails; with no SMTP_HOST the link is printed to the server log (local testing)
SMTP_HOST = _env('SMTP_HOST')
SMTP_PORT = int(_env('SMTP_PORT', '587'))
SMTP_USER = _env('SMTP_USER')
SMTP_PASSWORD = _env('SMTP_PASSWORD')
SMTP_FROM = _env('SMTP_FROM', 'Grain Marketing Desk <no-reply@localhost>')

# contract scanning and Intel drafts (Claude API, billed to JSA's key)
ANTHROPIC_API_KEY = _env('ANTHROPIC_API_KEY')
CLAUDE_MODEL = _env('CLAUDE_MODEL', 'claude-opus-5-5')

# nightly jobs (read-only Snowflake service user with a key pair)
SNOWFLAKE_ACCOUNT = _env('SNOWFLAKE_ACCOUNT')
SNOWFLAKE_USER = _env('SNOWFLAKE_USER')
SNOWFLAKE_PRIVATE_KEY_PATH = _env('SNOWFLAKE_PRIVATE_KEY_PATH', '/secrets/snowflake_key.p8')
SNOWFLAKE_ROLE = _env('SNOWFLAKE_ROLE')
SNOWFLAKE_WAREHOUSE = _env('SNOWFLAKE_WAREHOUSE', 'COMPUTE_WH')
TIMEZONE = _env('TIMEZONE', 'America/Chicago')

# storage: 'sqlite' (the Docker version, DB_PATH) or 'snowflake' (the Streamlit version, JSA.GRAIN_DESK)
STORE_BACKEND = _env('STORE_BACKEND', 'sqlite').lower()
SNOWFLAKE_PRIVATE_KEY = os.environ.get('SNOWFLAKE_PRIVATE_KEY', '')     # PEM text (Streamlit secrets); or use the path above
SNOWFLAKE_DATABASE = _env('GRAIN_DESK_DATABASE', 'JSA')
SNOWFLAKE_SCHEMA = _env('GRAIN_DESK_SCHEMA', 'GRAIN_DESK')
MAX_ASSET_BYTES_SF = 7 * 1024 * 1024                                    # a Snowflake BINARY holds up to 8 MB
LOGIN_CODE_MINUTES = int(_env('LOGIN_CODE_MINUTES', '10'))
