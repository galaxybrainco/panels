from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import render

from comics import discovery
from comics.models import ContentRating


def discover(request):
    q = request.GET.get("q", "").strip()
    rating = request.GET.get("rating", "all")
    tag = request.GET.get("tag", "").strip()
    try:
        comics = discovery.discover_comics(q=q, rating=rating, tag=tag)
    except discovery.InvalidRatingError:
        return HttpResponseBadRequest("Invalid rating")
    page_obj = Paginator(discovery.cards(comics), 24).get_page(request.GET.get("page"))
    return render(
        request,
        "comics/discover.html",
        {
            "page_obj": page_obj,
            "q": q,
            "rating": rating,
            "tag": tag,
            "ratings": ContentRating.choices,
        },
    )
