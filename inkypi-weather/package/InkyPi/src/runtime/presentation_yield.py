"""Presentation members that a no-change preparation left undisplayable.

Rotation displays an exact current-theme cache or a prepared request. A
no-change preparation (a cold Pixiv bank after the JST day change) consumes the
request and produces neither. On 2026-10-19 the post-display reservation still
picked that member after every display, and each reservation closed the
reserved-round background guard for the rest of the round.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class YieldedPresentationReservations:
    """Keep undisplayable no-change members out of post-display reservations.

    A member returns once its DATA lane succeeds again (a warmer bank can
    prepare it), its revision changes, or it gains an exact current-theme cache.
    Only post-display reservations consult it; the rotation's own fallback to a
    non-exact cache after a theme change still sees every member.
    """

    def __init__(self):
        self._markers = {}

    @staticmethod
    def _identity(instance, data_last_success_at):
        return (instance.structural_generation, instance.settings_revision, data_last_success_at)

    def marked(self, instance_uuid):
        return instance_uuid in self._markers

    def forget(self, instance_uuid):
        self._markers.pop(instance_uuid, None)

    def yield_reservation(self, instance, data_last_success_at, playlist_manager, playlist_name):
        """Remember the member and move its current reservation behind its round."""
        self._markers[instance.instance_uuid] = self._identity(instance, data_last_success_at)
        deferred = released = False
        if playlist_name:
            deferred = playlist_manager.defer_rotation_reservation(
                instance.instance_uuid,
                expected_playlist_name=playlist_name,
            )
            if not deferred:
                released = playlist_manager.release_rotation_reservation(
                    instance.instance_uuid,
                    expected_playlist_name=playlist_name,
                )
        logger.info(
            "No-change presentation left its member undisplayable; yielding its rotation reservation. | "
            "plugin_id: %s | instance_uuid: %s | deferred: %s | released: %s",
            instance.plugin_id,
            instance.instance_uuid,
            deferred,
            released,
        )
        return deferred or released

    def excludes(self, instance, data_last_success_at, displayable):
        """Whether a marked member still has nothing new to prepare or display."""
        marker = self._markers.get(instance.instance_uuid)
        if marker is None:
            return False
        if displayable or marker != self._identity(instance, data_last_success_at):
            self.forget(instance.instance_uuid)
            return False
        return True
