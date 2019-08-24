# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
import configparser
import logging
import os
import re
import zipfile
import zlib

from .deltabot import DeltaBot
import pkg_resources


__version__ = '0.9.0'


class Plugin(ABC):
    """Interface for the bot's plugins."""

    name = ''
    description = ''
    long_description = ''
    version = ''
    commands = []
    filters = []

    @classmethod
    def activate(cls, bot):
        """Activate the plugin, this method is called when the bot starts."""
        cls.bot = bot

    @classmethod
    def deactivate(cls):
        """Deactivate the plugin, this method is called before the plugin is disabled/removed, do clean up here."""
        cls.bot.remove_commands(cls.commands)
        cls.bot.remove_filters(cls.filters)


class SimpleBot(DeltaBot):
    # deltachat.account.Account instance
    account = None
    # the list of installed plugins
    plugins = None
    # logging.Logger compatible instance
    logger = None
   # locale to start the bot: es, en, etc.
    locale = 'en'
    # base directory for the bot configuration and db files
    basedir = None

    def __init__(self, basedir):
        super().__init__(basedir)

        self._cfg = configparser.ConfigParser(allow_no_value=True)
        self._cfg.path = os.path.join(self.basedir, 'simplebot.cfg')
        self._load_config()

        self._mdl = set()
        self._mpl = set()
        self._cdl = set()
        self._cpl = set()
        self.load_plugins()

    def start(self):
        self.activate_plugins()
        try:
            super().start()
        finally:
            self.deactivate_plugins()

    def send_html(self, chat, html, basename, user_agent):
        if user_agent == 'zhv':
            file_path = self.get_blobpath(basename+'.htmlzip')
            zlib.Z_DEFAULT_COMPRESSION = 9
            with zipfile.ZipFile(file_path, 'w', compression=zipfile.ZIP_DEFLATED) as fd:
                fd.writestr('index.html', html)
            chat.send_file(file_path)
        else:
            file_path = self.get_blobpath(basename+'.html')
            with open(file_path, 'w') as fd:
                fd.write(html)
            chat.send_file(file_path, mime_type='text/html')
        return file_path

    def get_blobpath(self, basename):
        path = os.path.join(self.get_blobdir(), basename)

        basename = basename.split('.', 1)
        if len(basename) == 2:
            basename, extension = basename[0], '.'+basename[1]
        else:
            basename, extension = basename[0], ''

        i = 1
        while os.path.exists(path):
            path = os.path.join(self.get_blobdir(),
                                '{}-{}{}'.format(basename, i, extension))
            i += 1

        return path

    def get_dir(self, plugin_name):
        pdir = os.path.join(self.basedir, plugin_name)
        if not os.path.exists(pdir):
            os.makedirs(pdir)
        return pdir

    def _load_config(self):
        if os.path.exists(self._cfg.path):
            self._cfg.read(self._cfg.path)

        botcfg = self.get_config(__name__)
        botcfg.setdefault('displayname', 'SimpleBot🤖')
        botcfg.setdefault('mdns_enabled', '0')
        botcfg.setdefault('mvbox_move', '1')
        self.save_config()

        self.set_name(botcfg['displayname'])
        self.account.set_config('mdns_enabled', botcfg['mdns_enabled'])
        self.account.set_config('mvbox_move', botcfg['mvbox_move'])

    def get_config(self, section):
        if not self._cfg.has_section(section):
            self._cfg.add_section(section)
        return self._cfg[section]

    def save_config(self):
        with open(self._cfg.path, 'w') as fd:
            self._cfg.write(fd)

    def add_on_msg_detected_listener(self, listener):
        self._mdl.add(listener)

    def add_on_msg_processed_listener(self, listener):
        self._mpl.add(listener)

    def remove_on_msg_detected_listener(self, listener):
        self._mdl.discard(listener)

    def remove_on_msg_processed_listener(self, listener):
        self._mpl.discard(listener)

    def add_on_cmd_detected_listener(self, listener):
        self._cdl.add(listener)

    def add_on_cmd_processed_listener(self, listener):
        self._cpl.add(listener)

    def remove_on_cmd_detected_listener(self, listener):
        self._cdl.discard(listener)

    def remove_on_cmd_processed_listener(self, listener):
        self._cpl.discard(listener)

    # def on_message_delivered(self, msg):
    #     self.account.delete_messages((msg,))

    def on_message(self, msg, text=None):
        self.logger.debug('Received message from {}'.format(
            msg.get_sender_contact().addr,))

        if msg.get_mime_headers()['chat-version'] is None:
            self.logger.debug('Classic email rejected')
            self.account.delete_messages((msg,))
            return

        if text is None:
            text = msg.text

        for listener in self._mdl:
            try:
                text = listener(msg, text)
                if text is None:
                    self.logger.debug('Message rejected')
                    self.account.delete_messages((msg,))
                    return
            except Exception as ex:
                self.logger.exception(ex)

        processed = False
        for f in self.filters:
            try:
                if f(msg, text):
                    processed = True
                    self.logger.debug('Message processed')
            except Exception as ex:
                self.logger.exception(ex)

        if not processed:
            self.logger.debug('Message was not processed')

        for listener in self._mpl:
            try:
                listener(msg, processed)
            except Exception as ex:
                self.logger.exception(ex)

        self.account.mark_seen_messages([msg])

    def on_command(self, msg, text=None):
        self.logger.debug('Received command from {}'.format(
            msg.get_sender_contact().addr,))

        if msg.get_mime_headers()['chat-version'] is None:
            self.logger.debug('Classic email rejected')
            self.account.delete_messages((msg,))
            return

        if text is None:
            text = msg.text
        real_cmd = self.get_args('/z', text)
        if real_cmd is None:
            msg.user_agent = 'unknow'
        else:
            msg.user_agent = 'zhv'
            text = real_cmd

        for listener in self._cdl:
            try:
                text = listener(msg, text)
                if text is None:
                    self.logger.debug('Command rejected')
                    self.account.delete_messages((msg,))
                    return
            except Exception as ex:
                self.logger.exception(ex)

        for cmd in self.commands:
            args = self.get_args(cmd, text)
            if args is not None:
                try:
                    self.commands[cmd][-1](msg, args)
                    processed = True
                    self.logger.debug('Command processed: {}'.format(cmd))
                    break
                except Exception as ex:
                    self.logger.exception(ex)
        else:
            processed = False

        if not processed:
            self.logger.debug('Command was not processed')

        for listener in self._cpl:
            try:
                listener(msg, processed)
            except Exception as ex:
                self.logger.exception(ex)

        self.account.mark_seen_messages([msg])

    def load_plugins(self):
        self.plugins = []
        for ep in pkg_resources.iter_entry_points('simplebot.plugins'):
            try:
                self.plugins.append(ep.load())
            except Exception as ex:
                self.logger.exception(ex)

    def activate_plugins(self):
        for plugin in self.plugins:
            plugin.activate(self)

    def deactivate_plugins(self):
        for plugin in self.plugins:
            try:
                plugin.deactivate()
            except Exception as ex:
                self.logger.exception(ex)
