/**
 * RebornStudio API 계약(TypeScript)의 진입점.
 *
 * 사용 예:
 *   import type { paths, components } from "@reborn/contracts/api";
 *
 *   type UserDTO = components["schemas"]["UserDTO"];
 *   type LoginResponse = paths["/auth/login"]["post"]["responses"]["200"]["content"]["application/json"];
 *
 * 갱신: `pnpm --filter @reborn/contracts gen`
 *   또는 repo root에서 `make contracts`
 */
export type { components, paths, operations } from "./api.js";
