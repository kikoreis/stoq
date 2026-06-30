# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2011 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##
""" Crash report logic """

import datetime
import hashlib
import logging
import sys
import time
import traceback
import os

from stoqlib.lib.component import get_utility

import stoq
from stoqlib.database.runtime import get_default_store
from stoqlib.lib.environment import is_developer_mode
from stoqlib.lib.interfaces import IAppInfo
from stoqlib.lib.osutils import get_product_key
from stoqlib.lib.osutils import get_system_locale
from stoqlib.lib.parameters import sysparam
from stoqlib.lib.pluginmanager import InstalledPlugin
from stoqlib.lib.uptime import get_uptime
from stoqlib.lib.webservice import get_main_cnpj

try:
    import sentry_sdk
    has_sentry = True
except ImportError:
    has_sentry = False

log = logging.getLogger(__name__)
_tracebacks = []

# Exceptions to ignore when sending reports to Sentry.
# (class_name, message) tuples for message-based filtering:
IGNORE_EXCEPTIONS = set([
    ('InternalError', 'current transaction is aborted, '
     'commands ignored until end of transaction block'),
])
# Exception classes to ignore:
IGNORE_EXCEPTION_CLASSES = set()


def before_send(event, hint):
    """sentry-sdk callback: drop events for ignored exceptions."""
    exc_info = hint.get('exc_info')
    if exc_info:
        exc_type, exc_value, _ = exc_info
        key = (exc_type.__name__, str(exc_value))
        if key in IGNORE_EXCEPTIONS:
            return None
        for ignore_cls in IGNORE_EXCEPTION_CLASSES:
            if isinstance(exc_value, ignore_cls):
                return None
    return event


def _get_revision(module):
    if not hasattr(module, 'library'):
        return ''

    if not hasattr(module.library, 'get_revision'):
        return ''

    revision = module.library.get_revision()
    if revision is None:
        return ''
    return revision


def _fix_version(version):
    if isinstance(version, (list, tuple)):
        version = '.'.join(map(str, version))
    return str(version)


def collect_report():
    report_ = {}

    # Date and uptime
    report_['date'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_['tz'] = time.tzname
    report_['uptime'] = get_uptime()
    report_['locale'] = get_system_locale()

    # Python and System
    import platform
    report_['architecture'] = ' '.join(platform.architecture())
    report_['python_version'] = _fix_version(sys.version_info)
    report_['uname'] = ' '.join(platform.uname())
    report_['system'] = platform.system()
    if hasattr(platform, 'dist'):
        report_['distribution'] = ' '.join(platform.dist())
    # Stoq application
    info = get_utility(IAppInfo, None)
    if info and info.get('name'):
        report_['app_name'] = info.get('name')
        report_['app_version'] = _fix_version(info.get('ver'))

    # External dependencies
    try:
        import gi
    except ImportError:
        pass
    else:
        report_['gtk_version'] = _fix_version(gi.version_info)

    import kiwi
    report_['kiwi_version'] = _fix_version(
        kiwi.__version__.version + (_get_revision(kiwi), ))

    import psycopg2
    try:
        parts = psycopg2.__version__.split(' ')
        extra = ' '.join(parts[1:])
        report_['psycopg_version'] = _fix_version(
            list(map(int, parts[0].split('.'))) + [extra])
    except Exception:
        report_['psycopg_version'] = _fix_version(psycopg2.__version__)

    import reportlab
    report_['reportlab_version'] = _fix_version(reportlab.Version)

    import stoqdrivers
    report_['stoqdrivers_version'] = _fix_version(
        stoqdrivers.__version__ + (_get_revision(stoqdrivers), ))

    report_['product_key'] = get_product_key()

    try:
        from stoqlib.lib.kiwilibrary import library
        report_['bdist_type'] = library.bdist_type
    except Exception:
        pass

    # PostgreSQL database server
    try:
        from stoqlib.database.settings import get_database_version
        default_store = get_default_store()
        report_['postgresql_version'] = _fix_version(
            get_database_version(default_store))
        report_['demo'] = sysparam.get_bool('DEMO_MODE')
        report_['hash'] = sysparam.get_string('USER_HASH')
        report_['cnpj'] = get_main_cnpj(default_store)
        report_['plugins'] = ', '.join(
            InstalledPlugin.get_plugin_names(default_store))
    except Exception:
        pass

    # Tracebacks
    report_['tracebacks'] = {}
    for i, trace in enumerate(_tracebacks):
        t = ''.join(traceback.format_exception(*trace))
        # Eliminate duplicates:
        md5sum = hashlib.md5(t.encode()).hexdigest()
        report_['tracebacks'][md5sum] = t

    if info and info.get('log'):
        report_['log'] = open(info.get('log')).read()
        report_['log_name'] = info.get('log')

    return report_


def collect_traceback(tb, output=True, submit=False):
    """Collects traceback which might be submitted
    @output: if it is to be printed
    @submit: if it is to be submitted immediately
    """
    _tracebacks.append(tb)
    if output:
        traceback.print_exception(*tb)

    if has_sentry and not is_developer_mode():  # pragma no cover
        extra = collect_report()
        extra.pop('tracebacks', None)

        sentry_url = os.environ.get('STOQ_SENTRY_URL', '')
        if not sentry_url:
            return

        sentry_sdk.init(sentry_url,
                        before_send=before_send,
                        release=stoq.version)
        sentry_sdk.set_user({
            'id': extra.get('hash', None),
            'username': extra.get('cnpj', None)})

        # Don't send logs to sentry
        extra.pop('log', None)
        extra.pop('log_name', None)

        tags = {}
        tag_names = [
            'architecture', 'cnpj', 'system', 'app_name',
            'bdist_type', 'app_version', 'distribution',
            'python_version', 'psycopg_version', 'pygtk_version',
            'gtk_version', 'kiwi_version', 'reportlab_version',
            'stoqdrivers_version', 'postgresql_version']
        for name in tag_names:
            value = extra.pop(name, None)
            if value is not None:
                tags[name] = value

        sentry_sdk.set_tags(tags)
        sentry_sdk.set_context('extra', extra)
        sentry_sdk.capture_exception(tb)


def has_tracebacks():
    return bool(_tracebacks)
