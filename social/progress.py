from social.models import ReadingProgress


def record_progress(user, page):
    progress, _ = ReadingProgress.objects.update_or_create(
        user=user, series=page.series, defaults={"page": page}
    )
    return progress


def resume_point(user, series):
    return ReadingProgress.objects.filter(user=user, series=series).first()
