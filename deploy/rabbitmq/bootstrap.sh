#!/usr/bin/env sh

set -eu

: "${RABBITMQ_ADMIN_USER:?Set RABBITMQ_ADMIN_USER}"
: "${RABBITMQ_ADMIN_PASSWORD:?Set RABBITMQ_ADMIN_PASSWORD}"
: "${RABBITMQ_VHOST:?Set RABBITMQ_VHOST}"
: "${RABBITMQ_PUBLISHER_USER:?Set RABBITMQ_PUBLISHER_USER}"
: "${RABBITMQ_PUBLISHER_PASSWORD:?Set RABBITMQ_PUBLISHER_PASSWORD}"
: "${RABBITMQ_PROJECTION_USER:?Set RABBITMQ_PROJECTION_USER}"
: "${RABBITMQ_PROJECTION_PASSWORD:?Set RABBITMQ_PROJECTION_PASSWORD}"
: "${RABBITMQ_CONSUMER_USER:?Set RABBITMQ_CONSUMER_USER}"
: "${RABBITMQ_CONSUMER_PASSWORD:?Set RABBITMQ_CONSUMER_PASSWORD}"
: "${RABBITMQ_OBSERVER_USER:?Set RABBITMQ_OBSERVER_USER}"
: "${RABBITMQ_OBSERVER_PASSWORD:?Set RABBITMQ_OBSERVER_PASSWORD}"

admin() {
  rabbitmqadmin \
    --host rabbitmq \
    --port 15672 \
    --username "${RABBITMQ_ADMIN_USER}" \
    --password "${RABBITMQ_ADMIN_PASSWORD}" \
    --non-interactive \
    "$@"
}

attempt=0
until admin show overview >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 30 ]; then
    printf 'RabbitMQ management API did not become ready\n' >&2
    exit 1
  fi
  sleep 2
done

admin declare vhost \
  --name "${RABBITMQ_VHOST}" \
  --default-queue-type quorum \
  --description "AI Hub standard-events isolated environment"

admin declare user \
  --name "${RABBITMQ_PUBLISHER_USER}" \
  --password "${RABBITMQ_PUBLISHER_PASSWORD}"
admin declare user \
  --name "${RABBITMQ_PROJECTION_USER}" \
  --password "${RABBITMQ_PROJECTION_PASSWORD}"
admin declare user \
  --name "${RABBITMQ_CONSUMER_USER}" \
  --password "${RABBITMQ_CONSUMER_PASSWORD}"
admin declare user \
  --name "${RABBITMQ_OBSERVER_USER}" \
  --password "${RABBITMQ_OBSERVER_PASSWORD}" --tags monitoring

admin declare permissions --vhost "${RABBITMQ_VHOST}" \
  --username "${RABBITMQ_PUBLISHER_USER}" \
  --configure '^$' --read '^$' --write '^ai-hub\.events$'
admin declare permissions --vhost "${RABBITMQ_VHOST}" \
  --username "${RABBITMQ_PROJECTION_USER}" \
  --configure '^$' --read '^ai-hub\.platform\.projection$' --write '^$'
admin declare permissions --vhost "${RABBITMQ_VHOST}" \
  --username "${RABBITMQ_CONSUMER_USER}" \
  --configure '^$' --read '^ai-hub\.standalone\.reference-consumer$' --write '^$'
admin declare permissions --vhost "${RABBITMQ_VHOST}" \
  --username "${RABBITMQ_OBSERVER_USER}" \
  --configure '^$' --read '^$' --write '^$'

admin declare user_limit \
  --username "${RABBITMQ_PUBLISHER_USER}" --name max-connections --value 4
admin declare user_limit \
  --username "${RABBITMQ_PROJECTION_USER}" --name max-connections --value 4
admin declare user_limit \
  --username "${RABBITMQ_CONSUMER_USER}" --name max-connections --value 4
admin declare user_limit \
  --username "${RABBITMQ_OBSERVER_USER}" --name max-connections --value 2
admin declare vhost_limit \
  --vhost "${RABBITMQ_VHOST}" --name max-connections --value 20
admin declare vhost_limit \
  --vhost "${RABBITMQ_VHOST}" --name max-queues --value 20

admin declare exchange --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.events --type topic --durable true --auto-delete false
admin declare exchange --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.dead-letter --type topic --durable true --auto-delete false

admin declare queue --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.platform.projection --type quorum --durable true --auto-delete false \
  --arguments '{"x-dead-letter-exchange":"ai-hub.dead-letter","x-dead-letter-routing-key":"platform.projection.failed","x-max-length":100000,"x-overflow":"reject-publish"}'
admin declare queue --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.platform.projection.dlq --type quorum --durable true --auto-delete false \
  --arguments '{"x-max-length":10000,"x-overflow":"reject-publish"}'
admin declare queue --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.standalone.reference-consumer --type quorum --durable true \
  --auto-delete false \
  --arguments '{"x-dead-letter-exchange":"ai-hub.dead-letter","x-dead-letter-routing-key":"standalone.reference-consumer.failed","x-max-length":100000,"x-overflow":"reject-publish"}'
admin declare queue --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub.standalone.reference-consumer.dlq --type quorum --durable true \
  --auto-delete false --arguments '{"x-max-length":10000,"x-overflow":"reject-publish"}'

admin declare binding --vhost "${RABBITMQ_VHOST}" \
  --source ai-hub.events --destination-type queue \
  --destination ai-hub.platform.projection \
  --routing-key 'company.example.record.*.v1'
admin declare binding --vhost "${RABBITMQ_VHOST}" \
  --source ai-hub.dead-letter --destination-type queue \
  --destination ai-hub.platform.projection.dlq \
  --routing-key 'platform.projection.failed'
admin declare binding --vhost "${RABBITMQ_VHOST}" \
  --source ai-hub.events --destination-type queue \
  --destination ai-hub.standalone.reference-consumer \
  --routing-key 'company.example.record.*.v1'
admin declare binding --vhost "${RABBITMQ_VHOST}" \
  --source ai-hub.dead-letter --destination-type queue \
  --destination ai-hub.standalone.reference-consumer.dlq \
  --routing-key 'standalone.reference-consumer.failed'

admin declare policy --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub-platform-projection-delivery-limit \
  --pattern '^ai-hub\.platform\.projection$' \
  --apply-to quorum_queues --priority 10 \
  --definition '{"delivery-limit":5,"dead-letter-strategy":"at-least-once"}'
admin declare policy --vhost "${RABBITMQ_VHOST}" \
  --name ai-hub-reference-consumer-delivery-limit \
  --pattern '^ai-hub\.standalone\.reference-consumer$' \
  --apply-to quorum_queues --priority 10 \
  --definition '{"delivery-limit":5,"dead-letter-strategy":"at-least-once"}'

printf 'RabbitMQ M2 topology and least-privilege identities are ready\n'
