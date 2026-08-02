"""Collie messengers — phone access via Telegram, WhatsApp, Slack, Discord.

Phase 4 replaces the planned phone companion app: the user reaches Collie
from their pocket through the messengers they already have. The channel
implementations are vendored nanobot code (``nanobot/channels/``); this
package owns the Collie-side lifecycle, settings, pairing surface, and
delivery of automations to the phone.
"""

from collie_core.messengers.bus import CollieBus
from collie_core.messengers.manager import MESSENGERS, MessengerManager

__all__ = ["MESSENGERS", "CollieBus", "MessengerManager"]
