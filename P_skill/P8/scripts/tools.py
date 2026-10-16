"""Complete package-local tools; no preinstalled business library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def claim_risk_report(arguments, context):
    from _business.disposition import claim_risk_report as implementation
    return implementation(arguments, context)


def initialize_disposition(arguments, context):
    from _business.disposition import initialize_disposition as implementation
    return implementation(arguments, context)


def read_disposition(arguments, context):
    from _business.disposition import read_disposition as implementation
    return implementation(arguments, context)


def read_review_snapshot(arguments, context):
    from _business.disposition import read_review_snapshot as implementation
    return implementation(arguments, context)


def read_material(arguments, context):
    from _business.disposition import read_material as implementation
    return implementation(arguments, context)


def save_review_opinion(arguments, context):
    from _business.disposition import save_review_opinion as implementation
    return implementation(arguments, context)


def verify_job_closable(arguments, context):
    from _business.disposition import verify_job_closable as implementation
    return implementation(arguments, context)


def fail_risk_report(arguments, context):
    from _business.disposition import fail_risk_report as implementation
    return implementation(arguments, context)
