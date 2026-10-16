"""Complete package-local tools; no preinstalled business library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def read_current_window(arguments, context):
    from _business.p6 import read_current_window as implementation
    return implementation(arguments, context)


def read_current_evidence(arguments, context):
    from _business.p6 import read_current_evidence as implementation
    return implementation(arguments, context)


def save_current_description(arguments, context):
    from _business.p6 import save_current_description as implementation
    return implementation(arguments, context)


def fail_current_window(arguments, context):
    from _business.p6 import fail_current_window as implementation
    return implementation(arguments, context)
