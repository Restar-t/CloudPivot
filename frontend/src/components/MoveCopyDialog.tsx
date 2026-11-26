import { useEffect, useState } from "react";
import { Modal, Space, Spin, Tree, Typography, message } from "antd";
import type { DataNode } from "antd/es/tree";
import { listChildren, moveFiles, copyFiles } from "../api/files";

interface Props {
  open: boolean;
  mode: "move" | "copy";
  fileIds: number[];
  onClose: () => void;
  onDone: (results: { ok: boolean }[]) => void;
}

interface FolderNode extends DataNode {
  children?: FolderNode[];
}

/** 移动/复制对话框：异步加载目录树（含根），选择目标后批量执行。 */
export default function MoveCopyDialog({ open, mode, fileIds, onClose, onDone }: Props) {
  const [tree, setTree] = useState<FolderNode[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loadingTree, setLoadingTree] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSelected(null);
    setLoadingTree(true);
    loadFolder(0)
      .then((children) =>
        setTree([
          { key: 0, title: "根目录", isLeaf: children.length === 0, children },
        ]),
      )
      .finally(() => setLoadingTree(false));
  }, [open]);

  const loadFolder = async (id: number): Promise<FolderNode[]> => {
    const data = await listChildren(id, { folder_only: true, page_size: 100 });
    return data.items.map((f) => ({
      key: f.id,
      title: f.name,
      isLeaf: false, // 展开时按需加载（无子目录则显示为空）
    }));
  };

  const onLoadData = async (node: FolderNode): Promise<void> => {
    if (node.children?.length) return;
    const children = await loadFolder(Number(node.key));
    setTree((orig) => updateTree(orig, Number(node.key), children));
  };

  const updateTree = (nodes: FolderNode[], key: number, children: FolderNode[]): FolderNode[] =>
    nodes.map((n) =>
      n.key === key
        ? { ...n, children: children.length ? children : undefined, isLeaf: children.length === 0 }
        : n.children
          ? { ...n, children: updateTree(n.children as FolderNode[], key, children) }
          : n,
    );

  const onSubmit = async () => {
    if (selected === null) {
      message.warning("请先在目录树中选择目标文件夹");
      return;
    }
    setSubmitting(true);
    try {
      const r =
        mode === "move"
          ? await moveFiles(fileIds, selected)
          : await copyFiles(fileIds, selected);
      const ok = r.results.filter((x) => x.ok).length;
      const failed = r.results.filter((x) => !x.ok);
      if (failed.length === 0) {
        message.success(`${mode === "move" ? "移动" : "复制"}完成（${ok} 项）`);
      } else {
        message.warning(`${ok} 项成功，${failed.length} 项失败：${failed[0].message ?? ""}`);
      }
      onDone(r.results);
      onClose();
    } catch {
      // 拦截器已提示
    } finally {
      setSubmitting(false);
    }
  };

  const title = mode === "move" ? "移动到…" : "复制到…";

  return (
    <Modal
      title={
        <Space size="small">
          {title}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            已选 {fileIds.length} 项
          </Typography.Text>
        </Space>
      }
      open={open}
      onCancel={onClose}
      onOk={() => void onSubmit()}
      confirmLoading={submitting}
      okText={mode === "move" ? "移动" : "复制"}
      okButtonProps={{ disabled: selected === null }}
      width={420}
    >
      {loadingTree ? (
        <Spin style={{ display: "block", margin: "32px auto" }} />
      ) : (
        <Tree<FolderNode>
          treeData={tree}
          showLine
          defaultExpandAll
          selectedKeys={selected === null ? [] : [selected]}
          onSelect={(keys) => setSelected(keys.length ? Number(keys[0]) : null)}
          loadData={async (node) => {
            await onLoadData(node as FolderNode);
          }}
        />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        移动到目标目录中的同名文件会冲突（409）；复制会自动命名为「xx（1）」。
      </Typography.Text>
    </Modal>
  );
}
