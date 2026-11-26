import { useCallback, useEffect, useState } from "react";
import { Button, Modal, Popconfirm, Space, Spin, Table, Tag, Typography, message } from "antd";
import { RollbackOutlined, DeleteOutlined } from "@ant-design/icons";
import {
  deleteVersion, listVersions, restoreVersion,
  type VersionItem, type VersionListResult,
} from "../api/versions";
import { formatBytes, formatDateTime } from "../utils/format";

interface Props {
  open: boolean;
  file: { id: number; name: string } | null;
  onClose: (changed: boolean) => void; // changed=true 时父组件刷新列表/配额
}

/** 文件版本面板（M7.6）：时间线列表（当前标记）、恢复（指针切换）、删指定版本、总占用。 */
export default function VersionModal({ open, file, onClose }: Props) {
  const [data, setData] = useState<VersionListResult | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (fileId: number) => {
    setLoading(true);
    try {
      setData(await listVersions(fileId));
    } catch {
      // 拦截器已提示
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && file) void load(file.id);
  }, [open, file, load]);

  const onRestore = async (item: VersionItem) => {
    if (!file) return;
    try {
      const r = await restoreVersion(file.id, item.version_no);
      message.success(
        r.already_current ? `v${item.version_no} 已是当前版本` : `已恢复到 v${item.version_no}`,
      );
      await load(file.id);
    } catch {
      // 拦截器已提示
    }
  };

  const onDelete = async (item: VersionItem) => {
    if (!file) return;
    try {
      const r = await deleteVersion(file.id, item.version_no);
      message.success(`已删除 v${item.version_no}，释放 ${formatBytes(r.released_bytes)}`);
      await load(file.id);
    } catch {
      // 拦截器已提示（当前版本 409 / 最后版本 409）
    }
  };

  return (
    <Modal
      title={`版本历史「${file?.name ?? ""}」`}
      open={open}
      onCancel={() => onClose(false)}
      footer={null}
      width={680}
      destroyOnClose
    >
      <Spin spinning={loading}>
        {data && (
          <>
            <Typography.Paragraph type="secondary">
              共 {data.items.length} 个版本，合计占用 {formatBytes(data.total_size)}。
              恢复 = 切换当前指针（不额外占用）；删除指定版本会释放其占用的空间。
            </Typography.Paragraph>
            <Table<VersionItem>
              rowKey="version_no"
              dataSource={data.items}
              pagination={false}
              size="small"
              columns={[
                {
                  title: "版本",
                  dataIndex: "version_no",
                  width: 90,
                  render: (v: number, item) => (
                    <Space>
                      v{v}
                      {item.current && <Tag color="blue">当前</Tag>}
                    </Space>
                  ),
                },
                {
                  title: "大小",
                  dataIndex: "size",
                  width: 100,
                  render: (v: number) => formatBytes(v),
                },
                {
                  title: "上传时间",
                  dataIndex: "created_at",
                  render: (v: string) => formatDateTime(v),
                },
                {
                  title: "操作",
                  width: 180,
                  render: (_, item) => (
                    <Space>
                      {!item.current && (
                        <Button
                          size="small"
                          icon={<RollbackOutlined />}
                          onClick={() => void onRestore(item)}
                        >
                          恢复此版本
                        </Button>
                      )}
                      {!item.current && (
                        <Popconfirm
                          title={`删除 v${item.version_no}？`}
                          description="将释放该版本占用的空间，不可恢复。"
                          okText="删除"
                          cancelText="取消"
                          okButtonProps={{ danger: true }}
                          onConfirm={() => void onDelete(item)}
                        >
                          <Button size="small" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      )}
                      {item.current && <Typography.Text type="secondary">使用中</Typography.Text>}
                    </Space>
                  ),
                },
              ]}
            />
          </>
        )}
      </Spin>
    </Modal>
  );
}
