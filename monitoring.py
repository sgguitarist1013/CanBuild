"""Opt-in Sentry error monitoring. Credentials are supplied by the host."""
import os
import sentry_sdk


def scrub_event(event, hint):
    # Property searches contain visitor-entered addresses. Do not send request data.
    event.pop("request", None)
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    return event


def configure_monitoring():
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("RENDER_GIT_COMMIT"),
        send_default_pii=False,
        traces_sample_rate=0,
        include_local_variables=False,
        before_send=scrub_event,
    )
