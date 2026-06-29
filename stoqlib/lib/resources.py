# -*- coding: utf-8 -*-
#
# Copyright (C) 2005-2014 Async Open Source
#
# Author(s): Stoq Team <stoq-devel@async.com.br>
#
#
# Resource access helpers replacing pkg_resources.
# Uses kiwi's Library so resources resolve to stoq's data dir.

from stoqlib.lib.kiwilibrary import library

__all__ = ['resource_filename', 'resource_string', 'resource_listdir']


def _split(resource):
    return [p for p in resource.split('/') if p]


def resource_filename(package, resource):
    return library.get_resource_filename(package, *_split(resource))


def resource_string(package, resource):
    return library.get_resource_string(package, *_split(resource))


def resource_listdir(package, resource):
    return library.get_resource_names(package, *_split(resource))
