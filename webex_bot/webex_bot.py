"""Main module."""
import logging
import os

import backoff
import coloredlogs
import requests
import webexpythonsdk

from webex_bot.commands.echo import EchoCommand
from webex_bot.commands.help import HelpCommand
from webex_bot.exceptions import BotException
from webex_bot.formatting import quote_info
from webex_bot.models.command import CALLBACK_KEYWORD_KEY, Command, COMMAND_KEYWORD_KEY
from webex_bot.models.response import Response
from webex_bot.websockets.webex_websocket_client import WebexWebsocketClient, DEFAULT_DEVICE_URL

log = logging.getLogger(__name__)


class WebexBot(WebexWebsocketClient):
    def __init__(self,
                 teams_bot_token,
                 approved_users=[],
                 approved_domains=[],
                 approved_rooms=[],
                 device_url=DEFAULT_DEVICE_URL,
                 include_demo_commands=False,
                 bot_name="Webex Bot",
                 bot_help_subtitle="Here are my available commands. Click one to begin.",
                 threads=True,
                 help_command=None,
                 log_level="INFO",
                 proxies=None):
        self.teams_bot_token = teams_bot_token
        self.commands = []
        self.default_handler = None  # Fallback handler    

        coloredlogs.install(level=os.getenv("LOG_LEVEL", log_level),
                            fmt='%(asctime)s  [%(levelname)s]  '
                                '[%(module)s.%(name)s.%(funcName)'
                                's]:%(lineno)s %(message)s')
        log.info("Registering bot with Webex cloud")
        WebexWebsocketClient.__init__(self,
                                      teams_bot_token,
                                      on_message=self.process_incoming_message,
                                      on_card_action=self.process_incoming_card_action,
                                      device_url=device_url,
                                      proxies=proxies)

        if help_command is None:
            self.help_command = HelpCommand(bot=self)
        else:
            self.help_command = help_command

        self.commands = {
            self.help_command
        }

        if include_demo_commands:
            self.add_command(EchoCommand())

        self.help_command.commands = self.commands

        self.card_callback_commands = {}
        self.approved_users = approved_users
        self.approved_domains = approved_domains
        self.approved_rooms = approved_rooms
        self.approval_parameters_check()
        self.bot_display_name = ""
        self.get_me_info()
        self.threads = threads    
    
    def set_default_handler(self, handler_func):
        """
        Set a fallback function for unmatched messages.
        """
        self.default_handler = handler_func

    def send_message(self, text, room_id):
        """
        Send a simple message to a Webex room.
        Used by plugins like ChuckCommand.
        """
        self.teams.messages.create(roomId=room_id, text=text)

    @backoff.on_exception(backoff.expo, requests.exceptions.ConnectionError)
    def get_me_info(self):
        me = self.teams.people.me()
        self.bot_display_name = me.displayName
        log.info(f"Running as {me.type} '{me.displayName}' with email {me.emails}")
        log.debug(f"Running as bot '{me}'")

    def add_command(self, command_class: Command):
        for c in self.commands:
            log.debug(f"Checking command '{c}' against {command_class}")
            new_callback_keyword = command_class.card_callback_keyword
            if new_callback_keyword and c.card_callback_keyword == new_callback_keyword:
                raise Exception(f"Error adding new command: '{command_class.command_keyword}'. "
                                f"Duplicate callback_keyword found: "
                                f"'{new_callback_keyword}'. Use a unique keyword in your "
                                f"'{command_class.command_keyword}' adaptive card JSON.")

        self.commands.add(command_class)
        for chained_command in command_class.chained_commands:
            self.commands.add(chained_command)

    def approval_parameters_check(self):
        if len(self.approved_users) == 0 and len(self.approved_domains) == 0 and len(self.approved_rooms) == 0:
            log.warning("Your bot is open to anyone on Webex Teams...")

    def check_user_approved(self, user_email, approved_rooms):
        user_approved = False
        self.approval_parameters_check()

        if len(self.approved_users) == 0 and len(self.approved_domains) == 0 and len(approved_rooms) == 0:
            user_approved = True
        elif len(self.approved_domains) > 0 and user_email.split('@')[1] in self.approved_domains:
            user_approved = True
        elif len(self.approved_users) > 0 and user_email in self.approved_users:
            user_approved = True
        elif len(approved_rooms) > 0 and self.is_user_member_of_room(user_email, approved_rooms):
            user_approved = True

        if not user_approved:
            log.warning(f"{user_email} is not approved to interact at this time. Ignoring.")
        return user_approved

    def is_user_member_of_room(self, user_email, approved_rooms):
        is_user_member = False
        for approved_room in approved_rooms:
            try:
                room_members = self.teams.memberships.list(roomId=approved_room, personEmail=user_email)
                for member in room_members:
                    if member.personEmail == user_email:
                        is_user_member = True
            except webexpythonsdk.exceptions.ApiError as apie:
                log.warn(f"API error: {apie}")
        return is_user_member

    def process_incoming_card_action(self, attachment_actions, activity):
        callback_keyword = attachment_actions.inputs.get(CALLBACK_KEYWORD_KEY)
        command_keyword = attachment_actions.inputs.get(COMMAND_KEYWORD_KEY)
        is_card_callback_command = callback_keyword is not None
        raw_message = callback_keyword if callback_keyword else command_keyword
        log.debug(f"raw_message (callback) ='{raw_message}' is_card_callback_command={is_card_callback_command}")

        self.process_raw_command(raw_message,
                                 attachment_actions, activity['actor']['emailAddress'], activity,
                                 is_card_callback_command=is_card_callback_command)

    def process_incoming_message(self, teams_message, activity):
        user_email = teams_message.personEmail
        raw_message = teams_message.text
        is_one_on_one_space = 'ONE_ON_ONE' in activity['target']['tags']

        if activity['actor']['type'] != 'PERSON':
            log.debug('message is from a bot, ignoring')
            return

        log.info(f"Message from {user_email}: {teams_message}")

        if not self.check_user_approved(user_email=user_email, approved_rooms=self.approved_rooms):
            return

        if not is_one_on_one_space:
            raw_message = raw_message.replace(self.bot_display_name, '').strip()

        self.process_raw_command(raw_message, teams_message, user_email, activity)

    def process_raw_command(self, raw_message, teams_message, user_email, activity, is_card_callback_command=False):
        room_id = teams_message.roomId
        is_one_on_one_space = 'ONE_ON_ONE' in activity['target']['tags']
        command = None
        user_command = raw_message.lower()
        log.info(f"New user_command: {user_command}")
        log.info(f"is_card_callback_command: {is_card_callback_command}")

        for c in self.commands:
            log.debug("--------")
            log.debug(f"Checking c.command_keyword: {c.command_keyword}")
            if not is_card_callback_command and c.command_keyword:
                if c.exact_command_keyword_match:
                    if user_command == c.command_keyword:
                        log.info("Exact match found!")
                        command = c
                        break
                else:
                    if user_command.find(c.command_keyword) != -1:
                        log.info("Sub-string match found!")
                        command = c
                        break
            else:
                if user_command == c.command_keyword or user_command == c.card_callback_keyword:
                    command = c
                    break

        if not command:
            log.warning(f"Did not find command for {user_command}.")
            if self.default_handler:
                log.info("Using fallback handler for unmatched message.")
                self.default_handler(teams_message)
                return
            command = self.help_command
        else:
            log.info(f"Found command: {command.command_keyword}")
            if command.approved_rooms:
                if not self.check_user_approved(user_email=user_email, approved_rooms=command.approved_rooms):
                    log.info(f"{user_email} is not allowed to run command: '{command.command_keyword}'")
                    return

        message_without_command = WebexBot.get_message_passed_to_command(command.command_keyword, raw_message)
        thread_parent_id = activity.get('parent', {}).get('id', activity.get('id'))

        if command.delete_previous_message and hasattr(teams_message, 'messageId'):
            previous_message_id = teams_message.messageId
            log.info(f"delete_previous_message is True. Deleting message with ID: {previous_message_id}")
            self.teams.messages.delete(previous_message_id)

        if not is_card_callback_command and command.card is not None:
            response = Response()
            response.text = "This bot requires a client which can render cards."
            response.attachments = {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": command.card
            }

            pre_card_load_reply, pre_card_load_reply_one_to_one = self.run_pre_card_load_reply(command, message_without_command, teams_message, activity)
            self.do_reply(pre_card_load_reply, room_id, user_email, pre_card_load_reply_one_to_one, is_one_on_one_space, thread_parent_id)
            reply = response
        else:
            pre_execute_reply, pre_execute_reply_one_to_one = self.run_pre_execute(command, message_without_command, teams_message, activity)
            self.do_reply(pre_execute_reply, room_id, user_email, pre_execute_reply_one_to_one, is_one_on_one_space, thread_parent_id)
            reply, reply_one_to_one = self.run_command_and_handle_bot_exceptions(command, message_without_command, teams_message, activity)

        return self.do_reply(reply, room_id, user_email, reply_one_to_one, is_one_on_one_space, thread_parent_id)

    def do_reply(self, reply, room_id, user_email, reply_one_to_one, is_one_on_one_space, conv_target_id):
        if reply and isinstance(reply, Response):
            if not reply.roomId:
                reply.roomId = room_id
            if not reply.parentId and conv_target_id and self.threads:
                reply.parentId = conv_target_id
            reply = reply.as_dict()
            self.teams.messages.create(**reply)
            reply = "ok"
        elif reply and isinstance(reply, list):
            for response in reply:
                if isinstance(response, Response):
                    if not response.roomId:
                        response.roomId = room_id
                    if not response.parentId and conv_target_id:
                        response.parentId = conv_target_id
                    self.teams.messages.create(**response.as_dict())
                else:
                    self.send_message_to_room_or_person(user_email, room_id, reply_one_to_one, is_one_on_one_space, response, conv_target_id)
            reply = "ok"
        elif reply:
            self.send_message_to_room_or_person(user_email, room_id, reply_one_to_one, is_one_on_one_space, reply, conv_target_id)
        return reply

    def send_message_to_room_or_person(self, user_email, room_id, reply_one_to_one, is_one_on_one_space, reply, conv_target_id):
        heads_up = quote_info(f"{user_email} I've messaged you 1-1. Please reply to me there.")
        if reply_one_to_one:
            if not is_one_on_one_space:
                if self.threads:
                    self.teams.messages.create(roomId=room_id, markdown=heads_up, parentId=conv_target_id)
                else:
                    self.teams.messages.create(roomId=room_id, markdown=heads_up)
            if self.threads:
                self.teams.messages.create(toPersonEmail=user_email, markdown=reply, parentId=conv_target_id)
            else:
                self.teams.messages.create(toPersonEmail=user_email, markdown=reply)
        else:
            if self.threads:
                self.teams.messages.create(roomId=room_id, markdown=reply, parentId=conv_target_id)
            else:
                self.teams.messages.create(roomId=room_id, markdown=reply)

    def run_pre_card_load_reply(self, command, message, teams_message, activity):
        try:
            return command.pre_card_load_reply(message, teams_message, activity), False
        except BotException as e:
            log.warn(f"BotException: {e.debug_message}")
            return e.reply_message, e.reply_one_to_one

    def run_pre_execute(self, command, message, teams_message, activity):
        try:
            return command.pre_execute(message, teams_message, activity), False
        except BotException as e:
            log.warn(f"BotException: {e.debug_message}")
            return e.reply_message, e.reply_one_to_one

    def run_command_and_handle_bot_exceptions(self, command, message, teams_message, activity):
        try:
            return command.card_callback(message, teams_message, activity), False
        except BotException as e:
            log.warn(f"BotException: {e.debug_message}")
            return e.reply_message, e.reply_one_to_one

    @staticmethod
    def get_message_passed_to_command(command, message):
        if command and message.lower().startswith(command.lower()):
            return message[len(command):]
        return message
