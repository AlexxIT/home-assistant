"""
Decorator service for the media_player.play_media service.

For more details about this component, please refer to the documentation at
...
"""
import asyncio
import logging
import os

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID, ATTR_MEDIA_DURATION, ATTR_MEDIA_POSITION,
    DOMAIN as MEDIA_PLAYER_DOMAIN, SERVICE_PLAY_MEDIA,
    MEDIA_PLAYER_PLAY_MEDIA_SCHEMA)
from homeassistant.const import (
    ATTR_ENTITY_ID, EVENT_STATE_CHANGED, STATE_IDLE, STATE_PAUSED,
    STATE_PLAYING, STATE_OFF)
from homeassistant.config import load_yaml_config_file


_LOGGER = logging.getLogger(__name__)

DOMAIN = 'playlist_player'
DEPENDENCIES = ['media_player']

DATA_PLAYLIST_PLAYERS = 'data_playlist_players'


@asyncio.coroutine
def async_setup(hass, config):
    if DATA_PLAYLIST_PLAYERS not in hass.data:
        hass.data[DATA_PLAYLIST_PLAYERS] = {}

    @asyncio.coroutine
    def async_play_media(call):
        if not call.data.get(ATTR_ENTITY_ID):
            raise RuntimeError('Empty entity_id not supported.')

        for entity_id in call.data.get(ATTR_ENTITY_ID):
            if entity_id not in hass.data[DATA_PLAYLIST_PLAYERS]:
                player = PlaylistPlayer(hass, entity_id)
                hass.data[DATA_PLAYLIST_PLAYERS][entity_id] = player
            else:
                player = hass.data[DATA_PLAYLIST_PLAYERS][entity_id]

            yield from player.async_play(call.data)

    descriptions = load_yaml_config_file(
        os.path.join(os.path.dirname(__file__),
                     'media_player', 'services.yaml'))

    hass.services.async_register(DOMAIN, SERVICE_PLAY_MEDIA, async_play_media,
                                 description=descriptions[SERVICE_PLAY_MEDIA],
                                 schema=MEDIA_PLAYER_PLAY_MEDIA_SCHEMA)

    @asyncio.coroutine
    def async_state_changed(event):
        entity_id = event.data.get(ATTR_ENTITY_ID)

        if entity_id in hass.data[DATA_PLAYLIST_PLAYERS]:
            player = hass.data[DATA_PLAYLIST_PLAYERS][entity_id]
            new_state = event.data.get('new_state')
            yield from player.async_state_changed(new_state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, async_state_changed)

    return True


class PlaylistPlayer:
    def __init__(self, hass, entity_id):
        self.hass = hass
        self.entity_id = entity_id

        self.start_time = 0
        self.time_left = 0
        self.time_past = 0

    @asyncio.coroutine
    def async_play(self, call_data):
        self.data = dict(call_data).copy()
        self.data[ATTR_ENTITY_ID] = self.entity_id

        self.media_ids = call_data.get(ATTR_MEDIA_CONTENT_ID).split(',')
        self.state = STATE_IDLE

        _LOGGER.info('Play for %s', self.entity_id)

        yield from self.async_media_next_track()

    @asyncio.coroutine
    def async_media_next_track(self):
        if len(self.media_ids) > 0:
            self.data[ATTR_MEDIA_CONTENT_ID] = self.media_ids.pop(0)

            _LOGGER.info('Next for %s', self.entity_id)

            self.hass.async_add_job(
                self.hass.services.async_call(
                    MEDIA_PLAYER_DOMAIN, SERVICE_PLAY_MEDIA, self.data)
            )
        else:
            yield from self.async_stop()

    @asyncio.coroutine
    def async_stop(self):
        del self.hass.data[DATA_PLAYLIST_PLAYERS][self.entity_id]

        _LOGGER.info('Stop for %s', self.entity_id)

        self.entity_id = None

    @asyncio.coroutine
    def async_state_changed(self, new_state):
        import time

        _LOGGER.debug('State for %s: %s => %s', self.entity_id, self.state,
                      new_state.state)

        if new_state.state == STATE_PLAYING:
            # Chromecast - support position, fire playing state 3 times,
            #     support off state from HDMI CEC (stop button)
            # Kodi - don't support position, fire playing state 1 time
            # Apple TV - suport position and off state from Homeassistant
            duration = new_state.attributes.get(ATTR_MEDIA_DURATION, 0)
            position = new_state.attributes.get(ATTR_MEDIA_POSITION, 0)
            new_time_left = duration - position

            _LOGGER.debug('Position: %.2f, Duration: %.2f', position, duration)

            if self.state == STATE_IDLE or new_time_left != self.time_left:
                # idle => playing = video started
                # or updated duration, or updated position
                self.start_time = time.time()
                self.time_left = new_time_left
                self.time_past = 0
            elif self.state == STATE_PAUSED:
                # paused => playing = continue after pause, can be new video
                self.start_time = time.time()
            else:
                # playing => playing = can be new video
                pass

        elif new_state.state == STATE_PAUSED:
            if self.state == STATE_PLAYING:
                # playing => paused = update time_left
                self.time_past += time.time() - self.start_time
            else:
                # idle or paused => paused
                _LOGGER.warning('Strange status change from %s to %s for %s',
                                self.state, new_state.state, self.entity_id)

        elif new_state.state == STATE_IDLE:
            if self.state == STATE_PLAYING:
                # playing => idle = video ended or stop pressed
                self.time_past += time.time() - self.start_time

                _LOGGER.debug('Video time_past=%.2f time_left=%.2f',
                              self.time_past, self.time_left)

                # check if video ended
                if abs(self.time_past - self.time_left) < 2:
                    yield from self.async_media_next_track()
                else:
                    yield from self.async_stop()
            elif self.state == STATE_PAUSED:
                # paused => idle = stop pressed
                yield from self.async_stop()
            else:
                # idle => idle = can be loading
                pass

        elif new_state.state == STATE_OFF:
            yield from self.async_stop()

        else:
            _LOGGER.warning('Unsupported new_state %s for %s', new_state.state,
                            self.entity_id)
            return (yield from self.async_stop())

        self.state = new_state.state
