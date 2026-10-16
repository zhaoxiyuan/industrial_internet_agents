"""Complete package-local tools; no preinstalled business library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def claim_description(arguments, context):
    from _business.p7 import claim_description as implementation
    return implementation(arguments, context)


def complete_assessment(arguments, context):
    from _business.p7 import complete_assessment as implementation
    return implementation(arguments, context)


def report_closed_event(arguments, context):
    from _business.p7 import report_closed_event as implementation
    return implementation(arguments, context)


def read_event(arguments, context):
    from _business.p7 import read_event as implementation
    return implementation(arguments, context)


def renew_description_lease(arguments, context):
    from _business.p7 import renew_description_lease as implementation
    return implementation(arguments, context)


def fail_description(arguments, context):
    from _business.p7 import fail_description as implementation
    return implementation(arguments, context)
