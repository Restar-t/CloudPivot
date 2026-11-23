/**
 * axios 封装：统一响应包 {code, message, data} 拦截 + 错误提示 + 401 自动刷新。
 *
 * - 请求拦截：自动附加 Bearer access token
 * - code === 0：拦截器直接返回 data（业务代码拿到干净数据）
 * - code !== 0 / HTTP 错误：弹出 message 并 reject(ApiError)
 * - HTTP 401（access 过期 1002 / 会话失效 1003）：单飞 refresh → 重放原请求；
 *   refresh 也失败 → 清空会话并跳转登录页
 */
import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { message } from "antd";
import { useAuthStore } from "../stores/auth";
import { rawRefresh } from "./auth";

export class ApiError extends Error {
  code: number;

  constructor(code: number, msg: string) {
    super(msg);
    this.name = "ApiError";
    this.code = code;
  }
}

export const request = axios.create({
  baseURL: "/api",
  timeout: 15000,
});

// 不触发自动刷新的路径（认证入口本身）
const AUTH_FREE_PATHS = ["/auth/login", "/auth/register", "/auth/refresh"];

let refreshPromise: Promise<void> | null = null;

function doRefresh(refreshToken: string): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = rawRefresh(refreshToken)
      .then((tokens) => {
        useAuthStore.getState().setTokens(tokens);
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

request.interceptors.request.use((config) => {
  const { accessToken } = useAuthStore.getState();
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

request.interceptors.response.use(
  (resp) => {
    const body = resp.data;
    if (body && typeof body === "object" && "code" in body) {
      if (body.code !== 0) {
        message.error(body.message || "请求失败");
        return Promise.reject(new ApiError(body.code, body.message || "请求失败"));
      }
      return body.data;
    }
    return body;
  },
  async (error: AxiosError) => {
    const status = error.response?.status;
    const body = error.response?.data as { code?: number; message?: string } | undefined;
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    const path = config?.url ?? "";

    const retriable =
      status === 401 &&
      config &&
      !config._retried &&
      !AUTH_FREE_PATHS.some((p) => path.includes(p));

    if (retriable) {
      const { refreshToken, clear } = useAuthStore.getState();
      if (refreshToken) {
        try {
          await doRefresh(refreshToken);
          config._retried = true;
          const { accessToken } = useAuthStore.getState();
          config.headers.Authorization = `Bearer ${accessToken}`;
          return request.request(config);
        } catch {
          clear();
          message.error("登录已失效，请重新登录");
          window.location.href = "/login";
          return Promise.reject(new ApiError(1003, "登录已失效"));
        }
      }
      clear();
      window.location.href = "/login";
    }

    const msg =
      body?.message || (status === 503 ? "服务暂不可用" : "") || error.message || "网络错误";
    if (!(status === 401 && AUTH_FREE_PATHS.some((p) => path.includes(p)))) {
      message.error(msg);
    }
    return Promise.reject(new ApiError(body?.code ?? 9999, msg));
  },
);
