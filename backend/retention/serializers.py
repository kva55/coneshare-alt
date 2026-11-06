import calendar

from rest_framework import serializers

from .models import RetentionLog, RetentionSettings


class RetentionSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetentionSettings
        fields = ['mode', 'after_value', 'after_unit', 'yearly_month', 'yearly_day', 'overwrite']

    def validate_after_value(self, value):
        if not 1 <= value <= 3650:
            raise serializers.ValidationError('Enter a number between 1 and 3650.')
        return value

    def validate_yearly_month(self, value):
        if not 1 <= value <= 12:
            raise serializers.ValidationError('Month must be 1 to 12.')
        return value

    def validate(self, attrs):
        month = attrs.get('yearly_month', getattr(self.instance, 'yearly_month', 1))
        day = attrs.get('yearly_day', getattr(self.instance, 'yearly_day', 1))
        if not 1 <= day <= calendar.monthrange(2023, month)[1]:   # a non-leap year: Feb 29 is not allowed
            raise serializers.ValidationError({'yearly_day': 'That day does not exist in every year.'})
        return attrs


class RetentionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RetentionLog
        fields = ['id', 'created_at', 'document_name', 'reason', 'overwritten', 'ok', 'error']
