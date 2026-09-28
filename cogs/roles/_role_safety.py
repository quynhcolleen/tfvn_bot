"""Privileged-permission denylist for roles the bot may sell or assign."""

from cogs.onboarding._role_exam_helpers import (
    UNSAFE_ROLE_PERMISSION_NAMES,
    unsafe_role_permission_names,
)

dangerous_permission_names = unsafe_role_permission_names

__all__ = (
    "UNSAFE_ROLE_PERMISSION_NAMES",
    "dangerous_permission_names",
    "unsafe_role_permission_names",
)
