/** 版本接口（M7）。 */
import { request } from "./request";

export interface VersionItem {
  version_no: number;
  version_id: number;
  size: number;
  created_at: string;
  created_by: number;
  current: boolean;
}

export interface VersionListResult {
  file_id: number;
  file_name: string;
  current_version_id: number;
  total_size: number;
  items: VersionItem[];
}

export function listVersions(fileId: number): Promise<VersionListResult> {
  return request.get(`/files/${fileId}/versions`);
}

export function restoreVersion(fileId: number, versionNo: number): Promise<{
  restored: boolean;
  already_current: boolean;
  version_no: number;
}> {
  return request.post(`/files/${fileId}/versions/${versionNo}/restore`);
}

export function deleteVersion(fileId: number, versionNo: number): Promise<{
  deleted: boolean;
  version_no: number;
  released_bytes: number;
}> {
  return request.delete(`/files/${fileId}/versions/${versionNo}`);
}
