from django.tasks import task


@task
def ping() -> str:
    return "pong"
