from rest_framework import permissions


class IsAdmin(permissions.BasePermission):
    """
    Allows access only to organization admin users.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'role', '') == 'admin')


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Allows read-only access to all authenticated users,
    but restricts mutating operations (POST, PUT, PATCH, DELETE) to organization admin users.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(getattr(request.user, 'role', '') == 'admin')
