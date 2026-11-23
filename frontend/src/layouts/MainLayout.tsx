import { Layout, Typography } from "antd";
import { CloudServerOutlined } from "@ant-design/icons";
import { Outlet } from "react-router-dom";

const { Header, Content, Footer } = Layout;

/**
 * 主布局：顶栏（品牌 + 用户区占位）+ 内容区。
 * M1 顶栏右侧接入用户菜单（退出 / 改密）；M2 内容区挂文件列表页。
 */
export default function MainLayout() {
  return (
    <Layout style={{ minHeight: "100%" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "#001529",
          paddingInline: 24,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <CloudServerOutlined style={{ color: "#1677ff", fontSize: 22 }} />
          <Typography.Title level={4} style={{ color: "#fff", margin: 0 }}>
            网盘
          </Typography.Title>
        </div>
        {/* 用户区占位：M1 登录后显示头像与菜单 */}
        <Typography.Text style={{ color: "rgba(255,255,255,0.65)" }}>
          未登录
        </Typography.Text>
      </Header>
      <Content style={{ padding: 24, maxWidth: 1200, width: "100%", margin: "0 auto" }}>
        <Outlet />
      </Content>
      <Footer style={{ textAlign: "center", color: "rgba(0,0,0,0.45)" }}>
        netdisk · M0 骨架阶段
      </Footer>
    </Layout>
  );
}
