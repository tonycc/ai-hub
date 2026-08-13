from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    notification_id: UUID
    application_id: str
    recipient_user_id: UUID
    subject: str
    status: str
    requested_at: datetime
    delivered_at: datetime | None
    delivery_reference: str | None
    failure_reason: str | None


class NotificationNotFoundError(LookupError):
    pass


class NotificationConfigurationDisabledError(PermissionError):
    pass


class NotificationRecipientNotFoundError(LookupError):
    pass


class NotificationService:
    @staticmethod
    def _from_row(row: Any) -> NotificationRecord:
        return NotificationRecord(
            notification_id=row["notification_id"],
            application_id=row["application_id"],
            recipient_user_id=row["recipient_user_id"],
            subject=row["subject"],
            status=row["status"],
            requested_at=row["requested_at"],
            delivered_at=row["delivered_at"],
            delivery_reference=row["delivery_reference"],
            failure_reason=row["failure_reason"],
        )

    async def create(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        recipient_user_id: UUID,
        subject: str,
        body: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> NotificationRecord:
        channel_enabled = await session.scalar(
            sa.text(
                """
                SELECT enabled
                FROM platform_core.notification_configuration
                WHERE application_id = :application_id AND channel = 'IN_APP'
                """
            ),
            {"application_id": application_id},
        )
        if channel_enabled is not True:
            raise NotificationConfigurationDisabledError(
                "IN_APP notification channel is not enabled for the application"
            )
        recipient_active = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.identity_user
                    WHERE user_id = :recipient_user_id AND status = 'ACTIVE'
                )
                """
            ),
            {"recipient_user_id": recipient_user_id},
        )
        if not recipient_active:
            raise NotificationRecipientNotFoundError(
                "Notification recipient is not an active platform user"
            )
        notification_id = uuid4()
        delivery_reference = f"test-channel:{notification_id}"
        requested_at = datetime.now(UTC)
        parameters = {
            "notification_id": notification_id,
            "application_id": application_id,
            "recipient_user_id": recipient_user_id,
            "subject": subject,
            "body": body,
            "payload": json.dumps(payload),
            "idempotency_key": idempotency_key,
            "delivery_reference": delivery_reference,
            "requested_at": requested_at,
        }
        inserted = (
            (
                await session.execute(
                    sa.text(
                        """
                INSERT INTO platform_core.notification
                    (notification_id, application_id, recipient_user_id, subject, body,
                     payload, idempotency_key, status, delivery_reference,
                     requested_at, delivered_at)
                VALUES
                    (:notification_id, :application_id, :recipient_user_id, :subject,
                     :body, CAST(:payload AS jsonb), :idempotency_key, 'DELIVERED',
                     :delivery_reference, :requested_at, :requested_at)
                ON CONFLICT (application_id, idempotency_key) DO NOTHING
                RETURNING notification_id, application_id, recipient_user_id, subject,
                          status, requested_at, delivered_at, delivery_reference,
                          failure_reason
                    """
                    ),
                    parameters,
                )
            )
            .mappings()
            .one_or_none()
        )
        if inserted is not None:
            return self._from_row(inserted)

        existing = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT notification_id, application_id, recipient_user_id, subject,
                           status, requested_at, delivered_at, delivery_reference,
                           failure_reason
                    FROM platform_core.notification
                    WHERE application_id = :application_id
                      AND idempotency_key = :idempotency_key
                    """
                    ),
                    parameters,
                )
            )
            .mappings()
            .one()
        )
        return self._from_row(existing)

    async def list_configurations(
        self,
        session: AsyncSession,
        *,
        application_ids: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT c.application_id, a.name AS application_name, c.channel,
                           c.enabled, c.sender_name, c.configuration,
                           c.updated_by_user_id, c.updated_at
                    FROM platform_core.notification_configuration AS c
                    JOIN platform_core.application AS a
                      ON a.application_id = c.application_id
                    WHERE (CAST(:application_ids AS varchar[]) IS NULL
                           OR c.application_id = ANY(CAST(:application_ids AS varchar[])))
                    ORDER BY a.name, c.channel
                    """
                    ),
                    {
                        "application_ids": (
                            sorted(application_ids) if application_ids is not None else None
                        )
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_recipients(
        self,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Return the minimum identity fields needed to address a notification."""
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT user_id, subject, display_name
                    FROM platform_core.identity_user
                    WHERE status = 'ACTIVE'
                    ORDER BY display_name, subject
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def upsert_configuration(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        channel: str,
        enabled: bool,
        sender_name: str,
        configuration: dict[str, Any],
        user_id: UUID,
    ) -> dict[str, Any]:
        application_exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application
                    WHERE application_id = :application_id
                )
                """
            ),
            {"application_id": application_id},
        )
        if not application_exists:
            raise NotificationNotFoundError("Application was not found")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.notification_configuration
                        (application_id, channel, enabled, sender_name, configuration,
                         updated_by_user_id)
                    VALUES
                        (:application_id, :channel, :enabled, :sender_name,
                         CAST(:configuration AS jsonb), :user_id)
                    ON CONFLICT (application_id, channel) DO UPDATE
                    SET enabled = EXCLUDED.enabled,
                        sender_name = EXCLUDED.sender_name,
                        configuration = EXCLUDED.configuration,
                        updated_by_user_id = EXCLUDED.updated_by_user_id,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING application_id, channel, enabled, sender_name,
                              configuration, updated_by_user_id, updated_at
                    """
                    ),
                    {
                        "application_id": application_id,
                        "channel": channel,
                        "enabled": enabled,
                        "sender_name": sender_name,
                        "configuration": json.dumps(configuration),
                        "user_id": user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def list_notifications(
        self,
        session: AsyncSession,
        *,
        application_ids: frozenset[str] | None,
        status: str | None,
        recipient_user_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        parameters = {
            "application_ids": (sorted(application_ids) if application_ids is not None else None),
            "status": status,
            "recipient_user_id": recipient_user_id,
            "limit": limit,
            "offset": offset,
        }
        total = await session.scalar(
            sa.text(
                """
                SELECT COUNT(*)
                FROM platform_core.notification
                WHERE (CAST(:application_ids AS varchar[]) IS NULL
                       OR application_id = ANY(CAST(:application_ids AS varchar[])))
                  AND (CAST(:status AS varchar) IS NULL OR status = :status)
                  AND (CAST(:recipient_user_id AS uuid) IS NULL
                       OR recipient_user_id = :recipient_user_id)
                """
            ),
            parameters,
        )
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT n.notification_id, n.application_id,
                           a.name AS application_name, n.recipient_user_id,
                           u.display_name AS recipient_name, n.subject, n.status,
                           n.requested_at, n.delivered_at, n.delivery_reference,
                           n.failure_reason
                    FROM platform_core.notification AS n
                    JOIN platform_core.application AS a
                      ON a.application_id = n.application_id
                    JOIN platform_core.identity_user AS u
                      ON u.user_id = n.recipient_user_id
                    WHERE (CAST(:application_ids AS varchar[]) IS NULL
                           OR n.application_id = ANY(CAST(:application_ids AS varchar[])))
                      AND (CAST(:status AS varchar) IS NULL OR n.status = :status)
                      AND (CAST(:recipient_user_id AS uuid) IS NULL
                           OR n.recipient_user_id = :recipient_user_id)
                    ORDER BY n.requested_at DESC, n.notification_id DESC
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    parameters,
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows], int(total or 0)

    async def get(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        notification_id: UUID,
    ) -> NotificationRecord:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT notification_id, application_id, recipient_user_id, subject,
                           status, requested_at, delivered_at, delivery_reference,
                           failure_reason
                    FROM platform_core.notification
                    WHERE application_id = :application_id
                      AND notification_id = :notification_id
                    """
                    ),
                    {"application_id": application_id, "notification_id": notification_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotificationNotFoundError("Notification was not found")
        return self._from_row(row)
