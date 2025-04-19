from webex_bot.models.command import Command

class HelpCommand(Command):
    def __init__(self, bot):
        super().__init__(
            command_keyword="help",
            help_message="List all available bot commands."
        )
        self.bot = bot

    def execute(self, message, attachment_actions, activity):
        help_lines = []
        for cmd in self.bot.commands:
            if cmd.command_keyword != "help":
                help_lines.append(f"\\{cmd.command_keyword} - {cmd.help_message}")
        return "🛠️ Available Commands:\n" + "\n".join(help_lines)
