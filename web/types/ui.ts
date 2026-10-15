/**
 * Frontend-only types for UI state.
 *
 * API response types come from web/lib/api.ts (or web/generated/api/ after running
 * `make generate-types`). Only put types here that are purely UI concerns.
 */

export interface NavItem {
  href: string;
  label: string;
}
