"""Complete package-local tools; no preinstalled business library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def bind_work_permit(arguments, context):
    from _business.main import bind_work_permit as implementation
    return implementation(arguments, context)


def query_pipeline(arguments, context):
    from _business.main import query_pipeline as implementation
    return implementation(arguments, context)


def retry_failed(arguments, context):
    from _business.main import retry_failed as implementation
    return implementation(arguments, context)


def reopen_monitoring(arguments, context):
    from _business.main import reopen_monitoring as implementation
    return implementation(arguments, context)


def configure_schedule(arguments, context):
    from _business.main import configure_schedule as implementation
    return implementation(arguments, context)


def list_time_windows(arguments, context):
    from _business.main import list_time_windows as implementation
    return implementation(arguments, context)


def retry_time_window(arguments, context):
    from _business.main import retry_time_window as implementation
    return implementation(arguments, context)


def close_time_monitoring(arguments, context):
    from _business.main import close_time_monitoring as implementation
    return implementation(arguments, context)


def configure_participants(arguments, context):
    from _business.main import configure_participants as implementation
    return implementation(arguments, context)
