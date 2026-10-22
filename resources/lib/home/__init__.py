#!/usr/bin/env python
# -*- coding: utf-8 -*-
import xbmc


def open_home_window():
    """ Open the TV interface, run as script: RunScript(plugin.video.amazon-test,home) """
    from ..common import Globals
    from ..l10n import getString
    from ..users import loadUser, loadUsers, switchUser

    _g = Globals()
    users = loadUsers()
    if not users:
        _g.dialog.notification(getString(30200), getString(30216))
        xbmc.executebuiltin(f"Addon.OpenSettings({_g.addon.getAddonInfo('id')})")
        return
    if not loadUser('mid', cachedUsers=users):
        switchUser(0)
    _g.InitialiseProvider(loadUser('mid', cachedUsers=users), loadUser('baseurl', cachedUsers=users),
                          loadUser('atvurl', cachedUsers=users), loadUser('pv', cachedUsers=users), loadUser('deviceid', cachedUsers=users))

    # imported here as the window requires a running Kodi GUI
    from .window import open_home_window as _open_home_window
    _open_home_window()
