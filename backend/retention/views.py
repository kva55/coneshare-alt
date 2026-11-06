from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsAdmin

from . import services
from .models import RetentionLog, RetentionSettings
from .serializers import RetentionLogSerializer, RetentionSettingsSerializer


class RetentionSettingsView(APIView):
    permission_classes = [IsAdmin]

    def _payload(self, row):
        now = timezone.now()
        cutoff = services.policy_cutoff(row, now)
        return {'settings': RetentionSettingsSerializer(row).data,
                'due_now': len(services.due_documents(now, row)),
                'cutoff': cutoff}

    @extend_schema(tags=['retention'], responses={200: dict})
    def get(self, request):
        return Response(self._payload(RetentionSettings.load()))

    @extend_schema(tags=['retention'], request=RetentionSettingsSerializer, responses={200: dict, 400: dict})
    def put(self, request):
        row = RetentionSettings.load()
        serializer = RetentionSettingsSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        row = serializer.save(updated_by=request.user)
        return Response(self._payload(row))


class RetentionRunView(APIView):
    """Run the job now (it also runs hourly)."""
    permission_classes = [IsAdmin]

    @extend_schema(tags=['retention'], request=None, responses={200: dict})
    def post(self, request):
        deleted, failed = services.run()
        return Response({'deleted': deleted, 'failed': failed})


class RetentionLogView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(tags=['retention'], responses={200: RetentionLogSerializer(many=True)})
    def get(self, request):
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(RetentionLog.objects.filter(organization_id=str(request.user.organization_id)), request, view=self)
        return paginator.get_paginated_response(RetentionLogSerializer(page, many=True).data)
