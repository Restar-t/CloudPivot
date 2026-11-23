/** 认证接口封装。refresh 用裸 axios（绕开拦截器，避免 401 刷新递归）。 */
import axios from "axios";
import { request } from "./request";
import type { AuthUser } from "../stores/auth";
import type { TokenPair } from "../stores/auth";

export interface LoginResult extends TokenPair {
  user: AuthUser;
}

export function apiRegister(username: string, password: string): Promise<AuthUser> {
  return request.post("/auth/register", { username, password });
}

export function apiLogin(username: string, password: string): Promise<LoginResult> {
  return request.post("/auth/login", { username, password });
}

export function apiLogout(refreshToken: string | null): Promise<boolean> {
  return request.post("/auth/logout", { refresh_token: refreshToken });
}

export function apiMe(): Promise<AuthUser> {
  return request.get("/users/me");
}

export function apiChangePassword(
  oldPassword: string,
  newPassword: string,
  refreshToken: string | null,
): Promise<TokenPair> {
  return request.put(
    "/auth/password",
    { old_password: oldPassword, new_password: newPassword },
    { headers: refreshToken ? { "X-Refresh-Token": refreshToken } : undefined },
  );
}

/** 供 request.ts 拦截器调用：单飞刷新。失败时抛错由调用方清理会话。 */
export async function rawRefresh(refreshToken: string): Promise<TokenPair> {
  const resp = await axios.post("/api/auth/refresh", { refresh_token: refreshToken }, { timeout: 10000 });
  const body = resp.data;
  if (!body || body.code !== 0) {
    throw new Error(body?.message || "刷新失败");
  }
  return body.data as TokenPair;
}
