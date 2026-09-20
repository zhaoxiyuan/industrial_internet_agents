export class GatewayError extends Error {
  constructor(code, message, options = {}) {
    const { cause, status = 500, details, retryable = false, ambiguous = false } = options;
    super(message, cause ? { cause } : undefined);
    this.name = "GatewayError";
    this.code = code;
    this.status = status;
    this.details = details;
    this.retryable = retryable;
    this.ambiguous = ambiguous;
  }
}

export class PlatformSendError extends GatewayError {
  constructor(code, message, options = {}) {
    super(code, message, {
      status: options.status ?? 502,
      details: options.details,
      retryable: options.retryable ?? false,
      ambiguous: options.ambiguous ?? false,
      cause: options.cause,
    });
    this.name = "PlatformSendError";
    this.safeToRetry = options.safeToRetry ?? false;
  }
}

export function asGatewayError(error) {
  if (error instanceof GatewayError) {
    return error;
  }
  return new GatewayError("INTERNAL_ERROR", "Internal gateway error", {
    status: 500,
    cause: error instanceof Error ? error : undefined,
  });
}

export function errorBody(error, requestId) {
  const converted = asGatewayError(error);
  const body = {
    error: {
      code: converted.code,
      message: converted.message,
      request_id: requestId,
      retryable: Boolean(converted.retryable),
      ambiguous: Boolean(converted.ambiguous),
    },
  };
  if (converted.details !== undefined) {
    body.error.details = converted.details;
  }
  return { status: converted.status, body };
}
