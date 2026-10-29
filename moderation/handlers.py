from federation import handlers
from moderation import blocks, reports


@handlers.register("Block")
def handle_block(activity):
    return blocks.record_inbound_block(activity)


@handlers.register("Flag")
def handle_flag(activity):
    return reports.record_inbound_flag(activity)
