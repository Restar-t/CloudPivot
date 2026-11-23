/**
 * 认证状态（zustand）：token 与用户信息，localStorage 持久化。
 *
 * 权衡说明：localStorage 存 token 有 XSS 风险，课程项目取其简单性；
 * 生产可换 httpOnly cookie（后端配套 CSRF 防护）。此边界记入已知限制。
 */
import { create } from "zustand";

export interface AuthUser {
  id: number;
  username: string;
  quota_bytes: number;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

const STORAGE_KEY = "nd_auth";

interface Persisted {
  user: AuthUser | null;
  accessToken: string | null;
  refreshToken: string | null;
}

function load(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as Persisted;
  } catch {
    // 损坏数据当未登录处理
  }
  return { user: null, accessToken: null, refreshToken: null };
}

function persist(state: AuthState): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ user: state.user, accessToken: state.accessToken, refreshToken: state.refreshToken }),
  );
}

interface AuthState extends Persisted {
  setAuth: (tokens: TokenPair, user: AuthUser) => void;
  setTokens: (tokens: TokenPair) => void;
  setUser: (user: AuthUser) => void;
  clear: () => void;
}

const initial = load();

export const useAuthStore = create<AuthState>((set) => ({
  user: initial.user,
  accessToken: initial.accessToken,
  refreshToken: initial.refreshToken,

  setAuth: (tokens, user) =>
    set((state) => {
      const next = { ...state, accessToken: tokens.access_token, refreshToken: tokens.refresh_token, user };
      persist(next);
      return next;
    }),
  setTokens: (tokens) =>
    set((state) => {
      const next = { ...state, accessToken: tokens.access_token, refreshToken: tokens.refresh_token };
      persist(next);
      return next;
    }),
  setUser: (user) =>
    set((state) => {
      const next = { ...state, user };
      persist(next);
      return next;
    }),
  clear: () =>
    set(() => {
      localStorage.removeItem(STORAGE_KEY);
      return { user: null, accessToken: null, refreshToken: null };
    }),
}));
