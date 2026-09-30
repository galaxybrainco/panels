from actors.models import Actor
from federation.models import Delivery


def unique_inboxes(urls):
    seen = set()
    result = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def enqueue_delivery(activity, inbox_url, actor: Actor) -> Delivery:
    from federation.tasks import deliver_activity

    delivery = Delivery.objects.create(
        inbox_url=inbox_url, activity=activity, actor=actor
    )
    deliver_activity.enqueue(str(delivery.id))
    return delivery


def fan_out(activity, inbox_urls, actor: Actor):
    return [
        enqueue_delivery(activity, url, actor) for url in unique_inboxes(inbox_urls)
    ]
