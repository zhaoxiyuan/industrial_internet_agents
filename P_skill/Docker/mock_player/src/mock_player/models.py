class ToolError(Exception):
    def __init__(self, message, status=400, code="invalid_argument"):
        self.message, self.status, self.code = message, status, code
        super().__init__(message)


TERMINAL = {"completed", "stopped", "failed", "interrupted"}
