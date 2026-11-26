/** 回收站接口（M4）。 */
import { request } from "./request";

export interface TrashItem {
  id: number;
  name: string;
  is_folder: boolean;
  size: number;
  deleted_at: string;
  remaining_days: number;
  parent_id: number;
  original_path: string;
}

export interface TrashPage {
  items: TrashItem[];
  total: number;
  page: number;
  page_size: number;
}

export function listTrash(page = 1, pageSize = 50): Promise<TrashPage> {
  return request.get("/trash", { params: { page, page_size: pageSize } });
}

export function restoreFromTrash(fileId: number): Promise<{
  id: number;
  name: string;
  parent_id: number;
  restored_rows: number;
}> {
  return request.post(`/trash/${fileId}/restore`);
}

export function purgeFromTrash(fileId: number): Promise<{
  purged: boolean;
  files: number;
  bytes_released: number;
}> {
  return request.delete(`/trash/${fileId}`);
}

export function clearTrash(): Promise<{ roots: number; files: number; bytes_released: number }> {
  return request.delete("/trash");
}
