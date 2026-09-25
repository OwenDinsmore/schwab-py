from __future__ import annotations

import atexit
from schwab._http import httpx
import json
import logging
import sys
import schwab

from collections.abc import Callable, Container, Iterable
from typing import Any, TextIO


def get_logger() -> logging.Logger:
    return logging.getLogger(__name__)


# Collecting redactions from responses costs a JSON parse and tree walk per
# request, and the collected values are kept for the life of the process, so it
# is only done once bug report logging, the only consumer, has been enabled.
_collect_response_redactions = False


class LogRedactor:
    '''
    Collects strings that should not be emitted and replaces them with safe
    placeholders.
    '''

    def __init__(self) -> None:
        from collections import defaultdict

        self.redacted_strings: dict[str, Any] = {}
        self.label_counts: defaultdict[str, int] = defaultdict(int)

    def register(self, string: Any, label: str) -> None:
        '''
        Registers a string that should not be emitted and the label with with
        which it should be replaced.
        '''
        string = str(string)
        if string not in self.redacted_strings:
            self.label_counts[label] += 1
            self.redacted_strings[string] = (label, self.label_counts[label])

    def redact(self, msg: str) -> str:
        '''
        Scans the string for secret strings and returns a sanitized version with
        the secrets replaced with placeholders.
        '''
        for string, label in self.redacted_strings.items():
            label, count = label
            msg = msg.replace(string, '<REDACTED {}{}>'.format(
                label, '-{}'.format(count) if
                self.label_counts[label] > 1 else ''))
        return msg


def register_redactions_from_response(resp: Any) -> None:
    '''
    Convenience method that calls ``register_redactions`` if resp represents a
    successful response and bug report logging is enabled. Note this method
    assumes that resp has a JSON contents.
    '''
    if not _collect_response_redactions:
        return
    if resp.status_code == httpx.codes.OK:
        try:
            register_redactions(resp.json())
        except json.decoder.JSONDecodeError:
            pass


def register_redactions(obj: Any, key_path: list[str] | None = None,
                        bad_patterns: Iterable[str] = [
                            'auth', 'acl', 'displayname', 'id', 'key', 'token',
                            'accountnumber', 'hashvalue', 'accounthash'],
                        whitelisted: Container[str] = set([
                            'requestid',
                            'token_type',
                            'legid',
                            'bidid',
                            'askid',
                            'lastid',
                            'bidsizeinlong',
                            'bidsizeindouble',
                            'bidpriceindouble'])) -> None:
    '''
    Recursively iterates through the leaf elements of ``obj`` and registers
    elements with keys matching a blacklist with the global ``Redactor``.
    '''
    if key_path is None:
        key_path = []

    if isinstance(obj, list):
        for idx, value in enumerate(obj):
            key_path.append(str(idx))
            register_redactions(value, key_path, bad_patterns, whitelisted)
            key_path.pop()
    elif isinstance(obj, dict):
        for key, value in obj.items():
            key_path.append(key)
            register_redactions(value, key_path, bad_patterns, whitelisted)
            key_path.pop()
    else:
        if key_path:
            last_key = key_path[-1].lower()
            if last_key in whitelisted:
                return
            elif any(bad in last_key for bad in bad_patterns):
                schwab.LOG_REDACTOR.register(obj, '-'.join(key_path))


def enable_bug_report_logging() -> None:
    '''
    Turns on bug report logging. Will collect all logged output, redact out
    anything that should be kept secret, and emit the result at program exit.

    Notes:
     * This method does a best effort redaction. Never share its output
       without verifying that all secret information is properly redacted.
     * Because this function records all logged output, it has a performance
       penalty. It should not be called in production code.
    '''
    _enable_bug_report_logging()


def _enable_bug_report_logging(
        output: TextIO | None = None,
        loggers: Iterable[logging.Logger] | None = None
) -> Callable[[], None]:
    '''
    Module-internal version of :func:`enable_bug_report_logging`, intended for
    use in tests.
    '''
    global _collect_response_redactions
    _collect_response_redactions = True

    if output is None:
        output = sys.stderr

    if loggers is None:
        loggers = (
            schwab.auth.get_logger(),
            schwab.client.base.get_logger(),
            schwab.streaming.get_logger(),
            get_logger())

    class RecordingHandler(logging.Handler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(self.format(record))

    handler = RecordingHandler()
    handler.setFormatter(logging.Formatter(
        '[%(filename)s:%(lineno)s:%(funcName)s] %(message)s'))

    for logger in loggers:
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

    def write_logs() -> None:
        print(file=output)
        print(' ### BEGIN REDACTED LOGS ###', file=output)
        print(file=output)

        for msg in handler.messages:
            msg = schwab.LOG_REDACTOR.redact(msg)
            print(msg, file=output)
    atexit.register(write_logs)

    get_logger().debug('schwab-api version %s', schwab.__version__)

    return write_logs
