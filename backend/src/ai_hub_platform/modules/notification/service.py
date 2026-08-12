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
        ).mappings().one_or_none()
        if inserted is not None:
            return self._from_row(inserted)

        existing = (
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
        ).mappings().one()
        return self._from_row(existing)

    async def get(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        notification_id: UUID,
    ) -> NotificationRecord:
        row = (
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
        ).mappings().one_or_none()
        if row is None:
            raise NotificationNotFoundError("Notification was not found")
        return self._from_row(row)
