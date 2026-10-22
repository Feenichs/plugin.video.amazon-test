#!/usr/bin/env python3
"""
Generates the skin files of the TV interface (home window):

- resources/skins/default/1080i/script-amazon-home.xml
- resources/skins/default/media/pv_*.png

Kodi windows cannot define their own includes, and the eight content rows of
the home window use identical tile layouts, so the window XML is generated
from the templates below. Re-run this script after changing them:

    python3 .scripts/generate_home_window.py
"""

import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIN = os.path.join(ROOT, 'resources', 'skins', 'default')
XML_PATH = os.path.join(SKIN, '1080i', 'script-amazon-home.xml')
MEDIA = os.path.join(SKIN, 'media')

ADDON = 'plugin.video.amazon-test'
NUM_ROWS = 30

# Colours (AARRGGBB), Prime Video: dark blue-grey background and blue accent
BACKGROUND_RGB = (15, 23, 30)
BACKGROUND = 'FF0F171E'
TEXT = 'FFFFFFFF'
TEXT_DIM = 'FF8197A4'
TEXT_DARK = 'FF0F171E'
ACCENT = 'FF1A98FF'
MESSAGE = 'FF4DB6FF'
TILE = 'FF1B2530'
TILE_FOCUSED = 'FF2A3946'

NAV_WIDTH = 200

# Tiles of the rows (16:9)
TILE_WIDTH = 410
THUMB_LEFT = 12
THUMB_TOP = 12
THUMB_WIDTH = 384
THUMB_HEIGHT = 216
TILE_HEIGHT = THUMB_TOP + THUMB_HEIGHT + 50

# Tiles of the grid
GRID_COLUMNS = 5
GRID_TILE_WIDTH = 360
GRID_THUMB_WIDTH = 336
GRID_THUMB_HEIGHT = 189
GRID_TILE_HEIGHT = THUMB_TOP + GRID_THUMB_HEIGHT + 50

# Episodes of the detail page
EPISODE_TILE_WIDTH = 410
EPISODE_TILE_HEIGHT = TILE_HEIGHT + 30


# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------

def write_png(path, width, height, pixel):
    """Write an RGBA PNG, pixel(x, y) returns (r, g, b, a)"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload))
                + kind
                + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    png = b''.join((
        b'\x89PNG\r\n\x1a\n',
        chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)),
        chunk(b'IDAT', zlib.compress(bytes(raw), 9)),
        chunk(b'IEND', b''),
    ))
    with open(path, 'wb') as png_file:
        png_file.write(png)


def rounded_coverage(x, y, width, height, radius, samples=4):
    """Anti-aliased coverage (0..1) of a pixel within a rounded rectangle"""
    inside = 0
    for sub_y in range(samples):
        for sub_x in range(samples):
            px = x + (sub_x + 0.5) / samples
            py = y + (sub_y + 0.5) / samples
            cx = min(max(px, radius), width - radius)
            cy = min(max(py, radius), height - radius)
            if (px - cx) ** 2 + (py - cy) ** 2 <= radius ** 2:
                inside += 1
    return inside / (samples * samples)


def rounded(width, height, radius, outline=0):
    def pixel(x, y):
        alpha = rounded_coverage(x, y, width, height, radius)
        if outline:
            inner = rounded_coverage(x - outline, y - outline, width - 2 * outline, height - 2 * outline,
                                     max(radius - outline, 1))
            if outline <= x < width - outline and outline <= y < height - outline:
                alpha = max(0.0, alpha - inner)
        return 255, 255, 255, int(round(255 * alpha))

    return pixel


def generate_textures():
    if not os.path.isdir(MEDIA):
        os.makedirs(MEDIA)
    r, g, b = BACKGROUND_RGB
    write_png(os.path.join(MEDIA, 'pv_white.png'), 8, 8, lambda x, y: (255, 255, 255, 255))
    # 9-slice textures, used with border="12" / border="28" / border="16"
    write_png(os.path.join(MEDIA, 'pv_rounded.png'), 64, 64, rounded(64, 64, 10))
    write_png(os.path.join(MEDIA, 'pv_pill.png'), 64, 64, rounded(64, 64, 31))
    write_png(os.path.join(MEDIA, 'pv_focus.png'), 64, 64, rounded(64, 64, 14, outline=4))
    # alpha masks for rounded 16:9 thumbnails
    write_png(os.path.join(MEDIA, 'pv_mask.png'), THUMB_WIDTH, THUMB_HEIGHT, rounded(THUMB_WIDTH, THUMB_HEIGHT, 10))
    write_png(os.path.join(MEDIA, 'pv_mask_grid.png'), GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT,
              rounded(GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 10))
    # gradients blending the preview image into the background
    write_png(os.path.join(MEDIA, 'pv_gradient_left.png'), 256, 4, lambda x, y: (r, g, b, int(255 * (1 - x / 255.0) ** 1.3)))
    write_png(os.path.join(MEDIA, 'pv_gradient_bottom.png'), 4, 256, lambda x, y: (r, g, b, int(255 * (y / 255.0) ** 1.2)))


# ---------------------------------------------------------------------------
# Window XML
# ---------------------------------------------------------------------------

def localize(string_id):
    return '$ADDON[%s %d]' % (ADDON, string_id)


def is_kind(*kinds):
    return '[' + ' | '.join('String.IsEqual(ListItem.Property(kind),%s)' % kind for kind in kinds) + ']'


TEXT_KINDS = ('action', 'all', 'next', 'back')


def tile_layout(list_id, focused, width, thumb_width, thumb_height, mask, subtitle=False):
    """Layout of one tile; list_id is used for focus dependent styling"""
    has_focus = 'Control.HasFocus(%d)' % list_id
    zoom = focus_ring = ''
    if focused:
        zoom = ('<animation effect="zoom" end="106" center="{cx},{cy}" time="150" tween="sine" condition="{focus}">'
                'Conditional</animation>').format(cx=THUMB_LEFT + thumb_width // 2, cy=THUMB_TOP + thumb_height // 2,
                                                  focus=has_focus)
        focus_ring = '''
            <control type="image">
                <left>{left}</left><top>{top}</top><width>{width}</width><height>{height}</height>
                <texture border="16" colordiffuse="FFFFFFFF">pv_focus.png</texture>
                <visible>{focus}</visible>
            </control>'''.format(left=THUMB_LEFT - 6, top=THUMB_TOP - 6, width=thumb_width + 12,
                                 height=thumb_height + 12, focus=has_focus)
    text_tile = is_kind(*TEXT_KINDS)
    # titles are part of most artworks, the label is shown below the focused tile and below
    # live channels; tiles without an image show their title inside
    overlay = 'String.IsEqual(ListItem.Property(overlay),true)'
    label_visible = '!' + text_tile + ' + String.IsEqual(ListItem.Property(tile),image) + !' + overlay
    if not subtitle:
        label_visible += ' + [%s | String.IsEqual(ListItem.Property(kind),station)]' % (has_focus if focused else 'false')
    labels = '''
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>34</height>
                <font>font12</font>
                <textcolor>{colour}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>{label_visible}</visible>
            </control>'''.format(xl=THUMB_LEFT + 2, xt=THUMB_TOP + thumb_height + 10, xw=thumb_width - 4,
                                 colour=TEXT if focused else TEXT_DIM, scroll='true' if focused else 'false',
                                 label_visible=label_visible)
    if subtitle:
        labels += '''
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>30</height>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Label2]</label>
            </control>'''.format(xl=THUMB_LEFT + 2, xt=THUMB_TOP + thumb_height + 42, xw=thumb_width - 4, dim=TEXT_DIM)

    return '''
        <control type="group">
            {zoom}
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <texture border="12" colordiffuse="{tile}">pv_rounded.png</texture>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="{mask}">$INFO[ListItem.Art(thumb)]</texture>
                <visible>!{text_tile} + String.IsEqual(ListItem.Property(tile),image) + !String.IsEqual(ListItem.Property(kind),station)</visible>
            </control>
            <control type="group">
                <visible>String.IsEqual(ListItem.Property(kind),station)</visible>
                <control type="image">
                    <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                    <aspectratio>scale</aspectratio>
                    <texture background="true" diffuse="{mask}" colordiffuse="FF666666">$INFO[ListItem.Art(fanart)]</texture>
                </control>
                <control type="image">
                    <left>{ll}</left><top>{lt}</top><width>{lw}</width><height>{lh}</height>
                    <aspectratio>keep</aspectratio>
                    <texture background="true">$INFO[ListItem.Property(logo)]</texture>
                </control>
            </control>
            <control type="textbox">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>String.IsEqual(ListItem.Property(tile),text) + !{text_tile}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{tw}</width><height>{th}</height>
                <texture border="12" colordiffuse="{tile_focused}">pv_rounded.png</texture>
                <visible>{text_tile}</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>String.IsEqual(ListItem.Property(kind),action)</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]  &#x203A;</label>
                <visible>String.IsEqual(ListItem.Property(kind),all) | String.IsEqual(ListItem.Property(kind),next)</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>&#x2039;  $INFO[ListItem.Label]</label>
                <visible>String.IsEqual(ListItem.Property(kind),back)</visible>
            </control>
            <control type="label">
                <left>{pl}</left><top>{tt}</top><width>{pw}</width><height>{th}</height>
                <align>center</align><aligny>center</aligny>
                <font>font30_title</font>
                <textcolor>FFFFFFFF</textcolor>
                <shadowcolor>AA000000</shadowcolor>
                <wrapmultiline>true</wrapmultiline>
                <label>$INFO[ListItem.Label]</label>
                <visible>{overlay} + String.IsEqual(ListItem.Property(tile),image)</visible>
            </control>
            <control type="progress">
                <left>{bl}</left><top>{bt}</top><width>{bw}</width><height>6</height>
                <texturebg colordiffuse="66FFFFFF">pv_white.png</texturebg>
                <midtexture colordiffuse="{accent}">pv_white.png</midtexture>
                <info>ListItem.PercentPlayed</info>
                <visible>!String.IsEmpty(ListItem.Property(progress))</visible>
            </control>
            <control type="group">
                <left>{badge_left}</left><top>{badge_top}</top>
                <visible>String.IsEqual(ListItem.Property(live),true)</visible>
                <control type="image">
                    <width>80</width><height>32</height>
                    <texture border="12" colordiffuse="FFD6223A">pv_rounded.png</texture>
                </control>
                <control type="label">
                    <width>80</width><height>32</height>
                    <align>center</align><aligny>center</aligny>
                    <font>font10</font><textcolor>FFFFFFFF</textcolor>
                    <label>LIVE</label>
                </control>
            </control>
            {focus_ring}{labels}
        </control>'''.format(
        zoom=zoom, focus_ring=focus_ring, labels=labels, mask=mask, text_tile=text_tile, overlay=overlay,
        tl=THUMB_LEFT, tt=THUMB_TOP, tw=thumb_width, th=thumb_height,
        pl=THUMB_LEFT + 16, pw=thumb_width - 32,
        ll=THUMB_LEFT + thumb_width // 4, lt=THUMB_TOP + thumb_height // 4, lw=thumb_width // 2, lh=thumb_height // 2,
        bl=THUMB_LEFT + 14, bt=THUMB_TOP + thumb_height - 16, bw=thumb_width - 28,
        badge_left=THUMB_LEFT + 12, badge_top=THUMB_TOP + 12,
        tile=TILE, tile_focused=TILE_FOCUSED, text=TEXT, accent=ACCENT,
    )


def row(index):
    list_id = 301 + index
    return '''
            <control type="group" id="{group_id}">
                <width>1920</width><height>{height}</height>
                <control type="label" id="{label_id}">
                    <left>80</left><top>4</top><width>1600</width><height>46</height>
                    <font>font14</font>
                    <textcolor>{text}</textcolor>
                </control>
                <control type="list" id="{list_id}">
                    <left>68</left><top>50</top><width>1852</width><height>{tile_height}</height>
                    <orientation>horizontal</orientation>
                    <onup>{list_id}</onup>
                    <ondown>{list_id}</ondown>
                    <scrolltime tween="sine">200</scrolltime>
                    <itemlayout width="{tile_width}" height="{tile_height}">{item}
                    </itemlayout>
                    <focusedlayout width="{tile_width}" height="{tile_height}">{focused}
                    </focusedlayout>
                </control>
            </control>'''.format(
        group_id=501 + index, label_id=401 + index, list_id=list_id, text=TEXT,
        height=TILE_HEIGHT + 50, tile_width=TILE_WIDTH, tile_height=TILE_HEIGHT,
        item=tile_layout(list_id, False, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'pv_mask.png'),
        focused=tile_layout(list_id, True, TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'pv_mask.png'),
    )


def pill_layout(list_id, focused, width, active_condition=None):
    """Tab bar entries, detail buttons and seasons"""
    has_focus = 'Control.HasFocus(%d)' % list_id
    parts = []
    if focused:
        parts.append('''
            <control type="image">
                <left>6</left><top>8</top><width>{w}</width><height>56</height>
                <texture border="28" colordiffuse="FFFFFFFF">pv_pill.png</texture>
                <visible>{focus}</visible>
            </control>
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{dark}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{focus}</visible>
            </control>'''.format(w=width - 12, width=width, dark=TEXT_DARK, focus=has_focus))
    if active_condition is None:
        # buttons: translucent pill
        parts.append('''
            <control type="image">
                <left>6</left><top>8</top><width>{w}</width><height>56</height>
                <texture border="28" colordiffuse="33FFFFFF">pv_pill.png</texture>
                <visible>{hide}</visible>
            </control>'''.format(w=width - 12, hide='!' + has_focus if focused else 'true'))
    focus_condition = (' + !%s' % has_focus) if focused else ''
    active = active_condition or 'true'
    parts.append('''
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{active}{focus_condition}</visible>
            </control>'''.format(width=width, text=TEXT, active=active, focus_condition=focus_condition))
    if active_condition:
        parts.append('''
            <control type="label">
                <width>{width}</width><height>72</height>
                <align>center</align><aligny>center</aligny>
                <font>font13</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{active}{focus_condition}</visible>
            </control>
            <control type="image">
                <left>{bar_left}</left><top>62</top><width>50</width><height>4</height>
                <texture colordiffuse="{accent}">pv_white.png</texture>
                <visible>{active}{focus_condition}</visible>
            </control>'''.format(width=width, dim=TEXT_DIM, accent=ACCENT, active=active,
                                 focus_condition=focus_condition, bar_left=(width - 50) // 2))
    return ''.join(parts)


PREVIEW_TEMPLATE = '''        <!-- Preview of the focused title on tabs and category pages -->
        <control type="group">
            <visible>{visible}</visible>
            <control type="image">
                <left>560</left><top>0</top><width>1360</width><height>765</height>
                <aspectratio>scale</aspectratio>
                <fadetime>300</fadetime>
                <texture background="true">$INFO[{li}ListItem.Art(fanart)]</texture>
                <visible>!Player.HasVideo</visible>
            </control>
            <control type="videowindow">
                <left>560</left><top>0</top><width>1360</width><height>765</height>
                <visible>Player.HasVideo</visible>
            </control>
            <control type="image">
                <left>560</left><top>0</top><width>800</width><height>765</height>
                <texture>pv_gradient_left.png</texture>
            </control>
            <control type="image">
                <left>560</left><top>400</top><width>1360</width><height>365</height>
                <texture>pv_gradient_bottom.png</texture>
            </control>
            <control type="image">
                <width>1920</width><height>220</height>
                <texture flipy="true">pv_gradient_bottom.png</texture>
            </control>
            <control type="group">
                <left>80</left><top>160</top>
                <control type="image">
                    <width>620</width><height>150</height>
                    <aspectratio align="left" aligny="bottom">keep</aspectratio>
                    <fadetime>200</fadetime>
                    <texture background="true">$INFO[{li}ListItem.Art(clearlogo)]</texture>
                    <visible>!String.IsEmpty({li}ListItem.Art(clearlogo))</visible>
                </control>
                <control type="textbox">
                    <top>40</top><width>1000</width><height>110</height>
                    <aligny>bottom</aligny>
                    <font>font45_title</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[{li}ListItem.Label]</label>
                    <visible>String.IsEmpty({li}ListItem.Art(clearlogo)) + ![{text_tiles}]</visible>
                </control>
                <control type="label">
                    <top>164</top><width>1000</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[{li}ListItem.Property(meta)]</label>
                </control>
                <control type="label">
                    <top>164</top><width>1000</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[{li}ListItem.Label2]</label>
                    <visible>String.IsEqual({li}ListItem.Property(kind),station)</visible>
                </control>
                <control type="label">
                    <top>202</top><width>1000</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{message}</textcolor>
                    <label>$INFO[{li}ListItem.Property(message)]</label>
                </control>
                <control type="textbox">
                    <top>246</top><width>860</width><height>130</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[{li}ListItem.Plot]</label>
                    <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                </control>
            </control>
        </control>

'''


WINDOW_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- Generated by .scripts/generate_home_window.py - do not edit manually -->
<window>
    <defaultcontrol>100</defaultcontrol>
    <backgroundcolor>{background}</backgroundcolor>
    <animation effect="fade" time="250">WindowOpen</animation>
    <animation effect="fade" time="200">WindowClose</animation>
    <controls>
        <!--
            A video that keeps playing in the background has to be rendered, otherwise it runs
            out of sync with the audio. It is shown in the preview area of the tabs and of the
            detail page, or dimmed behind the grid.
        -->
        <control type="videowindow">
            <width>1920</width><height>1080</height>
            <visible>Player.HasVideo + {grid_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="{background}">pv_white.png</texture>
            <visible>!Player.HasVideo | !{grid_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="E6{background_rgb}">pv_white.png</texture>
            <visible>Player.HasVideo + {grid_view}</visible>
        </control>

{preview}
        <!-- Detail page of a movie or a season -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),detail)</visible>
            <control type="image">
                <left>420</left><top>0</top><width>1500</width><height>844</height>
                <aspectratio>scale</aspectratio>
                <fadetime>300</fadetime>
                <texture background="true">$INFO[Window.Property(detail_fanart)]</texture>
                <visible>!Player.HasVideo</visible>
            </control>
            <control type="videowindow">
                <left>420</left><top>0</top><width>1500</width><height>844</height>
                <visible>Player.HasVideo</visible>
            </control>
            <control type="image">
                <left>420</left><top>0</top><width>900</width><height>844</height>
                <texture>pv_gradient_left.png</texture>
            </control>
            <control type="image">
                <left>420</left><top>480</top><width>1500</width><height>364</height>
                <texture>pv_gradient_bottom.png</texture>
            </control>
            <control type="image">
                <top>844</top><width>1920</width><height>236</height>
                <texture colordiffuse="{background}">pv_white.png</texture>
            </control>
            <control type="image">
                <width>1920</width><height>220</height>
                <texture flipy="true">pv_gradient_bottom.png</texture>
            </control>
            <control type="group">
                <left>80</left><top>140</top>
                <control type="image">
                    <width>640</width><height>160</height>
                    <aspectratio align="left" aligny="bottom">keep</aspectratio>
                    <texture background="true">$INFO[Window.Property(detail_logo)]</texture>
                    <visible>!String.IsEmpty(Window.Property(detail_logo))</visible>
                </control>
                <control type="textbox">
                    <top>30</top><width>1100</width><height>130</height>
                    <aligny>bottom</aligny>
                    <font>font45_title</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[Window.Property(detail_title)]</label>
                    <visible>String.IsEmpty(Window.Property(detail_logo))</visible>
                </control>
                <control type="label">
                    <top>174</top><width>1100</width><height>40</height>
                    <font>font14</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[Window.Property(detail_season)]</label>
                    <visible>!Control.HasFocus({episode_list})</visible>
                </control>
                <control type="label">
                    <top>174</top><width>1100</width><height>40</height>
                    <font>font14</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[Container({episode_list}).ListItem.Label2,, · ]$INFO[Container({episode_list}).ListItem.Label]</label>
                    <visible>Control.HasFocus({episode_list})</visible>
                </control>
                <control type="label">
                    <top>216</top><width>1100</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[Window.Property(detail_meta)]$INFO[Window.Property(detail_genres),  ·  ]</label>
                </control>
                <control type="label">
                    <top>254</top><width>1100</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{message}</textcolor>
                    <label>$INFO[Window.Property(detail_message)]</label>
                </control>
                <control type="textbox">
                    <top>298</top><width>900</width><height>105</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[Window.Property(detail_plot)]</label>
                    <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                    <visible>!Control.HasFocus({episode_list})</visible>
                </control>
                <control type="textbox">
                    <top>298</top><width>900</width><height>105</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[Container({episode_list}).ListItem.Plot]</label>
                    <autoscroll delay="6000" time="3000" repeat="10000">true</autoscroll>
                    <visible>Control.HasFocus({episode_list})</visible>
                </control>
            </control>
            <control type="list" id="{button_list}">
                <left>68</left><top>590</top><width>1800</width><height>72</height>
                <orientation>horizontal</orientation>
                <onup>{button_list}</onup>
                <ondown>{button_list}</ondown>
                <scrolltime>150</scrolltime>
                <itemlayout width="{button_width}" height="72">{button_item}
                </itemlayout>
                <focusedlayout width="{button_width}" height="72">{button_focused}
                </focusedlayout>
            </control>
            <control type="list" id="{season_list}">
                <left>68</left><top>672</top><width>1800</width><height>72</height>
                <orientation>horizontal</orientation>
                <onup>{season_list}</onup>
                <ondown>{season_list}</ondown>
                <scrolltime>150</scrolltime>
                <visible>String.IsEqual(Window.Property(has_seasons),true)</visible>
                <itemlayout width="200" height="72">{season_item}
                </itemlayout>
                <focusedlayout width="200" height="72">{season_focused}
                </focusedlayout>
            </control>
            <control type="list" id="{episode_list}">
                <left>68</left><top>{episode_top}</top><width>1852</width><height>{episode_height}</height>
                <orientation>horizontal</orientation>
                <onup>{episode_list}</onup>
                <ondown>{episode_list}</ondown>
                <scrolltime tween="sine">200</scrolltime>
                <visible>String.IsEqual(Window.Property(has_episodes),true)</visible>
                <itemlayout width="{episode_width}" height="{episode_height}">{episode_item}
                </itemlayout>
                <focusedlayout width="{episode_width}" height="{episode_height}">{episode_focused}
                </focusedlayout>
            </control>
        </control>

        <!-- Header -->
        <!-- pv_logo.png: clearlogo.png scaled to twice the header size (4K), Kodi does not
             smooth large images when it scales them down -->
        <control type="image">
            <left>60</left><top>33</top><width>200</width><height>62</height>
            <aspectratio align="left">keep</aspectratio>
            <texture>pv_logo.png</texture>
        </control>
        <control type="list" id="100">
            <left>300</left><top>28</top><width>1400</width><height>72</height>
            <orientation>horizontal</orientation>
            <ondown>100</ondown>
            <scrolltime>150</scrolltime>
            <itemlayout width="{nav_width}" height="72">{nav_item}
            </itemlayout>
            <focusedlayout width="{nav_width}" height="72">{nav_focused}
            </focusedlayout>
        </control>
        <control type="label">
            <right>60</right><top>28</top><width>300</width><height>40</height>
            <align>right</align>
            <font>font14</font>
            <textcolor>{text}</textcolor>
            <label>$INFO[System.Time]</label>
        </control>
        <control type="label">
            <right>60</right><top>66</top><width>300</width><height>32</height>
            <align>right</align>
            <font>font10</font>
            <textcolor>{dim}</textcolor>
            <label>$INFO[Window.Property(user)]</label>
        </control>
        <control type="label">
            <left>80</left><top>104</top><width>1760</width><height>50</height>
            <font>font30_title</font>
            <textcolor>{text}</textcolor>
            <label>$INFO[Window.Property(page_title)]</label>
            <visible>!String.IsEqual(Window.Property(view),detail)</visible>
        </control>

        <!-- Rows -->
        <control type="grouplist" id="300">
            <left>0</left><top>590</top><width>1920</width><height>490</height>
            <orientation>vertical</orientation>
            <itemgap>6</itemgap>
            <scrolltime tween="sine">250</scrolltime>
            <onup>100</onup>
            <visible>String.IsEqual(Window.Property(view),tab)</visible>
            {rows}
        </control>

        <!-- Grid for "show all", search results and the following pages -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),grid)</visible>
            <control type="panel" id="600">
                <left>68</left><top>180</top><width>1820</width><height>900</height>
                <orientation>vertical</orientation>
                <onup>100</onup>
                <scrolltime tween="sine">250</scrolltime>
                <itemlayout width="{grid_width}" height="{grid_height}">{grid_item}
                </itemlayout>
                <focusedlayout width="{grid_width}" height="{grid_height}">{grid_focused}
                </focusedlayout>
            </control>
        </control>

        <!-- Status -->
        <control type="label">
            <left>80</left><bottom>30</bottom><width>1760</width><height>40</height>
            <align>center</align>
            <font>font13</font>
            <textcolor>{dim}</textcolor>
            <label>{loading}</label>
            <visible>String.IsEqual(Window.Property(loading),true)</visible>
            <animation effect="fade" start="100" end="40" time="800" pulse="true" condition="true">Conditional</animation>
        </control>
        <control type="label">
            <left>80</left><top>700</top><width>1760</width><height>60</height>
            <align>center</align>
            <font>font14</font>
            <textcolor>{dim}</textcolor>
            <label>{empty}</label>
            <visible>String.IsEqual(Window.Property(empty),true) + !String.IsEqual(Window.Property(loading),true)</visible>
        </control>
    </controls>
</window>
'''


def preview():
    """Preview of the focused tile, or of the first tile of the first row while the tab bar has the focus"""
    text_tiles = ' | '.join('String.IsEqual(ListItem.Property(kind),%s)' % kind for kind in TEXT_KINDS)
    tab = 'String.IsEqual(Window.Property(view),tab)'
    return ''.join(PREVIEW_TEMPLATE.format(
        visible=visible, li=prefix, text=TEXT, dim=TEXT_DIM, message=MESSAGE,
        text_tiles=text_tiles.replace('ListItem.', prefix + 'ListItem.'))
        for prefix, visible in (('', tab + ' + !Control.HasFocus(100)'),
                                ('Container(301).', tab + ' + Control.HasFocus(100)')))


def generate_window():
    xml = WINDOW_TEMPLATE.format(
        preview=preview(),
        background=BACKGROUND,
        background_rgb=BACKGROUND[2:],
        grid_view='String.IsEqual(Window.Property(view),grid)',
        tab_view='String.IsEqual(Window.Property(view),tab)',
        text_tiles=' | '.join('String.IsEqual(ListItem.Property(kind),%s)' % kind for kind in TEXT_KINDS),
        text=TEXT, dim=TEXT_DIM, message=MESSAGE,
        nav_width=NAV_WIDTH,
        nav_item=pill_layout(100, False, NAV_WIDTH, 'String.IsEqual(ListItem.Property(active),true)'),
        nav_focused=pill_layout(100, True, NAV_WIDTH, 'String.IsEqual(ListItem.Property(active),true)'),
        button_list=700, season_list=710, episode_list=720,
        button_width=420,
        button_item=pill_layout(700, False, 420),
        button_focused=pill_layout(700, True, 420),
        season_item=pill_layout(710, False, 200, 'String.IsEqual(ListItem.Property(active),true)'),
        season_focused=pill_layout(710, True, 200, 'String.IsEqual(ListItem.Property(active),true)'),
        episode_top=760, episode_width=EPISODE_TILE_WIDTH, episode_height=EPISODE_TILE_HEIGHT,
        episode_item=tile_layout(720, False, EPISODE_TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'pv_mask.png', subtitle=True),
        episode_focused=tile_layout(720, True, EPISODE_TILE_WIDTH, THUMB_WIDTH, THUMB_HEIGHT, 'pv_mask.png', subtitle=True),
        rows=''.join(row(index) for index in range(NUM_ROWS)),
        grid_width=GRID_TILE_WIDTH, grid_height=GRID_TILE_HEIGHT,
        grid_item=tile_layout(600, False, GRID_TILE_WIDTH, GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 'pv_mask_grid.png'),
        grid_focused=tile_layout(600, True, GRID_TILE_WIDTH, GRID_THUMB_WIDTH, GRID_THUMB_HEIGHT, 'pv_mask_grid.png'),
        loading=localize(30328),
        empty=localize(30329),
    )
    directory = os.path.dirname(XML_PATH)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(XML_PATH, 'w', encoding='utf-8') as xml_file:
        xml_file.write(xml)


if __name__ == '__main__':
    generate_textures()
    generate_window()
    print('Generated', os.path.relpath(XML_PATH, ROOT))
