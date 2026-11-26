import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Space,
  Table,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  FolderAddOutlined,
  ReloadOutlined,
  EditOutlined,
  UploadOutlined,
  ShareAltOutlined,
  EyeOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import type { TablePaginationConfig } from "antd";
import { createFolder, getPath, listChildren, renameFile, type FileItem } from "../api/files";
import {
  downloadFile,
  getQuota,
  precheckFile,
  trashFile,
  type QuotaInfo,
} from "../api/transfer";
import UploadPanel, { type UploadTask } from "../components/UploadPanel";
import ShareDialog from "../components/ShareDialog";
import VersionModal from "../components/VersionModal";
import PreviewModal from "../components/PreviewModal";
import MoveCopyDialog from "../components/MoveCopyDialog";
import { batchDelete } from "../api/files";
import { downloadZip } from "../api/transfer";
import { fileKind, formatBytes, formatDateTime } from "../utils/format";

type SortField = "name" | "size" | "uploaded_at";

/** 文件页（M2+M3）：面包屑 / 目录浏览 / 新建文件夹 / 重命名 / 上传 / 下载 / 删除进回收站 / 配额条。 */
export default function Files() {
  const { folderId } = useParams();
  const currentId = folderId ? Number(folderId) : 0;
  const navigate = useNavigate();

  const [items, setItems] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [path, setPath] = useState<{ id: number; name: string }[]>([{ id: 0, name: "根目录" }]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sort, setSort] = useState<SortField>("name");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  const [createOpen, setCreateOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [renameLoading, setRenameLoading] = useState(false);
  const [createForm] = Form.useForm<{ name: string }>();
  const [renameForm] = Form.useForm<{ new_name: string }>();

  // M3：上传队列 / 配额 / 正在下载的行
  const [uploadQueue, setUploadQueue] = useState<UploadTask[]>([]);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [downloading, setDownloading] = useState<Set<number>>(new Set());
  const [zipping, setZipping] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // M4：多选批量 / 移动复制对话框 / 搜索
  const [selectedIds, setSelectedIds] = useState<import("react").Key[]>([]);
  const [dialog, setDialog] = useState<"move" | "copy" | null>(null);
  const [shareFile, setShareFile] = useState<FileItem | null>(null);
  const [previewFile, setPreviewFile] = useState<FileItem | null>(null);
  const [versionFile, setVersionFile] = useState<FileItem | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listChildren(currentId, { page, page_size: pageSize, sort, order });
      setItems(data.items);
      setTotal(data.total);
      setPath(await getPath(currentId));
    } catch {
      // 拦截器已提示错误；保持旧数据
    } finally {
      setLoading(false);
    }
  }, [currentId, page, pageSize, sort, order]);

  useEffect(() => {
    setPage(1); // 切目录回第一页
  }, [currentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadQuota = useCallback(async () => {
    try {
      setQuota(await getQuota());
    } catch {
      // 配额读取失败不阻塞页面
    }
  }, []);

  useEffect(() => {
    void loadQuota();
  }, [loadQuota]);

  // ---------- M3：上传 / 下载 / 删除 ----------
  const onPickFiles = (list: FileList | null) => {
    if (!list) return;
    const accepted: UploadTask[] = [];
    Array.from(list).forEach((file) => {
      const problem = precheckFile(file);
      if (problem) {
        message.warning(`「${file.name}」${problem}`);
        return;
      }
      accepted.push({ uid: `${Date.now()}-${Math.random().toString(16).slice(2)}`, file, parentId: currentId });
    });
    if (accepted.length) setUploadQueue((q) => [...q, ...accepted]);
    if (fileInputRef.current) fileInputRef.current.value = ""; // 允许重复选同一文件
  };

  const onUploadFinished = async () => {
    await Promise.all([load(), loadQuota()]);
  };

  const removeUploadTask = (uid: string) =>
    setUploadQueue((q) => q.filter((t) => t.uid !== uid));

  const onDownload = async (item: FileItem) => {
    setDownloading((s) => new Set(s).add(item.id));
    try {
      await downloadFile(item.id, item.name);
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    } finally {
      setDownloading((s) => {
        const next = new Set(s);
        next.delete(item.id);
        return next;
      });
    }
  };

  const onTrash = async (item: FileItem) => {
    try {
      const r = await trashFile(item.id);
      message.success(`「${r.name}」已移入回收站${r.trashed > 1 ? `（含 ${r.trashed} 项）` : ""}`);
      setSelectedIds([]);
      await Promise.all([load(), loadQuota()]);
    } catch {
      // 拦截器已提示
    }
  };

  // ---------- M4：批量操作 ----------
  const selectedNumeric = selectedIds.map(Number);

  const onBatchDelete = async () => {
    try {
      const r = await batchDelete(selectedNumeric);
      const ok = r.results.filter((x) => x.ok);
      const failed = r.results.filter((x) => !x.ok);
      if (failed.length === 0) {
        message.success(`已删除 ${ok.length} 项进回收站`);
      } else {
        message.warning(`${ok.length} 项成功，${failed.length} 项失败：${failed[0].message ?? ""}`);
      }
      setSelectedIds([]);
      await Promise.all([load(), loadQuota()]);
    } catch {
      // 拦截器已提示
    }
  };

  const onZip = async () => {
    setZipping(true);
    try {
      await downloadZip(selectedNumeric);
    } catch (e) {
      message.error((e as Error).message || "打包下载失败");
    } finally {
      setZipping(false);
    }
  };

  const onMoveCopyDone = async () => {
    setSelectedIds([]);
    await Promise.all([load(), loadQuota()]);
  };

  const onCreate = async ({ name }: { name: string }) => {
    setCreateLoading(true);
    try {
      await createFolder(currentId, name.trim());
      message.success(`已创建文件夹「${name.trim()}」`);
      setCreateOpen(false);
      createForm.resetFields();
      await load();
    } catch {
      // 拦截器已提示（重名 2002 / 非法名 2003）
    } finally {
      setCreateLoading(false);
    }
  };

  const onRename = async ({ new_name }: { new_name: string }) => {
    if (!renameTarget) return;
    setRenameLoading(true);
    try {
      await renameFile(renameTarget.id, new_name.trim());
      message.success("已重命名");
      setRenameTarget(null);
      await load();
    } catch {
      // 拦截器已提示
    } finally {
      setRenameLoading(false);
    }
  };

  const onTableChange = (pagination: TablePaginationConfig, _f: unknown, sorter: unknown) => {
    const s = sorter as { field?: string; order?: "ascend" | "descend" };
    if (s?.field && s.order) {
      setSort(s.field as SortField);
      setOrder(s.order === "descend" ? "desc" : "asc");
    }
    if (pagination.current) setPage(pagination.current);
    if (pagination.pageSize) setPageSize(pagination.pageSize);
  };

  return (
    <Card
      title={
        <Space size="middle">
          <Breadcrumb
            items={path.map((p) => ({
              title:
                p.id === 0 ? (
                  <Link to="/">根目录</Link>
                ) : (
                  <Link to={`/folders/${p.id}`}>{p.name}</Link>
                ),
            }))}
          />
        </Space>
      }
      extra={
        <Space>
          <Input.Search
            placeholder="搜索文件名"
            size="middle"
            style={{ width: 200 }}
            allowClear
            onSearch={(v) => navigate(v.trim() ? `/search?q=${encodeURIComponent(v.trim())}` : "/search")}
          />
          {quota && (
            <Tooltip
              title={`已用 ${formatBytes(quota.used_bytes)} / 总量 ${formatBytes(quota.quota_bytes)}，剩余 ${formatBytes(quota.remaining_bytes)}`}
            >
              <div style={{ width: 160, lineHeight: 1.2 }}>
                <Progress
                  percent={Math.min(100, (quota.used_bytes / quota.quota_bytes) * 100)}
                  size="small"
                  showInfo={false}
                  status={quota.remaining_bytes === 0 ? "exception" : "normal"}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {formatBytes(quota.used_bytes)} / {formatBytes(quota.quota_bytes)}
                </Typography.Text>
              </div>
            </Tooltip>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => void load()} />
          <Button icon={<FolderAddOutlined />} onClick={() => setCreateOpen(true)}>
            新建文件夹
          </Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => fileInputRef.current?.click()}>
            上传
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => onPickFiles(e.target.files)}
          />
        </Space>
      }
    >
      {selectedIds.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            marginBottom: 12,
            background: "#e6f4ff",
            borderRadius: 8,
          }}
        >
          <Typography.Text>已选 {selectedIds.length} 项：</Typography.Text>
          <Button size="small" icon={<DownloadOutlined />} loading={zipping} onClick={() => void onZip()}>
            ZIP 下载
          </Button>
          <Button size="small" onClick={() => setDialog("move")}>
            移动到…
          </Button>
          <Button size="small" onClick={() => setDialog("copy")}>
            复制到…
          </Button>
          <Popconfirm
            title={`删除选中的 ${selectedIds.length} 项？`}
            description="将移入回收站（可在回收站还原）。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void onBatchDelete()}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              批量删除
            </Button>
          </Popconfirm>
          <Button size="small" type="text" onClick={() => setSelectedIds([])}>
            取消选择
          </Button>
        </div>
      )}
      <UploadPanel
        queue={uploadQueue}
        onFinished={() => void onUploadFinished()}
        onRemove={removeUploadTask}
      />
      <Table<FileItem>
      rowKey="id"
      loading={loading}
      dataSource={items}
      onChange={onTableChange}
      rowSelection={{
        selectedRowKeys: selectedIds,
        onChange: (keys) => setSelectedIds(keys),
      }}
      locale={{
        emptyText: <Empty description="此目录为空，点击右上角「上传」添加文件" />,
      }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 项`,
        }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            key: "name",
            sorter: true,
            render: (_, item) => (
              <Button
                type="text"
                icon={item.is_folder ? <FolderOutlined /> : <FileOutlined />}
                onClick={() => item.is_folder && navigate(`/folders/${item.id}`)}
                style={{ maxWidth: 480, justifyContent: "flex-start" }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {item.name}
                </span>
              </Button>
            ),
          },
          {
            title: "大小",
            dataIndex: "size",
            key: "size",
            width: 110,
            sorter: true,
            render: (v: number, item) => (item.is_folder ? "—" : formatBytes(v)),
          },
          {
            title: "类型",
            key: "kind",
            width: 120,
            render: (_, item) => fileKind(item.name, item.is_folder),
          },
          {
            title: "修改时间",
            dataIndex: "updated_at",
            key: "uploaded_at",
            width: 170,
            sorter: true,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "操作",
            key: "actions",
            width: 250,
            render: (_, item) => (
              <Space size={0}>
                <Tooltip title="重命名">
                  <Button
                    size="small"
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setRenameTarget(item);
                      renameForm.setFieldsValue({ new_name: item.name });
                    }}
                  />
                </Tooltip>
                {!item.is_folder && (
                  <>
                    <Tooltip title="版本历史">
                      <Button
                        size="small"
                        type="text"
                        icon={<HistoryOutlined />}
                        onClick={() => setVersionFile(item)}
                      />
                    </Tooltip>
                    <Tooltip title="预览">
                      <Button
                        size="small"
                        type="text"
                        icon={<EyeOutlined />}
                        onClick={() => setPreviewFile(item)}
                      />
                    </Tooltip>
                    <Tooltip title="下载">
                      <Button
                        size="small"
                        type="text"
                        icon={<DownloadOutlined />}
                        loading={downloading.has(item.id)}
                        onClick={() => void onDownload(item)}
                      />
                    </Tooltip>
                    <Tooltip title="分享">
                      <Button
                        size="small"
                        type="text"
                        icon={<ShareAltOutlined />}
                        onClick={() => setShareFile(item)}
                      />
                    </Tooltip>
                  </>
                )}
                <Popconfirm
                  title={item.is_folder ? "将此目录及其内容移入回收站？" : "将此文件移入回收站？"}
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() => void onTrash(item)}
                >
                  <Tooltip title="删除（进回收站）">
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Tooltip>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <MoveCopyDialog
        open={dialog !== null}
        mode={dialog === "copy" ? "copy" : "move"}
        fileIds={selectedNumeric}
        onClose={() => setDialog(null)}
        onDone={() => void onMoveCopyDone()}
      />

      <VersionModal
        open={versionFile !== null}
        file={versionFile}
        onClose={(changed) => {
          setVersionFile(null);
          if (changed) void Promise.all([load(), loadQuota()]);
        }}
      />
      <ShareDialog
        open={shareFile !== null}
        file={shareFile}
        onClose={() => setShareFile(null)}
      />
      <PreviewModal
        open={previewFile !== null}
        file={previewFile}
        onClose={() => setPreviewFile(null)}
      />

      <Modal
        title="新建文件夹"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form form={createForm} layout="vertical" onFinish={onCreate}>
          <Form.Item
            name="name"
            label="文件夹名称"
            rules={[
              { required: true, message: "请输入名称" },
              { max: 255, message: "名称过长" },
              {
                pattern: /^[^\\\\/:*?"<>|]+$/,
                message: "名称不能包含 / \\ : * ? \" < > | 等字符",
              },
            ]}
          >
            <Input placeholder="例如：课程资料" autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={createLoading}>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title={`重命名「${renameTarget?.name ?? ""}」`}
        open={renameTarget !== null}
        onCancel={() => setRenameTarget(null)}
        footer={null}
        destroyOnClose
      >
        <Form form={renameForm} layout="vertical" onFinish={onRename}>
          <Form.Item
            name="new_name"
            label="新名称"
            rules={[
              { required: true, message: "请输入新名称" },
              { max: 255, message: "名称过长" },
              {
                pattern: /^[^\\\\/:*?"<>|]+$/,
                message: "名称不能包含 / \\ : * ? \" < > | 等字符",
              },
            ]}
          >
            <Input autoFocus />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={renameLoading}>
            确认
          </Button>
        </Form>
      </Modal>
    </Card>
  );
}
