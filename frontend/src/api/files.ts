/** 目录/文件接口封装。 */
import { request } from "./request";

export interface FileItem {
  id: number;
  name: string;
  is_folder: boolean;
  size: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ChildrenPage {
  items: FileItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface PathEntry {
  id: number;
  name: string;
}

export interface ListParams {
  page?: number;
  page_size?: number;
  sort?: "name" | "size" | "uploaded_at";
  order?: "asc" | "desc";
}

export function listChildren(
  folderId: number,
  params: ListParams & { folder_only?: boolean } = {},
): Promise<ChildrenPage> {
  return request.get(`/folders/${folderId}/children`, { params });
}

export function getPath(folderId: number): Promise<PathEntry[]> {
  return request.get(`/folders/${folderId}/path`).then((d) => (d as unknown as { items: PathEntry[] }).items);
}

export function createFolder(parentId: number, name: string): Promise<FileItem> {
  return request.post("/folders", { parent_id: parentId, name });
}

export function renameFile(fileId: number, newName: string): Promise<FileItem> {
  return request.patch(`/files/${fileId}/rename`, { new_name: newName });
}

// ---------- M4 ----------
export interface BatchResult {
  results: { id: number; ok: boolean; code?: number; message?: string; new_ids?: number[]; trashed?: number; name?: string }[];
}

export function moveFiles(fileIds: number[], targetFolderId: number): Promise<BatchResult> {
  return request.post("/files/move", { file_ids: fileIds, target_folder_id: targetFolderId });
}

export function copyFiles(fileIds: number[], targetFolderId: number): Promise<BatchResult> {
  return request.post("/files/copy", { file_ids: fileIds, target_folder_id: targetFolderId });
}

export function batchDelete(fileIds: number[]): Promise<BatchResult> {
  return request.post("/files/batch-delete", { file_ids: fileIds });
}

export function searchFiles(
  q: string,
  params: ListParams = {},
): Promise<ChildrenPage> {
  return request.get("/files/search", { params: { q, ...params } });
}
