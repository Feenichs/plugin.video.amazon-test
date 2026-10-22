#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys

if __name__ == '__main__':
    # The add-on's globals expect the arguments of a plugin call
    sys.argv = ['plugin://plugin.video.amazon-test/', '-1', '?mode=home']
    from resources.lib.home import open_home_window
    open_home_window()
