#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Data layer of the TV interface.

The pages of the Prime Video website are requested as JSON. A landing page
already contains every row with its titles, images and descriptions, so a tab
needs a single request, unlike the classic menu which opens the detail page of
every title. Detail pages are only loaded when a title is opened.
"""
import json
import re
from urllib.parse import quote_plus, urlencode, urlparse, parse_qs

from ..common import Globals, findKey
from ..logging import Log
from ..network import getURL, GrabJSON, FQify

_g = Globals()

JSON_HEADERS = {'accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}

# Item kinds
KIND_TITLE = 'title'      # movie or show, opens the detail page
KIND_EPISODE = 'episode'  # playable episode
KIND_STATION = 'station'  # live TV channel, playable
KIND_EVENT = 'event'      # live event, playable
KIND_LINK = 'link'        # category or collection, opens a page with rows
KIND_ALL = 'all'          # "show all" tile at the end of a row
KIND_NEXT = 'next'        # next grid page
KIND_BACK = 'back'        # previous grid page
KIND_ACTION = 'action'    # built-in tile (search, settings, classic menu)

ROW_MAX_ITEMS = 20
PAGINATION_PARAMS = ('pageSize=20&pageType=browse&pageId=default&variant=desktopWindows&actionScheme=default&payloadScheme=default'
                     '&decorationScheme=web-search-decoration-tournaments-v2&featureScheme=web-search-v4'
                     '&dynamicFeatures=HorizontalPagination&widgetScheme=web-explorecs-v11')
_URN_RE = re.compile(r'/d(?:p|etail)/([^/?]+)')
_SIZE_RE = re.compile(r'\._[^/]*_\.')


def fetch(url, postdata=None):
    """ Request a Prime Video page as JSON, fall back to the HTML parser of the add-on (e.g. for captchas) """
    url = FQify(url)
    response = getURL(url, useCookie=True, rjson=False, headers=JSON_HEADERS, postdata=postdata, silent=True)
    if response:
        response = response.strip()
        if response.startswith('{'):
            try:
                return json.loads(response)
            except ValueError as e:
                Log(f'[Home] Invalid JSON from {url}: {e}', Log.ERROR)
    Log(f'[Home] No JSON from {url}, using the page parser', Log.DEBUG)
    return GrabJSON(url) or {}


THUMB_WIDTH = 640
FANART_WIDTH = 1920


def image(url, width=THUMB_WIDTH):
    """ Image URL scaled to a width, the originals are 4K images of several MB """
    if not url:
        return ''
    url = _SIZE_RE.sub('.', url)
    base, dot, ext = url.rpartition('.')
    if not dot or '/' in ext:
        return url
    return f'{base}._SX{width}_.{ext}'


def text(value):
    """ Clean up descriptions: characters missing in Kodi's fonts """
    return (value or '').replace('\u30fb', ' \u00b7 ').replace('\u2028', '\n').replace('\u2029', '\n').strip()


def video_path():
    """ Path prefix of the video pages, primevideo.com has none """
    return '' if _g.UsePrimeVideo else '/gp/video'


def asin_from_url(url):
    match = _URN_RE.search(url or '')
    return match.group(1) if match else ''


def _text(value):
    """ Strings are sometimes wrapped as {'string': ...} """
    if isinstance(value, dict):
        return value.get('string') or value.get('text') or ''
    return value or ''


def _focus_message(cues):
    for key in ('focusMessage', 'glanceMessage', 'compactFocusMessage'):
        message = _text((cues or {}).get(key, {}).get('message'))
        if message:
            return message
    return ''


def make_item(kind, label='', **kwargs):
    item = {
        'kind': kind,
        'label': label,
        'subtitle': '',
        'asin': '',
        'url': '',
        'thumb': '',
        'fanart': '',
        'logo': '',
        'plot': '',
        'year': '',
        'runtime': '',
        'duration': 0,
        'rating': '',
        'message': '',
        'entitled': False,
        'progress': 0,
        'resume': 0,
        'mediatype': '',
        'watchlist': None,
        'more': None,
        'row_title': '',
        'action': '',
    }
    item.update(kwargs)
    return item


def parse_entity(entity):
    """ Convert a title card of a landing page into an item, None if it is not supported """
    images = entity.get('images') or {}
    cover = (images.get('cover') or images.get('boxart') or {}).get('url', '')
    hero = (images.get('hero') or {}).get('url', '')
    logo = (images.get('titleLogo') or {}).get('url', '')
    title = entity.get('displayTitle') or _text(entity.get('title')) or entity.get('alternateText') or ''
    link = (entity.get('link') or {}).get('url', '')
    cues = entity.get('entitlementCues') or {}

    if 'station' in entity:
        station = entity['station']
        channel = (entity.get('playbackAction') or {}).get('channelId') or station.get('id')
        if not channel:
            return None
        now = {}
        for show in station.get('schedule') or []:
            badge = (show.get('metadata') or {}).get('linearBadge') or {}
            if badge.get('isLive') or 'NOW' in str(badge.get('label', '')).upper():
                now = show.get('metadata') or {}
                break
        programme = now.get('title') or ''
        episode = now.get('linearEpisodeTitle') or ''
        return make_item(KIND_STATION, station.get('name') or title,
                         subtitle=programme,
                         asin=channel,
                         thumb=image((now.get('image') or {}).get('url')) or station.get('logo', ''),
                         fanart=image((now.get('image') or {}).get('url'), FANART_WIDTH),
                         logo=station.get('logo', ''),
                         plot=text('\n'.join(x for x in (programme, episode, now.get('synopsis', '')) if x)),
                         message=now.get('localizedTimeRange') or now.get('localizedStartTime') or '',
                         mediatype='live',
                         entitled=True)

    entity_type = (entity.get('entityType') or '').lower()
    widget = (entity.get('widgetType') or '').lower()

    if widget == 'imagetextlink' or (not entity_type and link and not entity.get('titleID')):
        if not link:
            return None
        return make_item(KIND_LINK, title, url=link, thumb=image(cover or hero), fanart=image(hero or cover, FANART_WIDTH), logo=logo)

    if not link and not entity.get('titleID'):
        return None

    asin = entity.get('titleID') or asin_from_url(link)
    item = make_item(KIND_TITLE, title,
                     asin=asin,
                     url=link or f'{video_path()}/detail/{asin}',
                     thumb=image(cover or hero),
                     fanart=image(hero or cover, FANART_WIDTH),
                     logo=logo,
                     plot=text(entity.get('synopsis')),
                     year=str(entity.get('releaseYear') or ''),
                     runtime=entity.get('runtime') or '',
                     rating=(entity.get('maturityRatingBadge') or {}).get('displayText', ''),
                     message=_focus_message(cues),
                     entitled=cues.get('entitlementType', '').lower() == 'entitled',
                     mediatype='tvshow' if 'tv' in entity_type else 'movie',
                     watchlist=(entity.get('watchlistAction') or {}).get('endpoint'))

    if entity_type == 'event' or 'liveInfo' in entity:
        item['kind'] = KIND_EVENT
        item['mediatype'] = 'event'

    playback = (entity.get('playbackActions') or [None])[0] or {}
    if entity.get('progress'):
        item['progress'] = int(entity['progress'].get('percentage') or 0)
        item['resume'] = int(playback.get('resumeTime') or 0)
    item['subtitle'] = ' · '.join(x for x in (item['year'], item['runtime']) if x)
    return item


def _containers(data):
    """ Find the list of rows in the different page layouts """
    body = data.get('body', data)
    for path in (('containers',), ('content', 'baseOutput', 'containers'), ('results', 'containers')):
        node = body
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list):
            return node
    found = findKey('containers', body)
    return found if isinstance(found, list) else []


def pagination_url(container):
    """ URL of the next items of a row (paginateCollection), None if there are no more """
    if 'paginationTargetId' not in container:
        return None
    query = []
    for key, value in container.items():
        if key in ('collectionType', 'paginationServiceToken', 'paginationTargetId', 'tags', 'paginationStartIndex'):
            name = key.replace('paginationServiceToken', 'serviceToken').replace('paginationStartIndex', 'startIndex')
            query.append(f"{name}={','.join(value) if isinstance(value, list) else quote_plus(str(value))}")
    if 'collectionType' not in container:
        query.append('collectionType=Container')
    return f'{video_path()}/api/paginateCollection?' + '&'.join(query) + '&' + PAGINATION_PARAMS


def next_page_url(data, url):
    """ URL of the page following a paginateCollection or search response """
    if not data.get('hasMoreItems'):
        return None
    params = (data.get('pagination') or {}).get('queryParameters') or {}
    if 'serviceToken' in params and 'targetId' in params:
        query = {'serviceToken': params['serviceToken'], 'startIndex': params.get('startIndex', ''),
                 'paginationTargetId': params['targetId'], 'collectionType': 'Container'}
        return f'{video_path()}/api/paginateCollection?' + urlencode(query) + '&' + PAGINATION_PARAMS
    match = re.search(r'startIndex=(\d+)', url)
    if match:
        index = int(match.group(1))
        return url.replace(f'startIndex={index}', f'startIndex={index + 20}')
    return None


def page_rows(data, hero_title=''):
    """ Rows of a landing, category, search or watchlist page """
    rows = []
    for container in _containers(data):
        entities = container.get('entities') or container.get('items') or []
        items = [item for item in (parse_entity(e) for e in entities if isinstance(e, dict)) if item]
        if not items:
            continue
        ctype = (container.get('containerType') or '').lower()
        title = _text(container.get('title')) or container.get('text') or ''
        if 'hero' in ctype:
            title = hero_title
        more = pagination_url(container)
        see_more = (container.get('seeMore') or {}).get('link', {}).get('url') or ''
        rows.append({'title': title, 'items': items, 'more': more, 'see_more': see_more,
                     'grid': ctype == 'grid', 'continue': bool(container.get('isContinueWatching'))})
    return rows


def grid_page(url, postdata=None):
    """ Items and next page URL of a "show all" grid, a search or the watchlist """
    data = fetch(url, postdata)
    if 'entities' in data:
        items = [item for item in (parse_entity(e) for e in data['entities']) if item]
        return items, next_page_url(data, url)
    items = []
    next_url = None
    for row in page_rows(data):
        items.extend(row['items'])
        next_url = next_url or row['more']
    # a search page continues with startIndex
    body = data.get('body', data)
    if not next_url and body.get('pagination', {}).get('queryParameters'):
        next_url = _search_next_page(url, body['pagination'])
    return items, next_url


def _search_next_page(url, pagination):
    params = pagination.get('queryParameters') or {}
    if not pagination.get('page') or 'phrase' not in url:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query['startIndex'] = [str(params.get('startIndex', (int(pagination['page']) - 1) * int(pagination.get('pageSize', 20))))]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def search_url(phrase):
    return f'{video_path()}/search?phrase=' + quote_plus(phrase)


# Detail pages

def _states(data):
    body = data.get('body', data)
    return [body[part]['state'] for part in ('atf', 'btf') if isinstance(body.get(part), dict) and 'state' in body[part]]


def _merged(states, *keys):
    """ Merge a dictionary found in the atf and btf states """
    merged = {}
    for state in states:
        node = state
        for key in keys:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k].update(v)
                else:
                    merged.setdefault(k, v)
    return merged


def _playbacks(actions):
    """ Feature and trailer playback payloads of a title's actions """
    feature = trailer = None
    for action in (actions or {}).get('primaryActions', []) + (actions or {}).get('secondaryActions', []):
        payloads = [action.get('payload', {})]
        payloads += [a.get('payload', {}) for a in (action.get('payload', {}).get('expandingCard') or {}).get('actions', [])]
        for payload in payloads:
            playback = payload.get('playback')
            if not playback:
                continue
            if playback.get('isTrailer') or playback.get('videoMaterialType') == 'Trailer':
                trailer = trailer or playback
            else:
                feature = feature or playback
    return feature, trailer


_MARKUP_RE = re.compile(r'\{(?:strike)\}.*?\{end\}|\{[a-zA-Z]+\}')


def _offers(actions):
    """ Rent and buy offers of a title, one per edition and quality """
    offers = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('actionType') == 'TRANSACT':
                transaction = (node.get('payload') or {}).get('transaction') or {}
                ref = transaction.get('refMarker', '')
                kind = 'rent' if '_rent_' in ref else 'buy' if '_buy_' in ref and '_mis_' not in ref else None
                label = ' '.join(_MARKUP_RE.sub(' ', transaction.get('label', '')).split())
                if kind and transaction.get('asin') and transaction.get('offerToken'):
                    # the buy box repeats offers of the "more ways to watch" list with longer labels
                    for offer in offers:
                        if offer['asin'] == transaction['asin'] and offer['kind'] == kind:
                            break
                    else:
                        offers.append({'kind': kind, 'label': label, 'asin': transaction['asin'],
                                       'offer_token': transaction['offerToken'],
                                       'purchase_data': transaction.get('purchaseData', ''),
                                       'csrf_token': transaction.get('csrfToken', ''),
                                       'csrf_workflow': transaction.get('csrfTokenWorkflow', ''),
                                       'ref': ref})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(actions or {})
    # short labels ("HD €3.99") without the action word, sorted rent first and by price
    for offer in offers:
        offer['label'] = re.sub(r'^(?:Rent|Buy movie|Buy season|Buy episode|Buy|Leihen|Kaufen)\s*', '', offer['label'], flags=re.I)
    quality = {'SD': 0, 'HD': 1, 'UHD': 2}
    return sorted(offers, key=lambda o: (o['kind'] != 'rent', quality.get(o['label'].split(' ')[0], 3), _price(o['label'])))


def _price(label):
    prices = re.findall(r'(\d+[.,]\d{2})', label)
    return float(prices[-1].replace(',', '.')) if prices else 0.0


def _entitlement(actions):
    messages = (actions or {}).get('messages') or {}
    return messages.get('entitlementType', '').lower() == 'entitled', _text((messages.get('focusMessage') or {}).get('dvMessage'))


def detail(url):
    """ Everything shown on the detail page of a movie or a season """
    data = fetch(url)
    states = _states(data)
    if not states:
        return None
    tid = states[0].get('pageTitleId') or asin_from_url(url)
    header = _merged(states, 'detail', 'headerDetail').get(tid) or {}
    details = _merged(states, 'detail', 'detail')
    actions = _merged(states, 'action', 'atf')
    actions.update(_merged(states, 'action', 'btf'))
    watchlist = _merged(states, 'watchlist').get(tid) or {}
    images = header.get('images') or {}
    feature, trailer = _playbacks(actions.get(tid))
    entitled, message = _entitlement(actions.get(tid))
    title_type = (header.get('titleType') or '').lower()
    title = header.get('parentTitle') or header.get('title') or ''

    page_context = next((s.get('pageContext') for s in states if s.get('pageContext')), {}) or {}
    info = {
        'asin': tid,
        'url': url,
        'gti': (_merged(states, 'self').get(tid) or {}).get('gti', ''),
        'sub_page_type': page_context.get('subPageType', ''),
        'title': title,
        'season_title': header.get('title', '') if title_type == 'season' else '',
        'mediatype': 'season' if title_type == 'season' else 'movie',
        'plot': text(header.get('synopsis')),
        'year': str(header.get('releaseYear') or ''),
        'runtime': header.get('runtime') or '',
        'duration': int(header.get('duration') or 0),
        'rating': (header.get('ratingBadge') or {}).get('displayText', ''),
        'genres': [g.get('text', '') for g in header.get('genres') or [] if isinstance(g, dict)],
        'fanart': image(images.get('heroshot') or images.get('covershot'), FANART_WIDTH),
        'thumb': image(images.get('covershot') or images.get('heroshot')),
        'poster': image(images.get('packshot') or images.get('titleshot')),
        'logo': images.get('titleLogo') or '',
        'uhd': bool(header.get('isUhd')),
        'entitled': entitled,
        'message': message,
        'feature': _play_info(feature),
        'trailer': bool(trailer),
        'offers': [] if entitled else _offers(actions.get(tid)),
        # purchases Amazon allows to cancel, the website only shows its cancel link if there are any
        'refund': (_merged(states, 'refund', 'fragments').get(tid) or {}).get('refundableOrders') or [],
        'watchlist': watchlist.get('endpoint'),
        'in_watchlist': (watchlist.get('tag') or '').lower() == 'remove',
        'seasons': [],
        'episodes': [],
    }

    if title_type == 'season':
        info['seasons'] = _seasons(states, tid)
        info['episodes'] = _episodes(states, details, actions, info)
    return info


def _play_info(playback):
    if not playback:
        return None
    return {'asin': playback.get('playbackID') or playback.get('titleID') or '',
            'label': playback.get('label', ''),
            'resume': int(playback.get('resumeTime') or 0),
            'runtime': int(playback.get('runTime') or 0),
            'progress': int(float(playback.get('progress') or 0) * 100)}


def _seasons(states, tid):
    seasons = []
    for state in states:
        for entry in (state.get('seasons') or {}).get(tid, []):
            label = entry.get('displayName') or _text(entry.get('displayText')) or str(entry.get('sequenceNumber', ''))
            link = entry.get('seasonLink') or entry.get('link') or ''
            asin = entry.get('seasonId') or asin_from_url(link)
            if link and all(s['asin'] != asin for s in seasons):
                seasons.append(make_item(KIND_LINK, label, url=link, asin=asin,
                                         selected=bool(entry.get('isSelected')) or asin == tid))
    return seasons


def _episodes(states, details, actions, season):
    ids = []
    for state in states:
        for tid in (state.get('episodeList') or {}).get('cardTitleIds', []):
            if tid not in ids:
                ids.append(tid)
    episodes = []
    for tid in ids:
        ep = details.get(tid)
        if not ep:
            continue
        images = ep.get('images') or {}
        feature, _ = _playbacks(actions.get(tid))
        number = ep.get('episodeNumber') or 0
        play = _play_info(feature) or {}
        label = ep.get('title', '')
        entitled, message = _entitlement(actions.get(tid))
        episodes.append(make_item(
            KIND_EPISODE, label,
            subtitle=' · '.join(x for x in (f'E{number}' if number else '', ep.get('runtime', '')) if x),
            asin=tid,
            thumb=image(images.get('covershot') or images.get('packshot')) or season['thumb'],
            fanart=season['fanart'],
            logo=season['logo'],
            plot=text(ep.get('synopsis')),
            year=str(ep.get('releaseYear') or ''),
            runtime=ep.get('runtime', ''),
            duration=int(ep.get('duration') or 0),
            rating=season['rating'],
            message=message or season['message'],
            entitled=entitled or season['entitled'],
            progress=play.get('progress', 0),
            resume=play.get('resume', 0),
            mediatype='episode',
            episode=number,
            show_title=season['title'],
            season_title=season['season_title']))
    return episodes


def watchlist_toggle(endpoint, add):
    """ Add a title to or remove it from the watchlist """
    query = dict(endpoint.get('query') or {})
    query['tag'] = 'Add' if add else 'Remove'
    return bool(getURL(FQify(endpoint['partialURL']), postdata=query, useCookie=True, check=True))
