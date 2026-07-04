# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005-2011 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##
##
"""Check Stoq dependencies.

Version requirements for pip packages are read from pyproject.toml
[project.dependencies], keeping a single source of truth. Non-Python
checks (GTK typelibs, psql binary, pyobjc) keep their own constants.
"""

import importlib
import importlib.metadata
import os
import platform
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from stoqlib.lib.translation import stoqlib_gettext as _

# Non-pip checks: not in pyproject.toml, keep hardcoded
GTK_REQUIRED = (3, 24)
PYCAIRO_REQUIRED = (1, 27, 0)
PYGOBJECTWEBKIT_REQUIRED = (4, 1)
PYOBJC_REQUIRED = (2, 3)
PSQL_REQUIRED = (18, 4)
PYSERIAL_REQUIRED = (3, 5)

# Dist name -> import name (where they differ)
_DIST_TO_IMPORT = {
    'kiwi-gtk': 'kiwi',
    'pillow': 'PIL',
    'psycopg2-binary': 'psycopg2',
    'pyjwt': 'jwt',
    'python-dateutil': 'dateutil',
}

# Version extractors for source-tree packages without dist metadata
_VERSION_EXTRACTORS = {
    'kiwi': lambda m: '.'.join(map(str, m.__version__.version)),
    'storm': lambda m: '.'.join(map(str, m.version_info)),
    'stoqdrivers': lambda m: '.'.join(map(str, m.__version__)),
    'psycopg2': lambda m: m.__version__.split(' ', 1)[0],
    'reportlab': lambda m: m.Version,
    'weasyprint': lambda m: str(m.VERSION),
    'xlwt': lambda m: m.__VERSION__,
    'PIL': lambda m: m.__version__,
    'dateutil': lambda m: m.__version__,
    'mako': lambda m: m.__version__,
}


def _load_pyproject_specifiers():
    """Parse pyproject.toml dependencies into {dist_name: SpecifierSet}."""
    pyproject = Path(__file__).resolve().parents[2] / 'pyproject.toml'
    if not pyproject.exists():
        return {}
    with open(pyproject, 'rb') as f:
        data = tomllib.load(f)
    specs = {}
    for dep in data.get('project', {}).get('dependencies', []):
        try:
            req = Requirement(dep)
        except Exception:
            continue
        specs[req.name.lower()] = req.specifier
    return specs


_PYPROJECT_SPECS = _load_pyproject_specifiers()


def _tuple2str(tpl):
    if isinstance(tpl, str):
        return tpl
    return '.'.join(map(str, tpl))


class DependencyChecker(object):
    def __init__(self):
        self.text_mode = False

    def check_kiwi(self, version=None):
        """Check kiwi. version arg kept for setup_old.py compat."""
        self._check_kiwi()

    def check(self):
        # Core (dependencies.py itself imports packaging at load time)
        self._check_packaging()

        # GUI prerequisites (so we can show error dialogs)
        self._check_gtk(GTK_REQUIRED)
        self._check_kiwi()
        self._check_pycairo(PYCAIRO_REQUIRED)
        self._check_pygobjectwebkit(PYGOBJECTWEBKIT_REQUIRED)
        if platform.system() == 'Darwin':
            self._check_pyobjc(PYOBJC_REQUIRED)
        self._check_zope_interface()
        self._check_dateutil()
        self._check_xlwt()
        self._check_pyjwt()

        # Database
        self._check_psql(PSQL_REQUIRED)
        self._check_psycopg()
        self._check_storm()
        self._check_sqlparse()

        # Printing
        self._check_pil()
        self._check_reportlab()
        self._check_mako()
        if platform.system() not in ['Darwin', 'Windows']:
            self._check_weasyprint()
            self._check_poppler()

        # ECF
        self._check_pyserial(PYSERIAL_REQUIRED)
        self._check_stoqdrivers()

        # NFE / crypto
        self._check_cryptography()
        self._check_pykcs11()

    # --- error reporting ---

    def _error(self, title, msg, details=None):
        if self.text_mode:
            msg = msg.replace('<b>', '').replace('</b>', '')
            raise SystemExit("ERROR: %s\n\n%s" % (title, msg))

        from gi.repository import Gtk
        dialog = Gtk.MessageDialog(parent=None, flags=0,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.OK,
                                   text=title)
        dialog.set_markup(msg)
        if details:
            dialog.format_secondary_markup(details)
        dialog.run()
        raise SystemExit

    def _missing(self, project, url=None, version=None, details=None):
        msg = _("<b>%s</b> could not be found on your system.\n"
                "%s %s or higher is required for Stoq to run.\n\n"
                "You can find a recent version of %s on it's homepage at\n%s") % (
            project, project, _tuple2str(version),
            project, url)
        self._error(_("Missing dependency"), msg, details=details)

    def _too_old(self, project, url=None, required=None, found=None):
        msg = _("<b>%s</b> was found on your system, but it is\n"
                "too old for Stoq to be able to run. %s %s was found, "
                "but %s is required.\n\n"
                "You can find a recent version of %s on it's homepage at\n%s") % (
            project, project, found, _tuple2str(required),
            project, url)
        self._error(_("Out-dated dependency"), msg)

    def _incompatible(self, project, url=None, required=None, found=None):
        msg = _("<b>%s</b> was found on your system, but its version,\n"
                "%s incompatible with Stoq, you need to downgrade to %s "
                "for Stoq to work.\n\n"
                "You can find an older version of %s on it's homepage at\n%s") % (
            project, found, _tuple2str(required),
            project, url)
        self._error(_("Incompatible dependency"), msg)

    # --- version helpers ---

    def _get_spec(self, dist_name):
        return _PYPROJECT_SPECS.get(dist_name.lower())

    def _get_version(self, dist_name, import_name=None):
        """Get installed version. Tries importlib.metadata, then
        falls back to reading the module's version attribute."""
        if import_name is None:
            import_name = _DIST_TO_IMPORT.get(dist_name, dist_name)
        for name in (dist_name, import_name):
            try:
                return importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
        try:
            mod = importlib.import_module(import_name)
        except ImportError:
            return None
        extractor = _VERSION_EXTRACTORS.get(import_name)
        if extractor:
            return extractor(mod)
        for attr in ('__version__', 'VERSION', 'Version', 'version'):
            v = getattr(mod, attr, None)
            if isinstance(v, str):
                return v
            if hasattr(v, 'version'):
                return '.'.join(map(str, v.version))
            if isinstance(v, tuple):
                return '.'.join(map(str, v))
        return None

    def _version_too_old(self, dist_name, found):
        """Check if found version satisfies the pyproject specifier."""
        spec = self._get_spec(dist_name)
        if spec is None:
            return False
        try:
            v = Version(str(found))
        except InvalidVersion:
            clean = str(found).split(' ', 1)[0].split('+', 1)[0]
            try:
                v = Version(clean)
            except InvalidVersion:
                return False
        return not spec.contains(v)

    def _required_str(self, dist_name):
        """Human-readable required version string from pyproject."""
        spec = self._get_spec(dist_name)
        return str(spec) if spec else "unknown"

    def _check_pip_package(self, dist_name, project, url,
                           import_name=None, details=None):
        """Generic check for a pip package listed in pyproject.toml."""
        if import_name is None:
            import_name = _DIST_TO_IMPORT.get(dist_name, dist_name)
        try:
            importlib.import_module(import_name)
        except ImportError as e:
            self._missing(project=project, url=url,
                          version=self._required_str(dist_name),
                          details=details or str(e) if details else None)
            return
        found = self._get_version(dist_name, import_name)
        if found and self._version_too_old(dist_name, found):
            self._too_old(project=project, url=url,
                          found=found, required=self._required_str(dist_name))

    # --- non-pip checks (hardcoded versions) ---

    def _check_gtk(self, gtk_version):
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            gi.require_version('PangoCairo', '1.0')
            from gi.repository import Gtk
            Gtk  # pylint: disable=W0104
        except (ValueError, ImportError) as e:
            raise SystemExit(
                "ERROR: GTK+ not found, can't start Stoq: %r" % (e, ))

        if (Gtk.MAJOR_VERSION, Gtk.MINOR_VERSION) < gtk_version:
            self._too_old(project="Gtk+",
                          url="http://www.gtk.org/",
                          found=_tuple2str(Gtk.gtk_version),
                          required=gtk_version)

    def _check_pycairo(self, version):
        try:
            import cairo
        except ImportError:
            self._missing(project="pycairo",
                          url='http://www.cairographics.org/pycairo/',
                          version=version)
            return

        if cairo.version_info < version:
            self._too_old(project="pycairo",
                          url='http://www.cairographics.org/pycairo/',
                          found=cairo.version,
                          required=version)

    def _check_pygobjectwebkit(self, version):
        try:
            import gi
            gi.require_version('WebKit2', '%s.%s' % (version))
            from gi.repository import WebKit2
            WebKit2  # pylint: disable=W0104
        except (ValueError, ImportError):
            self._missing(project='WebKit2',
                          url='https://pygobject.gnome.org/',
                          version=version)

    def _check_psql(self, version):
        if 'WINEPREFIX' in os.environ:
            return

        executable = 'psql'
        paths = os.environ['PATH'].split(os.pathsep)
        if platform.system() == 'Windows':
            executable += '.exe'
            paths.insert(0, os.path.dirname(sys.argv[0]))
        for path in paths:
            full = os.path.join(path, executable)
            if os.path.exists(full):
                break
        else:
            self._missing(project="PostgreSQL",
                          url='http://www.postgresql.org/',
                          version=version)

    def _check_pyserial(self, version):
        try:
            import serial
            serial  # pylint: disable=W0104
        except ImportError:
            self._missing(project='pySerial',
                          url='http://pyserial.sourceforge.net/',
                          version=version)

    def _check_pyobjc(self, version):
        try:
            import objc
            objc  # pylint: disable=W0104
        except ImportError:
            self._missing(project='pyobjc',
                          url='http://pyobjc.sf.net/',
                          version=version)
            return

        if list(map(int, objc.__version__.split('.'))) < list(version):
            self._too_old(project="pyobjc",
                          url='http://pyobjc.sf.net/',
                          required=version,
                          found=objc.__version__)

        try:
            import AppKit
            AppKit  # pylint: disable=W0104
        except ImportError:
            self._missing(project='pyobjc with cocoa support',
                          url='http://pyobjc.sf.net/',
                          version=version)

    # --- pip package checks (versions from pyproject.toml) ---

    def _check_kiwi(self):
        self._check_pip_package('kiwi-gtk', 'Kiwi',
                                'http://www.async.com.br/projects/kiwi/')

    def _check_zope_interface(self):
        self._check_pip_package('zope.interface', 'ZopeInterface',
                                'http://www.zope.org/Products/ZopeInterface')

    def _check_dateutil(self):
        self._check_pip_package('python-dateutil', 'Dateutil',
                                'http://labix.org/python-dateutil/')

    def _check_xlwt(self):
        self._check_pip_package('xlwt', 'xlwt',
                                'http://www.python-excel.org/')

    def _check_pyjwt(self):
        self._check_pip_package('pyjwt', 'PyJWT',
                                'https://pyjwt.readthedocs.io/')

    def _check_psycopg(self):
        self._check_pip_package(
            'psycopg2-binary',
            "psycopg2 - PostgreSQL Database adapter for Python",
            'http://www.initd.org/projects/psycopg2')

    def _check_storm(self):
        self._check_pip_package('storm', 'storm - an object-relational mapper',
                                'https://storm.canonical.com')

    def _check_pil(self):
        self._check_pip_package('pillow',
                                'Pillow - The friendly PIL fork',
                                'https://python-pillow.org/')

    def _check_reportlab(self):
        self._check_pip_package('reportlab', 'Reportlab',
                                'http://www.reportlab.org/')

    def _check_mako(self):
        self._check_pip_package('mako', 'Mako',
                                'http://www.makotemplates.org/')

    def _check_weasyprint(self):
        try:
            import weasyprint
            weasyprint  # pylint: disable=W0104
        except ImportError as e:
            self._missing(project='weasyprint',
                          url='http://weasyprint.org/',
                          version=self._required_str('weasyprint'),
                          details=str(e))
            return
        found = self._get_version('weasyprint')
        if found and self._version_too_old('weasyprint', found):
            self._too_old(project="weasyprint",
                          url='http://weasyprint.org/',
                          found=found,
                          required=self._required_str('weasyprint'))

    def _check_poppler(self):
        try:
            import gi
            gi.require_version('Poppler', '0.18')
            from gi.repository import Poppler
            Poppler  # pylint: disable=W0104
        except (ValueError, ImportError) as e:
            self._missing(project='Poppler',
                          url='https://poppler.freedesktop.org/',
                          details=str(e))

    def _check_stoqdrivers(self):
        self._check_pip_package('stoqdrivers', 'Stoqdrivers',
                                'http://www.stoq.com.br')

    def _check_packaging(self):
        self._check_pip_package('packaging', 'packaging',
                                'https://packaging.pypa.io/')

    def _check_cryptography(self):
        self._check_pip_package('cryptography', 'cryptography',
                                'https://cryptography.io/')

    def _check_pykcs11(self):
        self._check_pip_package('PyKCS11', 'PyKCS11',
                                'https://pypi.org/project/PyKCS11/')

    def _check_sqlparse(self):
        self._check_pip_package('sqlparse', 'sqlparse',
                                'https://sqlparse.readthedocs.io/')


def check_dependencies(text_mode=False):
    dp = DependencyChecker()
    dp.text_mode = text_mode
    dp.check()
