from federation import handlers
from moderation import blocks


@handlers.register("Block")
def handle_block(activity):
    return blocks.record_inbound_block(activity)
