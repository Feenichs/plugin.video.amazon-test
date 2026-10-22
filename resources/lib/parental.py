#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PINs of the add-on: the parents' PIN protects purchases and unlocks every
title, children's PINs unlock titles up to their own age rating.

Playback asks for a PIN if the age rating of a title reaches the age chosen in
the settings. Titles without a rating are free, need any PIN or only the
parents' PIN, also chosen in the settings. Everything is stored in the config
folder of the add-on, PINs as salted hashes, so that changing the limits is
only possible with the parents' PIN.
"""
import hashlib
import json
import os
import re

import xbmcgui

from .common import Globals
from .configs import getConfig, writeConfig
from .l10n import getString

_g = Globals()

PIN_CONFIG = 'purchase_pin'
AGE_CONFIG = 'parental_age'
UNRATED_CONFIG = 'parental_unrated'
CHILDREN_CONFIG = 'parental_children'
MAX_PIN_ATTEMPTS = 3

AGES = (0, 6, 12, 16, 18)
# titles without a rating
UNRATED_FREE = 0
UNRATED_ANY_PIN = 1
UNRATED_PARENTS = 2
# what a PIN unlocks
PARENTS = 99

MARKET_DE = 'A1PA6795UKMFR9'
# ratings without the age in their name
_RATINGS = {
    'all': 0, 'u': 0, 'g': 0, 'tv-y': 0, 'tv-g': 0,
    'pg': 6, 'tv-y7': 7, 'tv-y7-fv': 7, 'tv-pg': 10,
    'pg-13': 13, 'tv-14': 14, 'r': 17, 'tv-ma': 17, 'nc-17': 18,
}


def rating_age(rating):
    """ Age of a rating like '16', 'FSK 16', '13+', 'ALL' or 'PG-13', None if it is unknown """
    text = (rating or '').strip().lower()
    if not text:
        return None
    if text in _RATINGS:
        return _RATINGS[text]
    match = re.search(r'(\d{1,2})', text)
    if match:
        return int(match.group(1))
    return None


def age_label(age):
    return f'FSK {age}' if _g.MarketID == MARKET_DE else f'{age}+'


def _hash(pin, salt):
    return hashlib.sha256((salt + pin).encode('utf-8')).hexdigest()


def _new_hash(pin):
    salt = os.urandom(16).hex()
    return f'{salt}:{_hash(pin, salt)}'


def _matches(pin, stored):
    salt, _, digest = (stored or '').partition(':')
    return bool(digest) and _hash(pin, salt) == digest


def _children():
    try:
        return json.loads(getConfig(CHILDREN_CONFIG, '[]'))
    except ValueError:
        return []


def _save_children(children):
    writeConfig(CHILDREN_CONFIG, json.dumps(children))


def _unlocks(pin):
    """ Age a PIN unlocks (PARENTS for the parents' PIN), None for a wrong PIN """
    if _matches(pin, getConfig(PIN_CONFIG)):
        return PARENTS
    return next((child['age'] for child in _children() if _matches(pin, child['pin'])), None)


def _ask_pin(heading):
    return xbmcgui.Dialog().numeric(0, heading, bHiddenInput=True)


def _wrong_pin():
    xbmcgui.Dialog().notification(_g.__plugin__, getString(30371), xbmcgui.NOTIFICATION_WARNING, 2000)


# Parents' PIN, also for purchases

def pin_is_set():
    return bool(getConfig(PIN_CONFIG))


def check_pin():
    """ True if no parents' PIN is set or the user entered it correctly """
    stored = getConfig(PIN_CONFIG)
    if not stored:
        return True
    for _ in range(MAX_PIN_ATTEMPTS):
        pin = _ask_pin(getString(30369))
        if not pin:
            return False
        if _matches(pin, stored):
            return True
        _wrong_pin()
    return False


def _new_pin(exclude=None):
    """ Ask a new PIN twice, None if cancelled or it is already used by someone else """
    dialog = xbmcgui.Dialog()
    pin = _ask_pin(getString(30369) if exclude is None else getString(30410))
    if not pin:
        return None
    if _ask_pin(getString(30370)) != pin:
        dialog.ok(_g.__plugin__, getString(30372))
        return None
    own = (exclude or {}).get('pin')
    others = [child['pin'] for child in _children() if child['pin'] != own]
    if exclude is not None:
        others.append(getConfig(PIN_CONFIG))
    if any(_matches(pin, stored) for stored in others):
        dialog.ok(_g.__plugin__, getString(30415))
        return None
    return pin


def set_pin():
    """ Set or change the parents' PIN, an existing PIN has to be entered first """
    if not check_pin():
        return False
    pin = _new_pin()
    if not pin:
        return False
    writeConfig(PIN_CONFIG, _new_hash(pin))
    xbmcgui.Dialog().notification(_g.__plugin__, getString(30375), time=2000, sound=False)
    return True


def remove_pin():
    if pin_is_set() and check_pin():
        writeConfig(PIN_CONFIG, '')
        xbmcgui.Dialog().notification(_g.__plugin__, getString(30376), time=2000, sound=False)
        if getConfig(AGE_CONFIG):
            # without the parents' PIN the limits could not be changed any more
            writeConfig(AGE_CONFIG, '')
            xbmcgui.Dialog().ok(getString(30396), getString(30417))


# Playback

def may_play(rating, title=''):
    """ True if a title with this age rating may be played, asks for a PIN if needed """
    limit = getConfig(AGE_CONFIG)
    if limit == '' or not pin_is_set():
        return True
    age = rating_age(rating)
    unrated = int(getConfig(UNRATED_CONFIG, str(UNRATED_PARENTS)))
    if age is None:
        if unrated == UNRATED_FREE:
            return True
        required = 0 if unrated == UNRATED_ANY_PIN else PARENTS
    else:
        if age < int(limit):
            return True
        required = age
    label = age_label(age) if age is not None else getString(30402)
    heading = getString(30413) % (f'{title} ({label})' if title else label)
    for _ in range(MAX_PIN_ATTEMPTS):
        pin = _ask_pin(heading)
        if not pin:
            return False
        unlocks = _unlocks(pin)
        if unlocks is None:
            _wrong_pin()
            continue
        if unlocks >= required:
            return True
        xbmcgui.Dialog().ok(_g.__plugin__, getString(30414) % age_label(unlocks))
        return False
    return False


# Settings

def _require_parents_pin():
    """ The settings need the parents' PIN, set it up if there is none """
    if pin_is_set():
        return check_pin()
    xbmcgui.Dialog().ok(getString(30396), getString(30416))
    return set_pin()


def age_settings():
    """ Age from which a PIN is required and the handling of titles without rating """
    if not _require_parents_pin():
        return
    dialog = xbmcgui.Dialog()
    limit = getConfig(AGE_CONFIG)
    options = [('', getString(30399)), ('0', getString(30400))] + [(str(age), getString(30401) % age_label(age)) for age in AGES[1:]]
    current = next((i for i, (value, _) in enumerate(options) if value == limit), 0)
    choice = dialog.select(getString(30397).rstrip('…'), [label for _, label in options], preselect=current)
    if choice < 0:
        return
    writeConfig(AGE_CONFIG, options[choice][0])
    if options[choice][0] == '':
        return
    unrated = [getString(30403), getString(30404), getString(30405)]
    current = int(getConfig(UNRATED_CONFIG, str(UNRATED_PARENTS)))
    choice = dialog.select(getString(30402), unrated, preselect=current)
    if choice >= 0:
        writeConfig(UNRATED_CONFIG, str(choice))
    dialog.notification(_g.__plugin__, getString(30375), time=2000, sound=False)


def _choose_age(heading, preselect=0):
    ages = AGES[:-1]
    choice = xbmcgui.Dialog().select(heading, [getString(30409) % age_label(age) for age in ages],
                                     preselect=ages.index(preselect) if preselect in ages else 0)
    return ages[choice] if choice >= 0 else None


def children_settings():
    """ Add, change and remove the PINs of the children """
    if not _require_parents_pin():
        return
    dialog = xbmcgui.Dialog()
    while True:
        children = _children()
        entries = [f"{child['name']} · {getString(30409) % age_label(child['age'])}" for child in children]
        choice = dialog.select(getString(30398).rstrip('…'), entries + [getString(30406)])
        if choice < 0:
            return
        if choice == len(children):
            name = dialog.input(getString(30407)).strip()
            if not name:
                continue
            age = _choose_age(getString(30408))
            if age is None:
                continue
            pin = _new_pin(exclude={})
            if pin:
                children.append({'name': name, 'age': age, 'pin': _new_hash(pin)})
                _save_children(children)
                dialog.notification(_g.__plugin__, getString(30375), time=2000, sound=False)
            continue
        child = children[choice]
        action = dialog.contextmenu([getString(30410), getString(30411), getString(30412)])
        if action == 0:
            pin = _new_pin(exclude=child)
            if pin:
                child['pin'] = _new_hash(pin)
        elif action == 1:
            age = _choose_age(getString(30408), child['age'])
            if age is not None:
                child['age'] = age
        elif action == 2 and dialog.yesno(child['name'], getString(30412) + '?'):
            children.remove(child)
        else:
            continue
        _save_children(children)
