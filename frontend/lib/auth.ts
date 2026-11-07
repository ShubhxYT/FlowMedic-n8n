export const SESSION_COOKIE = "fm_session";
export const CSRF_COOKIE = "fm_csrf";
export const cookieNames = new Set([SESSION_COOKIE, CSRF_COOKIE]);

export function selectedCookies(header: string): string {
  return header.split(";").map((part) => part.trim()).filter((part) =>
    cookieNames.has(part.split("=", 1)[0]),
  ).join("; ");
}

export function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const part = document.cookie.split(";").map((value) => value.trim())
    .find((value) => value.startsWith(`${CSRF_COOKIE}=`));
  return part ? decodeURIComponent(part.slice(CSRF_COOKIE.length + 1)) : "";
}
