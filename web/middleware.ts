import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "bapc_session";
const PUBLIC_PATHS = ["/login", "/register"];

/**
 * Fast first gate: visitors without a session cookie go straight to /login.
 * Whether the session is valid, and what the user's role allows, is enforced by the backend.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.includes(pathname) || request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next();
  }
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  // Skip API proxying, Next internals, and static files.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico)$).*)"],
};
