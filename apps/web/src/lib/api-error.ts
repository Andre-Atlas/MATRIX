export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    /** Seconds from the Retry-After header (429/503), when the API sent one. */
    public readonly retryAfter?: number,
  ) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
  }
}

export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}
