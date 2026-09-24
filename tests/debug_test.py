import atexit
import io
import json
import logging
import schwab
import unittest

from schwab.client import Client
from .utils import MockResponse, no_duplicates
from unittest.mock import Mock, patch


class RedactorTest(unittest.TestCase):

    def setUp(self):
        self.redactor = schwab.debug.LogRedactor()

    @no_duplicates
    def test_no_redactions(self):
        self.assertEqual('test message', self.redactor.redact('test message'))

    @no_duplicates
    def test_simple_redaction(self):
        self.redactor.register('secret', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET> message',
            self.redactor.redact('secret message'))

    @no_duplicates
    def test_multiple_registrations_same_string(self):
        self.redactor.register('secret', 'SECRET')
        self.redactor.register('secret', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET> message',
            self.redactor.redact('secret message'))

    @no_duplicates
    def test_multiple_registrations_same_string_different_label(self):
        self.redactor.register('secret-A', 'SECRET')
        self.redactor.register('secret-B', 'SECRET')

        self.assertEqual(
            '<REDACTED SECRET-1> message <REDACTED SECRET-2>',
            self.redactor.redact('secret-A message secret-B'))


class RegisterRedactionsTest(unittest.TestCase):

    def setUp(self):
        self.captured = io.StringIO()
        self.logger = logging.getLogger('test')
        self.dump_logs = schwab.debug._enable_bug_report_logging(
            output=self.captured, loggers=[self.logger])
        self.addCleanup(atexit.unregister, self.dump_logs)
        self.addCleanup(self.logger.handlers.clear)
        self.addCleanup(setattr, schwab.debug,
                        '_collect_response_redactions', False)
        schwab.LOG_REDACTOR = schwab.debug.LogRedactor()

    @no_duplicates
    def test_empty_string(self):
        schwab.debug.register_redactions('')

    @no_duplicates
    def test_empty_dict(self):
        schwab.debug.register_redactions({})

    @no_duplicates
    def test_empty_list(self):
        schwab.debug.register_redactions([])

    @no_duplicates
    def test_dict(self):
        schwab.debug.register_redactions(
            {'BadNumber': '100001'},
            bad_patterns=['bad'])
        schwab.debug.register_redactions(
            {'OtherBadNumber': '200002'},
            bad_patterns=['bad'])

        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Bad Number: <REDACTED BadNumber>\n' +
            r'\[.*\] Other Bad Number: <REDACTED OtherBadNumber>\n')

    @no_duplicates
    def test_list_of_dict(self):
        schwab.debug.register_redactions(
            [{'GoodNumber': '900009'},
             {'BadNumber': '100001'},
             {'OtherBadNumber': '200002'}],
            bad_patterns=['bad'])

        self.logger.info('Good Number: 900009')
        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Good Number: 900009\n' +
            r'\[.*\] Bad Number: <REDACTED 1-BadNumber>\n' +
            r'\[.*\] Other Bad Number: <REDACTED 2-OtherBadNumber>\n')

    @no_duplicates
    def test_whitelist(self):
        schwab.debug.register_redactions(
            [{'GoodNumber': '900009'},
             {'BadNumber': '100001'},
             {'OtherBadNumber': '200002'}],
            bad_patterns=['bad'],
            whitelisted=['otherbadnumber'])

        self.logger.info('Good Number: 900009')
        self.logger.info('Bad Number: 100001')
        self.logger.info('Other Bad Number: 200002')

        self.dump_logs()
        self.assertRegex(
            self.captured.getvalue(),
            r'\[.*\] Good Number: 900009\n' +
            r'\[.*\] Bad Number: <REDACTED 1-BadNumber>\n' +
            r'\[.*\] Other Bad Number: 200002\n')

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_from_request_success(self, register_redactions):
        resp = MockResponse({'success': 1}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_called_with({'success': 1})

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_from_request_not_okay(self, register_redactions):
        resp = MockResponse({'success': 1}, 403)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_not_called()

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_register_unparseable_json(self, register_redactions):
        class MR(MockResponse):
            def json(self):
                raise json.decoder.JSONDecodeError('e243rschwabgew', '', 0)

        resp = MR({'success': 1}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_not_called()

class ResponseRedactionGatingTest(unittest.TestCase):

    def setUp(self):
        self.addCleanup(setattr, schwab.debug,
                        '_collect_response_redactions', False)

    @no_duplicates
    def test_clients_use_real_redaction_function(self):
        # Guards against a no-op stub shadowing the real function again. See
        # upstream issue #246.
        import schwab.client.synchronous
        import schwab.client.asynchronous
        self.assertIs(schwab.client.synchronous.register_redactions_from_response,
                      schwab.debug.register_redactions_from_response)
        self.assertIs(schwab.client.asynchronous.register_redactions_from_response,
                      schwab.debug.register_redactions_from_response)

    @no_duplicates
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_not_collected_unless_bug_report_logging_enabled(
            self, register_redactions):
        schwab.debug._collect_response_redactions = False
        resp = MockResponse({'accountNumber': '123'}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_not_called()

    @no_duplicates
    @patch('atexit.register')
    @patch('schwab.debug.register_redactions', new_callable=Mock)
    def test_collected_once_bug_report_logging_enabled(
            self, register_redactions, _):
        schwab.debug._enable_bug_report_logging(
                output=io.StringIO(), loggers=[])
        resp = MockResponse({'accountNumber': '123'}, 200)
        schwab.debug.register_redactions_from_response(resp)
        register_redactions.assert_called_once_with({'accountNumber': '123'})

    @no_duplicates
    @patch('atexit.register')
    def test_account_number_redacted_from_client_response_logs(self, _):
        schwab.LOG_REDACTOR = schwab.debug.LogRedactor()
        captured = io.StringIO()
        logger = logging.getLogger('schwab.client.base')
        self.addCleanup(logger.handlers.clear)
        dump_logs = schwab.debug._enable_bug_report_logging(
                output=captured, loggers=[logger])

        session = Mock()
        session.get.return_value = MockResponse(
                [{'accountNumber': '98765432', 'hashValue': 'ABCHASH'}], 200)
        client = Client('API_KEY', session)
        client.get_account_numbers()
        logger.debug('account is 98765432')

        dump_logs()
        self.assertNotIn('98765432', captured.getvalue())


class EnableDebugLoggingTest(unittest.TestCase):

    @patch('atexit.register')
    @patch('logging.Logger.addHandler')
    def test_enable_doesnt_throw_exceptions(self, _, __):
        self.addCleanup(setattr, schwab.debug,
                        '_collect_response_redactions', False)
        try:
            schwab.debug.enable_bug_report_logging()
        except AttributeError:
            self.fail("debug.enable_bug_report_logging() raised AttributeError unexpectedly")
