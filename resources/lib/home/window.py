#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TV interface in the style of the Prime Video app on Fire TV: a tab bar, rows of
tiles with a large preview of the focused title, grid pages and detail pages
with seasons and episodes, made for remote controls.
"""
import json
import os
import threading
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui

from . import data, purchase
from ..common import Globals, Settings
from ..configs import getConfig, writeConfig
from ..l10n import getString
from ..logging import Log

_g = Globals()
_s = Settings()

ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_PREVIOUS_MENU = 10
ACTION_SHOW_INFO = 11
ACTION_NAV_BACK = 92
ACTION_CONTEXT_MENU = 117
ACTION_MOUSE_LONG_CLICK = 108

NAV_LIST = 100
ROWS_GROUPLIST = 300
# row controls of the skin, NUM_ROWS of .scripts/generate_home_window.py
NUM_ROWS = 30
ROW_LISTS = tuple(range(301, 301 + NUM_ROWS))
ROW_LABELS = tuple(range(401, 401 + NUM_ROWS))
ROW_GROUPS = tuple(range(501, 501 + NUM_ROWS))
# load the next rows of a page when the focus is this close to the last row
MORE_ROWS_AHEAD = 3
GRID_PANEL = 600
BUTTON_LIST = 700
SEASON_LIST = 710
EPISODE_LIST = 720
DETAIL_LISTS = (BUTTON_LIST, SEASON_LIST, EPISODE_LIST)

CACHE_SECONDS = 300
GRID_PAGE_SIZE = 30
TAB_SWITCH_DELAY = 0.35
PLAYBACK_END_DELAY = 3
WINDOW_PROPERTY = 'amazon_home_window'

TAB_WATCHLIST = 'watchlist'
TAB_FREE_WITH_ADS = 'freewithads'
FREE_WITH_ADS_URL = '/gp/video/storefront/home/freetv?jic=36%7CCgtmcmVld2l0aGFkcxIMc3Vic2NyaXB0aW9u'
TAB_SEARCH = 'search'
TAB_ACCOUNT = 'account'


def _format_time(seconds):
    seconds = int(seconds)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 else '%d:%02d' % (seconds // 60, seconds % 60)


class HomeWindow(xbmcgui.WindowXML):

    def __init__(self, *args, **kwargs):
        super(HomeWindow, self).__init__(*args, **kwargs)
        self._lock = threading.RLock()
        self._generation = 0
        self._cache = {}
        self._tabs = []
        self._current_tab = None
        self._tab_timer = None
        # views opened on top of the tab: rows of a category, grids and detail pages
        self._stack = []
        self._row_data = {}
        self._row_offset = 0
        # page whose following rows are loaded while scrolling down
        self._more_rows = None
        self._grid = None
        self._detail = None
        self._buttons = []
        self._tab_focus = None
        self._restore = None
        self._focus_rows = False
        self._initialised = False
        self._closing = False
        self._refresh_pending = False
        self._window_id = None
        self._player = _PlayerEvents(self._on_playback_end)

    # Kodi callbacks

    def onInit(self):
        self._window_id = xbmcgui.getCurrentWindowId()
        xbmcgui.Window(10000).setProperty(WINDOW_PROPERTY, str(self._window_id))
        if self._initialised:
            # back from playback or another window
            if xbmc.getCondVisibility('Player.HasMedia'):
                self._refresh_pending = True
            else:
                self._start(self._refresh_view)
            return
        self._initialised = True
        self.setProperty('view', 'tab')
        self.setProperty('user', self._user_name())
        self._load_root()

    def onAction(self, action):
        action_id = action.getId()
        focus_id = self.getFocusId()

        if action_id in (ACTION_NAV_BACK, ACTION_PREVIOUS_MENU):
            self._on_back(focus_id)
            return

        if action_id in (ACTION_CONTEXT_MENU, ACTION_MOUSE_LONG_CLICK):
            item = self._focused_item(focus_id)
            if item:
                self._context_menu(item)
            return

        if action_id == ACTION_SHOW_INFO:
            item = self._focused_item(focus_id)
            if item and item['plot']:
                xbmcgui.Dialog().textviewer(item['label'], item['plot'])
            return

        if focus_id == NAV_LIST and action_id in (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT):
            self._schedule_tab_switch()
            return

        if focus_id == NAV_LIST and action_id == ACTION_MOVE_DOWN:
            self._focus_content()
            return

        if focus_id in ROW_LISTS and action_id in (ACTION_MOVE_UP, ACTION_MOVE_DOWN):
            self._move_row(ROW_LISTS.index(focus_id), up=action_id == ACTION_MOVE_UP)
            return

        if focus_id in DETAIL_LISTS and action_id in (ACTION_MOVE_UP, ACTION_MOVE_DOWN):
            self._move_detail(focus_id, up=action_id == ACTION_MOVE_UP)
            return

        if focus_id == GRID_PANEL and action_id == ACTION_MOVE_UP:
            # the panel moves up by itself, only leave it from the first line
            if self.getControl(GRID_PANEL).getSelectedPosition() < 4:
                self.setFocusId(NAV_LIST)

    def onClick(self, control_id):
        if control_id == NAV_LIST:
            self._switch_tab(self._selected_tab(), focus_rows=True)
            return
        if control_id == BUTTON_LIST:
            self._on_button()
            return
        if control_id == SEASON_LIST:
            self._on_season()
            return
        item = self._focused_item(control_id)
        if item:
            self._activate(item)

    # Navigation

    def _on_back(self, focus_id):
        if self._stack:
            self._close_view()
            return
        if focus_id != NAV_LIST:
            self._set_row_offset(0)
            self.setFocusId(NAV_LIST)
            return
        self.close_window()

    def close_window(self):
        self._closing = True
        with self._lock:
            self._generation += 1
        if self._tab_timer:
            self._tab_timer.cancel()
        self.close()

    def _focus_content(self):
        view = self.getProperty('view')
        if view == 'grid':
            self.setFocusId(GRID_PANEL)
            return
        if view == 'detail':
            self.setFocusId(BUTTON_LIST)
            return
        rows = [row for row in self._filled_rows() if row >= self._row_offset]
        if rows:
            self.setFocusId(ROW_LISTS[rows[0]])
        else:
            self._focus_rows = True

    def _filled_rows(self):
        return sorted(ROW_LISTS.index(list_id) for list_id in self._row_data)

    def _set_row_offset(self, offset):
        """ Show the filled rows from offset on, so that the focused row is the topmost one """
        self._row_offset = offset
        for index in self._filled_rows():
            self.getControl(ROW_GROUPS[index]).setVisible(index >= offset)

    def _move_row(self, index, up):
        filled = self._filled_rows()
        if up:
            above = [row for row in filled if row < index]
            if not above:
                self._set_row_offset(0)
                self.setFocusId(NAV_LIST)
                return
            target = above[-1]
        else:
            below = [row for row in filled if row > index]
            if len(below) < MORE_ROWS_AHEAD and self._more_rows:
                generation = self._generation
                self._start(self._load_more_rows, generation)
            if not below:
                return
            target = below[0]
        self._set_row_offset(target)
        self.setFocusId(ROW_LISTS[target])

    def _move_detail(self, focus_id, up):
        lists = [BUTTON_LIST]
        if self._detail and self._detail.get('seasons'):
            lists.append(SEASON_LIST)
        if self._detail and self._detail.get('episodes'):
            lists.append(EPISODE_LIST)
        index = lists.index(focus_id) if focus_id in lists else 0
        if up:
            if index == 0:
                self.setFocusId(NAV_LIST)
                return
            target = lists[index - 1]
        else:
            if index + 1 >= len(lists):
                return
            target = lists[index + 1]
        self.setProperty('detail_focus', 'episodes' if target == EPISODE_LIST else '')
        self.setFocusId(target)

    # Tabs

    def _load_root(self):
        self.setProperty('loading', 'true')

        def _run():
            self._tabs = self._build_tabs()
            nav = self.getControl(NAV_LIST)
            nav.reset()
            for tab in self._tabs:
                list_item = xbmcgui.ListItem(label=tab['title'], offscreen=True)
                list_item.setProperty('tab', tab['id'])
                nav.addItem(list_item)
            if self._closing:
                return
            self._switch_tab(self._tabs[0]['id'], force=True)
            self.setFocusId(NAV_LIST)

        self._start(_run)

    def _build_tabs(self):
        """ Tabs from the navigation of the website, followed by watchlist, search and account """
        tabs = []
        catalog = {}
        pv = _g.pv
        if hasattr(pv, 'BuildRoot') and hasattr(pv, '_catalog'):
            if 'root' not in pv._catalog:
                pv.BuildRoot()
            catalog = pv._catalog.get('root') or {}
        vp = data.video_path()
        for key, node in catalog.items():
            if not key.startswith('coll'):
                continue
            url = node.get('lazyLoadURL')
            if not url and 'movie' in key.lower():
                url = vp + '/movie'
            if not url or '_mmlinks' in key:
                continue
            tabs.append({'id': key, 'title': node.get('title', ''), 'url': url})
        if not tabs:
            tabs = [{'id': 'home', 'title': getString(30352), 'url': vp + '/storefront'},
                    {'id': 'movies', 'title': getString(30353), 'url': vp + '/movie'},
                    {'id': 'tv', 'title': getString(30354), 'url': vp + '/tv'},
                    {'id': 'livetv', 'title': getString(30346), 'url': vp + '/livetv'}]
        if not _g.UsePrimeVideo:
            # the page of the "Free with ads" rows, the website has no menu entry for it
            position = next((i + 1 for i, tab in enumerate(tabs) if 'streamfree' in tab['url']), min(1, len(tabs)))
            tabs.insert(position, {'id': TAB_FREE_WITH_ADS, 'title': getString(30395), 'url': FREE_WITH_ADS_URL})
        # "My Stuff" like in the app: watchlist and purchases and rentals (video library), split into
        # movies and TV shows with the filters of the website
        tabs.append({'id': TAB_WATCHLIST, 'title': getString(30386), 'url': vp + '/mystuff/watchlist',
                     'sections': [(vp + '/mystuff/watchlist/movie/', getString(30387)),
                                  (vp + '/mystuff/watchlist/tv/', getString(30388)),
                                  (vp + '/mystuff/library/movie/', getString(30389)),
                                  (vp + '/mystuff/library/tv/', getString(30390))]})
        tabs.append({'id': TAB_SEARCH, 'title': getString(30324), 'url': None})
        tabs.append({'id': TAB_ACCOUNT, 'title': getString(30350), 'url': None})
        return tabs

    def _selected_tab(self):
        position = self.getControl(NAV_LIST).getSelectedPosition()
        if 0 <= position < len(self._tabs):
            return self._tabs[position]['id']
        return self._tabs[0]['id'] if self._tabs else None

    def _schedule_tab_switch(self):
        if self._tab_timer:
            self._tab_timer.cancel()
        timer = threading.Timer(TAB_SWITCH_DELAY, lambda: self._switch_tab(self._selected_tab()))
        timer.daemon = True
        self._tab_timer = timer
        timer.start()

    def _switch_tab(self, tab_id, focus_rows=False, force=False):
        if self._closing or tab_id is None:
            return
        if self._stack:
            self._stack = []
            self._grid = self._detail = None
            self.setProperty('view', 'tab')
            self.setProperty('page_title', '')
            force = True
        if tab_id != self._current_tab or force:
            if tab_id != self._current_tab:
                self._restore = None
            self._current_tab = tab_id
            nav = self.getControl(NAV_LIST)
            for position, tab in enumerate(self._tabs):
                nav.getListItem(position).setProperty('active', 'true' if tab['id'] == tab_id else '')
            self._show_rows(lambda: self._tab_rows(tab_id), self._tab_url(tab_id))
        if focus_rows:
            self._focus_content()

    def _tab(self, tab_id):
        return next((tab for tab in self._tabs if tab['id'] == tab_id), None)

    def _tab_url(self, tab_id):
        """ URL of a tab that shows the rows of one page """
        tab = self._tab(tab_id)
        return tab['url'] if tab and 'sections' not in tab else None

    def _tab_rows(self, tab_id):
        if tab_id == TAB_SEARCH:
            return self._search_rows()
        if tab_id == TAB_ACCOUNT:
            return self._account_rows()
        tab = self._tab(tab_id)
        if not tab:
            return []
        if 'sections' in tab:
            return [self._section_row(url, title) for url, title in tab['sections']]
        return self._page_rows(tab['url'])

    def _section_row(self, url, title):
        """ One row with our own title from all titles of a page, e.g. the movies of the watchlist """
        rows = self._page_rows(url)
        items = [item for row in rows for item in row['items']]
        more = next((row['more'] for row in rows if row.get('more')), None)
        return {'title': title, 'items': items, 'more': more, 'see_more': '' if more else (url if len(items) > data.ROW_MAX_ITEMS else '')}

    def _page_rows(self, url, use_cache=True):
        """ Rows of a page, with the following rows loaded so far """
        now = time.time()
        cached = self._cache.get(url)
        if use_cache and cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        rows, next_url = data.rows_page(url, getString(30326))
        self._cache[url] = (now, rows, next_url)
        return rows

    def _search_rows(self):
        rows = [{'title': getString(30324), 'items': [
            data.make_item(data.KIND_ACTION, getString(30338), action='search')]}]
        history = json.loads(getConfig('search_history', '[]'))
        if history:
            rows.append({'title': getString(30339), 'items': [
                data.make_item(data.KIND_ACTION, query, action='search_query')
                for query in history]})
        return rows

    def _account_rows(self):
        items = [
            data.make_item(data.KIND_ACTION, getString(30340), action='settings'),
            data.make_item(data.KIND_ACTION, getString(30341), action='classic'),
            data.make_item(data.KIND_ACTION, getString(30348), action='refresh'),
        ]
        return [{'title': self._user_name() or getString(30350), 'items': items}]

    # Rows

    def _show_rows(self, get_rows, url=None):
        """ Show rows, url is the page they come from if it continues with more rows while scrolling """
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._row_data = {}
        self._row_offset = 0
        self._more_rows = None
        for index, group_id in enumerate(ROW_GROUPS):
            self.getControl(ROW_LISTS[index]).reset()
            self.getControl(group_id).setVisible(False)
        self.setProperty('empty', '')
        self.setProperty('loading', 'true')

        def _run():
            rows = get_rows()
            if self._stale(generation):
                return
            shown = self._add_rows(rows, 0, generation)
            if shown is None:
                return
            if url:
                self._more_rows = {'url': url, 'end': shown, 'generation': generation, 'loading': False}
            # back to a tile of a row that was loaded while scrolling, e.g. after playback
            for _ in range(len(ROW_LISTS)):
                if not (self._restore and self._restore[0] in ROW_LISTS and ROW_LISTS.index(self._restore[0]) >= self._row_count()):
                    break
                if not self._load_more_rows(generation):
                    if not self._stale(generation):
                        self._restore = None
                        self._focus_content()
                    break
            if not self._stale(generation):
                self.setProperty('loading', '')
                if not shown:
                    self.setProperty('empty', 'true')
                    if self.getFocusId() in ROW_LISTS:
                        self.setFocusId(NAV_LIST)

        self._start(_run)

    def _add_rows(self, rows, start, generation):
        """ Fill the row controls from start on, returns the number of filled rows, None if the view changed """
        index = start
        for row in rows:
            if index >= len(ROW_LISTS):
                break
            items = list(row['items'][:data.ROW_MAX_ITEMS])
            if not items:
                continue
            if row.get('more') or row.get('see_more') or len(row['items']) > data.ROW_MAX_ITEMS:
                items.append(data.make_item(data.KIND_ALL, getString(30327), row_title=row['title'],
                                            more=row, thumb=''))
            with self._lock:
                if self._stale(generation):
                    return None
                self._fill_row(index, row['title'], items)
            index += 1
            if self._focus_rows:
                self._focus_rows = False
                self._focus_content()
        return index

    def _row_count(self):
        return self._more_rows['end'] if self._more_rows else len(self._row_data)

    def _load_more_rows(self, generation):
        """ Append the next rows of the page, returns False if there are none """
        with self._lock:
            more = self._more_rows
            if self._stale(generation) or not more or more['generation'] != generation or more['loading']:
                return False
            cached = self._cache.get(more['url'])
            next_url = cached[2] if cached and len(cached) > 2 else None
            if not next_url or more['end'] >= len(ROW_LISTS):
                self._more_rows = None
                return False
            more['loading'] = True
        self.setProperty('loading', 'true')
        try:
            rows, after = data.rows_page(next_url)
        finally:
            more['loading'] = False
            if not self._stale(generation):
                self.setProperty('loading', '')
        if self._stale(generation):
            return False
        # keep the loaded rows, returning to the page shows them again
        with self._lock:
            if self._cache.get(more['url']) is cached:
                self._cache[more['url']] = (cached[0], cached[1] + rows, after if after != next_url else None)
        end = self._add_rows(rows, more['end'], generation)
        if end is None:
            return False
        more['end'] = end
        return True

    def _fill_row(self, index, title, items):
        list_id = ROW_LISTS[index]
        control = self.getControl(list_id)
        control.reset()
        control.addItems([self._make_listitem(item) for item in items])
        self.getControl(ROW_LABELS[index]).setLabel(title or '')
        self._row_data[list_id] = items
        self.getControl(ROW_GROUPS[index]).setVisible(index >= self._row_offset)
        self._restore_focus(list_id, control, len(items))

    # Views on top of the tabs

    def _push(self, view):
        """ Open a view, remembering the focused tile to return to it """
        focus_id = self.getFocusId()
        position = None
        if focus_id in ROW_LISTS + (GRID_PANEL, EPISODE_LIST, SEASON_LIST, BUTTON_LIST):
            position = (focus_id, self.getControl(focus_id).getSelectedPosition(), self._row_offset)
        if self._stack:
            self._stack[-1]['focus'] = position
        else:
            self._tab_focus = position
        self._stack.append(view)
        self._render(view)

    def _close_view(self):
        self._stack.pop()
        if self._stack:
            view = self._stack[-1]
            self._restore = view.get('focus')
            self._render(view)
            return
        self._grid = self._detail = None
        self.setProperty('view', 'tab')
        self.setProperty('page_title', '')
        self._restore = self._tab_focus
        self._focus_rows = not self._restore
        self._show_rows(lambda: self._tab_rows(self._current_tab), self._tab_url(self._current_tab))

    def _render(self, view):
        kind = view['type']
        if kind == 'rows':
            self._grid = self._detail = None
            self.setProperty('view', 'tab')
            self.setProperty('page_title', view['title'])
            self._focus_rows = not self._restore
            self._show_rows(lambda: self._page_rows(view['url']), view['url'])
        elif kind == 'grid':
            self._detail = None
            self._show_grid(view)
        elif kind == 'detail':
            self._grid = None
            self._show_detail(view)

    def _open_page(self, item):
        self._push({'type': 'rows', 'title': item['label'], 'url': item['url']})

    def _open_grid(self, title, source, page=1):
        self._push({'type': 'grid', 'title': title, 'source': source, 'page': page})

    def _open_all(self, item):
        row = item['more']
        source = {'items': list(row['items']), 'next_url': row.get('more'), 'lock': threading.Lock(), 'seen': set()}
        if not row.get('more') and row.get('see_more'):
            # the row continues on its own page
            source = self._new_source(row['see_more'])
        source['seen'] = {i['asin'] for i in source['items'] if i['asin']}
        self._open_grid(item['row_title'] or getString(30327), source)

    @staticmethod
    def _new_source(url):
        return {'items': [], 'next_url': url, 'lock': threading.Lock(), 'seen': set()}

    # Grid

    def _show_grid(self, view):
        with self._lock:
            self._generation += 1
            generation = self._generation
        source = view['source']
        page = view['page']
        panel = self.getControl(GRID_PANEL)
        title = view['title'] if page == 1 else f"{view['title']} · {getString(30342) % page}"
        self.setProperty('view', 'grid')
        self.setProperty('page_title', title)
        self.setProperty('empty', '')
        self.setProperty('loading', 'true')
        panel.reset()
        self._grid = {'items': []}

        def _run():
            start = (page - 1) * GRID_PAGE_SIZE
            end = start + GRID_PAGE_SIZE
            if not self._load_source(source, end + 1, generation):
                return
            items = source['items'][start:end]
            if page > 1:
                items.insert(0, data.make_item(data.KIND_BACK, getString(30343)))
            if len(source['items']) > end or source['next_url']:
                items.append(data.make_item(data.KIND_NEXT, getString(30344)))
            with self._lock:
                if self._stale(generation):
                    return
                self._grid['items'] = items
                panel.addItems([self._make_listitem(item) for item in items])
                self.setProperty('loading', '')
                if not source['items']:
                    self.setProperty('empty', 'true')
                    self.setFocusId(NAV_LIST)
                    return
                if not self._restore_focus(GRID_PANEL, panel, len(items)):
                    panel.selectItem(1 if page > 1 and len(items) > 1 else 0)
                    self.setFocusId(GRID_PANEL)

        self._start(_run)

    def _load_source(self, source, num_items, generation):
        """ Load pages into the source until it has num_items items or no more pages """
        with source['lock']:
            while len(source['items']) < num_items and source['next_url']:
                if self._stale(generation):
                    return False
                url = source['next_url']
                items, next_url = data.grid_page(url)
                for item in items:
                    if item['asin'] and item['asin'] in source['seen']:
                        continue
                    source['seen'].add(item['asin'])
                    source['items'].append(item)
                source['next_url'] = next_url if next_url != url and items else None
        return not self._stale(generation)

    # Detail page

    def _open_detail(self, item):
        self._push({'type': 'detail', 'url': item['url'], 'item': item})

    def _show_detail(self, view):
        with self._lock:
            self._generation += 1
            generation = self._generation
        item = view['item']
        self.setProperty('view', 'detail')
        self.setProperty('page_title', '')
        self.setProperty('detail_focus', '')
        self.setProperty('empty', '')
        for list_id in DETAIL_LISTS:
            self.getControl(list_id).reset()
        # show what the tile already knows while the page loads
        self._set_detail_properties(item.get('detail') or {
            'title': item['label'], 'plot': item['plot'], 'fanart': item['fanart'], 'logo': item['logo'],
            'year': item['year'], 'runtime': item['runtime'], 'rating': item['rating'], 'message': item['message'],
            'season_title': '', 'genres': [], 'uhd': False})
        self.setProperty('loading', 'true')

        def _run():
            info = view.get('detail') or data.detail(view['url'])
            if self._stale(generation):
                return
            self.setProperty('loading', '')
            if not info:
                self.setProperty('empty', 'true')
                return
            view['detail'] = info
            with self._lock:
                if self._stale(generation):
                    return
                self._detail = info
                self._set_detail_properties(info)
                self._fill_detail(info)

        self._start(_run)

    def _set_detail_properties(self, info):
        meta = [info.get('year'), info.get('runtime'), info.get('rating') and getString(30355) % info['rating'],
                'UHD' if info.get('uhd') else '']
        self.setProperty('detail_title', info.get('title', ''))
        self.setProperty('detail_season', info.get('season_title', ''))
        self.setProperty('detail_plot', info.get('plot', ''))
        self.setProperty('detail_fanart', info.get('fanart', ''))
        self.setProperty('detail_logo', info.get('logo', ''))
        self.setProperty('detail_meta', '  ·  '.join(x for x in meta if x))
        self.setProperty('detail_genres', ', '.join(info.get('genres') or []))
        self.setProperty('detail_message', info.get('message', ''))

    def _fill_detail(self, info):
        buttons = []
        feature = info.get('feature')
        episodes = info.get('episodes') or []
        if info['mediatype'] == 'movie':
            if feature and feature['resume'] > 0:
                buttons.append(('resume', getString(30345) % _format_time(feature['resume'])))
                buttons.append(('start', getString(30332)))
            elif feature or info.get('entitled'):
                # titles that have to be rented, bought or subscribed first have no playback action
                buttons.append(('play', getString(30330)))
        elif episodes:
            resume = next((e for e in episodes if 0 < e['progress'] < 95), None)
            if resume:
                buttons.append(('episode', getString(30356) % (resume['episode'], _format_time(resume['resume']))))
            else:
                buttons.append(('episode', getString(30357) % episodes[0]['episode']))
        if info.get('offers'):
            buttons.append(('offers', getString(30362)))
        if info.get('trailer'):
            buttons.append(('trailer', getString(30333)))
        if info.get('refund'):
            buttons.append(('refund', getString(30381)))
        if info.get('watchlist'):
            buttons.append(('watchlist', getString(30335 if info.get('in_watchlist') else 30334)))
        self._buttons = [b[0] for b in buttons]
        control = self.getControl(BUTTON_LIST)
        control.reset()
        for action, label in buttons:
            list_item = xbmcgui.ListItem(label=label, offscreen=True)
            list_item.setProperty('button', action)
            control.addItem(list_item)

        seasons = info.get('seasons') or []
        control = self.getControl(SEASON_LIST)
        control.reset()
        if len(seasons) > 1:
            control.addItems([self._season_listitem(s) for s in seasons])
            selected = next((i for i, s in enumerate(seasons) if s.get('selected')), 0)
            control.selectItem(selected)
        else:
            info['seasons'] = []
        self.setProperty('has_seasons', 'true' if info['seasons'] else '')

        control = self.getControl(EPISODE_LIST)
        control.reset()
        control.addItems([self._make_listitem(e) for e in episodes])
        self.setProperty('has_episodes', 'true' if episodes else '')

        restore = self._restore
        if restore and restore[0] in DETAIL_LISTS:
            self._restore = None
            target = self.getControl(restore[0])
            if target.size():
                target.selectItem(min(restore[1], target.size() - 1))
                self.setProperty('detail_focus', 'episodes' if restore[0] == EPISODE_LIST else '')
                self.setFocusId(restore[0])
                return
        self.setFocusId(BUTTON_LIST)

    @staticmethod
    def _season_listitem(season):
        list_item = xbmcgui.ListItem(label=season['label'], offscreen=True)
        list_item.setProperty('active', 'true' if season.get('selected') else '')
        return list_item

    def _on_season(self):
        seasons = (self._detail or {}).get('seasons') or []
        position = self.getControl(SEASON_LIST).getSelectedPosition()
        if not 0 <= position < len(seasons) or seasons[position].get('selected'):
            return
        view = self._stack[-1]
        view['url'] = seasons[position]['url']
        view['detail'] = None
        self._restore = (SEASON_LIST, position, 0)
        self._show_detail(view)

    def _on_button(self):
        info = self._detail
        position = self.getControl(BUTTON_LIST).getSelectedPosition()
        if not info or not 0 <= position < len(self._buttons):
            return
        action = self._buttons[position]
        title = info['title']
        if action in ('play', 'resume', 'start'):
            feature = info.get('feature') or {}
            resume = feature.get('resume', 0) if action == 'resume' else 0
            self._play(info['asin'], title, resume=resume, rating=info['rating'])
        elif action == 'episode':
            episodes = info['episodes']
            episode = next((e for e in episodes if 0 < e['progress'] < 95), episodes[0])
            self._play(episode['asin'], episode['label'], resume=episode['resume'], rating=episode['rating'])
        elif action == 'trailer':
            self._play(info['asin'], title, streamtype=1, resume=0, rating=info['rating'])
        elif action in ('offers', 'refund'):
            done = purchase.choose_and_purchase(info, info['offers']) if action == 'offers' else purchase.cancel_purchase(info)
            if done:
                # reload the page, it now offers playback
                self._cache = {}
                self._stack[-1]['detail'] = None
                self._restore = (BUTTON_LIST, 0, 0)
                self._show_detail(self._stack[-1])
        elif action == 'watchlist':
            add = not info.get('in_watchlist')
            if data.watchlist_toggle(info['watchlist'], add):
                info['in_watchlist'] = add
                self._cache = {}
                label = getString(30335 if add else 30334)
                self.getControl(BUTTON_LIST).getListItem(position).setLabel(label)
                xbmcgui.Dialog().notification(_g.__plugin__, getString(30358 if add else 30359), time=2000, sound=False)

    # Items

    def _make_listitem(self, item):
        list_item = xbmcgui.ListItem(label=item['label'], label2=item['subtitle'], offscreen=True)
        thumb = item['thumb'] or item['fanart']
        list_item.setArt({'thumb': thumb, 'icon': thumb, 'fanart': item['fanart'] or thumb, 'clearlogo': item['logo']})
        properties = {
            'kind': item['kind'],
            'tile': 'image' if thumb else 'text',
            'message': item['message'],
            'meta': '  ·  '.join(x for x in (item['year'], item['runtime'],
                                                  item['rating'] and getString(30355) % item['rating']) if x),
            'progress': str(item['progress']) if item['progress'] else '',
            'live': 'true' if item['kind'] == data.KIND_STATION else '',
            'logo': item['logo'],
            'overlay': 'true' if item['overlay'] else '',
        }
        for key, value in properties.items():
            list_item.setProperty(key, value)
        info_tag = list_item.getVideoInfoTag()
        info_tag.setTitle(item['label'])
        info_tag.setPlot(item['plot'])
        if item['rating']:
            info_tag.setMpaa(item['rating'])
        if item['duration']:
            info_tag.setDuration(int(item['duration']))
        if item['mediatype'] in ('movie', 'tvshow', 'episode'):
            info_tag.setMediaType(item['mediatype'])
        if item.get('show_title'):
            info_tag.setTvShowTitle(item['show_title'])
        if item.get('episode'):
            info_tag.setEpisode(int(item['episode']))
        if item['progress']:
            # drives ListItem.PercentPlayed of the progress bar, the duration is not always known
            info_tag.setResumePoint(float(item['progress']), 100.0)
        return list_item

    def _focused_item(self, control_id):
        if control_id == GRID_PANEL:
            items = self._grid['items'] if self._grid else []
        elif control_id in ROW_LISTS:
            items = self._row_data.get(control_id) or []
        elif control_id == EPISODE_LIST:
            items = (self._detail or {}).get('episodes') or []
        else:
            return None
        position = self.getControl(control_id).getSelectedPosition()
        return items[position] if 0 <= position < len(items) else None

    def _activate(self, item):
        kind = item['kind']
        if kind == data.KIND_ACTION:
            self._run_action(item)
        elif kind == data.KIND_ALL:
            self._open_all(item)
        elif kind == data.KIND_NEXT:
            view = self._stack[-1]
            self._open_grid(view['title'], view['source'], view['page'] + 1)
        elif kind == data.KIND_BACK:
            self._close_view()
        elif kind == data.KIND_LINK:
            self._open_page(item)
        elif kind == data.KIND_TITLE:
            self._open_detail(item)
        elif kind == data.KIND_EPISODE:
            self._play_with_resume(item)
        elif kind == data.KIND_STATION:
            self._play(item['asin'], item['label'], rating=item['rating'], streamtype=2, resume=0)
        elif kind == data.KIND_EVENT:
            self._play(item['asin'], item['label'], rating=item['rating'], streamtype=3, resume=0)

    def _context_menu(self, item):
        kind = item['kind']
        entries = []
        if kind in (data.KIND_TITLE, data.KIND_EPISODE):
            if item['resume'] > 0:
                entries.append((getString(30345) % _format_time(item['resume']), lambda: self._play(item['asin'], item['label'], rating=item['rating'], resume=item['resume'])))
                entries.append((getString(30332), lambda: self._play(item['asin'], item['label'], rating=item['rating'], resume=0)))
            if kind == data.KIND_TITLE:
                entries.insert(0, (getString(30360), lambda: self._open_detail(item)))
                entries.append((getString(30333), lambda: self._play(item['asin'], item['label'], rating=item['rating'], streamtype=1, resume=0)))
                if item['watchlist']:
                    entries.append((getString(30334), lambda: self._toggle_watchlist(item, True)))
                    if self._current_tab == TAB_WATCHLIST and not self._stack:
                        entries.append((getString(30335), lambda: self._toggle_watchlist(item, False)))
            else:
                entries.insert(0, (getString(30330), lambda: self._play(item['asin'], item['label'], rating=item['rating'], resume=0)))
        elif kind == data.KIND_ACTION and item['action'] == 'search_query':
            entries.append((getString(30361), lambda: self._remove_search(item['label'])))
        if not entries:
            return
        choice = xbmcgui.Dialog().contextmenu([label for label, _ in entries])
        if choice >= 0:
            entries[choice][1]()

    def _toggle_watchlist(self, item, add):
        if data.watchlist_toggle(item['watchlist'], add):
            self._cache = {}
            xbmcgui.Dialog().notification(_g.__plugin__, getString(30358 if add else 30359), time=2000, sound=False)
            if not add and self._current_tab == TAB_WATCHLIST and not self._stack:
                self._remember_focus()
                self._show_rows(lambda: self._tab_rows(TAB_WATCHLIST))

    def _run_action(self, item):
        action = item['action']
        if action == 'search':
            query = xbmcgui.Dialog().input(getString(30347)).strip()
            if query:
                self._search(query)
        elif action == 'search_query':
            self._search(item['label'])
        elif action == 'settings':
            _g.addon.openSettings()
        elif action == 'classic':
            xbmc.executebuiltin(f'ActivateWindow(Videos,{_g.pluginid}?classic=1,return)')
        elif action == 'refresh':
            self._cache = {}
            self._load_root()

    def _search(self, query):
        history = [q for q in json.loads(getConfig('search_history', '[]')) if q.lower() != query.lower()]
        writeConfig('search_history', json.dumps([query] + history[:14]))
        self._open_grid(query, self._new_source(data.search_url(query)))

    def _remove_search(self, query):
        history = [q for q in json.loads(getConfig('search_history', '[]')) if q != query]
        writeConfig('search_history', json.dumps(history))
        self._show_rows(lambda: self._tab_rows(TAB_SEARCH))

    # Playback

    def _play_with_resume(self, item):
        if item['resume'] > 0 and item['progress'] < 95:
            choice = xbmcgui.Dialog().contextmenu([getString(30345) % _format_time(item['resume']), getString(30332)])
            if choice < 0:
                return
            self._play(item['asin'], item['label'], rating=item['rating'], resume=item['resume'] if choice == 0 else 0)
        else:
            self._play(item['asin'], item['label'], rating=item['rating'], resume=0)

    def _play(self, asin, name, streamtype=0, resume=-1, rating=''):
        if not asin:
            return
        self._remember_focus()
        # the age rating decides whether playback asks for a PIN (parental.py)
        query = urlencode({'mode': 'PlayVideo', 'asin': asin, 'name': name, 'adult': '0', 'trailer': streamtype,
                           'selbitrate': '0', 'resume': resume, 'rating': rating or ''})
        Log(f'[Home] Playing {asin} (type {streamtype}, resume {resume})', Log.DEBUG)
        xbmc.executebuiltin(f'PlayMedia({_g.pluginid}?{query})')

    def _on_playback_end(self):
        if not self._refresh_pending or self._closing:
            return
        if xbmc.Monitor().waitForAbort(PLAYBACK_END_DELAY):
            return
        if self._closing or xbmc.getCondVisibility('Player.HasMedia') or xbmcgui.getCurrentWindowId() != self._window_id:
            return
        self._refresh_pending = False
        self._refresh_view()

    def _refresh_view(self):
        """ Reload the current view, e.g. the progress of "continue watching" after playback """
        self._cache = {}
        if not self._restore:
            self._remember_focus()
        if self._stack:
            view = self._stack[-1]
            if view['type'] == 'detail':
                view['detail'] = None
            self._render(view)
        elif self._current_tab:
            self._show_rows(lambda: self._tab_rows(self._current_tab), self._tab_url(self._current_tab))

    # Helpers

    def _remember_focus(self):
        focus_id = self.getFocusId()
        if focus_id in ROW_LISTS + (GRID_PANEL,) + DETAIL_LISTS:
            self._restore = (focus_id, self.getControl(focus_id).getSelectedPosition(), self._row_offset)
        else:
            self._restore = None

    def _restore_focus(self, control_id, control, num_items):
        restore = self._restore
        if not restore or restore[0] != control_id:
            return False
        self._restore = None
        position = min(restore[1], num_items - 1)
        if position >= 0:
            control.selectItem(position)
        if control_id in ROW_LISTS:
            self._set_row_offset(ROW_LISTS.index(control_id))
        self.setFocusId(control_id)
        return True

    @staticmethod
    def _user_name():
        from ..users import loadUser
        try:
            return loadUser('name')
        except Exception:
            return ''

    def _stale(self, generation):
        return self._closing or generation != self._generation

    def _start(self, target, *args):
        def _run():
            try:
                target(*args)
            except Exception as e:
                import traceback
                Log(f'[Home] Task failed: {e}\n{traceback.format_exc()}', Log.ERROR)
                self.setProperty('loading', '')

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()
        return thread


class _PlayerEvents(xbmc.Player):
    def __init__(self, on_playback_end):
        super(_PlayerEvents, self).__init__()
        self._on_playback_end = on_playback_end

    def _notify(self):
        thread = threading.Thread(target=self._on_playback_end)
        thread.daemon = True
        thread.start()

    def onPlayBackEnded(self):
        self._notify()

    def onPlayBackStopped(self):
        self._notify()

    def onPlayBackError(self):
        self._notify()


def _activate_running_window(value, timeout=3):
    """ Bring a running home window to the front, False if it is not running """
    if value.startswith('starting:'):
        try:
            return time.time() - float(value.partition(':')[2]) < 30
        except ValueError:
            return False
    if not value.isdigit():
        return False
    xbmc.executebuiltin(f'ActivateWindow({value})')
    monitor = xbmc.Monitor()
    for _ in range(timeout * 10):
        if xbmc.getCondVisibility(f'Window.IsActive({value})'):
            return True
        if monitor.waitForAbort(0.1):
            break
    return False


def open_home_window():
    home = xbmcgui.Window(10000)
    running = home.getProperty(WINDOW_PROPERTY)
    if running and _activate_running_window(running):
        return
    home.setProperty(WINDOW_PROPERTY, f'starting:{time.time()}')
    try:
        window = HomeWindow('script-amazon-home.xml', _g.PLUGIN_PATH, 'default', '1080i')
        window.doModal()
        del window
    finally:
        home.clearProperty(WINDOW_PROPERTY)
