"""Run inside the trusted Authentik worker via ``ak shell``, never platform-api.

Use Authentik's task implementation so validation/apply failures update the
instance status. The pinned apply_blueprint management command ignores the
apply() result; its exit code alone is not a convergence receipt.
"""

import sys
import time

BLUEPRINT_PATHS = (
    "ai-hub/ai-hub-blueprint.yaml",
    "ai-hub/ai-hub-production-blueprint.yaml",
)
TIMEOUT_SECONDS = 300


class ConvergenceError(RuntimeError):
    """A safe, secret-free deployment error."""


def wait_for_instances(model, *, timeout=TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    while True:
        instances = []
        for path in BLUEPRINT_PATHS:
            matches = list(model.objects.filter(path=path))
            if len(matches) > 1:
                raise ConvergenceError(f"Multiple Authentik blueprint instances for {path}")
            if matches:
                instances.append(matches[0])
        if len(instances) == len(BLUEPRINT_PATHS):
            return instances
        if time.monotonic() >= deadline:
            raise ConvergenceError("Timed out waiting for mounted Authentik blueprints")
        time.sleep(5)


def apply_instances(instances, task, *, timeout=TIMEOUT_SECONDS):
    for instance in instances:
        instance.refresh_from_db()
        if not instance.enabled:
            raise ConvergenceError(f"Authentik blueprint is disabled: {instance.path}")
        previous_applied = instance.last_applied
        try:
            message = task.send_with_options(
                args=(instance.pk,), rel_obj=instance, store_results=True
            )
            # Wait for this specific invocation, not an unrelated background
            # import or a stale successful status from the previous IP.
            message.get_result(block=True, timeout=timeout * 1000)
        except Exception as error:
            # Task exceptions can contain resolved blueprint values/secrets.
            raise ConvergenceError(
                f"Authentik blueprint task failed for {instance.path} ({type(error).__name__})"
            ) from None
        instance.refresh_from_db()
        if instance.status != "successful":
            raise ConvergenceError(f"Authentik blueprint apply failed: {instance.path}")
        if instance.last_applied is None or (
            previous_applied is not None and instance.last_applied <= previous_applied
        ):
            raise ConvergenceError(
                f"Authentik blueprint did not record a new apply: {instance.path}"
            )
        print(f"Applied Authentik blueprint: {instance.path}", flush=True)


def main():
    from authentik.blueprints.models import BlueprintInstance
    from authentik.blueprints.v1.tasks import apply_blueprint
    from authentik.tenants.models import Tenant

    tenants = list(Tenant.objects.filter(ready=True))
    if len(tenants) != 1:
        raise ConvergenceError("Expected one ready Authentik tenant for the single-node deployment")
    with tenants[0]:
        instances = wait_for_instances(BlueprintInstance)
        apply_instances(instances, apply_blueprint)


if __name__ == "__main__":
    try:
        main()
    except ConvergenceError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(f"Authentik blueprint convergence failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
