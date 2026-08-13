#!/usr/bin/env bash

set -euo pipefail

M2_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M2_PROJECT_ROOT="$(cd "${M2_SCRIPT_DIR}/../.." && pwd)"
M2_COMPOSE_FILE="${M2_PROJECT_ROOT}/deploy/compose.yaml"
M2_ENV_FILE="${M2_PROJECT_ROOT}/.env.example"
M2_PROJECT_NAME="ai-hub-m2-runtime-$PPID-$$"
M2_WORK_DIR="$(mktemp -d /tmp/ai-hub-m2-runtime.XXXXXX)"
M2_EDGE_PORT="${M2_EDGE_PORT:-18089}"
M2_POSTGRES_PORT="${M2_POSTGRES_PORT:-15435}"
M2_RABBITMQ_PORT="${M2_RABBITMQ_PORT:-25673}"
M2_RABBITMQ_MANAGEMENT_PORT="${M2_RABBITMQ_MANAGEMENT_PORT:-15674}"
M2_SOURCE_APPLICATION="standalone-example"
M2_RECORD_ID="30000000-0000-4000-8000-000000000001"
M2_DELETE_RECORD_ID="30000000-0000-4000-8000-000000000002"

export AI_HUB_EDGE_PORT="${M2_EDGE_PORT}"
export AI_HUB_POSTGRES_PORT="${M2_POSTGRES_PORT}"
export AI_HUB_RABBITMQ_PORT="${M2_RABBITMQ_PORT}"
export AI_HUB_RABBITMQ_MANAGEMENT_PORT="${M2_RABBITMQ_MANAGEMENT_PORT}"
export AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL="http://rabbitmq:15672"
export AI_HUB_OPERATIONS_RABBITMQ_USERNAME="platform_observer"
export AI_HUB_OPERATIONS_RABBITMQ_PASSWORD="local-only-rabbitmq-observer-password"
export RABBITMQ_OBSERVER_PASSWORD="local-only-rabbitmq-observer-password"
m2_compose() {
  docker compose \
    --project-name "${M2_PROJECT_NAME}" \
    --env-file "${M2_ENV_FILE}" \
    -f "${M2_COMPOSE_FILE}" \
    --profile standard-events \
    "$@"
}

m2_base_compose() {
  docker compose \
    --project-name "${M2_PROJECT_NAME}-base-contract" \
    --env-file "${M2_ENV_FILE}" \
    -f "${M2_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m2_note() {
  printf 'M2 runtime gate: %s\n' "$1"
}

m2_fail() {
  printf 'M2 runtime gate failed: %s\n' "$1" >&2
  exit 1
}

m2_cleanup() {
  m2_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M2_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M2 runtime environment retained as project %s\n' "${M2_PROJECT_NAME}"
  else
    m2_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  case "${M2_WORK_DIR}" in
    /tmp/ai-hub-m2-runtime.*) rm -rf -- "${M2_WORK_DIR}" ;;
    *) printf 'Refusing to remove unexpected temporary path: %s\n' "${M2_WORK_DIR}" >&2 ;;
  esac
  exit "${m2_exit_code}"
}

trap m2_cleanup EXIT INT TERM

m2_require_command() {
  command -v "$1" >/dev/null 2>&1 || m2_fail "required command is missing: $1"
}

m2_platform_psql() {
  m2_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m2_source_psql() {
  m2_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d standalone_app_db "$@"
}

m2_wait_service() {
  m2_service=$1
  m2_context=$2
  m2_attempt=0
  while true; do
    m2_container_id="$(m2_compose ps -q "${m2_service}")"
    if [[ -n "${m2_container_id}" ]]; then
      m2_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${m2_container_id}")"
      if [[ "${m2_state}" == "healthy" || "${m2_state}" == "running" ]]; then
        return 0
      fi
    fi
    m2_attempt=$((m2_attempt + 1))
    if ((m2_attempt >= 90)); then
      m2_compose ps -a >&2 || true
      m2_fail "${m2_context}: service did not become ready"
    fi
    sleep 1
  done
}

m2_wait_sql() {
  m2_database=$1
  m2_query=$2
  m2_expected=$3
  m2_context=$4
  m2_attempt=0
  while true; do
    if [[ "${m2_database}" == "platform" ]]; then
      m2_actual="$(m2_platform_psql -Atc "${m2_query}")"
    else
      m2_actual="$(m2_source_psql -Atc "${m2_query}")"
    fi
    [[ "${m2_actual}" == "${m2_expected}" ]] && return 0
    m2_attempt=$((m2_attempt + 1))
    if ((m2_attempt >= 90)); then
      m2_compose logs --no-color --tail 200 \
        standalone-outbox-publisher platform-projection-worker >&2 || true
      m2_fail "${m2_context}: expected ${m2_expected}, got ${m2_actual}"
    fi
    sleep 1
  done
}

m2_wait_queue() {
  m2_queue=$1
  m2_field=$2
  m2_expected=$3
  m2_context=$4
  m2_attempt=0
  while true; do
    m2_actual="$(m2_compose exec -T rabbitmq rabbitmqctl -q list_queues \
      --vhost ai-hub-local name "${m2_field}" \
      | awk -v queue="${m2_queue}" '$1 == queue {print $2}')"
    [[ "${m2_actual}" == "${m2_expected}" ]] && return 0
    m2_attempt=$((m2_attempt + 1))
    if ((m2_attempt >= 90)); then
      m2_fail "${m2_context}: expected ${m2_expected}, got ${m2_actual:-missing}"
    fi
    sleep 1
  done
}

m2_fixture() {
  m2_operation=$1
  m2_record=$2
  m2_name=$3
  m2_commit=$4
  m2_compose exec -T \
    -e M2_OPERATION="${m2_operation}" \
    -e M2_RECORD_ID="${m2_record}" \
    -e M2_RECORD_NAME="${m2_name}" \
    -e M2_COMMIT="${m2_commit}" \
    standalone-app-events python -c '
import asyncio
import json
import os
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from standalone_app.config import get_settings
from standalone_app.records import change_record, delete_record

async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        common = dict(
            session=session,
            application_id=settings.application_id,
            events_enabled="EVENT_PUBLISHER" in settings.capabilities,
            record_id=UUID(os.environ["M2_RECORD_ID"]),
            owner_subject=(
                "another-user" if os.environ["M2_RECORD_ID"].endswith("2")
                else "ai-hub-demo-user"
            ),
            actor_type="service",
            actor_id="m2-runtime-gate",
            trace_id="m2-runtime-gate",
        )
        if os.environ["M2_OPERATION"] == "delete":
            mutation = await delete_record(**common)
        else:
            mutation = await change_record(name=os.environ["M2_RECORD_NAME"], **common)
        if mutation is None:
            raise RuntimeError("record mutation was rejected")
        if os.environ["M2_COMMIT"] == "1":
            await session.commit()
        else:
            await session.rollback()
        print(json.dumps({
            "record_id": str(mutation.record_id),
            "aggregate_version": mutation.aggregate_version,
            "event_id": str(mutation.event.id) if mutation.event else None,
            "source_sequence": mutation.event.source_sequence if mutation.event else None,
        }))
    await engine.dispose()

asyncio.run(main())
'
}

m2_publish_event() {
  m2_event_id=$1
  m2_record=$2
  m2_aggregate_version=$3
  m2_source_sequence=$4
  m2_name=$5
  m2_type=${6:-company.example.record.changed.v1}
  m2_compose run --rm -T --no-deps \
    -e M2_EVENT_ID="${m2_event_id}" \
    -e M2_RECORD_ID="${m2_record}" \
    -e M2_AGGREGATE_VERSION="${m2_aggregate_version}" \
    -e M2_SOURCE_SEQUENCE="${m2_source_sequence}" \
    -e M2_RECORD_NAME="${m2_name}" \
    -e M2_EVENT_TYPE="${m2_type}" \
    --entrypoint python \
    standalone-outbox-publisher -c '
import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID
import aio_pika
from ai_hub_sdk import CloudEvent, EventActor
from standalone_app.config import get_event_publisher_settings

async def main():
    settings = get_event_publisher_settings()
    record_id = os.environ["M2_RECORD_ID"]
    event_type = os.environ["M2_EVENT_TYPE"]
    data = {"record_id": record_id}
    if event_type.endswith("changed.v1"):
        data.update(
            name=os.environ["M2_RECORD_NAME"],
            state="ACTIVE",
            owner_subject="ai-hub-demo-user",
        )
    event = CloudEvent(
        id=UUID(os.environ["M2_EVENT_ID"]),
        source="urn:ai-hub:application:standalone-example",
        type=event_type,
        subject=f"example-record/{record_id}",
        time=datetime.now(UTC),
        dataschema="https://ai-hub.example.internal/contracts/events/example-record-event-data.v1.schema.json",
        producer_application_id="standalone-example",
        event_version=1,
        aggregate_version=int(os.environ["M2_AGGREGATE_VERSION"]),
        source_sequence=int(os.environ["M2_SOURCE_SEQUENCE"]),
        object_type="example_record",
        trace_id="m2-runtime-gate",
        actor=EventActor(type="service", id="m2-runtime-gate"),
        data_classification="internal",
        data=data,
    )
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    exchange = await channel.get_exchange(settings.exchange_name, ensure=False)
    await exchange.publish(
        aio_pika.Message(
            body=event.model_dump_json(exclude_none=False).encode(),
            content_type="application/cloudevents+json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(event.id),
            type=event.type,
        ),
        routing_key=event.type,
        mandatory=True,
    )
    await connection.close()

asyncio.run(main())
'
}

m2_export_snapshot() {
  m2_output_path=$1
  m2_compose exec -T standalone-app-events standalone-snapshot-export >"${m2_output_path}"
}

m2_copy_snapshot_to_platform() {
  m2_source_path=$1
  m2_container_id="$(m2_compose ps -q platform-projection-worker)"
  docker cp "${m2_source_path}" "${m2_container_id}:/tmp/m2-snapshot.json"
}

m2_import_runtime_evidence() {
  m2_evidence_path="${M2_WORK_DIR}/conformance-evidence.json"
  m2_verified_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  jq -n \
    --arg application_id "${M2_SOURCE_APPLICATION}" \
    --arg verified_at "${m2_verified_at}" \
    --arg publisher_event_id "${m2_outage_event_id}" \
    --arg consumer_event_id "${m2_outage_event_id}" \
    --arg projection_event_id "${m2_incremental_version}" \
    '{
      application_id: $application_id,
      environment: "local",
      contract_version: "m3-conformance-0.2.0",
      source: "scripts/ci/m2-runtime.sh",
      verified_at: $verified_at,
      profiles: {
        EVENT_PUBLISHER: {
          status: "PASSED",
          evidence: {
            outbox_transaction_rollback: true,
            broker_outage_recovery: true,
            publisher_confirmed_event_id: $publisher_event_id
          }
        },
        EVENT_CONSUMER: {
          status: "PASSED",
          evidence: {
            application_inbox_atomic: true,
            duplicate_effect_count: 1,
            processed_event_id: $consumer_event_id
          }
        },
        PROJECTION_READER: {
          status: "PASSED",
          evidence: {
            duplicate_safe: true,
            gap_recovery: true,
            empty_schema_rebuild: true,
            incremental_version: $projection_event_id
          }
        }
      }
    }' >"${m2_evidence_path}"
  m2_api_container_id="$(m2_compose ps -q platform-api)"
  docker cp "${m2_evidence_path}" \
    "${m2_api_container_id}:/tmp/m3-conformance-evidence.json"
  m2_compose exec -T platform-api \
    ai-hub-conformance-evidence-import /tmp/m3-conformance-evidence.json \
    | jq --exit-status \
      '.imported == true and (.profiles | length == 3)' >/dev/null
}

for m2_command in awk curl docker jq sed; do
  m2_require_command "${m2_command}"
done

cd "${M2_PROJECT_ROOT}"

m2_note "verifying the API-only profile excludes every event dependency"
m2_base_services="$(m2_base_compose config --services)"
for m2_forbidden in rabbitmq rabbitmq-bootstrap standalone-outbox-publisher \
  standalone-event-consumer platform-projection-worker \
  standalone-event-publisher-migrate standalone-event-consumer-migrate \
  standalone-publisher-db-bootstrap platform-projection-migrate \
  standalone-consumer-db-bootstrap platform-event-registration-migrate \
  standalone-app-events; do
  if grep -Fxq "${m2_forbidden}" <<<"${m2_base_services}"; then
    m2_fail "base-access unexpectedly includes ${m2_forbidden}"
  fi
done

m2_note "starting a fresh isolated standard-events deployment"
if [[ "${M2_SKIP_BUILD:-0}" == "1" ]]; then
  m2_compose up -d --no-build
else
  m2_compose up -d --build
fi

for m2_migration in platform-core-migrate platform-event-registration-migrate \
  platform-projection-migrate standalone-migrate standalone-publisher-db-bootstrap \
  standalone-consumer-db-bootstrap standalone-event-publisher-migrate \
  standalone-event-consumer-migrate; do
  m2_container_id="$(m2_compose ps -a -q "${m2_migration}")"
  [[ -n "${m2_container_id}" ]] || m2_fail "migration container is missing: ${m2_migration}"
  m2_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "${m2_container_id}")"
  [[ "${m2_exit_code}" == "0" ]] || m2_fail "migration failed: ${m2_migration}"
done
m2_wait_service standalone-app-events "event-enabled standalone reference API"
m2_wait_service standalone-outbox-publisher "Outbox publisher"
m2_wait_service standalone-event-consumer "reference event consumer"
m2_wait_service platform-projection-worker "platform projection Worker"

m2_note "verifying capability-specific schemas, topology, and least-privilege identities"
m2_source_psql -Atc "SELECT to_regclass('app.integration_outbox') IS NOT NULL;" \
  | grep -Fxq t || m2_fail "EVENT_PUBLISHER Outbox is missing"
m2_source_psql -Atc "SELECT to_regclass('app.integration_inbox') IS NOT NULL;" \
  | grep -Fxq t || m2_fail "EVENT_CONSUMER Inbox is missing"
m2_source_psql -Atc "SELECT to_regclass('app.integration_consumer_effect') IS NOT NULL;" \
  | grep -Fxq t || m2_fail "EVENT_CONSUMER effect table is missing"
m2_platform_psql -Atc \
  "SELECT capabilities::text FROM platform_core.application WHERE application_id = '${M2_SOURCE_APPLICATION}';" \
  | grep -Fq EVENT_PUBLISHER || m2_fail "event capability registration is missing"
m2_platform_psql -Atc \
  "SELECT count(*) FROM platform_core.event_contract_registration WHERE producer_application_id = '${M2_SOURCE_APPLICATION}' AND status = 'ACTIVE';" \
  | grep -Fxq 2 || m2_fail "registered event contracts are missing"
m2_compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
  -f /opt/ai-hub/postgres-verify/role-boundaries.sql >/dev/null
m2_publisher_write_code=0
m2_compose exec -T postgres sh -c \
  "PGPASSWORD=local-only-standalone-publisher-password psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U standalone_outbox_publisher -d standalone_app_db -c \"UPDATE app.example_record SET name = 'forbidden';\"" \
  >/dev/null 2>&1 || m2_publisher_write_code=$?
[[ "${m2_publisher_write_code}" != "0" ]] \
  || m2_fail "Outbox publisher database role can modify application business data"

m2_permissions="$(m2_compose exec -T rabbitmq rabbitmqctl -q list_permissions \
  --vhost ai-hub-local --no-table-headers)"
grep -Eq '^standalone_publisher[[:space:]]+\^\$[[:space:]]+\^ai-hub\\\.events\$[[:space:]]+\^\$' \
  <<<"${m2_permissions}" || m2_fail "publisher permission is broader or different than registered"
grep -Eq '^platform_projection[[:space:]]+\^\$[[:space:]]+\^\$[[:space:]]+\^ai-hub\\\.platform\\\.projection\$' \
  <<<"${m2_permissions}" || m2_fail "projection permission is broader or different than registered"
grep -Eq '^standalone_consumer[[:space:]]+\^\$[[:space:]]+\^\$[[:space:]]+\^ai-hub\\\.standalone\\\.reference-consumer\$' \
  <<<"${m2_permissions}" || m2_fail "consumer permission is broader or different than registered"
m2_wait_queue ai-hub.platform.projection messages 0 "initial projection queue drain"
m2_wait_queue ai-hub.standalone.reference-consumer messages 0 \
  "initial consumer queue drain"
m2_wait_sql platform \
  "SELECT count(*) FROM platform_projection.example_record_projection WHERE producer_application_id = '${M2_SOURCE_APPLICATION}';" \
  2 "initial snapshot-equivalent events were not projected"

m2_note "verifying business data and Outbox share the exact transaction"
m2_before_name="$(m2_source_psql -Atc "SELECT name FROM app.example_record WHERE id = '${M2_RECORD_ID}';")"
m2_before_outbox="$(m2_source_psql -Atc "SELECT count(*) FROM app.integration_outbox;")"
m2_fixture change "${M2_RECORD_ID}" "M2 rollback must disappear" 0 >/dev/null
[[ "$(m2_source_psql -Atc "SELECT name FROM app.example_record WHERE id = '${M2_RECORD_ID}';")" == "${m2_before_name}" ]] \
  || m2_fail "rolled-back business change was persisted"
[[ "$(m2_source_psql -Atc "SELECT count(*) FROM app.integration_outbox;")" == "${m2_before_outbox}" ]] \
  || m2_fail "rolled-back transaction produced an Outbox row"

m2_note "verifying broker outage retention and recovery"
m2_compose stop rabbitmq >/dev/null
m2_outage_json="$(m2_fixture change "${M2_RECORD_ID}" "M2 broker outage retained" 1)"
m2_outage_version="$(jq -r '.aggregate_version' <<<"${m2_outage_json}")"
m2_outage_event_id="$(jq -r '.event_id' <<<"${m2_outage_json}")"
[[ "$(m2_source_psql -Atc "SELECT name FROM app.example_record WHERE id = '${M2_RECORD_ID}';")" == "M2 broker outage retained" ]] \
  || m2_fail "business fact did not commit while broker was unavailable"
[[ "$(m2_source_psql -Atc "SELECT status FROM app.integration_outbox WHERE event_id = '${m2_outage_event_id}';")" != "PUBLISHED" ]] \
  || m2_fail "Outbox claimed a publish while broker was unavailable"
m2_compose start rabbitmq >/dev/null
m2_wait_sql source \
  "SELECT status FROM app.integration_outbox WHERE event_id = '${m2_outage_event_id}';" \
  PUBLISHED "Outbox did not resume after RabbitMQ recovery"
m2_wait_sql platform \
  "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';" \
  "M2 broker outage retained:${m2_outage_version}" "projection did not recover after broker outage"
m2_wait_sql source \
  "SELECT count(*) FROM app.integration_inbox WHERE event_id = '${m2_outage_event_id}' AND processed_at IS NOT NULL;" \
  1 "application consumer did not commit its Inbox"
m2_wait_sql source \
  "SELECT count(*) FROM app.integration_consumer_effect WHERE event_id = '${m2_outage_event_id}';" \
  1 "application consumer did not commit its local effect"

m2_note "verifying duplicate delivery is Inbox-idempotent"
m2_inbox_before="$(m2_platform_psql -Atc "SELECT count(*) FROM platform_projection.integration_inbox WHERE event_id = '${m2_outage_event_id}';")"
m2_source_psql -c \
  "UPDATE app.integration_outbox SET status = 'PENDING', published_at = NULL, next_attempt_at = CURRENT_TIMESTAMP WHERE event_id = '${m2_outage_event_id}';" \
  >/dev/null
m2_wait_sql source \
  "SELECT status FROM app.integration_outbox WHERE event_id = '${m2_outage_event_id}';" \
  PUBLISHED "duplicate Outbox row was not republished"
m2_wait_queue ai-hub.platform.projection messages 0 "duplicate delivery was not consumed"
m2_wait_queue ai-hub.standalone.reference-consumer messages 0 \
  "duplicate delivery was not consumed by reference consumer"
[[ "$(m2_platform_psql -Atc "SELECT count(*) FROM platform_projection.integration_inbox WHERE event_id = '${m2_outage_event_id}';")" == "${m2_inbox_before}" ]] \
  || m2_fail "duplicate delivery created another Inbox record"
[[ "$(m2_platform_psql -Atc "SELECT aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';")" == "${m2_outage_version}" ]] \
  || m2_fail "duplicate delivery changed projection state"
[[ "$(m2_source_psql -Atc "SELECT count(*) FROM app.integration_inbox WHERE event_id = '${m2_outage_event_id}';")" == "1" ]] \
  || m2_fail "duplicate delivery created another application Inbox record"
[[ "$(m2_source_psql -Atc "SELECT count(*) FROM app.integration_consumer_effect WHERE event_id = '${m2_outage_event_id}';")" == "1" ]] \
  || m2_fail "duplicate delivery repeated the application consumer effect"

m2_note "verifying a crash before database commit causes redelivery"
m2_compose stop platform-projection-worker >/dev/null
m2_precommit_json="$(m2_fixture change "${M2_RECORD_ID}" "M2 redelivered before commit" 1)"
m2_precommit_version="$(jq -r '.aggregate_version' <<<"${m2_precommit_json}")"
AI_HUB_PROCESSING_DELAY_SECONDS=10 AI_HUB_ACKNOWLEDGEMENT_DELAY_SECONDS=0 \
  m2_compose up -d --no-deps --force-recreate platform-projection-worker >/dev/null
m2_wait_queue ai-hub.platform.projection messages_unacknowledged 1 \
  "worker never acquired the pre-commit crash message"
m2_compose kill platform-projection-worker >/dev/null
AI_HUB_PROCESSING_DELAY_SECONDS=0 AI_HUB_ACKNOWLEDGEMENT_DELAY_SECONDS=0 \
  m2_compose up -d --no-deps --force-recreate platform-projection-worker >/dev/null
m2_wait_sql platform \
  "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';" \
  "M2 redelivered before commit:${m2_precommit_version}" "pre-commit crash message was not redelivered"

m2_note "verifying a crash after commit but before acknowledgement is idempotent"
m2_compose stop platform-projection-worker >/dev/null
m2_postcommit_json="$(m2_fixture change "${M2_RECORD_ID}" "M2 duplicate after commit" 1)"
m2_postcommit_version="$(jq -r '.aggregate_version' <<<"${m2_postcommit_json}")"
m2_postcommit_event_id="$(jq -r '.event_id' <<<"${m2_postcommit_json}")"
AI_HUB_PROCESSING_DELAY_SECONDS=0 AI_HUB_ACKNOWLEDGEMENT_DELAY_SECONDS=10 \
  m2_compose up -d --no-deps --force-recreate platform-projection-worker >/dev/null
m2_wait_sql platform \
  "SELECT count(*) FROM platform_projection.integration_inbox WHERE event_id = '${m2_postcommit_event_id}' AND processed_at IS NOT NULL;" \
  1 "post-commit crash fixture did not commit"
m2_wait_queue ai-hub.platform.projection messages_unacknowledged 1 \
  "worker did not pause before acknowledgement"
m2_compose kill platform-projection-worker >/dev/null
AI_HUB_PROCESSING_DELAY_SECONDS=0 AI_HUB_ACKNOWLEDGEMENT_DELAY_SECONDS=0 \
  m2_compose up -d --no-deps --force-recreate platform-projection-worker >/dev/null
m2_wait_queue ai-hub.platform.projection messages 0 "post-commit duplicate was not consumed"
[[ "$(m2_platform_psql -Atc "SELECT count(*) FROM platform_projection.integration_inbox WHERE event_id = '${m2_postcommit_event_id}';")" == "1" ]] \
  || m2_fail "post-commit redelivery duplicated Inbox state"
[[ "$(m2_platform_psql -Atc "SELECT aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';")" == "${m2_postcommit_version}" ]] \
  || m2_fail "post-commit redelivery changed projection version"

m2_note "verifying explicit gap handling, ordered drain, and stale protection"
m2_sequence_base="$(m2_source_psql -Atc "SELECT current_sequence FROM app.integration_source_state WHERE application_id = '${M2_SOURCE_APPLICATION}';")"
m2_gap_v3=$((m2_postcommit_version + 1))
m2_gap_v4=$((m2_postcommit_version + 2))
m2_publish_event 50000000-0000-4000-8000-000000000004 "${M2_RECORD_ID}" \
  "${m2_gap_v4}" "$((m2_sequence_base + 2))" "M2 gap version four"
m2_wait_sql platform \
  "SELECT status || ':' || expected_version || ':' || received_version FROM platform_projection.projection_gap WHERE record_id = '${M2_RECORD_ID}';" \
  "OPEN:${m2_gap_v3}:${m2_gap_v4}" "version gap was not made explicit"
m2_publish_event 50000000-0000-4000-8000-000000000003 "${M2_RECORD_ID}" \
  "${m2_gap_v3}" "$((m2_sequence_base + 1))" "M2 gap version three"
m2_wait_sql platform \
  "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';" \
  "M2 gap version four:${m2_gap_v4}" "pending version was not drained after gap resolution"
m2_wait_sql platform \
  "SELECT status FROM platform_projection.projection_gap WHERE record_id = '${M2_RECORD_ID}';" \
  RESOLVED "gap was not marked resolved"
m2_publish_event 50000000-0000-4000-8000-000000000002 "${M2_RECORD_ID}" \
  "${m2_postcommit_version}" "$((m2_sequence_base + 3))" "M2 stale must not win"
m2_wait_queue ai-hub.platform.projection messages 0 "stale event was not consumed"
[[ "$(m2_platform_psql -Atc "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';")" == "M2 gap version four:${m2_gap_v4}" ]] \
  || m2_fail "stale aggregate version overwrote newer projection"

m2_note "verifying deletion propagation and permanent-failure DLQ"
m2_delete_json="$(m2_fixture delete "${M2_DELETE_RECORD_ID}" ignored 1)"
m2_delete_version="$(jq -r '.aggregate_version' <<<"${m2_delete_json}")"
m2_wait_sql platform \
  "SELECT aggregate_version || ':' || (deleted_at IS NOT NULL)::text FROM platform_projection.example_record_projection WHERE record_id = '${M2_DELETE_RECORD_ID}';" \
  "${m2_delete_version}:true" "delete event did not produce a tombstone"
m2_compose exec -T rabbitmq rabbitmqadmin \
  --host rabbitmq --port 15672 --username ai_hub_admin \
  --password local-only-rabbitmq-password --non-interactive \
  publish message --vhost ai-hub-local \
  --exchange ai-hub.events \
  --routing-key company.example.record.changed.v1 \
  --payload '{"contract":"invalid"}' \
  --properties '{"delivery_mode":2}' >/dev/null
m2_wait_queue ai-hub.platform.projection.dlq messages_ready 1 \
  "contract-invalid event did not reach the DLQ"

m2_note "verifying snapshot watermark rebuild from an empty projection schema"
m2_source_psql -c \
  "UPDATE app.integration_source_state SET current_sequence = GREATEST(current_sequence, $((m2_sequence_base + 3))) WHERE application_id = '${M2_SOURCE_APPLICATION}';" \
  >/dev/null
m2_source_psql -c \
  "UPDATE app.example_record SET name = 'M2 gap version four', aggregate_version = ${m2_gap_v4}, updated_at = CURRENT_TIMESTAMP WHERE id = '${M2_RECORD_ID}';" \
  >/dev/null
m2_snapshot_path="${M2_WORK_DIR}/snapshot.json"
m2_export_snapshot "${m2_snapshot_path}"
jq --exit-status \
  --argjson watermark "$((m2_sequence_base + 3))" \
  '.watermark == $watermark and (.checksum | length == 64)' \
  "${m2_snapshot_path}" >/dev/null
m2_compose stop platform-projection-worker >/dev/null
m2_compose run --rm -T --no-deps platform-projection-migrate \
  alembic -c /workspace/backend/alembic-projection.ini downgrade base >/dev/null
m2_platform_psql -Atc "SELECT to_regclass('platform_projection.example_record_projection') IS NULL;" \
  | grep -Fxq t || m2_fail "projection schema was not emptied"
m2_compose run --rm -T --no-deps platform-projection-migrate \
  alembic -c /workspace/backend/alembic-projection.ini upgrade head >/dev/null
AI_HUB_PROCESSING_DELAY_SECONDS=0 AI_HUB_ACKNOWLEDGEMENT_DELAY_SECONDS=0 \
  m2_compose up -d --no-deps --force-recreate platform-projection-worker >/dev/null
m2_copy_snapshot_to_platform "${m2_snapshot_path}"
m2_compose exec -T platform-projection-worker \
  ai-hub-projection-rebuild /tmp/m2-snapshot.json \
  | jq --exit-status '.rebuilt == true' >/dev/null
m2_wait_sql platform \
  "SELECT count(*) FROM platform_projection.example_record_projection WHERE producer_application_id = '${M2_SOURCE_APPLICATION}';" \
  1 "snapshot did not rebuild live records from empty schema"
m2_wait_sql platform \
  "SELECT last_snapshot_watermark FROM platform_projection.projection_checkpoint WHERE producer_application_id = '${M2_SOURCE_APPLICATION}';" \
  "$((m2_sequence_base + 3))" "snapshot watermark was not installed"

m2_incremental_json="$(m2_fixture change "${M2_RECORD_ID}" "M2 incremental after rebuild" 1)"
m2_incremental_version="$(jq -r '.aggregate_version' <<<"${m2_incremental_json}")"
m2_wait_sql platform \
  "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';" \
  "M2 incremental after rebuild:${m2_incremental_version}" "incremental event after snapshot was not applied"
m2_stale_rebuild_code=0
m2_compose exec -T platform-projection-worker \
  ai-hub-projection-rebuild /tmp/m2-snapshot.json \
  >/dev/null 2>&1 || m2_stale_rebuild_code=$?
[[ "${m2_stale_rebuild_code}" != "0" ]] \
  || m2_fail "an older snapshot was allowed to roll back an installed checkpoint"
[[ "$(m2_platform_psql -Atc "SELECT name || ':' || aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '${M2_RECORD_ID}';")" == "M2 incremental after rebuild:${m2_incremental_version}" ]] \
  || m2_fail "rejected stale snapshot changed projection state"
m2_fresh_snapshot_path="${M2_WORK_DIR}/fresh-snapshot.json"
m2_export_snapshot "${m2_fresh_snapshot_path}"
m2_copy_snapshot_to_platform "${m2_fresh_snapshot_path}"
m2_compose exec -T platform-projection-worker \
  ai-hub-projection-reconcile /tmp/m2-snapshot.json \
  | jq --exit-status '.consistent == true and (.missing_record_ids | length == 0) and (.mismatched_record_ids | length == 0)' \
  >/dev/null

m2_note "verifying the platform projection remains read-only outside its Worker"
m2_platform_write_code=0
m2_compose exec -T postgres sh -c \
  "PGPASSWORD=local-only-platform-password psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U ai_hub_platform -d platform_db -c \"UPDATE platform_projection.example_record_projection SET name = 'forbidden';\"" \
  >/dev/null 2>&1 || m2_platform_write_code=$?
[[ "${m2_platform_write_code}" != "0" ]] || m2_fail "platform API role can modify source-derived projection"
[[ "$(m2_source_psql -Atc "SELECT name FROM app.example_record WHERE id = '${M2_RECORD_ID}';")" == "M2 incremental after rebuild" ]] \
  || m2_fail "platform-side checks changed source business data"

m2_publisher_logs="$(m2_compose logs --no-color standalone-outbox-publisher)"
grep -Fq '"metrics"' <<<"${m2_publisher_logs}" \
  || m2_fail "Outbox publisher metrics are not observable"
m2_projection_logs="$(m2_compose logs --no-color platform-projection-worker)"
grep -Fq '"metrics"' <<<"${m2_projection_logs}" \
  || m2_fail "projection Worker metrics are not observable"

m2_note "recording digest-addressed runtime evidence for the M3 conformance service"
m2_import_runtime_evidence
m2_platform_psql -Atc \
  "SELECT count(*) FROM platform_core.conformance_runtime_evidence WHERE application_id = '${M2_SOURCE_APPLICATION}' AND environment = 'local' AND status = 'PASSED' AND expires_at > CURRENT_TIMESTAMP;" \
  | grep -Fxq 3 || m2_fail "runtime conformance evidence was not persisted"

m2_note "all reliable-event, failure, capability, and rebuild scenarios passed"
