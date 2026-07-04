# -*- Mode: Python; coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2017 Async Open Source <http://www.async.com.br>
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

"""TLS HTTP session for Sefaz web services using a client certificate.

Originally built on python-nss (NSS certdb + NSS SSL). python-nss is
unmaintained and does not build on Python 3.14 / NSS 3.120, so this
module now uses the stdlib :mod:`ssl` module plus pyOpenSSL to load a
PKCS#12 (.pfx) client certificate. The ``nss_setup``/``NssSession``/
``NssResponse`` names are kept so callers (certutils.py) are unchanged.

PKCS#11 (A3) client-cert SSL is not supported here: stdlib ssl needs an
OpenSSL pkcs11 engine/provider (``libengine-pkcs11-openssl3``) to use a
token private key during the handshake, and that package is not
installed. PKCS#12 (A1) certificates work out of the box. PKCS#11
*signing* (xmlutils.PyKCS11Signer) is unaffected.
"""

import http.client
import logging
import os
import ssl
import tempfile
import urllib.parse

log = logging.getLogger(__name__)

_certdb = None
_password_callback = None
_certificate_callback = None
_ssl_context = None


class _Token:
    """Shim mimicking the nss Slot passed to password_callback."""

    def __init__(self, token_name):
        self.token_name = token_name


def nss_setup(certdb, password_callback=None,
              certificate_callback=None):
    """Register the cert db and callbacks for later NssSession use.

    Kept for compatibility with certutils.py. ``certdb`` is the
    directory containing ``cert.pfx`` (PKCS#12) or ``cert.so``
    (PKCS#11 module). Re-calling invalidates any cached SSL context.
    """
    global _certdb, _password_callback
    global _certificate_callback, _ssl_context
    _certdb = certdb
    _password_callback = password_callback
    _certificate_callback = certificate_callback
    _ssl_context = None


def _get_pkcs12_password():
    if _password_callback is None:
        return None
    return _password_callback(_Token('PKCS12'), False)


def _build_ssl_context():
    """Build an ssl.SSLContext with the client cert loaded.

    PKCS#12 (A1) certs are loaded via the cryptography package.
    PKCS#11 (A3) certs cannot be used here: driving an OpenSSL
    pkcs11 engine/provider for the TLS handshake needs the OpenSSL C
    ENGINE/OSSL_STORE API, which neither stdlib ssl nor pyOpenSSL
    expose (pyOpenSSL dropped Engine in 23.x). The pkcs11 engine/provider
    .so is installed at the OS level, but Python has no way to wire it
    into SSLContext.load_cert_chain. A3 *signing* (xmlutils) is
    unaffected because that uses PyKCS11 directly.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_default_certs()
    if os.environ.get('STOQ_SSL_VERIFY', '1') == '0':
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

    if not _certdb or not os.path.isdir(_certdb):
        log.warning('certdb not configured: %s', _certdb)
        return ctx

    pfx_path = os.path.join(_certdb, 'cert.pfx')
    if not os.path.isfile(pfx_path):
        raise RuntimeError(
            'PKCS#11 (A3) client-cert SSL is not supported from '
            'Python: stdlib ssl and pyOpenSSL cannot drive the '
            'pkcs11 engine/provider for a TLS handshake. Use a '
            'PKCS#12 (A1) certificate, or export the A3 cert to '
            'PKCS#12. A3 signing (xmlutils) still works via PyKCS11.')

    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, pkcs12)
    password = _get_pkcs12_password()
    pw_bytes = password.encode() if password else None
    with open(pfx_path, 'rb') as f:
        key, cert, addl = pkcs12.load_key_and_certificates(
            f.read(), pw_bytes)

    bundle = cert.public_bytes(Encoding.PEM)
    bundle += key.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    for ca in addl or ():
        bundle += ca.public_bytes(Encoding.PEM)

    # load_cert_chain reads cert+key from one PEM bundle. NamedTemporary
    # uses mkstemp so the file is 0600; deleted right after loading.
    with tempfile.NamedTemporaryFile(
            delete=False, suffix='.pem') as f:
        f.write(bundle)
        bundle_path = f.name
    try:
        ctx.load_cert_chain(bundle_path)
    finally:
        os.unlink(bundle_path)

    return ctx


class NssResponse(object):
    """Response wrapper matching the old nss response API.

    Exposes ``status_code``, ``reason``, ``content`` (bytes) and
    ``text`` (decoded) so it is a drop-in for the previous object.
    """

    def __init__(self, response):
        self._response = response
        self.status_code = response.status
        self.reason = response.reason

    @property
    def content(self):
        return self._response.read()

    @property
    def text(self):
        return self.content.decode('utf-8', errors='replace')


class NssSession(object):
    """HTTPS session for Sefaz using a client certificate.

    Use as a context manager so the SSL context is built and
    connections are closed::

        with NssSession() as s:
            res = s.post(url, data, headers)
    """

    SCHEME_PORT_MAP = {
        'http': 80,
        'https': 443,
    }

    def __init__(self):
        # Map (host, port) -> open HTTP(S)Connection for reuse.
        self._conns = {}

    def __enter__(self):
        self.init()
        return self

    def __exit__(self, *args):
        for conn in self._conns.values():
            conn.close()
        self.shutdown()

    def init(self):
        global _ssl_context
        if _ssl_context is None:
            _ssl_context = _build_ssl_context()

    def shutdown(self):
        # No global NSS state to tear down; connections are closed in
        # __exit__. The SSLContext is kept for reuse across sessions.
        pass

    def get(self, url, headers=None):
        return self.request('GET', url, headers=headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self.request('POST', url, data=data, headers=headers,
                            timeout=timeout)

    def request(self, method, url, data=None, headers=None,
                timeout=None):
        parsed = urllib.parse.urlparse(url)
        port = parsed.port or self.SCHEME_PORT_MAP[parsed.scheme]
        key = (parsed.hostname, port)

        conn = self._conns.get(key)
        if conn is None:
            if parsed.scheme == 'https':
                conn = http.client.HTTPSConnection(
                    parsed.hostname, port,
                    context=_ssl_context, timeout=timeout)
            else:
                conn = http.client.HTTPConnection(
                    parsed.hostname, port, timeout=timeout)
            self._conns[key] = conn

        conn.request(method, parsed.path, body=data, headers=headers)
        return NssResponse(conn.getresponse())


if __name__ == '__main__':
    # Smoke test: uses the stoq certdb (certutils.certdb_path) and
    # posts a NFe status-servico SOAP envelope to the RS homologation
    # endpoint. Requires a configured PKCS#12 certificate.
    from stoqlib.lib.certutils import certdb_path

    nss_setup(certdb_path)

    url = ('https://nfce-homologacao.sefazrs.rs.gov.br/ws/'
           'NfeStatusServico/NFeStatusServico2.asmx')
    data = (
        '<soap:Envelope '
        'xmlns:soap="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<soap:Header>'
        '<nfeCabecMsg xmlns="http://www.portalfiscal.inf.br/nfe/'
        'wsdl/NfeStatusServico2">'
        '<versaoDados>3.10</versaoDados><cUF>43</cUF>'
        '</nfeCabecMsg></soap:Header>'
        '<soap:Body>'
        '<nfeDadosMsg xmlns="http://www.portalfiscal.inf.br/nfe/'
        'wsdl/NfeStatusServico2">'
        '<consStatServ xmlns="http://www.portalfiscal.inf.br/nfe" '
        'versao="3.10">'
        '<tpAmb>2</tpAmb><cUF>43</cUF>'
        '<xServ>STATUS</xServ></consStatServ>'
        '</nfeDadosMsg></soap:Body></soap:Envelope>')
    headers = {
        'Content-type': 'application/soap+xml; charset=utf-8',
        'Accept': 'application/soap+xml; charset=utf-8',
    }
    with NssSession() as s:
        res = s.post(url, data, headers)
        print("status:", res.status_code)
        print("reason:", res.reason)
        print("text:", res.text)
