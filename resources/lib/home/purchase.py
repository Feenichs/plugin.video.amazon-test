#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Renting and buying titles from the TV interface.

The offers are shown on the detail pages, but the purchase request itself is
not implemented yet: its endpoint is only known from the website's scripts and
will be added once it has been verified with a capture of a real rental. Until
then PURCHASE_ENABLED stays False and the user is sent to the browser.

The purchase PIN is prepared for that: it is stored as a salted hash in the
add-on's config folder and asked before every purchase once purchases are enabled.
"""
import hashlib
import os

import xbmcgui

from ..common import Globals
from ..configs import getConfig, writeConfig
from ..l10n import getString

_g = Globals()

PURCHASE_ENABLED = False
PIN_CONFIG = 'purchase_pin'
MAX_PIN_ATTEMPTS = 3


def _hash(pin, salt):
    return hashlib.sha256((salt + pin).encode('utf-8')).hexdigest()


def pin_is_set():
    return bool(getConfig(PIN_CONFIG))


def check_pin():
    """ True if no PIN is set or the user entered it correctly """
    stored = getConfig(PIN_CONFIG)
    if not stored:
        return True
    salt, _, digest = stored.partition(':')
    for _ in range(MAX_PIN_ATTEMPTS):
        pin = xbmcgui.Dialog().numeric(0, getString(30369), bHiddenInput=True)
        if not pin:
            return False
        if _hash(pin, salt) == digest:
            return True
        xbmcgui.Dialog().notification(_g.__plugin__, getString(30371), xbmcgui.NOTIFICATION_WARNING, 2000)
    return False


def set_pin():
    """ Set or change the purchase PIN, an existing PIN has to be entered first """
    if not check_pin():
        return
    dialog = xbmcgui.Dialog()
    pin = dialog.numeric(0, getString(30369), bHiddenInput=True)
    if not pin:
        return
    if dialog.numeric(0, getString(30370), bHiddenInput=True) != pin:
        dialog.ok(_g.__plugin__, getString(30372))
        return
    salt = os.urandom(16).hex()
    writeConfig(PIN_CONFIG, f'{salt}:{_hash(pin, salt)}')
    dialog.notification(_g.__plugin__, getString(30375), time=2000, sound=False)


def remove_pin():
    if pin_is_set() and check_pin():
        writeConfig(PIN_CONFIG, '')
        xbmcgui.Dialog().notification(_g.__plugin__, getString(30376), time=2000, sound=False)


def offer_label(offer):
    return getString(30363 if offer['kind'] == 'rent' else 30364) % offer['label']


def choose_and_purchase(title, offers):
    """ Let the user pick an offer and purchase it, True if the title was purchased """
    if not offers:
        return False
    dialog = xbmcgui.Dialog()
    choice = dialog.contextmenu([offer_label(offer) for offer in offers])
    if choice < 0:
        return False
    offer = offers[choice]
    if not PURCHASE_ENABLED:
        dialog.ok(title, getString(30365) % _g.BaseUrl.replace('https://', '').replace('http://', ''))
        return False
    if not dialog.yesno(getString(30373), getString(30374) % (title, offer_label(offer))):
        return False
    if not check_pin():
        return False
    return _purchase(offer)


def _purchase(offer):
    """ Send the purchase request, added once the request is known from a browser capture """
    raise NotImplementedError('The purchase request is not implemented yet')
