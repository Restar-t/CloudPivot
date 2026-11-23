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

export function listChildren(folderId: number, params: ListParams = {}): Promise<ChildrenPage> {
  return request.get(`/folders/${folderId}/children`, { params });
}

export async function getPath(folderId: number): Promise<PathEntry[]> {
  // 拦截器已解包响应包，此处类型断言到业务形状
  const d = await request.get(`/folders/${folderId}/path`);
  return (d as unknown as { items: PathEntry[] }).items;
}

export function createFolder(parentId: number, name: string): Promise<FileItem> {
  return request.post("/folders", { parent_id: parentId, name });
}

export function renameFile(fileId: number, newName: string): Promise<FileItem> {
  return request.patch(`/files/${fileId}/rename`, { new_name: newName });
}
