#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Renting and buying titles from the TV interface.

The website purchases through a server driven workflow: the first request to
/gp/video/acquisition/workflow/actions starts it with the offer of the detail
page, every response describes the next dialog (confirmation with the price,
Amazon's purchase PIN, resolution hints, notifications) or ends the workflow
with a callback. The answer to a dialog is sent as the next request. This
module shows these dialogs with Kodi's dialogs, following the website's
checkout code (DVWebClient checkout-purchase-flow).

Before a purchase is started, the purchase PIN of the add-on is asked. It is
stored as a salted hash in the add-on's config folder.
"""
import hashlib
import json
import os
import re

import xbmcgui

from ..common import Globals
from ..configs import getConfig, writeConfig
from ..l10n import getString
from ..logging import Log
from ..network import getURL, FQify

_g = Globals()

PURCHASE_ENABLED = True
PIN_CONFIG = 'purchase_pin'
MAX_PIN_ATTEMPTS = 3
MAX_WORKFLOW_STEPS = 10
WEB_DEVICE_TYPE = 'AOAGZA014O5RE'

# workflow response: task type of the final callback and the dialog layouts
TASK_CALLBACK = 'Callback'
CANCEL_ACTION = 'CANCEL'
LAYOUT_CONFIRMATION = 'confirmationdialog'
LAYOUT_PIN = 'pindialog'
LAYOUT_NOTIFICATION = 'notificationdialog'
LAYOUT_RESOLUTION = 'resolutionsupport'
LAYOUT_REFUND = 'refundconfirmationdialog'
CALLBACKS_SUCCESS = ('refreshAfterDone', 'callbackRefundSuccess')
CALLBACKS_CANCEL = ('callbackCancel', 'resetAfterCancel')
CALLBACK_CSRF = 'callbackCSRF'

RESULT_PURCHASED = 'purchased'
RESULT_CANCELLED = 'cancelled'
RESULT_FAILED = 'failed'


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
        return False
    dialog = xbmcgui.Dialog()
    pin = dialog.numeric(0, getString(30369), bHiddenInput=True)
    if not pin:
        return False
    if dialog.numeric(0, getString(30370), bHiddenInput=True) != pin:
        dialog.ok(_g.__plugin__, getString(30372))
        return False
    salt = os.urandom(16).hex()
    writeConfig(PIN_CONFIG, f'{salt}:{_hash(pin, salt)}')
    dialog.notification(_g.__plugin__, getString(30375), time=2000, sound=False)
    return True


def remove_pin():
    if pin_is_set() and check_pin():
        writeConfig(PIN_CONFIG, '')
        xbmcgui.Dialog().notification(_g.__plugin__, getString(30376), time=2000, sound=False)


def offer_label(offer):
    return getString(30363 if offer['kind'] == 'rent' else 30364) % offer['label']


def choose_and_purchase(info, offers):
    """ Let the user pick an offer and purchase it, True if the title was purchased """
    if not offers:
        return False
    dialog = xbmcgui.Dialog()
    choice = dialog.contextmenu([offer_label(offer) for offer in offers])
    if choice < 0:
        return False
    offer = offers[choice]
    if not PURCHASE_ENABLED:
        dialog.ok(info['title'], getString(30365) % _site())
        return False
    if not pin_is_set():
        # purchases require the PIN of the add-on, set it up on first use
        dialog.ok(getString(30366), getString(30377))
        if not set_pin():
            return False
    elif not check_pin():
        return False
    result = Workflow(info, offer).run()
    if result == RESULT_PURCHASED:
        dialog.notification(_g.__plugin__, getString(30378), time=3000, sound=False)
    return result == RESULT_PURCHASED


def cancel_purchase(info):
    """ Cancel a purchase of the title, True if it was cancelled """
    orders = info.get('refund') or []
    if not orders:
        return False
    dialog = xbmcgui.Dialog()
    order = orders[0]
    if len(orders) > 1:
        choice = dialog.select(getString(30383), [o.get('description') or o.get('orderId', '') for o in orders])
        if choice < 0:
            return False
        order = orders[choice]
    if order.get('singleRefundLink') and not _g.UsePrimeVideo:
        # Classic Amazon stores (amazon.de, ...) cancel in the returns assistant of the website. Amazon
        # refused every withdrawal sent with the add-on's login, and failed attempts may block the
        # withdrawal of the order, so the page is opened on the phone with a QR code instead.
        _show_refund_link(info, order)
        return False
    if order.get('csrfToken') and order.get('asin'):
        result = Workflow(info, refund_order=order).run()
        if result == RESULT_PURCHASED:
            dialog.notification(_g.__plugin__, getString(30382), time=3000, sound=False)
        elif result == RESULT_FAILED and order.get('singleRefundLink'):
            _show_refund_link(info, order)
        return result == RESULT_PURCHASED
    dialog.ok(getString(30381), getString(30380) % _site())
    return False


def _show_refund_link(info, order):
    """ Show a QR code with the cancellation page of the order """
    url = FQify(order['singleRefundLink'])
    try:
        path = _qr_image(url, order.get('orderId') or info['asin'])
    except Exception as e:
        Log(f'[Purchase] QR code failed: {e}', Log.ERROR)
        path = None
    if not path:
        xbmcgui.Dialog().textviewer(getString(30381), getString(30385) % (order.get('description', ''), url))
        return
    QRDialog(getString(30381), path, '[CR][CR]'.join(x for x in (
        getString(30392), f"[B]{info['title']}[/B][CR]{order.get('description', '')}", getString(30394)) if x)).doModal()


def _qr_image(url, key):
    """ PNG of a QR code in Kodi's temp folder, the file name changes with the content (texture cache) """
    from ..deps import segno
    from xbmcvfs import translatePath
    name = 'amazon_refund_' + hashlib.sha1((key + url).encode('utf-8')).hexdigest()[:16] + '.png'
    path = os.path.join(translatePath('special://temp/'), name)
    if not os.path.exists(path):
        segno.make(url, error='m', micro=False).save(path, kind='png', scale=10, border=4, dark='#000000', light='#ffffff')
    return path


class QRDialog(xbmcgui.WindowDialog):
    """ Full screen dialog with a QR code and a text, any key closes it """

    def __init__(self, title, image, text):
        super(QRDialog, self).__init__()
        media = os.path.join(_g.PLUGIN_PATH, 'resources', 'skins', 'default', 'media')
        # coordinates of a WindowDialog are based on 1280x720
        self.addControl(xbmcgui.ControlImage(0, 0, 1280, 720, os.path.join(media, 'pv_white.png'), colorDiffuse='0xF20F171E'))
        self.addControl(xbmcgui.ControlImage(150, 150, 420, 420, image, aspectRatio=2))
        self.addControl(xbmcgui.ControlLabel(640, 150, 560, 50, title, font='font30_title', textColor='0xFFFFFFFF'))
        box = xbmcgui.ControlTextBox(640, 215, 520, 300, font='font13', textColor='0xFFCCD6DD')
        self.addControl(box)
        box.setText(text)
        self.addControl(xbmcgui.ControlLabel(640, 530, 520, 40, getString(30393), font='font12', textColor='0xFF8197A4'))

    def onAction(self, action):
        self.close()


def _site():
    return _g.BaseUrl.replace('https://', '').replace('http://', '')


def _plain(text):
    """ The dialogs use markdown, Kodi shows plain text with [B] """
    text = text or ''
    text = re.sub(r'\*\*(.+?)\*\*', r'[B]\1[/B]', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.replace('\\n', '[CR]').replace('\n', '[CR]').strip()


class Workflow:
    """ Purchase workflow of one offer, see the module description """

    def __init__(self, info, offer=None, refund_order=None):
        self._title = info['title']
        endpoint = FQify(info['url'].split('?')[0])
        if refund_order:
            # the website sends ASINs as asin and other title ids as globalTitleIdentifier
            title_id = refund_order['asin']
            is_asin = re.match(r'^[A-Z0-9]{10}$', title_id) is not None
            self._workflow_type = 'Refund'
            self._client_data = {
                'asin': title_id if is_asin else None,
                'csrfToken': refund_order['csrfToken'],
                'globalTitleIdentifier': None if is_asin else title_id,
                'serviceOverrides': None,
                'currentEndpoint': endpoint,
            }
            return
        self._workflow_type = 'Commerce-TVOD'
        self._client_data = {
            'csrfToken': offer['csrf_workflow'],
            'offerToken': offer['offer_token'],
            'supportsWebPlayback': True,
            'supportsHDMovies': True,
            'supportsUHD': False,
            'subPageType': info.get('sub_page_type') or None,
            'titleId': info['asin'],
            'refTag': offer['ref'],
            'globalTitleIdentifier': info.get('gti') or None,
            'deviceTypeId': WEB_DEVICE_TYPE,
            'serviceOverrides': None,
            'currentEndpoint': endpoint,
        }

    def _request(self, step=None, init=False):
        # the action and its data (e.g. the PIN) are merged into the client data, like the website does
        client_data = {'action': step['action'], **(step.get('clientData') or {})} if step else {}
        client_data.update(self._client_data)
        client_data['dialogIngress'] = True
        # the website leaves out undefined values (JSON.stringify), only serviceOverrides is sent as null
        client_data = {k: v for k, v in client_data.items() if v is not None or k == 'serviceOverrides'}
        body = {'clientData': client_data, 'pageType': 'Detail', 'workflowType': self._workflow_type,
                'initRequest': init, 'refererURI': ''}
        Log(f"[Purchase] {self._workflow_type} request: {'start' if init else step.get('action')}")
        response = getURL(FQify('/gp/video/acquisition/workflow/actions'), useCookie=True, rjson=False, silent=True,
                          postdata=json.dumps(body),
                          headers={'Content-Type': 'application/json; charset=UTF-8', 'Accept': 'application/json',
                                   'X-Requested-With': 'XMLHttpRequest', 'Origin': _g.BaseUrl,
                                   'Referer': self._client_data['currentEndpoint']})
        try:
            return json.loads(response) if response else None
        except ValueError:
            Log(f'[Purchase] Invalid workflow response: {response[:200]}', Log.ERROR)
            return None

    def run(self):
        response = self._request(init=True)
        for _ in range(MAX_WORKFLOW_STEPS):
            contract = (response or {}).get('clientContract')
            if not isinstance(contract, dict):
                Log(f'[Purchase] {self._workflow_type}: unexpected response {str(response)[:300]!r}', Log.ERROR)
                return self._fail()
            task = contract.get('taskData') or {}
            if contract.get('taskType') == TASK_CALLBACK:
                return self._callback(task)
            step = self._dialog(task)
            if step is None:
                return self._fail()
            response = self._request(step)
            if step.get('action') == CANCEL_ACTION and not (response or {}).get('clientContract'):
                return RESULT_CANCELLED
        return self._fail()

    def _callback(self, task):
        name = ((task.get('templateData') or {}).get('callbacks') or {}).get(task.get('callbackId'), '')
        Log(f'[Purchase] {self._workflow_type} finished: {name or task.get("callbackId")}')
        if name in CALLBACKS_SUCCESS:
            return RESULT_PURCHASED
        if name in CALLBACKS_CANCEL:
            return RESULT_CANCELLED
        if name == CALLBACK_CSRF:
            # the tokens of the detail page are outdated, reloading the page renews them
            xbmcgui.Dialog().ok(self._title, getString(30379))
            return RESULT_FAILED
        view = task.get('viewData') or {}
        return self._fail(view.get('title'), view.get('message'))

    def _dialog(self, task):
        """ Show the dialog of a workflow step, returns the next step or None if it is not supported """
        view = task.get('viewData') or {}
        template = (task.get('templateData') or {}).get('widgetData') or {}
        layout = template.get('layoutType') or ''
        dialog = xbmcgui.Dialog()
        Log(f'[Purchase] {self._workflow_type} dialog: {layout or task.get("templateId")}')

        if layout == LAYOUT_CONFIRMATION:
            text = [view.get('messageBody'), view.get('messageBodySubtext'), view.get('submitButtonDetails'), view.get('messageFooterText')]
            confirmed = dialog.yesno(_plain(view.get('messageBoxHeading')) or getString(30373),
                                     '[CR]'.join(_plain(t) for t in text if t),
                                     nolabel=_plain(view.get('secondaryButtonText')) or getString(222),
                                     yeslabel=_plain(view.get('submitButtonText')))
            if confirmed:
                return {'action': template['primaryAction']}
            return {'action': template.get('secondaryAction') or CANCEL_ACTION}

        if layout == LAYOUT_PIN:
            # the purchase PIN of the Amazon account (Prime Video settings)
            heading = _plain(view.get('messageBoxHeading'))
            pin = dialog.numeric(0, heading or _plain(view.get('preInputText')), bHiddenInput=True)
            if not pin:
                return {'action': CANCEL_ACTION}
            return {'action': template['submitAction'], 'clientData': {'pin': pin}}

        if layout == LAYOUT_RESOLUTION:
            if dialog.yesno(_plain(view.get('messageBoxHeading')), _plain(view.get('messageBody')),
                            nolabel=_plain(view.get('secondaryButtonText')) or getString(222),
                            yeslabel=_plain(view.get('submitButtonText'))):
                return {'action': template['primaryAction']}
            return {'action': CANCEL_ACTION}

        if layout == LAYOUT_REFUND:
            items = view.get('refundItems') or []
            reasons = view.get('cancellationReasons') or []
            if not items or not reasons:
                return None
            item = items[0]
            if len(items) > 1:
                choice = dialog.select(_plain(view.get('refundItemsKey')) or getString(30383), [i.get('formatString', '') for i in items])
                if choice < 0:
                    return {'action': template.get('cancelAction') or CANCEL_ACTION}
                item = items[choice]
            choice = dialog.select(_plain(view.get('cancellationReasonsKey')) or getString(30384),
                                   [r.get('cancellationTitle', '') for r in reasons])
            if choice < 0:
                return {'action': template.get('cancelAction') or CANCEL_ACTION}
            text = [view.get('bodyContent'), f"{view.get('refundAmountKey', '')} {item.get('refundAmount', '')}".strip()]
            if not dialog.yesno(_plain(view.get('titleText')) or getString(30381), '[CR]'.join(_plain(t) for t in text if t),
                                yeslabel=_plain(view.get('submitButtonText')) or getString(30381), nolabel=getString(222)):
                return {'action': template.get('cancelAction') or CANCEL_ACTION}
            return {'action': template['currentRefundAction'],
                    'clientData': {'returnReason': reasons[choice]['cancellationAction'], 'orderId': item.get('orderNumber')}}

        if layout == LAYOUT_NOTIFICATION:
            secondary = template.get('secondaryAction')
            if secondary and view.get('secondaryButtonText'):
                ok = dialog.yesno(_plain(view.get('messageBoxHeading')), _plain(view.get('messageBody')),
                                  nolabel=_plain(view.get('secondaryButtonText')), yeslabel=_plain(view.get('submitButtonText')))
                return {'action': template['primaryAction'] if ok else secondary}
            dialog.ok(_plain(view.get('messageBoxHeading')), _plain(view.get('messageBody')))
            return {'action': template['primaryAction']}

        Log(f'[Purchase] Unsupported workflow dialog {layout!r} (template {task.get("templateId")}), '
            f'keys {sorted(template)}', Log.ERROR)
        return None

    def _fail(self, title=None, message=None):
        Log(f'[Purchase] {self._workflow_type} failed: {title!r} {message!r}', Log.ERROR)
        xbmcgui.Dialog().ok(_plain(title) or self._title, _plain(message) or getString(30380) % _site())
        return RESULT_FAILED
