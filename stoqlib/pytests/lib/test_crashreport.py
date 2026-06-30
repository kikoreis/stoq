import sys
from unittest import mock

import pytest

from stoqlib.lib.crashreport import (
    collect_traceback,
    before_send,
    IGNORE_EXCEPTIONS,
    IGNORE_EXCEPTION_CLASSES,
)


class CrashTestException(AssertionError):
    pass


def _make_hint(exc_type, exc_value):
    return {'exc_info': (exc_type, exc_value, None)}


def _make_event():
    return {'exception': {'values': []}}


def test_before_send_ignore_by_class():
    exc = CrashTestException('test')
    hint = _make_hint(CrashTestException, exc)
    event = _make_event()
    IGNORE_EXCEPTION_CLASSES.add(CrashTestException)
    try:
        assert before_send(event, hint) is None
    finally:
        IGNORE_EXCEPTION_CLASSES.discard(CrashTestException)


def test_before_send_ignore_by_name_and_message():
    text = 'This is a test'
    exc = AssertionError(text)
    key = ('AssertionError', text)
    hint = _make_hint(AssertionError, exc)
    event = _make_event()
    IGNORE_EXCEPTIONS.add(key)
    try:
        assert before_send(event, hint) is None
    finally:
        IGNORE_EXCEPTIONS.discard(key)


def test_before_send_dont_ignore():
    exc = CrashTestException('test')
    hint = _make_hint(CrashTestException, exc)
    event = _make_event()
    assert before_send(event, hint) is not None


@mock.patch('stoqlib.lib.crashreport.is_developer_mode')
def test_collect_traceback(is_developer_mode_mock):
    is_developer_mode_mock.return_value = False
    try:
        raise ValueError()
    except ValueError:
        tb = sys.exc_info()
    collect_traceback(tb)
