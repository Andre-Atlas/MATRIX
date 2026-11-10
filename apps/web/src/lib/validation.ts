export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 256;

export function validateNewPassword(current: string, next: string, confirm: string): string | null {
  if (!current) return "Enter your current password.";
  if (next.length < PASSWORD_MIN) return `The new password must have at least ${PASSWORD_MIN} characters.`;
  if (next.length > PASSWORD_MAX) return `The new password must have at most ${PASSWORD_MAX} characters.`;
  if (next === current) return "The new password must differ from the current one.";
  if (next !== confirm) return "The confirmation does not match the new password.";
  return null;
}

export function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()) && value.length <= 320;
}

/** "in 30 s" style text for a Retry-After (seconds). */
export function retryText(seconds: number): string {
  const s = Math.max(1, Math.min(3600, Math.ceil(seconds)));
  return s >= 120 ? `${Math.ceil(s / 60)} min` : `${s} s`;
}
