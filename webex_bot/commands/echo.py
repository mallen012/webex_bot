from webex_bot.models.command import Command

class EchoCommand(Command):
    def __init__(self):
        super().__init__(
            command_keyword="echo",
            help_message="Echo back what you said."
        )

    def execute(self, message, attachment_actions, activity):
        return f"You said: {message.text}"
