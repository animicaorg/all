export function stringifyJson(value: unknown): string {
  return JSON.stringify(value, (_key, nestedValue) =>
    typeof nestedValue === "bigint" ? nestedValue.toString() : nestedValue
  );
}

export function serializeError(error: unknown): Record<string, unknown> {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
      ...(Object.prototype.hasOwnProperty.call(error, "code")
        ? { code: (error as Error & { code?: string }).code }
        : {})
    };
  }

  return { message: String(error) };
}
