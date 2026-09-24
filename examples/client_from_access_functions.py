'''
Example of storing the token somewhere other than a file, using
schwab.auth.client_from_access_functions.

This example stores the token in a SQLite database, but the same pattern works
for any storage you can read and write a string to, such as AWS Secrets
Manager, S3, Redis, or a database table shared between machines.

Before running this, create a token with easy_client() or the
schwab-generate-token.py script, then copy it into the database with:

    python client_from_access_functions.py import /path/to/token.json
'''

import json
import os
import sqlite3
import sys

from schwab.auth import client_from_access_functions


API_KEY = os.environ['SCHWAB_API_KEY']
APP_SECRET = os.environ['SCHWAB_APP_SECRET']
DB_PATH = 'tokens.db'


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'CREATE TABLE IF NOT EXISTS tokens (name TEXT PRIMARY KEY, token TEXT)')
    return conn


def read_token():
    '''
    Takes no arguments and returns the token object exactly as it was last
    passed to write_token(). Treat the object as opaque: serialize it with json
    rather than picking out fields.
    '''
    with connect() as conn:
        row = conn.execute(
            'SELECT token FROM tokens WHERE name = ?', ('schwab',)).fetchone()
    if row is None:
        raise RuntimeError(
            'No token stored. Import one first, see the top of this file.')
    return json.loads(row[0])


def write_token(token, *args, **kwargs):
    '''
    Called with the token object whenever the token is created or refreshed.
    Must accept and ignore extra positional and keyword arguments, which the
    underlying OAuth library passes along.
    '''
    with connect() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO tokens (name, token) VALUES (?, ?)',
            ('schwab', json.dumps(token)))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'import':
        with open(sys.argv[2], 'r') as f:
            write_token(json.load(f))
        print('Imported token into', DB_PATH)
        return

    client = client_from_access_functions(
        API_KEY, APP_SECRET, read_token, write_token)

    resp = client.get_account_numbers()
    resp.raise_for_status()
    print('Token works, found {} account(s)'.format(len(resp.json())))


if __name__ == '__main__':
    main()
