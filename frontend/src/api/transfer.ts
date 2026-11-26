/**
 * 文件传输接口（M3）：上传（带进度）/ 下载（二进制流）/ 删除 / 配额。
 *
 * 下载不走 request 拦截器：拦截器按响应包 {code,message,data} 解包，
 * 而文件响应是二进制流 + 需要读取响应头（文件名、长度）。此处用裸实例手动带 token。
 */
import axios from "axios";
import { request } from "./request";
import { useAuthStore } from "../stores/auth";
import type { FileItem } from "./files";

export interface UploadResult extends FileItem {
  version_no: number;
  storage_object_id: number;
  sha256: string;
}

export interface QuotaInfo {
  quota_bytes: number;
  used_bytes: number;
  remaining_bytes: number;
}

const UPLOAD_TIMEOUT_MS = 10 * 60 * 1000; // 大文件上传给足时间
const MAX_FILE_SIZE = 5 * 1024 ** 3; // 与后端 UPLOAD_MAX_FILE_SIZE 默认值一致（M3 前端预检）

/** 后端白名单（与 .env UPLOAD_TYPE_WHITELIST 默认值一致）；真正裁决权在服务端。 */
export const ALLOWED_EXTENSIONS = new Set([
  "jpg", "jpeg", "png", "gif", "webp", "bmp", "ico", "pdf", "txt", "md", "csv", "json", "log",
  "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z", "tar", "gz", "mp4", "mp3", "wav", "mkv",
]);

export function precheckFile(file: File): string | null {
  /** 上传前预检，返回错误文案（null 表示通过）。只做友好提示，服务端仍会强制校验。 */
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!ext || !ALLOWED_EXTENSIONS.has(ext)) return `不支持的文件类型 .${ext || "(无扩展名)"}`;
  if (file.size <= 0) return "不能上传空文件";
  if (file.size > MAX_FILE_SIZE) return "文件超过 5GB 上限";
  return null;
}

export function uploadFile(
  parentId: number,
  file: File,
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("parent_id", String(parentId));
  form.append("file", file);
  return request.post("/files/upload", form, {
    timeout: UPLOAD_TIMEOUT_MS,
    signal,
    onUploadProgress: (e) => {
      if (e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  }) as unknown as Promise<UploadResult>;
}

/** 下载并触发浏览器保存。鉴权失败时后端返回 JSON 错误包（需读出来提示）。 */
export async function downloadFile(fileId: number, fallbackName: string): Promise<void> {
  const { accessToken } = useAuthStore.getState();
  const resp = await axios.get(`/api/files/${fileId}/download`, {
    responseType: "blob",
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  const contentType = String(resp.headers["content-type"] ?? "");
  if (contentType.includes("application/json")) {
    // 业务错误：把 blob 还原成错误包
    const body = JSON.parse(await (resp.data as Blob).text());
    throw new Error(body.message || "下载失败");
  }
  const disposition = String(resp.headers["content-disposition"] ?? "");
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  const realName = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : asciiMatch
      ? asciiMatch[1]
      : fallbackName;
  saveBlob(resp.data as Blob, realName);
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function trashFile(fileId: number): Promise<{ trashed: number; name: string }> {
  return request.delete(`/files/${fileId}`);
}

export function getQuota(): Promise<QuotaInfo> {
  return request.get("/users/me/quota");
}
