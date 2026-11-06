from celery import shared_task

from . import services


@shared_task
def run_retention():
    deleted, failed = services.run()
    return {'deleted': deleted, 'failed': failed}
