import pytest
from django.core.cache import cache

from core.models import AppConfiguration
from core.services import get_dynamic_setting, get_max_watermark_file_size_mb


@pytest.mark.django_db
def test_get_dynamic_setting_bool_false_string_is_false():
    AppConfiguration.objects.update_or_create(
        key='FLATTEN_WATERMARKED_DOWNLOADS',
        defaults={'value': 'false', 'description': 'Flatten watermarked downloads.'},
    )
    cache.delete('app_config:FLATTEN_WATERMARKED_DOWNLOADS')

    assert get_dynamic_setting('FLATTEN_WATERMARKED_DOWNLOADS') is False


@pytest.mark.django_db
def test_get_dynamic_setting_bool_invalid_falls_back_to_default(settings):
    settings.FLATTEN_WATERMARKED_DOWNLOADS = True
    AppConfiguration.objects.update_or_create(
        key='FLATTEN_WATERMARKED_DOWNLOADS',
        defaults={'value': 'not-a-bool', 'description': 'Flatten watermarked downloads.'},
    )
    cache.delete('app_config:FLATTEN_WATERMARKED_DOWNLOADS')

    assert get_dynamic_setting('FLATTEN_WATERMARKED_DOWNLOADS') is True


@pytest.mark.django_db
def test_get_max_watermark_file_size_mb_preserves_zero():
    try:
        AppConfiguration.objects.update_or_create(
            key='MAX_WATERMARK_FILE_SIZE_MB',
            defaults={'value': '0', 'description': 'Max watermark file size in MB'},
        )
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')

        assert get_max_watermark_file_size_mb() == 0
    finally:
        AppConfiguration.objects.filter(key='MAX_WATERMARK_FILE_SIZE_MB').delete()
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')


@pytest.mark.django_db
def test_get_max_watermark_file_size_mb_defaults_to_100_when_none(settings):
    try:
        if hasattr(settings, 'MAX_WATERMARK_FILE_SIZE_MB'):
            delattr(settings, 'MAX_WATERMARK_FILE_SIZE_MB')
        AppConfiguration.objects.filter(key='MAX_WATERMARK_FILE_SIZE_MB').delete()
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')

        assert get_max_watermark_file_size_mb() == 100
    finally:
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')


@pytest.mark.django_db
def test_get_max_watermark_file_size_mb_returns_configured_int():
    try:
        AppConfiguration.objects.update_or_create(
            key='MAX_WATERMARK_FILE_SIZE_MB',
            defaults={'value': '250', 'description': 'Max watermark file size in MB'},
        )
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')

        assert get_max_watermark_file_size_mb() == 250
    finally:
        AppConfiguration.objects.filter(key='MAX_WATERMARK_FILE_SIZE_MB').delete()
        cache.delete('app_config:MAX_WATERMARK_FILE_SIZE_MB')

