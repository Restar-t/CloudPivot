/**
 * axios 封装：统一响应包 {code, message, data} 拦截 + 错误提示。
 *
 * - code === 0：拦截器直接返回 data（业务代码拿到的就是干净数据）
 * - code !== 0：弹出 message 并 reject(ApiError)
 * - HTTP 401：统一处理占位（M1 接入 refresh 重放与跳转登录）
 * - 网络错误：弹出 message 并 reject
 */
import axios from "axios";
import { message } from "antd";

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
  (error) => {
    const status = error.response?.status;
    const body = error.response?.data;
    // TODO(M1)：401 → 尝试 refresh → 重放原请求 → 失败跳转登录页
    if (status === 401) {
      console.warn("[auth] 401，等待 M1 接入自动刷新", body?.code);
    }
    const msg =
      body?.message || (status === 503 ? "服务暂不可用" : "") || error.message || "网络错误";
    message.error(msg);
    return Promise.reject(new ApiError(body?.code ?? 9999, msg));
  },
);
