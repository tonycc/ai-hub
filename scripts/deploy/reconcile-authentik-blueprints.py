"""Run inside the trusted Authentik worker via ``ak shell``, never platform-api.

Use Authentik's task implementation so validation/apply failures update the
instance status. The pinned apply_blueprint management command ignores the
apply() result; its exit code alone is not a convergence receipt.
"""

import os
import sys
import time
from urllib.parse import urlsplit

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


def wait_for_background_imports(instances, pending_tasks, *, timeout=TIMEOUT_SECONDS):
    # Discovery creates the instance before its first apply finishes. Starting
    # another import then can race on objects without unique identifiers (Brand).
    # Drain discovery and apply tasks, including built-in blueprint dependencies,
    # before scheduling this deployment's strictly ordered applications.
    print("Waiting for Authentik background blueprint imports", flush=True)
    deadline = time.monotonic() + timeout
    while True:
        for instance in instances:
            instance.refresh_from_db()
            if not instance.enabled:
                raise ConvergenceError(f"Authentik blueprint is disabled: {instance.path}")
        if (
            all(instance.status != "unknown" for instance in instances)
            and not pending_tasks.exists()
        ):
            return
        if time.monotonic() >= deadline:
            raise ConvergenceError("Timed out waiting for Authentik background blueprint imports")
        time.sleep(2)


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


def _redirect_entries(value, redirect_type):
    urls = [item.strip() for item in value.split(",")]
    if not urls or any(not item for item in urls) or len(set(urls)) != len(urls):
        raise ConvergenceError(f"Invalid {redirect_type} redirect URI list")
    entries = []
    for url in urls:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ConvergenceError(f"Invalid {redirect_type} redirect URI")
        if redirect_type == "authorization" and parsed.path != "/auth/callback":
            raise ConvergenceError("Authorization redirect URI must use /auth/callback")
        if redirect_type == "logout" and parsed.path != "/":
            raise ConvergenceError("Logout redirect URI must use /")
        entries.append(
            {
                "matching_mode": "strict",
                "url": url,
                "redirect_uri_type": redirect_type,
            }
        )
    return entries


def reconcile_portal_redirect_uris(provider_model):
    authorization = os.environ.get("AI_HUB_PORTAL_OIDC_REDIRECT_URIS") or os.environ.get(
        "AI_HUB_PORTAL_OIDC_REDIRECT_URI", ""
    )
    logout = os.environ.get("AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URIS") or os.environ.get(
        "AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI", ""
    )
    desired = _redirect_entries(authorization, "authorization") + _redirect_entries(
        logout, "logout"
    )
    providers = list(provider_model.objects.filter(name="ai-hub-portal"))
    if len(providers) != 1:
        raise ConvergenceError("Expected exactly one ai-hub-portal OAuth2 provider")
    provider = providers[0]
    provider.redirect_uris = desired
    provider.full_clean()
    provider.save(update_fields=["redirect_uris"])
    provider.refresh_from_db()
    if provider.redirect_uris != desired:
        raise ConvergenceError("AI Hub Portal redirect URI reconciliation did not converge")
    print(f"Reconciled {len(desired)} AI Hub Portal redirect URIs", flush=True)


def main():
    from authentik.blueprints.models import BlueprintInstance
    from authentik.blueprints.v1.tasks import apply_blueprint, blueprints_discovery
    from authentik.providers.oauth2.models import OAuth2Provider
    from authentik.tasks.models import Task, TaskStatus
    from authentik.tenants.models import Tenant

    tenants = list(Tenant.objects.filter(ready=True))
    if len(tenants) != 1:
        raise ConvergenceError("Expected one ready Authentik tenant for the single-node deployment")
    with tenants[0]:
        instances = wait_for_instances(BlueprintInstance)
        pending_tasks = Task.objects.filter(
            tenant=tenants[0],
            actor_name__in=[blueprints_discovery.actor_name, apply_blueprint.actor_name],
        ).exclude(state__in=[TaskStatus.DONE, TaskStatus.REJECTED])
        wait_for_background_imports(instances, pending_tasks)
        apply_instances(instances, apply_blueprint)
        reconcile_portal_redirect_uris(OAuth2Provider)


if __name__ == "__main__":
    try:
        main()
    except ConvergenceError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(f"Authentik blueprint convergence failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None
