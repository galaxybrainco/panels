from django.core.exceptions import ValidationError
from django.db import transaction

from comics.models import PageStatus
from social.models import Boost, Like


def _require_ap_id(page):
    if page.status != PageStatus.PUBLISHED or not page.ap_id:
        raise ValidationError("Only published pages can be liked or boosted.")
    return page.ap_id


@transaction.atomic
def like(actor, page):
    like_obj, _ = Like.objects.get_or_create(
        actor=actor, object_id=_require_ap_id(page), defaults={"page": page}
    )
    return like_obj


@transaction.atomic
def unlike(actor, page):
    if page.ap_id:
        Like.objects.filter(actor=actor, object_id=page.ap_id).delete()


@transaction.atomic
def boost(actor, page):
    boost_obj, _ = Boost.objects.get_or_create(
        actor=actor, object_id=_require_ap_id(page), defaults={"page": page}
    )
    return boost_obj


@transaction.atomic
def unboost(actor, page):
    if page.ap_id:
        Boost.objects.filter(actor=actor, object_id=page.ap_id).delete()


def like_count(page):
    return Like.objects.filter(object_id=page.ap_id).count() if page.ap_id else 0


def boost_count(page):
    return Boost.objects.filter(object_id=page.ap_id).count() if page.ap_id else 0
