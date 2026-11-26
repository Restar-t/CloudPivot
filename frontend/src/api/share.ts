/** 分享接口（M5）：成员侧 + 公开侧。 */
import axios from "axios";
import { request } from "./request";

export interface ShareItem {
  id: number;
  token: string;
  file_name: string;
  file_size: number;
  file_exists: boolean;
  visit_count: number;
  max_visits: number | null;
  expire_at: string | null;
  status: "ACTIVE" | "CANCELLED";
  last_visit_at: string | null;
  created_at: string;
}

export function createShare(payload: {
  file_id: number;
  extract_code?: string;
  expire_days?: number;
  max_visits?: number;
}): Promise<{
  id: number;
  token: string;
  extract_code: string;
  max_visits: number | null;
  expire_at: string | null;
  file_name: string;
  file_size: number;
}> {
  return request.post("/shares", payload);
}

export function listShares(): Promise<{ items: ShareItem[] }> {
  return request.get("/shares");
}

export function cancelShare(id: number): Promise<{ cancelled: boolean; auth_epoch: number }> {
  return request.post(`/shares/${id}/cancel`);
}

// ---------- 公开侧（免登录） ----------
export interface ShareAuthResult {
  session_token: string;
  expires_at: string;
  file: { id: number; name: string; size: number; updated_at: string | null };
}

export function authShare(
  token: string,
  extractCode: string,
  idempotencyKey?: string,
): Promise<ShareAuthResult> {
  // 公开接口不要求登录：用裸 axios 绕开 Bearer 附加与 401 刷新逻辑；
  // 错误包 {code,message} 原样抛 ApiError 风格 Error 供页面展示
  return axios
    .post(`/api/s/${token}/auth`, {
      extract_code: extractCode,
      idempotency_key: idempotencyKey ?? crypto.randomUUID(),
    })
    .then((resp) => resp.data.data as ShareAuthResult)
    .catch((err) => {
      const body = err.response?.data as { code?: number; message?: string } | undefined;
      const e = new Error(body?.message || "验证失败") as Error & { code?: number };
      e.code = body?.code ?? 9999;
      throw e;
    });
}

export function shareDownloadUrl(token: string, session: string): string {
  return `/api/s/${token}/download?session=${encodeURIComponent(session)}`;
}

export function sharePreviewUrl(token: string, session: string): string {
  return `/api/s/${token}/preview?session=${encodeURIComponent(session)}`;
}
