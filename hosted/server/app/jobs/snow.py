"""Snowflake connection for the nightly jobs: a read-only service user signing in with a key pair."""
import snowflake.connector

from .. import config


def query(sql):
    """Run one query and return rows as dicts (column names upper case, as Snowflake returns them)."""
    if not (config.SNOWFLAKE_ACCOUNT and config.SNOWFLAKE_USER):
        raise RuntimeError('SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER are not set')
    with snowflake.connector.connect(
        account=config.SNOWFLAKE_ACCOUNT,
        user=config.SNOWFLAKE_USER,
        authenticator='SNOWFLAKE_JWT',
        private_key_file=config.SNOWFLAKE_PRIVATE_KEY_PATH,
        role=config.SNOWFLAKE_ROLE or None,
        warehouse=config.SNOWFLAKE_WAREHOUSE or None,
        login_timeout=60,
    ) as c, c.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()
