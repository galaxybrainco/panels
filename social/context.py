from social.models import Notification


def notifications(request):
    if request.user.is_authenticated:
        unread = Notification.objects.filter(recipient=request.user, read=False).count()
    else:
        unread = 0
    return {"unread_notifications": unread}
