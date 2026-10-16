"""P6 v4: one run, one time window, one description."""

def read_current_evidence(arguments, context):
    from .timeflow import read_current_evidence as implementation
    return implementation(arguments, context)

def read_current_window(arguments, context):
    from .timeflow import read_current_window as implementation
    return implementation(arguments, context)

def save_current_description(arguments, context):
    from .timeflow import save_current_description as implementation
    return implementation(arguments, context)

def fail_current_window(arguments, context):
    from .timeflow import fail_current_window as implementation
    return implementation(arguments, context)
